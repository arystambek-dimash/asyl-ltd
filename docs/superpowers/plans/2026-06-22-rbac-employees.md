# RBAC + Employees Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded roles with a dynamic RBAC system: a fixed permission catalog in code, admin-managed roles (permissions per section/action), and employee accounts (User profile + one role), enforced by a single `HasPerm` class and surfaced to the frontend via `me.permissions` and a `can()` helper.

**Architecture:** New `rbac` app holds the Permission/Role models, the single permission catalog (`rbac/perms.py`), and the `HasPerm` class. New `employees` app holds the Employee profile (OneToOne→User) and its API. All existing views switch from `IsManager`/etc. to a `required_perms` dict resolved by `HasPerm`. The frontend gates nav and buttons through one `can(code)` helper reading `me.permissions`.

**Tech Stack:** Django 5, DRF, djangorestframework-simplejwt, PostgreSQL, pytest; Next.js 15 + Tailwind + zustand.

## Global Constraints

- Single source of truth for permission codes: `rbac/perms.py`. No magic strings elsewhere.
- One DRF permission class `HasPerm`; views declare `required_perms: dict[str, str]` (action → code). No `if user.is_manager` anywhere.
- Permission check lives in `User.has_perm_code(code)`; superuser → always True.
- Employee = OneToOne profile on `accounts.User`; one `role` (FK→Role).
- Roles are dynamic (admin CRUD); system presets have `is_system=True`, editable but not deletable.
- Employee creation is transactional (User + Employee atomic).
- Passwords: `write_only`, stored via `set_password`, never returned.
- A role with assigned employees cannot be deleted (clear 400, not cascade).
- Error shape `{"detail": "...", "code": "..."}`; Russian messages.
- `shipping.debt_override` permission replaces the `user.is_boss` debt rule.
- `IsStaff`/`IsClientUser` kept (staff vs portal client). `IsManager/IsAccountant/IsOperator/IsBoss/IsOperatorOrBoss` removed.
- Tests written first (TDD). Backend run from `backend/` with venv active.

---

## File Structure

```
backend/
  rbac/
    perms.py            # PERMISSIONS catalog (sections, actions, codes) + PRESETS
    models.py           # Permission, Role
    permissions.py      # HasPerm (the only access class beyond IsStaff/IsClientUser)
    serializers.py      # PermissionSerializer, RoleSerializer
    views.py            # PermissionViewSet (RO), RoleViewSet
    urls.py
    admin.py
    migrations/000X_seed_permissions_and_presets.py
  employees/
    models.py           # Employee (OneToOne→User, role FK, profile fields)
    serializers.py      # EmployeeSerializer (write-only password)
    views.py            # EmployeeViewSet (transactional create)
    urls.py
    admin.py
  accounts/
    models.py           # +has_perm_code(), +perm_codes  (remove is_manager/.. props)
    serializers.py      # MeSerializer +permissions
  config/settings.py    # add rbac, employees
  config/urls.py        # include rbac.urls, employees.urls
  catalog/clients/orders/warehouse/shipments/eventlog views.py  # required_perms
  shipments/services.py # debt rule → has_perm_code

frontend/
  src/lib/types.ts      # Me.permissions; Role, Permission, Employee types
  src/store/auth.ts     # (me already carries permissions)
  src/lib/can.ts        # can(me, code)
  src/components/layout/sidebar.tsx  # nav gated by <section>.view
  src/app/management/employees/page.tsx
  src/app/management/roles/page.tsx
```

---

### Task 1: Permission catalog + Permission/Role models

**Files:**
- Create: `backend/rbac/__init__.py`, `backend/rbac/apps.py`, `backend/rbac/perms.py`, `backend/rbac/models.py`, `backend/rbac/admin.py`, `backend/rbac/tests/__init__.py`, `backend/rbac/tests/test_catalog.py`
- Modify: `backend/config/settings.py` (add `"rbac"`)

**Interfaces:**
- Produces:
  - `rbac.perms.PERMISSIONS: list[dict]` each `{"code","section","action","label"}`; `ALL_CODES: set[str]`.
  - `rbac.perms.PRESETS: dict[str, list[str]]` — preset name → list of codes.
  - `rbac.models.Permission(code unique, section, action, label)`.
  - `rbac.models.Role(name unique, description, is_system bool, permissions M2M→Permission)` with `__str__` = name.

- [ ] **Step 1: Scaffold the app**

Run:
```bash
cd backend && . .venv/bin/activate
python manage.py startapp rbac
mkdir -p rbac/tests && touch rbac/tests/__init__.py && rm -f rbac/tests.py
```

- [ ] **Step 2: Write the permission catalog**

`backend/rbac/perms.py`:
```python
# Единый источник истины для кодов прав. Все ссылки импортируют отсюда.

_SECTIONS = {
    "catalog": ("Номенклатура", ["view", "create", "edit", "delete"]),
    "clients": ("Клиенты", ["view", "create", "edit", "delete"]),
    "warehouse": ("Склад", ["view", "adjust"]),
    "orders": ("Заказы", ["view", "create", "edit", "confirm"]),
    "payments": ("Оплаты", ["view", "create"]),
    "shipping": ("Пост отгрузки", ["view", "arrive", "load", "ship", "debt_override"]),
    "events": ("Журнал", ["view"]),
    "reports": ("Отчёты", ["view"]),
    "employees": ("Сотрудники", ["view", "manage"]),
}

_ACTION_LABELS = {
    "view": "Просмотр", "create": "Создание", "edit": "Редактирование",
    "delete": "Удаление", "adjust": "Корректировка", "confirm": "Подтверждение",
    "arrive": "Приём машины", "load": "Загрузка", "ship": "Отгрузка",
    "debt_override": "Отгрузка в долг", "manage": "Управление",
}

PERMISSIONS = [
    {"code": f"{sec}.{act}", "section": sec, "action": act,
     "label": f"{sec_label}: {_ACTION_LABELS[act]}"}
    for sec, (sec_label, acts) in _SECTIONS.items()
    for act in acts
]
ALL_CODES = {p["code"] for p in PERMISSIONS}
SECTION_LABELS = {sec: lbl for sec, (lbl, _) in _SECTIONS.items()}

def _codes(*sections_or_codes):
    out = []
    for x in sections_or_codes:
        if x in _SECTIONS:
            out += [f"{x}.{a}" for a in _SECTIONS[x][1]]
        else:
            out.append(x)
    return out

PRESETS = {
    "Менеджер": _codes("catalog", "clients", "orders",
                       "payments.view", "reports.view", "events.view"),
    "Бухгалтер": _codes("payments.view", "payments.create", "orders.view",
                        "clients.view", "reports.view", "events.view"),
    "Оператор": _codes("shipping.view", "shipping.arrive", "shipping.load",
                       "shipping.ship", "orders.view", "warehouse.view", "events.view"),
    "Начальник": _codes("catalog", "clients", "orders", "payments.view",
                        "payments.create", "warehouse", "shipping", "reports.view",
                        "events.view"),
}
```

