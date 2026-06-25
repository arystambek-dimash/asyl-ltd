from rest_framework import serializers
from decimal import Decimal
from .models import Client, Store


class ClientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    debt_total = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "phone", "name",
                  "country", "iin", "bank", "bank_account", "user", "debt_total"]

    def get_debt_total(self, obj):
        total = sum((o.remaining_amount for o in obj.orders.all() if o.is_debt),
                    Decimal("0"))
        return str(total.quantize(Decimal("0.01")))


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "client", "name", "address", "phone",
                  "payment_schedule_type", "payment_days", "contract_signed_at"]
