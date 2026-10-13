"""Цикл ассистента: шаги и карта данных, итоговый ответ, таблица с графиком, статистика."""

from unittest.mock import patch

import pytest

from apps.assistant import agent
from apps.assistant.queries import QueryResult
from apps.common.openai_responses import Completed

from .conftest import drive, final, reasoning, tool_call

pytestmark = pytest.mark.django_db

USAGE = {"input_tokens": 1000, "cached_tokens": 800, "output_tokens": 50, "reasoning_tokens": 20}


@pytest.fixture
def user(make_user):
    return make_user("analyst")


def _responses(*outputs):
    return patch.object(
        agent.openai_responses, "request_completed",
        side_effect=[Completed(list(output), f"resp_{index}", dict(USAGE)) for index, output in enumerate(outputs)],
    )


def _steps(events):
    """Последнее состояние каждого шага по его id."""
    steps = {}
    for event in events:
        if event["type"] == "step":
            steps[event["step"]["id"]] = event["step"]
    return list(steps.values())


def test_sql_tool_feeds_the_model_and_the_shown_table_comes_from_the_query(user):
    sql = "SELECT o.status, count(*) n FROM orders_order o JOIN clients_client c ON c.id = o.client_id GROUP BY 1"
    result = QueryResult(columns=["status", "n"], rows=[["shipped", 3], ["cancelled", 1]], truncated=False)
    trace = []
    with _responses(
        [reasoning(), tool_call("run_sql", {"purpose": "Считаю заказы по статусам", "sql": sql})],
        final("Отгружено 3 заказа.", show_query=1, chart={"type": "bar", "x": "status", "y": "n"}),
    ) as request, patch.object(agent, "run_select", return_value=result) as run_select:
        events, answer = drive(agent.run(user, "Сколько заказов?", [], trace))

    run_select.assert_called_once_with(sql)
    assert answer.text == "Отгружено 3 заказа."
    assert answer.table == {
        "columns": ["status", "n"],
        "rows": [["shipped", 3], ["cancelled", 1]],
        "truncated": False,
        "chart": {"type": "bar", "x": "status", "y": "n"},
    }
    steps = _steps(events)
    assert [(step["kind"], step["status"]) for step in steps] == [
        ("think", "done"), ("sql", "done"), ("think", "done"),
    ]
    assert steps[1]["tables"] == ["orders_order", "clients_client"]
    assert steps[1]["row_count"] == 2 and steps[1]["preview"]["rows"] == [["shipped", 3], ["cancelled", 1]]
    assert steps[2]["text"] == "Формулирую ответ"
    assert trace == steps
    # Карта данных: затронутые таблицы и внешний ключ заказа на клиента.
    assert {"type": "map", "tables": ["orders_order", "clients_client"],
            "links": [["orders_order", "clients_client"]]} in events
    assert answer.stats["model_calls"] == 2 and answer.stats["sql_queries"] == 1
    assert answer.stats["input_tokens"] == 2000 and answer.stats["cached_tokens"] == 1600

    first, second = (call.args[0] for call in request.call_args_list)
    assert first["store"] is False and first["tool_choice"] == "auto"
    assert first["include"] == ["reasoning.encrypted_content"]
    assert first["prompt_cache_key"] == "asyl-assistant"
    assert first["input"] == [{"role": "user", "content": "Сколько заказов?"}]
    # Рассуждение и вызов возвращаются модели без поля status, затем — результат инструмента.
    assert second["input"][1] == {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
    assert "status" not in second["input"][2]
    assert second["input"][3]["type"] == "function_call_output"
    assert second["input"][3]["call_id"] == "call_1"
    assert '"shipped"' in second["input"][3]["output"]


@pytest.mark.parametrize("chart", [
    {"type": "bar", "x": "status", "y": "status"},
    {"type": "bar", "x": "status", "y": "missing"},
    {"type": "line", "x": "n", "y": "status"},
    {"type": "none", "x": "", "y": ""},
    {"type": "pie", "x": "status", "y": "n"},
])
def test_chart_needs_real_columns_and_numbers(user, chart):
    result = QueryResult(columns=["status", "n"], rows=[["shipped", 3]], truncated=False)
    with _responses(
        [tool_call("run_sql", {"purpose": "Счёт", "sql": "SELECT status, count(*) n FROM orders_order GROUP BY 1"})],
        final("Ответ.", show_query=1, chart=chart),
    ), patch.object(agent, "run_select", return_value=result):
        _events, answer = drive(agent.run(user, "Вопрос", [], []))

    assert "chart" not in answer.table


def test_rejected_sql_goes_back_to_the_model_as_an_error(user):
    trace = []
    with _responses(
        [tool_call("run_sql", {"purpose": "Пароли", "sql": "SELECT password FROM accounts_user"})],
        final("Пароли недоступны."),
    ) as request:
        _events, answer = drive(agent.run(user, "Покажи пароли", [], trace))

    tool_output = request.call_args_list[1].args[0]["input"][-1]["output"]
    assert tool_output.startswith("Ошибка: Столбец password секретный")
    sql_step = next(step for step in trace if step["kind"] == "sql")
    assert sql_step["status"] == "error" and sql_step["error"].startswith("Столбец password")
    assert answer.table is None


def test_schema_tools_report_steps_and_the_data_map(user):
    with _responses(
        [
            tool_call("search_schema", {"query": "возврат"}, "call_1"),
            tool_call("describe_tables", {"tables": ["orders_goodsreturn", "clients_client"]}, "call_2"),
        ],
        final("Возвраты лежат в orders_goodsreturn."),
    ) as request:
        events, _answer = drive(agent.run(user, "Где возвраты?", [], []))

    steps = _steps(events)
    assert [step["kind"] for step in steps] == ["think", "search", "schema", "think"]
    assert steps[1]["text"] == "Ищу в справочнике: возврат" and steps[1]["found"] >= 1
    assert steps[2]["tables"] == ["orders_goodsreturn", "clients_client"]
    assert {"type": "map", "tables": ["orders_goodsreturn", "clients_client"],
            "links": [["orders_goodsreturn", "clients_client"]]} in events
    outputs = [item["output"] for item in request.call_args_list[1].args[0]["input"] if item.get("type") == "function_call_output"]
    assert "orders_goodsreturn" in outputs[0]
    assert "- status varchar(10)" in outputs[1]


def test_show_query_out_of_range_shows_no_table(user):
    with _responses(final("Ответ без таблицы.", show_query=5)):
        _events, answer = drive(agent.run(user, "Вопрос", [], []))

    assert answer.table is None


def test_last_call_closes_tools_and_endless_tool_use_fails(user):
    endless = [[tool_call("search_schema", {"query": "заказ"})] for _ in range(agent.MODEL_CALL_LIMIT)]
    trace = []
    with _responses(*endless) as request, pytest.raises(agent.AssistantError) as caught:
        drive(agent.run(user, "Вопрос", [], trace))

    assert caught.value.code == "assistant_step_limit"
    assert request.call_count == agent.MODEL_CALL_LIMIT
    assert request.call_args_list[-1].args[0]["tool_choice"] == "none"
    assert trace, "шаги остаются для журнала даже при сбое"


def test_without_reasoning_effort_no_reasoning_parameters_are_sent(user, settings):
    settings.ASSISTANT_AI_REASONING_EFFORT = ""
    with _responses(final("Готово.")) as request:
        drive(agent.run(user, "Вопрос", [], []))

    body = request.call_args.args[0]
    assert "reasoning" not in body and "include" not in body


def test_model_and_effort_can_be_overridden_for_evaluation(user):
    with _responses(final("Готово.")) as request:
        _events, answer = drive(agent.run(user, "Вопрос", [], [], model="gpt-other", effort="low"))

    body = request.call_args.args[0]
    assert body["model"] == "gpt-other" and body["reasoning"] == {"effort": "low"}
    assert answer.stats["model"] == "gpt-other"


def test_instructions_carry_rules_statuses_and_tables(user):
    with _responses(final("Готово.")) as request:
        drive(agent.run(user, "Вопрос", [], []))

    instructions = request.call_args.args[0]["instructions"]
    assert f"accounts_user.id = {user.pk}" in instructions
    assert "`shipped` — в интерфейсе «Отгружено»" in instructions
    assert "deleted_at IS NULL" in instructions
    assert "orders_order (orders.Order)" in instructions
    assert "assistant_assistantchat" not in instructions
