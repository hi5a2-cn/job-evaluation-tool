import logging
import pytest
from jet.logging import get_logger, mask_sensitive_data


def test_mask_deepseek_hex_key():
    key = "sk-0123456789abcdef0123456789abcdef"
    text = f"API error occurred while calling DeepSeek: invalid key {key} provided"
    masked = mask_sensitive_data(text)
    assert key not in masked
    assert "sk-012***def" in masked


def test_mask_openai_key():
    key = "sk-proj-1234567890abcdef1234567890"
    text = f"Failed to authenticate with OpenAI: {key}"
    masked = mask_sensitive_data(text)
    assert key not in masked
    assert "sk-pro***890" in masked


def test_mask_bearer_token_and_header():
    # Bearer with sk- key
    key = "sk-0123456789abcdef0123456789abcdef"
    header1 = f"Authorization: Bearer {key}"
    masked1 = mask_sensitive_data(header1)
    assert key not in masked1
    assert "Authorization: Bearer ***" in masked1

    # Bearer with generic secret token
    generic_token = "secret_auth_token_987654321"
    header2 = f"Authorization: Bearer {generic_token}"
    masked2 = mask_sensitive_data(header2)
    assert generic_token not in masked2
    assert "Authorization: Bearer ***" in masked2


def test_mask_exception_traceback():
    key = "sk-test-1234567890abcdef1234"
    traceback_str = (
        "Traceback (most recent call last):\n"
        '  File "/src/jet/llm/client.py", line 42, in chat\n'
        f"    response = await client.post(url, headers={{'Authorization': 'Bearer {key}'}})\n"
        f"httpx.HTTPStatusError: 401 Client Error: Unauthorized with key {key}"
    )
    masked = mask_sensitive_data(traceback_str)
    assert key not in masked
    assert "Bearer ***" in masked


def test_caplog_and_logger_integration(caplog):
    logger = get_logger("jet.test_mask")
    fake_key = "sk-test-1234567890abcdef"

    with caplog.at_level(logging.INFO):
        logger.info("Attempting request with key %s and header Bearer %s", fake_key, fake_key)
        logger.warning("Error with token %s", fake_key)

    for record in caplog.records:
        assert fake_key not in record.message
        assert fake_key not in record.getMessage()

    assert fake_key not in caplog.text
