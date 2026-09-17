# Упрощение прав Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Каталог прав = страницы меню; Моноблок только для просмотра по одному праву `monoblock.view`; отгрузка только у грузчика; мёртвые права удалены без потери доступа; шаблоны ролей в карточке сотрудника.

**Architecture:** Бэкенд: новый `perms.py`, одна data-миграция `rbac.0025` (создать → выдать по объединению → удалить старые → обновить подписи), замена кодов во всех гейтах, `SUPERUSER_ONLY` для записей AI 24/7 и настроек сессий, грузчик закрывает открытую AI-сессию перед отгрузкой. Фронт: Моноблок без кнопок действий, гейты по `monoblock.view`, пресеты в `lib/permission-presets.ts`, порядок разделов в пикере как в меню.

**Tech Stack:** Django 5 + DRF (pytest, `backend/.venv/bin/python -m pytest`, свой `DB_NAME`), Next.js 15 + React 19 (vitest, `npm run check`, `npm run build`).

**Spec:** `docs/superpowers/specs/2026-09-17-permissions-simplification-design.md`

## Global Constraints

- Коды, которые остаются, НЕ переименовываются (меняются только подписи); переименование только `shipping.rollback` → `orders.rollback`.
- Никто из сотрудников не теряет доступ: миграция выдаёт объединение (таблица «Убираются» в спеке).
- Суперпользователь проходит любой гейт (`User.has_perm_code`), `SUPERUSER_ONLY` — только суперпользователь.
- Тексты интерфейса — русские; подписи прав `"<Раздел>: <Действие>"`.
- Не коммитить и не пушить без команды юзера (память `push-on-command-only`): шаги «Commit» в этом плане = `git add` явных путей, без `git commit`.
- Проверки перед сдачей: полный `pytest` бэкенда, `npm run check` и `npm run build` фронта.

---

### Task 1: Каталог прав, `SUPERUSER_ONLY` и тест «каждый код из кода есть в каталоге»

**Files:**
- Modify: `backend/apps/sys_permissions/perms.py`
- Modify: `backend/apps/common/permissions.py`
- Modify: `backend/apps/sys_permissions/tests/test_catalog.py`
- Create: `backend/apps/sys_permissions/tests/test_codes_used_exist.py`

**Interfaces:**
- Produces: `apps.common.permissions.SUPERUSER_ONLY: str` — значение в `required_perms` (ViewSet и APIView), означающее «только суперпользователь»; `apps.sys_permissions.perms.SECTION_ORDER: list[str]` (порядок разделов = меню); `ALL_CODES` с новыми кодами `monoblock.view`, `orders.rollback`.

- [ ] **Step 1: Failing test — codes referenced by code exist in the catalog**

```python
# backend/apps/sys_permissions/tests/test_codes_used_exist.py
"""Каждый код права, который проверяет бэкенд или фронт, существует в каталоге.

Иначе право нельзя выдать никому, кроме суперпользователя (так было с grain.edit).
"""
import re
from pathlib import Path

from apps.sys_permissions.perms import ALL_CODES, SECTION_ORDER

ROOT = Path(__file__).resolve().parents[4]
SECTIONS = "|".join(sorted(SECTION_ORDER + ["shipping", "train", "ai_247"], key=len, reverse=True))
CODE = re.compile(rf"""["']((?:{SECTIONS})\.[a-z_]+)["']""")


def _sources():
    backend = ROOT / "backend" / "apps"
    for path in backend.rglob("*.py"):
        parts = set(path.parts)
        if {"tests", "migrations"} & parts or path.name in {"migration_data.py", "conftest.py"}:
            continue
        yield path
    frontend = ROOT / "frontend" / "src"
    if frontend.exists():
        for path in frontend.rglob("*.ts*"):
            if ".test." in path.name:
                continue
            yield path


def test_every_permission_code_used_in_code_is_in_the_catalog():
    missing = {}
    for path in _sources():
        for code in CODE.findall(path.read_text(encoding="utf-8")):
            if code not in ALL_CODES:
                missing.setdefault(code, []).append(str(path.relative_to(ROOT)))
    assert missing == {}
```

- [ ] **Step 2: Run it — expect FAIL** (`grain.edit`, `shipping.*`, `train.*`, … and `SECTION_ORDER` import error)

Run: `cd backend && DB_NAME=test_asyl_perms .venv/bin/python -m pytest -q apps/sys_permissions/tests/test_codes_used_exist.py`

- [ ] **Step 3: New catalog**

Replace `_SECTIONS` / `_ACTION_LABELS` / `PERMISSIONS` in `backend/apps/sys_permissions/perms.py`:

