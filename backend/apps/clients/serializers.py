from django.db import transaction
from rest_framework import serializers

from apps.accounts.passwords import validate_new_password
from apps.orders.debt import debt_by_currency, debt_fields
from apps.sales.access import assigned_department_id, scope_by_client_department
from apps.sales.models import Department

from .managers import sync_user_names
from .models import Client, Store


class ClientReadSerializer(serializers.ModelSerializer):
    FINANCIAL_FIELDS = frozenset(
        {"debt_total", "debt_currency", "debt_by_currency"}
    )

    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    name = serializers.CharField(read_only=True)
    portal_access_enabled = serializers.SerializerMethodField()
    department_name = serializers.CharField(
        source="department.name",
        allow_null=True,
        read_only=True,
    )
    debt_total = serializers.SerializerMethodField()
    debt_currency = serializers.SerializerMethodField()
    debt_by_currency = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "company_name",
            "phone",
            "name",
            "country",
            "iin",
            "bank",
            "bank_account",
            "department",
            "department_name",
            "user",
            "portal_access_enabled",
            "currency",
            "debt_total",
            "debt_currency",
            "debt_by_currency",
            "created_at",
        ]
        read_only_fields = ["user", "created_at"]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None and not request.user.has_perm_code("reports.view"):
            for field_name in self.FINANCIAL_FIELDS:
                fields.pop(field_name, None)
        return fields

    def get_portal_access_enabled(self, obj):
        user = obj.user
        return (
            user.is_active
            and user.has_usable_password()
            and not user.must_change_password
        )

    def _debt(self, obj) -> dict:
        cached = getattr(obj, "_debt_fields_cache", None)
        if cached is None:
            cached = debt_fields(debt_by_currency(obj.orders.all()), fallback=obj.currency)
            obj._debt_fields_cache = cached
        return cached

    def get_debt_by_currency(self, obj):
        return self._debt(obj)["debt_by_currency"]

    def get_debt_currency(self, obj):
        return self._debt(obj)["debt_currency"]

    def get_debt_total(self, obj):
        return self._debt(obj)["debt_total"]


class ClientCreateUpdateSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
    )

    class Meta:
        model = Client
        fields = [
            "first_name",
            "last_name",
            "company_name",
            "phone",
            "country",
            "iin",
            "bank",
            "bank_account",
            "currency",
            "department",
        ]

    def to_representation(self, instance):
        return ClientReadSerializer(instance, context=self.context).data

    def validate_department(self, value):
        if value is None or value.is_active:
            return value
        if self.instance is not None and self.instance.department_id == value.pk:
            return value
        raise serializers.ValidationError("Выберите действующий отдел продаж")

    def validate(self, attrs):
        request = self.context.get("request")
        department_id = assigned_department_id(request.user) if request else None
        if department_id is None:
            return attrs

        if self.instance is None:
            department = Department.objects.get(pk=department_id)
            if not department.is_active:
                raise serializers.ValidationError({
                    "department": (
                        "Закреплённый отдел продаж отключён — "
                        "обратитесь к администратору"
                    )
                })
            # The employee's ownership scope is authoritative even when an old
            # or malicious client submits another department.
            attrs["department"] = department
            return attrs

        if "department" in attrs:
            department = attrs["department"]
            if department is None or department.pk != department_id:
                raise serializers.ValidationError({
                    "department": "Нельзя передать клиента в другой отдел"
                })
        return attrs

    def create(self, validated_data):
        first_name = validated_data.pop("first_name")
        last_name = validated_data.pop("last_name", "")
        return Client.objects.create_with_user(
            first_name=first_name,
            last_name=last_name,
            **validated_data,
        )

    @transaction.atomic
    def update(self, instance, validated_data):
        names = {
            field: validated_data.pop(field)
            for field in ("first_name", "last_name")
            if field in validated_data
        }
        instance = super().update(instance, validated_data)
        sync_user_names(instance.user, names)
        return instance


class ClientPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_password(self, value):
        return validate_new_password(value, user=self.context["client"].user)

    def save(self, **kwargs):
        client = self.context["client"]
        user = client.user
        user.set_password(self.validated_data["password"])
        user.is_active = True
        user.must_change_password = True
        user.save(update_fields=["password", "is_active", "must_change_password"])
        return client


class StoreSerializer(serializers.ModelSerializer):
    # Последний допустимый день графика оплат: число месяца или день недели (Пн=1).
    SCHEDULE_MAX_DAY = {"monthly": 31, "weekly": 7}

    client_name = serializers.CharField(
        source="client.name",
        read_only=True,
    )
    payment_schedule_type = serializers.ChoiceField(
        choices=Store.SCHEDULE_TYPES, required=False,
    )

    class Meta:
        model = Store
        fields = [
            "id",
            "client",
            "client_name",
            "name",
            "address",
            "phone",
            "payment_schedule_type",
            "payment_days",
        ]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None:
            fields["client"].queryset = scope_by_client_department(
                Client.objects.all(),
                request.user,
            )
        return fields

    def validate(self, attrs):
        # Тот же график, что проверяет форма магазина: кривые дни (строки, 0,
        # 32) навсегда закрыли бы окно оплаты по отгруженным заказам.
        if "payment_schedule_type" not in attrs and "payment_days" not in attrs:
            return attrs
        schedule_type = attrs.get(
            "payment_schedule_type",
            self.instance.payment_schedule_type if self.instance else "none",
        )
        days = attrs.get(
            "payment_days",
            self.instance.payment_days if self.instance else [],
        )
        if schedule_type == "none":
            attrs["payment_days"] = []
            return attrs
        max_day = self.SCHEDULE_MAX_DAY.get(schedule_type)
        if max_day is None:
            # Старый магазин с типом вне списка: дни без типа не проверить.
            raise serializers.ValidationError(
                {"payment_schedule_type": "Выберите тип графика оплат."}
            )
        if (
            not isinstance(days, list)
            or not days
            or any(
                isinstance(day, bool) or not isinstance(day, int)
                or not 1 <= day <= max_day
                for day in days
            )
        ):
            unit = "числа месяца от 1 до 31" if schedule_type == "monthly" else "дни недели от 1 до 7"
            raise serializers.ValidationError(
                {"payment_days": f"Укажите {unit}."}
            )
        attrs["payment_days"] = sorted(set(days))
        return attrs
