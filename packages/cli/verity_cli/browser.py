"""Adapts the browser executor to the runtime's ``BrowserDriver`` port.

The runtime walks a graph's read steps through a protocol and never imports a
browser -- the same rule that keeps it from importing the verifier. So the two
are composed here, in the CLI, exactly as :class:`verity_cli.run.ContractGate`
composes the runtime and the verifier. This module is the only place
``verity_browser`` and ``verity_runtime`` appear together.

Nothing this driver returns is ever shown to the verifier. What the browser
saw is recorded on the run's trace and used by ``verity replay`` to diff two
runs; it is data, not a fact a verdict can rest on.
"""

from __future__ import annotations

from verity_browser import BrowserSession
from verity_runtime import TIER_BROWSER, BrowserObservation, interpolate_inputs
from verity_schema.trace import Trace
from verity_schema.workgraph import BrowserAction

#: Verbs the browser carries out. A read verb not listed here is recorded the
#: way the no-op driver records it -- reached, not driven -- so an unsupported
#: step is never mistaken for one that happened.
_DRIVEN = frozenset({"NAVIGATE", "CLICK", "TYPE", "SELECT", "EXTRACT"})


class PlaywrightDriver:
    """A ``BrowserDriver`` backed by ``verity_browser``'s Playwright session.

    The session is created on the first driven step, so a run whose read steps
    are all no-ops (or a graph with none) never launches Chromium.
    """

    tier = TIER_BROWSER

    def __init__(
        self,
        *,
        base_url: str,
        run_id: str,
        headless: bool = True,
        executable_path: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._run_id = run_id
        self._headless = headless
        self._executable_path = executable_path
        self._session: BrowserSession | None = None

    def _ensure_session(self) -> BrowserSession:
        if self._session is None:
            self._session = BrowserSession(
                base_url=self._base_url,
                run_id=self._run_id,
                headless=self._headless,
                executable_path=self._executable_path,
            )
        return self._session

    def perform(
        self,
        *,
        verb: str,
        action: BrowserAction | None,
        intent: str,
        inputs: dict[str, str],
        timeout_ms: int,
    ) -> BrowserObservation:
        if verb not in _DRIVEN:
            return BrowserObservation(action=verb, note=intent)

        spec = action or BrowserAction()
        session = self._ensure_session()
        url = interpolate_inputs(spec.url, inputs)
        value = interpolate_inputs(spec.value, inputs)

        if verb == "NAVIGATE":
            result = session.navigate(url or "/", timeout_ms=timeout_ms)
        elif verb == "CLICK":
            result = session.click(spec.target, timeout_ms=timeout_ms)
        elif verb == "TYPE":
            result = session.type(spec.target, value, timeout_ms=timeout_ms)
        elif verb == "SELECT":
            result = session.select(spec.target, value, timeout_ms=timeout_ms)
        else:  # EXTRACT
            mapping = dict(spec.extract) or ({spec.target: spec.target} if spec.target else {})
            result = session.extract(mapping, timeout_ms=timeout_ms)

        return BrowserObservation(
            action=verb,
            ok=result.ok,
            url=result.url,
            dom_hash=result.dom_hash,
            extracted=dict(result.extracted),
            error=result.error,
        )

    def finish(self) -> Trace | None:
        if self._session is None:
            return None
        return self._session.trace()

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
