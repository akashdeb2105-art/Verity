"""Cassettes make a suite hermetic, which is what makes it a pull-request check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from verity_connectors import (
    Cassette,
    CassetteConnector,
    CassetteMissError,
    CassetteMode,
    ConnectorRegistry,
)


class _Unreachable:
    """Stands in for a system that is not available. Any call is a failure."""

    name = "ledger"
    capabilities = frozenset({"accounting"})
    channel = "api"

    def read(self, resource: str, *, key: Any = None, query: Any = None) -> Any:
        raise AssertionError("replay must not reach the live system")

    def health(self) -> bool:
        raise AssertionError("replay must not reach the live system")


def _record_registry(sandbox_client: Any, cassette: Cassette) -> ConnectorRegistry:
    from verity_connectors import build_sandbox_registry

    registry = build_sandbox_registry("http://sandbox", client=sandbox_client)
    for name in list(registry.names):
        inner = registry.get(name)
        if inner is not None and hasattr(inner, "routes"):
            registry.register(CassetteConnector(inner, cassette))
    return registry


def test_recording_then_replaying_gives_the_same_verdict(
    sandbox_client: Any, checked_contract: Any, tmp_path: Path
) -> None:
    from verity_verifier import VerifyOptions, verify_checked

    from tests.conftest import DEMO_INPUTS

    path = tmp_path / "ledger.cassette.json"

    recording = Cassette(path, CassetteMode.RECORD)
    registry = _record_registry(sandbox_client, recording)
    recorded = verify_checked(
        checked_contract,
        VerifyOptions(registry=registry, evidence_dir=str(tmp_path / "e1"),
                      inputs=dict(DEMO_INPUTS)),
    ).report
    recording.save()

    assert path.exists()
    assert len(recording) > 0

    # Replay with the live systems replaced by objects that refuse to be called.
    replaying = Cassette(path, CassetteMode.REPLAY)
    offline = ConnectorRegistry()
    for name in ("po_system", "ledger"):
        connector = _Unreachable()
        connector.name = name  # type: ignore[misc]
        offline.register(CassetteConnector(connector, replaying))
    # Documents still come from the sandbox: cassettes cover connector reads.
    from verity_connectors import DocumentConnector

    offline.register(DocumentConnector("http://sandbox", client=sandbox_client))

    replayed = verify_checked(
        checked_contract,
        VerifyOptions(registry=offline, evidence_dir=str(tmp_path / "e2"),
                      inputs=dict(DEMO_INPUTS)),
    ).report

    assert replayed.verdict is recorded.verdict
    assert [a.passed for a in replayed.assertions] == [a.passed for a in recorded.assertions]


def test_a_missing_interaction_is_an_explicit_miss_not_a_silent_pass(
    tmp_path: Path,
) -> None:
    cassette = Cassette(tmp_path / "empty.json", CassetteMode.REPLAY)
    connector = CassetteConnector(_Unreachable(), cassette)
    with pytest.raises(CassetteMissError):
        connector.read("bill", query={"ref": "INV-4471"})


def test_cassette_files_are_written_deterministically(
    sandbox_client: Any, checked_contract: Any, tmp_path: Path
) -> None:
    from verity_verifier import VerifyOptions, verify_checked

    from tests.conftest import DEMO_INPUTS

    contents = []
    for index in range(2):
        path = tmp_path / f"c{index}.json"
        cassette = Cassette(path, CassetteMode.RECORD)
        registry = _record_registry(sandbox_client, cassette)
        verify_checked(
            checked_contract,
            VerifyOptions(registry=registry, evidence_dir=str(tmp_path / f"e{index}"),
                          inputs=dict(DEMO_INPUTS)),
        )
        cassette.save()
        contents.append(path.read_text("utf-8"))

    assert contents[0] == contents[1]


def test_cassette_mode_off_is_transparent(sandbox_client: Any, tmp_path: Path) -> None:
    from verity_connectors import ledger_connector

    cassette = Cassette(tmp_path / "unused.json", CassetteMode.OFF)
    connector = CassetteConnector(
        ledger_connector("http://sandbox", client=sandbox_client), cassette
    )
    result = connector.read("bill", query={"ref": "INV-4471"})
    assert result.cardinality == 1
    assert len(cassette) == 0
