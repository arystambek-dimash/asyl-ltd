from rest_framework import serializers

from apps.bots.wagon_report import message_state, recipient_to
from apps.common.money import money_string
from apps.orders.debt import order_payment_status, order_remaining
from apps.orders.serializers import OrderWagonsMixin, TransportNumbersSerializer, TransportSuggestionsMixin
from apps.orders.services import can_set_truck_number

from .models import WaybillSettings
from .services import estimated_load_kg


class LoadSerializer(serializers.Serializer):
    bags = serializers.IntegerField(min_value=0)


class LoaderOrderItemSerializer(serializers.Serializer):
    label = serializers.CharField(source="product_label")
    quantity = serializers.IntegerField()
    weight_kg = serializers.DecimalField(source="product_weight_kg", max_digits=10, decimal_places=2)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)


class LoaderOrderSerializer(OrderWagonsMixin, TransportSuggestionsMixin, serializers.Serializer):
    """Заказ на экране грузчика: кому, на чём, что и сколько — без истории поста."""

    id = serializers.IntegerField()
    status = serializers.CharField()
    transport_type = serializers.CharField()
    truck_number = serializers.CharField()
    trailer_number = serializers.CharField()
    # Отгрузка по отчёту о вагонах: станция и вагоны.
    rail_station = serializers.CharField()
    wagons = serializers.SerializerMethodField()
    # «Отправить отчёт» в истории вагонов: когда, кому (в дательном падеже —
    # «Динаре») и что с сообщением: queued, sending, sent, failed, unknown или
    # link (как бот отстал от очереди — см. message_state).
    report_sent_at = serializers.SerializerMethodField()
    report_sent_to = serializers.SerializerMethodField()
    report_status = serializers.SerializerMethodField()
    report_error = serializers.SerializerMethodField()
    # Прошлые пары клиента — чипы «как в прошлый раз» (считаются на страницу).
    transport_suggestions = serializers.SerializerMethodField()
    # Пару номеров указал клиент: грузчик её не меняет и прицеп к ней не
    # дописывает. Статус замком здесь не считается — пустой номер после въезда
    # грузчик дописать может, а замену непустого отклонит сервис.
    transport_locked = serializers.SerializerMethodField()
    currency = serializers.CharField()
    # Плановый день (дата приезда, иначе день создания) — по нему очередь делится на дни.
    planned_on = serializers.DateField()
    client_name = serializers.SerializerMethodField()
    # Страна номера по умолчанию в поле «Тягач».
    client_country = serializers.CharField(source="client.country")
    items = LoaderOrderItemSerializer(many=True, source="items.all")
    bags = serializers.SerializerMethodField()
    total_kg = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()
    shipped_at = serializers.SerializerMethodField()
    can_rollback = serializers.SerializerMethodField()
    # Грузчик должен видеть, оплачен ли заказ: клиент мог заплатить заранее.
    payment_status = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()

    def get_client_name(self, order):
        return order.client.display_name

    @staticmethod
    def _report_message(order):
        shipment = getattr(order, "shipment", None)
        return shipment.report_message if shipment is not None and shipment.report_message_id else None

    def get_report_sent_at(self, order):
        shipment = getattr(order, "shipment", None)
        return shipment.report_sent_at if shipment is not None else None

    def get_report_sent_to(self, order):
        message = self._report_message(order)
        return recipient_to(message.recipient_name) if message is not None else ""

    def get_report_status(self, order):
        message = self._report_message(order)
        return message_state(message)[0] if message is not None else ""

    def get_report_error(self, order):
        message = self._report_message(order)
        return message_state(message)[1] if message is not None else ""

    def get_transport_locked(self, order):
        request = self.context.get("request")
        return request is not None and not can_set_truck_number(order, request.user)

    def get_bags(self, order):
        return order.ordered_bags

    def get_total_kg(self, order):
        return money_string(estimated_load_kg(order))

    def get_total_amount(self, order):
        return money_string(order.total_amount)

    def get_payment_status(self, order):
        # По факту денег, а не по сохранённому payment_status: у заказов,
        # отгруженных до 1e1b940, он может отставать (ORD-33).
        return order_payment_status(order)

    def get_remaining_amount(self, order):
        return money_string(order_remaining(order))

    def get_shipped_at(self, order):
        shipment = getattr(order, "shipment", None)
        return shipment.shipped_at if shipment else None

    def get_can_rollback(self, order):
        """Может ли этот грузчик отменить свою отгрузку прямо сейчас."""
        from .services import loader_rollback_blocker

        request = self.context.get("request")
        if request is None or order.status != "shipped":
            return False
        return not loader_rollback_blocker(order, request.user)


class LoaderDispatchSerializer(TransportNumbersSerializer):
    """«Отгружено»: номер тягача и прицепа можно дописать прямо на кнопке."""


class WaybillSignerSerializer(serializers.Serializer):
    role = serializers.CharField(max_length=40, allow_blank=True)
    name = serializers.CharField(max_length=60, allow_blank=True)


class WaybillSettingsSerializer(serializers.ModelSerializer):
    point_name = serializers.CharField(max_length=120)
    signers = WaybillSignerSerializer(many=True, max_length=6)

    class Meta:
        model = WaybillSettings
        fields = ["point_name", "signers", "updated_at"]
        read_only_fields = ["updated_at"]
