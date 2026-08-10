from decimal import Decimal

from apps.common.money import (
    as_money_strings,
    money_string,
    primary_currency
)
from apps.orders.debt import debt_by_currency
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

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
    password_change_required = serializers.BooleanField(
        source="user.must_change_password",
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
            "user",
            "portal_access_enabled",
            "password_change_required",
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
        # Direct domain use keeps the complete representation. Every DRF HTTP
        # response supplies request in serializer context and enforces the
        # reports permission here, including nested/create representations.
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
        cached = getattr(obj, "_debt_totals_cache", None)
        if cached is None:
            cached = debt_by_currency(obj.orders.all())
            obj._debt_totals_cache = cached
        return cached

    def get_debt_by_currency(self, obj):
        return as_money_strings(self._debt(obj))

    def get_debt_currency(self, obj):
        return primary_currency(self._debt(obj), fallback=obj.currency)

    def get_debt_total(self, obj):
        totals = self._debt(obj)
        return money_string(totals.get(self.get_debt_currency(obj), Decimal("0")))


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
        ]

    def to_representation(self, instance):
        return ClientReadSerializer(instance, context=self.context).data

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
        user_update_fields = []
        for field in ("first_name", "last_name"):
            if field not in validated_data:
                continue
            value = validated_data.pop(field)
            if getattr(instance.user, field) != value:
                setattr(instance.user, field, value)
                user_update_fields.append(field)

        instance = super().update(instance, validated_data)
        if user_update_fields:
            instance.user.save(update_fields=user_update_fields)
        return instance


class ClientPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_password(self, value):
        try:
            validate_password(value, user=self.context["client"].user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value

    def save(self, **kwargs):
        client = self.context["client"]
        user = client.user
        user.set_password(self.validated_data["password"])
        user.is_active = True
        user.must_change_password = True
        user.save(update_fields=["password", "is_active", "must_change_password"])
        return client


class StoreSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(
        source="client.name",
        read_only=True,
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
            "contract_signed_at"
        ]
