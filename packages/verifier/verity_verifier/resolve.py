"""Fact resolution: reading the world, once, before anything is asserted.

Facts resolve in declaration order, so a later fact may reference an earlier
one in its key or query. Every resolved fact produces an evidence record, and
every unresolved fact produces an explicit reason -- which is what turns "I
could not check" into an honest ``INCONCLUSIVE`` instead of a quiet pass.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from verity_connectors import ConnectorError, ConnectorRegistry, DocumentConnector
from verity_evidence import EvidenceStore
from verity_extract import ExtractionError, PdfExtractor
from verity_schema import (
    EvidenceKind,
    FactResult,
    FactSpec,
    Label,
    OutcomeContract,
    Provenance,
    SourceKind,
    Trace,
)

from .expr import EvaluationError, Scope, evaluate, parse

_TEMPLATE = re.compile(r"\{\{(.+?)\}\}")

#: Trace step actions that indicate the runtime wrote through a browser UI.
_UI_WRITE_ACTIONS = frozenset({"CLICK", "TYPE", "SELECT", "SUBMIT"})
_API_WRITE_ACTIONS = frozenset({"CALL_API", "CREATE_RECORD", "UPDATE_RECORD"})


@dataclass
class ResolutionOutcome:
    """Everything the evaluator and the reporter need after facts are read."""

    values: dict[str, Any] = field(default_factory=dict)
    results: list[FactResult] = field(default_factory=list)
    channels: dict[str, str] = field(default_factory=dict)
    anchors: dict[str, dict[str, str]] = field(default_factory=dict)
    """Per document fact: field name -> the label the value was found next to."""

    drift_notes: list[str] = field(default_factory=list)

    @property
    def unresolved(self) -> list[FactResult]:
        return [r for r in self.results if not r.resolved]


class FactResolver:
    def __init__(
        self,
        registry: ConnectorRegistry,
        store: EvidenceStore,
        *,
        documents: DocumentConnector | None = None,
        extractor: PdfExtractor | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._documents = documents
        self._extractor = extractor or PdfExtractor()

    def resolve_all(
        self,
        contract: OutcomeContract,
        inputs: dict[str, Any],
        *,
        trace: Trace | None = None,
    ) -> ResolutionOutcome:
        outcome = ResolutionOutcome()
        for spec in contract.facts:
            started = time.perf_counter()
            try:
                self._resolve_one(contract, spec, inputs, outcome, trace)
                duration = int((time.perf_counter() - started) * 1000)
                last = outcome.results[-1]
                outcome.results[-1] = last.model_copy(update={"duration_ms": duration})
            except _FactUnresolvedError as exc:
                duration = int((time.perf_counter() - started) * 1000)
                outcome.results.append(
                    FactResult(
                        id=spec.id, resolved=False, source=spec.source,
                        error=str(exc), duration_ms=duration,
                        cardinality=exc.cardinality,
                    )
                )
        return outcome

    # -- per source kind --------------------------------------------------
    def _resolve_one(
        self,
        contract: OutcomeContract,
        spec: FactSpec,
        inputs: dict[str, Any],
        outcome: ResolutionOutcome,
        trace: Trace | None,
    ) -> None:
        source = contract.sources[spec.source]
        scope = Scope(facts=outcome.values, inputs=inputs)

        if source.kind is SourceKind.CONNECTOR:
            self._resolve_connector(spec, source.capability, scope, outcome)
        elif source.kind is SourceKind.DOCUMENT:
            self._resolve_document(spec, scope, outcome, inputs)
        elif source.kind is SourceKind.TRACE:
            self._resolve_trace(spec, scope, outcome, trace)
        else:  # pragma: no cover - SourceKind is closed
            raise _FactUnresolvedError(f"unsupported source kind '{source.kind}'")

    def _resolve_connector(
        self,
        spec: FactSpec,
        capability: str | None,
        scope: Scope,
        outcome: ResolutionOutcome,
    ) -> None:
        if spec.read is None:
            raise _FactUnresolvedError(f"fact '{spec.id}' has no read specification")

        try:
            connector = self._registry.resolve(spec.source, capability)
        except ConnectorError as exc:
            raise _FactUnresolvedError(str(exc)) from exc

        key = _render(spec.read.key, scope) if spec.read.key else None
        query = {k: _render(v, scope) for k, v in spec.read.query.items()}

        try:
            result = connector.read(spec.read.resource, key=key, query=query)
        except ConnectorError as exc:
            raise _FactUnresolvedError(f"connector '{connector.name}' failed: {exc}") from exc

        records = [_project(r, spec.select) for r in result.records]

        if spec.expect_cardinality is not None and len(records) != spec.expect_cardinality:
            raise _FactUnresolvedError(
                f"expected {spec.expect_cardinality} record(s) from "
                f"{connector.name}.{spec.read.resource}, found {len(records)}",
                cardinality=len(records),
            )

        value: Any = records[0] if len(records) == 1 else records

        evidence = self._store.put(
            kind=EvidenceKind.API_RESPONSE,
            label=Label.OBSERVED,
            payload={"request": result.request, "records": records,
                     "connector": connector.name, "channel": result.channel},
            produced_by=f"connector:{connector.name}",
            summary=f"{connector.name}.{spec.read.resource} -> {len(records)} record(s)",
        )

        outcome.values[spec.id] = value
        outcome.channels[spec.id] = result.channel
        outcome.results.append(
            FactResult(
                id=spec.id, resolved=True, label=Label.OBSERVED, value=value,
                evidence_refs=[evidence.id], source=spec.source,
                cardinality=len(records),
            )
        )

    def _resolve_document(
        self,
        spec: FactSpec,
        scope: Scope,
        outcome: ResolutionOutcome,
        inputs: dict[str, Any],
    ) -> None:
        if self._documents is None:
            raise _FactUnresolvedError("no document source is bound")
        if not spec.extract:
            raise _FactUnresolvedError(f"document fact '{spec.id}' declares no fields")

        uri = _render(spec.document, scope) if spec.document else _default_document_uri(inputs)
        if not uri:
            raise _FactUnresolvedError(f"fact '{spec.id}' has no document reference")

        try:
            document = self._documents.fetch(uri)
        except ConnectorError as exc:
            raise _FactUnresolvedError(str(exc)) from exc

        field_spec = {
            name: {"type": f.type, "required": f.required, "pattern": f.pattern}
            for name, f in spec.extract.items()
        }

        try:
            extraction = self._extractor.extract(
                document.data, field_spec, uri=uri, document_sha256=document.sha256
            )
        except ExtractionError as exc:
            raise _FactUnresolvedError(f"document '{uri}' could not be read: {exc}") from exc

        if extraction.missing:
            raise _FactUnresolvedError(
                f"document '{uri}' is missing required field(s): "
                f"{', '.join(sorted(extraction.missing))}"
            )

        refs: list[str] = []
        anchors: dict[str, str] = {}
        for name, extracted in sorted(extraction.fields.items()):
            anchors[name] = extracted.anchor
            declared = spec.extract[name].expect_anchor
            if declared and extracted.anchor and extracted.anchor != declared:
                outcome.drift_notes.append(
                    f"{spec.id}.{name} was found under '{extracted.anchor}' but the "
                    f"contract expects '{declared}': the document template changed"
                )
            record = self._store.put(
                kind=EvidenceKind.DOCUMENT_FIELD,
                label=Label.OBSERVED,
                payload={"field": name, "value": extracted.value, "raw": extracted.raw},
                produced_by="document:pdf",
                summary=f"{name} = {extracted.raw} (page {extracted.page})",
                provenance=Provenance(
                    source_ref=f"sha256:{document.sha256}", page=extracted.page,
                    bbox=extracted.bbox, field=name,
                    method=f"{extracted.method}:{extracted.anchor}".rstrip(":"),
                    confidence=extracted.confidence,
                ),
            )
            refs.append(record.id)

        outcome.values[spec.id] = extraction.values()
        outcome.channels[spec.id] = "document"
        outcome.anchors[spec.id] = anchors
        outcome.results.append(
            FactResult(
                id=spec.id, resolved=True, label=Label.OBSERVED,
                value=extraction.values(), evidence_refs=refs, source=spec.source,
                cardinality=1,
            )
        )

    def _resolve_trace(
        self,
        spec: FactSpec,
        scope: Scope,
        outcome: ResolutionOutcome,
        trace: Trace | None,
    ) -> None:
        if trace is None:
            raise _FactUnresolvedError(
                f"fact '{spec.id}' needs an execution trace, and none was supplied"
            )
        if spec.read is None:
            raise _FactUnresolvedError(f"trace fact '{spec.id}' has no read specification")

        resource = spec.read.resource
        query = {k: _render(v, scope) for k, v in spec.read.query.items()}

        if resource == "dom_text":
            selector = str(query.get("selector", ""))
            value: Any = _dom_text(trace, selector)
            if value is None:
                raise _FactUnresolvedError(
                    f"the trace records no DOM text for selector '{selector}'"
                )
            payload: Any = {"selector": selector, "value": value}
        elif resource in ("step", "steps"):
            value = [s.model_dump(mode="json") for s in trace.steps]
            payload = {"steps": len(trace.steps)}
        else:
            raise _FactUnresolvedError(f"unsupported trace resource '{resource}'")

        record = self._store.put(
            kind=EvidenceKind.DOM_SNAPSHOT,
            label=Label.OBSERVED,
            payload=payload,
            produced_by=f"trace:{trace.runtime.name}",
            summary=f"trace {resource}",
        )
        outcome.values[spec.id] = {"value": value} if resource == "dom_text" else value
        outcome.channels[spec.id] = "ui"
        outcome.results.append(
            FactResult(
                id=spec.id, resolved=True, label=Label.OBSERVED,
                value=outcome.values[spec.id], evidence_refs=[record.id],
                source=spec.source, cardinality=1,
            )
        )


class _FactUnresolvedError(Exception):
    def __init__(self, message: str, *, cardinality: int | None = None) -> None:
        self.cardinality = cardinality
        super().__init__(message)


def _project(record: dict[str, Any], select: list[str]) -> dict[str, Any]:
    if not select:
        return dict(record)
    return {name: record.get(name) for name in select}


def _render(template: str | None, scope: Scope) -> str:
    """Expand ``{{ expression }}`` using the same parser the assertions use.

    Reusing the expression language here means there is exactly one way to read
    a value in a contract, and no second mini-language to secure.
    """
    if template is None:
        return ""

    def substitute(match: re.Match[str]) -> str:
        source = match.group(1).strip()
        try:
            value = evaluate(parse(source), scope)
        except (EvaluationError, ValueError) as exc:
            raise _FactUnresolvedError(f"cannot expand '{{{{ {source} }}}}': {exc}") from exc
        return "" if value is None else str(value)

    return _TEMPLATE.sub(substitute, template)


def _default_document_uri(inputs: dict[str, Any]) -> str:
    return str(inputs.get("document_uri", ""))


def _dom_text(trace: Trace, selector: str) -> str | None:
    for step in reversed(trace.steps):
        texts = step.data.get("dom_text")
        if isinstance(texts, dict) and selector in texts:
            return str(texts[selector])
    return None


def infer_write_channel(trace: Trace | None) -> str:
    """Which channel the runtime appears to have written through.

    Used to detect self-confirmation: a value written through a UI and read
    back through that same UI proves very little, and Verity says so rather
    than quietly counting it as verified.
    """
    if trace is None:
        return "unknown"
    actions = {step.action.upper() for step in trace.steps}
    if actions & _UI_WRITE_ACTIONS:
        return "ui"
    if actions & _API_WRITE_ACTIONS:
        return "api"
    return "unknown"
