"""Shared fixtures.

Every test runs against the in-process sandbox through FastAPI's test client,
so the suite needs no server, no network and no credentials. That is not just
convenience: a suite that cannot run hermetically cannot be a pull-request
check, and the whole CI story depends on it.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "examples" / "contracts" / "invoice_to_po.yaml"

DEMO_INPUTS = {"invoice_number": "INV-4471", "po_number": "PO-2211"}
SANDBOX_BASE = "http://sandbox"


@pytest.fixture()
def sandbox_app() -> Any:
    from verity_sandbox.app import create_app

    return create_app(seed=1)


@pytest.fixture()
def sandbox_client(sandbox_app: Any) -> Any:
    from fastapi.testclient import TestClient

    with TestClient(sandbox_app, base_url=SANDBOX_BASE) as client:
        yield client


@pytest.fixture()
def registry(sandbox_client: Any) -> Any:
    from verity_connectors import build_sandbox_registry

    return build_sandbox_registry(SANDBOX_BASE, client=sandbox_client)


@pytest.fixture()
def checked_contract() -> Any:
    from verity_verifier import load_contract, typecheck

    return typecheck(load_contract(CONTRACT_PATH))


@pytest.fixture()
def run_verification(registry: Any, checked_contract: Any, tmp_path: Path) -> Any:
    from verity_verifier import VerifyOptions, verify_checked

    counter = {"n": 0}

    def _run(**overrides: Any) -> Any:
        counter["n"] += 1
        options = VerifyOptions(
            registry=registry,
            evidence_dir=str(tmp_path / f"evidence-{counter['n']}"),
            inputs=dict(DEMO_INPUTS),
            **overrides,
        )
        return verify_checked(checked_contract, options)

    return _run
