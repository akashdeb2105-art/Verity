"""How dangerous a step is, and what that requires of the operator.

Two separable questions, kept separate on purpose.

**Classification** asks what a step would actually do. It reads the verb, the
resource and the payload, and it is deterministic: the same node classifies
the same way every time, with no model call and no network.

**Policy** asks what the operator wants done about that. It maps a risk level
onto a requirement -- proceed, ask a human, or refuse outright -- and it is
configuration, not code.

The rule that ties them together is that *a declaration cannot lower risk*. A
graph carries a ``risk`` field, but that field is a claim made by whoever
wrote the graph, and a claim is not evidence. The effective level is the
higher of what was declared and what was assessed, so mislabelling a wire
transfer as ``LOW`` buys nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

from verity_schema import RiskLevel
from verity_schema.workgraph import Node

_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

#: The lowest level a verb can be assessed at, whatever the graph claims.
#:
#: ``SEND_MESSAGE`` sits above the record verbs because it cannot be recalled.
#: A wrongly created bill can be deleted; a message that has left the building
#: has been read by the time anyone notices.
VERB_FLOOR: dict[str, RiskLevel] = {
    "CREATE_RECORD": RiskLevel.MEDIUM,
    "UPDATE_RECORD": RiskLevel.MEDIUM,
    "CALL_API": RiskLevel.MEDIUM,
    "SEND_MESSAGE": RiskLevel.HIGH,
}

#: Payload keys read as monetary amounts. Declared, not guessed, so that
#: adding a field cannot silently change how a graph is classified.
AMOUNT_FIELDS: frozenset[str] = frozenset({"amount", "total", "value", "price", "sum"})


class PolicyError(Exception):
    """A policy could not be understood, so no run may rely on it."""


class Requirement(str, Enum):
    """What the operator requires before a step of a given risk may proceed."""

    ALLOW = "ALLOW"
    """Proceed, once verification has passed. Never on its own authority."""

    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    """A person must approve this exact payload before it happens."""

    FORBID = "FORBID"
    """Not available to an automated run at all, approved or otherwise."""

    @property
    def blocks(self) -> bool:
        return self is Requirement.FORBID


@dataclass(frozen=True)
class Signal:
    """One observed reason a step was assessed where it was.

    Carried into the report so a halt can be explained in a sentence rather
    than attributed to "the policy".
    """

    name: str
    level: RiskLevel
    detail: str


@dataclass(frozen=True)
class Assessment:
    """What a step was judged to be, and why."""

    node_id: str
    declared: RiskLevel
    assessed: RiskLevel
    signals: tuple[Signal, ...] = ()

    @property
    def level(self) -> RiskLevel:
        """The effective risk: the higher of declared and assessed."""
        return max(self.declared, self.assessed, key=lambda r: _ORDER[r])

    @property
    def understated(self) -> bool:
        """The graph claimed less risk than the step actually carries."""
        return _ORDER[self.assessed] > _ORDER[self.declared]

    def explain(self) -> str:
        if not self.signals:
            return f"{self.level.value} (no escalating signals)"
        reasons = "; ".join(f"{s.name}: {s.detail}" for s in self.signals)
        return f"{self.level.value} ({reasons})"


@dataclass(frozen=True)
class Decision:
    """The policy's answer for one step."""

    assessment: Assessment
    requirement: Requirement
    reason: str = ""

    @property
    def node_id(self) -> str:
        return self.assessment.node_id

    @property
    def level(self) -> RiskLevel:
        return self.assessment.level

    @property
    def needs_approval(self) -> bool:
        return self.requirement is Requirement.REQUIRE_APPROVAL

    @property
    def blocks(self) -> bool:
        return self.requirement.blocks


