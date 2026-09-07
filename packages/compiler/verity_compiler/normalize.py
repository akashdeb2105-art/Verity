"""Raw events to semantic steps.

Entirely rule-based. Nothing here calls a model, and nothing here guesses at
business meaning: a click on a value is an EXTRACT because the person looked at
something, not because we think we know why.

The rules are conservative on purpose. A step we cannot classify becomes CLICK
rather than something more specific, because a wrong label propagates into the
proposed contract, and a wrong proposed assertion is worse than a missing one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from verity_capture import CaptureSession, RawEvent, RawEventKind
from verity_schema import RiskLevel

from .documents import DOCUMENT_SYSTEM, DocumentRead, read_documents

#: Button or link text that means "this writes something".
CREATE_WORDS = re.compile(r"\b(create|add|new|save|submit|post|generate)\b", re.I)
UPDATE_WORDS = re.compile(r"\b(update|edit|change|modify|apply|approve|confirm)\b", re.I)
SEND_WORDS = re.compile(r"\b(send|email|reply|notify|dispatch)\b", re.I)
DELETE_WORDS = re.compile(r"\b(delete|remove|void|cancel|destroy)\b", re.I)
DOCUMENT_URL = re.compile(r"\.(pdf|docx?|xlsx?|csv|png|jpe?g|tiff?)(\?|$)", re.I)
SEARCH_WORDS = re.compile(r"\b(search|find|look ?up|filter|query)\b", re.I)


@dataclass
class Step:
    """One semantic action, with a pointer back to the events it came from."""

    index: int
    verb: str
    label: str
    url: str | None = None
    system: str | None = None
    """Which application this happened in, derived from the URL."""

    element_hint: str | None = None
    document_url: str = ""
    """Where the document itself came from -- not the page that linked to it."""
    document_sha256: str = ""
    """Set when this step read a document, so a draft can cite the exact bytes."""
    document_error: str = ""
    """Set when a document was carried but could not be read."""
    value: str | None = None
    observed: dict[str, str] = field(default_factory=dict)
    """What the screen showed at this moment."""

    risk: RiskLevel = RiskLevel.LOW
    source_events: list[int] = field(default_factory=list)
    confidence: float = 1.0
    note: str = ""


def system_of(url: str | None) -> str | None:
    """Name the application a URL belongs to.

    A heuristic, and labelled as one: the first meaningful path segment, or the
    host when there is none. It is good enough to group steps for review, and
    the person renames it if it is wrong.
    """
    if not url:
        return None
    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    if segments and segments[0] in ("ui", "app", "api", "web"):
        segments = segments[1:]
    if segments:
        return re.sub(r"[^a-z0-9]+", "_", segments[0].lower()).strip("_") or None
    host = (parsed.hostname or "").split(".")[0]
    return re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_") or None


def normalize(
    session: CaptureSession, session_path: str | Path | None = None
) -> list[Step]:
    """Turn a recording into an ordered list of semantic steps.

    ``session_path`` is where the recording was read from. Documents live in a
    folder beside it, so without the path they cannot be read and every
    document step stays unread -- which the draft then says out loud.
    """
    documents = read_documents(session, session_path)
    steps: list[Step] = []
    for event in session.events:
        if event.kind in (RawEventKind.SESSION_START, RawEventKind.SESSION_END):
            continue
        if event.kind is RawEventKind.NETWORK:
            continue

        step = _classify(event)
        if step is None:
            continue

        if step.system == DOCUMENT_SYSTEM:
            _apply_document(step, documents.get(event.seq))

        if steps and _is_duplicate_navigation(steps[-1], step):
            steps[-1].source_events.append(event.seq)
            steps[-1].observed = step.observed or steps[-1].observed
            continue

        step.index = len(steps)
        steps.append(step)

    return _merge_search(steps)


def _classify(event: RawEvent) -> Step | None:
    system = system_of(event.url)
    observed = dict(event.visible_text)
    element = event.element
    hint = element.best_hint if element else None
    text = ((element.name if element else None) or (element.text if element else None) or "")

    if event.kind is RawEventKind.NAVIGATE:
        return Step(
            index=-1, verb="NAVIGATE", label=f"Open {_page_label(event.url)}",
            url=event.url, system=system, element_hint=hint, observed=observed,
            source_events=[event.seq],
        )

    if event.kind is RawEventKind.OPEN_DOCUMENT:
        return Step(
            index=-1, verb="EXTRACT", label=f"Read document {event.document_ref or ''}".strip(),
            url=event.url, system=DOCUMENT_SYSTEM, element_hint=hint, observed=observed,
            source_events=[event.seq], note="document",
        )

    if event.kind in (RawEventKind.INPUT, RawEventKind.SELECT):
        verb = "SELECT" if event.kind is RawEventKind.SELECT else "TYPE"
        return Step(
            index=-1, verb=verb, label=f"Enter {text or 'a value'}".strip(),
            url=event.url, system=system, element_hint=hint,
            value=None if event.value_redacted else event.value,
            observed=observed, source_events=[event.seq],
            note="redacted" if event.value_redacted else "",
        )

    if event.kind is RawEventKind.SUBMIT:
        return Step(
            index=-1, verb="CREATE_RECORD", label="Submit the form", url=event.url,
            system=system, element_hint=hint, observed=observed,
            risk=RiskLevel.MEDIUM, source_events=[event.seq], confidence=0.7,
        )

    if event.kind is RawEventKind.CLICK:
        return _classify_click(event, system, hint, text, observed)

    return None


def _classify_click(
    event: RawEvent, system: str | None, hint: str | None, text: str, observed: dict[str, str]
) -> Step:
    role = (event.element.role if event.element else None) or ""
    testid = (event.element.testid if event.element else None) or ""

    if DELETE_WORDS.search(text):
        # Named, never emitted as an executable verb: destructive actions are
        # blocked in V1, so the step is recorded and flagged for a human.
        return Step(
            index=-1, verb="ASK_HUMAN", label=f"Destructive action: {text}",
            url=event.url, system=system, element_hint=hint, observed=observed,
            risk=RiskLevel.CRITICAL, source_events=[event.seq], confidence=0.6,
            note="destructive action recorded but never compiled into an executable step",
        )

    if role == "button" or (event.element and event.element.tag == "button"):
        if SEND_WORDS.search(text):
            verb, risk = "SEND_MESSAGE", RiskLevel.HIGH
        elif CREATE_WORDS.search(text):
            verb, risk = "CREATE_RECORD", RiskLevel.MEDIUM
        elif UPDATE_WORDS.search(text):
            verb, risk = "UPDATE_RECORD", RiskLevel.MEDIUM
        elif SEARCH_WORDS.search(text):
            verb, risk = "SEARCH", RiskLevel.LOW
        else:
            verb, risk = "CLICK", RiskLevel.LOW
        return Step(
            index=-1, verb=verb, label=text or "Press a button", url=event.url,
            system=system, element_hint=hint, observed=observed, risk=risk,
            source_events=[event.seq], confidence=0.8 if verb != "CLICK" else 1.0,
        )

    if role == "link":
        target = (event.element.name if event.element else None) or text
        if DOCUMENT_URL.search(target or "") or DOCUMENT_URL.search(hint or ""):
            # The person opened a document. Verity saw that it happened but not
            # what was inside it, and the draft has to say so rather than
            # quietly omitting every fact the document would have carried.
            # Deliberately no observed values. The click happened on the page
            # that linked to the document, so that page's text is on screen --
            # but it is not the document's content, and treating it as such
            # invents comparisons that were never made.
            return Step(
                index=-1, verb="EXTRACT", label=f"Open document {target}".strip(),
                url=event.url, system=DOCUMENT_SYSTEM, element_hint=hint,
                observed={}, source_events=[event.seq], confidence=0.5,
                note="document-not-read",
            )
        return Step(
            index=-1, verb="NAVIGATE", label=f"Follow {text or 'a link'}".strip(),
            url=event.url, system=system, element_hint=hint, observed=observed,
            source_events=[event.seq],
        )

    # A click on something that displays a value: the person was reading it.
    if testid and testid in observed:
        return Step(
            index=-1, verb="EXTRACT", label=f"Read {_humanise(testid)}",
            url=event.url, system=system, element_hint=hint,
            value=observed.get(testid), observed=observed, source_events=[event.seq],
        )

    return Step(
        index=-1, verb="CLICK", label=text or "Click", url=event.url, system=system,
        element_hint=hint, observed=observed, source_events=[event.seq],
    )


def _merge_search(steps: list[Step]) -> list[Step]:
    """A value typed and then submitted is one SEARCH, not two steps."""
    merged: list[Step] = []
    skip = set()
    for i, step in enumerate(steps):
        if i in skip:
            continue
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        if (
            step.verb == "TYPE" and nxt is not None
            and nxt.verb in ("SEARCH", "CREATE_RECORD", "NAVIGATE")
            and nxt.system == step.system
            and (SEARCH_WORDS.search(nxt.label) or nxt.verb == "NAVIGATE")
        ):
            merged.append(
                Step(
                    index=len(merged), verb="SEARCH",
                    label=f"Search for {step.value or 'a value'}",
                    url=step.url, system=step.system, element_hint=step.element_hint,
                    value=step.value, observed=nxt.observed or step.observed,
                    source_events=[*step.source_events, *nxt.source_events],
                )
            )
            skip.add(i + 1)
            continue
        step.index = len(merged)
        merged.append(step)
    return merged


def _is_duplicate_navigation(previous: Step, current: Step) -> bool:
    return (
        previous.verb == "NAVIGATE" and current.verb == "NAVIGATE"
        and previous.url == current.url
    )


def _page_label(url: str | None) -> str:
    if not url:
        return "a page"
    segments = [s for s in urlparse(url).path.split("/") if s and s not in ("ui", "app")]
    return " / ".join(_humanise(s) for s in segments) or "the home page"


def _humanise(token: str) -> str:
    return re.sub(r"[-_]+", " ", token).strip()


def _apply_document(step: Step, read: DocumentRead | None) -> None:
    """Fill in what the document actually said, or record why nothing did.

    The label changes with the outcome, because the step list is the first
    thing a person reads and it has to be true at a glance: "Read invoice"
    when the document was read, "Open document (not read)" when it was not.
    """
    if read is None or not read.ok:
        # One note for every way a document can fail to produce facts, with
        # the reason attached. What matters to a reader is the same in all of
        # them: no fact came out of this, so nothing here is being checked.
        step.note = "document-not-read"
        step.label += "  (not read)"
        step.confidence = 0.5
        step.document_error = (
            "it was never fetched" if read is None
            else (read.error or "the document yielded no fields")
        )
        return

    step.observed = dict(read.values)
    step.document_url = read.attachment.url
    step.note = "document"
    step.confidence = 1.0
    step.document_sha256 = read.sha256
    step.label = f"Read {Path(read.attachment.filename).stem or 'document'}"