- [ ] **Step 3: Write the failing catalog test**

`backend/rbac/tests/test_catalog.py`:
```python
from rbac.perms import PERMISSIONS, ALL_CODES, PRESETS


def test_codes_unique():
    codes = [p["code"] for p in PERMISSIONS]
    assert len(codes) == len(set(codes))


def test_presets_reference_existing_codes():
    for name, codes in PRESETS.items():
        for c in codes:
            assert c in ALL_CODES, f"{name}: unknown code {c}"


def test_known_codes_present():
    for c in ("orders.create", "shipping.debt_override", "employees.manage"):
        assert c in ALL_CODES
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest rbac/tests/test_catalog.py -v`
Expected: 3 PASS (pure-python, no DB).

- [ ] **Step 5: Implement models**

`backend/rbac/models.py`:
```python
from django.db import models


class Permission(models.Model):
    code = models.CharField(max_length=50, unique=True)
    section = models.CharField(max_length=30)
    action = models.CharField(max_length=30)
    label = models.CharField(max_length=120)

    def __str__(self):
        return self.code


class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=300, blank=True, default="")
    is_system = models.BooleanField(default=False)
    permissions = models.ManyToManyField(Permission, related_name="roles", blank=True)

    def __str__(self):
        return self.name
```

`backend/rbac/admin.py`:
```python
from django.contrib import admin
from .models import Permission, Role

admin.site.register([Permission, Role])
```

- [ ] **Step 6: Register app, migrate**

Add `"rbac"` to `INSTALLED_APPS` in `backend/config/settings.py`.
Run:
```bash
python manage.py makemigrations rbac && python manage.py migrate
pytest rbac/ -q
```
Expected: catalog tests still pass; migration applies.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(rbac): permission catalog, Permission/Role models"
```

---

### Task 2: User.has_perm_code + perm_codes; remove role props

**Files:**
- Modify: `backend/accounts/models.py`
- Test: `backend/accounts/tests/test_perm_codes.py`

**Interfaces:**
- Consumes: `employees.Employee.role` (defined Task 4) — accessed defensively via `getattr`.
- Produces:
  - `User.has_perm_code(code: str) -> bool` — True if superuser, else code in role's permissions.
  - `User.perm_codes -> set[str]` — all codes for the user (empty if no employee/role).

- [ ] **Step 1: Write the failing test**

`backend/accounts/tests/test_perm_codes.py`:
```python
import pytest
from rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


def _role_with(*codes):
    role = Role.objects.create(name="R")
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            code=c, defaults={"section": c.split(".")[0], "action": c.split(".")[1], "label": c})
        role.permissions.add(p)
    return role


def test_superuser_has_any_code(make_user):
    u = make_user(username="su")
    u.is_superuser = True
    u.save()
    assert u.has_perm_code("orders.create") is True


def test_employee_role_grants_code(make_user):
    from employees.models import Employee
    u = make_user(username="e1")
    role = _role_with("orders.view")
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    assert u.has_perm_code("orders.view") is True
    assert u.has_perm_code("orders.create") is False
    assert "orders.view" in u.perm_codes


def test_no_employee_no_codes(make_user):
    u = make_user(username="e2")
    assert u.perm_codes == set()
    assert u.has_perm_code("orders.view") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest accounts/tests/test_perm_codes.py -v`
Expected: FAIL (`has_perm_code` missing; `employees` import will also fail until Task 4 — that's expected; run this test after Task 4, see Step 4 note).

- [ ] **Step 3: Implement on User, remove old props**

In `backend/accounts/models.py` replace the role properties block. New `User`:
```python
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)

    @property
    def _role(self):
        emp = getattr(self, "employee", None)
        return emp.role if emp else None

    @property
    def perm_codes(self) -> set:
        if self.is_superuser:
            from rbac.perms import ALL_CODES
            return set(ALL_CODES)
        role = self._role
        if role is None:
            return set()
        return set(role.permissions.values_list("code", flat=True))

    def has_perm_code(self, code: str) -> bool:
        if self.is_superuser:
            return True
        role = self._role
        return role is not None and role.permissions.filter(code=code).exists()
```
(Remove `_in_group`, `is_manager`, `is_accountant`, `is_operator`, `is_boss`.)

- [ ] **Step 4: Run the test (after Task 4 exists)**

Note: this test imports `employees.models.Employee`. Run it after Task 4 is implemented:
Run: `pytest accounts/tests/test_perm_codes.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(accounts): has_perm_code/perm_codes, drop hardcoded role props"
```

---

### Task 3: HasPerm permission class

**Files:**
- Create: `backend/rbac/permissions.py`
- Test: `backend/rbac/tests/test_hasperm.py`

**Interfaces:**
- Consumes: `User.has_perm_code` (Task 2).
- Produces:
  - `rbac.permissions.HasPerm(code)` — DRF `BasePermission` instance factory; `has_permission` returns `request.user.has_perm_code(code)` for authenticated non-client staff (and superuser).
  - Pattern for views: define `required_perms = {"list": "x.view", ...}` and a `PermViewSetMixin.get_permissions()` that resolves the code by `self.action`.

- [ ] **Step 1: Write the failing test**

`backend/rbac/tests/test_hasperm.py`:
```python
import pytest
from rbac.permissions import HasPerm
from rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


