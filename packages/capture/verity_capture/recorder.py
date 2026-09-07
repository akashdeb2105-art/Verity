"""The teaching session: a controlled browser that records what a person does.

Playwright is imported lazily, inside :meth:`BrowserRecorder.start`, so that
every other part of this package -- the event model, the redaction layer, the
session writer -- can be imported and tested on a machine with no browser at
all. That is not a workaround: it keeps the browser at the edge of the system
where it belongs, and it is why the compiler's tests need no Chromium.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .events import (
    DOCUMENT_URL,
    Attachment,
    CaptureSession,
    ElementRef,
    RawEvent,
    RawEventKind,
    SessionMetadata,
)
from .redaction import RedactionResult, redact_value, redact_visible_text, summarise

#: A recording browser should record, not phone home. These switch off the
#: background traffic Chromium makes on its own -- component updates, safe
#: browsing, first-run pings -- which is noise in a recording, a delay on a
#: restricted network, and a request the person never made.
LAUNCH_ARGS = [
    "--no-sandbox",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--metrics-recording-only",
]


def _allowlist_args(domains: list[str]) -> list[str]:
    """Confine the recording browser to a set of hosts, in the browser itself.

    Enforced by Chromium's own resolver rather than by a Python request
    handler. That is not a style preference: a per-request callback into
    Python deadlocks Playwright's synchronous API -- a click that triggers a
    request freezes the recorder while the recorder is waiting for the click.
    A resolver rule cannot deadlock, cannot be slow, and cannot be bypassed by
    a request the callback never sees.
    """
    clean = sorted({d.lower().strip().lstrip(".") for d in domains if d.strip()})
    if not clean:
        return []
    rules = ["MAP * ~NOTFOUND"]
    for domain in clean:
        rules.append(f"EXCLUDE {domain}")
        rules.append(f"EXCLUDE *.{domain}")
    return ["--host-resolver-rules=" + ",".join(rules)]


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

    fetch_documents: bool = True
    """Download a document the person opens, and keep it with the recording.

    A recording that only says 'they opened INV-4471.pdf' cannot produce a
    single check about what the invoice said. Fetching it is what makes the
    document an independent channel instead of a filename."""

    max_document_bytes: int = 25 * 1024 * 1024

    executable_path: str | None = None
    """A specific browser binary, when Playwright's own download is not the one
    to use -- a pinned Chrome in CI, or a managed install. Defaults to
    VERITY_BROWSER_EXECUTABLE so the environment can say so without code."""


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
        self._attachments: list[Attachment] = []
        self._blobs: dict[str, bytes] = {}
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
            executable = self._options.executable_path or os.environ.get(
                "VERITY_BROWSER_EXECUTABLE"
            )
            self._browser = self._playwright.chromium.launch(
                headless=self._options.headless,
                args=LAUNCH_ARGS + _allowlist_args(self._options.allowed_domains),
                **({"executable_path": executable} if executable else {}),
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            self._playwright.stop()
            raise CaptureError(f"could not launch a browser: {exc}") from exc

        width, height = self._options.viewport
        self._context = self._browser.new_context(
            viewport={"width": width, "height": height},
            # A teaching session reads; it does not save files to the machine.
            # Documents are fetched deliberately, into the recording.
            accept_downloads=False,
        )

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
        self._fetch_documents()
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
            attachments=list(self._attachments),
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

    @property
    def attachment_blobs(self) -> dict[str, bytes]:
        """The fetched document bytes, keyed by sha256. Written by write_session."""
        return dict(self._blobs)

    # -- internals -------------------------------------------------------
    def _allowed(self, url: str) -> bool:
        """Whether a URL is inside the recording's own allowlist.

        Checked here rather than relying on the page route, because a fetch
        made through the browser context does not necessarily pass through it.
        A recorder that wanders off the allowed domains to download something
        would defeat the point of having an allowlist.
        """
        allowed = {d.lower().lstrip(".") for d in self._options.allowed_domains}
        if not allowed:
            return True
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in allowed)

    def _fetch_documents(self) -> None:
        """Download every document the recording touched, once it is over.

        Deliberately at the end, reading back over the events already
        recorded, rather than at the moment of the click. Nothing extra runs
        while the person is working, and a fetch is never issued from inside a
        callback the browser driver is waiting on -- which stalls a
        synchronous session.

        For a document at a stable address this fetches exactly what the click
        would have. One that only resolves mid-session would not, and that is
        recorded as a failed attachment rather than left as a silent gap.
        """
        if not self._options.fetch_documents:
            return
        for event in self._events:
            href = event.element.href if event.element else None
            if not href or not DOCUMENT_URL.search(href):
                continue
            # Resolved here, not in the page: a recording keeps what the page
            # literally said, and turning that into an address is a rule that
            # belongs somewhere it can be read and tested.
            self._capture_document(urljoin(event.url or "", href), event.seq)

    def _capture_document(self, url: str, source_event: int) -> None:
        """Fetch a document the person opened and keep it with the recording.

        Uses the recording context's own request API, so the fetch carries the
        same cookies the person was browsing with -- a document behind a login
        is exactly the case that matters.

        Every failure is recorded as a failed attachment rather than dropped.
        A recording that silently lacks a document would produce a contract
        that silently lacks the checks that document supports.
        """
        if any(a.url == url for a in self._attachments):
            return

        filename = Path(urlparse(url).path).name
        def fail(reason: str) -> None:
            self._attachments.append(Attachment(
                sha256="", url=url, filename=filename,
                source_event=source_event, error=reason,
            ))

        if not self._allowed(url):
            fail("outside the recording's allowed domains")
            return

        try:
            response = self._context.request.get(url, timeout=15_000)
            if not response.ok:
                fail(f"HTTP {response.status}")
                return
            body: bytes = response.body()
        except Exception as exc:
            fail(f"{type(exc).__name__}: {exc}".strip()[:200])
            return

        if len(body) > self._options.max_document_bytes:
            fail(f"{len(body)} bytes exceeds the {self._options.max_document_bytes} limit")
            return

        digest = hashlib.sha256(body).hexdigest()
        self._blobs[digest] = body
        self._attachments.append(Attachment(
            sha256=digest, url=url, filename=filename,
            media_type=str(response.headers.get("content-type", "")).split(";")[0].strip(),
            byte_count=len(body), source_event=source_event,
        ))

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
        href=raw.get("href"),
    )


def attachments_dir(session_path: str | Path) -> Path:
    """Where a recording's documents live: a sibling folder named after it."""
    target = Path(session_path)
    return target.parent / (target.name.split(".")[0] + ".attachments")


