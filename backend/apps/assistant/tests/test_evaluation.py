"""Проверка качества: разбор чисел из ответа и сверка с эталоном."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.assistant import agent, evaluation
from apps.assistant.evaluation import Case, Expected, found_numbers, missing, run_case
from apps.common.openai_responses import Completed

from .conftest import final


def test_numbers_are_read_from_russian_text_and_table_cells():
    text = "Долг: 644 191 028,77 ₸ и 1 624 020 $; заказов 263."
    table = {"columns": ["n", "sum"], "rows": [[7, "12.50"], [None, "Мука"]]}

    assert found_numbers(text, table) == [
        Decimal("644191028.77"), Decimal("1624020"), Decimal("263"), Decimal("7"), Decimal("12.50"),
    ]


def test_money_may_be_rounded_to_a_tenge_but_counts_must_match():
    expected = Expected(numbers=(Decimal("644191028.77"), Decimal("263")), names=(("Osiyo", "ТОО Осиё"),))

    assert missing(expected, "Долг 644 191 029 ₸ у клиента osiyo, заказов 263", None) == []
    assert missing(expected, "Долг 644 191 029 ₸ у клиента Osiyo, заказов 264", None) == ["число 263"]
    assert missing(expected, "Заказов 263, долг 644191028.77", None) == ["имя «Osiyo / ТОО Осиё»"]


@pytest.mark.django_db
def test_run_case_scores_the_answer_and_keeps_speed_stats(make_user):
    case = Case("demo", "Сколько заказов?", lambda: Expected(numbers=(Decimal("0"),)))
    with patch.object(
        agent.openai_responses, "request_completed",
        return_value=Completed(final("Заказов: 0."), "resp", {"input_tokens": 900, "cached_tokens": 600}),
    ):
        result = run_case(case, make_user("judge"))

    assert result["ok"] is True and result["missing"] == []
    assert result["stats"]["model_calls"] == 1 and result["stats"]["cached_tokens"] == 600
    summary = evaluation.summary([result])
    assert summary["correct"] == 1 and summary["total"] == 1
    assert summary["cached_share"] == pytest.approx(0.67)
