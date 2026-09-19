"""Fixed-client Alpha sign-in; all trust decisions remain in the backend."""
import base64
from datetime import timedelta
import hashlib
import re
import secrets
from urllib.parse import urlencode, urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from core import mail
from users.models import ExternalIdentity, User, WR1SignIn
from users.serializers import UserSerializer
from users.tokens import build_token_response
from users.access import require_allowed_email
from worlds.models import World


OPAQUE = r'\A[A-Za-z0-9_-]{43}\Z'
PENDING_SECONDS = 300
CONTINUATION_SECONDS = 900


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configuration():
    if not settings.WR1_SSO_ENABLED:
        raise exceptions.NotFound('Alpha sign-in is not available yet.')
    local = settings.WR1_SSO_ALLOW_INSECURE_LOCAL
    if local and not settings.DEBUG:
        raise ImproperlyConfigured('Insecure Alpha sign-in is only allowed with DEBUG.')
    values = [settings.WR1_SSO_ISSUER, settings.WR1_SSO_AUTHORIZE_URL,
              settings.WR1_SSO_REDIRECT_URI, settings.WR1_SSO_EXCHANGE_URL]
    for index, value in enumerate(values):
        parsed = urlsplit(value)
        allowed = ('localhost', '127.0.0.1', '::1')
        if index == 3:
            allowed += ('host.docker.internal', 'api', 'wr1-api')
        valid_http = local and parsed.scheme == 'http' and parsed.hostname in allowed
        if (not parsed.hostname or not (parsed.scheme == 'https' or valid_http)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or '\\' in value or any(c.isspace() for c in value)):
            raise ImproperlyConfigured('Alpha sign-in requires fixed HTTPS URLs or explicit local development URLs.')
    if (not settings.WR1_SSO_CLIENT_ID or len(settings.WR1_SSO_CLIENT_ID) > 128
            or len(settings.WR1_SSO_CLIENT_SECRET) < 32):
        raise ImproperlyConfigured('Alpha sign-in requires a client ID and a dedicated secret of at least 32 characters.')
    callback = urlsplit(settings.WR1_SSO_REDIRECT_URI)
    if callback.path != '/auth/wr1/callback':
        raise ImproperlyConfigured('Alpha callback must use /auth/wr1/callback.')
    return ('wr1-bridge-local' if local else '__Host-wr1-bridge',
            callback.scheme + '://' + callback.netloc)


def fail(message='This Alpha sign-in has expired. Please start again.'):
    raise exceptions.ValidationError({'detail': message})


class BridgeThrottle(AnonRateThrottle):
    scope = 'wr1_bridge'
    rate = '30/min'


class ProofThrottle(AnonRateThrottle):
    scope = 'wr1_bridge_email'
    rate = '2/min'


