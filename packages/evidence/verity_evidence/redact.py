"""Redaction, applied before anything is persisted.

Fail-closed: a field that looks like a secret is redacted even when the
detector is unsure. Losing a value from an evidence record is recoverable;
writing a credential to disk is not.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

#: Field names whose values are never stored, regardless of content.
SENSITIVE_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"pass(word|wd|phrase)", r"secret", r"^token$", r"_token$", r"^api[_-]?key$",
        r"authorization", r"^auth$", r"cookie", r"session[_-]?id", r"credential",
        r"private[_-]?key", r"client[_-]?secret", r"refresh[_-]?token",
        r"access[_-]?token", r"^cvv$", r"^cvc$", r"card[_-]?number", r"^pan$",
        r"ssn", r"social[_-]?security", r"^pin$", r"otp",
    )
)

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_VENDOR_KEY = re.compile(r"\b(sk|pk|rk|ghp|gho|ghs|xox[bapsr])[-_][A-Za-z0-9_-]{16,}\b")
_LONG_DIGITS = re.compile(r"\b\d[\d \-]{11,22}\d\b")


def looks_sensitive_key(key: str) -> bool:
    return any(p.search(key) for p in SENSITIVE_KEY_PATTERNS)


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or not digits.isdigit():
        return False
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def redact_text(text: str) -> tuple[str, bool]:
    """Redact secret-shaped substrings. Returns the text and whether it changed."""
    original = text
    for pattern in (_PRIVATE_KEY, _JWT, _BEARER, _VENDOR_KEY):
        text = pattern.sub(REDACTED, text)

    def _maybe_card(match: re.Match[str]) -> str:
        digits = re.sub(r"[ \-]", "", match.group(0))
        return REDACTED if _luhn_ok(digits) else match.group(0)

    text = _LONG_DIGITS.sub(_maybe_card, text)
    return text, text != original


def redact(value: Any, _key: str | None = None) -> tuple[Any, bool]:
    """Recursively redact a JSON-shaped value.

    Returns ``(redacted_value, was_redacted)``.
    """
    if _key is not None and looks_sensitive_key(_key):
        return REDACTED, True

    if isinstance(value, str):
        return redact_text(value)

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            new_value, item_changed = redact(item, str(key))
            out[str(key)] = new_value
            changed = changed or item_changed
        return out, changed

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = []
        changed = False
        for item in value:
            new_value, item_changed = redact(item)
            items.append(new_value)
            changed = changed or item_changed
        return items, changed

    return value, False
