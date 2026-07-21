from decimal import Decimal
from django.conf import settings
from django.db import models


class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [(Decimal("25"), "25 кг"), (Decimal("50"), "50 кг")]

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=10, choices=COLORS)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS)
    # Цена не является свойством товара: она закрепляется отдельно для каждого
    # клиента в ClientPrice и фиксируется в OrderItem.unit_price при заказе.
    # Поле временно оставлено nullable для совместимости со старыми миграциями.
    price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # Если стоит — при въезде машины с этим товаром пост спрашивает вес машины;
    # иначе вес не спрашивается (используется расчётный по мешкам).
    ask_truck_weight = models.BooleanField(default=False)

    class Meta:
        unique_together = ("name", "color", "weight_kg")

    @property
    def cv_class(self):
        w = "50" if Decimal(self.weight_kg) == Decimal("50") else "25"
        return f"{self.color}_{w}"

    def __str__(self):
        color = dict(self.COLORS).get(self.color, self.color)
        return f"{self.name} · {color} {int(self.weight_kg)} кг"


class ClientPrice(models.Model):
    """Договорная цена товара для конкретного клиента (прайс-лист клиента)."""
    CURRENCIES = (("KZT", "KZT (тенге)"), ("USD", "USD (доллар)"))

    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="prices")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="client_prices")
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="KZT")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="set_client_prices")

    class Meta:
        unique_together = ("client", "product", "currency")