```python
# Разделы = страницы меню в том же порядке; действие — что человек на странице делает.
# Подписи конкретны: право не должно скрывать, что оно разрешает.
_SECTIONS = {
    "reports": ("Отчёты", {"view": "Просмотр", "export": "Выписки Excel"}),
    "orders": ("Заказы", {
        "view": "Просмотр и запрос смены статуса",
        "create": "Создание",
        "edit": "Изменение и удаление",
        "confirm": "Подтверждение заявок",
        "confirm_all": "Заявки всех отделов",
        "correct_price": "Корректировка стоимости",
        "rollback": "Откат отгрузки",
    }),
    "payments": ("Касса", {
        "view": "Транзакции",
        "create": "Приём оплат и POS",
        "confirm": "Подтверждение оплат и возвраты",
    }),
    "monoblock": ("Моноблок", {"view": "Доступ (видит всё)"}),
    "loader": ("Грузчик", {"view": "Очередь и накладные", "confirm": "Отгрузить (списание со склада)"}),
    "warehouse": ("Склады", {"view": "Просмотр", "adjust": "Приход, перемещение и корректировка"}),
    "silos": ("Силосы", {"view": "Просмотр"}),
    "grain": ("Приход и вывоз", {
        "view": "Просмотр",
        "supply": "Новый приход",
        "arrive": "Приём поезда и оформление вывоза",
        "weigh": "Взвешивание и остановки под аркой",
        "correct_weighing": "Ручной заезд и правка выездного веса",
        "inventory": "Расхождения веса и корректировка силосов",
        "delete": "Удаление рейса",
        "admin": "Настройка силосов и видов зерна",
    }),
    "clients": ("Клиенты", {
        "view": "Просмотр",
        "create": "Создание",
        "edit": "Изменение",
        "delete": "Удаление",
        "set_price": "Цены клиента",
        "manage_access": "Доступ в кабинет клиента",
    }),
    "catalog": ("Товары", {"view": "Просмотр", "create": "Создание", "edit": "Изменение и архив"}),
    "tasks": ("Задачи", {"view": "Задачи всех сотрудников", "create": "Создание"}),
    "events": ("Журнал", {"view": "Журнал событий"}),
    "employees": ("Сотрудники", {"view": "Просмотр", "manage": "Изменение профилей"}),
    "sys_permissions": ("Администрирование", {"manage": "Права, отделы, настройки камер и накладной"}),
}

SECTION_ORDER = list(_SECTIONS)

PERMISSIONS = [
    {"code": f"{sec}.{act}", "section": sec, "action": act, "label": f"{sec_label}: {label}"}
    for sec, (sec_label, actions) in _SECTIONS.items()
    for act, label in actions.items()
]
ALL_CODES = {p["code"] for p in PERMISSIONS}
```

- [ ] **Step 4: `SUPERUSER_ONLY` in both mixins** (`backend/apps/common/permissions.py`)

```python
# Значение в required_perms: действие доступно только суперпользователю.
SUPERUSER_ONLY = "__superuser__"
```

In `PermViewSetMixin.get_permissions` after `code = self.required_perms.get(action)` / `if code is None: return [DenyAll()]`:

```python
        if code == SUPERUSER_ONLY:
            return [IsSuperUser()]
```

In `PermAPIViewMixin.get_permissions` after the `if codes is None: return [DenyAll()]` block:

```python
        if codes == SUPERUSER_ONLY:
            return [IsSuperUser()]
```

- [ ] **Step 5: Update `test_catalog.py` known codes**

```python
def test_known_codes_are_present():
    expected = {
        "orders.create",
        "orders.rollback",
        "monoblock.view",
        "loader.confirm",
        "clients.set_price",
        "clients.manage_access",
        "reports.export",
        "employees.manage",
        "grain.delete",
        "grain.correct_weighing",
        "sys_permissions.manage",
    }
    assert expected <= ALL_CODES


def test_retired_codes_are_not_in_runtime_catalog():
    retired = {
        "shipping.view", "shipping.load", "shipping.ship", "shipping.arrive",
        "shipping.rollback", "shipping.debt_override", "train.view", "train.load",
        "ai_247.manage", "sys_permissions.view", "catalog.delete",
        "grain.lab", "grain.dispatch", "grain.unload", "grain.exit",
    }
    assert retired.isdisjoint(ALL_CODES)
```

(Keep `test_codes_are_unique` and `test_legacy_rbac_codes_are_not_in_runtime_catalog`.)

- [ ] **Step 6: Stage** — `git add` the four files. The scan test stays red until Tasks 3, 5 and 6 replace the old codes.

---

### Task 2: Data-миграция `rbac.0025` — выдать объединение, удалить старые коды, обновить подписи

