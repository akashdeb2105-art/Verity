"""Static contract checking: what must be rejected before anything is read."""

from __future__ import annotations

import pytest
from verity_schema import Strength
from verity_verifier import (
    ContractLoadError,
    ContractTypeError,
    load_contract,
    load_contract_text,
    typecheck,
)

BASE = """
apiVersion: verity/v1
kind: OutcomeContract
metadata: {{name: t, version: 0.1.0, risk: {risk}}}
sources:
  api: {{kind: connector, capability: purchase_orders}}
  ui: {{kind: trace}}
facts:
  - id: po
    source: api
    read: {{resource: purchase_order, key: PO-1}}
  - id: screen
    source: ui
    read: {{resource: dom_text, query: {{selector: "#x"}}}}
expected:
{assertions}
"""


def build(assertions: str, risk: str = "LOW") -> object:
    return load_contract_text(BASE.format(assertions=assertions, risk=risk))


def test_undefined_fact_is_a_compile_error() -> None:
    with pytest.raises(ContractTypeError, match="undefined fact 'ghost'"):
        typecheck(build("  - id: a\n    assert: 'ghost.total > 0'"))


def test_unknown_function_is_a_compile_error() -> None:
    with pytest.raises(ContractTypeError, match="unknown function"):
        typecheck(build("  - id: a\n    assert: 'frobnicate(po.total)'"))


def test_malformed_expression_is_a_compile_error() -> None:
    with pytest.raises(ContractTypeError):
        typecheck(build("  - id: a\n    assert: 'po.total >>'"))


def test_strength_is_derived_from_sources_not_declared() -> None:
    checked = typecheck(build("  - id: a\n    assert: 'po.total > 0'"))
    assert checked.assertions[0].strength is Strength.STRONG


def test_trace_sourced_assertions_are_weak() -> None:
    checked = typecheck(build("  - id: a\n    assert: 'screen.value ~= \"Draft\"'"))
    assert checked.assertions[0].strength is Strength.WEAK


def test_mixing_a_trace_fact_weakens_the_whole_assertion() -> None:
    checked = typecheck(
        build("  - id: a\n    assert: 'po.total > 0 and screen.value ~= \"Draft\"'")
    )
    assert checked.assertions[0].strength is Strength.WEAK


def test_declared_strength_must_match_the_derived_one() -> None:
    with pytest.raises(ContractTypeError, match="declared strength"):
        typecheck(
            build("  - id: a\n    strength: WEAK\n    assert: 'po.total > 0'")
        )


def test_constant_assertions_prove_nothing_and_are_rejected() -> None:
    with pytest.raises(ContractTypeError, match="no observable source"):
        typecheck(build("  - id: a\n    assert: '1 == 1'"))


def test_medium_risk_without_a_strong_assertion_warns_loudly() -> None:
    checked = typecheck(
        build("  - id: a\n    assert: 'screen.value ~= \"Draft\"'", risk="MEDIUM")
    )
    assert any("cannot verify business state" in w for w in checked.warnings)


def test_unused_facts_are_reported() -> None:
    checked = typecheck(build("  - id: a\n    assert: 'po.total > 0'"))
    assert any("screen" in w and "never asserted" in w for w in checked.warnings)


def test_duplicate_assertion_ids_are_rejected() -> None:
    with pytest.raises(ContractLoadError, match="duplicate assertion ids"):
        build("  - id: a\n    assert: 'po.total > 0'\n  - id: a\n    assert: 'po.total > 1'")


def test_reference_contract_typechecks_cleanly() -> None:
    from tests.conftest import CONTRACT_PATH

    checked = typecheck(load_contract(CONTRACT_PATH))
    assert checked.warnings == []
    assert all(a.strength is Strength.STRONG for a in checked.assertions)
