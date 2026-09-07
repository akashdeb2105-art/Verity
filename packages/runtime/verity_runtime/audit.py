"""An append-only record of every decision in a run, chained so edits show.

A log that can be quietly edited is not evidence, it is a story. Each entry
here carries the hash of the one before it, so changing an entry, removing one
from the middle, or reordering two of them breaks the chain from that point on
and :func:`verify` says exactly where.

What this does not do is worth stating plainly, because a security control
that is oversold is worse than none. A hash chain proves *internal*
consistency. Someone who can rewrite the whole file can recompute every hash
and produce a chain that verifies -- and truncating the log at the end leaves
no gap at all, because there is nothing after the cut to disagree with. Both
of those need an anchor outside the file: a copy somewhere the writer cannot
reach, a signature, or a length recorded elsewhere. Until Verity has one, this
detects accident and casual tampering, and it does not detect a determined
rewrite. It is labelled that way here so nobody has to find out later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: What the first entry chains from. A fixed, recognisable value, so an entry
#: claiming to be first cannot be confused with one whose parent was removed.
GENESIS = "sha256:" + "0" * 64


def _digest(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditEntry:
    """One recorded decision."""

    seq: int
    kind: str
    node_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    at: float = 0.0
    prev: str = GENESIS
    hash: str = ""

    def computed_hash(self) -> str:
        """The hash this entry's own contents imply."""
        return _digest({
            "seq": self.seq, "kind": self.kind, "node_id": self.node_id,
            "detail": self.detail, "at": self.at, "prev": self.prev,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "kind": self.kind, "node_id": self.node_id,
            "detail": dict(self.detail), "at": self.at,
            "prev": self.prev, "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEntry:
        return cls(
            seq=int(data["seq"]), kind=str(data["kind"]),
            node_id=str(data.get("node_id") or ""),
            detail=dict(data.get("detail") or {}),
            at=float(data.get("at") or 0.0),
            prev=str(data.get("prev") or GENESIS),
            hash=str(data.get("hash") or ""),
        )


@dataclass(frozen=True)
class AuditVerification:
    """Whether a chain holds, and where it stops holding."""

    intact: bool
    checked: int
    broken_at: int = -1
    reason: str = ""


@dataclass
class AuditLog:
    """The chain for one run."""

    run_id: str = ""
    entries: list[AuditEntry] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def head(self) -> str:
        """The hash of the last entry: the value an external anchor would record."""
        return self.entries[-1].hash if self.entries else GENESIS

    def record(
        self, kind: str, *, node_id: str = "", at: float = 0.0, **detail: Any
    ) -> AuditEntry:
        """Append one entry, chained to the current head."""
        draft = AuditEntry(
            seq=len(self.entries), kind=kind, node_id=node_id,
            detail=dict(detail), at=at, prev=self.head,
        )
        entry = AuditEntry(
            seq=draft.seq, kind=draft.kind, node_id=draft.node_id,
            detail=draft.detail, at=draft.at, prev=draft.prev,
            hash=draft.computed_hash(),
        )
        self.entries.append(entry)
        return entry

    def of_kind(self, kind: str) -> list[AuditEntry]:
        return [e for e in self.entries if e.kind == kind]

    def to_jsonl(self) -> str:
        return "".join(
            json.dumps(e.to_dict(), sort_keys=True, default=str) + "\n" for e in self.entries
        )

    @classmethod
    def from_jsonl(cls, text: str, run_id: str = "") -> AuditLog:
        entries = [
            AuditEntry.from_dict(json.loads(line))
            for line in text.splitlines() if line.strip()
        ]
        return cls(run_id=run_id, entries=entries)


def verify(log: AuditLog) -> AuditVerification:
    """Check that every entry still matches its own hash and its parent's.

    Reports the first entry where the chain stops holding, rather than a
    boolean. "Something was changed" is not actionable; "entry 4 no longer
    matches its contents" is.
    """
    expected_prev = GENESIS
    for index, entry in enumerate(log.entries):
        if entry.seq != index:
            return AuditVerification(
                intact=False, checked=index, broken_at=index,
                reason=(
                    f"entry {index} is numbered {entry.seq}, so an entry was "
                    "removed or reordered"
                ),
            )
        if entry.prev != expected_prev:
            return AuditVerification(
                intact=False, checked=index, broken_at=index,
                reason=(
                    f"entry {index} chains from {entry.prev}, but the entry "
                    f"before it hashes to {expected_prev}"
                ),
            )
        if entry.hash != entry.computed_hash():
            return AuditVerification(
                intact=False, checked=index, broken_at=index,
                reason=(
                    f"entry {index} does not match its own contents, so it was "
                    "edited after it was written"
                ),
            )
        expected_prev = entry.hash
    return AuditVerification(intact=True, checked=len(log.entries))


__all__ = [
    "GENESIS",
    "AuditEntry",
    "AuditLog",
    "AuditVerification",
    "verify",
]
