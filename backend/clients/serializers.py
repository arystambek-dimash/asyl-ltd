from rest_framework import serializers
from decimal import Decimal
from .models import Client


class ClientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    debt_total = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "phone", "name",
                  "country", "iin", "bank", "bank_account", "user", "debt_total"]

    def get_debt_total(self, obj):
        total = Decimal("0")
        for o in obj.orders.all():
            if o.status == "cancelled" or o.is_fully_paid:
                continue
            total += o.total_amount - o.paid_total
        return str(total.quantize(Decimal("0.01")))
