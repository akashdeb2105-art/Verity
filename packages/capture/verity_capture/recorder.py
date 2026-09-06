"""The teaching session: a controlled browser that records what a person does.

Playwright is imported lazily, inside :meth:`BrowserRecorder.start`, so that
every other part of this package -- the event model, the redaction layer, the
session writer -- can be imported and tested on a machine with no browser at
all. That is not a workaround: it keeps the browser at the edge of the system
where it belongs, and it is why the compiler's tests need no Chromium.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import (
    CaptureSession,
    ElementRef,
    RawEvent,
    RawEventKind,
    SessionMetadata,
)
from .redaction import RedactionResult, redact_value, redact_visible_text, summarise


class CaptureError(Exception):
    """Recording could not start or could not continue."""


@dataclass
class RecorderOptions:
    start_url: str
    headless: bool = True
    allowed_domains: list[str] = field(default_factory=list)
    """Deny-by-default when non-empty. A teaching session should not be able to
    wander onto a site the person did not intend to record."""

    max_events: int = 500
    viewport: tuple[int, int] = (1280, 900)


class BrowserRecorder:
    """Records a teaching session in an isolated browser context.

    The context is ephemeral: a fresh profile, discarded when the session ends.
    Nothing is carried between sessions, so a recording cannot silently depend
    on a cookie somebody set last week.
    """

    def __init__(self, options: RecorderOptions) -> None:
        self._options = options
        self._events: list[RawEvent] = []
        self._redactions: list[RedactionResult] = []
        self._seq = 0
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._started_at: datetime | None = None
        self._session_id = f"sess_{uuid.uuid4().hex[:12]}"

    # -- lifecycle -------------------------------------------------------
    def start(self) -> Any:
        from ._inject import INIT_SCRIPT

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise CaptureError(
                "recording needs Playwright and a browser: "
                "pip install playwright && playwright install chromium"
            ) from exc

        self._started_at = datetime.now(timezone.utc)
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                headless=self._options.headless, args=["--no-sandbox"]
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            self._playwright.stop()
            raise CaptureError(f"could not launch a browser: {exc}") from exc

        width, height = self._options.viewport
        self._context = self._browser.new_context(viewport={"width": width, "height": height})

        if self._options.allowed_domains:
            self._install_domain_allowlist()

        self._context.expose_binding("__verityEvent", self._on_page_event)
        self._context.add_init_script(INIT_SCRIPT)

        self._page = self._context.new_page()
        self._page.on("framenavigated", self._on_navigated)

        self._record(
            RawEventKind.SESSION_START, url=self._options.start_url,
            note="teaching session started",
        )
        self._page.goto(self._options.start_url, wait_until="domcontentloaded")
        return self._page

    def stop(self) -> CaptureSession:
        self._record(RawEventKind.SESSION_END, note="teaching session ended")
        finished = datetime.now(timezone.utc)

        # Teardown must never mask the recording. A browser that will not close
        # is a nuisance; losing the session somebody just spent ten minutes
        # producing is not.
        for closer in (self._context, self._browser, self._playwright):
            if closer is None:
                continue
            shutdown = getattr(closer, "close", None) or getattr(closer, "stop", None)
            if shutdown is None:
                continue
            with contextlib.suppress(Exception):
                shutdown()

        return CaptureSession(
            metadata=SessionMetadata(
                session_id=self._session_id,
                started_at=self._started_at,
                finished_at=finished,
                start_url=self._options.start_url,
                browser="chromium",
            ),
            events=list(self._events),
        )

    def __enter__(self) -> BrowserRecorder:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser is not None:
            self.stop()

    # -- properties ------------------------------------------------------
    @property
    def page(self) -> Any:
        if self._page is None:
            raise CaptureError("the session has not been started")
        return self._page

    @property
    def redaction_summary(self) -> dict[str, Any]:
        return summarise(self._redactions)

    # -- internals -------------------------------------------------------
    def _install_domain_allowlist(self) -> None:
        allowed = {d.lower().lstrip(".") for d in self._options.allowed_domains}

        def route(handler: Any, request: Any) -> None:
            from urllib.parse import urlparse

            host = (urlparse(request.url).hostname or "").lower()
            if any(host == d or host.endswith("." + d) for d in allowed):
                handler.continue_()
            else:
                handler.abort()

        self._context.route("**/*", route)

    def _on_navigated(self, frame: Any) -> None:
        if frame.parent_frame is not None:
            return
        self._record(RawEventKind.NAVIGATE, url=frame.url, note="navigation")

    def _on_page_event(self, source: Any, payload: dict[str, Any]) -> None:
        """Receive one event from the page and redact before recording it."""
        if len(self._events) >= self._options.max_events:
            return

        raw_element = payload.get("element") or {}
        result = redact_value(
            payload.get("value"),
            input_type=raw_element.get("input_type"),
            name=raw_element.get("element_name"),
            element_id=raw_element.get("element_id"),
            label=raw_element.get("label"),
            placeholder=raw_element.get("placeholder"),
            autocomplete=raw_element.get("autocomplete"),
            aria_label=raw_element.get("aria_label"),
            tag=raw_element.get("tag"),
        )
        self._redactions.append(result)

        visible, visible_changed = redact_visible_text(payload.get("visible_text") or {})

        try:
            kind = RawEventKind(payload.get("kind", "click"))
        except ValueError:
            return

        self._record(
            kind,
            url=payload.get("url"),
            title=payload.get("title"),
            element=_element_from(raw_element),
            value=result.value,
            value_redacted=result.redacted or bool(payload.get("value_withheld_in_page")),
            visible_text=visible,
            dom_hash=payload.get("dom_hash"),
            note=result.reason.value if result.redacted else "",
        )
        if visible_changed:
            self._redactions.append(result)

    def _record(self, kind: RawEventKind, **fields: Any) -> None:
        self._seq += 1
        self._events.append(
            RawEvent(seq=self._seq, kind=kind, ts=datetime.now(timezone.utc), **fields)
        )


def _element_from(raw: dict[str, Any]) -> ElementRef | None:
    if not raw:
        return None
    return ElementRef(
        role=raw.get("role"), name=raw.get("name"), testid=raw.get("testid"),
        label=raw.get("label"), tag=raw.get("tag"), input_type=raw.get("input_type"),
        css=[c for c in (raw.get("css") or []) if c], text=raw.get("text"),
    )


def write_session(session: CaptureSession, path: str | Path) -> Path:
    """Write a recording to disk as JSON."""
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(session.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def read_session(path: str | Path) -> CaptureSession:
    import json

    return CaptureSession.model_validate(json.loads(Path(path).read_text("utf-8")))
