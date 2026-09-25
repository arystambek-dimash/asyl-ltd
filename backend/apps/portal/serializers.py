from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rest_framework import serializers
from apps.common.money import CURRENCY_CHOICES, DEFAULT_CURRENCY, money_string
from apps.catalog.models import ClientPrice, Product
from apps.catalog.photos import product_photo_url
from apps.clients.models import Store
from apps.orders.debt import available_to_pay, order_remaining
from apps.orders.labels import payment_method_label
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.statuses import is_financial
from apps.orders.serializers import (
    DepartmentLabelMixin,
    OrderWagonsMixin,
    apipay_invoice_data,
)
from apps.orders.services import client_release_invoice_error, order_items_error
from apps.orders.transport import transport_locked, transport_on_site

MAX_PORTAL_ORDER_ITEMS = 100
MAX_PORTAL_ITEM_QUANTITY = 1_000_000


class CatalogProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    weight_kg = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )
    price = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        # Warehouse balance is staff-only operational data.  The portal may
        # list orderable catalogue references and the client's own price, but
        # must never serialize an exact stock quantity.
        fields = ["id", "label", "weight_kg", "price", "currency", "photo_url"]

    def get_photo_url(self, obj):
        return product_photo_url(obj)

    def get_price(self, obj):
        # Только закреплённая цена текущего клиента.
        prices = getattr(obj, "portal_client_prices", [])
        return money_string(prices[0].price) if prices else None

    def get_currency(self, obj):
        return self.context.get("currency", DEFAULT_CURRENCY)


class PortalOrderItemSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    product_label = serializers.CharField(read_only=True)
    # PositiveIntegerField пропускает 0 — заказ из «нулевых» позиций бессмыслен.
    quantity = serializers.IntegerField(
        min_value=1,
        max_value=MAX_PORTAL_ITEM_QUANTITY,
    )

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "quantity"]
        extra_kwargs = {
            "product": {"required": True, "allow_null": False},
        }


