"""API ассистента: права, свои разговоры, поток ответа, лимиты и сбои."""

import json
from unittest.mock import patch

import pytest

from apps.assistant import agent, services
from apps.assistant.models import AssistantChat, AssistantMessage
from apps.common.openai_responses import Completed, OpenAIResponseError

from .conftest import final, tool_call

ASK_URL = "/api/assistant/ask/"
CHATS_URL = "/api/assistant/chats/"


@pytest.fixture
def analyst(user_with_perms):
    return user_with_perms("analyst", ["assistant.view"])


def _model(*outputs):
    return patch.object(
        agent.openai_responses, "request_completed",
        side_effect=[Completed(list(output), "resp") if isinstance(output, list) else output for output in outputs],
    )


def _events(response):
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/x-ndjson")
    assert response["X-Accel-Buffering"] == "no"
    body = b"".join(response.streaming_content).decode()
    return [json.loads(line) for line in body.splitlines()]


@pytest.mark.django_db
def test_page_requires_the_assistant_permission(auth_client, user_with_perms):
    client = auth_client(user_with_perms("cashier", ["payments.view"]))

    assert client.get(CHATS_URL).status_code == 403
    assert client.post(ASK_URL, {"question": "Долги?"}, format="json").status_code == 403


@pytest.mark.django_db
def test_without_openai_key_the_assistant_is_unavailable(auth_client, analyst, settings):
    settings.OPENAI_API_KEY = ""

    response = auth_client(analyst).post(ASK_URL, {"question": "Долги?"}, format="json")

    assert response.status_code == 503
    assert response.json()["code"] == "assistant_unavailable"
    assert not AssistantMessage.objects.exists()


@pytest.mark.django_db
def test_question_streams_start_and_reply_and_is_saved(auth_client, analyst):
    with _model(final("Долгов нет.")):
        events = _events(auth_client(analyst).post(ASK_URL, {"question": "  Есть долги?  "}, format="json"))

    chat = AssistantChat.objects.get(user=analyst)
    assert chat.title == "Есть долги?"
    assert events[0]["type"] == "start"
    assert events[0]["chat"]["id"] == chat.id
    assert events[0]["question"]["content"] == "Есть долги?"
    assert events[-1]["type"] == "reply"
    assert events[-1]["message"]["content"] == "Долгов нет."
    assert [(m.role, m.content) for m in chat.messages.all()] == [("user", "Есть долги?"), ("assistant", "Долгов нет.")]


@pytest.mark.django_db
def test_follow_up_sends_previous_turns_with_their_sql(auth_client, analyst):
    chat = AssistantChat.objects.create(user=analyst, title="Заказы")
    AssistantMessage.objects.create(chat=chat, role="user", content="Сколько заказов?")
    AssistantMessage.objects.create(
        chat=chat, role="assistant", content="5 заказов.",
        trace=[
            {"id": 1, "kind": "sql", "status": "error", "sql": "SELECT broken"},
            {"id": 2, "kind": "sql", "status": "done", "sql": "SELECT count(*) FROM orders_order"},
        ],
    )

    with _model(final("Вчера — 2.")) as request:
        _events(auth_client(analyst).post(ASK_URL, {"question": "А вчера?", "chat": chat.id}, format="json"))

    history = request.call_args.args[0]["input"]
    assert history[0] == {"role": "user", "content": "Сколько заказов?"}
    assert history[1]["role"] == "assistant"
    assert "SELECT count(*) FROM orders_order" in history[1]["content"]
    assert "SELECT broken" not in history[1]["content"]
    assert history[2] == {"role": "user", "content": "А вчера?"}


@pytest.mark.django_db
def test_chats_are_private(auth_client, analyst, user_with_perms):
    other = user_with_perms("other", ["assistant.view"])
    foreign = AssistantChat.objects.create(user=other, title="Чужой")
    own = AssistantChat.objects.create(user=analyst, title="Свой")
    client = auth_client(analyst)

    assert [chat["id"] for chat in client.get(CHATS_URL).json()] == [own.id]
    assert client.get(f"{CHATS_URL}{foreign.id}/").status_code == 404
    assert client.delete(f"{CHATS_URL}{foreign.id}/").status_code == 404
    assert client.post(ASK_URL, {"question": "Что там?", "chat": foreign.id}, format="json").status_code == 404
    assert client.delete(f"{CHATS_URL}{own.id}/").status_code == 204
    assert list(AssistantChat.objects.all()) == [foreign]


