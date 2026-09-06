"""Evidence storage, bundling and integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from verity_evidence import (
    BundleIntegrityError,
    EvidenceStore,
    build_manifest,
    canonical_json,
    check_bundle,
    merkle_root,
    verify_manifest,
    write_bundle,
)
from verity_schema import EvidenceKind, Label, Verdict, VerificationReport


def _store(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "evidence")


def test_evidence_is_content_addressed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
                      payload={"a": 1}, produced_by="test")
    second = store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
                       payload={"a": 1}, produced_by="test")
    assert first.id == second.id
    assert len(store) == 1


def test_different_payloads_get_different_addresses(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
                  payload={"a": 1}, produced_by="test")
    b = store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
                  payload={"a": 2}, produced_by="test")
    assert a.id != b.id


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_merkle_root_is_independent_of_leaf_order() -> None:
    leaves = ["aa" * 32, "bb" * 32, "cc" * 32]
    assert merkle_root(leaves) == merkle_root(list(reversed(leaves)))


def test_empty_merkle_root_is_defined() -> None:
    assert merkle_root([]).startswith("sha256:")


def test_manifest_verifies_against_its_own_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(kind=EvidenceKind.COMPUTED, label=Label.OBSERVED,
                       payload={"x": 1}, produced_by="test")
    manifest = build_manifest([record], {record.id: 10})
    assert verify_manifest(manifest)


def _report() -> VerificationReport:
    return VerificationReport(
        verdict=Verdict.PASS, contract_name="c", contract_version="0.1.0",
        contract_fingerprint="sha256:x",
        started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T00:00:01Z",
    )


def test_bundle_round_trips_and_verifies(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
              payload={"total": "14800.00"}, produced_by="connector:ledger")
    bundle = write_bundle(tmp_path / "bundle", report=_report(), store=store)

    manifest = check_bundle(bundle)
    assert len(manifest.entries) == 1
    assert (bundle / "report.json").exists()


def test_a_tampered_evidence_object_fails_verification(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
              payload={"total": "14800.00"}, produced_by="connector:ledger")
    bundle = write_bundle(tmp_path / "bundle", report=_report(), store=store)

    target = next((bundle / "objects").rglob("*.json"))
    target.write_text('{"total":"148000.00"}', encoding="utf-8")

    with pytest.raises(BundleIntegrityError, match="altered"):
        check_bundle(bundle)


def test_a_tampered_manifest_fails_verification(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
              payload={"total": "14800.00"}, produced_by="connector:ledger")
    bundle = write_bundle(tmp_path / "bundle", report=_report(), store=store)

    manifest_path = bundle / "manifest.json"
    payload = json.loads(manifest_path.read_text("utf-8"))
    payload["merkle_root"] = "sha256:" + "00" * 32
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BundleIntegrityError, match="merkle root"):
        check_bundle(bundle)


def test_a_missing_evidence_object_fails_verification(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(kind=EvidenceKind.API_RESPONSE, label=Label.OBSERVED,
              payload={"total": "1"}, produced_by="t")
    bundle = write_bundle(tmp_path / "bundle", report=_report(), store=store)
    next((bundle / "objects").rglob("*.json")).unlink()

    with pytest.raises(BundleIntegrityError, match="missing"):
        check_bundle(bundle)
