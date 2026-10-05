# 来源：jet-demo d13aa43 core/security_utils.py::mask_sensitive_data
import logging
import re
from typing import Any

API_KEY_PATTERNS = [
    # Bearer tokens (including Authorization headers)
    (re.compile(r'(Bearer\s+)[a-zA-Z0-9_\-\.\*]{8,}'), r'\1***'),
    # sk-... keys (DeepSeek, OpenAI, etc.)
    (re.compile(r'(sk-[a-zA-Z0-9_\-]{3})[a-zA-Z0-9_\-]{8,}([a-zA-Z0-9_\-]{3})'), r'\1***\2'),
    (re.compile(r'(sk-[a-zA-Z0-9_\-]{6,})'), r'sk-***'),
    # Generic key/secret assignments in logs
    (re.compile(r'((?:api[_-]?key|secret|token|password)\s*[:=]\s*["\']?)[a-zA-Z0-9_\-]{6,}(["\']?)', re.IGNORECASE), r'\1***\2'),
]


def mask_sensitive_data(text: Any) -> str:
    """Mask sensitive keys, tokens, and credentials in text."""
    if text is None:
        return ""
    val = str(text)
    for pattern, replacement in API_KEY_PATTERNS:
        val = pattern.sub(replacement, val)
    return val


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts API keys and sensitive tokens."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg:
            try:
                msg_str = record.getMessage()
            except Exception:
                msg_str = str(record.msg)
            record.msg = mask_sensitive_data(msg_str)
            record.args = ()
        return True


def get_logger(name: str) -> logging.Logger:
    """Obtain a logger with SensitiveDataFilter applied."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, SensitiveDataFilter) for f in logger.filters):
        logger.addFilter(SensitiveDataFilter())
    return logger
