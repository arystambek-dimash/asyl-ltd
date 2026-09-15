import pytest

from apps.common.crypto import SecretDecryptError, decrypt_secret, encrypt_secret


def test_roundtrip_and_distinct_tokens(settings):
    settings.SECRET_KEY = "a" * 60
    first = encrypt_secret("apipay-live-key")
    second = encrypt_secret("apipay-live-key")
    assert first != second
    assert "apipay-live-key" not in first
    assert decrypt_secret(first) == "apipay-live-key"


def test_foreign_key_fails_and_fallback_keys_still_decrypt(settings):
    settings.SECRET_KEY = "old-key" * 10
    settings.SECRET_KEY_FALLBACKS = []
    token = encrypt_secret("secret")
    settings.SECRET_KEY = "new-key" * 10
    with pytest.raises(SecretDecryptError):
        decrypt_secret(token)
    settings.SECRET_KEY_FALLBACKS = ["old-key" * 10]
    assert decrypt_secret(token) == "secret"


def test_garbage_token_is_reported_not_crashed():
    with pytest.raises(SecretDecryptError):
        decrypt_secret("not-a-token")


def test_empty_values():
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""
