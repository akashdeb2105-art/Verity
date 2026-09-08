"""A Playwright-driven browser that carries out the read steps of a graph.

This package is the browser at the edge of the system. It knows how to open a
page, click, type, select and read text back, and it records what it saw as a
``verity-trace/v1`` trace. It knows nothing about the runtime, the verifier,
policy or approvals -- an import-linter contract keeps it that way, so the
runtime can consult a browser the same way it consults a verifier: through a
protocol, never an import.

Playwright is imported inside :meth:`BrowserSession._launch`, not at module
scope, so importing this module -- and the ``dom_hash`` definition every other
package shares from it -- needs no browser installed. That is the same
discipline :mod:`verity_capture.recorder` follows, and for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from verity_schema import __version__
from verity_schema.trace import RuntimeInfo, Trace, TraceStep, TraceTarget

from .domhash import DOM_HASH_JS_CALL, structural_hash

#: A browser that carries out a workflow should carry it out, not phone home.
#: The same background-traffic switches :mod:`verity_capture` uses.
LAUNCH_ARGS = [
    "--no-sandbox",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-client-side-phishing-detection",
    "--metrics-recording-only",
]


class BrowserError(Exception):
    """The browser could not start. A step that merely failed is not this."""


@dataclass(frozen=True)
class PageObservation:
    """What one action saw. Data for the trace and for replay -- never a fact.

    ``ok`` is ``False`` when the action could not be carried out (a target that
    is not there, a navigation that timed out). The caller decides what that
    means; this class does not raise, because a run that is stopped must still
    know what it did.
    """

    verb: str
    url: str = ""
    dom_hash: str = ""
    target: str = ""
    extracted: dict[str, str] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


class BrowserSession:
    """One browser, one page, driven step by step.

    ``base_url`` is prepended to every relative navigation, so a graph names
    ``/ui/invoices`` and the environment supplies the host -- the same
    indirection the connector registry gives verification.
    """

    def __init__(
        self,
        *,
        base_url: str,
        run_id: str,
        headless: bool = True,
        executable_path: str | None = None,
        viewport: tuple[int, int] = (1280, 900),
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._run_id = run_id
        self._headless = headless
        self._executable_path = executable_path
        self._viewport = viewport

        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._seq = 0
        self._steps: list[TraceStep] = []
        self._started_at: datetime | None = None

    # -- lifecycle ----------------------------------------------------------
    def _launch(self) -> Any:
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BrowserError(
                "the browser executor needs Playwright and Chromium: "
                "pip install \".[browser]\" && playwright install chromium"
            ) from exc

        self._started_at = datetime.now(timezone.utc)
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                headless=self._headless,
                args=LAUNCH_ARGS,
                **({"executable_path": self._executable_path} if self._executable_path else {}),
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            self._playwright.stop()
            raise BrowserError(f"could not launch Chromium: {exc}") from exc

        width, height = self._viewport
        self._context = self._browser.new_context(
            viewport={"width": width, "height": height},
            accept_downloads=False,
        )
        self._page = self._context.new_page()
        return self._page

    def close(self) -> None:
        """Release the browser. Safe to call more than once, and safe to call
        when nothing was ever launched."""
        import contextlib

        for closer in (self._context, self._browser, self._playwright):
            if closer is None:
                continue
            shutdown = getattr(closer, "close", None) or getattr(closer, "stop", None)
            if shutdown is None:
                continue
            with contextlib.suppress(Exception):
                shutdown()
        self._context = self._browser = self._playwright = self._page = None

    # -- actions ----------------------------------------------------------
    def navigate(self, url: str, *, timeout_ms: int) -> PageObservation:
        page = self._launch()
        destination = url if url.startswith(("http://", "https://")) else self._base_url + (
            url if url.startswith("/") else "/" + url
        )
        try:
            page.goto(destination, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            return self._record("NAVIGATE", ok=False, error=_reason(exc), url=destination)
        return self._record("NAVIGATE")

    def click(self, target: str, *, timeout_ms: int) -> PageObservation:
        return self._interact(
            "CLICK", target, timeout_ms, lambda loc: loc.click(timeout=timeout_ms)
        )

    def type(self, target: str, value: str, *, timeout_ms: int) -> PageObservation:
        return self._interact(
            "TYPE", target, timeout_ms, lambda loc: loc.fill(value, timeout=timeout_ms)
        )

    def select(self, target: str, value: str, *, timeout_ms: int) -> PageObservation:
        return self._interact(
            "SELECT", target, timeout_ms,
            lambda loc: loc.select_option(value, timeout=timeout_ms),
        )

    def extract(self, mapping: dict[str, str], *, timeout_ms: int) -> PageObservation:
        page = self._launch()
        found: dict[str, str] = {}
        for name, hint in mapping.items():
            try:
                text = page.locator(_selector(hint)).first.inner_text(timeout=timeout_ms)
            except Exception as exc:
                return self._record(
                    "EXTRACT", ok=False,
                    error=f"{name!r} ({hint}) was not on the page: {_reason(exc)}",
                    extracted=found,
                )
            found[name] = text.strip()
        return self._record("EXTRACT", extracted=found)

    # -- trace ----------------------------------------------------------
    def trace(self) -> Trace:
        return Trace(
            run_id=self._run_id,
            runtime=RuntimeInfo(name="verity-browser", version=__version__, adapter="playwright"),
            started_at=self._started_at,
            finished_at=datetime.now(timezone.utc) if self._started_at else None,
            status_reported="unknown",
            steps=list(self._steps),
        )

    # -- internals ----------------------------------------------------------
    def _interact(
        self, verb: str, target: str, timeout_ms: int, action: Any
    ) -> PageObservation:
        page = self._launch()
        try:
            action(page.locator(_selector(target)).first)
        except Exception as exc:
            return self._record(verb, ok=False, error=_reason(exc), target=target)
        return self._record(verb, target=target)

    def _record(
        self,
        verb: str,
        *,
        ok: bool = True,
        error: str = "",
        url: str | None = None,
        target: str = "",
        extracted: dict[str, str] | None = None,
    ) -> PageObservation:
        self._seq += 1
        page_url = url if url is not None else (self._page.url if self._page else "")
        dom = ""
        if ok and self._page is not None:
            try:
                dom = str(self._page.evaluate(DOM_HASH_JS_CALL))
            except Exception:  # pragma: no cover - a page that cannot be read
                dom = structural_hash(self._page.content())
        self._steps.append(TraceStep(
            seq=self._seq,
            ts=datetime.now(timezone.utc),
            action=verb,
            url=page_url or None,
            target=TraceTarget(testid=target) if target else None,
            dom_hash=dom or None,
            result="ok" if ok else "error",
            error=error or None,
            data={"extracted": dict(extracted)} if extracted else {},
        ))
        return PageObservation(
            verb=verb, url=page_url, dom_hash=dom, target=target,
            extracted=dict(extracted or {}), ok=ok, error=error,
        )


def _selector(hint: str) -> str:
    """Resolve a target hint to a Playwright selector. Nothing clever, on purpose.

    A bare word is a ``data-testid``. An explicit ``css=`` / ``text=`` /
    ``role=`` prefix is passed straight through. No model, no fuzzy matching --
    a selector that guesses is a selector that can be wrong without saying so.
    """
    hint = hint.strip()
    for prefix in ("css=", "text=", "role=", "xpath="):
        if hint.startswith(prefix):
            return hint if prefix != "css=" else hint[len("css="):]
    if hint.startswith("testid="):
        hint = hint[len("testid="):]
    return f'[data-testid="{hint}"]'


def _reason(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text.splitlines()[0][:200]


__all__ = ["LAUNCH_ARGS", "BrowserError", "BrowserSession", "PageObservation"]
