from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers, generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from apps.clients.models import Client
from config.throttles import RegisterRateThrottle

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default="")
    company_name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=50)
    iin = serializers.CharField(min_length=12, max_length=12)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Это имя пользователя уже занято")
        return value

    def validate_password(self, value):
        # Публичная регистрация — применяем настроенные правила паролей,
        # а не только минимальную длину.
        candidate = User(
            username=str(self.initial_data.get("username", "")),
            first_name=str(self.initial_data.get("first_name", "")),
            last_name=str(self.initial_data.get("last_name", "")),
        )
        try:
            validate_password(value, user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value

    def validate_iin(self, value):
        value = value.strip()
        if not value.isdigit():
            raise serializers.ValidationError("ИИН/БИН должен состоять из 12 цифр")
        return value

    def validate_company_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("Введите название ТОО / ИП")
        return value

    @transaction.atomic
    def create(self, data):
        user = User.objects.create_user(
            username=data["username"], password=data["password"], is_client=True)
        Client.objects.create(
            user=user, first_name=data["first_name"], last_name=data.get("last_name", ""),
            company_name=data["company_name"], phone=data["phone"], iin=data["iin"])
        return user


class RegisterView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    throttle_classes = [RegisterRateThrottle]
    serializer_class = RegisterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {"access": str(refresh.access_token), "refresh": str(refresh)}, status=201)
