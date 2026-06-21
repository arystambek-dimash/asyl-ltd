from rest_framework import serializers
from .models import Client


class ClientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)

    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "phone", "name",
                  "country", "requisites", "user"]