**Files:**
- Create: `backend/apps/sys_permissions/migrations/0025_simplify_permissions.py`
- Create: `backend/apps/sys_permissions/tests/test_simplify_permissions_migration.py`

**Interfaces:**
- Consumes: `perms.PERMISSIONS` shape (code/section/action/label) — the migration freezes its own copy, it does NOT import `perms.py`.

- [ ] **Step 1: Failing migration test**

```python
# backend/apps/sys_permissions/tests/test_simplify_permissions_migration.py
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SimplifyPermissionsMigrationTests(TransactionTestCase):
    migrate_from = [("rbac", "0024_orders_confirm_all_permission"), ("employees", "0011_grant_shipping_operators_payment_permissions")]
    migrate_to = [("rbac", "0025_simplify_permissions"), ("employees", "0011_grant_shipping_operators_payment_permissions")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        User = apps.get_model("accounts", "User")
        Employee = apps.get_model("employees", "Employee")
        Permission = apps.get_model("rbac", "Permission")

        def perm(code):
            section, action = code.split(".")
            return Permission.objects.get_or_create(code=code, defaults={"section": section, "action": action, "label": code})[0]

        def employee(username, *codes):
            user = User.objects.create(username=username)
            emp = Employee.objects.create(user=user, phone=username)
            emp.permissions.add(*[perm(code) for code in codes])
            return emp.pk

        self.shipper = employee("shipper", "shipping.ship", "orders.view")
        self.watcher = employee("watcher", "shipping.view")
        self.wagons = employee("wagons", "train.load")
        self.ai = employee("ai", "ai_247.manage")
        self.rollback = employee("rollback", "shipping.rollback")
        self.dead = employee("dead", "catalog.delete", "sys_permissions.view", "grain.lab", "catalog.edit")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def codes(self, pk):
        Employee = self.apps.get_model("employees", "Employee")
        return set(Employee.objects.get(pk=pk).permissions.values_list("code", flat=True))

    def test_nobody_loses_access_and_old_codes_are_gone(self):
        assert self.codes(self.shipper) == {"monoblock.view", "loader.view", "loader.confirm", "orders.view"}
        assert self.codes(self.watcher) == {"monoblock.view"}
        assert self.codes(self.wagons) == {"monoblock.view", "loader.view", "loader.confirm"}
        assert self.codes(self.ai) == {"monoblock.view"}
        assert self.codes(self.rollback) == {"orders.rollback"}
        assert self.codes(self.dead) == {"catalog.edit"}
        Permission = self.apps.get_model("rbac", "Permission")
        assert not Permission.objects.filter(code__startswith="shipping.").exists()
        assert Permission.objects.get(code="grain.weigh").label == "Приход и вывоз: Взвешивание и остановки под аркой"
```

- [ ] **Step 2: Run — expect FAIL** (`0025_simplify_permissions` not found)

Run: `cd backend && DB_NAME=test_asyl_perms .venv/bin/python -m pytest -q apps/sys_permissions/tests/test_simplify_permissions_migration.py`

- [ ] **Step 3: Write the migration** — freeze the full catalog from Task 1 Step 3 as `CATALOG` (list of `code/section/action/label` dicts, copied verbatim, labels `"<Раздел>: <Действие>"`) plus:

```python
from typing import ClassVar

from django.db import migrations

GRANTS = {
    # старый код -> что получает каждый его обладатель (объединение, без потери доступа)
    "shipping.view": ("monoblock.view",),
    "shipping.load": ("monoblock.view", "loader.view", "loader.confirm"),
    "shipping.ship": ("monoblock.view", "loader.view", "loader.confirm"),
    "train.view": ("monoblock.view",),
    "train.load": ("monoblock.view", "loader.view", "loader.confirm"),
    "ai_247.manage": ("monoblock.view",),
    "shipping.rollback": ("orders.rollback",),
}
RETIRED = (
    *GRANTS,
    "shipping.arrive", "shipping.debt_override", "sys_permissions.view", "catalog.delete",
    "grain.lab", "grain.dispatch", "grain.unload", "grain.exit",
)


def simplify(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Employee = apps.get_model("employees", "Employee")
    db = schema_editor.connection.alias
    by_code = {}
    for row in CATALOG:
        by_code[row["code"]] = Permission.objects.using(db).update_or_create(code=row["code"], defaults=row)[0]
    for old, new_codes in GRANTS.items():
        old_row = Permission.objects.using(db).filter(code=old).first()
        if old_row is None:
            continue
        targets = [by_code[code] for code in new_codes]
        for employee in Employee.objects.using(db).filter(permissions=old_row).distinct().iterator():
            employee.permissions.add(*targets)
    Permission.objects.using(db).filter(code__in=RETIRED).delete()


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0024_orders_confirm_all_permission"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]
    operations: ClassVar[list] = [migrations.RunPython(simplify, migrations.RunPython.noop)]
```

