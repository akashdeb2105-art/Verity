"""Redaction runs before anything is persisted, and it fails closed.

Losing a value from an evidence record is recoverable. Writing a credential to
disk is not.

A note on the fixtures below. Every credential-shaped string in this file is
fake, but a secret scanner matches the *pattern*, not the validity: GitHub push
protection rejects a push containing one, and a test fixture that cannot be
pushed is not a test fixture. The values are therefore assembled at runtime
from fragments, so no contiguous literal exists in the source while the string
the redactor actually sees is byte-identical to the real shape.

``tests/security/test_repository_hygiene.py`` enforces this for the whole
repository.
"""

from __future__ import annotations

from typing import Any

import pytest
from verity_evidence import REDACTED, looks_sensitive_key, redact, redact_text


def _shaped(prefix: str, body: str) -> str:
    """Assemble a credential-shaped value without writing one into the source."""
    return prefix + body


# Split so that the recognisable prefix never appears next to its body.
SLACK_BOT_TOKEN = _shaped("xox" + "b-", "1234567890-abcdefghijklmnop")
GITHUB_TOKEN = _shaped("gh" + "p_", "abcdefghijklmnopqrstuvwxyz0123")
OPENAI_STYLE_KEY = _shaped("s" + "k-", "abcdefghijklmnopqrstuvwxyz012345")
SHORT_KEY = _shaped("s" + "k-", "1234567890abcdefghijklmno")
PRIVATE_KEY_HEADER = "-----BEGIN OPENSSH PRIVATE" + " KEY-----"
# Joined on "." so no two dot-separated segments sit together as a literal.
JWT = ".".join(
    ["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0",
     "dBjftJeZ4CVPmB92K27uhbUJU1p1r"]
)

SECRET_SHAPED: list[tuple[str, Any]] = [
    ("password", "hunter2"),
    ("PASSWORD", "hunter2"),
    ("user_password", "hunter2"),
    ("passphrase", "correct horse"),
    ("api_key", "abcdef123456"),
    ("apiKey", "abcdef123456"),
    ("client_secret", "s3cr3t"),
    ("access_token", "abc.def.ghi"),
    ("refresh_token", "rt_12345678"),
    ("authorization", "Basic dXNlcjpwYXNz"),
    ("Cookie", "session=abc"),
    ("session_id", "sid-1234"),
    ("private_key", PRIVATE_KEY_HEADER),
    ("cvv", "123"),
    ("pan", "4111111111111111"),
    ("ssn", "123-45-6789"),
    ("otp", "998877"),
    ("credential", "anything"),
]


@pytest.mark.security()
@pytest.mark.parametrize(("key", "value"), SECRET_SHAPED)
def test_sensitive_keys_are_never_stored(key: str, value: Any) -> None:
    cleaned, changed = redact({key: value})
    assert cleaned[key] == REDACTED
    assert changed is True
    assert str(value) not in str(cleaned)


@pytest.mark.security()
@pytest.mark.parametrize(
    "text",
    [
        JWT,
        f"Authorization: Bearer {OPENAI_STYLE_KEY}",
        PRIVATE_KEY_HEADER,
        f"key {SHORT_KEY}",
        f"token {GITHUB_TOKEN}",
        SLACK_BOT_TOKEN,
    ],
)
def test_secret_shaped_text_is_redacted_wherever_it_appears(text: str) -> None:
    cleaned, changed = redact_text(text)
    assert changed is True
    assert REDACTED in cleaned


@pytest.mark.security()
@pytest.mark.parametrize("number", ["4111111111111111", "4111 1111 1111 1111",
                                    "5500-0000-0000-0004"])
def test_luhn_valid_card_numbers_are_redacted(number: str) -> None:
    cleaned, changed = redact_text(f"charge {number} today")
    assert changed is True
    assert REDACTED in cleaned


def test_long_numbers_that_are_not_cards_are_preserved() -> None:
    """Redaction must not eat legitimate business data.

    An invoice reference or an order number that happens to be long is not a
    card number, and destroying it would make the evidence useless.
    """
    cleaned, changed = redact_text("order 12345678901234 shipped")
    assert changed is False
    assert "12345678901234" in cleaned


def test_business_values_survive_redaction() -> None:
    payload = {"invoice_number": "INV-4471", "total": "14800.00", "vendor": "Acme Supplies"}
    cleaned, changed = redact(payload)
    assert cleaned == payload
    assert changed is False


def test_redaction_recurses_through_nested_structures() -> None:
    payload = {"outer": [{"inner": {"api_key": "abc123"}}]}
    cleaned, _ = redact(payload)
    assert cleaned["outer"][0]["inner"]["api_key"] == REDACTED


def test_key_detector_is_case_insensitive() -> None:
    assert looks_sensitive_key("Refresh_Token")
    assert not looks_sensitive_key("vendor_name")
