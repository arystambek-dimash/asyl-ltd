# Asyl-LTD CRM Backend (DRF API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Django REST Framework backend for the Asyl-LTD flour-mill CRM: clients, dynamic product catalog, warehouse stock in bags, orders with a strict payment-gated shipment lifecycle, an immutable event log, role-based access, and a client self-service portal — all manual-entry now but field-ready for future camera/scale hardware.

**Architecture:** A single Django project (`config`) with focused apps split by responsibility: `accounts` (users, roles, JWT), `catalog` (grades, packagings, products), `clients`, `warehouse` (stock, receipts), `orders` (orders, items, payments, lifecycle), `shipments` (weigh-in/weigh-out, stock deduction), `eventlog` (append-only log), and `portal` (client-scoped endpoints). Business rules live in service functions wrapped in DB transactions, called by thin DRF views. Tests are written first (TDD).

**Tech Stack:** Python 3.12, Django 5, Django REST Framework, djangorestframework-simplejwt, PostgreSQL, pytest + pytest-django, factory_boy.

## Global Constraints

- Python 3.12, Django 5.x, Django REST Framework 3.15+.
- Database: PostgreSQL (no SQLite in committed settings; tests may use a Postgres test DB).
- Auth: JWT via `djangorestframework-simplejwt` (access + refresh).
- Warehouse stock is tracked **in bags** (integer count) per Product; weight is derived.
- Order statuses (exact strings): `draft`, `confirmed`, `paid`, `arrived`, `loading`, `shipped`, `cancelled`.
- `EventLog` is append-only: no update, no delete, ever.
- Transition `confirmed/paid → arrived` requires the order to be fully paid, UNLESS a user with role **Начальник (boss)** sets a debt-override flag; the override is recorded in `EventLog`.
- Stock is deducted at `shipped`, inside a single DB transaction, and never below zero.
- All server-side validation; never trust the client for transitions.
- Error responses use shape `{"detail": "...", "code": "..."}`.
- Roles (Django groups): `manager`, `accountant`, `operator`, `boss`. Plus superuser (admin) and an external `client` user type.
- Money as `Decimal` (`max_digits=12, decimal_places=2`); weights as `Decimal` (`max_digits=10, decimal_places=2`, kilograms).
- Russian-language user-facing error messages.

---

## File Structure

```
config/                 # Django project: settings, urls, wsgi
  settings.py
  urls.py
accounts/               # User model, roles, JWT wiring
  models.py             # User (custom), role helpers
  serializers.py        # login/refresh handled by simplejwt
  permissions.py        # role-based DRF permissions
  urls.py
catalog/                # Grade, Packaging, Product
  models.py
  serializers.py
  views.py
  urls.py
clients/                # Client
  models.py
  serializers.py
  views.py
  urls.py
warehouse/              # StockItem, StockReceipt + receipt service
  models.py
  services.py           # receive_stock()
  serializers.py
  views.py
  urls.py
orders/                 # Order, OrderItem, Payment + lifecycle services
  models.py
  services.py           # add_payment(), confirm/arrive (transitions live here)
  serializers.py
  views.py
  urls.py
shipments/              # Shipment + load/ship services
  models.py
  services.py           # record_arrival(), record_loading(), record_shipment()
  serializers.py
  views.py
  urls.py
eventlog/               # EventLog (append-only)
  models.py
  services.py           # log_event()
  serializers.py
  views.py
  urls.py
portal/                 # client-scoped catalog + orders
  serializers.py
  views.py
  urls.py
conftest.py             # pytest fixtures (users per role, api clients)
pytest.ini
requirements.txt
```

Business logic lives in each app's `services.py` (transaction-wrapped, framework-agnostic) so it is unit-testable without HTTP. DRF views stay thin.

---

### Task 1: Project scaffold, settings, pytest, JWT auth wiring

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `conftest.py`
- Create: `config/settings.py`, `config/urls.py`, `config/wsgi.py`, `manage.py`
- Create: `accounts/models.py`, `accounts/__init__.py`, `accounts/apps.py`
- Test: `accounts/tests/test_auth.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - Custom user model `accounts.User` (AbstractUser subclass) with helper props `is_manager`, `is_accountant`, `is_operator`, `is_boss` (each returns bool based on group membership), and `is_client` (BooleanField, default `False`).
  - JWT endpoints: `POST /api/auth/login/` (returns `{access, refresh}`), `POST /api/auth/refresh/`.
  - pytest fixtures available project-wide (defined in Task 2's conftest expansion, but base `api_client` fixture defined here).

- [ ] **Step 1: Create requirements.txt**

```
Django>=5.0,<6.0
djangorestframework>=3.15
djangorestframework-simplejwt>=5.3
psycopg[binary]>=3.1
pytest>=8.0
pytest-django>=4.8
factory_boy>=3.3
```

- [ ] **Step 2: Install deps and scaffold the project**

Run:
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
django-admin startproject config .
python manage.py startapp accounts
```
Expected: `manage.py` and `config/` exist; `accounts/` app created.

- [ ] **Step 3: Configure settings.py**

In `config/settings.py` set:
```python
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth",
    "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "accounts",
]
AUTH_USER_MODEL = "accounts.User"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "asyl"),
        "USER": os.environ.get("DB_USER", "asyl"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "asyl"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
}
LANGUAGE_CODE = "ru"
```
Add `import os` at top.

- [ ] **Step 4: Add the custom User model**

`accounts/models.py`:
```python
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_client = models.BooleanField(default=False)

    def _in_group(self, name: str) -> bool:
        return self.groups.filter(name=name).exists()

    @property
    def is_manager(self) -> bool:
        return self._in_group("manager")

    @property
    def is_accountant(self) -> bool:
        return self._in_group("accountant")

    @property
    def is_operator(self) -> bool:
        return self._in_group("operator")

    @property
    def is_boss(self) -> bool:
        return self._in_group("boss")
```

- [ ] **Step 5: Add unified exception handler**

`config/exceptions.py`:
```python
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict):
        detail = response.data.get("detail", response.data)
        code = getattr(exc, "default_code", "error")
        response.data = {"detail": detail, "code": code}
    return response
```

- [ ] **Step 6: Wire JWT urls**

`config/urls.py`:
```python
from django.contrib import admin
from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login/", TokenObtainPairView.as_view(), name="login"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="refresh"),
]
```

- [ ] **Step 7: Add pytest config and base conftest**

`pytest.ini`:
```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings
python_files = test_*.py
```
`conftest.py`:
```python
import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db, django_user_model):
    def _make(username="u", password="pass12345", client=False, groups=()):
        from django.contrib.auth.models import Group
        user = django_user_model.objects.create_user(
            username=username, password=password, is_client=client
        )
        for g in groups:
            grp, _ = Group.objects.get_or_create(name=g)
            user.groups.add(grp)
        return user
    return _make
```

- [ ] **Step 8: Write the failing auth test**