- [ ] **Step 4: Run test — expect PASS.** Also `makemigrations --check --dry-run` → «No changes detected».
- [ ] **Step 5: Stage** both files.

---

### Task 3: Бэкенд-гейты на новые коды

**Files (modify):**
- `backend/apps/shipments/views.py:45-51`
- `backend/apps/orders/views.py:760,775-776,786-801`
- `backend/apps/cameras/api_views/operations.py:46-52,175,560,580-581,649-650,682,691`
- `backend/apps/cameras/api_views/history.py:36,144,223`
- `backend/apps/cameras/api_views/configuration.py:134`
- `backend/apps/cameras/api_views/shipping_sessions.py:28-60,174-219`
- `backend/apps/cameras/api_views/shipping_automation.py:28`
- `backend/apps/cameras/api_views/counting.py:200-267`
- `backend/apps/cameras/policies.py:130-147`
- `backend/apps/cameras/models.py:184` (comment)
- `backend/apps/grain/views.py:463-478,1127`
- `backend/apps/catalog/views.py:28`
- `backend/apps/sys_permissions/views.py:18-26`
- Tests: `backend/apps/conftest.py` fixtures and every test file listed by `grep -rln "shipping\.\(view\|load\|ship\|arrive\|rollback\|debt_override\)\|train\.\(view\|load\)\|ai_247\.manage\|sys_permissions\.view\|catalog\.delete\|grain\.\(lab\|dispatch\|unload\|exit\|edit\)" backend/apps --include='*.py'`

**Interfaces:**
- Consumes: `SUPERUSER_ONLY` (Task 1).

- [ ] **Step 1: Replace gates** (exact mapping — apply literally):

| Where | Old | New |
|---|---|---|
| shipments `ShipmentViewSet.required_perms` arrive/load/finish_loading/ship/rewind_loading | `shipping.*` | `"loader.confirm"` (comment: «Отгрузка — работа грузчика; UI вызывает только dispatch, эти шаги остаются API») |
| orders `rollback_shipment` | `shipping.rollback` | `orders.rollback` |
| orders `train`, `loading_camera` | `train.load`, `shipping.load` | `"loader.confirm"` |
| orders post board `HasPerm(...)` | `orders.view, shipping.view, shipping.load, train.view` | `HasPerm("orders.view", "monoblock.view", "loader.view")` |
| operations `ALWAYS_ON_READ_PERMISSIONS` | `("shipping.load", "ai_247.manage")` | `("monoblock.view",)` |
| operations `ALWAYS_ON_MANAGE_PERMISSION` | `"ai_247.manage"` | `SUPERUSER_ONLY` (rename constant usage unchanged) |
| operations `SHIPPING_CONTINUOUS_READ_PERMISSIONS` | `shipping.load, shipping.view, sys_permissions.manage` | `("monoblock.view", "sys_permissions.manage")` |
| operations shipping-settings GET (`:691`) | `HasPerm("shipping.view", "sys_permissions.manage")` | `HasPerm("monoblock.view", "sys_permissions.manage")` |
| history `:36` | `HasPerm("shipping.load", "shipping.view", "train.load", "train.view")` | `HasPerm("monoblock.view")` |
| history `:144`, `:223` | `HasPerm("shipping.view")` | `HasPerm("monoblock.view")` |
| configuration `:134` | `HasPerm("shipping.load", "sys_permissions.manage")` | `HasPerm("monoblock.view", "sys_permissions.manage")` |
| shipping_sessions `READ_PERMS` | 4 codes | `("monoblock.view",)` |
| shipping_sessions `WRITE_PERMS` / identify / idle settings PATCH | `shipping.load`/`train.load` per kind | superuser only: `[IsSuperUser()]`; delete the per-kind `code = ...` checks and `_allowed(...)` transport filtering (one right sees both transports) |
| shipping_automation `PERMISSIONS` | 4 codes | `("monoblock.view",)` |
| counting `:202-203`, `:267` | `HasPerm("shipping.load", "train.load")` / `HasPerm("shipping.load")` | `HasPerm("loader.confirm")` |
| counting DELETE body `:247-251` | per-transport `shipping.load`/`train.load` check | `if not request.user.has_perm_code("loader.confirm"): raise PermissionDenied("Нет права на отгрузку")` |
| policies `can_control_session` | automatic-only clause with `train.load`/`shipping.load` | `or user.has_perm_code("loader.confirm")` (грузчик завершает любую сессию заказа, который отгружает) |
| grain approve/lab/suggest/assign/change silo/unloading/exit | `grain.dispatch/lab/unload/exit` | `"grain.admin"` (comment: «старый процесс вагонов») |
| grain `:1127` arch stop close | `grain.edit` | `grain.weigh` |
| catalog destroy | `catalog.delete` | `catalog.edit` |
| sys_permissions list/retrieve | `("sys_permissions.view", "employees.manage")` | `("sys_permissions.manage", "employees.manage")` |

