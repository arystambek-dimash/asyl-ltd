from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenViewBase,
)

from config.throttles import LoginRateThrottle

from .credentials import token_pair
from .serializers import (
    InitialPasswordSerializer,
    MeSerializer,
    PasswordChangeAwareTokenObtainPairSerializer,
    RevocableTokenRefreshSerializer,
)


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Логин под отдельным жёстким лимитом (защита от подбора пароля)."""
    throttle_classes = [LoginRateThrottle]
    serializer_class = PasswordChangeAwareTokenObtainPairSerializer


class RevocableTokenRefreshView(TokenRefreshView):
    serializer_class = RevocableTokenRefreshSerializer


class InitialPasswordView(TokenViewBase):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]
    serializer_class = InitialPasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(token_pair(serializer.save()))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)
