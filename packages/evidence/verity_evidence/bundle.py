"""Evidence bundles: the portable output of one verification.

A bundle is a directory (optionally tarred) containing the report, every
evidence object it cites, and a manifest with a merkle root. It is what CI
uploads as an artifact and what a person opens later to answer "how do we know
that?".
"""

from __future__ import annotations

import json
import tarfile
from datetime import datetime
from pathlib import Path

from verity_schema import EvidenceManifest, VerificationReport

from .manifest import build_manifest, verify_manifest
from .store import EvidenceStore, canonical_json


class BundleIntegrityError(Exception):
    """A bundle's contents do not match its manifest."""


def write_bundle(
    directory: str | Path,
    *,
    report: VerificationReport,
    store: EvidenceStore,
    created_at: datetime | None = None,
) -> Path:
    """Write ``report.json``, ``manifest.json`` and ``objects/`` into a directory."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)

    cited = {ref for fact in report.facts for ref in fact.evidence_refs}
    cited |= {ref for a in report.assertions for ref in a.evidence_refs}
    records = [r for r in store.records if r.id in cited] or store.records

    sizes: dict[str, int] = {}
    for record in records:
        if not record.payload_ref:
            continue
        source = store.root / record.payload_ref
        if not source.exists():
            continue
        target = out / record.payload_ref
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        target.write_bytes(payload)
        sizes[record.id] = len(payload)

    manifest = build_manifest(records, sizes, created_at=created_at)
    (out / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "report.json").write_text(
        json.dumps(report.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "evidence.json").write_text(
        canonical_json([r.model_dump(mode="json") for r in records]), encoding="utf-8"
    )
    return out


def read_manifest(directory: str | Path) -> EvidenceManifest:
    path = Path(directory) / "manifest.json"
    return EvidenceManifest.model_validate(json.loads(path.read_text("utf-8")))


def check_bundle(directory: str | Path) -> EvidenceManifest:
    """Verify a bundle end to end. Raises :class:`BundleIntegrityError` if broken.

    Checks both that every object hashes to its recorded digest and that the
    manifest's merkle root matches its own entries -- so neither editing an
    object nor editing the manifest passes.
    """
    import hashlib

    root = Path(directory)
    manifest = read_manifest(root)

    if not verify_manifest(manifest):
        raise BundleIntegrityError("manifest merkle root does not match its entries")

    for entry in manifest.entries:
        if not entry.path:
            continue
        target = root / entry.path
        if not target.exists():
            raise BundleIntegrityError(f"missing evidence object: {entry.path}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != entry.sha256:
            raise BundleIntegrityError(f"evidence object altered: {entry.path}")
    return manifest


def tar_bundle(directory: str | Path, archive: str | Path) -> Path:
    out = Path(archive)
    with tarfile.open(out, "w") as tar:
        tar.add(str(directory), arcname=Path(directory).name)
    return out