class BridgeView(APIView):
    # The cookie and state bind the browser. An ambient Core JWT is never an
    # instruction to attach an Alpha identity to that Core account.
    authentication_classes = ()
    permission_classes = ()
    throttle_classes = (BridgeThrottle,)

    def initial(self, request, *args, **kwargs):
        self.cookie_name, origin = configuration()
        if request.headers.get('Origin') != origin:
            raise exceptions.PermissionDenied('Start Alpha sign-in from Core.')
        super().initial(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        response['Referrer-Policy'] = 'no-referrer'
        return response

    def locked_attempt(self, request, state, status):
        browser = request.COOKIES.get(self.cookie_name, '')
        if not re.fullmatch(OPAQUE, browser):
            fail()
        attempt = WR1SignIn.objects.select_for_update().filter(state_hash=digest(state)).first()
        if (not attempt or not constant_time_compare(attempt.browser_hash, digest(browser))
                or attempt.status != status or attempt.expires_ts <= timezone.now()
                or attempt.issuer != settings.WR1_SSO_ISSUER
                or attempt.client_id != settings.WR1_SSO_CLIENT_ID
                or attempt.redirect_uri != settings.WR1_SSO_REDIRECT_URI):
            fail()
        return attempt


class StateInput(serializers.Serializer):
    state = serializers.RegexField(OPAQUE, max_length=43)


class CallbackInput(StateInput):
    code = serializers.RegexField(OPAQUE, max_length=43)


class FinishInput(StateInput):
    proof = serializers.RegexField(r'\A[0-9]{8}\Z', required=False)


class StartInput(serializers.Serializer):
    world = serializers.IntegerField(min_value=1, max_value=9223372036854775807)


def read_input(serializer_class, request):
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class Start(BridgeView):
    def post(self, request):
        data = read_input(StartInput, request)
        world = World.objects.filter(pk=data['world'], context__isnull=True, is_public=True).first()
        if not world:
            fail('This Core world is not available.')
        browser = request.COOKIES.get(self.cookie_name, '')
        if not re.fullmatch(OPAQUE, browser):
            browser = secrets.token_urlsafe(32)
        if WR1SignIn.objects.filter(browser_hash=digest(browser),
                expires_ts__gt=timezone.now()).exclude(status__in=['consumed', 'failed']).count() >= 5:
            fail('Too many pending sign-ins. Wait a few minutes before trying again.')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        WR1SignIn.objects.create(
            state_hash=digest(state), browser_hash=digest(browser), verifier=verifier,
            issuer=settings.WR1_SSO_ISSUER, client_id=settings.WR1_SSO_CLIENT_ID,
            redirect_uri=settings.WR1_SSO_REDIRECT_URI, world=world,
            expires_ts=timezone.now() + timedelta(seconds=PENDING_SECONDS))
        url = settings.WR1_SSO_AUTHORIZE_URL + '?' + urlencode({
            'response_type': 'code', 'client_id': settings.WR1_SSO_CLIENT_ID,
            'redirect_uri': settings.WR1_SSO_REDIRECT_URI, 'state': state,
            'code_challenge_method': 'S256',
            'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),
        })
        response = Response({'authorization_url': url}, status=201)
        response.set_cookie(self.cookie_name, browser, max_age=1800,
                            secure=not settings.WR1_SSO_ALLOW_INSECURE_LOCAL,
                            httponly=True, samesite='Lax', path='/')
        return response


class IdentityInput(serializers.Serializer):
    iss = serializers.CharField(max_length=255, trim_whitespace=False)
    aud = serializers.CharField(max_length=128, trim_whitespace=False)
    sub = serializers.RegexField(r'\A[1-9][0-9]{0,19}\Z', max_length=20)
    email = serializers.EmailField(max_length=255)
    email_verified = serializers.BooleanField()


def exchange(attempt, code):
    try:
        with requests.post(settings.WR1_SSO_EXCHANGE_URL,
                auth=(settings.WR1_SSO_CLIENT_ID, settings.WR1_SSO_CLIENT_SECRET),
                json={'grant_type': 'authorization_code', 'code': code,
                      'redirect_uri': attempt.redirect_uri, 'code_verifier': attempt.verifier},
                timeout=(3, 5), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                fail('Alpha could not complete this sign-in. Please start again.')
            body = b''
            for chunk in response.iter_content(4096):
                body += chunk
                if len(body) > 16384:
                    fail('Alpha returned an invalid response.')
        import json
        raw = json.loads(body)
    except (requests.RequestException, ValueError):
        fail('Alpha is unavailable. Please start again or use Core email login.')
    if (not isinstance(raw, dict) or type(raw.get('email_verified')) is not bool
            or any(not isinstance(raw.get(key), str) for key in ('iss', 'aud', 'sub', 'email'))):
        fail('Alpha returned an invalid identity.')
    serializer = IdentityInput(data=raw)
    if not serializer.is_valid():
        fail('Alpha returned an invalid identity.')
    identity = serializer.validated_data
    if identity['iss'] != attempt.issuer or identity['aud'] != attempt.client_id:
        fail('Alpha returned an unexpected identity provider.')
    return identity


def identity_user(attempt):
    link = ExternalIdentity.objects.select_related('user').filter(
        issuer=attempt.issuer, subject=attempt.subject).first()
    return link.user if link else None


def email_user(attempt):
    # Matches the expression index/unique constraint, including old mixed-case emails.
    return User.objects.alias(email_upper=Upper('email')).filter(email_upper=attempt.email.upper()).first()


def check_user(user):
    if user and (not user.is_active or user.is_invalid or user.is_temporary):
        raise exceptions.PermissionDenied('This Core account is not available.')


def outcome(attempt):
    linked = identity_user(attempt)
    user = linked or email_user(attempt)
    check_user(user)
    proof_needed = not linked and not attempt.email_proved and (user is not None or not attempt.email_verified)
    return {'status': 'verification_required' if proof_needed else 'ready',
            'email': user.email if linked else attempt.email,
            'user_id': user.pk if user else None,
            'destination': '/worlds/%s' % attempt.world_id}


class Callback(BridgeView):
    def post(self, request):
        data = read_input(CallbackInput, request)
        with transaction.atomic():
            attempt = self.locked_attempt(request, data['state'], 'pending')
            attempt.status = 'exchanging'
            attempt.save(update_fields=['status'])
        # No database locks are held while contacting Alpha. A lost response
        # requires a new handoff, not a second use of a single-use code.
        try:
            identity = exchange(attempt, data['code'])
        except exceptions.APIException:
            WR1SignIn.objects.filter(pk=attempt.pk, status='exchanging').update(status='failed', verifier='')
            raise
        with transaction.atomic():
            attempt = self.locked_attempt(request, data['state'], 'exchanging')
            attempt.subject = identity['sub']
            attempt.email = identity['email'].strip().lower()
            attempt.email_verified = identity['email_verified']
            attempt.status = 'ready'
            attempt.verifier = ''
            attempt.expires_ts = timezone.now() + timedelta(seconds=CONTINUATION_SECONDS)
            attempt.save()
            return Response(outcome(attempt))


class SendProof(BridgeView):
    throttle_classes = (BridgeThrottle, ProofThrottle)

    def post(self, request):
        data = read_input(StateInput, request)
        with transaction.atomic():
            attempt = self.locked_attempt(request, data['state'], 'ready')
            if outcome(attempt)['status'] != 'verification_required':
                fail('Email verification is not required for this sign-in.')
            if attempt.proof_attempts >= 5:
                fail('Too many incorrect codes. Please start again.')
            if attempt.proof_sent_ts and attempt.proof_sent_ts > timezone.now() - timedelta(seconds=60):
                raise exceptions.Throttled(wait=60)
            code = '%08d' % secrets.randbelow(100000000)
            attempt.proof_hash = digest(code)
            attempt.proof_sent_ts = timezone.now()
            attempt.save(update_fields=['proof_hash', 'proof_sent_ts'])
        # Delivery outside the row lock; the code is tied to this assertion,
        # browser and deadline, and cannot be used as an ordinary login token.
        mail.send_email(subject='Written Realms Core sign-in code',
            body=('Enter this code in the Core tab where you started signing in from Alpha:\n\n%s\n\n'
                  'It expires with that sign-in attempt, within 15 minutes. '
                  'If you did not request this, ignore this email.') % code,
            to_addresses=[attempt.email])
        return Response({'status': 'code_sent'})


class Status(BridgeView):
    def post(self, request):
        data = read_input(StateInput, request)
        with transaction.atomic():
            return Response(outcome(self.locked_attempt(request, data['state'], 'ready')))


class Finish(BridgeView):
    def post(self, request):
        data = read_input(FinishInput, request)
        with transaction.atomic():
            attempt = self.locked_attempt(request, data['state'], 'ready')
            if data.get('proof'):
                if (attempt.proof_attempts >= 5 or not attempt.proof_hash
                        or not constant_time_compare(attempt.proof_hash, digest(data['proof']))):
                    attempt.proof_attempts += 1
                    attempt.save(update_fields=['proof_attempts'])
                    return Response({'detail': 'Invalid code. Request a new code or start again.'}, status=400)
                attempt.email_proved = True
                attempt.proof_hash = ''
            # Serialize only first-use account resolution for this subject.
            # PostgreSQL advisory locks cover the absent-row case too.
            key = int.from_bytes(hashlib.sha256((attempt.issuer + '\0' + attempt.subject).encode()).digest()[:8], 'big', signed=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])
            result = outcome(attempt)
            if result['status'] != 'ready':
                return Response(result)
            if not World.objects.filter(pk=attempt.world_id, is_public=True, context__isnull=True).exists():
                fail('This Core world is no longer available.')
            require_allowed_email(attempt.email)
            user = identity_user(attempt)
            if user:
                user = User.objects.select_for_update().get(pk=user.pk)
            else:
                # Recheck even after outcome(): a native signup may have raced
                # us. Lock the email match so it cannot change while linking.
                user, created = User.objects.select_for_update().get_or_create(email__iexact=attempt.email,
                    defaults={'email': attempt.email, 'password': '!', 'is_confirmed': True})
                if not created and not attempt.email_proved:
                    return Response({'status': 'verification_required', 'email': attempt.email,
                                     'user_id': user.pk, 'destination': result['destination']})
                check_user(user)
                ExternalIdentity.objects.create(issuer=attempt.issuer, subject=attempt.subject, user=user)
            # Core-owned flags are checked under the account lock.
            check_user(user)
            if attempt.email_proved and user.email.lower() == attempt.email and not user.is_confirmed:
                user.is_confirmed = True
                user.save(update_fields=['is_confirmed'])
            attempt.status = 'consumed'
            attempt.proof_hash = ''
            attempt.save()
            return Response({'status': 'authenticated', 'destination': result['destination'],
                             **build_token_response(user), 'user': UserSerializer(user).data})
