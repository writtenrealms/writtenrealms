import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import (
    get_user_model)
from django.utils import timezone

from google.oauth2 import id_token
from google.auth.transport import requests as gauth_requests

from rest_framework import serializers, validators

from core import mail
from users.models import (
    EmailConfirmation,
    LoginLinkRequest)


User = get_user_model()


class UserSerializer(serializers.ModelSerializer):

    is_admin = serializers.BooleanField(source='is_staff', read_only=True)
    num_players = serializers.SerializerMethodField()
    email = serializers.EmailField(
        validators=[validators.UniqueValidator(queryset=User.objects.all())])
    name = serializers.CharField(
        source='username',
        required=False,
        allow_blank=True,
        validators=[validators.UniqueValidator(queryset=User.objects.all())])

    # Formatting help for Herald
    date_joined_str = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id', 'email', 'name', 'key', 'first_name', 'last_name',
            'is_admin', 'is_staff', 'is_temporary', 'is_confirmed',
            'is_invalid', 'cod_accepted',
            #'is_builder',
            'num_players', 'date_joined', 'date_joined_str',
            # config flags
            'send_newsletter', 'use_grapevine', 'accessibility_mode',
            # patreon
            'name_recognition', 'multiplayer_worlds',
        )

    def validate_name(self, value):
        if not value:
            return None
        return value

    def get_num_players(self, obj):
        return 0

    def get_date_joined_str(self, user):
        return user.date_joined.strftime('%m/%d')


def _hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def _create_login_link_for_user(user):
    # Invalidate any outstanding login links for this user.
    LoginLinkRequest.objects.filter(
        user=user,
        used_ts__isnull=True).update(used_ts=timezone.now())

    token = secrets.token_urlsafe(32)
    expires_ts = timezone.now() + timedelta(
        seconds=settings.LOGIN_LINK_TTL_SECONDS)
    LoginLinkRequest.objects.create(
        user=user,
        code_hash=_hash_token(token),
        expires_ts=expires_ts)
    mail.send_login_link(user.email, token)
    return token


class EmailLoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        try:
            self.user = User.objects.get(email__iexact=value)
        except User.DoesNotExist:
            self.user = None
        return value

    def create(self, validated_data):
        email = validated_data['email']
        user = self.user
        if user is None:
            user = User.objects.create(
                email=email,
                is_confirmed=False)
            user.set_unusable_password()
            user.save()
        elif user.is_invalid:
            return None

        _create_login_link_for_user(user)
        return user


class EmailLoginConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, value):
        token_hash = _hash_token(value)
        self.login_request = LoginLinkRequest.objects.select_related('user').filter(
            code_hash=token_hash).order_by('-created_ts').first()
        if not self.login_request:
            raise serializers.ValidationError("Invalid or expired login link.")

        if self.login_request.used_ts:
            raise serializers.ValidationError("Login link has already been used.")
        if self.login_request.expires_ts <= timezone.now():
            raise serializers.ValidationError("Invalid or expired login link.")
        return value

    def create(self, validated_data):
        login_request = self.login_request
        login_request.used_ts = timezone.now()
        login_request.save(update_fields=['used_ts'])

        user = login_request.user
        if user.is_invalid:
            raise serializers.ValidationError("Invalid e-mail address.")
        if not user.is_confirmed:
            user.is_confirmed = True
        user.last_login = timezone.now()
        user.save(update_fields=['is_confirmed', 'last_login'])
        return user


class SignupSerializer(serializers.Serializer):

    email = serializers.EmailField(
        validators=[validators.UniqueValidator(queryset=User.objects.all())])
    username = serializers.CharField(
        required=False,
        allow_blank=True,
        validators=[validators.UniqueValidator(queryset=User.objects.all())])
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    send_newsletter = serializers.BooleanField(default=False)

    def create(self, validated_data):
        # Create user
        user = User.objects.create(
            email=validated_data['email'],
            username=validated_data.get('username', None),
            send_newsletter=validated_data.get('send_newsletter', False),
            first_name=validated_data.get('first_name', None),
            last_name=validated_data.get('last_name', None))

        user.set_unusable_password()
        user.save()

        _create_login_link_for_user(user)

        return user

    def validate_username(self, value):
        return None if not value else value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already registered.")
        return value


