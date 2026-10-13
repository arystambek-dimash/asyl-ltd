from django.conf import settings
from django.db import models

from apps.common.money import CURRENCY_CHOICES


class ClientActDocument(models.Model):
    """Документ 1С из акта сверки, уже перенесённый в CRM.

    Акты выгружают снова и снова за пересекающиеся периоды, а документ
    клиента в валюте переносится один раз: реестр — граница «уже есть».
    Он же делает импорт порциями продолжаемым. Реализация помнит свой заказ;
    поступление — свои оплаты и ``unapplied``: сколько денег ещё не разнесено
    (предоплата до отгрузок), их гасят следующие реализации акта.
    """

    KINDS = ("sale", "payment", "opening")

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="act_documents")
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    kind = models.CharField(max_length=10)
    # Документ как в акте, без хвоста «, Накладная/Чек …»: «Реализация ТМЗ и
    # услуг 00000000527 от 10.06.2026». Сальдо — «Сальдо на начало 01.01.2026».
    document = models.CharField(max_length=255)
    day = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # Способ оплаты CRM у поступления: bank / cash.
    method = models.CharField(max_length=10, blank=True, default="")
    unapplied = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    act_number = models.CharField(max_length=50, blank=True, default="")
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="act_documents",
    )
    payments = models.ManyToManyField("orders.Payment", blank=True, related_name="act_documents")
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client", "currency", "document"], name="client_act_document_once",
            ),
        ]

    def __str__(self):
        return f"{self.document} ({self.amount} {self.currency})"
