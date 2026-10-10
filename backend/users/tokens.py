from rest_framework_simplejwt.tokens import RefreshToken


def build_token_response(user):
    from users.access import require_allowed_email
    require_allowed_email(user=user)
    refresh = RefreshToken.for_user(user)
    refresh['email'] = user.email
    refresh['is_staff'] = bool(user.is_staff)
    access_token = str(refresh.access_token)
    return {
        'access': access_token,
        'refresh': str(refresh),
        'token': access_token,
    }
