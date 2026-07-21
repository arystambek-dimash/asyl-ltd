from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rest_framework import serializers
from apps.common.money import money_string
from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Department, Store
from apps.orders.models import Order, OrderItem


class CatalogProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    weight_kg = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )
    available_bags = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "label", "weight_kg", "available_bags", "price", "currency"]

    def get_available_bags(self, obj):
        s = getattr(obj, "stock", None)
        return s.bags if s and s.bags > 0 else 0

    def get_price(self, obj):
        # Только закреплённая цена текущего клиента. Базовую цену не раскрываем.
        prices = getattr(obj, "portal_client_prices", [])
        return money_string(prices[0].price) if prices else None

    def get_currency(self, obj):
        return self.context.get("currency", "KZT")


class PortalOrderItemSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    product_label = serializers.CharField(read_only=True)
    # PositiveIntegerField пропускает 0 — заказ из «нулевых» позиций бессмыслен.
    quantity = serializers.IntegerField(min_value=1)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "quantity"]
        extra_kwargs = {
            "product": {"required": True, "allow_null": False},
        }


class PortalOrderSerializer(serializers.ModelSerializer):
    items = PortalOrderItemSerializer(many=True)
    settlement_intent = serializers.ChoiceField(
        choices=Order.SETTLEMENT_INTENTS, required=False)
    payment_method = serializers.ChoiceField(
        choices=Order.PAYMENT_METHODS, required=False)
    transport_type = serializers.ChoiceField(
        choices=Order.TRANSPORT_TYPES, required=False, default="truck")
    currency = serializers.ChoiceField(choices=Order.CURRENCIES, required=False)
    store = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.all(), required=False, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True, default=None)
    total_amount = serializers.SerializerMethodField()
    paid_total = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    has_pending_payment = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "status", "payment_status", "settlement_intent", "payment_method",
                  "currency",
                  "transport_type",
                  "store", "store_name",
                  "items", "total_amount", "paid_total", "remaining_amount",
                  "has_pending_payment",
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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # При создании заказа выбор оплаты намеренно не принимаем: цена и
        # фактический остаток известны только после подтверждения и отгрузки.
        if self.instance is None:
            attrs["payment_method"] = "pending"
            attrs["settlement_intent"] = "pending"
            return attrs
        method = attrs.get("payment_method")
        intent = attrs.get("settlement_intent")
        if method is not None:
            expected_intent = "debt" if method == "debt" else "instant"
            if intent is not None and intent != expected_intent:
                raise serializers.ValidationError({
                    "detail": "Способ оплаты не соответствует способу расчёта.",
                    "code": "payment_method_mismatch",
                })
            attrs["settlement_intent"] = expected_intent
        elif intent is not None:
            # Старые приложения присылают только settlement_intent.
            attrs["payment_method"] = "debt" if intent == "debt" else "invoice"
        else:
            # Способ оплаты клиент выбирает не при оформлении, а только после
            # завершения отгрузки. До этого заказ не должен считаться долгом.
            attrs["payment_method"] = "pending"
            attrs["settlement_intent"] = "pending"
        return attrs

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

    def get_total_amount(self, obj):
        if not self._money_visible(obj):
            return None
        return money_string(obj.total_amount)

    def get_paid_total(self, obj):
        if not self._money_visible(obj):
            return None
        return money_string(obj.paid_total)

    def get_remaining_amount(self, obj):
        if not self._money_visible(obj):
            return None
        return money_string(obj.remaining_amount)

    def get_has_pending_payment(self, obj):
        # Клиент отправил заявку на оплату, идёт цепочка подтверждения.
        from apps.orders.models import Payment
        # get_queryset() prefetches payments; filtering the related manager
        # would bypass that cache and issue one EXISTS query per order.
        return any(
            payment.status in Payment.IN_PROGRESS_STATUSES
            for payment in obj.payments.all()
        )

    @transaction.atomic
    def create(self, validated_data):
        # Атомарно: сбой на любой позиции не должен оставлять заказ-сироту.
        from apps.warehouse.services import ensure_products_available
        items = validated_data.pop("items")
        # Клиент портала тоже заказывает только товар в наличии.
        ensure_products_available(item["product"] for item in items)
        intent = validated_data.get("settlement_intent", "pending")
        method = validated_data.get("payment_method", "pending")
        transport = validated_data.get("transport_type", "truck")
        store = validated_data.get("store")
        client = self._client()
        currency = validated_data.pop("currency", client.currency)
        product_ids = [item["product"].id for item in items]
        client_prices = {
            row.product_id: row.price
            for row in ClientPrice.objects.filter(
                client=client, product_id__in=product_ids, currency=currency)
        }
        order = Order.objects.create(client=client, status="pending",
                                     currency=currency,
                                     department=Department.default_code(),
                                     settlement_intent=intent,
                                     payment_method=method, store=store,
                                     transport_type=transport)
        for item in items:
            product = item["product"]
            OrderItem.objects.create(
                order=order, unit_price=client_prices.get(product.id), **item)
        return order
