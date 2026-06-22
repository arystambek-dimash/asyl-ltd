import json
import re

DEFAULT_TEMPLATE = ('{"decision": "{{decision}}", "allowed": {{allowed}}, '
                    '"order_id": {{order_id}}, "reason": "{{reason}}"}')

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render_value(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    # строки: JSON-экранирование БЕЗ окружающих кавычек
    # (автор шаблона сам оборачивает строковые плейсхолдеры в кавычки)
    return json.dumps(str(v))[1:-1]


def render_template(template: str, ctx: dict) -> dict:
    tpl = template.strip() or DEFAULT_TEMPLATE

    def repl(m):
        return _render_value(ctx.get(m.group(1)))

    rendered = _PLACEHOLDER.sub(repl, tpl)
    try:
        return json.loads(rendered)
    except json.JSONDecodeError as e:
        raise ValueError(f"Шаблон даёт некорректный JSON: {e}")
