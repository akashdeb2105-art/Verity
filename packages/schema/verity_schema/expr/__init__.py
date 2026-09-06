"""The assertion expression language: lexer, parser and AST.

This lives in the schema package because it is part of the *format*, not part
of verification. A contract file contains expressions; anything that reads or
writes a contract needs to parse them. Evaluating one is a separate job and
belongs to the verifier.

Putting it here is what lets the compiler validate a proposed expression --
does it parse, does it reference only declared facts -- without importing the
verifier. The two must stay independent: a contract that could only be checked
by the thing that wrote it would prove nothing.
"""

from .lexer import ExpressionSyntaxError, Token, TokenType, tokenize
from .nodes import (
    FUNCTION_NAMES,
    BoolOp,
    Call,
    Compare,
    Expr,
    ListLit,
    Literal,
    Not,
    Path,
    referenced_paths,
    referenced_roots,
    render,
    unknown_functions,
    walk,
)
from .parser import COMPARE_OPS, parse

__all__ = [
    "COMPARE_OPS",
    "FUNCTION_NAMES",
    "BoolOp",
    "Call",
    "Compare",
    "Expr",
    "ExpressionSyntaxError",
    "ListLit",
    "Literal",
    "Not",
    "Path",
    "Token",
    "TokenType",
    "parse",
    "referenced_paths",
    "referenced_roots",
    "render",
    "tokenize",
    "unknown_functions",
    "walk",
]