@pytest.mark.django_db
def test_chat_detail_lists_messages_in_order(auth_client, analyst):
    chat = AssistantChat.objects.create(user=analyst, title="Склад")
    AssistantMessage.objects.create(chat=chat, role="user", content="Остатки?")
    AssistantMessage.objects.create(
        chat=chat, role="assistant", content="10 мешков.", table={"columns": ["bags"], "rows": [[10]]},
        trace=[
            {"id": 1, "kind": "schema", "status": "done", "tables": ["warehouse_stockitem", "unknown_table"]},
            {"id": 2, "kind": "sql", "status": "done", "tables": ["warehouse_stockitem", "catalog_product"]},
        ],
    )

    data = auth_client(analyst).get(f"{CHATS_URL}{chat.id}/").json()

    assert [message["content"] for message in data["messages"]] == ["Остатки?", "10 мешков."]
    assert data["messages"][1]["table"] == {"columns": ["bags"], "rows": [[10]]}
    # Карта для повтора хода работы: таблицы каталога из шагов и внешние ключи между ними.
    assert data["messages"][1]["data_map"] == {
        "tables": ["warehouse_stockitem", "catalog_product"],
        "links": [["warehouse_stockitem", "catalog_product"]],
    }


@pytest.mark.django_db
def test_daily_question_limit(auth_client, analyst, settings):
    settings.ASSISTANT_MAX_DAILY_QUESTIONS = 1
    client = auth_client(analyst)
    with _model(final("Первый ответ.")):
        _events(client.post(ASK_URL, {"question": "Первый"}, format="json"))

    response = client.post(ASK_URL, {"question": "Второй"}, format="json")

    assert response.status_code == 429
    assert response.json()["code"] == "assistant_daily_limit"


@pytest.mark.django_db
def test_one_answer_at_a_time_per_user_and_a_cap_for_everyone(auth_client, analyst, user_with_perms):
    client = auth_client(analyst)
    held = services.claim_answer_slot(analyst)
    try:
        response = client.post(ASK_URL, {"question": "Ещё?"}, format="json")
    finally:
        held.release()
    assert response.status_code == 429
    assert response.json()["code"] == "assistant_user_busy"

    others = [
        services.claim_answer_slot(user_with_perms(f"busy-{index}", ["assistant.view"]))
        for index in range(services.PARALLEL_ANSWERS)
    ]
    try:
        response = client.post(ASK_URL, {"question": "Ещё?"}, format="json")
    finally:
        for slot in others:
            slot.release()
    assert response.status_code == 429
    assert response.json()["code"] == "assistant_busy"
    assert not AssistantMessage.objects.exists()

    # Отданный до конца поток освобождает место: следующий вопрос проходит.
    with _model(final("Первый.")):
        _events(client.post(ASK_URL, {"question": "Первый"}, format="json"))
    with _model(final("Второй.")):
        assert _events(client.post(ASK_URL, {"question": "Второй"}, format="json"))[-1]["message"]["content"] == "Второй."


@pytest.mark.django_db
def test_provider_failure_is_saved_as_a_readable_error(auth_client, analyst):
    with _model(OpenAIResponseError("openai_rate_limited", retryable=True)):
        events = _events(auth_client(analyst).post(ASK_URL, {"question": "Долги?"}, format="json"))

    reply = events[-1]["message"]
    assert reply["error"] == "openai_rate_limited"
    assert "перегружен" in reply["content"]


@pytest.mark.django_db(transaction=True)
def test_end_to_end_sql_runs_read_only_and_becomes_the_answer_table(auth_client, analyst):
    sql = "SELECT count(*) AS orders FROM orders_order WHERE deleted_at IS NULL"
    with _model(
        [tool_call("run_sql", {"purpose": "Считаю живые заказы", "sql": sql})],
        final("Заказов пока нет.", show_query=1),
    ):
        events = _events(auth_client(analyst).post(ASK_URL, {"question": "Сколько заказов?"}, format="json"))

    assert events[0]["type"] == "start" and events[-1]["type"] == "reply"
    assert {"type": "map", "tables": ["orders_order"], "links": []} in events
    reply = events[-1]["message"]
    assert reply["table"] == {"columns": ["orders"], "rows": [[0]], "truncated": False}
    sql_step = next(step for step in reply["trace"] if step["kind"] == "sql")
    assert (sql_step["text"], sql_step["sql"], sql_step["status"], sql_step["row_count"]) == (
        "Считаю живые заказы", sql, "done", 1,
    )
    assert reply["stats"]["sql_queries"] == 1 and reply["stats"]["total_ms"] >= 0
