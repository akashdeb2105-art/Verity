"""Merkle manifest for an evidence bundle.

The point is not cryptographic ceremony. It is that a bundle handed to an
auditor, attached to a CI run, or reopened in three weeks can be checked
against a single value, and a tampered bundle fails that check.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from verity_schema import EvidenceManifest, EvidenceRecord, ManifestEntry

EMPTY_MERKLE_ROOT = "sha256:" + hashlib.sha256(b"").hexdigest()


def merkle_root(leaf_hashes: list[str]) -> str:
    """Binary merkle root over sorted leaves.

    Leaves are sorted so that the root depends on the *set* of evidence, not on
    the order it happened to be produced in -- two runs that gather the same
    evidence in a different order produce the same root.
    """
    if not leaf_hashes:
        return EMPTY_MERKLE_ROOT

    level = [bytes.fromhex(h) for h in sorted(leaf_hashes)]
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            nxt.append(hashlib.sha256(left + right).digest())
        level = nxt
    return "sha256:" + level[0].hex()


def build_manifest(
    records: list[EvidenceRecord],
    sizes: dict[str, int],
    *,
    created_at: datetime | None = None,
    extra: dict[str, object] | None = None,
) -> EvidenceManifest:
    entries = [
        ManifestEntry(
            id=record.id,
            sha256=record.sha256,
            bytes=sizes.get(record.id, 0),
            path=record.payload_ref or "",
        )
        for record in sorted(records, key=lambda r: r.id)
    ]
    return EvidenceManifest(
        created_at=created_at or datetime.now(timezone.utc),
        entries=entries,
        merkle_root=merkle_root([e.sha256 for e in entries]),
        extra=dict(extra or {}),
    )


def verify_manifest(manifest: EvidenceManifest) -> bool:
    """Recompute the root. False means the manifest and its entries disagree."""
    return merkle_root([e.sha256 for e in manifest.entries]) == manifest.merkle_root