`accounts/tests/test_auth.py`:
```python
import pytest

pytestmark = pytest.mark.django_db


def test_login_returns_tokens(api_client, make_user):
    make_user(username="boss", password="pass12345", groups=("boss",))
    resp = api_client.post(
        "/api/auth/login/", {"username": "boss", "password": "pass12345"}
    )
    assert resp.status_code == 200
    assert "access" in resp.data and "refresh" in resp.data


def test_boss_role_property(make_user):
    user = make_user(username="b", groups=("boss",))
    assert user.is_boss is True
    assert user.is_manager is False
```

- [ ] **Step 9: Run migrations and the test**

Run:
```bash
python manage.py makemigrations accounts
python manage.py migrate
pytest accounts/tests/test_auth.py -v
```
Expected: both tests PASS.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: scaffold DRF project with custom User, roles, JWT auth"
```

---

### Task 2: Role-based DRF permissions + shared fixtures

**Files:**
- Create: `accounts/permissions.py`
- Modify: `conftest.py` (add per-role user fixtures + auth helper)
- Test: `accounts/tests/test_permissions.py`

**Interfaces:**
- Consumes: `accounts.User` role props from Task 1.
- Produces:
  - Permission classes: `IsManager`, `IsAccountant`, `IsOperator`, `IsBoss`, `IsStaff` (any non-client authenticated user), `IsClientUser` (the external portal user). Each subclasses `rest_framework.permissions.BasePermission`.
  - Fixtures: `manager`, `accountant`, `operator`, `boss`, `client_user` (User instances); `auth_client(user)` returns an `APIClient` with that user's JWT set.

- [ ] **Step 1: Write failing permission tests**

`accounts/tests/test_permissions.py`:
```python
import pytest
from accounts.permissions import IsBoss, IsStaff, IsClientUser

pytestmark = pytest.mark.django_db


class _Req:
    def __init__(self, user):
        self.user = user


def test_is_boss_allows_boss(boss):
    assert IsBoss().has_permission(_Req(boss), None) is True


def test_is_boss_denies_manager(manager):
    assert IsBoss().has_permission(_Req(manager), None) is False


def test_is_staff_denies_client(client_user):
    assert IsStaff().has_permission(_Req(client_user), None) is False


def test_is_client_user_allows_client(client_user):
    assert IsClientUser().has_permission(_Req(client_user), None) is True
```

- [ ] **Step 2: Add the role fixtures + auth helper to conftest.py**

Append to `conftest.py`:
```python
@pytest.fixture
def manager(make_user):
    return make_user(username="manager", groups=("manager",))


@pytest.fixture
def accountant(make_user):
    return make_user(username="accountant", groups=("accountant",))


@pytest.fixture
def operator(make_user):
    return make_user(username="operator", groups=("operator",))


@pytest.fixture
def boss(make_user):
    return make_user(username="boss", groups=("boss",))


@pytest.fixture
def client_user(make_user):
    return make_user(username="client", client=True)


@pytest.fixture
def auth_client():
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken

    def _auth(user):
        c = APIClient()
        token = RefreshToken.for_user(user).access_token
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return c
    return _auth
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest accounts/tests/test_permissions.py -v`
Expected: FAIL with `ModuleNotFoundError: accounts.permissions`.

- [ ] **Step 4: Implement permissions**

`accounts/permissions.py`:
```python
from rest_framework.permissions import BasePermission


def _auth(request):
    return bool(request.user and request.user.is_authenticated)


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and not request.user.is_client