- [ ] **Step 2: Update fixtures** in `backend/apps/conftest.py`: in `operator` replace `shipping.view/arrive/load/ship` with `"monoblock.view", "loader.view", "loader.confirm"`; in `boss` replace the six `shipping.*` codes with `"monoblock.view", "loader.view", "loader.confirm", "orders.rollback"`; replace any `train.*`/`ai_247.manage` the same way (`train.*` → `monoblock.view`+`loader.confirm`, `ai_247.manage` → superuser in the test).

- [ ] **Step 3: Run the affected suites, fix each test by the mapping above** (tests asserting that `shipping.view`-only users are denied writes stay valid with `monoblock.view`; tests where `ai_247.manage` users write production settings become superuser; `grain.lab`/`dispatch`/`unload`/`exit` users → `grain.admin`; wagon-arch stop close → `grain.weigh`):

Run: `cd backend && DB_NAME=test_asyl_perms .venv/bin/python -m pytest -q apps/cameras apps/shipments apps/orders apps/grain apps/catalog apps/sys_permissions apps/sales apps/employees`
Expected after fixes: all pass; `test_codes_used_exist.py` still lists only frontend files.

- [ ] **Step 4: Stage** changed backend files.

---

### Task 4: «Отгружено» у грузчика закрывает открытую AI-сессию

**Files:**
- Modify: `backend/apps/cameras/counting.py` (new function after `stop`)
- Modify: `backend/apps/shipments/views.py` (`LoaderViewSet.confirm`)
- Modify: `backend/apps/shipments/services.py:283-285` (message)
- Test: `backend/apps/shipments/tests/test_loader.py`

**Interfaces:**
- Produces: `apps.cameras.counting.close_session_for_dispatch(order: Order, user) -> None` — если по заказу открыта AI-сессия: активная при статусе `loading` завершается с посчитанными мешками (заказ → `loaded`), иначе отменяется; ошибки камеры — `ai.AiError`/`ai.AiUnavailable`.

