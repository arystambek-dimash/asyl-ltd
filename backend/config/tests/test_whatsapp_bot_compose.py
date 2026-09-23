"""WhatsApp-бот в проде: отдельный процесс с healthcheck, ключи необязательны и только у него."""
import re
from pathlib import Path

from config.tests.test_passage_scale_compose import _service_block

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")


def test_whatsapp_bot_is_a_single_health_checked_backend_process():
    bot = _service_block(COMPOSE, "whatsapp-bot")

    assert COMPOSE.count("\n  whatsapp-bot:\n") == 1
    assert "<<: *backend-environment" in bot
    assert "APP_SERVICE: whatsapp-bot" in bot
    assert "entrypoint: []" in bot
    assert 'command: ["python", "manage.py", "run_whatsapp_bot"]' in bot
    assert "backend:\n        condition: service_healthy" in bot
    assert "init: true" in bot
    assert 'test: ["CMD", "python", "/app/whatsapp_bot_healthcheck.py"]' in bot
    assert "- /tmp/whatsapp-bot:rw,noexec,nosuid,nodev,size=1m,mode=1777" in bot
    assert "WHATSAPP_BOT_HEARTBEAT_FILE: /tmp/whatsapp-bot/heartbeat.json" in bot
    assert "restart: unless-stopped" in bot
    assert "logging: *default-logging" in bot
    assert "ports:" not in bot
    # База и выход в интернет к провайдеру; публичного вебхука нет.
    assert "      - data\n      - default" in bot


def test_whatsapp_bot_env_is_optional_and_secrets_stay_with_the_bot():
    bot = _service_block(COMPOSE, "whatsapp-bot")
    anchor = COMPOSE.split("x-default-logging:", 1)[0]

    assert not re.search(r"WHATSAPP_BOT_\w+:\?", COMPOSE)
    assert "WHATSAPP_BOT_ENABLED: ${WHATSAPP_BOT_ENABLED:-0}" in anchor
    for name in ("WHATSAPP_BOT_API_URL", "WHATSAPP_BOT_INSTANCE_ID", "WHATSAPP_BOT_API_TOKEN", "WHATSAPP_BOT_LLM_MODEL"):
        assert f"{name}: ${{{name}:-}}" in bot
        assert name not in anchor


def test_env_example_documents_the_bot():
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    for name in ("WHATSAPP_BOT_ENABLED=0", "WHATSAPP_BOT_INSTANCE_ID=", "WHATSAPP_BOT_API_TOKEN=",
                 "WHATSAPP_BOT_LLM_ENABLED=0"):
        assert name in example
