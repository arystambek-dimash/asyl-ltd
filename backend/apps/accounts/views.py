from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView, TokenViewBase

from config.throttles import LoginRateThrottle

from .serializers import (
    InitialPasswordSerializer,
    MeSerializer,
    RevocableTokenRefreshSerializer,
)


class RevocableTokenRefreshView(TokenRefreshView):
    serializer_class = RevocableTokenRefreshSerializer


class InitialPasswordView(TokenViewBase):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]
    serializer_class = InitialPasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {"access": str(refresh.access_token), "refresh": str(refresh)}
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)
