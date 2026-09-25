from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.common.money import CURRENCY_CHOICES, DEFAULT_CURRENCY
from apps.common.text import match_key


def product_photo_path(instance, filename):
    # Имя файла задаёт сервис (uuid), путь по товару — чтобы файлы легко найти.
    return f"products/{instance.pk}/{filename}"


class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [
        (Decimal("2"), "2 кг"),
        (Decimal("5"), "5 кг"),
        (Decimal("10"), "10 кг"),
        (Decimal("25"), "25 кг"),
        (Decimal("50"), "50 кг"),
    ]

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=10, choices=COLORS)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS)
    is_active = models.BooleanField(default=True)
    # db_default: откат релиза и старые миграции вставляют товары без этой колонки.
    photo = models.FileField(upload_to=product_photo_path, blank=True, default="", db_default="")

    class Meta:
        unique_together = ("name", "color", "weight_kg")

    @property
    def cv_class(self):
        return f"{self.color}_{int(Decimal(self.weight_kg))}"

    @property
    def packaging_label(self):
        return f"{int(self.weight_kg)} кг"

    def __str__(self):
        color = dict(self.COLORS).get(self.color, self.color)
        return f"{self.name} · {color} {self.packaging_label}"


class ClientPrice(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="prices")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="client_prices")
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=DEFAULT_CURRENCY)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="set_client_prices")

    class Meta:
        unique_together = ("client", "product", "currency")


class ProductAlias(models.Model):
    """Код товара в отчётах о вагонах («Д1с») → товар каталога.

    Код в отчёте не фиксирован: словарь правят на странице «Товары» и
    пополняют при разборе отчёта. Хранится ключ сравнения
    (:func:`apps.common.text.match_key`): «Д1с» и «Д1c» — один код.
    """

    code = models.CharField(max_length=64, unique=True)
    # Код как его пишет отчёт («Д1с») — для людей и «Отправить отчёт»;
    # сравнение — только по ``code``. Последнее введённое написание.
    spelling = models.CharField(max_length=64, blank=True, default="", db_default="")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="aliases")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="product_aliases")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    @property
    def display_code(self) -> str:
        """Код для людей: как в отчёте, у записей без написания — ключ сравнения."""
        return self.spelling or self.code

    def save(self, *args, **kwargs):
        self.spelling = " ".join(str(self.spelling or self.code).split())
        self.code = match_key(self.code)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.display_code} → {self.product}"
