"""The Verity verifier: deterministic evaluation of Outcome Contracts.

This package is independent of any executor by design. It imports the schema,
the evidence store, the connectors and the document extractor -- and nothing
that runs a workflow. ``import-linter`` enforces that, so the boundary cannot
erode one convenient import at a time.

There are no model calls here. A promoted verification costs nothing per run,
which is the only reason continuous verification is affordable at all.
"""

from .divergence import EnvironmentChange, detect_environment_changes, localize
from .errors import ContractError, ContractLoadError, ContractTypeError
from .expr import EvaluationError, ExpressionSyntaxError, parse, render
from .parse import (
    contract_line_of,
    discover_contracts,
    load_contract,
    load_contract_text,
)
from .resolve import FactResolver, ResolutionOutcome, infer_write_channel
from .typecheck import CheckedAssertion, CheckedContract, typecheck
from .verify import (
    VerificationOutcome,
    VerifyOptions,
    decide,
    missing_inputs,
    verify,
    verify_checked,
)

__version__ = "0.0.1"

__all__ = [
    "CheckedAssertion", "CheckedContract", "ContractError", "ContractLoadError",
    "ContractTypeError", "EnvironmentChange", "EvaluationError",
    "ExpressionSyntaxError", "FactResolver", "ResolutionOutcome",
    "VerificationOutcome", "VerifyOptions", "__version__", "contract_line_of",
    "decide", "detect_environment_changes", "discover_contracts", "infer_write_channel",
    "load_contract", "load_contract_text", "localize", "missing_inputs", "parse",
    "render", "typecheck", "verify", "verify_checked",
]
