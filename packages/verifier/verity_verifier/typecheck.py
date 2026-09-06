"""Static checking of an Outcome Contract, before any fact is read.

Two jobs:

1. Reject contracts that cannot be evaluated -- undefined facts, unknown
   functions, malformed expressions. These are compile-time errors, because a
   contract that silently skips an assertion is worse than no contract.
2. Derive each assertion's ``strength`` from the sources it actually touches.
   Strength is computed, never trusted from the file: an assertion is only as
   strong as the weakest source it reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from verity_schema import (
    Assertion,
    OutcomeContract,
    RiskLevel,
    SourceKind,
    SourceSpec,
    Strength,
)
from verity_schema.expr import FUNCTION_NAMES as _FUNCTIONS

from .errors import ContractTypeError
from .expr import Call, Expr, Path, parse, referenced_roots, walk

RESERVED_ROOTS = frozenset({"inputs"})


@dataclass
class CheckedAssertion:
    assertion: Assertion
    ast: Expr
    strength: Strength
    fact_refs: frozenset[str]
    is_forbidden: bool = False
    """Declared in the contract's ``forbidden`` block. Forbidden entries are
    written as negative assertions that must hold -- 'no payment was made' --
    so they evaluate identically and are only labelled differently in reports."""

    notes: list[str] = field(default_factory=list)


@dataclass
class CheckedContract:
    contract: OutcomeContract
    assertions: list[CheckedAssertion]
    warnings: list[str] = field(default_factory=list)

    @property
    def by_id(self) -> dict[str, CheckedAssertion]:
        return {c.assertion.id: c for c in self.assertions}


def typecheck(contract: OutcomeContract) -> CheckedContract:
    """Check a contract and derive assertion strengths, or raise."""
    fact_ids = {f.id for f in contract.facts}
    source_of_fact = {f.id: contract.sources[f.source] for f in contract.facts}
    warnings: list[str] = []

    forbidden_ids = {a.id for a in contract.forbidden}
    checked: list[CheckedAssertion] = []
    for assertion in contract.all_assertions:
        location = f"assertion '{assertion.id}'"
        try:
            tree = parse(assertion.assert_)
        except ValueError as exc:
            raise ContractTypeError(str(exc), location=location) from exc

        _check_functions(tree, location)
        refs = _check_references(tree, fact_ids, location)

        strength = _derive_strength(refs, source_of_fact)
        notes: list[str] = []

        if assertion.strength is not None and assertion.strength is not strength:
            raise ContractTypeError(
                f"declared strength {assertion.strength.value} but the facts it reads "
                f"make it {strength.value}",
                location=location,
            )
        if strength is Strength.INVALID:
            raise ContractTypeError(
                "references a fact with no observable source", location=location
            )

        checked.append(
            CheckedAssertion(
                assertion=assertion, ast=tree, strength=strength,
                fact_refs=frozenset(refs), is_forbidden=assertion.id in forbidden_ids,
                notes=notes,
            )
        )

    warnings.extend(_check_risk_coverage(contract, checked))
    warnings.extend(_check_unused_facts(contract, checked))
    return CheckedContract(contract=contract, assertions=checked, warnings=warnings)


def _check_functions(tree: Expr, location: str) -> None:
    for node in walk(tree):
        if isinstance(node, Call) and node.name not in _FUNCTIONS:
            raise ContractTypeError(
                f"unknown function '{node.name}()' "
                f"(available: {', '.join(sorted(_FUNCTIONS))})",
                location=location,
            )


def _check_references(tree: Expr, fact_ids: set[str], location: str) -> set[str]:
    """Every root a path reads must be a declared fact or ``inputs``."""
    roots = referenced_roots(tree)
    unknown = roots - fact_ids - RESERVED_ROOTS
    if unknown:
        # A bare identifier inside any()/all()/none() is a field of the current
        # item, not a fact, so those are excluded before reporting.
        unknown = {r for r in unknown if not _is_predicate_local(tree, r)}
    if unknown:
        listed = ", ".join(sorted(f"'{u}'" for u in unknown))
        known = ", ".join(sorted(fact_ids | RESERVED_ROOTS)) or "(none)"
        raise ContractTypeError(
            f"references undefined fact {listed}; declared facts are: {known}",
            location=location,
        )
    return roots & fact_ids


def _is_predicate_local(tree: Expr, root: str) -> bool:
    """True when ``root`` only ever appears inside a collection predicate."""
    for node in walk(tree):
        if isinstance(node, Call) and node.name in ("any", "all", "none", "unique"):
            for predicate in node.args[1:]:
                if any(
                    isinstance(inner, Path) and inner.root == root
                    for inner in walk(predicate)
                ):
                    return True
    return False


def _derive_strength(refs: set[str], source_of_fact: Mapping[str, SourceSpec]) -> Strength:
    if not refs:
        # A constant assertion reads nothing observable, so it proves nothing.
        return Strength.INVALID
    kinds = set()
    for ref in refs:
        source = source_of_fact.get(ref)
        if source is None:
            return Strength.INVALID
        kinds.add(source.kind)
    if SourceKind.TRACE in kinds:
        return Strength.WEAK
    return Strength.STRONG


def _check_risk_coverage(
    contract: OutcomeContract, checked: list[CheckedAssertion]
) -> list[str]:
    """A MEDIUM-or-higher contract needs at least one STRONG blocking assertion.

    Without one, nothing here proves anything about business state, and the
    honest thing is to say so rather than to report a confident PASS.
    """
    if not contract.metadata.risk.requires_strong_verification:
        return []
    has_strong = any(
        c.strength is Strength.STRONG and c.assertion.severity.value == "BLOCKING"
        for c in checked
    )
    if has_strong:
        return []
    return [
        f"contract risk is {contract.metadata.risk.value} but no STRONG blocking "
        "assertion exists: this contract cannot verify business state and any "
        "workflow using it must fall back to human approval"
    ]


def _check_unused_facts(
    contract: OutcomeContract, checked: list[CheckedAssertion]
) -> list[str]:
    used: set[str] = set()
    for item in checked:
        used |= item.fact_refs
    unused = sorted({f.id for f in contract.facts} - used)
    return [f"fact '{name}' is resolved but never asserted on" for name in unused]


def _unused(_: RiskLevel) -> None:  # pragma: no cover
    return None
