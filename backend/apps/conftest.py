import ipaddress
import socket

import pytest


class ExternalNetworkBlocked(RuntimeError):
    """Тест полез в сеть за пределы loopback; внешний вызов нужно замокать."""


def _ip(host):
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return None


def _is_local(host) -> bool:
    address = _ip(host)
    if address is None:
        return host in ("localhost", b"localhost")
    return address.is_loopback or address.is_unspecified


@pytest.fixture(autouse=True, scope="session")
def _block_external_network():
    """Под pytest сокеты открываются только на loopback и unix-сокеты.

    Без этого незамоканный вызов ПК цеха, go2rtc, ApiPay или Green-API ушёл
    бы на боевой адрес: тест висел бы на таймауте или трогал живой сервис.
    Ошибка — не ``OSError``, чтобы best-effort код её не проглотил молча.
    Имя хоста не резолвится вовсе; IP-литерал проверяет ``connect``.
    Исключение — хост тестовой базы: psycopg резолвит ``DB_HOST`` сам.
    """
    from django.conf import settings

    database_hosts = {db.get("HOST") for db in settings.DATABASES.values()} - {None, ""}
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def _check(host, target):
        if not _is_local(host):
            raise ExternalNetworkBlocked(
                f"pytest: сеть к {target!r} закрыта — замокайте внешний вызов "
                "(apps/conftest.py::_block_external_network)"
            )

    def guarded_connect(self, address):
        if isinstance(address, tuple):
            _check(address[0], address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        if isinstance(address, tuple):
            _check(address[0], address)
        return connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and _ip(host) is None and host not in database_hosts:
            _check(host, host)
        return getaddrinfo(host, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(socket.socket, "connect", guarded_connect)
        patcher.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        patcher.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
        yield


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def production_throttling():
    """Re-enable the API-wide throttles that the test settings switch off.

    DRF copies ``DEFAULT_THROTTLE_CLASSES`` onto ``APIView`` at import time, so
    ``override_settings`` cannot bring them back for views that inherit them.
    Patch the class attribute instead, with a tiny rate a short burst exceeds.
    """
    from contextlib import contextmanager
    from unittest.mock import patch

    from django.core.cache import cache
    from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
    from rest_framework.views import APIView

    @contextmanager
    def enabled(rate="2/min"):
        cache.clear()
        try:
            with patch.object(APIView, "throttle_classes", (AnonRateThrottle, UserRateThrottle)), patch.object(
                AnonRateThrottle, "rate", rate, create=True
            ), patch.object(UserRateThrottle, "rate", rate, create=True):
                yield
        finally:
            cache.clear()

    return enabled


@pytest.fixture
def make_user(db, django_user_model):
    def _make(username="u", password="pass12345", client=False):
        return django_user_model.objects.create_user(
            username=username,
            password=password,
            is_client=client,
            first_name="A",
            last_name="B",
        )

    return _make


@pytest.fixture
def get_permission(db):
    """Право из каталога ``PERMISSIONS`` по коду (создаётся при первом вызове)."""
    from apps.sys_permissions.models import Permission
    from apps.sys_permissions.perms import PERMISSIONS

    catalog = {permission["code"]: permission for permission in PERMISSIONS}

    def _get(code):
        assert code in catalog, f"кода нет в каталоге прав: {code}"
        meta = catalog[code]
        permission, _ = Permission.objects.get_or_create(
            code=code,
            defaults={"section": meta["section"], "action": meta["action"], "label": meta["label"]},
        )
        return permission

    return _get


@pytest.fixture
def user_with_perms(make_user, get_permission):
    from apps.employees.models import Employee

    def _make(username="emp", codes=(), department=None):
        user = make_user(username=username)
        employee = Employee.objects.create(user=user, phone="x", sales_department=department)
        employee.permissions.add(*(get_permission(code) for code in codes))
        return user

    return _make


@pytest.fixture
def departments(db):
    """Два отдела продаж: ``mill`` (Мельница) и ``city`` (Нью-Сити)."""
    from apps.sales.models import Department

    return (
        Department.objects.create(code="mill", name="Мельница"),
        Department.objects.create(code="city", name="Нью-Сити"),
    )


@pytest.fixture
def make_product(db):
    from apps.catalog.models import Product

    def _make(name="Премиум", color="Red", weight_kg="50", **fields):
        return Product.objects.create(name=name, color=color, weight_kg=weight_kg, **fields)

    return _make


@pytest.fixture
def media_root(settings, tmp_path):
    """MEDIA_ROOT во временной папке теста: файлы не пишутся в настоящий media."""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def manager(user_with_perms):
    return user_with_perms(
        "manager",
        codes=[
            "catalog.view",
            "catalog.create",
            "catalog.edit",
            "clients.view",
            "clients.create",
            "clients.edit",
            "clients.delete",
            "clients.set_price",
            "stores.view",
            "stores.create",
            "stores.edit",
            "stores.delete",
            "orders.view",
            "orders.create",
            "orders.edit",
            "orders.confirm",
        ],
    )


@pytest.fixture
def accountant(user_with_perms):
    return user_with_perms(
        "accountant",
        codes=[
            "payments.view",
            "payments.create",
            "payments.confirm",
            "orders.view",
            "orders.confirm",
            "orders.edit",
        ],
    )


@pytest.fixture
def payment_recorder(user_with_perms):
    return user_with_perms(
        "payment-recorder",
        codes=[
            "payments.view",
            "payments.create",
            "orders.view",
            "orders.edit",
        ],
    )


@pytest.fixture
def operator(user_with_perms):
    return user_with_perms(
        "operator",
        codes=[
            "monoblock.view",
            "loader.view",
            "loader.confirm",
            "loader.trucks",
            "loader.wagons",
            "orders.view",
            "warehouse.view",
            "events.view",
        ],
    )


@pytest.fixture
def boss(user_with_perms):
    return user_with_perms(
        "boss",
        codes=[
            "monoblock.view",
            "loader.view",
            "loader.confirm",
            "loader.trucks",
            "loader.wagons",
            "orders.rollback",
            "orders.view",
            "orders.edit",
            "warehouse.view",
            "warehouse.adjust",
            "catalog.view",
            "clients.view",
            "clients.edit",
            "clients.set_price",
            "stores.view",
            "stores.edit",
            "employees.view",
            "employees.manage",
            "sys_permissions.manage",
            "reports.view",
            "reports.export",
        ],
    )


@pytest.fixture
def settle_payment():
    def _settle(payment, user):
        from apps.orders import services

        if payment.status == "requested":
            services.receive_payment(payment, user)
        if payment.status == "received":
            services.accountant_confirm_payment(payment, user)
        payment.refresh_from_db()
        return payment

    return _settle


@pytest.fixture
def client_user(make_user):
    return make_user(username="client", client=True)


@pytest.fixture
def auth_client():
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken

    def _auth(user):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    return _auth


@pytest.fixture
def api_as():
    """``APIClient`` от имени пользователя через ``force_authenticate``, без JWT."""
    from rest_framework.test import APIClient

    def _as(user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    return _as


@pytest.fixture
def count_queries(api_as):
    """Число SQL-запросов одного GET ``url`` от имени ``user`` (для проверок N+1)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def _count(user, url):
        # Свежий инстанс из БД — как в реальном запросе (JWT достаёт юзера заново),
        # иначе кэш effective_perm_codes переживает вызовы и искажает счётчик.
        api = api_as(type(user).objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(url)
            assert response.status_code == 200
        return len(ctx)

    return _count


@pytest.fixture
def apipay_department(db):
    """Основной отдел ``main`` (код по умолчанию у Order) с ключом ApiPay.

    Общего ключа в настройках больше нет: каждый тест, который выставляет
    счёт или принимает вебхук, работает через ключ и секрет этого отдела.
    """
    from apps.sales.models import Department

    department, _ = Department.objects.get_or_create(
        code="main", defaults={"name": "Мельница", "is_default": True}
    )
    department.set_apipay_api_key("server-only-key")
    department.set_apipay_webhook_secret("webhook-secret")
    department.save()
    return department


@pytest.fixture
def run_camera_monitor_once(monkeypatch, settings, tmp_path):
    """Один круг ``monitor_cameras --once`` при здоровых камерах и включённом AI.

    Возвращает функцию запуска; она отдаёт stdout команды. Проверку камер
    подменяет ``health.monitor_once``, heartbeat пишется во временную папку.
    """
    from io import StringIO
    from types import SimpleNamespace
    from unittest.mock import patch

    from django.core.management import call_command

    from apps.cameras import ai

    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    settings.CAMERA_MONITOR_HEARTBEAT_FILE = str(tmp_path / "camera-monitor.json")
    healthy = SimpleNamespace(
        status="healthy",
        observed_status="healthy",
        online_count=8,
        expected_count=8,
        failure_streak=0,
        recovery_streak=1,
    )

    def _run():
        stdout = StringIO()
        with (
            # Закрытие соединения между кругами сломало бы транзакцию теста.
            patch("apps.common.daemon.close_old_connections"),
            patch(
                "apps.cameras.management.commands.monitor_cameras.health.monitor_once",
                return_value=healthy,
            ),
        ):
            call_command("monitor_cameras", "--once", stdout=stdout)
        return stdout.getvalue()

    return _run


_ROWS_WITH_WAREHOUSE = frozenset(
    {
        "orders.order",
        "warehouse.stockitem",
        "warehouse.stockreceipt",
        "warehouse.stockmovement",
        "cameras.alwaysonstockbatch",
    }
)


@pytest.fixture(autouse=True)
def _main_warehouse_for_test_rows():
    """Тестовые заказы и складские строки без склада получают склад ``main``.

    Рабочий код склад передаёт всегда (колонки NOT NULL); это только
    сокращение для фикстур, которые создают строки напрямую через ORM.
    """
    from django.apps import apps
    from django.db.models.signals import pre_save

    def _fill(sender, instance, raw=False, **kwargs):
        # Исторические модели миграционных тестов живут в своём реестре.
        if raw or sender._meta.apps is not apps:
            return
        if sender._meta.label_lower not in _ROWS_WITH_WAREHOUSE:
            return
        if instance.warehouse_id is None:
            from apps.warehouse.models import Warehouse

            instance.warehouse_id = Warehouse.objects.get(code="main").pk

    pre_save.connect(_fill, dispatch_uid="tests.main_warehouse_for_test_rows")
    yield
    pre_save.disconnect(dispatch_uid="tests.main_warehouse_for_test_rows")
