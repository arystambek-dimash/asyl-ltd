import uuid

from django.db import transaction
from rest_framework import serializers

from .models import StockItem, StockMovement, StockReceipt, Warehouse
from .services import DEFAULT_WAREHOUSE_CODE, get_compatibility_warehouse


def _lock_warehouse_configuration():
    """Serialize default promotion on the immutable compatibility anchor."""
    return Warehouse.objects.select_for_update().get(
        code=DEFAULT_WAREHOUSE_CODE,
    )


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = [
            "id",
            "code",
            "name",
            "address",
            "is_active",
            "is_default",
        ]
        read_only_fields = ["id"]
        # Default promotion is serialized explicitly below: the current row
        # must be cleared before the partial unique constraint can accept the
        # new one. DRF's generated constraint validator checks too early.
        validators = []
        extra_kwargs = {
            "code": {"required": False},
            "address": {"required": False},
            "is_default": {"validators": []},
        }

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Укажите название склада")
        # PostgreSQL clusters created with the C locale only fold ASCII in
        # ILIKE/lower(). Python's Unicode casefold keeps the operator-facing
        # validation correct for Cyrillic names too; the DB expression remains
        # the final concurrent-write guard.
        duplicates = Warehouse.objects.all()
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        normalized = value.casefold()
        names = duplicates.values_list("name", flat=True)
        if any(name.strip().casefold() == normalized for name in names):
            raise serializers.ValidationError("Склад с таким названием уже существует")
        return value

    def validate_code(self, value):
        return value.strip().lower()

    def _validate_state(self, attrs, instance):
        is_default = attrs.get(
            "is_default",
            instance.is_default if instance is not None else False,
        )
        is_active = attrs.get(
            "is_active",
            instance.is_active if instance is not None else True,
        )
        if is_default and not is_active:
            raise serializers.ValidationError(
                {"is_active": "Основной склад должен быть активным"}
            )
        if (
            instance is not None
            and instance.is_default
            and attrs.get("is_default") is False
        ):
            raise serializers.ValidationError(
                {"is_default": ("Сначала назначьте другой склад основным")}
            )
        if instance is not None and instance.code == DEFAULT_WAREHOUSE_CODE:
            if attrs.get("code", instance.code) != DEFAULT_WAREHOUSE_CODE:
                raise serializers.ValidationError(
                    {"code": "Код системного склада нельзя изменить"}
                )
            if not is_active:
                raise serializers.ValidationError(
                    {"is_active": "Системный склад должен оставаться активным"}
                )
        return attrs

    def validate(self, attrs):
        return self._validate_state(attrs, self.instance)

    @transaction.atomic
    def create(self, validated_data):
        # The operator-facing form only asks for a name.  Preserve explicitly
        # supplied legacy integration codes, otherwise create an opaque stable
        # key that never changes when the display name is edited.
        validated_data.setdefault("code", f"wh-{uuid.uuid4().hex[:12]}")
        if validated_data.get("is_default"):
            _lock_warehouse_configuration()
            Warehouse.objects.select_for_update().filter(is_default=True).update(
                is_default=False
            )
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        # Always take the stable anchor first. This gives every warehouse
        # update the same lock order and prevents two concurrent promotions
        # from both working from a stale view of the previous default.
        _lock_warehouse_configuration()
        instance = Warehouse.objects.select_for_update().get(pk=instance.pk)
        self._validate_state(validated_data, instance)
        if validated_data.get("is_default") and not instance.is_default:
            Warehouse.objects.select_for_update().filter(is_default=True).exclude(
                pk=instance.pk
            ).update(is_default=False)
        return super().update(instance, validated_data)


class EffectiveWarehouseRepresentationMixin:
    """Represent rollback-created NULL rows as belonging to main warehouse."""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("warehouse") is None:
            compatibility = get_compatibility_warehouse()
            data["warehouse"] = compatibility.pk
            data["warehouse_name"] = compatibility.name
            data["warehouse_code"] = compatibility.code
        return data


class StockAdjustmentSerializer(serializers.Serializer):
    warehouse = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.filter(is_active=True),
        required=False,
    )
    product = serializers.IntegerField()
    delta = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class StockTransferSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    from_warehouse = serializers.IntegerField()
    to_warehouse = serializers.IntegerField()
    bags = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class StockItemSerializer(
    EffectiveWarehouseRepresentationMixin,
    serializers.ModelSerializer,
):
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True, default=None
    )
    warehouse_code = serializers.CharField(
        source="warehouse.code", read_only=True, allow_null=True, default=None
    )
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    grade = serializers.CharField(source="product.name", read_only=True)
    color = serializers.CharField(source="product.color", read_only=True)
    color_label = serializers.CharField(
        source="product.get_color_display", read_only=True
    )
    packaging = serializers.SerializerMethodField()
    weight_kg = serializers.DecimalField(
        source="product.weight_kg", max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = StockItem
        fields = [
            "id",
            "warehouse",
            "warehouse_name",
            "warehouse_code",
            "product",
            "product_label",
            "grade",
            "color",
            "color_label",
            "packaging",
            "weight_kg",
            "bags",
        ]

    def get_packaging(self, obj):
        return f"{int(obj.product.weight_kg)} кг"


class StockReceiptSerializer(
    EffectiveWarehouseRepresentationMixin,
    serializers.ModelSerializer,
):
    warehouse = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.filter(is_active=True),
        required=False,
    )
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True, default=None
    )
    warehouse_code = serializers.CharField(
        source="warehouse.code", read_only=True, allow_null=True, default=None
    )

    class Meta:
        model = StockReceipt
        fields = [
            "id",
            "warehouse",
            "warehouse_name",
            "warehouse_code",
            "product",
            "bags",
            "received_at",
            "received_by",
        ]
        read_only_fields = ["received_at", "received_by"]


class StockMovementSerializer(
    EffectiveWarehouseRepresentationMixin,
    serializers.ModelSerializer,
):
    warehouse_name = serializers.CharField(
        source="warehouse.name", read_only=True, allow_null=True, default=None
    )
    warehouse_code = serializers.CharField(
        source="warehouse.code", read_only=True, allow_null=True, default=None
    )
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True, default=None
    )

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "warehouse",
            "warehouse_name",
            "warehouse_code",
            "product",
            "product_label",
            "delta",
            "balance_after",
            "reason",
            "note",
            "created_at",
            "created_by_name",
            "transfer_id",
        ]