@dataclass(frozen=True)
class Policy:
    """A declarative statement of what each risk level requires.

    Defaults are deliberately inconvenient. A policy that lets an unattended
    run perform its most dangerous action without asking anyone is not a
    policy; it is a decoration.
    """

    name: str = "default"
    requirements: Mapping[RiskLevel, Requirement] = field(
        default_factory=lambda: {
            RiskLevel.LOW: Requirement.ALLOW,
            RiskLevel.MEDIUM: Requirement.REQUIRE_APPROVAL,
            RiskLevel.HIGH: Requirement.REQUIRE_APPROVAL,
            RiskLevel.CRITICAL: Requirement.FORBID,
        }
    )
    high_amount: Decimal = Decimal("10000")
    critical_amount: Decimal = Decimal("100000")
    amount_fields: frozenset[str] = AMOUNT_FIELDS

    def requirement_for(self, level: RiskLevel) -> Requirement:
        """What this policy requires at ``level``.

        An unlisted level is forbidden, not allowed. A policy with a gap in it
        is a policy that has not decided, and a missing decision is not
        permission.
        """
        return self.requirements.get(level, Requirement.FORBID)

    def decide(self, node: Node) -> Decision:
        """Classify ``node`` and say what it requires."""
        assessment = classify(node, amount_fields=self.amount_fields,
                              high_amount=self.high_amount,
                              critical_amount=self.critical_amount)
        requirement = self.requirement_for(assessment.level)
        if node.approval_required and requirement is Requirement.ALLOW:
            return Decision(
                assessment=assessment,
                requirement=Requirement.REQUIRE_APPROVAL,
                reason="the graph marks this node approval_required",
            )
        return Decision(
            assessment=assessment,
            requirement=requirement,
            reason=f"policy {self.name!r} requires {requirement.value} at {assessment.level.value}",
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Policy:
        """Build a policy from parsed configuration, rejecting anything unclear.

        Strict on purpose. A typo in a policy file that silently fell back to a
        default would be the quietest possible way to disable a control.
        """
        unknown = set(data) - {
            "name", "requirements", "high_amount", "critical_amount", "amount_fields"
        }
        if unknown:
            raise PolicyError(f"unknown policy keys: {', '.join(sorted(unknown))}")

        requirements: dict[RiskLevel, Requirement] = {}
        raw = data.get("requirements") or {}
        if not isinstance(raw, Mapping):
            raise PolicyError("'requirements' must be a mapping of risk level to requirement")
        for key, value in raw.items():
            try:
                level = RiskLevel(str(key).upper())
                requirements[level] = Requirement(str(value).upper())
            except ValueError as exc:
                raise PolicyError(f"bad requirement {key!r}: {value!r}") from exc

        fields_raw = data.get("amount_fields")
        amount_fields = (
            frozenset(str(f).lower() for f in fields_raw)
            if isinstance(fields_raw, (list, tuple, set, frozenset))
            else AMOUNT_FIELDS
        )
        return cls(
            name=str(data.get("name") or "custom"),
            requirements=requirements or dict(Policy().requirements),
            high_amount=_amount_or_fail(data.get("high_amount"), Decimal("10000")),
            critical_amount=_amount_or_fail(data.get("critical_amount"), Decimal("100000")),
            amount_fields=amount_fields,
        )


def classify(
    node: Node,
    *,
    amount_fields: frozenset[str] = AMOUNT_FIELDS,
    high_amount: Decimal = Decimal("10000"),
    critical_amount: Decimal = Decimal("100000"),
) -> Assessment:
    """Judge one node on what it would do, ignoring what it claims to be."""
    signals: list[Signal] = []
    assessed = VERB_FLOOR.get(node.type, RiskLevel.LOW)
    if node.type in VERB_FLOOR:
        signals.append(Signal(
            name="verb",
            level=assessed,
            detail=f"{node.type} changes a system of record",
        ))

    if node.write is not None:
        for signal in _amount_signals(node, amount_fields, high_amount, critical_amount):
            signals.append(signal)
            assessed = max(assessed, signal.level, key=lambda r: _ORDER[r])

    return Assessment(
        node_id=node.id,
        declared=node.risk,
        assessed=assessed,
        signals=tuple(signals),
    )


def _amount_signals(
    node: Node,
    amount_fields: frozenset[str],
    high_amount: Decimal,
    critical_amount: Decimal,
) -> list[Signal]:
    """Escalate on the size of what is being written.

    An amount that cannot be read escalates too. "I could not tell how much
    this was" puts the operator in exactly the position that ``INCONCLUSIVE``
    describes elsewhere in Verity: not knowing is not the same as it being
    small, and only one of those two readings is safe.
    """
    assert node.write is not None
    out: list[Signal] = []
    for key, raw in sorted(node.write.payload.items()):
        if key.lower() not in amount_fields:
            continue
        value = _parse_amount(raw)
        if value is None:
            out.append(Signal(
                name="amount_unreadable",
                level=RiskLevel.HIGH,
                detail=f"{key}={raw!r} could not be read as an amount",
            ))
        elif value >= critical_amount:
            out.append(Signal(
                name="amount_critical",
                level=RiskLevel.CRITICAL,
                detail=f"{key}={value} at or above {critical_amount}",
            ))
        elif value >= high_amount:
            out.append(Signal(
                name="amount_high",
                level=RiskLevel.HIGH,
                detail=f"{key}={value} at or above {high_amount}",
            ))
    return out


def _parse_amount(raw: str) -> Decimal | None:
    """Read a monetary amount, or admit that it could not be read.

    Unresolved placeholders are the common case: ``{{ inputs.amount }}`` has no
    value at classification time, and treating it as zero would classify every
    templated payment as harmless.
    """
    cleaned = raw.strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    if not cleaned:
        return None
    try:
        return abs(Decimal(cleaned))
    except InvalidOperation:
        return None


def _amount_or_fail(raw: object, fallback: Decimal) -> Decimal:
    if raw is None:
        return fallback
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise PolicyError(f"{raw!r} is not a usable amount threshold") from exc


__all__ = [
    "AMOUNT_FIELDS",
    "VERB_FLOOR",
    "Assessment",
    "Decision",
    "Policy",
    "PolicyError",
    "Requirement",
    "Signal",
    "classify",
]