class IsClientUser(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_client


class IsManager(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_manager


class IsAccountant(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_accountant


class IsOperator(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_operator


class IsBoss(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_boss
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest accounts/tests/test_permissions.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: role-based DRF permissions and per-role test fixtures"
```

---

### Task 3: Catalog — dynamic Grade, Packaging, Product

**Files:**
- Create: `catalog/models.py`, `catalog/serializers.py`, `catalog/views.py`, `catalog/urls.py`, `catalog/admin.py`
- Modify: `config/settings.py` (add `"catalog"`), `config/urls.py` (include catalog urls)
- Test: `catalog/tests/test_catalog_api.py`

**Interfaces:**
- Consumes: `IsStaff`, `IsManager` permissions (Task 2); `auth_client` fixture.
- Produces:
  - `catalog.models.Grade(name, is_active)`, `catalog.models.Packaging(name, weight_kg: Decimal, is_active)`, `catalog.models.Product(grade FK, packaging FK, price: Decimal, is_active)` with `unique_together(grade, packaging)` and property `weight_kg` = `packaging.weight_kg`, and `__str__` = `"{grade.name} {packaging.name}"`.
  - Endpoints under `/api/grades/`, `/api/packagings/`, `/api/products/` (ModelViewSets). Read = any staff; write = manager only.

- [ ] **Step 1: Write failing catalog API tests**

`catalog/tests/test_catalog_api.py`:
```python
import pytest
from catalog.models import Grade, Packaging, Product

pytestmark = pytest.mark.django_db


def _make_product(price="100.00"):
    g = Grade.objects.create(name="Премиум")
    p = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    return Product.objects.create(grade=g, packaging=p, price=price)


def test_manager_creates_grade(auth_client, manager):
    resp = auth_client(manager).post("/api/grades/", {"name": "Премиум"})
    assert resp.status_code == 201
    assert Grade.objects.filter(name="Премиум").exists()


def test_operator_cannot_create_grade(auth_client, operator):
    resp = auth_client(operator).post("/api/grades/", {"name": "X"})
    assert resp.status_code == 403


def test_product_weight_from_packaging(auth_client, manager):
    prod = _make_product()
    assert str(prod) == "Премиум 50 кг"
    assert prod.weight_kg == prod.packaging.weight_kg


def test_staff_can_list_products(auth_client, operator):
    _make_product()
    resp = auth_client(operator).get("/api/products/")
    assert resp.status_code == 200
    assert len(resp.data) == 1
```

- [ ] **Step 2: Implement models**

`catalog/models.py`:
```python
from django.db import models


class Grade(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Packaging(models.Model):
    name = models.CharField(max_length=50, unique=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="products")
    packaging = models.ForeignKey(Packaging, on_delete=models.PROTECT, related_name="products")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("grade", "packaging")

    @property
    def weight_kg(self):
        return self.packaging.weight_kg

    def __str__(self):
        return f"{self.grade.name} {self.packaging.name}"
```

- [ ] **Step 3: Implement serializers, views, urls, admin**

`catalog/serializers.py`:
```python
from rest_framework import serializers
from .models import Grade, Packaging, Product


class GradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Grade
        fields = ["id", "name", "is_active"]


class PackagingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Packaging
        fields = ["id", "name", "weight_kg", "is_active"]


class ProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    weight_kg = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Product
        fields = ["id", "grade", "packaging", "price", "is_active", "label", "weight_kg"]
```

`catalog/views.py`:
```python
from rest_framework import viewsets
from accounts.permissions import IsStaff, IsManager
from .models import Grade, Packaging, Product
from .serializers import GradeSerializer, PackagingSerializer, ProductSerializer


class _StaffReadManagerWrite(viewsets.ModelViewSet):
    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [IsManager()]


class GradeViewSet(_StaffReadManagerWrite):
    queryset = Grade.objects.all()
    serializer_class = GradeSerializer


class PackagingViewSet(_StaffReadManagerWrite):
    queryset = Packaging.objects.all()
    serializer_class = PackagingSerializer


class ProductViewSet(_StaffReadManagerWrite):
    queryset = Product.objects.select_related("grade", "packaging").all()
    serializer_class = ProductSerializer
```

`catalog/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import GradeViewSet, PackagingViewSet, ProductViewSet

router = DefaultRouter()
router.register("grades", GradeViewSet)
router.register("packagings", PackagingViewSet)
router.register("products", ProductViewSet)
urlpatterns = router.urls
```

`catalog/admin.py`:
```python
from django.contrib import admin
from .models import Grade, Packaging, Product

admin.site.register([Grade, Packaging, Product])
```

- [ ] **Step 4: Register app and urls**

In `config/settings.py` add `"catalog"` to `INSTALLED_APPS`.
In `config/urls.py` add: `path("api/", include("catalog.urls")),` and `from django.urls import include`.

- [ ] **Step 5: Migrate and run tests (verify pass)**

Run:
```bash
python manage.py makemigrations catalog && python manage.py migrate
pytest catalog/tests/test_catalog_api.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: dynamic catalog (Grade, Packaging, Product) with role-gated API"
```

---

### Task 4: Clients

**Files:**
- Create: `clients/models.py`, `clients/serializers.py`, `clients/views.py`, `clients/urls.py`, `clients/admin.py`
- Modify: `config/settings.py`, `config/urls.py`
- Test: `clients/tests/test_clients_api.py`

**Interfaces:**
- Consumes: `IsStaff`, `IsManager`; `accounts.User`.
- Produces:
  - `clients.models.Client(name, contact, country="", requisites="", user FK→accounts.User null)`. `name` and `contact` required; `country`, `requisites` optional (blank allowed). `user` links a portal login (nullable, OneToOne).
  - `/api/clients/` ModelViewSet (read: staff; write: manager).

- [ ] **Step 1: Write failing client tests**

`clients/tests/test_clients_api.py`:
```python
import pytest
from clients.models import Client

pytestmark = pytest.mark.django_db


def test_manager_creates_client_without_optional_fields(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/", {"name": "Лидер", "contact": "+998..."}
    )
    assert resp.status_code == 201
    c = Client.objects.get(name="Лидер")
    assert c.country == "" and c.requisites == ""


def test_country_and_requisites_optional(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/",
        {"name": "Эксп", "contact": "x", "country": "Узбекистан"},
    )
    assert resp.status_code == 201


def test_accountant_cannot_create_client(auth_client, accountant):
    resp = auth_client(accountant).post(
        "/api/clients/", {"name": "X", "contact": "y"}
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Implement model**

`clients/models.py`:
```python
from django.conf import settings
from django.db import models


class Client(models.Model):
    name = models.CharField(max_length=200)
    contact = models.CharField(max_length=200)
    country = models.CharField(max_length=100, blank=True, default="")
    requisites = models.TextField(blank=True, default="")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="client_profile",
    )

    def __str__(self):
        return self.name
```

- [ ] **Step 3: Implement serializer, view, urls, admin**

`clients/serializers.py`:
```python
from rest_framework import serializers
from .models import Client


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "name", "contact", "country", "requisites", "user"]
```

`clients/views.py`:
```python
from rest_framework import viewsets
from accounts.permissions import IsStaff, IsManager
from .models import Client
from .serializers import ClientSerializer


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [IsManager()]
```

`clients/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import ClientViewSet

router = DefaultRouter()
router.register("clients", ClientViewSet)
urlpatterns = router.urls
```

`clients/admin.py`:
```python
from django.contrib import admin
from .models import Client

admin.site.register(Client)
```

- [ ] **Step 4: Register app and urls**

Add `"clients"` to `INSTALLED_APPS`; add `path("api/", include("clients.urls")),` to `config/urls.py`.

- [ ] **Step 5: Migrate and run tests**

Run:
```bash
python manage.py makemigrations clients && python manage.py migrate
pytest clients/tests/test_clients_api.py -v
```
Expected: all 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: clients API with optional country/requisites and portal user link"
```

---

### Task 5: EventLog — append-only log + log_event service

**Files:**
- Create: `eventlog/models.py`, `eventlog/services.py`, `eventlog/serializers.py`, `eventlog/views.py`, `eventlog/urls.py`
- Modify: `config/settings.py`, `config/urls.py`
- Test: `eventlog/tests/test_eventlog.py`

**Interfaces:**
- Consumes: `IsStaff`; `accounts.User`.
- Produces:
  - `eventlog.models.EventLog(event_type, message, order FK→orders.Order null, user FK null, payload JSON, created_at auto)`. `save()` raises on update; `delete()` raises. NOTE: the `order` FK is added in Task 6 after Order exists — in this task define EventLog with `order = None`-capable nullable FK using a string reference `"orders.Order"` (Django resolves lazily; the migration for it is generated in Task 6's migration run, so define the field here but run makemigrations only after Order exists). To keep this task self-contained and testable now, the FK is declared but the app `orders` must be in INSTALLED_APPS before migrating; therefore **this task declares EventLog without the order FK**, and Task 6 adds the `order` FK via a migration.
  - `eventlog.services.log_event(event_type: str, message: str, *, user=None, order=None, payload: dict | None = None) -> EventLog`.
  - `GET /api/events/` (staff read-only, newest first, filter by `?order=<id>` and `?event_type=`).

- [ ] **Step 1: Write failing eventlog tests**

`eventlog/tests/test_eventlog.py`:
```python
import pytest
from eventlog.models import EventLog
from eventlog.services import log_event

pytestmark = pytest.mark.django_db


def test_log_event_creates_entry(boss):
    e = log_event("arrival", "Машина прибыла", user=boss, payload={"net": 1000})
    assert e.pk is not None
    assert e.payload["net"] == 1000


def test_eventlog_is_append_only_no_update(boss):
    e = log_event("arrival", "msg", user=boss)
    e.message = "changed"
    with pytest.raises(Exception):
        e.save()


def test_eventlog_no_delete(boss):
    e = log_event("arrival", "msg", user=boss)
    with pytest.raises(Exception):
        e.delete()


def test_events_endpoint_lists_newest_first(auth_client, operator):
    log_event("a", "first", user=operator)
    log_event("b", "second", user=operator)
    resp = auth_client(operator).get("/api/events/")
    assert resp.status_code == 200
    assert resp.data[0]["message"] == "second"
```

- [ ] **Step 2: Implement model (append-only)**

`eventlog/models.py`:
```python
from django.conf import settings
from django.db import models


class EventLog(models.Model):
    event_type = models.CharField(max_length=50)
    message = models.CharField(max_length=500)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("EventLog записи неизменяемы")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("EventLog записи нельзя удалять")
```

- [ ] **Step 3: Implement service, serializer, view, urls**

`eventlog/services.py`:
```python
from .models import EventLog


def log_event(event_type, message, *, user=None, order=None, payload=None):
    return EventLog.objects.create(
        event_type=event_type, message=message, user=user,
        payload=payload or {},
    )
```
(The `order` kwarg is accepted now and wired to a real FK in Task 6.)

`eventlog/serializers.py`:
```python
from rest_framework import serializers
from .models import EventLog


class EventLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventLog
        fields = ["id", "event_type", "message", "user", "payload", "created_at"]
```

`eventlog/views.py`:
```python
from rest_framework import viewsets, mixins
from accounts.permissions import IsStaff
from .models import EventLog
from .serializers import EventLogSerializer


class EventLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = EventLog.objects.all()
        order = self.request.query_params.get("order")
        etype = self.request.query_params.get("event_type")
        if order:
            qs = qs.filter(order_id=order)
        if etype:
            qs = qs.filter(event_type=etype)
        return qs
```

`eventlog/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import EventLogViewSet

router = DefaultRouter()
router.register("events", EventLogViewSet, basename="events")
urlpatterns = router.urls
```

- [ ] **Step 4: Register app and urls**

Add `"eventlog"` to `INSTALLED_APPS`; add `path("api/", include("eventlog.urls")),`.
Note: the `?order=` filter references `order_id`, which becomes real in Task 6; until then it filters on a column added in Task 6. For this task, remove the `order` filter branch temporarily OR keep it — the `test_events_endpoint_lists_newest_first` test does not exercise it. Keep the code; the column is added in Task 6.

CORRECTION for self-containment: in Step 3 `views.py`, guard the order filter so this task migrates cleanly:
```python
        if order and hasattr(EventLog, "order"):
            qs = qs.filter(order_id=order)
```

- [ ] **Step 5: Migrate and run tests**

Run:
```bash
python manage.py makemigrations eventlog && python manage.py migrate
pytest eventlog/tests/test_eventlog.py -v
```
Expected: all 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: append-only EventLog with log_event service and read API"
```

---

### Task 6: Orders & OrderItems — creation, totals, EventLog order FK

**Files:**
- Create: `orders/models.py`, `orders/serializers.py`, `orders/views.py`, `orders/urls.py`, `orders/admin.py`
- Modify: `eventlog/models.py` (add `order` FK), `eventlog/services.py` (wire order), `config/settings.py`, `config/urls.py`
- Test: `orders/tests/test_orders_api.py`

**Interfaces:**
- Consumes: `Client` (Task 4), `Product` (Task 3), `IsStaff`/`IsManager`, `log_event`.
- Produces:
  - `orders.models.Order(client FK, status default "draft", truck_number="", debt_override BooleanField default False, debt_override_by FK null, created_by FK null, created_at)`. Property `total_amount` = sum of items' `quantity * product.price`. Property `is_fully_paid` = `paid_total >= total_amount and total_amount > 0` (paid_total defined in Task 7; until then property returns False when no payments). Constant `Order.STATUSES`.
  - `orders.models.OrderItem(order FK, product FK, quantity int>0)`.
  - `/api/orders/` ModelViewSet with nested items on create (read: staff; write: manager). Statuses are NOT settable directly via this serializer — only via lifecycle endpoints (Tasks 7–9).

- [ ] **Step 1: Write failing orders tests**

`orders/tests/test_orders_api.py`:
```python
import pytest
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order

pytestmark = pytest.mark.django_db


def _product(price="100.00"):
    g = Grade.objects.create(name="Премиум")
    p = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    return Product.objects.create(grade=g, packaging=p, price=price)


def test_manager_creates_order_with_items(auth_client, manager):
    client = Client.objects.create(name="Лидер", contact="x")
    prod = _product("100.00")
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "items": [{"product": prod.id, "quantity": 5}]},
        format="json",
    )
    assert resp.status_code == 201
    order = Order.objects.get()
    assert order.status == "draft"
    assert order.total_amount == 500


def test_order_status_not_settable_via_create(auth_client, manager):
    client = Client.objects.create(name="L", contact="x")
    prod = _product()
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "status": "shipped",
         "items": [{"product": prod.id, "quantity": 1}]},
        format="json",
    )
    assert resp.status_code == 201
    assert Order.objects.get().status == "draft"
```

- [ ] **Step 2: Implement models**

`orders/models.py`:
```python
from django.conf import settings
from django.db import models
from decimal import Decimal


class Order(models.Model):
    STATUSES = ["draft", "confirmed", "paid", "arrived", "loading", "shipped", "cancelled"]

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, default="draft")
    truck_number = models.CharField(max_length=30, blank=True, default="")
    debt_override = models.BooleanField(default=False)
    debt_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="debt_overrides",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_amount(self) -> Decimal:
        return sum((i.quantity * i.product.price for i in self.items.all()), Decimal("0"))

    @property
    def paid_total(self) -> Decimal:
        return sum((p.amount for p in self.payments.all()), Decimal("0"))

    @property
    def is_fully_paid(self) -> bool:
        return self.total_amount > 0 and self.paid_total >= self.total_amount


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
```

(`payments` related manager is created in Task 7; `paid_total` returns 0 until then because the relation is empty / not yet present — guard added in Task 7. For THIS task, temporarily define `paid_total` to return `Decimal("0")` and replace it in Task 7.)

To keep Task 6 runnable, use this `paid_total` now:
```python
    @property
    def paid_total(self) -> Decimal:
        return Decimal("0")
```

- [ ] **Step 3: Add order FK to EventLog**

In `eventlog/models.py` add to `EventLog`:
```python
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="events",
    )
```
In `eventlog/services.py` update create call to pass `order=order`:
```python
    return EventLog.objects.create(
        event_type=event_type, message=message, user=user,
        order=order, payload=payload or {},
    )
```

- [ ] **Step 4: Implement serializers (nested items, status read-only)**

`orders/serializers.py`:
```python
from rest_framework import serializers
from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "product", "quantity"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    status = serializers.CharField(read_only=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = ["id", "client", "status", "truck_number", "items",
                  "total_amount", "paid_total", "is_fully_paid",
                  "debt_override", "created_at"]
        read_only_fields = ["truck_number", "debt_override"]

    def create(self, validated_data):
        items = validated_data.pop("items")
        validated_data["created_by"] = self.context["request"].user
        order = Order.objects.create(**validated_data)
        for item in items:
            OrderItem.objects.create(order=order, **item)
        return order
```

- [ ] **Step 5: Implement view, urls, admin**

`orders/views.py`:
```python
from rest_framework import viewsets
from accounts.permissions import IsStaff, IsManager
from .models import Order
from .serializers import OrderSerializer


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.select_related("client").prefetch_related("items__product")
    serializer_class = OrderSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [IsManager()]
```

`orders/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import OrderViewSet

router = DefaultRouter()
router.register("orders", OrderViewSet)
urlpatterns = router.urls
```

`orders/admin.py`:
```python
from django.contrib import admin
from .models import Order, OrderItem

admin.site.register([Order, OrderItem])
```

- [ ] **Step 6: Register app and urls, migrate, test**

Add `"orders"` to `INSTALLED_APPS`; add `path("api/", include("orders.urls")),`.
Run:
```bash
python manage.py makemigrations orders eventlog && python manage.py migrate
pytest orders/tests/test_orders_api.py -v
```
Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: orders with nested items, totals, and EventLog order link"
```

---

### Task 7: Payments + auto-paid transition

**Files:**
- Create: `orders/payment_service.py` (or add to `orders/services.py`)
- Modify: `orders/models.py` (Payment model + real `paid_total`), `orders/serializers.py`, `orders/views.py` (payment action), `orders/urls.py` (no change)
- Test: `orders/tests/test_payments.py`

**Interfaces:**
- Consumes: `Order` (Task 6), `IsAccountant`, `log_event`.
- Produces:
  - `orders.models.Payment(order FK→payments, amount Decimal>0, paid_at auto, recorded_by FK null)`.
  - `orders.services.add_payment(order, amount: Decimal, user) -> Payment`: creates payment, logs event, and if order becomes fully paid AND status in (`confirmed`,) sets status to `paid` (transactional).
  - `POST /api/orders/{id}/payments/` (accountant only): body `{amount}`.
  - Real `Order.paid_total` summing payments.

- [ ] **Step 1: Write failing payment tests**

`orders/tests/test_payments.py`:
```python
import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _order(status="confirmed", price="100.00", qty=5):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price=price)
    c = Client.objects.create(name="L", contact="x")
    o = Order.objects.create(client=c, status=status)
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    return o


def test_partial_payment_keeps_status(auth_client, accountant):
    o = _order()  # total 500
    resp = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": "200.00"}, format="json"
    )
    assert resp.status_code == 201
    o.refresh_from_db()
    assert o.paid_total == Decimal("200.00")
    assert o.status == "confirmed"


def test_full_payment_sets_status_paid(auth_client, accountant):
    o = _order()  # total 500
    auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    o.refresh_from_db()
    assert o.is_fully_paid is True
    assert o.status == "paid"


def test_manager_cannot_record_payment(auth_client, manager):
    o = _order()
    resp = auth_client(manager).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Add Payment model and fix paid_total**

In `orders/models.py` add:
```python
class Payment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
```
Replace the temporary `paid_total` with:
```python
    @property
    def paid_total(self) -> Decimal:
        return sum((p.amount for p in self.payments.all()), Decimal("0"))
```

- [ ] **Step 3: Implement add_payment service**

`orders/services.py`:
```python
from decimal import Decimal
from django.db import transaction
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from .models import Order, Payment


@transaction.atomic
def add_payment(order: Order, amount: Decimal, user) -> Payment:
    if amount is None or Decimal(amount) <= 0:
        raise ValidationError({"detail": "Сумма оплаты должна быть больше нуля", "code": "invalid_amount"})
    payment = Payment.objects.create(order=order, amount=amount, recorded_by=user)
    log_event("payment", f"Оплата {amount}", user=user, order=order,
              payload={"amount": str(amount)})
    order.refresh_from_db()
    if order.status == "confirmed" and order.is_fully_paid:
        order.status = "paid"
        order.save(update_fields=["status"])
        log_event("status", "Заказ оплачен", user=user, order=order)
    return payment
```

- [ ] **Step 4: Add payment serializer + action**

In `orders/serializers.py` add:
```python
class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "order", "amount", "paid_at", "recorded_by"]
        read_only_fields = ["order", "paid_at", "recorded_by"]
```
(Add `Payment` to the import.)

In `orders/views.py`:
```python
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.permissions import IsAccountant
from .serializers import PaymentSerializer
from .services import add_payment

    @action(detail=True, methods=["post"], permission_classes=[IsAccountant],
            url_path="payments")
    def payments(self, request, pk=None):
        order = self.get_object()
        amount = request.data.get("amount")
        payment = add_payment(order, amount, request.user)
        return Response(PaymentSerializer(payment).data, status=201)
```

- [ ] **Step 5: Migrate and run tests**

Run:
```bash
python manage.py makemigrations orders && python manage.py migrate
pytest orders/tests/test_payments.py -v
```
Expected: all 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: payments with auto paid-status transition, accountant-gated"
```

---

### Task 8: Warehouse — StockItem, receipts, receive_stock service

**Files:**
- Create: `warehouse/models.py`, `warehouse/services.py`, `warehouse/serializers.py`, `warehouse/views.py`, `warehouse/urls.py`, `warehouse/admin.py`
- Modify: `config/settings.py`, `config/urls.py`
- Test: `warehouse/tests/test_warehouse.py`

**Interfaces:**
- Consumes: `Product` (Task 3), `IsStaff`/`IsManager`, `log_event`.
- Produces:
  - `warehouse.models.StockItem(product OneToOne, bags int default 0)`.
  - `warehouse.models.StockReceipt(product FK, bags int>0, received_at auto, received_by FK null)`.
  - `warehouse.services.receive_stock(product, bags: int, user) -> StockReceipt`: atomically increments StockItem.bags (get_or_create), logs event.
  - `warehouse.services.deduct_stock(product, bags: int)` raises `ValidationError` if insufficient; decrements. (Used by Task 9.)
  - `GET /api/stock/` (staff read), `POST /api/stock/receipts/` (manager).

- [ ] **Step 1: Write failing warehouse tests**

`warehouse/tests/test_warehouse.py`:
```python
import pytest
from catalog.models import Grade, Packaging, Product
from warehouse.models import StockItem
from warehouse.services import receive_stock, deduct_stock
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _product():
    g = Grade.objects.create(name="Премиум")
    p = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    return Product.objects.create(grade=g, packaging=p, price="100.00")


def test_receive_stock_increments(boss):
    prod = _product()
    receive_stock(prod, 100, boss)
    receive_stock(prod, 50, boss)
    assert StockItem.objects.get(product=prod).bags == 150


def test_deduct_stock_reduces(boss):
    prod = _product()
    receive_stock(prod, 100, boss)
    deduct_stock(prod, 30)
    assert StockItem.objects.get(product=prod).bags == 70


def test_deduct_more_than_available_raises(boss):
    prod = _product()
    receive_stock(prod, 10, boss)
    with pytest.raises(ValidationError):
        deduct_stock(prod, 50)


def test_receipt_endpoint_manager_only(auth_client, operator):
    prod = _product()
    resp = auth_client(operator).post(
        "/api/stock/receipts/", {"product": prod.id, "bags": 10}, format="json"
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Implement models**

`warehouse/models.py`:
```python
from django.conf import settings
from django.db import models


class StockItem(models.Model):
    product = models.OneToOneField("catalog.Product", on_delete=models.CASCADE, related_name="stock")
    bags = models.PositiveIntegerField(default=0)


class StockReceipt(models.Model):
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    bags = models.PositiveIntegerField()
    received_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
```

- [ ] **Step 3: Implement services**

`warehouse/services.py`:
```python
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from .models import StockItem, StockReceipt


@transaction.atomic
def receive_stock(product, bags, user):
    if bags <= 0:
        raise ValidationError({"detail": "Количество мешков должно быть больше нуля", "code": "invalid_bags"})
    item, _ = StockItem.objects.select_for_update().get_or_create(product=product)
    item.bags = F("bags") + bags
    item.save()
    item.refresh_from_db()
    receipt = StockReceipt.objects.create(product=product, bags=bags, received_by=user)
    log_event("receipt", f"Приёмка {bags} мешков", user=user,
              payload={"product": product.id, "bags": bags})
    return receipt


@transaction.atomic
def deduct_stock(product, bags):
    item = StockItem.objects.select_for_update().filter(product=product).first()
    if item is None or item.bags < bags:
        available = 0 if item is None else item.bags
        raise ValidationError({
            "detail": f"Недостаточно мешков на складе (есть {available}, нужно {bags})",
            "code": "insufficient_stock",
        })
    item.bags = F("bags") - bags
    item.save()
    item.refresh_from_db()
    return item
```

- [ ] **Step 4: Implement serializers, views, urls, admin**

`warehouse/serializers.py`:
```python
from rest_framework import serializers
from .models import StockItem, StockReceipt


class StockItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockItem
        fields = ["id", "product", "bags"]


class StockReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockReceipt
        fields = ["id", "product", "bags", "received_at", "received_by"]
        read_only_fields = ["received_at", "received_by"]
```

`warehouse/views.py`:
```python
from rest_framework import viewsets, mixins
from rest_framework.response import Response
from accounts.permissions import IsStaff, IsManager
from .models import StockItem
from .serializers import StockItemSerializer, StockReceiptSerializer
from .services import receive_stock
from catalog.models import Product


class StockViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = StockItem.objects.select_related("product")
    serializer_class = StockItemSerializer
    permission_classes = [IsStaff]


class StockReceiptViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = StockReceiptSerializer
    permission_classes = [IsManager]

    def create(self, request, *args, **kwargs):
        product = Product.objects.get(pk=request.data["product"])
        receipt = receive_stock(product, int(request.data["bags"]), request.user)
        return Response(StockReceiptSerializer(receipt).data, status=201)
```

`warehouse/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import StockViewSet, StockReceiptViewSet

router = DefaultRouter()
router.register("stock/receipts", StockReceiptViewSet, basename="stock-receipts")
router.register("stock", StockViewSet, basename="stock")
urlpatterns = router.urls
```

`warehouse/admin.py`:
```python
from django.contrib import admin
from .models import StockItem, StockReceipt

admin.site.register([StockItem, StockReceipt])
```

- [ ] **Step 5: Register, migrate, test**

Add `"warehouse"` to `INSTALLED_APPS`; add `path("api/", include("warehouse.urls")),`.
Run:
```bash
python manage.py makemigrations warehouse && python manage.py migrate
pytest warehouse/tests/test_warehouse.py -v
```
Expected: all 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: warehouse stock in bags with receive/deduct services and API"
```

---

### Task 9: Shipment lifecycle — arrive / load / ship with all business rules

**Files:**
- Create: `shipments/models.py`, `shipments/services.py`, `shipments/serializers.py`, `shipments/views.py`, `shipments/urls.py`, `shipments/admin.py`
- Modify: `config/settings.py`, `config/urls.py`
- Test: `shipments/tests/test_lifecycle.py`

**Interfaces:**
- Consumes: `Order` (Task 6), `deduct_stock` (Task 8), `log_event`, `IsOperator`, `IsBoss`.
- Produces:
  - `shipments.models.Shipment(order OneToOne, truck_number, weigh_in_kg Decimal null, weigh_out_kg Decimal null, net_weight_kg Decimal null, bags_loaded int default 0, arrived_at null, shipped_at null)`.
  - Services in `shipments/services.py`:
    - `record_arrival(order, truck_number, weigh_in_kg, user, debt_override=False)`: requires status `paid` OR (`confirmed`/`paid` + boss debt_override). Sets status `arrived`, creates Shipment, logs. Raises `ValidationError` code `payment_required` if not paid and no override.
    - `record_loading(order, bags, user)`: requires status `arrived`; sets `loading`, records bags, logs.
    - `record_shipment(order, weigh_out_kg, user)`: requires status `loading`; computes `net = abs(weigh_out - weigh_in)`; deducts stock per order items (transactional); sets `shipped`; logs discrepancy (camera-bag estimate vs scale net). Rejects double-ship.
  - Endpoints: `POST /api/orders/{id}/arrive/`, `/load/`, `/ship/` (operator; arrive accepts `debt_override` which is only honored for boss).

- [ ] **Step 1: Write failing lifecycle tests**

`shipments/tests/test_lifecycle.py`:
```python
import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem
from warehouse.services import receive_stock
from rest_framework.exceptions import ValidationError
from shipments.services import record_arrival, record_loading, record_shipment

pytestmark = pytest.mark.django_db


def _paid_order(boss, status="paid", bags_in_stock=100, qty=50):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, bags_in_stock, boss)
    c = Client.objects.create(name="L", contact="x")
    o = Order.objects.create(client=c, status=status)
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    return o, prod


def test_arrive_requires_payment(boss, operator):
    o, _ = _paid_order(boss, status="confirmed")
    with pytest.raises(ValidationError):
        record_arrival(o, "01A123", Decimal("8000"), operator)


def test_boss_debt_override_allows_arrival(boss):
    o, _ = _paid_order(boss, status="confirmed")
    record_arrival(o, "01A123", Decimal("8000"), boss, debt_override=True)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.debt_override is True


def test_full_flow_deducts_stock_and_computes_net(boss, operator):
    o, prod = _paid_order(boss, status="paid", bags_in_stock=100, qty=50)
    record_arrival(o, "01A123", Decimal("8000"), operator)
    record_loading(o, 50, operator)
    record_shipment(o, Decimal("10500"), operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.shipment.net_weight_kg == Decimal("2500")
    from warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50


def test_double_ship_rejected(boss, operator):
    o, _ = _paid_order(boss)
    record_arrival(o, "01A123", Decimal("8000"), operator)
    record_loading(o, 50, operator)
    record_shipment(o, Decimal("10500"), operator)
    with pytest.raises(ValidationError):
        record_shipment(o, Decimal("10500"), operator)
```

- [ ] **Step 2: Implement model**

`shipments/models.py`:
```python
from django.db import models


class Shipment(models.Model):
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="shipment")
    truck_number = models.CharField(max_length=30)
    weigh_in_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    weigh_out_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    net_weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    bags_loaded = models.PositiveIntegerField(default=0)
    arrived_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 3: Implement lifecycle services**

`shipments/services.py`:
```python
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from warehouse.services import deduct_stock
from .models import Shipment


@transaction.atomic
def record_arrival(order, truck_number, weigh_in_kg, user, debt_override=False):
    if order.status not in ("confirmed", "paid"):
        raise ValidationError({"detail": "Машину можно принять только для подтверждённого заказа", "code": "invalid_status"})
    if not order.is_fully_paid:
        if not (debt_override and user.is_boss):
            raise ValidationError({"detail": "Заказ не оплачен — въезд запрещён", "code": "payment_required"})
        order.debt_override = True
        order.debt_override_by = user
        log_event("debt_override", f"Отгрузка в долг разрешена ({user.username})", user=user, order=order)
    order.truck_number = truck_number
    order.status = "arrived"
    order.save(update_fields=["truck_number", "status", "debt_override", "debt_override_by"])
    shipment, _ = Shipment.objects.get_or_create(order=order, defaults={"truck_number": truck_number})
    shipment.truck_number = truck_number
    shipment.weigh_in_kg = weigh_in_kg
    shipment.arrived_at = timezone.now()
    shipment.save()
    log_event("arrival", f"Машина {truck_number} прибыла", user=user, order=order,
              payload={"weigh_in_kg": str(weigh_in_kg)})
    return shipment


@transaction.atomic
def record_loading(order, bags, user):
    if order.status != "arrived":
        raise ValidationError({"detail": "Загрузка возможна только после прибытия", "code": "invalid_status"})
    order.status = "loading"
    order.save(update_fields=["status"])
    shipment = order.shipment
    shipment.bags_loaded = bags
    shipment.save(update_fields=["bags_loaded"])
    log_event("loading", f"Загружено {bags} мешков", user=user, order=order,
              payload={"bags": bags})
    return shipment


@transaction.atomic
def record_shipment(order, weigh_out_kg, user):
    if order.status != "loading":
        raise ValidationError({"detail": "Выезд возможен только во время загрузки", "code": "invalid_status"})
    shipment = order.shipment
    net = abs(Decimal(weigh_out_kg) - shipment.weigh_in_kg)
    for item in order.items.select_related("product").all():
        deduct_stock(item.product, item.quantity)
    shipment.weigh_out_kg = weigh_out_kg
    shipment.net_weight_kg = net
    shipment.shipped_at = timezone.now()
    shipment.save()
    order.status = "shipped"
    order.save(update_fields=["status"])
    bag_estimate = sum(
        (i.quantity * i.product.weight_kg for i in order.items.all()), Decimal("0")
    )
    log_event("shipment", f"Выезд, нетто {net} кг", user=user, order=order,
              payload={"net_weight_kg": str(net),
                       "bag_estimate_kg": str(bag_estimate),
                       "discrepancy_kg": str(net - bag_estimate)})
    return shipment
```

- [ ] **Step 4: Implement serializers, views, urls, admin**

`shipments/serializers.py`:
```python
from rest_framework import serializers
from .models import Shipment


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["id", "order", "truck_number", "weigh_in_kg", "weigh_out_kg",
                  "net_weight_kg", "bags_loaded", "arrived_at", "shipped_at"]
```

`shipments/views.py` — add lifecycle actions to the OrderViewSet via a mixin module. Implement as standalone APIViews registered on order detail:
```python
from rest_framework.views import APIView
from rest_framework.response import Response
from decimal import Decimal
from accounts.permissions import IsOperator
from orders.models import Order
from .services import record_arrival, record_loading, record_shipment
from .serializers import ShipmentSerializer


class _OrderActionView(APIView):
    permission_classes = [IsOperator]

    def get_order(self, pk):
        return Order.objects.select_related("shipment").prefetch_related("items__product").get(pk=pk)


class ArriveView(_OrderActionView):
    def post(self, request, pk):
        order = self.get_order(pk)
        shipment = record_arrival(
            order, request.data.get("truck_number", ""),
            Decimal(str(request.data["weigh_in_kg"])), request.user,
            debt_override=bool(request.data.get("debt_override", False)),
        )
        return Response(ShipmentSerializer(shipment).data)


class LoadView(_OrderActionView):
    def post(self, request, pk):
        order = self.get_order(pk)
        shipment = record_loading(order, int(request.data["bags"]), request.user)
        return Response(ShipmentSerializer(shipment).data)


class ShipView(_OrderActionView):
    def post(self, request, pk):
        order = self.get_order(pk)
        shipment = record_shipment(order, Decimal(str(request.data["weigh_out_kg"])), request.user)
        return Response(ShipmentSerializer(shipment).data)
```
Note: `ArriveView` permits operator; the `debt_override` flag is only honored inside the service when `user.is_boss`. A boss is also staff but not necessarily in the `operator` group — so add `IsBoss` as an allowed permission. Change `permission_classes` to a custom OR: create `accounts/permissions.py` helper `IsOperatorOrBoss`:

In `accounts/permissions.py` append:
```python
class IsOperatorOrBoss(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and not request.user.is_client and (
            request.user.is_operator or request.user.is_boss
        )
```
Use `IsOperatorOrBoss` in `_OrderActionView.permission_classes`.

`shipments/urls.py`:
```python
from django.urls import path
from .views import ArriveView, LoadView, ShipView

urlpatterns = [
    path("orders/<int:pk>/arrive/", ArriveView.as_view()),
    path("orders/<int:pk>/load/", LoadView.as_view()),
    path("orders/<int:pk>/ship/", ShipView.as_view()),
]
```

`shipments/admin.py`:
```python
from django.contrib import admin
from .models import Shipment

admin.site.register(Shipment)
```

- [ ] **Step 5: Register, migrate, test**

Add `"shipments"` to `INSTALLED_APPS`; add `path("api/", include("shipments.urls")),` BEFORE the orders include is fine (paths are distinct).
Run:
```bash
python manage.py makemigrations shipments && python manage.py migrate
pytest shipments/tests/test_lifecycle.py -v
```
Expected: all 4 PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: all tests from Tasks 1–9 PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: shipment lifecycle (arrive/load/ship) with payment gate, debt override, stock deduction"
```

---

### Task 10: Client portal — scoped catalog + orders

**Files:**
- Create: `portal/serializers.py`, `portal/views.py`, `portal/urls.py`
- Modify: `config/settings.py`, `config/urls.py`
- Test: `portal/tests/test_portal.py`

**Interfaces:**
- Consumes: `IsClientUser` (Task 2), `Product` (Task 3), `Order`/`OrderItem` (Task 6), `Client` (Task 4, via `request.user.client_profile`).
- Produces:
  - `GET /api/portal/catalog/` — active products with price (client read).
  - `GET /api/portal/orders/` — only the requesting client's orders.
  - `POST /api/portal/orders/` — client creates a `draft` order for themselves (client taken from `request.user.client_profile`, never from the body).
  - `GET /api/portal/orders/{id}/` — only if it belongs to the client (404 otherwise).

- [ ] **Step 1: Write failing portal tests**

`portal/tests/test_portal.py`:
```python
import pytest
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order

pytestmark = pytest.mark.django_db


def _product():
    g = Grade.objects.create(name="Премиум")
    p = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    return Product.objects.create(grade=g, packaging=p, price="100.00")


def _client_for(user):
    return Client.objects.create(name="Мой", contact="x", user=user)


def test_client_creates_own_draft_order(auth_client, client_user):
    _client_for(client_user)
    prod = _product()
    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": prod.id, "quantity": 3}]}, format="json",
    )
    assert resp.status_code == 201
    order = Order.objects.get()
    assert order.status == "draft"
    assert order.client.user_id == client_user.id


def test_client_sees_only_own_orders(auth_client, client_user, make_user):
    mine = _client_for(client_user)
    other_user = make_user(username="other", client=True)
    other = Client.objects.create(name="Чужой", contact="y", user=other_user)
    Order.objects.create(client=mine, status="draft")
    Order.objects.create(client=other, status="draft")
    resp = auth_client(client_user).get("/api/portal/orders/")
    assert resp.status_code == 200
    assert len(resp.data) == 1


def test_client_cannot_fetch_foreign_order(auth_client, client_user, make_user):
    _client_for(client_user)
    other_user = make_user(username="other", client=True)
    other = Client.objects.create(name="Чужой", contact="y", user=other_user)
    foreign = Order.objects.create(client=other, status="draft")
    resp = auth_client(client_user).get(f"/api/portal/orders/{foreign.id}/")
    assert resp.status_code == 404


def test_staff_cannot_use_portal(auth_client, manager):
    resp = auth_client(manager).get("/api/portal/orders/")
    assert resp.status_code == 403
```

- [ ] **Step 2: Implement serializers**

`portal/serializers.py`:
```python
from rest_framework import serializers
from catalog.models import Product
from orders.models import Order, OrderItem


class CatalogProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "label", "price", "weight_kg"]


class PortalOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["product", "quantity"]


class PortalOrderSerializer(serializers.ModelSerializer):
    items = PortalOrderItemSerializer(many=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Order
        fields = ["id", "status", "items", "total_amount", "paid_total", "created_at"]
        read_only_fields = ["status"]

    def create(self, validated_data):
        items = validated_data.pop("items")
        client = self.context["request"].user.client_profile
        order = Order.objects.create(client=client, status="draft")
        for item in items:
            OrderItem.objects.create(order=order, **item)
        return order
```

- [ ] **Step 3: Implement views**

`portal/views.py`:
```python
from rest_framework import viewsets, mixins
from accounts.permissions import IsClientUser
from catalog.models import Product
from orders.models import Order
from .serializers import CatalogProductSerializer, PortalOrderSerializer


class PortalCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CatalogProductSerializer
    permission_classes = [IsClientUser]
    queryset = Product.objects.filter(is_active=True).select_related("grade", "packaging")


class PortalOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PortalOrderSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Order.objects.filter(
            client__user=self.request.user
        ).prefetch_related("items__product")
```

- [ ] **Step 4: Implement urls, register**

`portal/urls.py`:
```python
from rest_framework.routers import DefaultRouter
from .views import PortalCatalogViewSet, PortalOrderViewSet

router = DefaultRouter()
router.register("portal/catalog", PortalCatalogViewSet, basename="portal-catalog")
router.register("portal/orders", PortalOrderViewSet, basename="portal-orders")
urlpatterns = router.urls
```
Add `"portal"` to `INSTALLED_APPS`; add `path("api/", include("portal.urls")),`.

- [ ] **Step 5: Run tests**

Run: `pytest portal/tests/test_portal.py -v`
Expected: all 4 PASS. (No new migration — portal has no models.)

- [ ] **Step 6: Full suite + commit**

Run: `pytest -v`
Expected: all tests PASS.
```bash
git add -A
git commit -m "feat: client portal with scoped catalog and self-service orders"
```

---

### Task 11: Seed roles/groups + order confirm endpoint + final wiring

**Files:**
- Create: `accounts/migrations/0002_seed_groups.py` (data migration)
- Modify: `orders/services.py` (add `confirm_order`), `orders/views.py` (confirm action)
- Test: `orders/tests/test_confirm.py`

**Interfaces:**
- Consumes: `Order` (Task 6), `IsManager`, `log_event`.
- Produces:
  - Data migration creating Django groups `manager`, `accountant`, `operator`, `boss`.
  - `orders.services.confirm_order(order, user)`: `draft → confirmed`; logs.
  - `POST /api/orders/{id}/confirm/` (manager).

- [ ] **Step 1: Write failing confirm test**

`orders/tests/test_confirm.py`:
```python
import pytest
from clients.models import Client
from orders.models import Order
from django.contrib.auth.models import Group

pytestmark = pytest.mark.django_db


def test_confirm_moves_draft_to_confirmed(auth_client, manager):
    c = Client.objects.create(name="L", contact="x")
    o = Order.objects.create(client=c, status="draft")
    resp = auth_client(manager).post(f"/api/orders/{o.id}/confirm/")
    assert resp.status_code == 200
    o.refresh_from_db()
    assert o.status == "confirmed"


def test_seed_groups_exist():
    for name in ("manager", "accountant", "operator", "boss"):
        assert Group.objects.filter(name=name).exists()
```

- [ ] **Step 2: Create the data migration**

`accounts/migrations/0002_seed_groups.py`:
```python
from django.db import migrations


def seed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ("manager", "accountant", "operator", "boss"):
        Group.objects.get_or_create(name=name)


def unseed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=("manager", "accountant", "operator", "boss")).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
```

- [ ] **Step 3: Add confirm_order service + action**

In `orders/services.py` add:
```python
@transaction.atomic
def confirm_order(order, user):
    if order.status != "draft":
        raise ValidationError({"detail": "Подтвердить можно только черновик", "code": "invalid_status"})
    order.status = "confirmed"
    order.save(update_fields=["status"])
    log_event("status", "Заказ подтверждён", user=user, order=order)
    return order
```
In `orders/views.py` add to `OrderViewSet`:
```python
    @action(detail=True, methods=["post"], permission_classes=[IsManager],
            url_path="confirm")
    def confirm(self, request, pk=None):
        from .services import confirm_order
        order = confirm_order(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)
```
(Ensure `IsManager` and `Response`/`action` are imported in views.)

- [ ] **Step 4: Migrate and run tests**

Run:
```bash
python manage.py migrate
pytest orders/tests/test_confirm.py -v
```
Expected: both PASS.

- [ ] **Step 5: Full suite + commit**

Run: `pytest -v`
Expected: ALL tests across Tasks 1–11 PASS.
```bash
git add -A
git commit -m "feat: seed role groups and add order confirm transition"
```

---

## Self-Review Notes (coverage map)

- §2 stack (DRF, PostgreSQL, JWT) → Task 1.
- §3 Client (optional country/requisites, portal user link) → Task 4.
- §3 Grade/Packaging/Product dynamic catalog → Task 3.
- §3 StockItem in bags, StockReceipt → Task 8.
- §3 Order/OrderItem/Payment → Tasks 6, 7.
- §3 Shipment (weigh-in/out, net, bags) → Task 9.
- §3 EventLog append-only → Task 5.
- §4 lifecycle statuses + payment gate + boss debt override + stock deduction at ship + double-ship rejection → Tasks 7, 9, 11.
- §5 roles & permissions → Tasks 2, 11.
- §6 all endpoints → Tasks 3,4,5,6,7,8,9.
- §6 portal endpoints + isolation → Task 10.
- §8 transactional integrity, no-negative-stock, error shape, append-only, TDD → Tasks 5,7,8,9 + Task 1 exception handler.
- §7/§7a frontend → OUT OF SCOPE (separate plan).
