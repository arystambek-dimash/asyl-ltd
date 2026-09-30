from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenViewBase

from apps.cameras.api_views.access import clear_camera_cookie
from config.throttles import LoginRateThrottle, TokenRefreshRateThrottle

from .credentials import (
    REFRESH_COOKIE,
    SessionEndpointMixin,
    clear_refresh_cookie,
    end_session,
    refresh_session,
    session_response,
    start_session,
)
from .serializers import (
    InitialPasswordSerializer,
    LoginSerializer,
    MeSerializer,
)

# Вьюхи сессии — на TokenViewBase: без аутентификации и прав, а отказ сессии
# отдаётся 401 (у APIView без WWW-Authenticate он превратился бы в 403).


class LoginView(SessionEndpointMixin, TokenViewBase):
    """Логин под отдельным жёстким лимитом (защита от подбора пароля)."""

    throttle_classes = [LoginRateThrottle]
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return start_session(serializer.user)


class RevocableTokenRefreshView(SessionEndpointMixin, TokenViewBase):
    """Новый access по refresh-куке; тело запроса не читается."""

    throttle_classes = [TokenRefreshRateThrottle]

    def post(self, request):
        return session_response(refresh_session(request.COOKIES.get(REFRESH_COOKIE)))

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        # Сессия кончилась — кука больше не нужна. 403/429/5xx её не трогают.
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            clear_refresh_cookie(response)
        return response


class LogoutView(SessionEndpointMixin, TokenViewBase):
    """Выход: refresh — в чёрный список, refresh- и камерная куки сняты. Всегда 204."""

    throttle_classes = [TokenRefreshRateThrottle]

    def post(self, request):
        end_session(request.COOKIES.get(REFRESH_COOKIE))
        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_refresh_cookie(response)
        clear_camera_cookie(response, request)
        return response


class InitialPasswordView(SessionEndpointMixin, TokenViewBase):
    throttle_classes = [LoginRateThrottle]
    serializer_class = InitialPasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return start_session(serializer.save())


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)