- [ ] **Step 1: Failing tests** (append to `test_loader.py`; reuse the file's existing order/loader fixtures and `patch`):

```python
from unittest.mock import patch

from apps.cameras.models import AiCountingSession


def test_dispatch_closes_open_ai_session_and_ships(auth_client, loader_user, shippable_order):
    shippable_order.status = "loading"
    shippable_order.save(update_fields=["status"])
    session = AiCountingSession.objects.create(order=shippable_order, camera="cam2", status=AiCountingSession.ACTIVE)

    def fake_stop(camera, order, user, *, complete_order, expected_session_id):
        assert (camera, complete_order, expected_session_id) == ("cam2", True, session.pk)
        AiCountingSession.objects.filter(pk=session.pk).update(status=AiCountingSession.CLOSED)
        type(order).objects.filter(pk=order.pk).update(status="loaded")
        return {}

    with patch("apps.cameras.counting.stop", side_effect=fake_stop) as stop:
        response = auth_client(loader_user).post(f"/api/loader/orders/{shippable_order.pk}/dispatch/", {}, format="json")

    assert response.status_code == 200, response.data
    stop.assert_called_once()
    shippable_order.refresh_from_db()
    assert shippable_order.status == "shipped"


def test_dispatch_reports_unreachable_camera(auth_client, loader_user, shippable_order):
    shippable_order.status = "loading"
    shippable_order.save(update_fields=["status"])
    AiCountingSession.objects.create(order=shippable_order, camera="cam2", status=AiCountingSession.ACTIVE)
    from apps.cameras import ai

    with patch("apps.cameras.counting.stop", side_effect=ai.AiUnavailable("down")):
        response = auth_client(loader_user).post(f"/api/loader/orders/{shippable_order.pk}/dispatch/", {}, format="json")

    assert response.status_code == 502
    assert response.data["code"] == "ai_unavailable"
    shippable_order.refresh_from_db()
    assert shippable_order.status == "loading"
```

(If `test_loader.py` names its fixtures differently, use those names; `AiCountingSession` may require extra non-null fields — create it the way `apps/cameras/tests/test_ai.py` does.)

- [ ] **Step 2: Run — expect FAIL** (`stop` never called; 400 `ai_session_active`).

- [ ] **Step 3: Implement**

`backend/apps/cameras/counting.py`:

```python
def close_session_for_dispatch(order: Order, user) -> None:
    """Грузчик отгружает заказ, по которому открыт AI-подсчёт.

    Активный подсчёт идущей погрузки завершается штатно — в заказ идут мешки,
    посчитанные камерой; незапущенный или сбойный — отменяется, и отгрузка
    возьмёт заказанное количество.
    """
    session = (
        AiCountingSession.objects.filter(order_id=order.pk, status__in=AiCountingSession.OPEN_STATUSES)
        .order_by("-pk")
        .first()
    )
    if session is None:
        return
    status = Order.objects.filter(pk=order.pk).values_list("status", flat=True).first()
    stop(
        session.camera,
        order,
        user,
        complete_order=session.status == AiCountingSession.ACTIVE and status == "loading",
        expected_session_id=session.pk,
    )
```

`backend/apps/shipments/views.py` `LoaderViewSet.confirm`:

```python
    @action(detail=True, methods=["post"], url_path="dispatch")
    def confirm(self, request, pk=None):
        from apps.cameras import ai, counting

        serializer = LoaderDispatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = self.get_object()
        try:
            # Кнопок погрузки в Моноблоке больше нет: открытый подсчёт закрывает сама отгрузка.
            counting.close_session_for_dispatch(order, request.user)
        except ai.AiUnavailable:
            return Response({"detail": "AI-сервис камер недоступен", "code": "ai_unavailable"}, status=502)
        except ai.AiError as exc:
            return Response({"detail": exc.detail, "code": "ai_error"}, status=exc.status if exc.status in (400, 409, 503) else 502)
        dispatch_order(order, request.user, truck_number=serializer.validated_data["truck_number"])
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)
```

`services.py` `_assert_no_open_ai_session` detail → `"По заказу идёт AI-подсчёт — отгрузите его на странице «Грузчик»"`.

- [ ] **Step 4: Run** `apps/shipments/tests/test_loader.py` and `apps/cameras/tests/test_ai.py` — PASS.
- [ ] **Step 5: Stage.**

---

### Task 5: Моноблок только для просмотра (фронт)

**Files:**
- Modify: `frontend/src/app/monoblock/page.tsx:1405-1425,1502,1683-1720,1765-1815,1870-1955`
- Modify: `frontend/src/components/shipping/shipping-table.tsx`
- Modify: `frontend/src/components/shipping/shipping-row-detail.tsx`
- Modify: `frontend/src/app/monoblock/shipping-segments/[id]/print/page.tsx:10`
- Modify: `frontend/src/components/layout/sidebar.tsx:70-77`
- Modify: `frontend/src/app/dashboard/page.tsx:669`
- Delete: `frontend/src/components/shipping/use-shipping-actions.ts`, `bag-counter.tsx`, `bag-counter.test.tsx`, `rewind-loading-modal.tsx`, `frontend/src/lib/shipping-flow.ts`, `shipping-flow.test.ts`, `frontend/src/components/monoblock/shipping-transport-status.tsx` + test (not mounted)
- Tests: `frontend/src/app/monoblock/page.test.tsx`, `live-detections.test.tsx`, `components/shipping/shipping-table.test.tsx`, `components/layout/sidebar.test.tsx`

**Interfaces:**
- Produces: `ShippingTableCapabilities = { canOpenOrder: boolean }` (history and evidence visible to every monoblock viewer).

- [ ] **Step 1: Failing tests**

`shipping-table.test.tsx` — replace action-button tests with:

```tsx
it("is read-only: rows show status and history but no shipping actions", async () => {
  renderTable({ orders: [loadedOrder, loadingOrder], capabilities: { canOpenOrder: true } });
  expect(screen.queryByRole("button", { name: "Завершить погрузку" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Оформить выезд" })).not.toBeInTheDocument();
  expect(screen.getAllByText("Ожидает оформления выезда").length).toBeGreaterThan(0);
});
```

`page.test.tsx` — the page opens with only `permissions: ["monoblock.view"]`, shows tabs «Отгрузка» and «AI 24/7», and requests no write endpoint while rendering; «Настроить» and «Куда приходовать → Сохранить» only for `is_superuser: true`.

`sidebar.test.tsx` — «Моноблок» link visible with `monoblock.view`, hidden without it.

- [ ] **Step 2: Run** `npx vitest run src/app/monoblock src/components/shipping src/components/layout` — FAIL.

- [ ] **Step 3: Page gates** in `MonoblockPageInner`:

```tsx
  const { me } = useAuth();
  const canView = can(me, "monoblock.view");
  const canManage = can(me, "sys_permissions.manage");
  // Моноблок только для просмотра: одно право видит всё. Отгрузка — на странице «Грузчик»,
  // настройки AI 24/7 и «Куда приходовать» — только суперпользователь.
  const canManageAlwaysOn = !!me?.is_superuser;
  const canOpenOrder = can(me, "orders.view");
  const canViewCameraSettings = canView || canManage;
  const canViewShippingSettings = canView || canManage;
```

Delete `canLoad`, `canTrain`, `canShip`, `canRollback`, `canViewShipping`, `canViewAlwaysOn`, `canViewSessions`, `canViewContinuous`; every former use becomes `canView` (fetch URLs), tabs bar always rendered (`showHeader = true`, `activeTab = tab`), `defaultTransportType = "truck"`, stat card `tone={counts.ready > 0 ? "success" : undefined}`, `ShippingTable capabilities={{ canOpenOrder }}` and `reloadHistories={reloadHistories}`. `MonoblockPage`: `<RequirePerm perm="monoblock.view" title="Моноблок">`. Card subtitle «Очередь, погрузка и оформление выезда.» → «Очередь и погрузка. Отгружает грузчик на странице «Грузчик».»

- [ ] **Step 4: Table without actions** in `shipping-table.tsx`: remove imports of `BagCounterHandle`, `RewindLoadingModal`, `ShipmentRollbackModal`, `use-shipping-actions`, `ConfirmDialog`, `ShippingCapabilities`, `apiError`; remove `Dialog` type, `flowCapabilities`, `useShippingActions`, `bagCounterRef`, dialog state, `finishRow`, `confirmDialog`, `openFinish`, the three modals. `rowActions` becomes:

```tsx
  type RowView = { note: string | null; menu: ActionMenuItem[] };
  function rowView(row: Row): RowView {
    const menu: ActionMenuItem[] = [];
    if (row.kind === "session") return { note: "Идёт погрузка", menu };
    const { order, session, history } = row;
    let note: string | null = null;
    if (order.status === "confirmed") {
      note = session ? (session.status === "starting" ? "Привязка заказа" : "Идёт погрузка") : "Ожидает распознавания номера";
    } else if (isLoadingStatus(order.status)) {
      note = "Идёт погрузка";
    } else if (order.status === "loaded") {
      note = "Ожидает оформления выезда";
    }
    if (history && (order.status === "loaded" || order.status === "shipped")) {
      menu.push({ key: "history", label: "История подсчёта", icon: Film, onSelect: () => setHistoryOpen(history) });
    }
    if (canOpenOrder) {
      menu.push({ key: "open", label: "Открыть заказ", onSelect: () => router.push(`/orders/${order.id}`) });
    }
    return { note, menu };
  }
```

Cells render `note` where they rendered `primary`/`note`; `ShippingRowDetail` gets no `onSaveBags`/`onAccept`/`bagCounterRef`/`busy`/`canCount` props (remove the counter and «Принять N» from the detail; keep camera, live total and transport evidence).

- [ ] **Step 5: Sidebar, dashboard, print page**: sidebar Моноблок `perm: "monoblock.view"` (comment: «Моноблок только для просмотра — одно право»); dashboard camera wall `can(me, "monoblock.view")`; print page `RequirePerm perm="monoblock.view"`.

- [ ] **Step 6: Delete** the dead files listed above; `npx tsc --noEmit -p .` clean.
- [ ] **Step 7: Run** tests from Step 2 — PASS. Stage.

---

### Task 6: Окно прав — порядок разделов, откат в «Заказах», шаблоны ролей

**Files:**
- Create: `frontend/src/lib/permission-presets.ts`, `frontend/src/lib/permission-presets.test.ts`
- Modify: `frontend/src/components/permission-picker.tsx`, `permission-picker.test.tsx`
- Modify: `frontend/src/app/management/employees/page.tsx:560-575`
- Modify: `frontend/src/app/orders/page.tsx:622`, `frontend/src/app/orders/[id]/page.tsx:142` (`shipping.rollback` → `orders.rollback`)

**Interfaces:**
- Produces: `PERMISSION_PRESETS: { key: string; label: string; codes: string[] }[]`; `SECTION_ORDER: string[]` (frontend copy of backend order); `PermissionPicker` sorts sections by `SECTION_ORDER`.

- [ ] **Step 1: Failing tests**

```ts
// frontend/src/lib/permission-presets.test.ts
import { expect, it } from "vitest";
import { PERMISSION_PRESETS, applyPreset } from "./permission-presets";

it("a preset replaces the selection but skips codes the admin cannot grant", () => {
  const cashier = PERMISSION_PRESETS.find((preset) => preset.key === "cashier")!;
  const next = applyPreset(cashier, new Set(["grain.view"]), new Set(["reports.view"]));
  expect(next.has("grain.view")).toBe(false);
  expect(next.has("payments.confirm")).toBe(true);
  expect(next.has("reports.view")).toBe(false);
});
```

```tsx
// permission-picker.test.tsx
it("orders sections like the menu, not alphabetically", () => {
  render(<PermissionPicker perms={[perm("tasks.view"), perm("orders.view"), perm("monoblock.view")]} selected={new Set()} onToggle={vi.fn()} />);
  expect(screen.getAllByRole("heading").map((h) => h.textContent)).toEqual(["Заказы", "Моноблок", "Задачи"]);
});
```

(`perm(code)` helper builds `{ id, code, section, action, label }`; section titles render as headings — change the title `div` to `<h3>`.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** `permission-presets.ts`:

```ts
/** Порядок разделов = меню (как `SECTION_ORDER` в backend/apps/sys_permissions/perms.py). */
export const SECTION_ORDER = [
  "reports", "orders", "payments", "monoblock", "loader", "warehouse", "silos",
  "grain", "clients", "catalog", "tasks", "events", "employees", "sys_permissions",
];

export interface PermissionPreset { key: string; label: string; codes: string[] }

/** Шаблоны ролей: ставят галочки в один клик, дальше права правятся вручную. */
export const PERMISSION_PRESETS: PermissionPreset[] = [
  { key: "cashier", label: "Кассир", codes: ["payments.view", "payments.create", "payments.confirm", "reports.view", "clients.view", "orders.view"] },
  { key: "accountant", label: "Бухгалтер", codes: ["reports.view", "reports.export", "payments.view", "payments.confirm", "clients.view", "orders.view"] },
  { key: "manager", label: "Менеджер", codes: ["orders.view", "orders.create", "orders.edit", "orders.confirm", "orders.correct_price", "clients.view", "clients.create", "clients.edit", "clients.set_price", "clients.manage_access", "catalog.view", "warehouse.view"] },
  { key: "loader", label: "Грузчик", codes: ["loader.view", "loader.confirm", "monoblock.view"] },
  { key: "weigher", label: "Весовщик", codes: ["grain.view", "grain.arrive", "grain.weigh", "grain.correct_weighing"] },
  { key: "storekeeper", label: "Кладовщик", codes: ["warehouse.view", "warehouse.adjust", "silos.view", "catalog.view"] },
  { key: "observer", label: "Наблюдатель", codes: ["monoblock.view", "orders.view", "reports.view"] },
];

/** Шаблон заменяет выбор; права, которые текущий админ выдать не может, не ставятся. */
export function applyPreset(preset: PermissionPreset, _current: Set<string>, ungrantable: Set<string>): Set<string> {
  return new Set(preset.codes.filter((code) => !ungrantable.has(code)));
}
```

Picker: `PERM_SECTION_LABELS` → `{ reports: "Отчёты", orders: "Заказы", payments: "Касса", monoblock: "Моноблок", loader: "Грузчик", warehouse: "Склады", silos: "Силосы", grain: "Приход и вывоз", clients: "Клиенты", catalog: "Товары", tasks: "Задачи", events: "Журнал", employees: "Сотрудники", sys_permissions: "Администрирование" }`; sections sorted by `SECTION_ORDER.indexOf` (unknown last).

Employees page above `<PermissionPicker>` inside the fieldset:

```tsx
<div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Шаблоны ролей">
  {PERMISSION_PRESETS.map((preset) => (
    <Chip key={preset.key} active={false} onClick={() => choosePreset(preset)}>
      {preset.label}
    </Chip>
  ))}
</div>
```

with

```tsx
  function choosePreset(preset: PermissionPreset) {
    if (selectedPermissions.size > 0 && !window.confirm(`Заменить отмеченные права шаблоном «${preset.label}»?`)) return;
    setSelectedPermissions(applyPreset(preset, selectedPermissions, ungrantablePermissions));
  }
```

- [ ] **Step 4: Run** tests + `test_codes_used_exist.py` (backend) — both PASS now. Stage.

---

### Task 7: Полная проверка и документация

**Files:**
- Modify: `deploy/shipping-transports.md` (first line: «Устарело: сервис не запускается; отгрузка — страница «Грузчик».»)

- [ ] **Step 1:** `cd backend && DB_NAME=test_asyl_full_perms .venv/bin/python -m pytest -q` — all pass.
- [ ] **Step 2:** `cd backend && .venv/bin/python manage.py makemigrations --check --dry-run` — no changes.
- [ ] **Step 3:** `cd frontend && npm run check && npm run build` — pass.
- [ ] **Step 4:** Preview check (memory `verify-ui-in-preview`): local backend 8010 + frontend; user with only `monoblock.view` sees Моноблок without buttons; employee card shows presets and menu-ordered sections.
- [ ] **Step 5:** Stage everything; report to the user. No commit/push without command.
