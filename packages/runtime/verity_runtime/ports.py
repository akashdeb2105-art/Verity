"""What the runtime needs from the outside world, stated as protocols.

The architecture rule is that the verifier and the runtime have no import edge
in either direction. That is not fussiness: the portability claim is that a
contract can verify a run produced by somebody else's agent, and an executor
that the verifier depends on would quietly make that untrue.

But the runtime must not perform a consequential write while the outcome is
wrong, which means it has to consult a verifier. Both hold if the runtime
declares the *shape* of the thing it consults and never names it. The CLI
composes the two. This module is that declaration -- and it is the whole
reason ``import-linter`` can keep the packages apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class GateVerdict(str, Enum):
    """What a gate concluded. Mirrors the verifier's verdicts by value, not by import."""

    PASS = "PASS"  # noqa: S105 - a verdict, not a credential
    FAIL = "FAIL"
    DRIFT = "DRIFT"
    INCONCLUSIVE = "INCONCLUSIVE"

    @property
    def allows_write(self) -> bool:
        """Only PASS opens the gate.

        INCONCLUSIVE is the one worth being explicit about: "I could not
        check" is not permission. Every system that treats it as permission is
        lying about what it knows.
        """
        return self is GateVerdict.PASS


@dataclass(frozen=True)
class GateResult:
    """A gate's answer, with enough detail for a person to act on it."""

    verdict: GateVerdict
    reason: str = ""
    failed_assertions: tuple[str, ...] = ()
    first_divergence: str = ""
    evidence_ref: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def allows_write(self) -> bool:
        return self.verdict.allows_write


@runtime_checkable
class VerificationGate(Protocol):
    """Consulted before anything consequential happens.

    Implemented outside this package. The runtime knows only that something
    can be asked, and that only PASS lets it continue.
    """

    def check(self, inputs: dict[str, str]) -> GateResult: ...


class AlwaysPassGate:
    """A gate that permits everything. For tests of execution, never of safety.

    Named so that its use is obvious in a diff. A default gate that silently
    allowed writes would be the most dangerous line in the codebase.
    """

    def check(self, inputs: dict[str, str]) -> GateResult:
        return GateResult(
            verdict=GateVerdict.PASS,
            reason="no verification was configured for this run",
        )


class ClosedGate:
    """Refuses everything, giving the reason it was closed.

    This is the default. A runtime built with no gate configured must not be
    able to write: the safe direction for a missing decision is to stop.
    """

    def __init__(self, reason: str = "no verification gate was configured") -> None:
        self._reason = reason

    def check(self, inputs: dict[str, str]) -> GateResult:
        return GateResult(verdict=GateVerdict.INCONCLUSIVE, reason=self._reason)
