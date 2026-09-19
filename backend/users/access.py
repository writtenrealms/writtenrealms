from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from rest_framework.exceptions import PermissionDenied
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from core.access_policy import email_is_allowed


def require_allowed_email(email):
    if not email_is_allowed(email, settings.ACCESS_EMAIL_ALLOWLIST):
        raise PermissionDenied('This server is invitation-only. Contact its administrator for access.')


class AllowedEmailModelBackend(ModelBackend):
    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and email_is_allowed(
            user.email, settings.ACCESS_EMAIL_ALLOWLIST)


class AllowedEmailJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        require_allowed_email(user.email)
        return user


class AllowedEmailSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            require_allowed_email(result[0].email)
        return result


class AllowedEmailTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.exceptions import InvalidToken
        refresh = self.token_class(attrs['refresh'])
        user = get_user_model().objects.filter(pk=refresh.get('user_id'), is_active=True).first()
        if user is None:
            raise InvalidToken('No active account found.')
        require_allowed_email(user.email)
        # Rebuild the claim from the current account, including older tokens.
        refresh['email'] = user.email
        return super().validate({'refresh': str(refresh)})
