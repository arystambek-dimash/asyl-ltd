"""Сервисный пользователь WhatsApp-бота: от его имени бот проводит отчёты.

Права — ровно те, что нужны, чтобы создать и подтвердить вагонный заказ и
отгрузить вагоны. Цены клиента бот не меняет (``clients.set_price``) и
суперпользователем не бывает. Войти под ним нельзя: пароль непригоден, а
каждый запуск бота возвращает права и пароль к этому виду, что бы ни
поменяли в «Сотрудниках».
"""
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.employees.models import Employee
from apps.sys_permissions.models import Permission
from apps.sys_permissions.perms import PERMISSIONS

BOT_USERNAME = "whatsapp-bot"
BOT_FIRST_NAME = "WhatsApp-бот"
BOT_PERMISSION_CODES = ("orders.create", "orders.confirm", "loader.confirm", "loader.wagons")
_LABELS = {permission["code"]: permission for permission in PERMISSIONS}


def _permission(code: str) -> Permission:
    meta = _LABELS[code]
    permission, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": meta["section"], "action": meta["action"], "label": meta["label"]},
    )
    return permission


@transaction.atomic
def ensure_bot_user():
    """Создать или вернуть к норме сервисного пользователя бота."""
    User = get_user_model()
    user, _ = User.objects.select_for_update().get_or_create(
        username=BOT_USERNAME, defaults={"first_name": BOT_FIRST_NAME},
    )
    changed = []
    for field, value in (("is_superuser", False), ("is_staff", False), ("is_client", False),
                         ("is_active", True), ("must_change_password", False)):
        if getattr(user, field) != value:
            setattr(user, field, value)
            changed.append(field)
    if user.has_usable_password():
        user.set_unusable_password()
        changed.append("password")
    if changed:
        user.save(update_fields=changed)
    user.groups.clear()
    user.user_permissions.clear()
    employee, _ = Employee.objects.get_or_create(user=user, defaults={"position": "Сервисный пользователь"})
    if not employee.is_active or employee.sales_department_id is not None:
        # Без отдела: отчёты приходят по клиентам любого отдела.
        employee.is_active = True
        employee.sales_department = None
        employee.save(update_fields=["is_active", "sales_department"])
    employee.permissions.set([_permission(code) for code in BOT_PERMISSION_CODES])
    # Права кэшируются на экземпляре — отдаём свежий.
    return User.objects.select_related("employee").get(pk=user.pk)
