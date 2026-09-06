"""A content-addressed, append-only evidence store.

Evidence is immutable by construction: the address *is* the hash of the
content, so writing the same fact twice is a no-op and altering a stored fact
is impossible without changing its address.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from verity_schema import EvidenceKind, EvidenceRecord, Label, Provenance

from .redact import redact


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, Decimals as strings.

    Determinism matters here -- the same fact must produce the same evidence
    address on every machine, or replay comparisons become meaningless.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_default)


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def sha256_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class EvidenceStore:
    """Filesystem-backed content-addressed store.

    Layout::

        <root>/objects/<aa>/<full-sha256>.json
        <root>/index.json
    """

    def __init__(self, root: str | Path, *, fixed_time: datetime | None = None) -> None:
        self.root = Path(root)
        self.objects_dir = self.root / "objects"
        self._records: dict[str, EvidenceRecord] = {}
        self._fixed_time = fixed_time

    def _now(self) -> datetime:
        return self._fixed_time or datetime.now(timezone.utc)

    def put(
        self,
        *,
        kind: EvidenceKind,
        label: Label,
        payload: Any,
        produced_by: str,
        summary: str = "",
        provenance: Provenance | None = None,
    ) -> EvidenceRecord:
        """Store one piece of evidence and return its immutable record."""
        redacted_payload, was_redacted = redact(payload)
        body = canonical_json(redacted_payload).encode("utf-8")
        digest = sha256_of(body)
        evidence_id = f"ev_{digest[:16]}"

        relative = Path("objects") / digest[:2] / f"{digest}.json"
        target = self.root / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)

        record = EvidenceRecord(
            id=evidence_id,
            kind=kind,
            label=label,
            sha256=digest,
            produced_by=produced_by,
            produced_at=self._now(),
            provenance=provenance,
            redaction_applied=was_redacted,
            summary=summary[:280],
            payload_ref=str(relative).replace("\\", "/"),
        )
        self._records[evidence_id] = record
        return record

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        return self._records.get(evidence_id)

    def load_payload(self, evidence_id: str) -> Any:
        record = self._records.get(evidence_id)
        if record is None or record.payload_ref is None:
            raise KeyError(evidence_id)
        return json.loads((self.root / record.payload_ref).read_text("utf-8"))

    @property
    def records(self) -> list[EvidenceRecord]:
        return sorted(self._records.values(), key=lambda r: r.id)

    def __len__(self) -> int:
        return len(self._records)