class PortalOrderSerializer(OrderWagonsMixin, DepartmentLabelMixin, serializers.ModelSerializer):
    department_name = serializers.SerializerMethodField()
    # Вагоны отгрузки по отчёту и станция — клиенту только для чтения.
    wagons = serializers.SerializerMethodField()
    # Длину списка DRF проверяет до поштучной проверки позиций в БД.
    items = PortalOrderItemSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_PORTAL_ORDER_ITEMS,
        error_messages={
            "empty": "Добавьте хотя бы один товар.",
            "max_length": "В одном заказе можно указать не более {max_length} позиций.",
        },
    )
    transport_type = serializers.ChoiceField(
        choices=Order.TRANSPORT_TYPES, required=False, default="truck")
    currency = serializers.ChoiceField(choices=CURRENCY_CHOICES, required=False)
    store = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.all(), required=False, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True, default=None)
    total_amount = serializers.SerializerMethodField()
    paid_total = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    has_pending_payment = serializers.SerializerMethodField()
    available_amount = serializers.SerializerMethodField()
    payment_parts = serializers.SerializerMethodField()
    client_phone = serializers.CharField(source="client.phone", read_only=True)
    # Страна номера по умолчанию в полях «Тягач» и «Прицеп».
    client_country = serializers.CharField(source="client.country", read_only=True)
    receipt_available = serializers.SerializerMethodField()
    # Номер задал сотрудник или машина уже заехала: портал показывает его
    # только для чтения, без кнопки «Сохранить» (после заезда номер скрыт,
    # см. ``to_representation``).
    transport_locked = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "payment_status",
            "settlement_intent",
            "payment_method",
            "currency",
            "department",
            "department_name",
            "rejection_reason",
            "transport_type",
            "store",
            "store_name",
            "items",
            "total_amount",
            "paid_total",
            "remaining_amount",
            "has_pending_payment",
            "available_amount",
            "payment_parts",
            "client_phone",
            "client_country",
            "receipt_available",
            "truck_number",
            "trailer_number",
            "rail_station",
            "wagons",
            "transport_locked",
            "debt_requested",
            "created_at",
        ]
        read_only_fields = [
            "status",
            "payment_status",
            "settlement_intent",
            "payment_method",
            "department",
            "rejection_reason",
            "truck_number",
            "trailer_number",
            "rail_station",
            "debt_requested",
        ]

    def validate_items(self, items):
        # Та же проверка, что у сотрудников: каталог портала архив скрывает,
        # но id архивного товара может прийти из сохранённой корзины.
        if error := order_items_error(items):
            raise serializers.ValidationError(error)
        return items

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

    def get_transport_locked(self, obj):
        return transport_locked(obj, self.context["request"].user)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Машина уже на территории — номер клиенту не показываем совсем
        # (решение владельца): ни тягач, ни прицеп, ни номер вагона.
        if transport_on_site(instance):
            data["truck_number"] = ""
            data["trailer_number"] = ""
        return data

    def _money_visible(self, obj):
        # Деньги показываем только по финансовым заказам — правило общее с
        # оборотом и долгами, живёт в apps.orders.statuses.
        return is_financial(obj.status)

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
        return money_string(order_remaining(obj))

    def get_has_pending_payment(self, obj):
        # Клиент отправил заявку на оплату, идёт цепочка подтверждения.
        # get_queryset() prefetches payments; filtering the related manager
        # would bypass that cache and issue one EXISTS query per order.
        return any(
            payment.status in Payment.IN_PROGRESS_STATUSES
            for payment in obj.payments.all()
        )

    def get_available_amount(self, obj):
        if not self._money_visible(obj):
            return None
        return money_string(available_to_pay(obj))

    def get_payment_parts(self, obj):
        result = []
        request = self.context.get("request")
        user_id = getattr(getattr(request, "user", None), "pk", None)
        for payment in sorted(
            obj.payments.all(), key=lambda row: row.paid_at, reverse=True
        ):
            if payment.status not in (*Payment.IN_PROGRESS_STATUSES, "confirmed"):
                continue
            invoice = getattr(payment, "apipay_invoice", None)
            result.append({
                "id": payment.id,
                "amount": money_string(payment.amount),
                "method": payment.method,
                "method_label": payment_method_label(payment.method),
                "status": payment.status,
                # Те же правила, что у освобождения заявки на сервере.
                "can_release": (
                    payment.status in Payment.IN_PROGRESS_STATUSES
                    and payment.recorded_by_id == user_id
                    and client_release_invoice_error(invoice) is None
                ),
                "apipay_invoice": apipay_invoice_data(invoice),
            })
        return result

    def get_receipt_available(self, obj):
        return any(
            payment.status == "confirmed" for payment in obj.payments.all()
        )

    @transaction.atomic
    def create(self, validated_data):
        # Атомарно: сбой на любой позиции не должен оставлять заказ-сироту.
        from apps.warehouse.services import ensure_products_available, resolve_warehouse
        items = validated_data.pop("items")
        warehouse = resolve_warehouse()
        # Клиент портала тоже заказывает только товар в наличии.
        ensure_products_available(
            (item["product"] for item in items),
            warehouse=warehouse,
        )
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
                                     department=client.department.code if client.department_id else "",
                                     # Выбор оплаты при оформлении не принимаем: цена
                                     # и фактический остаток известны только после
                                     # подтверждения и отгрузки (дефолт модели — debt).
                                     settlement_intent="pending",
                                     payment_method="pending", store=store,
                                     warehouse=warehouse,
                                     transport_type=transport)
        # bulk_create не вызывает OrderItem.save(): снимок товара заполняем
        # тем же методом, а ограниченный список вставляем одним запросом.
        order_items = []
        for item in items:
            product = item["product"]
            order_item = OrderItem(
                order=order,
                product=product,
                quantity=item["quantity"],
                unit_price=client_prices.get(product.id),
            )
            order_item.fill_snapshot()
            order_items.append(order_item)
        OrderItem.objects.bulk_create(order_items)
        return order
