from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.throttles import EmailThrottle
from founders import services
from founders.models import FounderPass, PassTransfer
from system.models import SiteControl
from users.serializers import UserSerializer
from users.tokens import build_token_response


class EmailInput(serializers.Serializer):
    email = serializers.EmailField(max_length=255)


class TokenInput(serializers.Serializer):
    token = serializers.CharField(max_length=200)


def validated(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class PassList(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        passes = list(FounderPass.objects.filter(user=request.user).select_related(
            'program__world__config', 'gift').order_by('pk'))
        # Deadline correctness does not depend on beat being on time.
        for founder_pass in passes:
            deadline = founder_pass.program.claim_deadline
            if not founder_pass.claimed_at and deadline and deadline <= timezone.now():
                with transaction.atomic():
                    current = services.settle_pass(services.locked_pass(founder_pass.pk, request.user))
                    founder_pass.claimed_at, founder_pass.claim_reason = current.claimed_at, current.claim_reason
        pending = {item.founder_pass_id: item for item in PassTransfer.objects.filter(
            founder_pass__in=passes, active=True, expires_at__gt=timezone.now())}
        enabled = SiteControl.objects.filter(name='prod', founders_enabled=True).exists()
        data = []
        for item in passes:
            gift = getattr(item, 'gift', None)
            transfer = pending.get(item.pk)
            available = enabled and item.program.enabled
            data.append({
                'id': item.pk, 'world_id': item.program.world_id, 'world_name': item.program.world.name,
                'world_background': getattr(item.program.world.config, 'large_background', None) or None,
                'kind': item.kind, 'claimed_at': item.claimed_at, 'claim_reason': item.claim_reason,
                'claim_deadline': item.program.claim_deadline,
                'can_claim': item.claimed_at is None,
                'can_transfer': available and item.kind == 'direct' and item.claimed_at is None,
                'can_invite': available and item.kind == 'direct' and item.claimed_at is not None and gift is None,
                # Never expose a recipient's later private email change.
                'invited_email': gift.issued_email if gift else None,
                'transfer_email': transfer.destination_email if transfer else None,
                'transfer_expires_at': transfer.expires_at if transfer else None,
            })
        return Response({'passes': data})


class PassAction(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [EmailThrottle]

    def post(self, request, pass_id, action):
        try:
            if action == 'claim':
                services.claim(pass_id, request.user)
            elif action == 'invite':
                services.invite(pass_id, request.user, **validated(EmailInput, request.data))
            elif action == 'transfer':
                services.start_transfer(pass_id, request.user, **validated(EmailInput, request.data))
            elif action == 'cancel-transfer':
                services.cancel_transfer(pass_id, request.user)
            else:
                raise ValidationError('Unknown pass action.')
        except IntegrityError:
            raise ValidationError('The destination changed during this request. Refresh and try again; no extra pass was issued.')
        return Response({'ok': True})


class ConfirmTransfer(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [EmailThrottle]

    def post(self, request):
        user = services.complete_transfer(**validated(TokenInput, request.data))
        return Response({**build_token_response(user), 'user': UserSerializer(user).data})


class RequestEmailChange(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [EmailThrottle]

    def post(self, request):
        from users.email_changes import request_email_change
        request_email_change(request.user, **validated(EmailInput, request.data))
        return Response({'ok': True})


class ConfirmEmailChange(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [EmailThrottle]

    def post(self, request):
        from users.email_changes import confirm_email_change
        user = confirm_email_change(**validated(TokenInput, request.data))
        return Response({**build_token_response(user), 'user': UserSerializer(user).data})
