"""Telegram-бот в проде: отдельный процесс с healthcheck, токен необязателен и только у него."""
import re

from django.conf import settings

from config.tests.compose_files import REPO_ROOT, read_compose, service_block

COMPOSE = read_compose("docker-compose.prod.yml")


def test_telegram_bot_is_a_single_health_checked_backend_process():
    bot = service_block(COMPOSE, "telegram-bot")

    assert COMPOSE.count("\n  telegram-bot:\n") == 1
    assert "\n  whatsapp-bot:\n" not in COMPOSE
    assert "<<: *backend-environment" in bot
    assert "APP_SERVICE: telegram-bot" in bot
    assert "entrypoint: []" in bot
    assert 'command: ["python", "manage.py", "run_telegram_bot"]' in bot
    assert "backend:\n        condition: service_healthy" in bot
    assert "init: true" in bot
    assert 'test: ["CMD", "python", "/app/telegram_bot_healthcheck.py"]' in bot
    # Путь heartbeat задают settings; compose только монтирует его каталог.
    heartbeat_dir = settings.TELEGRAM_BOT_HEARTBEAT_FILE.rsplit("/", 1)[0]
    assert f"- {heartbeat_dir}:rw,noexec,nosuid,nodev,size=1m,mode=1777" in bot
    assert "TELEGRAM_BOT_HEARTBEAT_FILE" not in COMPOSE
    assert "restart: unless-stopped" in bot
    assert "logging: *default-logging" in bot
    assert "ports:" not in bot
    # База и выход в интернет к Telegram; публичного вебхука нет.
    assert "      - data\n      - default" in bot


def test_telegram_bot_env_is_optional_and_the_token_stays_with_the_bot():
    bot = service_block(COMPOSE, "telegram-bot")
    anchor = COMPOSE.split("x-default-logging:", 1)[0]

    assert not re.search(r"TELEGRAM_BOT_\w+:\?", COMPOSE)
    assert "TELEGRAM_BOT_ENABLED: ${TELEGRAM_BOT_ENABLED:-0}" in anchor
    for name in ("TELEGRAM_BOT_API_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_LLM_MODEL"):
        assert f"{name}: ${{{name}:-}}" in bot
        # Токен и адрес — только у бота (CAMERA_ALERT_TELEGRAM_BOT_TOKEN — другой бот).
        assert not re.search(rf"^\s+{name}:", anchor, re.MULTILINE)


def test_env_example_documents_the_bot():
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    for name in ("TELEGRAM_BOT_ENABLED=0", "TELEGRAM_BOT_TOKEN=", "TELEGRAM_BOT_LLM_ENABLED=0"):
        assert name in example
    assert "WHATSAPP_BOT_" not in example
