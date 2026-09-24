from django.conf import settings
from django.db import models


class Shipment(models.Model):
    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="shipment"
    )
    # Вагон едет без номера машины — поле необязательно.
    truck_number = models.CharField(max_length=30, blank=True, default="")
    # Ручной или расчётный входной вес в sales-flow.
    weigh_in_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    bags_loaded = models.PositiveIntegerField(default=0)
    arrived_at = models.DateTimeField(null=True, blank=True)
    loading_started_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    # «Отправить отчёт» в истории грузчика: когда и кто отправил отчёт о вагонах
    # этой отгрузки и само сообщение (кому, через бота или ссылкой, дошло ли).
    # Откат отгрузки удаляет Shipment — отметка уходит вместе с ней.
    report_sent_at = models.DateTimeField(null=True, blank=True, db_default=None)
    report_sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        db_default=None,
    )
    report_message = models.ForeignKey(
        "bots.OutgoingMessage", null=True, blank=True, on_delete=models.SET_NULL, related_name="shipments",
        db_default=None,
    )


class ShipmentWagon(models.Model):
    """Вагон отгрузки по отчёту: номер, товар, мешки и вес.

    Один вагонный заказ — вся партия (16 320 мешков = 12 вагонов по 68 т),
    поэтому номера вагонов живут здесь, а не в ``Order.truck_number``.
    Откат отгрузки удаляет Shipment — вагоны уходят вместе с ней.
    """

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="wagons")
    number = models.CharField(max_length=8)
    # Снимок товара, как у OrderItem: удаление товара не стирает историю вагона.
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="shipment_wagons",
    )
    product_label_snapshot = models.CharField(max_length=255, blank=True, default="")
    product_weight_kg_snapshot = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True)
    bags = models.PositiveIntegerField()
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2)
    position = models.PositiveSmallIntegerField()
    # Сообщение WhatsApp-бота, по которому проведён вагон (пусто — «Вставить отчёт» у грузчика).
    source_message = models.ForeignKey(
        "bots.BotMessage", null=True, blank=True, on_delete=models.SET_NULL, related_name="wagons",
    )

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["shipment", "number"], name="shipment_wagon_unique_number"),
            # Номер вагона — ровно 8 цифр (контрольную цифру проверяет разбор отчёта).
            models.CheckConstraint(condition=models.Q(number__regex=r"^[0-9]{8}$"), name="shipment_wagon_number_digits"),
        ]
        indexes = [
            # Дубли отчётов: тот же вагон у другой отгрузки за последние дни.
            models.Index(fields=["number"], name="shipment_wagon_number_idx"),
        ]

    @property
    def product_label(self):
        if self.product_label_snapshot:
            return self.product_label_snapshot
        return str(self.product) if self.product_id else "Удалённый товар"

    def save(self, *args, **kwargs):
        if self.product_id and not self.product_label_snapshot:
            self.product_label_snapshot = str(self.product)
            self.product_weight_kg_snapshot = self.product.weight_kg
        super().save(*args, **kwargs)


def default_waybill_signers():
    # Как на бумажном бланке мельницы; меняется в настройках накладной.
    return [
        {"role": "Бухгалтер", "name": "Егамбердиева Д"},
        {"role": "Склад", "name": "Тажи А"},
        {"role": "Кассир", "name": "Ибрагимова Г"},
    ]


class WaybillSettings(models.Model):
    """Шапка и подписи «Накладной на отпуск товаров» — одна строка на всё приложение."""

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    point_name = models.CharField(max_length=120, default="мельница Аксу")
    signers = models.JSONField(default=default_waybill_signers)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls) -> "WaybillSettings":
        settings, _ = cls.objects.get_or_create(singleton=True)
        return settings
