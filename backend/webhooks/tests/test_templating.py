import pytest
from webhooks.templating import render_template


def test_substitutes_bool_and_number_unquoted():
    out = render_template('{"open": {{allowed}}, "order": {{order_id}}}',
                          {"allowed": True, "order_id": 42})
    assert out == {"open": True, "order": 42}


def test_string_placeholder_escaped():
    out = render_template('{"msg": "{{reason}}"}', {"reason": 'нет "заказа"'})
    assert out == {"msg": 'нет "заказа"'}


def test_none_renders_null():
    out = render_template('{"order": {{order_id}}}', {"order_id": None})
    assert out == {"order": None}


def test_empty_template_uses_default():
    out = render_template("", {"decision": "allow", "allowed": True,
                               "order_id": 7, "reason": ""})
    assert out["decision"] == "allow" and out["allowed"] is True


def test_invalid_json_raises():
    with pytest.raises(ValueError):
        render_template('{"x": {{reason}}}', {"reason": "unquoted text"})
