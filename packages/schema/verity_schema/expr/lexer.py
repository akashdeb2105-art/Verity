"""Tokenizer for the Verity assertion expression language.

The language is hand-tokenized and hand-parsed rather than lowered onto
Python's :mod:`ast`. That costs a few hundred lines and buys a property worth
far more: "no arbitrary code execution" is true by construction, not by a
whitelist that a future contributor might widen. There is no code path from a
contract file to the Python interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum, auto

MAX_EXPRESSION_LENGTH = 4_000


class ExpressionSyntaxError(ValueError):
    """Raised for any malformed expression. Always a compile-time error."""

    def __init__(self, message: str, position: int, expression: str) -> None:
        self.position = position
        self.expression = expression
        caret = " " * position + "^"
        super().__init__(f"{message}\n  {expression}\n  {caret}")


class TokenType(Enum):
    NUMBER = auto()
    STRING = auto()
    IDENT = auto()
    OP = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    COMMA = auto()
    COLON = auto()
    DOT = auto()
    EOF = auto()


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    position: int
    number: Decimal | None = None


# Longest first: '<=' must win over '<'.
_OPERATORS = ("==", "!=", "<=", ">=", "~=", "<", ">")

KEYWORDS = frozenset({"and", "or", "not", "in", "matches", "true", "false", "null"})

_IDENT_START = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_BODY = _IDENT_START | frozenset("0123456789")
_DIGITS = frozenset("0123456789")


def tokenize(expression: str) -> list[Token]:
    """Turn an expression into tokens, or raise :class:`ExpressionSyntaxError`."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ExpressionSyntaxError(
            f"expression exceeds {MAX_EXPRESSION_LENGTH} characters", 0, expression[:80]
        )

    tokens: list[Token] = []
    i = 0
    n = len(expression)

    while i < n:
        ch = expression[i]

        if ch in " \t\r\n":
            i += 1
            continue

        if ch == "#":  # comment to end of line
            while i < n and expression[i] != "\n":
                i += 1
            continue

        if ch in ("'", '"'):
            value, i = _read_string(expression, i)
            tokens.append(Token(TokenType.STRING, value, i))
            continue

        if ch in _DIGITS:
            raw, i = _read_number(expression, i)
            try:
                number = Decimal(raw)
            except InvalidOperation as exc:  # pragma: no cover - guarded by _read_number
                raise ExpressionSyntaxError(f"invalid number {raw!r}", i, expression) from exc
            tokens.append(Token(TokenType.NUMBER, raw, i, number))
            continue

        if ch in _IDENT_START:
            start = i
            while i < n and expression[i] in _IDENT_BODY:
                i += 1
            tokens.append(Token(TokenType.IDENT, expression[start:i], start))
            continue

        matched = next((op for op in _OPERATORS if expression.startswith(op, i)), None)
        if matched:
            tokens.append(Token(TokenType.OP, matched, i))
            i += len(matched)
            continue

        simple = {
            "(": TokenType.LPAREN, ")": TokenType.RPAREN,
            "[": TokenType.LBRACKET, "]": TokenType.RBRACKET,
            ",": TokenType.COMMA, ":": TokenType.COLON, ".": TokenType.DOT,
        }.get(ch)
        if simple is not None:
            tokens.append(Token(simple, ch, i))
            i += 1
            continue

        if ch == "=":
            # A bare '=' is only ever a keyword-argument separator. The parser
            # rejects it anywhere else with a position-aware message.
            tokens.append(Token(TokenType.OP, "=", i))
            i += 1
            continue

        raise ExpressionSyntaxError(f"unexpected character {ch!r}", i, expression)

    tokens.append(Token(TokenType.EOF, "", n))
    return tokens


def _read_string(expression: str, start: int) -> tuple[str, int]:
    quote = expression[start]
    i = start + 1
    out: list[str] = []
    while i < len(expression):
        ch = expression[i]
        if ch == "\\":
            if i + 1 >= len(expression):
                raise ExpressionSyntaxError("dangling escape", i, expression)
            nxt = expression[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise ExpressionSyntaxError("unterminated string", start, expression)


def _read_number(expression: str, start: int) -> tuple[str, int]:
    i = start
    seen_dot = False
    while i < len(expression):
        ch = expression[i]
        if ch in _DIGITS:
            i += 1
        elif ch == "_" and i > start:
            i += 1  # digit separator, stripped below
        elif (
            ch == "."
            and not seen_dot
            and i + 1 < len(expression)
            and expression[i + 1] in _DIGITS
        ):
            seen_dot = True
            i += 1
        else:
            break
    return expression[start:i].replace("_", ""), i
