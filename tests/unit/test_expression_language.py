"""The expression language: parsing, evaluation and its security properties."""

from __future__ import annotations

from decimal import Decimal

import pytest
from verity_verifier.expr import (
    EvaluationError,
    ExpressionSyntaxError,
    Scope,
    evaluate,
    parse,
    referenced_roots,
    render,
)

FACTS = {
    "doc": {"total": Decimal("148000.00"), "vendor": "Acme Supplies ", "number": "INV-4471"},
    "po": {"total": Decimal("14800.00"), "vendor": "ACME   supplies", "currency": "USD"},
    "bill": {"status": "DRAFT", "amount": "14800.00"},
    "rows": [{"id": 1, "kind": "create"}, {"id": 2, "kind": "update"}],
    "dupes": [{"id": 1, "n": "A"}, {"id": 2, "n": "A"}],
}
SCOPE = Scope(facts=FACTS, inputs={"invoice_number": "INV-4471"})


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("po.total < doc.total", True),
        ("within(doc.total, po.total, tolerance = 0.01)", False),
        ("within(po.total, po.total, tolerance = 0.01)", True),
        ('doc.vendor ~= po.vendor', True),
        ('doc.vendor == po.vendor', False),
        ('bill.status == "DRAFT"', True),
        ("count(rows) == 2", True),
        ('any(rows, kind == "create")', True),
        ('none(rows, kind == "payment")', True),
        ('all(rows, id > 0)', True),
        ("unique(rows, id)", True),
        ("unique(dupes, n)", False),
        ("exists(doc.number)", True),
        ("is_null(doc.number)", False),
        ('doc.number matches "^INV-"', True),
        ('doc.number in ["INV-4471", "INV-0001"]', True),
        ('bill.status in ["POSTED", "VOID"]', False),
        ("not (po.total > doc.total)", True),
        ("po.total > 0 and doc.total > 0", True),
        ("po.total > 999999 or doc.total > 0", True),
        ("within(bill.amount, po.total, tolerance = 0.01)", True),  # numeric string coercion
    ],
)
def test_evaluation(expression: str, expected: bool) -> None:
    assert bool(evaluate(parse(expression), SCOPE)) is expected


def test_normalized_equality_folds_case_and_whitespace() -> None:
    assert evaluate(parse("doc.vendor ~= po.vendor"), SCOPE) is True


def test_render_round_trips_to_canonical_source() -> None:
    source = "within(doc.total, po.total, tolerance = 0.01)"
    assert render(parse(source)) == source


def test_referenced_roots_drive_strength_derivation() -> None:
    assert referenced_roots(parse("within(doc.total, po.total, tolerance = 0.01)")) == {
        "doc", "po"
    }


class TestNoCodeExecution:
    """The language must have no path to the Python interpreter."""

    @pytest.mark.security()
    @pytest.mark.parametrize(
        "hostile",
        [
            "__import__('os').system('id')",
            "doc.__class__",
            "eval('1+1')",
            "exec('x=1')",
            "open('/etc/passwd')",
            "doc.total; import os",
            "lambda: 1",
            "[x for x in range(10)]",
            "doc.total if True else 0",
            "globals()",
        ],
    )
    def test_hostile_input_never_executes(self, hostile: str) -> None:
        with pytest.raises((ExpressionSyntaxError, EvaluationError, ValueError)):
            evaluate(parse(hostile), SCOPE)

    @pytest.mark.security()
    def test_dunder_attributes_are_not_reachable(self) -> None:
        with pytest.raises(EvaluationError):
            evaluate(parse("doc.items"), SCOPE)


class TestSyntaxErrors:
    def test_assignment_is_rejected_with_a_helpful_message(self) -> None:
        with pytest.raises(ExpressionSyntaxError, match="use '=='"):
            parse("doc.total = 5")

    def test_unterminated_string(self) -> None:
        with pytest.raises(ExpressionSyntaxError, match="unterminated"):
            parse('doc.total == "oops')

    def test_deep_nesting_is_bounded(self) -> None:
        with pytest.raises(ExpressionSyntaxError, match="too deeply"):
            parse("(" * 64 + "1" + ")" * 64)

    def test_oversized_expression_is_rejected(self) -> None:
        with pytest.raises(ExpressionSyntaxError, match="exceeds"):
            parse("doc.total == 1 and " * 400 + "1 == 1")

    def test_unknown_function_fails_at_evaluation(self) -> None:
        with pytest.raises(EvaluationError, match="unknown function"):
            evaluate(parse("frobnicate(doc.total)"), SCOPE)


class TestUnresolvedReferences:
    def test_missing_fact_is_reported_not_swallowed(self) -> None:
        with pytest.raises(EvaluationError, match="not resolved"):
            evaluate(parse("ghost.total > 0"), SCOPE)

    def test_missing_field_is_reported(self) -> None:
        with pytest.raises(EvaluationError, match="no field"):
            evaluate(parse("doc.nonexistent > 0"), SCOPE)


def test_regex_pattern_length_is_bounded() -> None:
    with pytest.raises(EvaluationError, match="exceeds"):
        evaluate(parse(f'doc.number matches "{"a" * 600}"'), SCOPE)
