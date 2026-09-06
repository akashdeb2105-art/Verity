"""Shared enumerations and primitives for every Verity artifact.

These types are the vocabulary the whole system speaks. They live in the
schema package because the schema package is a leaf: it depends on nothing
else in the project, so any component can adopt the vocabulary without
inheriting a dependency on the verifier, the connectors or the sandbox.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

SCHEMA_VERSION = "verity/v1"


class Verdict(str, Enum):
    """The four -- and only four -- outcomes of a verification.

    ``INCONCLUSIVE`` is deliberately distinct from both ``PASS`` and ``FAIL``.
    A system that folds "I could not check" into "it passed" is lying; one
    that folds it into "it failed" trains its users to ignore alerts.
    """

    PASS = "PASS"  # noqa: S105 - a verdict, not a credential
    DRIFT = "DRIFT"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAIL = "FAIL"

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self]

    @property
    def is_success(self) -> bool:
        return self is Verdict.PASS


_EXIT_CODES: dict[Verdict, int] = {
    Verdict.PASS: 0,
    Verdict.FAIL: 1,
    Verdict.DRIFT: 2,
    Verdict.INCONCLUSIVE: 3,
}

# Severity order used when combining many assertion results into one verdict.
# A single FAIL outranks everything; INCONCLUSIVE outranks DRIFT, because an
# unresolvable fact is a more serious statement about our knowledge than a
# changed execution path.
_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.PASS: 0,
    Verdict.DRIFT: 1,
    Verdict.INCONCLUSIVE: 2,
    Verdict.FAIL: 3,
}


def worst(verdicts: Iterable[Verdict]) -> Verdict:
    """Combine verdicts, returning the most serious. Empty input is ``PASS``."""
    seen = list(verdicts)
    if not seen:
        return Verdict.PASS
    return max(seen, key=lambda v: _VERDICT_RANK[v])


class Label(str, Enum):
    """Epistemic status of a fact. Required, never inferred at read time.

    Only ``OBSERVED`` facts can satisfy a ``STRONG`` assertion. This is a
    schema-level constraint rather than a UI convention, which is what stops
    the slow drift into fabricated evidence.
    """

    OBSERVED = "OBSERVED"
    """Read directly from a source: an API response, a row, a document field."""

    INFERRED = "INFERRED"
    """Derived by computation or by a model. Carries provenance and confidence."""

    RECOMMENDED = "RECOMMENDED"
    """A proposal for a human. Never executable without explicit approval."""


class Strength(str, Enum):
    """How much an assertion is worth. Derived by the typechecker, never declared."""

    STRONG = "STRONG"
    """Every referenced fact is OBSERVED from a connector or a document."""

    WEAK = "WEAK"
    """At least one referenced fact comes from a trace, DOM or screenshot."""

    INVALID = "INVALID"
    """References an INFERRED fact with no evidence chain to an OBSERVED root."""


class Severity(str, Enum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


class Channel(str, Enum):
    """Whether a fact is read back through a different channel than it was written.

    ``INDEPENDENT`` is the honest case. ``SAME`` means the system is confirming
    itself, which is the most common way verification quietly becomes theatre,
    so it is recorded and surfaced rather than assumed away.
    """

    INDEPENDENT = "INDEPENDENT"
    SAME = "SAME"
    UNKNOWN = "UNKNOWN"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def requires_strong_verification(self) -> bool:
        return self in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


class SourceKind(str, Enum):
    CONNECTOR = "connector"
    """A typed system of record reached over an API. Produces OBSERVED facts."""

    DOCUMENT = "document"
    """A file parsed deterministically. Produces OBSERVED facts with provenance."""

    TRACE = "trace"
    """Whatever the runtime recorded. Produces WEAK facts and is never trusted."""


class EvidenceKind(str, Enum):
    API_RESPONSE = "api_response"
    DB_ROW = "db_row"
    DOCUMENT_FIELD = "document_field"
    DOM_SNAPSHOT = "dom_snapshot"
    SCREENSHOT_CROP = "screenshot_crop"
    AUDIT_EVENT = "audit_event"
    COMPUTED = "computed"
