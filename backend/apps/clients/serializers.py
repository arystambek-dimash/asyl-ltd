from rest_framework import serializers
from decimal import Decimal
from .models import Client, Department, Store


class DepartmentSerializer(serializers.ModelSerializer):
    # Код фиксирован — переименовать можно только название.
    code = serializers.CharField(read_only=True)

    class Meta:
        model = Department
        fields = ["id", "code", "name"]


class ClientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    debt_total = serializers.SerializerMethodField()
    department = serializers.ChoiceField(choices=Client.DEPARTMENTS, default="main")
    manager_name = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "phone", "name",
                  "country", "iin", "bank", "bank_account", "user",
                  "department", "manager", "manager_name", "debt_total"]

    def get_debt_total(self, obj):
        total = sum((o.remaining_amount for o in obj.orders.all() if o.is_debt),
                    Decimal("0"))
        return str(total.quantize(Decimal("0.01")))

    def get_manager_name(self, obj):
        return obj.manager.username if obj.manager else None

    def _is_dept2_only(self, user) -> bool:
        return (not user.is_superuser
                and not user.has_perm_code("clients.create")
                and user.has_perm_code("dept2.create"))

    def validate(self, attrs):
        # Менеджер Отдела 2 создаёт клиентов только в своём разделе и на себя —
        # серверное правило, не полагаемся на данные формы.
        user = self.context["request"].user
        if self._is_dept2_only(user):
            attrs["department"] = "field"
            attrs["manager"] = user
        elif attrs.get("department") == "field" and not attrs.get("manager"):
            instance_manager = getattr(self.instance, "manager", None)
            attrs["manager"] = instance_manager or user
        return attrs


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "client", "name", "address", "phone",
                  "payment_schedule_type", "payment_days", "contract_signed_at"]
