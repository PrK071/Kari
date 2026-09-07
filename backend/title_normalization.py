from __future__ import annotations

import hashlib
import re
import unicodedata
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_MULTIPLICATION_MARKS = str.maketrans({"×": "x", "✕": "x", "✖": "x"})


def normalize_match_text(value: object) -> str:
    """Normalize titles without discarding their original stored spelling.

    Accents and punctuation are search-insensitive, Unicode letters/numbers are
    preserved, and multiplication marks commonly used in manga titles match x.
    """
    text = unicodedata.normalize("NFKC", unescape(str(value or "")))
    text = text.translate(_MULTIPLICATION_MARKS).casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = "".join(char if char.isalnum() else " " for char in text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_aliases(values: object) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values] if values else []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_match_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def source_identifier(source_url: object) -> str:
    value = str(source_url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value.rstrip("/")
    hostname = (parsed.hostname or "").casefold()
    port = parsed.port
    default_port = (parsed.scheme.casefold() == "https" and port == 443) or (
        parsed.scheme.casefold() == "http" and port == 80
    )
    authority = hostname if not port or default_port else f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((parsed.scheme.casefold(), authority, path, query, ""))


def stable_catalog_id(provider: object, identifier: object) -> str:
    material = f"{str(provider or '').strip().casefold()}\0{str(identifier or '').strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
