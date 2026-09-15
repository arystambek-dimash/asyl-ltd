# Ключи ApiPay по отделам — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ключ ApiPay и секрет вебхука хранятся зашифрованными у отдела продаж, выдаются суперюзером в модалке «Отделы продаж», и все запросы к ApiPay и вебхуки маршрутизируются по отделу; строка «Удаленная оплата» уходит с главной мобильной кассы.

**Architecture:** `apps/common/crypto.py` (Fernet от `SECRET_KEY`) → поля и методы `sales.Department` → `ApiPayCredentials` в `apps/orders/apipay.py`, который получают все вызовы `api_request` (по заказу или по счёту) → сверка группирует по отделу → вебхук сопоставляет подпись с отделом. Глобальные `APIPAY_API_KEY`/`APIPAY_WEBHOOK_SECRET` удаляются; миграция переносит их из env в основной отдел. Фронт: `DepartmentManager` выносится в компонент с суперюзерским блоком «Kaspi / ApiPay».

**Tech Stack:** Django 5.2 / DRF, `cryptography` (Fernet), pytest-django (PostgreSQL), Next.js + vitest + testing-library.

**Spec:** `docs/superpowers/specs/2026-09-15-department-apipay-keys-design.md`

## Global Constraints

- Ключ и секрет никогда не попадают в ответы API, логи (`config/observability.py` уже редактирует `apipay_api_key`, `apipay_webhook_secret`) и в тесты как plaintext в базе.
- Права: поля ключа меняет только `request.user.is_superuser`; остальное как было (`HasPerm("sys_permissions.manage")`).
- Коды ошибок из спеки: `apipay_superuser_only` (403), `apipay_secret_without_key` (400), `payment_provider_not_configured` (400 касса), `webhook_not_configured` (503), `invalid_signature` (401), `invoice_department_mismatch` (403).
- Бэкенд-тесты: `cd backend && .venv/bin/python -m pytest <path> -q`. Фронт: `cd frontend && npx vitest run <path>`.
- Комментарии в коде на русском, как в проекте; тексты для пользователя на русском.
- Коммиты с `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### Task 1: Шифрование секретов

**Files:**
- Create: `backend/apps/common/crypto.py`
- Create: `backend/apps/common/tests/test_crypto.py`
- Modify: `backend/requirements-prod.txt`

**Interfaces:**
- Produces: `encrypt_secret(value: str) -> str`, `decrypt_secret(token: str) -> str`, `class SecretDecryptError(ValueError)`.

- [ ] **Step 1: Тест**

```python
# backend/apps/common/tests/test_crypto.py
import pytest

from apps.common.crypto import SecretDecryptError, decrypt_secret, encrypt_secret


def test_roundtrip_and_distinct_tokens(settings):
    settings.SECRET_KEY = "a" * 60
    first = encrypt_secret("apipay-live-key")
    second = encrypt_secret("apipay-live-key")
    assert first != second
    assert "apipay-live-key" not in first
    assert decrypt_secret(first) == "apipay-live-key"


def test_foreign_key_fails_and_fallback_keys_still_decrypt(settings):
    settings.SECRET_KEY = "old-key" * 10
    settings.SECRET_KEY_FALLBACKS = []
    token = encrypt_secret("secret")
    settings.SECRET_KEY = "new-key" * 10
    with pytest.raises(SecretDecryptError):
        decrypt_secret(token)
    settings.SECRET_KEY_FALLBACKS = ["old-key" * 10]
    assert decrypt_secret(token) == "secret"


