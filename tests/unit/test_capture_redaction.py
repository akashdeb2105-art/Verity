"""Redaction at the point of capture.

The adversarial suite below is the reason this layer exists. A recording is
made on a live system by a person who is not thinking about security, and every
case here is a shape a real login or payment screen actually uses.

Credential-shaped values are assembled at runtime, for the reason given in
``tests/unit/test_redaction.py``.
"""

from __future__ import annotations

import pytest
from verity_capture import RedactionReason, classify_field, redact_value
from verity_capture.redaction import redact_visible_text
from verity_evidence import REDACTED


def _shaped(prefix: str, body: str) -> str:
    return prefix + body


SLACK = _shaped("xox" + "b-", "1234567890-abcdefghijklmnop")
GITHUB = _shaped("gh" + "p_", "abcdefghijklmnopqrstuvwxyz0123")
OPENAI = _shaped("s" + "k-", "abcdefghijklmnopqrstuvwxyz012345")
JWT = ".".join(["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiIxIn0", "c2lnbmF0dXJl"])

#: Thirty ways a value must not reach the disk.
ADVERSARIAL: list[tuple[str, dict[str, object]]] = [
    # -- the obvious one, and its variations ---------------------------------
    ("password input", {"input_type": "password", "tag": "input"}),
    ("password input, uppercase type", {"input_type": "PASSWORD", "tag": "input"}),
    ("field named password", {"input_type": "text", "name": "password", "tag": "input"}),
    ("field named user_password", {"input_type": "text", "name": "user_password", "tag": "input"}),
    ("field named passwd", {"input_type": "text", "name": "passwd", "tag": "input"}),
    ("field labelled Pass Phrase", {"input_type": "text", "label": "Pass Phrase", "tag": "input"}),
    ("placeholder asks for a password",
     {"input_type": "text", "placeholder": "Enter your password", "tag": "input"}),
    ("aria-label names a password",
     {"input_type": "text", "aria_label": "Account password", "tag": "input"}),
    ("id names a password", {"input_type": "text", "element_id": "login-password", "tag": "input"}),
    # -- autocomplete, which the HTML specification defines ------------------
    ("autocomplete current-password",
     {"input_type": "text", "autocomplete": "current-password", "tag": "input"}),
    ("autocomplete new-password",
     {"input_type": "text", "autocomplete": "new-password", "tag": "input"}),
    ("autocomplete one-time-code",
     {"input_type": "text", "autocomplete": "one-time-code", "tag": "input"}),
    ("autocomplete cc-number",
     {"input_type": "text", "autocomplete": "cc-number", "tag": "input"}),
    ("autocomplete cc-csc", {"input_type": "text", "autocomplete": "cc-csc", "tag": "input"}),
    ("autocomplete with several tokens",
     {"input_type": "text", "autocomplete": "section-billing cc-number", "tag": "input"}),
    # -- money and identity --------------------------------------------------
    ("label Card Number", {"input_type": "text", "label": "Card Number", "tag": "input"}),
    ("label Security Code", {"input_type": "text", "label": "Security Code", "tag": "input"}),
    ("field named cvv", {"input_type": "text", "name": "cvv", "tag": "input"}),
    ("field named cvc", {"input_type": "text", "name": "cvc", "tag": "input"}),
    ("label Account Number", {"input_type": "text", "label": "Account Number", "tag": "input"}),
    ("field named routing_number",
     {"input_type": "text", "name": "routing_number", "tag": "input"}),
    ("label Social Security Number",
     {"input_type": "text", "label": "Social Security Number", "tag": "input"}),
    ("field named tax_id", {"input_type": "text", "name": "tax_id", "tag": "input"}),
    ("label PIN", {"input_type": "text", "label": "PIN", "tag": "input"}),
    # -- machine credentials -------------------------------------------------
    ("field named api_key", {"input_type": "text", "name": "api_key", "tag": "input"}),
    ("field named apiKey", {"input_type": "text", "name": "apiKey", "tag": "input"}),
    ("field named client_secret", {"input_type": "text", "name": "client_secret", "tag": "input"}),
    ("field named auth_token", {"input_type": "text", "name": "auth_token", "tag": "input"}),
    ("label Private Key", {"input_type": "text", "label": "Private Key", "tag": "input"}),
    # -- fail closed ---------------------------------------------------------
    ("an input type nobody has seen before",
     {"input_type": "quantum-credential", "tag": "input"}),
]


@pytest.mark.security()
@pytest.mark.parametrize(("description", "attributes"), ADVERSARIAL,
                         ids=[d for d, _ in ADVERSARIAL])
def test_no_sensitive_field_value_is_ever_recorded(
    description: str, attributes: dict[str, object]
) -> None:
    result = redact_value("hunter2-the-actual-secret", **attributes)  # type: ignore[arg-type]
    assert result.redacted, f"{description}: value would have been recorded"
    assert result.value == REDACTED
    assert "hunter2" not in str(result.value)


@pytest.mark.security()
def test_the_suite_covers_at_least_thirty_shapes() -> None:
    assert len(ADVERSARIAL) >= 30


@pytest.mark.security()
@pytest.mark.parametrize("secret", [SLACK, GITHUB, OPENAI, JWT, "4111 1111 1111 1111"])
def test_a_credential_pasted_into_an_ordinary_box_is_still_caught(secret: str) -> None:
    """Field inspection cannot help here. The second layer has to."""
    result = redact_value(secret, input_type="text", name="notes", tag="input")
    assert result.redacted
    assert result.reason is RedactionReason.CONTENT_PATTERN


BUSINESS_VALUES = [
    ("po_number", "PO-2211"), ("invoice_number", "INV-4471"), ("amount", "14800.00"),
    ("vendor", "Acme Supplies"), ("quantity", "400"), ("email", "ap@example.com"),
    ("description", "Steel bracket, 40mm"), ("order_ref", "ORD-99"),
    ("pinned", "yes"), ("passenger_count", "3"), ("author", "A. Debangshi"),
    ("authorised_by", "controller"), ("delivery_routing", "north"),
    ("boarding_pass_ref", "BP-77"), ("passage_notes", "n/a"),
]


@pytest.mark.parametrize(("name", "value"), BUSINESS_VALUES)
def test_business_data_is_not_destroyed(name: str, value: str) -> None:
    """Over-redaction makes a recording useless, which is its own failure."""
    result = redact_value(value, input_type="text", name=name, tag="input")
    assert not result.redacted, f"{name} was redacted but is ordinary business data"
    assert result.value == value


def test_buttons_and_links_are_not_treated_as_form_fields() -> None:
    """A submit button's `type` is reported like an input's, and it carries a
    label rather than data. Redacting it teaches people to ignore the notice."""
    assert not redact_value("Create Bill", input_type="submit", tag="button").redacted
    assert not redact_value("INV-4471.pdf", input_type=None, tag="a").redacted


def test_an_unrecognised_input_type_still_fails_closed() -> None:
    assert classify_field(input_type="totally-new", tag="input") is RedactionReason.UNKNOWN_FIELD


def test_screen_text_is_redacted_by_content_and_by_key() -> None:
    cleaned, changed = redact_visible_text(
        {"invoice-total": "14800.00", "api-key": "abc", "note": f"token {GITHUB}"}
    )
    assert cleaned["invoice-total"] == "14800.00"
    assert cleaned["api-key"] == REDACTED
    assert REDACTED in cleaned["note"]
    assert changed


def test_a_missing_value_is_not_an_error() -> None:
    result = redact_value(None, input_type="text", tag="input")
    assert result.value is None
    assert not result.redacted
