import json

import pytest


def tool_call(name, arguments, call_id="call_1"):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
        "status": "completed",
    }


def reasoning():
    return {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}


def final(answer, show_query=None, chart=None):
    chart = chart or {"type": "none", "x": "", "y": ""}
    text = json.dumps({"answer": answer, "show_query": show_query, "chart": chart}, ensure_ascii=False)
    return [{
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }]


def drive(generator):
    """События генератора и его итоговое значение."""
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            return events, stop.value


@pytest.fixture(autouse=True)
def assistant_settings(settings):
    settings.OPENAI_API_KEY = "unit-test-only"
    settings.ASSISTANT_AI_MODEL = "gpt-test"
    settings.ASSISTANT_AI_REASONING_EFFORT = "medium"
    settings.ASSISTANT_MAX_DAILY_QUESTIONS = 100