class _Req:
    def __init__(self, user):
        self.user = user


def test_superuser_allowed(make_user):
    u = make_user(username="su"); u.is_superuser = True; u.save()
    assert HasPerm("orders.create").has_permission(_Req(u), None) is True


def test_user_with_code_allowed(make_user):
    from employees.models import Employee
    u = make_user(username="m")
    role = Role.objects.create(name="R")
    p = Permission.objects.create(code="orders.view", section="orders", action="view", label="x")
    role.permissions.add(p)
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    assert HasPerm("orders.view").has_permission(_Req(u), None) is True
    assert HasPerm("orders.create").has_permission(_Req(u), None) is False


def test_anon_denied():
    from django.contrib.auth.models import AnonymousUser
    assert HasPerm("orders.view").has_permission(_Req(AnonymousUser()), None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest rbac/tests/test_hasperm.py -v`
Expected: FAIL (`rbac.permissions` missing).

- [ ] **Step 3: Implement HasPerm + mixin**

`backend/rbac/permissions.py`:
```python
from rest_framework.permissions import BasePermission


class HasPerm(BasePermission):
    def __init__(self, code):
        self.code = code

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_client", False):
            return False
        return user.has_perm_code(self.code)


class PermViewSetMixin:
    """Resolve required_perms[action] → HasPerm(code)."""
    required_perms: dict = {}

    def get_permissions(self):
        code = self.required_perms.get(getattr(self, "action", None))
        if code is None:
            from rest_framework.permissions import IsAuthenticated
            return [IsAuthenticated()]
        return [HasPerm(code)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest rbac/tests/test_hasperm.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(rbac): HasPerm permission class + PermViewSetMixin"
```

---

### Task 4: Employee model + API

**Files:**
- Create: `backend/employees/` app: `models.py`, `serializers.py`, `views.py`, `urls.py`, `admin.py`, `tests/__init__.py`, `tests/test_employees.py`
- Modify: `backend/config/settings.py` (add `"employees"`), `backend/config/urls.py`

**Interfaces:**
- Consumes: `rbac.models.Role`, `rbac.permissions` (PermViewSetMixin), `accounts.User`.
- Produces:
  - `employees.models.Employee(user OneToOne→User related_name="employee", first_name, last_name, phone, position, role FK→Role null, is_active)`, property `name`.
  - `/api/employees/` ViewSet; create is transactional (User+Employee); password write_only.

- [ ] **Step 1: Scaffold app**

Run:
```bash
python manage.py startapp employees
mkdir -p employees/tests && touch employees/tests/__init__.py && rm -f employees/tests.py
```

- [ ] **Step 2: Write the failing test**

`backend/employees/tests/test_employees.py`:
```python
import pytest
from django.contrib.auth import get_user_model
from rbac.models import Role
from employees.models import Employee

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def admin_client(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.is_staff = True; u.save()
    return auth_client(u)


def test_admin_creates_employee_with_account(admin_client):
    role = Role.objects.create(name="Оператор")
    resp = admin_client.post("/api/employees/", {
        "username": "ivan", "password": "pass12345",
        "first_name": "Иван", "last_name": "Петров", "phone": "+7700",
        "position": "Кладовщик", "role": role.id,
    }, format="json")
    assert resp.status_code == 201
    u = User.objects.get(username="ivan")
    assert u.check_password("pass12345")
    assert Employee.objects.get(user=u).role_id == role.id
    assert "password" not in resp.data


def test_password_required_on_create(admin_client):
    role = Role.objects.create(name="R")
    resp = admin_client.post("/api/employees/", {
        "username": "x", "first_name": "A", "last_name": "B",
        "phone": "y", "role": role.id,
    }, format="json")
    assert resp.status_code == 400


def test_non_admin_without_perm_denied(auth_client, make_user):
    u = make_user(username="plain")
    role = Role.objects.create(name="R")
    resp = auth_client(u).post("/api/employees/", {
        "username": "z", "password": "pass12345", "first_name": "A",
        "last_name": "B", "phone": "y", "role": role.id,
    }, format="json")
    assert resp.status_code == 403
```

- [ ] **Step 3: Implement model**

`backend/employees/models.py`:
```python
from django.conf import settings
from django.db import models


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee"
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=50, blank=True, default="")
    position = models.CharField(max_length=100, blank=True, default="")
    role = models.ForeignKey(
        "rbac.Role", null=True, blank=True, on_delete=models.PROTECT, related_name="employees"
    )
    is_active = models.BooleanField(default=True)

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.name
```

`backend/employees/admin.py`:
```python
from django.contrib import admin
from .models import Employee

admin.site.register(Employee)
```

- [ ] **Step 4: Implement serializer (transactional create, write-only password)**

`backend/employees/serializers.py`:
```python
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from .models import Employee

User = get_user_model()


class EmployeeSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username")
    password = serializers.CharField(write_only=True, required=True, min_length=6)
    role_name = serializers.CharField(source="role.name", read_only=True)
    name = serializers.CharField(read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "username", "password", "first_name", "last_name",
                  "phone", "position", "role", "role_name", "name", "is_active"]

    @transaction.atomic
    def create(self, validated_data):
        user_data = validated_data.pop("user")
        password = validated_data.pop("password")
        username = user_data["username"]
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError(
                {"detail": "Пользователь с таким логином уже существует", "code": "username_taken"})
        user = User.objects.create_user(username=username, password=password)
        return Employee.objects.create(user=user, **validated_data)
```

- [ ] **Step 5: Implement view + urls**

`backend/employees/views.py`:
```python
from rest_framework import viewsets
from rbac.permissions import PermViewSetMixin
from .models import Employee
from .serializers import EmployeeSerializer


class EmployeeViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Employee.objects.select_related("user", "role")
    serializer_class = EmployeeSerializer
    required_perms = {
        "list": "employees.view", "retrieve": "employees.view",
        "create": "employees.manage", "update": "employees.manage",
        "partial_update": "employees.manage", "destroy": "employees.manage",
    }
```

`backend/employees/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import EmployeeViewSet

router = DefaultRouter()
router.register("employees", EmployeeViewSet)
urlpatterns = router.urls
```

- [ ] **Step 6: Register + migrate + test**

Add `"employees"` to `INSTALLED_APPS`; add `path("api/", include("employees.urls"))` to `config/urls.py`.
Run:
```bash
python manage.py makemigrations employees && python manage.py migrate
pytest employees/ accounts/tests/test_perm_codes.py -v
```
Expected: employee tests pass; Task 2's perm_codes test now passes too.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(employees): Employee profile + transactional account-creating API"
```

---

### Task 5: Roles & Permissions API + seed migration

**Files:**
- Create: `backend/rbac/serializers.py`, `backend/rbac/views.py`, `backend/rbac/urls.py`, `backend/rbac/migrations/000X_seed.py`, `backend/rbac/tests/test_roles_api.py`
- Modify: `backend/config/urls.py`

**Interfaces:**
- Consumes: `Permission`, `Role`, `PermViewSetMixin`, `PERMISSIONS`/`PRESETS`.
- Produces:
  - `GET /api/permissions/` (read-only list).
  - `/api/roles/` CRUD; `permission_codes` writable list of codes; `is_system` roles cannot be destroyed; role with employees cannot be destroyed.

- [ ] **Step 1: Write the failing test**

`backend/rbac/tests/test_roles_api.py`:
```python
import pytest
from rbac.models import Role, Permission

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.save()
    return auth_client(u)


def test_permissions_list(admin_client):
    resp = admin_client.get("/api/permissions/")
    assert resp.status_code == 200
    assert any(p["code"] == "orders.create" for p in resp.data)


def test_create_role_with_codes(admin_client):
    resp = admin_client.post("/api/roles/", {
        "name": "Кладовщик", "permission_codes": ["warehouse.view", "warehouse.adjust"],
    }, format="json")
    assert resp.status_code == 201
    role = Role.objects.get(name="Кладовщик")
    assert set(role.permissions.values_list("code", flat=True)) == {"warehouse.view", "warehouse.adjust"}


def test_system_role_cannot_be_deleted(admin_client):
    r = Role.objects.create(name="Начальник", is_system=True)
    resp = admin_client.delete(f"/api/roles/{r.id}/")
    assert resp.status_code == 400


def test_role_with_employees_cannot_be_deleted(admin_client, make_user):
    from employees.models import Employee
    r = Role.objects.create(name="Темп")
    u = make_user(username="emp1")
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=r)
    resp = admin_client.delete(f"/api/roles/{r.id}/")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest rbac/tests/test_roles_api.py -v`
Expected: FAIL (no urls/serializers).

- [ ] **Step 3: Implement serializers**

`backend/rbac/serializers.py`:
```python
from rest_framework import serializers
from .models import Permission, Role


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "section", "action", "label"]


class RoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.ListField(
        child=serializers.CharField(), write_only=True, required=False)
    permissions = PermissionSerializer(many=True, read_only=True)
    employee_count = serializers.IntegerField(source="employees.count", read_only=True)

    class Meta:
        model = Role
        fields = ["id", "name", "description", "is_system",
                  "permissions", "permission_codes", "employee_count"]
        read_only_fields = ["is_system"]

    def _apply_codes(self, role, codes):
        perms = Permission.objects.filter(code__in=codes)
        role.permissions.set(perms)

    def create(self, validated_data):
        codes = validated_data.pop("permission_codes", [])
        role = Role.objects.create(**validated_data)
        self._apply_codes(role, codes)
        return role

    def update(self, instance, validated_data):
        codes = validated_data.pop("permission_codes", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        if codes is not None:
            self._apply_codes(instance, codes)
        return instance
```

- [ ] **Step 4: Implement views + urls**

`backend/rbac/views.py`:
```python
from rest_framework import viewsets, mixins
from rest_framework.exceptions import ValidationError
from .models import Permission, Role
from .serializers import PermissionSerializer, RoleSerializer
from .permissions import PermViewSetMixin


class PermissionViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    required_perms = {"list": "employees.view"}


class RoleViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Role.objects.prefetch_related("permissions")
    serializer_class = RoleSerializer
    required_perms = {
        "list": "employees.view", "retrieve": "employees.view",
        "create": "employees.manage", "update": "employees.manage",
        "partial_update": "employees.manage", "destroy": "employees.manage",
    }

    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError({"detail": "Системную роль нельзя удалить", "code": "system_role"})
        if instance.employees.exists():
            raise ValidationError({"detail": "На роль назначены сотрудники — удаление запрещено", "code": "role_in_use"})
        instance.delete()
```

`backend/rbac/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import PermissionViewSet, RoleViewSet

router = DefaultRouter()
router.register("permissions", PermissionViewSet, basename="permissions")
router.register("roles", RoleViewSet)
urlpatterns = router.urls
```
Add `path("api/", include("rbac.urls"))` to `config/urls.py`.

- [ ] **Step 5: Seed migration (permissions + presets)**

`backend/rbac/migrations/000X_seed.py` (use the real next number after the models migration):
```python
from django.db import migrations


def seed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    from rbac.perms import PERMISSIONS, PRESETS
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)
    for name, codes in PRESETS.items():
        role, _ = Role.objects.get_or_create(name=name, defaults={"is_system": True})
        role.is_system = True
        role.save()
        role.permissions.set(Permission.objects.filter(code__in=codes))


def unseed(apps, schema_editor):
    apps.get_model("rbac", "Role").objects.filter(is_system=True).delete()
    apps.get_model("rbac", "Permission").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
```

- [ ] **Step 6: Migrate + test**

Run:
```bash
python manage.py migrate
pytest rbac/ -v
```
Expected: all rbac tests pass; presets + permissions seeded.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(rbac): roles/permissions API + seed presets migration"
```

---

### Task 6: Switch all existing views to required_perms + me.permissions

**Files:**
- Modify: `backend/catalog/views.py`, `backend/clients/views.py`, `backend/orders/views.py`, `backend/warehouse/views.py`, `backend/shipments/views.py`, `backend/eventlog/views.py`
- Modify: `backend/shipments/services.py` (debt rule), `backend/accounts/serializers.py` (MeSerializer), `backend/accounts/permissions.py` (drop old classes)
- Delete usages of `IsManager/IsAccountant/IsOperator/IsBoss/IsOperatorOrBoss`
- Modify/replace tests that referenced old roles
- Modify: `backend/accounts/migrations/0002_seed_groups.py` is now obsolete — leave migration (already applied) but stop relying on groups; conftest role fixtures updated.

**Interfaces:**
- Consumes: `PermViewSetMixin`, `HasPerm`, `User.has_perm_code`.
- Produces: every staff endpoint gated by a permission code; `me.permissions` list.

- [ ] **Step 1: Update conftest fixtures to roles**

Replace group-based fixtures in `backend/conftest.py`. Add a helper that builds a user with a role having given codes:
```python
@pytest.fixture
def user_with_perms(make_user):
    from rbac.models import Permission, Role
    from employees.models import Employee
    def _make(username="emp", codes=()):
        user = make_user(username=username)
        role = Role.objects.create(name=f"role-{username}")
        for c in codes:
            p, _ = Permission.objects.get_or_create(
                code=c, defaults={"section": c.split(".")[0], "action": c.split(".")[1], "label": c})
            role.permissions.add(p)
        Employee.objects.create(user=user, first_name="A", last_name="B", phone="x", role=role)
        return user
    return _make
```
Keep `manager`/`accountant`/`operator`/`boss` fixtures but reimplement them on top of `user_with_perms` with the preset codes (so existing tests keep working):
```python
@pytest.fixture
def manager(user_with_perms):
    return user_with_perms("manager", codes=[
        "catalog.view","catalog.create","clients.view","clients.create",
        "orders.view","orders.create","orders.confirm"])
@pytest.fixture
def accountant(user_with_perms):
    return user_with_perms("accountant", codes=["payments.view","payments.create","orders.view"])
@pytest.fixture
def operator(user_with_perms):
    return user_with_perms("operator", codes=[
        "shipping.view","shipping.arrive","shipping.load","shipping.ship",
        "orders.view","warehouse.view"])
@pytest.fixture
def boss(user_with_perms):
    return user_with_perms("boss", codes=[
        "shipping.view","shipping.arrive","shipping.load","shipping.ship",
        "shipping.debt_override","orders.view","warehouse.view","warehouse.adjust",
        "catalog.view","clients.view"])
```

- [ ] **Step 2: Convert catalog views**

`backend/catalog/views.py` — replace `_StaffReadManagerWrite` with mixin + required_perms:
```python
from rest_framework import viewsets
from rbac.permissions import PermViewSetMixin
from .models import Grade, Packaging, Product
from .serializers import GradeSerializer, PackagingSerializer, ProductSerializer

_PERMS = {
    "list": "catalog.view", "retrieve": "catalog.view",
    "create": "catalog.create", "update": "catalog.edit",
    "partial_update": "catalog.edit", "destroy": "catalog.delete",
}

class GradeViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Grade.objects.all()
    serializer_class = GradeSerializer
    required_perms = _PERMS

class PackagingViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Packaging.objects.all()
    serializer_class = PackagingSerializer
    required_perms = _PERMS

class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Product.objects.select_related("grade", "packaging").all()
    serializer_class = ProductSerializer
    required_perms = _PERMS
```

- [ ] **Step 3: Convert clients views**

`backend/clients/views.py`:
```python
from rest_framework import viewsets
from rbac.permissions import PermViewSetMixin
from .models import Client
from .serializers import ClientSerializer


class ClientViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    required_perms = {
        "list": "clients.view", "retrieve": "clients.view",
        "create": "clients.create", "update": "clients.edit",
        "partial_update": "clients.edit", "destroy": "clients.delete",
    }
```

- [ ] **Step 4: Convert orders views**

In `backend/orders/views.py` replace `get_permissions` and action permission_classes:
```python
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rbac.permissions import PermViewSetMixin, HasPerm
from .models import Order
from .serializers import OrderSerializer, PaymentSerializer
from .services import add_payment, confirm_order


class OrderViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Order.objects.select_related("client").prefetch_related("items__product")
    serializer_class = OrderSerializer
    required_perms = {
        "list": "orders.view", "retrieve": "orders.view",
        "create": "orders.create", "update": "orders.edit",
        "partial_update": "orders.edit", "destroy": "orders.edit",
        "payments": "payments.create", "confirm": "orders.confirm",
    }

    @action(detail=True, methods=["post"], url_path="payments")
    def payments(self, request, pk=None):
        order = self.get_object()
        payment = add_payment(order, request.data.get("amount"), request.user)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        order = confirm_order(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)
```
(Because `PermViewSetMixin.get_permissions` reads `required_perms[self.action]`, the custom actions resolve correctly without per-action `permission_classes`.)

- [ ] **Step 5: Convert warehouse views**

`backend/warehouse/views.py` — set required_perms on each viewset:
```python
from rest_framework import viewsets, mixins
from rest_framework.response import Response
from rbac.permissions import PermViewSetMixin
from .models import StockItem, StockMovement
from .serializers import StockItemSerializer, StockReceiptSerializer, StockMovementSerializer
from .services import receive_stock, adjust_stock
from catalog.models import Product


class StockViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = StockItem.objects.select_related("product", "product__grade", "product__packaging")
    serializer_class = StockItemSerializer
    required_perms = {"list": "warehouse.view"}


class StockReceiptViewSet(PermViewSetMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = StockReceiptSerializer
    required_perms = {"create": "warehouse.adjust"}

    def create(self, request, *args, **kwargs):
        product = Product.objects.get(pk=request.data["product"])
        receipt = receive_stock(product, int(request.data["bags"]), request.user)
        return Response(StockReceiptSerializer(receipt).data, status=201)


class StockAdjustViewSet(PermViewSetMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = StockItemSerializer
    required_perms = {"create": "warehouse.adjust"}

    def create(self, request, *args, **kwargs):
        product = Product.objects.get(pk=request.data["product"])
        item = adjust_stock(product, int(request.data["delta"]), request.user,
                            note=request.data.get("note", ""))
        return Response(StockItemSerializer(item).data, status=201)


class StockMovementViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = StockMovementSerializer
    required_perms = {"list": "warehouse.view"}

    def get_queryset(self):
        qs = StockMovement.objects.select_related("product", "created_by")
        product = self.request.query_params.get("product")
        return qs.filter(product_id=product) if product else qs
```

- [ ] **Step 6: Convert shipments views + debt rule + eventlog**

`backend/shipments/views.py` — these are plain `APIView`s (not viewsets), so they
can't use the mixin. DRF instantiates each entry in `permission_classes` with no
args, so give each its own named `HasPerm` subclass with a zero-arg `__init__`:
```python
from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.response import Response
from rbac.permissions import HasPerm
from orders.models import Order
from .services import record_arrival, record_loading, record_shipment
from .serializers import ShipmentSerializer


class _CanArrive(HasPerm):
    def __init__(self): super().__init__("shipping.arrive")
class _CanLoad(HasPerm):
    def __init__(self): super().__init__("shipping.load")
class _CanShip(HasPerm):
    def __init__(self): super().__init__("shipping.ship")


class _Base(APIView):
    def get_order(self, pk):
        return Order.objects.select_related("shipment").prefetch_related("items__product").get(pk=pk)


class ArriveView(_Base):
    permission_classes = [_CanArrive]
    def post(self, request, pk):
        order = self.get_order(pk)
        s = record_arrival(order, request.data.get("truck_number", ""),
                           Decimal(str(request.data["weigh_in_kg"])), request.user,
                           debt_override=bool(request.data.get("debt_override", False)))
        return Response(ShipmentSerializer(s).data)


class LoadView(_Base):
    permission_classes = [_CanLoad]
    def post(self, request, pk):
        s = record_loading(self.get_order(pk), int(request.data["bags"]), request.user)
        return Response(ShipmentSerializer(s).data)


class ShipView(_Base):
    permission_classes = [_CanShip]
    def post(self, request, pk):
        s = record_shipment(self.get_order(pk), Decimal(str(request.data["weigh_out_kg"])), request.user)
        return Response(ShipmentSerializer(s).data)
```

In `backend/shipments/services.py` `record_arrival`, replace the debt check:
```python
    if not order.is_fully_paid:
        may_override = user.has_perm_code("shipping.debt_override")
        if not (debt_override and may_override):
            raise ValidationError({"detail": "Заказ не оплачен — въезд запрещён", "code": "payment_required"})
```

`backend/eventlog/views.py` — set `required_perms` via mixin:
```python
from rest_framework import viewsets, mixins
from rbac.permissions import PermViewSetMixin
from .models import EventLog
from .serializers import EventLogSerializer


class EventLogViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    required_perms = {"list": "events.view"}

    def get_queryset(self):
        qs = EventLog.objects.all()
        p = self.request.query_params
        if p.get("order"): qs = qs.filter(order_id=p["order"])
        if p.get("event_type"): qs = qs.filter(event_type=p["event_type"])
        if p.get("search"): qs = qs.filter(message__icontains=p["search"])
        if p.get("date_from"): qs = qs.filter(created_at__date__gte=p["date_from"])
        if p.get("date_to"): qs = qs.filter(created_at__date__lte=p["date_to"])
        return qs
```

- [ ] **Step 7: MeSerializer + drop old permission classes**

`backend/accounts/serializers.py` MeSerializer — replace `roles` with `permissions`:
```python
from rest_framework import serializers
from .models import User


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "is_client", "is_superuser",
                  "permissions", "role_name", "client_id"]

    def get_permissions(self, obj):
        return sorted(obj.perm_codes)

    def get_role_name(self, obj):
        emp = getattr(obj, "employee", None)
        return emp.role.name if emp and emp.role else None

    def get_client_id(self, obj):
        profile = getattr(obj, "client_profile", None)
        return profile.id if profile else None
```

`backend/accounts/permissions.py` — keep only `IsStaff`, `IsClientUser`; delete `IsManager/IsAccountant/IsOperator/IsBoss/IsOperatorOrBoss`. `IsStaff` no longer references `is_client` group logic — keep as-is (it checks `not request.user.is_client`).

- [ ] **Step 8: Run the full backend suite**

Run: `pytest -q`
Expected: ALL pass. Investigate any failure (most likely a test still posting with old role expectations — update it to use `user_with_perms`/preset fixtures). The `me` test (`accounts/tests/test_me.py`) must be updated to assert `permissions` instead of `roles`:
```python
def test_me_returns_permissions(auth_client, make_user):
    from rbac.models import Permission, Role
    from employees.models import Employee
    u = make_user(username="m")
    role = Role.objects.create(name="R")
    p = Permission.objects.create(code="orders.view", section="orders", action="view", label="x")
    role.permissions.add(p)
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    resp = auth_client(u).get("/api/auth/me/")
    assert resp.status_code == 200
    assert "orders.view" in resp.data["permissions"]
```

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "refactor: gate all endpoints by permission codes; me.permissions; drop role classes"
```

---

### Task 7: Frontend — can() helper, me.permissions, nav gating

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/components/layout/sidebar.tsx`, `frontend/src/components/layout/topbar.tsx`
- Create: `frontend/src/lib/can.ts`

**Interfaces:**
- Consumes: `me.permissions: string[]` from `/auth/me/`.
- Produces: `can(me, code) -> boolean`; nav items gated by `<section>.view`.

- [ ] **Step 1: Update Me type + add can()**

In `frontend/src/lib/types.ts` replace `roles` on `Me`:
```typescript
export interface Me {
  id: number;
  username: string;
  is_client: boolean;
  is_superuser: boolean;
  permissions: string[];
  role_name: string | null;
  client_id: number | null;
}
```
`frontend/src/lib/can.ts`:
```typescript
import type { Me } from "@/lib/types";

export function can(me: Me | null, code: string): boolean {
  if (!me) return false;
  if (me.is_superuser) return true;
  return me.permissions.includes(code);
}
```

- [ ] **Step 2: Gate sidebar nav by permission**

`frontend/src/components/layout/sidebar.tsx` — replace `roles` filtering with permission codes. Each staff nav item gets a `perm` (its `<section>.view`); the Управление group gets `employees.view`:
```typescript
// add perm to NavItem and children where relevant; filter:
import { can } from "@/lib/can";
// ...
const STAFF_NAV: NavItem[] = [
  { href: "/dashboard", label: "Дашборд", icon: LayoutDashboard },
  { label: "Номенклатура", icon: Package, perm: "catalog.view", children: [
      { href: "/catalog/grades", label: "Сорта" },
      { href: "/catalog/packagings", label: "Фасовки" },
      { href: "/catalog/products", label: "Товары" }]},
  { href: "/warehouse", label: "Склад", icon: Boxes, perm: "warehouse.view" },
  { href: "/orders", label: "Заказы", icon: ClipboardList, perm: "orders.view" },
  { href: "/clients", label: "Клиенты", icon: Users, perm: "clients.view" },
  { href: "/shipping", label: "Пост отгрузки", icon: Truck, perm: "shipping.view" },
  { href: "/events", label: "Журнал", icon: ScrollText, perm: "events.view" },
  { href: "/reports", label: "Отчёты", icon: BarChart3, perm: "reports.view" },
  { label: "Управление", icon: Settings, perm: "employees.view", children: [
      { href: "/management/employees", label: "Сотрудники" },
      { href: "/management/roles", label: "Роли" }]},
];
// NavItem interface: add `perm?: string`
// filter: const nav = me.is_client ? PORTAL_NAV : STAFF_NAV.filter(i => !i.perm || can(me, i.perm));
```
Add `Settings` to the lucide import. Dashboard (no perm) always visible to staff.

- [ ] **Step 3: Topbar role label**

In `frontend/src/components/layout/topbar.tsx` replace role text source: use `me.role_name` (fallback "Сотрудник"); superuser → "Администратор". Remove the old `ROLE_LABELS.map` over `me.roles`:
```typescript
const roleText = me.is_client ? "Клиент"
  : me.is_superuser ? "Администратор"
  : me.role_name || "Сотрудник";
```

- [ ] **Step 4: Typecheck + build**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: tsc exit 0; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(frontend): can() helper, permission-gated nav, role label"
```

---

### Task 8: Frontend — Employees & Roles management screens

**Files:**
- Create: `frontend/src/app/management/employees/page.tsx`, `frontend/src/app/management/roles/page.tsx`
- Modify: `frontend/src/lib/types.ts` (Role, Permission, Employee types)

**Interfaces:**
- Consumes: `/api/employees/`, `/api/roles/`, `/api/permissions/`, `can()`, `Modal`, `useApi`.
- Produces: two screens — employees table + create modal; roles list + permission-matrix editor.

- [ ] **Step 1: Add types**

In `frontend/src/lib/types.ts`:
```typescript
export interface Permission { id: number; code: string; section: string; action: string; label: string; }
export interface Role {
  id: number; name: string; description: string; is_system: boolean;
  permissions: Permission[]; employee_count: number;
}
export interface Employee {
  id: number; username: string; first_name: string; last_name: string;
  phone: string; position: string; role: number | null; role_name: string | null;
  name: string; is_active: boolean;
}
```

- [ ] **Step 2: Employees page**

`frontend/src/app/management/employees/page.tsx`:
```tsx
"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { Plus } from "lucide-react";
import type { Employee, Role } from "@/lib/types";

export default function EmployeesPage() {
  const { data: employees, reload } = useApi<Employee[]>("/employees/");
  const { data: roles } = useApi<Role[]>("/roles/");
  const { me } = useAuth();
  const canManage = can(me, "employees.manage");
  const empty = { username: "", password: "", first_name: "", last_name: "", phone: "", position: "", role: "" };
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/employees/", { ...form, role: form.role ? Number(form.role) : null });
      setForm(empty); setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Сотрудники">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{employees?.length ?? 0} сотрудников</p>
        {canManage && <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить сотрудника</Button>}
      </div>
      <Card><CardContent className="pt-6">
        <Table>
          <THead><TR><TH>Имя</TH><TH>Логин</TH><TH>Должность</TH><TH>Роль</TH><TH>Статус</TH></TR></THead>
          <TBody>
            {(employees ?? []).map((e) => (
              <TR key={e.id}>
                <TD className="font-medium">{e.name}</TD>
                <TD>{e.username}</TD>
                <TD>{e.position || "—"}</TD>
                <TD>{e.role_name || "—"}</TD>
                <TD><Badge tone={e.is_active ? "success" : "muted"}>{e.is_active ? "Активен" : "Отключён"}</Badge></TD>
              </TR>
            ))}
            {(employees ?? []).length === 0 && (
              <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">Сотрудников пока нет.</TD></TR>)}
          </TBody>
        </Table>
      </CardContent></Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый сотрудник" className="max-w-xl">
        <form onSubmit={submit} className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
          <div className="grid gap-2"><Label>Имя</Label>
            <Input value={form.first_name} required onChange={(e) => setForm({ ...form, first_name: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Фамилия</Label>
            <Input value={form.last_name} required onChange={(e) => setForm({ ...form, last_name: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Логин</Label>
            <Input value={form.username} required onChange={(e) => setForm({ ...form, username: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Пароль</Label>
            <Input type="password" value={form.password} required minLength={6}
              onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Телефон</Label>
            <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Должность</Label>
            <Input value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} /></div>
          <div className="grid gap-2 sm:col-span-2"><Label>Роль</Label>
            <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="">Без роли</option>
              {(roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </Select></div>
          {error && <p className="text-sm text-[var(--destructive)] sm:col-span-2">{error}</p>}
          <div className="flex justify-end gap-2 border-t pt-5 sm:col-span-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={busy}>{busy ? "Сохранение…" : "Создать"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
```

- [ ] **Step 3: Roles page (permission matrix)**

`frontend/src/app/management/roles/page.tsx`:
```tsx
"use client";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { Plus, Trash2 } from "lucide-react";
import type { Role, Permission } from "@/lib/types";

const SECTION_LABELS: Record<string, string> = {
  catalog: "Номенклатура", clients: "Клиенты", warehouse: "Склад", orders: "Заказы",
  payments: "Оплаты", shipping: "Пост отгрузки", events: "Журнал", reports: "Отчёты",
  employees: "Сотрудники",
};

export default function RolesPage() {
  const { data: roles, reload } = useApi<Role[]>("/roles/");
  const { data: perms } = useApi<Permission[]>("/permissions/");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [name, setName] = useState("");
  const [codes, setCodes] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const sections = Array.from(new Set((perms ?? []).map((p) => p.section)));

  function openNew() {
    setEditing(null); setName(""); setCodes(new Set()); setError(""); setOpen(true);
  }
  function openEdit(r: Role) {
    setEditing(r); setName(r.name);
    setCodes(new Set(r.permissions.map((p) => p.code))); setError(""); setOpen(true);
  }
  function toggle(code: string) {
    const next = new Set(codes);
    next.has(code) ? next.delete(code) : next.add(code);
    setCodes(next);
  }
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    const body = { name, permission_codes: Array.from(codes) };
    try {
      if (editing) await api.patch(`/roles/${editing.id}/`, body);
      else await api.post("/roles/", body);
      setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }
  async function remove(r: Role) {
    setError("");
    try { await api.delete(`/roles/${r.id}/`); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title="Роли">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{roles?.length ?? 0} ролей</p>
        <Button size="sm" onClick={openNew}><Plus className="size-4" /> Новая роль</Button>
      </div>
      {error && <p className="mb-3 text-sm text-[var(--destructive)]">{error}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(roles ?? []).map((r) => (
          <Card key={r.id}>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">{r.name}
                {r.is_system && <Badge tone="muted">системная</Badge>}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-xs text-[var(--muted-foreground)]">
                Прав: {r.permissions.length} · Сотрудников: {r.employee_count}</p>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(r)}>Изменить</Button>
                {!r.is_system && r.employee_count === 0 && (
                  <Button size="sm" variant="ghost" onClick={() => remove(r)}>
                    <Trash2 className="size-4" /></Button>)}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Modal open={open} onClose={() => setOpen(false)}
        title={editing ? `Роль: ${editing.name}` : "Новая роль"} className="max-w-2xl">
        <form onSubmit={save} className="flex flex-col gap-5">
          <div className="grid gap-2"><Label>Название роли</Label>
            <Input value={name} required onChange={(e) => setName(e.target.value)}
              disabled={editing?.is_system} /></div>
          <div className="flex flex-col gap-4">
            <Label>Права доступа</Label>
            {sections.map((sec) => (
              <div key={sec} className="rounded-lg border p-3">
                <div className="mb-2 text-sm font-semibold">{SECTION_LABELS[sec] ?? sec}</div>
                <div className="flex flex-wrap gap-2">
                  {(perms ?? []).filter((p) => p.section === sec).map((p) => (
                    <button key={p.code} type="button" onClick={() => toggle(p.code)}
                      className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                        codes.has(p.code)
                          ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                          : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"}`}>
                      {p.action}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="flex justify-end gap-2 border-t pt-5">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
```
(`useEffect` import kept for parity; remove if unused to satisfy lint.)

- [ ] **Step 4: Typecheck + build**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: tsc exit 0; build succeeds; routes `/management/employees`, `/management/roles` present.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(frontend): employees + roles management screens"
```

---

### Task 9: Full-stack verification in Docker

**Files:** none (verification only).

- [ ] **Step 1: Rebuild and start**

Run:
```bash
cd /Users/dimash/PycharmProjects/asyl-ltd
docker compose build backend frontend && docker compose up -d
sleep 14
```

- [ ] **Step 2: Verify me.permissions for superuser**

Run (python one-liner logging in as admin from .env): assert `/api/auth/me/` returns a non-empty `permissions` list containing `employees.manage`.
Expected: superuser gets all codes.

- [ ] **Step 3: Verify role + employee creation flow**

Via API as admin: `POST /api/roles/` with codes → 201; `POST /api/employees/` (username+password+role) → 201, password not echoed; login as the new employee → `/api/auth/me/` returns that role's permission set.
Expected: all succeed; new employee's permissions match the role.

- [ ] **Step 4: Verify gating**

As the new employee (e.g. operator preset), `POST /api/grades/` → 403 (no catalog.create); `GET /api/orders/` → 200 (has orders.view). 
Expected: gating matches role.

- [ ] **Step 5: Pages serve**

Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/management/employees` and `/management/roles` → 200.

- [ ] **Step 6: Final commit**

```bash
git add -A && git commit -m "chore: verify RBAC end-to-end" --allow-empty
```

---

## Self-Review Notes (coverage map)

- §1 models (Permission/Role/Employee, has_perm_code) → Tasks 1, 2, 4.
- §2 catalog single source of truth + presets → Task 1 (perms.py), Task 5 (seed).
- §3 HasPerm + required_perms everywhere; debt_override perm → Tasks 3, 6.
- §4 API (/permissions, /roles, /employees, me.permissions) → Tasks 4, 5, 6.
- §5 frontend can(), nav gating, employees+roles screens → Tasks 7, 8.
- §6 transactional create, role-with-employees undeletable, system presets undeletable, password hashing, error shape, TDD → Tasks 4, 5, 6.
- Migration of old groups/role props → Task 2 (drop props), Task 6 (fixtures/me), seed presets Task 5.
