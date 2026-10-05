import re
import unicodedata


def normalize_text(s: str | None) -> str:
    """Normalize text using NFKC, collapse whitespace, and strip. None becomes empty string."""
    if s is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(s))
    collapsed = re.sub(r"\s+", " ", normalized).strip()
    return collapsed


def normalize_city(s: str | None) -> str:
    """Normalize city name and remove trailing '市'."""
    text = normalize_text(s)
    return text.removesuffix("市")