class SaveTempCharSerializer(SignupSerializer):

    def create(self, validated_data):
        user = validated_data.get('user')

        user.email = validated_data['email']
        user.is_temporary = False
        user.send_newsletter = validated_data['send_newsletter']
        user.set_unusable_password()

        user.first_name = validated_data.get('first_name', None)
        user.last_name = validated_data.get('last_name', None)

        if validated_data.get('username'):
            user.username = validated_data['username']

        user.save()

        _create_login_link_for_user(user)

        return user


class EmailConfirmationSerializer(serializers.Serializer):

    code = serializers.CharField()

    def validate_code(self, value):
        try:
            self.confirm_record = EmailConfirmation.objects.get(code=value)
        except EmailConfirmation.DoesNotExist:
            raise serializers.ValidationError("Invalid confirmation code.")
        return value

    def create(self, validated_data):
        user = self.confirm_record.user
        user.is_confirmed = True
        user.save()
        user.email_confirmations.all().delete()
        return user


class GoogleLoginDeserializer(serializers.Serializer):

    credential = serializers.CharField()

    def validate_credential(self, value):
        if not settings.GOOGLE_CLIENT_ID:
            raise serializers.ValidationError("Google login is unavailable.")
        try:
            idinfo = id_token.verify_oauth2_token(
                value,
                gauth_requests.Request(),
                settings.GOOGLE_CLIENT_ID)
            if idinfo['iss'] not in [
                'accounts.google.com',
                'https://accounts.google.com']:
                raise ValueError('Wrong issuer.')
            if not idinfo.get('email_verified'):
                raise ValueError('Email not verified.')
        except ValueError:
            raise serializers.ValidationError("Invalid credential.")

        self.user_info = {
            'email': idinfo.get('email'),
            'google_id': idinfo.get('sub'),
            'first_name': idinfo.get('given_name'),
            'last_name': idinfo.get('family_name'),
        }

        return value

    def create(self, validated_data):
        user_info = self.user_info
        email = user_info['email']
        google_id = user_info['google_id']
        first_name = user_info['first_name']
        last_name = user_info['last_name']

        # First, see if we already have this e-mail address in our system
        email_user = User.objects.filter(email__iexact=email).first()
        google_user = User.objects.filter(google_id=google_id).first()

        # This is a new user, create it with no usable password
        if not email_user and not google_user:
            user = User.objects.create(
                email=email,
                first_name=first_name,
                last_name=last_name,
                google_id=google_id,
                is_confirmed=True)
            user.set_unusable_password()
            return user

        # There is already a user with that e-mail address, but no google
        # ID associated with it. Update the user with the google ID
        # and the first and last names if they were not set.
        if email_user and not google_user:
            email_user.google_id = google_id
            email_user.is_confirmed = True
            if not email_user.first_name:
                email_user.first_name = first_name
            if not email_user.last_name:
                email_user.last_name = last_name
            email_user.save()
            return email_user

        # The e-mail address on an existing google user is being updated.
        if not email_user and google_user:
            google_user.email = email
            google_user.is_confirmed = True
            if not google_user.first_name:
                google_user.first_name = first_name
            if not google_user.last_name:
                google_user.last_name = last_name
            google_user.save()
            return google_user

        # There is one user that matches both e-mail and google ID. This is
        # a typical login scenario.
        if email_user and google_user and email_user == google_user:
            if not email_user.first_name:
                email_user.first_name = first_name
            if not email_user.last_name:
                email_user.last_name = last_name
            email_user.save()
            return email_user

        # The last scenario is that there are already two users, one with the
        # e-mail address and one with the google ID. This is a problem.
        if email_user and google_user and email_user != google_user:
            raise serializers.ValidationError(
                "Account conflict. Please contact at staff member.")


class GoogleSaveDeserializer(GoogleLoginDeserializer):

    def create(self, validated_data):
        user = validated_data.get('user')

        user_info = self.user_info
        email = user_info['email']
        google_id = user_info['google_id']
        first_name = user_info['first_name']
        last_name = user_info['last_name']

        email_user = User.objects.filter(email__iexact=email).first()
        google_user = User.objects.filter(google_id=google_id).first()

        if email_user and google_user and email_user != google_user:
            raise serializers.ValidationError(
                "Account conflict. Please contact at staff member.")

        if email_user and google_user and email_user == google_user:
            user = email_user
        elif email_user and not google_user:
            user = email_user
        elif google_user and not email_user:
            user = google_user


        user.email = email
        user.google_id = google_id
        user.first_name = first_name
        user.last_name = last_name
        user.is_confirmed = True
        user.save()

        return user