def write_session(
    session: CaptureSession,
    path: str | Path,
    blobs: dict[str, bytes] | None = None,
) -> Path:
    """Write a recording to disk as JSON, with its documents beside it.

    Documents are stored under their own sha256, so the JSON and the bytes
    cannot drift apart: a file whose name no longer matches its content is a
    file that is not there.
    """
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(session.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    for attachment in session.attachments:
        body = (blobs or {}).get(attachment.sha256)
        if body is None:
            continue
        folder = attachments_dir(target)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / _blob_name(attachment)).write_bytes(body)
    return target


def read_session(path: str | Path) -> CaptureSession:
    import json

    return CaptureSession.model_validate(json.loads(Path(path).read_text("utf-8")))


def read_attachment(session_path: str | Path, attachment: Attachment) -> bytes | None:
    """The bytes of one document, or None if this recording does not carry it.

    The content is verified against the recorded hash before it is returned.
    A document that no longer matches what was recorded is not the document
    that was recorded, and returning it anyway would make a contract cite
    evidence it never saw.
    """
    if not attachment.ok:
        return None
    blob = attachments_dir(session_path) / _blob_name(attachment)
    if not blob.is_file():
        return None
    body = blob.read_bytes()
    if hashlib.sha256(body).hexdigest() != attachment.sha256:
        return None
    return body


def _blob_name(attachment: Attachment) -> str:
    suffix = Path(attachment.filename).suffix.lower()
    return attachment.sha256 + (suffix if 1 < len(suffix) <= 6 else "")
