"""Redaction at the point of capture.

This runs *before* an event is constructed, not before it is written. A
password value is never placed into a :class:`RawEvent` at all, so there is no
window in which it exists in memory as part of a recording, and no code path
that could persist one by forgetting to call a sanitiser later.

Two layers, deliberately:

1. **Field classification** -- decide from the element itself whether its value
   may ever be recorded. Catches the password box before the value is read.
2. **Content inspection** -- run the evidence redactor over whatever survives.
   Catches a token pasted into an ordinary text box, which no amount of field
   inspection would have flagged.

Fail-closed: when the signals are ambiguous, the value is dropped. Losing a
value from a recording costs a re-record; writing a credential to disk cannot
be undone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from verity_evidence import REDACTED, redact_text

#: Input types whose value is never recorded, whatever else the element says.
NEVER_RECORD_TYPES = frozenset({"password"})

#: autocomplete tokens that name a secret. Defined by the HTML specification,
#: so this is a reliable signal rather than a guess about naming habits.
SECRET_AUTOCOMPLETE = frozenset({
    "current-password", "new-password", "one-time-code",
    "cc-number", "cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year",
})

#: Substrings in a field's name, id, label or placeholder that mean "secret".
# Separators are `[_\s-]?` throughout: a label reads "Card Number" while the
# field behind it is named "card_number", and both must match.
# Word boundaries, defined explicitly. Python's `\b` treats `_` as a word
# character, so `\bsecret\b` does not match "client_secret" -- which is exactly
# how field names are written. These treat any non-alphanumeric as a separator.
_B = r"(?:^|[^A-Za-z0-9])"
_E = r"(?=$|[^A-Za-z0-9])"
_SEP = r"[_\s-]?"


def _token(pattern: str) -> str:
    return _B + pattern + _E


# 'pass' on its own is deliberately NOT here. Matching it redacts
# 'passenger_count', 'boarding_pass' and 'pass_rate', and a site that names a
# password field `pass` will almost always also mark it type="password", which
# the type rule catches first. Over-redaction destroys the recording and
# teaches people to distrust the notice.
SENSITIVE_NAME = re.compile(
    "|".join(
        _token(p) for p in (
            rf"pass{_SEP}(?:word|wd|phrase|code)s?",
            r"secrets?", r"tokens?", rf"api{_SEP}keys?", r"apikeys?", r"credentials?",
            # 'auth' alone only as a whole token: 'author' and 'authorised_by'
            # are not credentials.
            r"auth", r"authorization", r"authentication", rf"auth{_SEP}tokens?",
            r"otp", r"mfa", r"2fa", r"pin", r"cvv", r"cvc",
            rf"security{_SEP}codes?", rf"card{_SEP}numbers?",
            rf"account{_SEP}numbers?", rf"routing{_SEP}(?:number|no)",
            r"ssn", rf"social{_SEP}security", rf"tax{_SEP}id",
            rf"private{_SEP}keys?", rf"verification{_SEP}codes?", r"passcodes?",
        )
    ),
    re.IGNORECASE,
)

#: Input types whose values are ordinary business data.
KNOWN_SAFE_TYPES = frozenset({
    "text", "search", "email", "url", "tel", "number", "date", "datetime-local",
    "month", "week", "time", "range", "color", "checkbox", "radio", "", "textarea",
    "select-one", "select-multiple",
    # Buttons carry a label, not data. Their `type` is reported by the DOM the
    # same way a text box's is, so they must be named here or the fail-closed
    # rule below treats every submit button as a possible credential widget.
    "submit", "button", "reset", "image",
})


class RedactionReason(str, Enum):
    NONE = "none"
    FIELD_TYPE = "field_type"
    AUTOCOMPLETE = "autocomplete"
    FIELD_NAME = "field_name"
    CONTENT_PATTERN = "content_pattern"
    UNKNOWN_FIELD = "unknown_field"
    """Fail-closed: an unrecognised field carrying a value we cannot vouch for."""


@dataclass(frozen=True)
class RedactionResult:
    value: str | None
    redacted: bool
    reason: RedactionReason

    @property
    def safe_to_record(self) -> bool:
        return not self.redacted


def classify_field(
    *,
    input_type: str | None = None,
    name: str | None = None,
    element_id: str | None = None,
    label: str | None = None,
    placeholder: str | None = None,
    autocomplete: str | None = None,
    aria_label: str | None = None,
    tag: str | None = None,
) -> RedactionReason:
    """Decide, from the element alone, whether its value may be recorded."""
    normalised_type = (input_type or "").strip().lower()
    normalised_tag = (tag or "").strip().lower()

    if normalised_type in NEVER_RECORD_TYPES:
        return RedactionReason.FIELD_TYPE

    tokens = {t.strip().lower() for t in (autocomplete or "").split()}
    if tokens & SECRET_AUTOCOMPLETE:
        return RedactionReason.AUTOCOMPLETE

    for descriptor in (name, element_id, label, placeholder, aria_label):
        if descriptor and SENSITIVE_NAME.search(descriptor):
            return RedactionReason.FIELD_NAME

    # The fail-closed rule applies only to real form fields. An unrecognised
    # <input> type is exactly where a novel credential widget would appear; a
    # <button> or an <a> carries a label, not data, and treating one as a
    # possible secret only teaches people to ignore the redaction notice.
    is_form_field = normalised_tag in ("input", "textarea", "select", "")
    if is_form_field and normalised_type and normalised_type not in KNOWN_SAFE_TYPES:
        return RedactionReason.UNKNOWN_FIELD

    return RedactionReason.NONE


def redact_value(
    value: str | None,
    *,
    input_type: str | None = None,
    name: str | None = None,
    element_id: str | None = None,
    label: str | None = None,
    placeholder: str | None = None,
    autocomplete: str | None = None,
    aria_label: str | None = None,
    tag: str | None = None,
) -> RedactionResult:
    """Apply both layers and return what may be recorded.

    Call this with the value *as read from the page*. Its return value is the
    only thing that should ever reach a :class:`RawEvent`.
    """
    reason = classify_field(
        input_type=input_type, name=name, element_id=element_id, label=label,
        placeholder=placeholder, autocomplete=autocomplete, aria_label=aria_label,
        tag=tag,
    )
    if reason is not RedactionReason.NONE:
        return RedactionResult(REDACTED, True, reason)

    if value is None:
        return RedactionResult(None, False, RedactionReason.NONE)

    cleaned, changed = redact_text(value)
    if changed:
        return RedactionResult(cleaned, True, RedactionReason.CONTENT_PATTERN)

    return RedactionResult(value, False, RedactionReason.NONE)


def redact_visible_text(values: dict[str, str]) -> tuple[dict[str, str], bool]:
    """Redact text scraped from the page. Content inspection only.

    Screen text has no field type to inspect, so this is the second layer
    working alone.
    """
    out: dict[str, str] = {}
    changed_any = False
    for key, text in values.items():
        if SENSITIVE_NAME.search(key):
            out[key] = REDACTED
            changed_any = True
            continue
        cleaned, changed = redact_text(text)
        out[key] = cleaned
        changed_any = changed_any or changed
    return out, changed_any


def summarise(results: list[RedactionResult]) -> dict[str, Any]:
    """Counts by reason, for the line `verity teach` prints at the end."""
    counts: dict[str, int] = {}
    for result in results:
        if result.redacted:
            counts[result.reason.value] = counts.get(result.reason.value, 0) + 1
    return {"redacted": sum(counts.values()), "by_reason": counts}
