from rest_framework_simplejwt.tokens import RefreshToken


def build_token_response(user):
    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)
    return {
        'access': access_token,
        'refresh': str(refresh),
        'token': access_token,
    }
