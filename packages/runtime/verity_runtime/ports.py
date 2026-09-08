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

from verity_schema.trace import Trace
from verity_schema.workgraph import BrowserAction


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


# ---------------------------------------------------------------------------
# The browser boundary.
#
# A read step used to be a recorded no-op. Tier 2 lets it drive a real browser
# instead -- but the runtime must not import Playwright, for the same reason it
# must not import the verifier: an executor the browser package depended on, or
# a runtime that depended on a browser, would quietly end the claim that a
# contract can check a run somebody else's agent produced. So the runtime
# declares the *shape* of the driver it walks the read steps through, names no
# implementation, and an import-linter contract stops it acquiring one.
# ---------------------------------------------------------------------------

#: The two ways a read step can be carried out. Recorded on every run, because
#: "I read the page" and "I recorded that I would have" are different claims and
#: a replay that cannot tell them apart is this product's own failure, aimed
#: inward. A replay across a tier boundary is refused, not diffed.
TIER_BROWSER = "browser"
TIER_RECORDED = "recorded"


@dataclass(frozen=True)
class BrowserObservation:
    """What one read step saw.

    Recorded on the step result and on the run's trace, and nowhere else. It is
    data, never a fact the gate consults: the verifier reads the world through
    its own connectors, so a page the runtime happened to look at can never
    become the reason a write was allowed.
    """

    action: str
    ok: bool = True
    url: str = ""
    dom_hash: str = ""
    extracted: dict[str, str] = field(default_factory=dict)
    screenshot_ref: str = ""
    error: str = ""
    note: str = ""
    """The recorded-no-op driver's description of the step it did not drive.
    Kept so a run with no browser still reads the way it always has."""

    def as_outputs(self) -> dict[str, Any]:
        """The shape the executor stores on a step result.

        A recorded no-op yields exactly ``{"action", "intent"}`` -- the pair
        every run produced before Tier 2 existed -- so nothing downstream has
        to know whether a browser was involved.
        """
        out: dict[str, Any] = {"action": self.action}
        if self.note:
            out["intent"] = self.note
        if self.url:
            out["url"] = self.url
        if self.dom_hash:
            out["dom_hash"] = self.dom_hash
        if self.extracted:
            out["extracted"] = dict(self.extracted)
        if self.screenshot_ref:
            out["screenshot_ref"] = self.screenshot_ref
        return out


@runtime_checkable
class BrowserDriver(Protocol):
    """Carries out the read steps of a graph. Implemented outside this package.

    ``tier`` is recorded on the run so a replay can refuse to compare a browser
    run against a recorded one. ``perform`` is called once per read step;
    ``finish`` returns the trace of everything seen, or ``None`` if nothing was
    driven; ``close`` releases the browser and must be safe to call twice.
    """

    tier: str

    def perform(
        self,
        *,
        verb: str,
        action: BrowserAction | None,
        intent: str,
        inputs: dict[str, str],
        timeout_ms: int,
    ) -> BrowserObservation: ...

    def finish(self) -> Trace | None: ...

    def close(self) -> None: ...


class RecordedNoOpDriver:
    """The default driver: it records that a read step was reached, and drives
    nothing.

    This is the behaviour every run had before Tier 2. Keeping it the default
    is deliberate -- a run with no ``--browser`` still costs nothing, still
    replays byte-for-byte, and an unconfigured runtime never launches a browser
    it was not asked to.
    """

    tier = TIER_RECORDED

    def perform(
        self,
        *,
        verb: str,
        action: BrowserAction | None,
        intent: str,
        inputs: dict[str, str],
        timeout_ms: int,
    ) -> BrowserObservation:
        return BrowserObservation(action=verb, note=intent)

    def finish(self) -> Trace | None:
        return None

    def close(self) -> None:
        return None
