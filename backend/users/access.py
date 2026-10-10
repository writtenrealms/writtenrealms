from django.contrib.auth.backends import ModelBackend
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from system.access import email_admitted, require_platform_access


def require_allowed_email(email=None, *, user=None):
    require_platform_access(email, user=user)


class AllowedEmailModelBackend(ModelBackend):
    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and email_admitted(
            user.email, is_staff=user.is_staff)


class AllowedEmailJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        require_allowed_email(user=user)
        return user


class AllowedEmailSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            require_allowed_email(user=result[0])
        return result


class AllowedEmailTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.exceptions import InvalidToken
        refresh = self.token_class(attrs['refresh'])
        user = get_user_model().objects.filter(pk=refresh.get('user_id'), is_active=True).first()
        if user is None:
            raise InvalidToken('No active account found.')
        require_allowed_email(user=user)
        # Rebuild the claim from the current account, including older tokens.
        refresh['email'] = user.email
        refresh['is_staff'] = bool(user.is_staff)
        return super().validate({'refresh': str(refresh)})
