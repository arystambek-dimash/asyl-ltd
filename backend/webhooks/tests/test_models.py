from webhooks.models import Camera


def test_generate_key_is_unique_and_long():
    a, b = Camera.generate_key(), Camera.generate_key()
    assert a != b
    assert len(a) >= 24