def test_empty_values():
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""
```

- [ ] **Step 2: Запустить, убедиться что падает** (`ModuleNotFoundError`).
- [ ] **Step 3: Реализация**

```python
# backend/apps/common/crypto.py
"""Симметричное шифрование секретов, хранимых в базе (ключи ApiPay).

Ключ Fernet выводится из ``SECRET_KEY``: отдельного секрета в окружении не
нужно, а production-настройки уже требуют сильный ``SECRET_KEY``.
``SECRET_KEY_FALLBACKS`` позволяет ротацию без потери старых токенов.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

_DERIVATION_PREFIX = "asyl-secret-store:"


class SecretDecryptError(ValueError):
    """Токен не расшифровывается текущим SECRET_KEY и его fallback-ключами."""


def _fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256((_DERIVATION_PREFIX + secret_key).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _cipher() -> MultiFernet:
    keys = [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", [])]
    return MultiFernet([Fernet(_fernet_key(key)) for key in keys if key])


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SecretDecryptError("secret token is not readable") from exc
```

`requirements-prod.txt`: добавить строку `cryptography>=43,<47` после `sentry-sdk`.

- [ ] **Step 4: Тест проходит.**
- [ ] **Step 5: Commit** `feat(common): шифрование секретов в базе (Fernet от SECRET_KEY)`.

---

### Task 2: Поля ключей у отдела

**Files:**
- Modify: `backend/apps/sales/models.py`
- Create: `backend/apps/sales/migrations/0003_department_apipay_credentials.py` (makemigrations)
- Test: `backend/apps/sales/tests/test_department_apipay.py` (часть модели)

**Interfaces:**
- Produces: `Department.apipay_configured: bool`, `Department.apipay_api_key: str`, `Department.apipay_webhook_secret: str`, `Department.set_apipay_api_key(value: str) -> None`, `Department.set_apipay_webhook_secret(value: str) -> None`.

- [ ] **Step 1: Тест**

```python
# backend/apps/sales/tests/test_department_apipay.py
import pytest
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def test_department_stores_apipay_credentials_encrypted():
    department = Department.objects.create(code="mill", name="Мельница")
    assert not department.apipay_configured
    department.set_apipay_api_key("live-key-1234")
    department.set_apipay_webhook_secret("hook-secret")
    department.save()
    stored = Department.objects.get(pk=department.pk)
    assert stored.apipay_configured
    assert stored.apipay_api_key == "live-key-1234"
    assert stored.apipay_webhook_secret == "hook-secret"
    assert "live-key-1234" not in stored.apipay_api_key_encrypted
    assert stored.apipay_updated_at is not None
    stored.set_apipay_api_key("")
    stored.save()
    assert not stored.apipay_configured
    assert stored.apipay_webhook_secret == ""
```

- [ ] **Step 2: Падает** (нет полей).
- [ ] **Step 3: Модель**

```python
from django.utils import timezone
from apps.common.crypto import SecretDecryptError, decrypt_secret, encrypt_secret

class Department(models.Model):
    ...
    # Ключ ApiPay и секрет вебхука хранятся только зашифрованными: у каждого
    # отдела свой аккаунт Kaspi, общего ключа в окружении больше нет.
    apipay_api_key_encrypted = models.TextField(blank=True, default="")
    apipay_webhook_secret_encrypted = models.TextField(blank=True, default="")
    apipay_updated_at = models.DateTimeField(null=True, blank=True)

    @property
    def apipay_configured(self) -> bool:
        return bool(self.apipay_api_key_encrypted)

    @staticmethod
    def _reveal(token: str) -> str:
        try:
            return decrypt_secret(token)
        except SecretDecryptError:
            # Сменили SECRET_KEY без fallback: ключ считается не настроенным,
            # суперюзер вводит его заново.
            return ""

    @property
    def apipay_api_key(self) -> str:
        return self._reveal(self.apipay_api_key_encrypted)

    @property
    def apipay_webhook_secret(self) -> str:
        return self._reveal(self.apipay_webhook_secret_encrypted)

    def set_apipay_api_key(self, value: str) -> None:
        self.apipay_api_key_encrypted = encrypt_secret(value.strip())
        if not self.apipay_api_key_encrypted:
            self.apipay_webhook_secret_encrypted = ""
        self.apipay_updated_at = timezone.now()

    def set_apipay_webhook_secret(self, value: str) -> None:
        self.apipay_webhook_secret_encrypted = encrypt_secret(value.strip())
        self.apipay_updated_at = timezone.now()
```

Миграция: `cd backend && .venv/bin/python manage.py makemigrations sales -n department_apipay_credentials`.

- [ ] **Step 4: Тест проходит; `pytest apps/sales -q` зелёный.**
- [ ] **Step 5: Commit** `feat(sales): зашифрованные ключи ApiPay у отдела`.

---

### Task 3: API отдела — ключ задаёт только суперюзер

**Files:**
- Modify: `backend/apps/sales/serializers.py`
- Test: `backend/apps/sales/tests/test_department_apipay.py`

**Interfaces:**
- Consumes: методы модели из Task 2.
- Produces: поля ответа `apipay_configured`, `apipay_webhook_configured`, `apipay_key_hint` (только суперюзеру), `apipay_updated_at`; write-only `apipay_api_key`, `apipay_webhook_secret`.

- [ ] **Step 1: Тесты**

```python
from rest_framework.test import APIClient


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def superuser(make_user):
    user = make_user("root")
    user.is_superuser = True
    user.is_staff = True
    user.save()
    return user


def test_superuser_sets_key_and_secret_without_echo(superuser):
    department = Department.objects.create(code="mill", name="Мельница")
    response = _api(superuser).patch(
        f"/api/departments/{department.id}/",
        {"apipay_api_key": " live-key-1234 ", "apipay_webhook_secret": "hook"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["apipay_configured"] is True
    assert response.data["apipay_webhook_configured"] is True
    assert response.data["apipay_key_hint"] == "••••1234"
    assert "apipay_api_key" not in response.data
    assert "apipay_webhook_secret" not in response.data
    department.refresh_from_db()
    assert department.apipay_api_key == "live-key-1234"


def test_manager_with_manage_permission_cannot_touch_keys(user_with_perms):
    manager = user_with_perms("dept-admin", codes=["sys_permissions.manage"])
    department = Department.objects.create(code="mill", name="Мельница")
    response = _api(manager).patch(
        f"/api/departments/{department.id}/",
        {"name": "Мельница 2", "apipay_api_key": "live-key-1234"},
        format="json",
    )
    assert response.status_code == 403
    assert response.data["code"] == "apipay_superuser_only"
    department.refresh_from_db()
    assert department.name == "Мельница" and not department.apipay_configured


def test_clearing_key_drops_secret_and_secret_needs_key(superuser):
    department = Department.objects.create(code="mill", name="Мельница")
    api = _api(superuser)
    bad = api.patch(f"/api/departments/{department.id}/", {"apipay_webhook_secret": "hook"}, format="json")
    assert bad.status_code == 400
    assert bad.data["code"] == "apipay_secret_without_key"
    api.patch(f"/api/departments/{department.id}/", {"apipay_api_key": "live-key-1234", "apipay_webhook_secret": "hook"}, format="json")
    cleared = api.patch(f"/api/departments/{department.id}/", {"apipay_api_key": ""}, format="json")
    assert cleared.status_code == 200
    assert cleared.data["apipay_configured"] is False
    assert cleared.data["apipay_webhook_configured"] is False
    assert cleared.data["apipay_key_hint"] == ""


def test_staff_list_shows_status_but_no_hint(manager, superuser):
    department = Department.objects.create(code="mill", name="Мельница")
    department.set_apipay_api_key("live-key-1234")
    department.save()
    rows = _api(manager).get("/api/departments/").data
    assert rows[0]["apipay_configured"] is True
    assert "apipay_key_hint" not in rows[0]
    rows = _api(superuser).get("/api/departments/").data
    assert rows[0]["apipay_key_hint"] == "••••1234"
```

Проверить, что ошибка 403 отдаётся как `{"detail", "code"}` — `config/exceptions.py` нормализует `PermissionDenied({"detail":..., "code":...})`; если нет, кидать `PermissionDenied` с dict и проверить формат.

- [ ] **Step 2: Падает.**
- [ ] **Step 3: Сериализатор**

```python
class DepartmentSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    order_count = serializers.SerializerMethodField()
    apipay_configured = serializers.BooleanField(read_only=True)
    apipay_webhook_configured = serializers.SerializerMethodField()
    apipay_key_hint = serializers.SerializerMethodField()
    apipay_api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True, max_length=255,
    )
    apipay_webhook_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True, max_length=255,
    )
    # fields += [...]; read_only_fields += ["apipay_updated_at"]

    APIPAY_FIELDS = ("apipay_api_key", "apipay_webhook_secret")

    def get_apipay_webhook_configured(self, obj): return bool(obj.apipay_webhook_secret_encrypted)

    def get_apipay_key_hint(self, obj):
        key = obj.apipay_api_key
        return f"••••{key[-4:]}" if key else ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not (request and getattr(request.user, "is_superuser", False)):
            data.pop("apipay_key_hint", None)
        return data

    def validate(self, attrs):
        touches_keys = any(field in attrs for field in self.APIPAY_FIELDS)
        request = self.context.get("request")
        if touches_keys and not (request and request.user.is_superuser):
            raise PermissionDenied({"detail": "Ключ ApiPay может менять только администратор", "code": "apipay_superuser_only"})
        key = (attrs.get("apipay_api_key") or "").strip() if "apipay_api_key" in attrs else None
        if key is not None and key and len(key) < 8:
            raise serializers.ValidationError({"apipay_api_key": "Слишком короткий ключ"})
        secret = (attrs.get("apipay_webhook_secret") or "").strip() if "apipay_webhook_secret" in attrs else None
        has_key = bool(key) if key is not None else bool(self.instance and self.instance.apipay_configured)
        if secret and not has_key:
            raise serializers.ValidationError({"detail": "Сначала укажите API-ключ отдела", "code": "apipay_secret_without_key"})
        return attrs

    def _apply_apipay(self, instance, validated_data):
        key = validated_data.pop("apipay_api_key", None)
        secret = validated_data.pop("apipay_webhook_secret", None)
        if key is not None: instance.set_apipay_api_key(key or "")
        if secret is not None and (key or instance.apipay_configured): instance.set_apipay_webhook_secret(secret or "")
```

В `update` вызвать `_apply_apipay(instance, validated_data)` до `super().update`. В `create` — поля игнорируются для нового отдела (ключ выдаётся уже созданному отделу), но `validate` всё равно проверяет права.

- [ ] **Step 4: Тесты проходят; `pytest apps/sales -q`.**
- [ ] **Step 5: Commit** `feat(sales): API ключей ApiPay отдела только для суперюзера`.

---

### Task 4: Ключ по отделу во всех запросах к ApiPay

**Files:**
- Modify: `backend/apps/orders/apipay.py` (`_credentials`, `api_request`, `create_invoice`, `get_invoice`, `_hydrate_money_response`, `recover_invoice_issue_mapping`, `check_invoice_statuses`, `get_invoice_refunds`, `_cancel_invoice_locked`, `create_refund`)
- Modify: `backend/apps/orders/views.py:81-89` (`_provider_error`)
- Modify: `backend/config/_settings/base.py:311-312` (удалить две настройки)
- Modify: `backend/apps/conftest.py` (фикстура `apipay_department`)
- Modify tests: `backend/apps/orders/tests/test_apipay.py`, `test_staff_pos_qr.py`, `test_payment_regressions.py`, `test_apipay_refund_reconciliation.py`, `backend/apps/portal/tests/test_portal_actions.py`

**Interfaces:**
- Produces: `ApiPayCredentials`, `credentials_for_department`, `credentials_for_department_code`, `credentials_for_order`, `credentials_for_invoice`, `api_request(method, path, payload=None, *, credentials)`, `check_invoice_statuses(invoice_ids, *, credentials)`, `get_invoice(record)`; `ApiPayConfigurationError(message, department_name="")`.

- [ ] **Step 1: Фикстура и тесты**

```python
# backend/apps/conftest.py
@pytest.fixture
def apipay_department(db):
    """Основной отдел ``main`` (код по умолчанию у Order) с ключом ApiPay."""
    from apps.sales.models import Department
    department, _ = Department.objects.get_or_create(
        code="main", defaults={"name": "Мельница", "is_default": True})
    department.set_apipay_api_key("server-only-key")
    department.set_apipay_webhook_secret("webhook-secret")
    department.save()
    return department
```

Новые тесты в `test_apipay.py`:

```python
@patch("apps.orders.apipay.urllib.request.urlopen")
def test_each_department_uses_its_own_key(urlopen, apipay_department):
    other = Department.objects.create(code="city", name="Нью-Сити")
    other.set_apipay_api_key("city-key")
    other.save()
    urlopen.return_value = UpstreamResponse({"id": 42, "status": "processing"})
    payment = _payment()
    create_invoice(payment, user=None)
    assert urlopen.call_args.args[0].headers["X-api-key"] == "server-only-key"
    urlopen.return_value = UpstreamResponse({"id": 43, "status": "processing"})
    city_payment = _payment()
    Order.all_objects.filter(pk=city_payment.order_id).update(department="city")
    create_invoice(city_payment, user=None)
    assert urlopen.call_args.args[0].headers["X-api-key"] == "city-key"


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_department_without_key_rejects_before_reserving(urlopen):
    Department.objects.create(code="main", name="Мельница", is_default=True)
    payment = _payment()
    with pytest.raises(ApiPayConfigurationError) as exc:
        create_invoice(payment, user=None)
    assert "Мельница" in str(exc.value)
    assert not ApiPayInvoice.objects.exists()
    urlopen.assert_not_called()
```

Существующие тесты: убрать `settings.APIPAY_API_KEY = ...` и `settings.APIPAY_WEBHOOK_SECRET`, добавить параметр `apipay_department` в сигнатуры (или `autouse`-фикстуру на модуль: `@pytest.fixture(autouse=True) def _department(apipay_department): return apipay_department`). В `_signed_post` секрет брать из `apipay_department` (параметр `secret="webhook-secret"` по умолчанию совпадает с фикстурой; если тест передаёт другой секрет — `apipay_department.set_apipay_webhook_secret(secret); save()`).

Прямые вызовы `api_request(...)` в тестах (refund reconciliation патчит `apps.orders.apipay.api_request` — сигнатура с kwargs совместима) и `get_invoice(int)` → `get_invoice(record)`.

- [ ] **Step 2: Прогнать `pytest apps/orders/tests/test_apipay.py -q` — падает.**
- [ ] **Step 3: Реализация в `apipay.py`**

```python
from apps.sales.models import Department


class ApiPayConfigurationError(RuntimeError):
    def __init__(self, message: str, *, department_name: str = ""):
        super().__init__(message)
        self.department_name = department_name


@dataclass(frozen=True)
class ApiPayCredentials:
    api_key: str
    base_url: str
    department_id: int
    department_name: str


def credentials_for_department(department: Department) -> ApiPayCredentials:
    api_key = department.apipay_api_key
    if not api_key:
        raise ApiPayConfigurationError(
            f"В отделе «{department.name}» не подключён Kaspi (ApiPay)",
            department_name=department.name,
        )
    return ApiPayCredentials(api_key, settings.APIPAY_BASE_URL, department.pk, department.name)


def credentials_for_department_code(code: str) -> ApiPayCredentials:
    department = Department.objects.filter(code=code).first() if code else None
    if department is None:
        raise ApiPayConfigurationError("У заказа не указан отдел продаж")
    return credentials_for_department(department)


def credentials_for_order(order: Order) -> ApiPayCredentials:
    return credentials_for_department_code(order.department)


def credentials_for_invoice(record: ApiPayInvoice) -> ApiPayCredentials:
    code = Order.all_objects.filter(payments__pk=record.payment_id).values_list("department", flat=True).first()
    return credentials_for_department_code(code or "")


def api_request(method, path, payload=None, *, credentials: ApiPayCredentials):
    headers = {"Accept": "application/json", "X-API-Key": credentials.api_key}
    ... f"{credentials.base_url}/{path.lstrip('/')}" ...
```

Точки вызова:
- `create_invoice`: сразу после проверки `order.currency` и до `ApiPayInvoice.objects.create` — `credentials = credentials_for_order(order)`; передать в `api_request("POST", path, request_payload, credentials=credentials)`. При `recover_existing_issue` — `recover_invoice_issue_mapping(record)` сам возьмёт ключ.
- `get_invoice(record)`: `api_request("GET", f"/invoices/{record.invoice_id}", credentials=credentials_for_invoice(record))`; `_hydrate_money_response` вызывает `get_invoice(record)`.
- `recover_invoice_issue_mapping`: один раз `credentials = credentials_for_invoice(record)` до цикла.
- `check_invoice_statuses(invoice_ids, *, credentials)`.
- `get_invoice_refunds(record)`, `_cancel_invoice_locked(record)`: `credentials_for_invoice(record)`.
- `create_refund`: `credentials_for_order(order)` внутри транзакции резервирования (до сети), при `ApiPayConfigurationError` — как сейчас `definitive_failure`.
- `_provider_error` в `views.py`: `detail = str(exc)` для `ApiPayConfigurationError` (сообщение уже с именем отдела), код прежний.
- `base.py`: удалить `APIPAY_API_KEY`, `APIPAY_WEBHOOK_SECRET`. `grep -rn "APIPAY_API_KEY\|APIPAY_WEBHOOK_SECRET" backend/apps backend/config` должен вернуть пусто после Task 6.

- [ ] **Step 4: `pytest apps/orders apps/portal -q` зелёный** (кроме вебхуков — они в Task 6; временно в `webhooks.py` использовать `Department` секреты уже здесь, если проще — тогда Task 6 только про маппинг).
- [ ] **Step 5: Commit** `feat(apipay): ключ провайдера берётся из отдела заказа`.

---

### Task 5: Сверка по отделам

**Files:**
- Modify: `backend/apps/orders/reconciliation.py:210-260`
- Test: `backend/apps/orders/tests/test_apipay_reconciliation.py`

- [ ] **Step 1: Тест**

```python
@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconciliation_batches_per_department(check, apipay_department):
    city = Department.objects.create(code="city", name="Нью-Сити")
    city.set_apipay_api_key("city-key"); city.save()
    a = _invoice(11); b = _invoice(12)
    Order.all_objects.filter(pk=b.payment.order_id).update(department="city")
    check.return_value = {"invoices": []}
    reconcile_apipay_invoices(stale_after=timedelta(0), now=timezone.now() + timedelta(minutes=1))
    keys = sorted((call.kwargs["credentials"].api_key, call.args[0]) for call in check.call_args_list)
    assert keys == [("city-key", [12]), ("server-only-key", [11])]


@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconciliation_skips_department_without_key(check, apipay_department):
    Department.objects.create(code="nokey", name="Без ключа")
    record = _invoice(13)
    Order.all_objects.filter(pk=record.payment.order_id).update(department="nokey")
    stats = reconcile_apipay_invoices(stale_after=timedelta(0), now=timezone.now() + timedelta(minutes=1))
    check.assert_not_called()
    assert stats.failed == 1
```

(Сверить с фактическими хелперами модуля `_invoice` и как там задаётся `now`; подстроить.)

- [ ] **Step 2: Падает.**
- [ ] **Step 3: Реализация** — после `candidates = list(candidate_query)`:

```python
    # У каждого отдела свой ключ ApiPay: батчи собираются внутри отдела.
    grouped: dict[str, list[ApiPayInvoice]] = {}
    for record in candidates:
        grouped.setdefault(record.department_code, []).append(record)
    for department_code, records in grouped.items():
        try:
            credentials = credentials_for_department_code(department_code)
        except ApiPayConfigurationError as exc:
            stats.failed += len(records)
            log.warning("ApiPay reconciliation skipped department=%s: %s", department_code, exc)
            continue
        for offset in range(0, len(records), batch_size):
            batch = records[offset : offset + batch_size]
            ...
            response = check_invoice_statuses(requested_ids, credentials=credentials)
```

`candidate_query` дополняется `.annotate(department_code=F("payment__order__department"))` (`.only()` остаётся). Импорт `credentials_for_department_code, ApiPayConfigurationError` из `.apipay`.

- [ ] **Step 4: `pytest apps/orders/tests/test_apipay_reconciliation.py apps/orders/tests/test_apipay_refund_reconciliation.py apps/orders/tests/test_apipay_tasks.py -q`.**
- [ ] **Step 5: Commit** `feat(apipay): сверка счетов батчами по отделам`.

---

### Task 6: Вебхук сопоставляется с отделом

**Files:**
- Modify: `backend/apps/orders/models.py` (`ApiPayWebhookEvent.department`)
- Create: `backend/apps/orders/migrations/0040_apipaywebhookevent_department.py` (makemigrations)
- Modify: `backend/apps/orders/webhooks.py` (`apipay_webhook`, `_replay_one_webhook`)
- Test: `backend/apps/orders/tests/test_apipay.py`

- [ ] **Step 1: Тесты**

```python
def test_webhook_maps_signature_to_department(api_client, apipay_department):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(payment=payment, invoice_id=42, idempotency_key="k42", status="pending")
    response = _signed_post(api_client, {"event": "invoice.status_changed", "timestamp": "2026-09-15T10:00:00+00:00", "invoice": {"id": 42, "status": "paid", "amount": 5000}})
    assert response.status_code == 200
    event = ApiPayWebhookEvent.objects.get()
    assert event.department_id == apipay_department.pk


def test_webhook_signed_by_other_department_is_rejected(api_client, apipay_department):
    city = Department.objects.create(code="city", name="Нью-Сити")
    city.set_apipay_api_key("city-key"); city.set_apipay_webhook_secret("city-hook"); city.save()
    payment = _payment()  # заказ в main
    ApiPayInvoice.objects.create(payment=payment, invoice_id=42, idempotency_key="k42", status="pending")
    response = _signed_post(api_client, {...invoice 42 paid...}, secret="city-hook")
    assert response.status_code == 403
    assert response.json()["error"] == "invoice_department_mismatch"
    assert not ApiPayWebhookEvent.objects.exists()


def test_webhook_without_any_secret_is_503(api_client):
    Department.objects.create(code="main", name="Мельница")
    response = api_client.post("/api/webhooks/apipay/", data=b"{}", content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE="sha256=x")
    assert response.status_code == 503
```

`_signed_post` больше не принимает `settings`: подписывает `secret` и не трогает базу; тесты, которым нужен другой секрет, сами задают его отделу.

- [ ] **Step 2: Падает.**
- [ ] **Step 3: Реализация**

```python
def _department_for_signature(raw_body: bytes, signature: str) -> Department | None:
    """Отдел, чьим секретом подписано событие; None — ни один не подошёл."""
    for department in Department.objects.exclude(apipay_webhook_secret_encrypted="").order_by("created_at", "id"):
        if verify_signature(raw_body, signature, department.apipay_webhook_secret):
            return department
    return None
```

В `apipay_webhook`: вместо `settings.APIPAY_WEBHOOK_SECRET` — если `not Department.objects.exclude(apipay_webhook_secret_encrypted="").exists()` → 503; `department = _department_for_signature(...)`; `None` → 401. После поиска `invoice_record`: если найден и `Order.all_objects.filter(payments__pk=invoice_record.payment_id).values_list("department", flat=True).first() != department.code` → `logger.warning(...)`, `JsonResponse({"error": "invoice_department_mismatch"}, status=403)`. В `ApiPayWebhookEvent.objects.create(..., department=department)`.

В `_replay_one_webhook` после определения `invoice_record`: если `event.department_id is not None` и код отдела заказа счёта не равен `event.department.code` → `_defer_locked_event(event, "invoice_department_mismatch")`, `return "failed"`.

Модель: `department = models.ForeignKey("sales.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="apipay_webhook_events")`.

- [ ] **Step 4: `pytest apps/orders apps/portal apps/sales apps/common -q`; `grep APIPAY_WEBHOOK_SECRET` пусто.**
- [ ] **Step 5: Commit** `feat(apipay): вебхук сопоставляется с отделом по секрету подписи`.

---

### Task 7: Перенос ключа из env, compose, README, deploy-тест

**Files:**
- Create: `backend/apps/sales/migrations/0004_seed_apipay_from_env.py`
- Modify: `docker-compose.yml:112-113`, `docker-compose.prod.yml:111-112`
- Modify: `deploy/tests/test_production_hardening.py:783-791`
- Modify: `README.md` (раздел «ApiPay / Kaspi Pay»)
- Test: `backend/apps/sales/tests/test_department_apipay.py` (миграция через функцию)

- [ ] **Step 1: Тест функции переноса**

```python
def test_seed_from_env_fills_default_department_once(monkeypatch):
    from apps.sales.migrations import seed_apipay_from_env as seed  # helper вынесен в модуль миграции 0004
    main = Department.objects.create(code="main", name="Мельница", is_default=True)
    monkeypatch.setenv("APIPAY_API_KEY", "env-key-1234")
    monkeypatch.setenv("APIPAY_WEBHOOK_SECRET", "env-hook")
    seed.seed_from_environment(Department)
    main.refresh_from_db()
    assert main.apipay_api_key == "env-key-1234" and main.apipay_webhook_secret == "env-hook"
    monkeypatch.setenv("APIPAY_API_KEY", "other")
    seed.seed_from_environment(Department)
    main.refresh_from_db()
    assert main.apipay_api_key == "env-key-1234"
```

Реализация в миграции: `RunPython(lambda apps, schema: seed_from_environment(apps.get_model("sales", "Department")), migrations.RunPython.noop)`, где `seed_from_environment` читает `os.environ`, ничего не делает если ключа нет в env или у какого-то отдела уже есть `apipay_api_key_encrypted`, иначе `encrypt_secret` и `update()` основного (`is_default=True`) или первого активного отдела. Функция определяется в самом файле миграции и импортируется в тесте через `importlib`.

- [ ] **Step 2: compose** — обе переменные как `${APIPAY_API_KEY:-}` / `${APIPAY_WEBHOOK_SECRET:-}` с комментарием `# Только для разовой миграции sales.0004: после первого деплоя удалить из .env`. Deploy-тест: `assertNotIn("APIPAY_API_KEY:?", compose)`, `assertIn("APIPAY_API_KEY: ${APIPAY_API_KEY:-}", compose)`; название теста → `test_compose_keeps_apipay_env_optional_and_serial_celery_topology`. Запуск: `cd deploy && python -m pytest tests/test_production_hardening.py -q` (проверить, как запускаются эти тесты — `deploy/tests` может требовать `python -m unittest`).
- [ ] **Step 3: README** — пункт про секреты заменить на: ключ и секрет вебхука задаются суперюзером в «Заказы → Отделы → отдел → Kaspi / ApiPay», хранятся зашифрованными, у каждого ключа в кабинете ApiPay указывается один и тот же адрес `https://asyl-ltd.kz/api/webhooks/apipay/`; событие применяется только к счетам отдела, чьим секретом подписано.
- [ ] **Step 4: Прогнать тесты; Commit** `feat(apipay): разовый перенос ключа из env в основной отдел, compose и README`.

---

### Task 8: Главная мобильной кассы без «Удаленная оплата»

**Files:**
- Modify: `frontend/src/components/cashier/mobile/home-screen.tsx`
- Modify: `frontend/src/components/cashier/mobile/mobile-cashier.tsx:16,80-86` (тип `HomeItemKey` → `MobileMenuKey`)
- Test: `frontend/src/components/cashier/mobile/mobile-cashier.test.tsx:248,318-326`

- [ ] **Step 1: Обновить тесты**: в списке кнопок главной убрать `expect.stringContaining("Удаленная оплата")`; тест `shows no POS bar or remote-payment row...` → `shows no POS bar without the right to take payments`, убрать проверку строки. Добавить проверку, что на главной нет кнопки `/Удаленная оплата/` даже с правами.
- [ ] **Step 2: `npx vitest run src/components/cashier/mobile/mobile-cashier.test.tsx` — падает.**
- [ ] **Step 3: Реализация**: `export type HomeItemKey = MobileMenuKey;` удалить `remote` из `ITEMS`, `quick`, `subtitles`, импорт `Send`; `items={menu.map(...)}`. Комментарий шапки: «Строки главной: разделы меню; POS и удалённая оплата живут в панели внизу.»
- [ ] **Step 4: Тесты проходят, `pos-screen.test.tsx` тоже.**
- [ ] **Step 5: Commit** `fix(cashier): удалённая оплата только внутри POS, строки на главной нет`.

---

### Task 9: Блок «Kaspi / ApiPay» в модалке отделов

**Files:**
- Create: `frontend/src/components/orders/department-manager.tsx` (перенос `DepartmentManager` + `DEPARTMENT_COLORS` из `app/orders/page.tsx:233-419`)
- Create: `frontend/src/components/orders/department-manager.test.tsx`
- Modify: `frontend/src/app/orders/page.tsx` (импорт компонента, убрать неиспользуемые импорты `Check`, `Pencil`, `Settings2`, `Modal`, `Input` если больше не нужны)
- Modify: `frontend/src/lib/types.ts:54-63`

**Interfaces:**
- Consumes: API из Task 3 (`apipay_configured`, `apipay_webhook_configured`, `apipay_key_hint?`, `apipay_updated_at`; PATCH `apipay_api_key`, `apipay_webhook_secret`).

- [ ] **Step 1: Тесты** (моки как в `app/orders/page.test.tsx`: `@/lib/api` с `get/patch/post`, `@/store/auth` с `me`):

```tsx
it("superuser sees the Kaspi block, saves key and secret without echoing them", async () => {
  mocks.me = { is_superuser: true, permissions: ["sys_permissions.manage"] };
  mocks.get.mockResolvedValue({ data: [{ id: 1, code: "mill", name: "Мельница", color: "#315FD5", is_active: true, is_default: true, order_count: 3, created_at: "", apipay_configured: false, apipay_webhook_configured: false, apipay_key_hint: "", apipay_updated_at: null }] });
  mocks.patch.mockResolvedValue({ data: {} });
  render(<DepartmentManager onChanged={() => {}} />);
  await user.click(screen.getByRole("button", { name: /Отделы/ }));
  await user.click(await screen.findByTitle("Изменить отдел"));
  expect(screen.getByText("Не подключён")).toBeInTheDocument();
  await user.type(screen.getByLabelText("API-ключ"), "live-key-1234");
  await user.type(screen.getByLabelText("Секрет вебхука"), "hook");
  await user.click(screen.getByRole("button", { name: "Сохранить ключ" }));
  await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/departments/1/", { apipay_api_key: "live-key-1234", apipay_webhook_secret: "hook" }));
  expect(screen.getByLabelText("API-ключ")).toHaveValue("");
});

it("shows hint and disconnect for a configured department; manager sees no block", ...);
// «Отключить Kaspi» → patch("/departments/1/", { apipay_api_key: "" });
// mocks.me.is_superuser=false → queryByText("Kaspi / ApiPay") null, но строка «Kaspi подключён» видна.
```

- [ ] **Step 2: Падает (нет модуля).**
- [ ] **Step 3: Реализация** — перенести компонент; добавить:

```tsx
function ApiPayBlock({ department, onSaved }: { department: Department; onSaved: () => Promise<void> }) {
  const [key, setKey] = useState(""); const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const webhookUrl = `${typeof window === "undefined" ? "" : window.location.origin}/api/webhooks/apipay/`;
  const status = department.apipay_configured
    ? `Ключ ${department.apipay_key_hint ?? "••••"} · ${department.apipay_webhook_configured ? "вебхук настроен" : "без секрета вебхука"}`
    : "Не подключён";
  async function save(payload: Partial<Record<"apipay_api_key" | "apipay_webhook_secret", string>>) { ... api.patch(`/departments/${department.id}/`, payload) ... setKey(""); setSecret(""); await onSaved(); }
  return (
    <div className="mt-4 rounded-xl border bg-[var(--card)] p-3">
      <div className="text-sm font-bold">Kaspi / ApiPay</div>
      <div className="text-[11px] text-[var(--muted-foreground)]">{status}</div>
      <label className="mt-3 block text-xs font-medium">API-ключ<PasswordInput aria-label="API-ключ" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} /></label>
      <label ...>Секрет вебхука<PasswordInput aria-label="Секрет вебхука" .../></label>
      <div className="mt-2 text-[11px] text-[var(--muted-foreground)]">Адрес вебхука для ключа в кабинете ApiPay: <code>{webhookUrl}</code> <button onClick={() => navigator.clipboard?.writeText(webhookUrl)}>Скопировать</button></div>
      {error && <p className="mt-2 text-sm text-[var(--destructive)]">{error}</p>}
      <Button size="sm" className="mt-3 w-full" disabled={busy || (!key.trim() && !secret.trim())} onClick={() => void save({ ...(key.trim() && { apipay_api_key: key.trim() }), ...(secret.trim() && { apipay_webhook_secret: secret.trim() }) })}>Сохранить ключ</Button>
      {department.apipay_configured && <button className="mt-2 w-full text-xs text-[var(--muted-foreground)]" onClick={() => void save({ apipay_api_key: "" })}>Отключить Kaspi</button>}
    </div>
  );
}
```

В форме: `{editing && me?.is_superuser && <ApiPayBlock department={editing} onSaved={async () => { await reload(); setEditing(latest) }} />}` — после `reload()` обновить `editing` из свежих данных по `id`. В строке списка: `· {department.apipay_configured ? "Kaspi подключён" : "Kaspi не подключён"}`.

`types.ts`: добавить четыре поля в `Department`.

- [ ] **Step 4: `npx vitest run src/components/orders src/app/orders`; `npm run check`.**
- [ ] **Step 5: Commit** `feat(orders): ключ ApiPay отдела в модалке «Отделы продаж» для суперюзера`.

---

### Task 10: Финальная проверка

- [ ] `cd backend && .venv/bin/python -m pytest -q` (полный прогон) — зелёный.
- [ ] `cd frontend && npm run check && npm run build`.
- [ ] `deploy` тесты.
- [ ] `grep -rn "APIPAY_API_KEY\|APIPAY_WEBHOOK_SECRET"` — только compose (optional) и миграция 0004.
- [ ] Пуш ветки `feat/department-apipay-keys`.
