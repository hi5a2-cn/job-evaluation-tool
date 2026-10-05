import logging
from jet.logging import get_logger, mask_sensitive_data


def test_mask_sensitive_data_original_cases():
    sample_error = (
        "openai.AuthenticationError: Error code: 401 - {'error': {'message': "
        "'Incorrect API key provided: sk-ant-api03-abcdef1234567890xyz. "
        "You can find your API key at https://platform.openai.com/account/api-keys.'}}"
    )
    masked = mask_sensitive_data(sample_error)
    assert "sk-ant-api03-abcdef1234567890xyz" not in masked
    assert "sk-ant***xyz" in masked

    bearer_msg = "Request headers: {'Authorization': 'Bearer my_secret_token_12345678'}"
    masked_bearer = mask_sensitive_data(bearer_msg)
    assert "my_secret_token_12345678" not in masked_bearer
    assert "Bearer ***" in masked_bearer

    config_msg = "api_key = 'sk-proj-1234567890abcdef'"
    assert "sk-proj-1234567890abcdef" not in mask_sensitive_data(config_msg)


def test_mask_sensitive_data_none():
    assert mask_sensitive_data(None) == ""


def test_get_logger_filter():
    logger = get_logger("test_masked_logger")
    records = []

    class MemoryHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = MemoryHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    secret_key = "sk-proj-9876543210zyxwvutsrq"
    logger.info("Connecting with key: %s", secret_key)

    assert len(records) == 1
    assert secret_key not in records[0]
    assert "***" in records[0]
