from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from apps.catalog.models import Product
from apps.clients.models import Store
from apps.orders.models import Order, OrderItem


class CatalogProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    weight_kg = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )
    available_bags = serializers.SerializerMethodField()

    class Meta:
        model = Product
        # Цену клиенту не показываем — её назначает оператор при подтверждении.
        fields = ["id", "label", "weight_kg", "available_bags"]

    def get_available_bags(self, obj):
        s = getattr(obj, "stock", None)
        return s.bags if s and s.bags > 0 else 0


class PortalOrderItemSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    product_label = serializers.CharField(source="product.__str__", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "quantity"]


class PortalOrderSerializer(serializers.ModelSerializer):
    items = PortalOrderItemSerializer(many=True)
    settlement_intent = serializers.ChoiceField(
        choices=Order.SETTLEMENT_INTENTS, required=False, default="debt")
    transport_type = serializers.ChoiceField(
        choices=Order.TRANSPORT_TYPES, required=False, default="truck")
    store = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.all(), required=False, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True, default=None)
    total_amount = serializers.SerializerMethodField()
    paid_total = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "status", "payment_status", "settlement_intent", "transport_type",
                  "store", "store_name",
                  "items", "total_amount", "paid_total", "remaining_amount",
                  "truck_number", "debt_requested", "debt_override", "created_at"]
        read_only_fields = ["status", "payment_status",
                            "truck_number", "debt_requested", "debt_override"]

    def validate_store(self, store):
        if store is None:
            return store
        client = self._client()
        if store.client_id != client.id:
            raise serializers.ValidationError("Магазин принадлежит другому клиенту.")
        return store

    def _client(self):
        try:
            return self.context["request"].user.client_profile
        except ObjectDoesNotExist as exc:
            raise serializers.ValidationError({
                "detail": "К аккаунту не привязан профиль клиента.",
                "code": "missing_client_profile",
            }) from exc

    def _money_visible(self, obj):
        return obj.status not in ("draft", "pending", "rejected", "cancelled")

    def _amount(self, value):
        return str(value.quantize(Decimal("0.01")))

    def get_total_amount(self, obj):
        if not self._money_visible(obj):
            return None
        return self._amount(obj.total_amount)

    def get_paid_total(self, obj):
        if not self._money_visible(obj):
            return None
        return self._amount(obj.paid_total)

    def get_remaining_amount(self, obj):
        if not self._money_visible(obj):
            return None
        return self._amount(obj.remaining_amount)

    def create(self, validated_data):
        items = validated_data.pop("items")
        intent = validated_data.get("settlement_intent", "debt")
        transport = validated_data.get("transport_type", "truck")
        store = validated_data.get("store")
        client = self._client()
        order = Order.objects.create(client=client, status="pending",
                                     settlement_intent=intent, store=store,
                                     transport_type=transport)
        for item in items:
            OrderItem.objects.create(order=order, **item)
        return order
