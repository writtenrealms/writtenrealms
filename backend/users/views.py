import logging

from django.contrib.auth import get_user_model
from django.utils import timezone

from rest_framework import (
    permissions,
    status,
    viewsets)
from rest_framework.views import APIView
from rest_framework.response import Response

from rest_framework_simplejwt.views import TokenRefreshView

from core import permissions as core_permissions
from core.throttles import EmailThrottle
from core.ip import get_ip
from users import serializers as user_serializers
from users.tokens import build_token_response


User = get_user_model()

security_logger = logging.getLogger('security')

class LoggedInUserDetail(APIView):
    permission_classes = (
        permissions.IsAuthenticated,
    )

    def get(self, request, format=None):
        serializer = user_serializers.UserSerializer(request.user)
        return Response(serializer.data)

    def put(self, request, format=None):
        serializer = user_serializers.UserSerializer(
            request.user,
            data=request.data,
            partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class UserViewSet(viewsets.ModelViewSet):
    """
    Although a 'is own user' permission class could be added here, instead
    the qset filtering by request.user ensures that anyone other than the
    logged in user gets a 404.
    """
    permission_classes = (
        permissions.IsAuthenticated,
    )

    serializer_class = user_serializers.UserSerializer

    def get_queryset(self):
        return User.objects.filter(pk=self.request.user.pk)


user_detail = UserViewSet.as_view({
    'put': 'partial_update',
})


class PatronsView(APIView):

    authentication_classes = ()
    permission_classes = ()

    def get(self, request, tier=None, format=None):
        qs = User.objects.filter(name_recognition=True)
        if tier and tier.lower() == 'housing':
            qs = qs.filter(player_housing=True)
        if tier and tier.lower() == 'multiplayer':
            qs = qs.filter(multiplayer_worlds=True)

        return Response({
            'data': user_serializers.UserSerializer(qs, many=True).data
        })

patrons = PatronsView.as_view()


class RequestLoginLink(APIView):
    authentication_classes = ()
    permission_classes = ()
    throttle_classes = (EmailThrottle,)

    def post(self, request, format=None):
        ip = get_ip(request)
        security_logger.info("Login link request from IP %s" % ip)

        serializer = user_serializers.EmailLoginRequestSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({}, status=status.HTTP_201_CREATED)


request_login_link = RequestLoginLink.as_view()


class ConfirmLoginLink(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request, format=None):
        serializer = user_serializers.EmailLoginConfirmSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        response_data = build_token_response(user)
        response_data['user'] = user_serializers.UserSerializer(user).data
        return Response(response_data, status=status.HTTP_201_CREATED)


confirm_login_link = ConfirmLoginLink.as_view()


refresh_jwt_token = TokenRefreshView.as_view()


class Signup(APIView):

    authentication_classes = ()
    permission_classes = ()

    def post(self, request, format=None):
        if request.user.is_authenticated:
            serializer = user_serializers.UserSerializer(request.user)
            return Response({'user': serializer.data},
                            status=status.HTTP_200_OK)

        serializer = user_serializers.SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        ip = get_ip(request)
        user.ip = ip
        user.save()

        security_logger.info("New user %s signed up from IP %s" % (user.email, ip))

        user_data = user_serializers.UserSerializer(user).data
        return Response({
            'user': user_data,
            'login_link_sent': True,
        }, status=status.HTTP_201_CREATED)

signup = Signup.as_view()


class GoogleLogin(APIView):

    authentication_classes = ()
    permission_classes = ()

    def post(self, request, format=None):
        serializer = user_serializers.GoogleLoginDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        user.last_login = timezone.now()
        user.save()
        user_data = user_serializers.UserSerializer(user).data
        response_data = build_token_response(user)
        return Response({
            **response_data,
            'user': user_data,
        }, status=status.HTTP_201_CREATED)

google_login = GoogleLogin.as_view()


class Save(APIView):

    permission_classes = (
        core_permissions.IsTemporaryUser,
    )

    def post(self, request, format=None):
        serializer = user_serializers.SaveTempCharSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save(user=request.user)

        user_data = user_serializers.UserSerializer(user).data
        response_data = build_token_response(user)
        return Response({
            **response_data,
            'user': user_data,
            'login_link_sent': True,
        }, status=status.HTTP_201_CREATED)

save = Save.as_view()


class GoogleSave(APIView):

    permission_classes = (
        core_permissions.IsTemporaryUser,
    )

    def post(self, request, format=None):
        temp_user = request.user
        player = temp_user.characters.first()

        serializer = user_serializers.GoogleSaveDeserializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save(user=request.user)

        user.is_temporary = False
        user.save()

        player.user = user
        player.save(update_fields=['user'])

        user_data = user_serializers.UserSerializer(user).data
        response_data = build_token_response(user)
        return Response({
            **response_data,
            'user': user_data,
        }, status=status.HTTP_201_CREATED)

google_save = GoogleSave.as_view()


class ForgotPassword(APIView):
    "Send login link (legacy endpoint)"
    authentication_classes = ()
    permission_classes = ()
    throttle_classes = (EmailThrottle,)

    def post(self, request, format=None):
        ip = get_ip(request)
        security_logger.info("Login link request from IP %s" % ip)

        serializer = user_serializers.EmailLoginRequestSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({}, status=status.HTTP_201_CREATED)

forgot_password = ForgotPassword.as_view()


class ResetPassword(APIView):
    "Password reset disabled in passwordless mode"
    authentication_classes = ()
    permission_classes = ()

    def post(self, request, format=None):
        return Response({
            'detail': 'Password reset is disabled. Use a login link instead.',
        }, status=status.HTTP_410_GONE)

reset_password = ResetPassword.as_view()


class EmailConfirmationView(APIView):
    "Sets a user account as being confirmed via e-mail"
    authentication_classes = ()
    permission_classes = ()

    def post(self, request, format=None):
        serializer = user_serializers.EmailConfirmationSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        user_data = user_serializers.UserSerializer(user).data
        if not request.user.is_authenticated:
            token_data = build_token_response(user)
        else:
            token_data = {'token': None, 'access': None, 'refresh': None}
        return Response({
            **token_data,
            'user': user_data,
        }, status=status.HTTP_201_CREATED)

confirm_email = EmailConfirmationView.as_view()


class ResendConfirmationView(APIView):
    permission_classes = (
        permissions.IsAuthenticated,
    )

    throttle_classes = (EmailThrottle,)

    def post(self, request, format=None):
        from rest_framework import serializers
        if request.user.is_invalid:
            raise serializers.ValidationError("Invalid e-mail address.")

        serializer = user_serializers.EmailLoginRequestSerializer(
            data={'email': request.user.email})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({}, status=status.HTTP_201_CREATED)

resend_confirmation = ResendConfirmationView.as_view()


class AcceptCodeOfConduct(APIView):

    permission_classes = (
        permissions.IsAuthenticated,
    )

    def post(self, request, format=None):
        user = request.user
        user.cod_accepted = True
        user.save()
        user_data = user_serializers.UserSerializer(user).data
        return Response(user_data, status=status.HTTP_201_CREATED)

accept_cod = AcceptCodeOfConduct.as_view()
