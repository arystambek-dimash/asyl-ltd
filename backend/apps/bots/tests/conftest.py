"""Клиент, товар и цена из отчёта владельца, грузчики вагонов и включённый бот — общие для тестов бота."""
import pytest
from django.utils import timezone

from apps.bots import messages
from apps.bots.models import BotChat, TelegramBotSettings
from apps.bots.service_user import ensure_bot_user
from apps.bots.tests.samples import CONDUCT_CODES, OWNER_REPORT
from apps.bots.tests.telegram_fakes import DINARA, DINARA_CHAT, JIN, bot_alive, incoming
from apps.catalog.models import ClientPrice, Product, ProductAlias
from apps.clients.models import Client
from apps.sales.models import Department
from apps.warehouse.services import receive_stock


@pytest.fixture
def department():
    return Department.objects.create(code="export", name="Экспорт")


@pytest.fixture
def client(department):
    return Client.objects.create_with_user(
        first_name="Осиё", phone="+998 90 111 22 33", company_name="ООО OSIYO NAV NIHOL",
        currency="USD", department=department, country="Узбекистан",
    )


@pytest.fixture
def nurzhan(department):
    """Клиент фур из Казахстана — как в чате отгрузок: «Нуржан Сарыагаш 87029368080»."""
    return Client.objects.create_with_user(
        first_name="Нуржан", last_name="Сарыагаш", phone="+7 (702) 936-80-80",
        currency="KZT", department=department, country="Казахстан",
    )


@pytest.fixture
def product(boss):
    item = Product.objects.create(name="Мука высший сорт", color="Red", weight_kg="50")
    receive_stock(item, 20000, boss)
    ProductAlias.objects.create(code="Д1с", product=item)
    return item


@pytest.fixture
def price(client, product):
    return ClientPrice.objects.create(client=client, product=product, currency="USD", price="7.50")


@pytest.fixture
def conductor(user_with_perms):
    return user_with_perms("rail-conductor", codes=CONDUCT_CODES)


@pytest.fixture
def wagon_loader(user_with_perms):
    """Грузчик вагонов: отгружает, но заказов не создаёт."""
    return user_with_perms("wagon-loader", codes=["loader.view", "loader.confirm", "loader.wagons"])


@pytest.fixture
def wagon_viewer(user_with_perms):
    """Видит вкладку «Вагоны» грузчика, отгружать не может."""
    return user_with_perms("wagon-viewer", codes=["loader.view", "loader.wagons"])


@pytest.fixture
def bot_settings():
    """Бот включён в журнале, Джин-Син допущен."""
    row = TelegramBotSettings.load()
    row.enabled = True
    row.allowed_usernames = [JIN]
    row.save()
    return row


@pytest.fixture
def dinara_started():
    """Динара написала боту /start — у бота есть её личный чат."""
    return BotChat.objects.create(
        chat_id=DINARA_CHAT, chat_type="private", title="Динара", username=DINARA, last_message_at=timezone.now())


@pytest.fixture
def bot_on(settings, bot_settings, dinara_started):
    """Бот включён на сервере и жив, отчёты о вагонах — Динаре."""
    settings.TELEGRAM_BOT_ENABLED = True
    bot_settings.report_recipients = [DINARA]
    bot_alive(bot_settings).save()
    return bot_settings


@pytest.fixture
def bot_user():
    return ensure_bot_user()


@pytest.fixture
def receive(bot_settings, bot_user):
    """Бот принял сообщение и прошёл очередь."""

    def _receive(message):
        stored = messages.ingest(message)
        messages.process_pending(user=bot_user, bot_settings=bot_settings)
        stored.refresh_from_db()
        return stored

    return _receive


@pytest.fixture
def unknown_code_message(receive, client, product, price):
    """Отчёт владельца «на проверке»: код товара «Д1с» боту неизвестен."""
    ProductAlias.objects.all().delete()
    message = receive(incoming(OWNER_REPORT))
    assert message.status == "needs_review"
    return message
