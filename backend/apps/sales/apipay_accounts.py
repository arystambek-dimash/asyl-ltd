"""Подключения Kaspi (ApiPay): какой кабинет принимает оплату за какие отделы."""
from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction

from .models import ApiPayAccount, Department


def account_with_key(api_key: str, *, exclude_pk: int | None = None) -> ApiPayAccount | None:
    """Подключение с тем же ключом: один кабинет — одно подключение.

    Два подключения с одним ключом делили бы секрет вебхука, и событие
    доставалось бы первому из них. Подключений единицы — сравнение в памяти.
    """
    accounts = ApiPayAccount.objects.exclude(api_key_encrypted="")
    if exclude_pk is not None:
        accounts = accounts.exclude(pk=exclude_pk)
    return next((row for row in accounts if row.api_key == api_key), None)


@transaction.atomic
def set_account_departments(
    account: ApiPayAccount, departments: Iterable[Department]
) -> None:
    """Подключение обслуживает ровно эти отделы.

    Отдел с другим подключением переходит сюда; снятый отдел остаётся без
    Kaspi, пока ему не выберут подключение.
    """
    ids = [department.pk for department in departments]
    Department.objects.filter(pk__in=ids).update(apipay_account=account)
    Department.objects.filter(apipay_account=account).exclude(pk__in=ids).update(
        apipay_account=None
    )
