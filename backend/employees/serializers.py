from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from .models import Employee

User = get_user_model()


class EmployeeSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username")
    password = serializers.CharField(write_only=True, required=True, min_length=6)
    role_name = serializers.CharField(source="role.name", read_only=True)
    name = serializers.CharField(read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "username", "password", "first_name", "last_name",
                  "phone", "position", "role", "role_name", "name", "is_active"]

    @transaction.atomic
    def create(self, validated_data):
        user_data = validated_data.pop("user")
        password = validated_data.pop("password")
        username = user_data["username"]
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError(
                {"detail": "Пользователь с таким логином уже существует", "code": "username_taken"})
        user = User.objects.create_user(username=username, password=password)
        return Employee.objects.create(user=user, **validated_data)
