"""Recursive-descent parser for the assertion expression language.

Grammar (complete -- there is nothing else the language can express):

    expression  := or_expr
    or_expr     := and_expr ( "or" and_expr )*
    and_expr    := not_expr ( "and" not_expr )*
    not_expr    := "not" not_expr | comparison
    comparison  := primary ( COMPARE_OP primary )?
    primary     := literal | list | call | path | "(" expression ")"
    list        := "[" ( expression ( "," expression )* )? "]"
    call        := IDENT "(" arguments? ")"
    arguments   := argument ( "," argument )*
    argument    := IDENT ":" expression | expression
    path        := IDENT ( "." IDENT )*
    literal     := NUMBER | STRING | "true" | "false" | "null"

There is no arithmetic, no assignment, no indexing by expression, no lambda
and no attribute call. Aggregation is done by the closed set of builtin
functions in :mod:`verity_verifier.expr.evaluator`.
"""

from __future__ import annotations

from .lexer import ExpressionSyntaxError, Token, TokenType, tokenize
from .nodes import BoolOp, Call, Compare, Expr, ListLit, Literal, Not, Path

COMPARE_OPS = frozenset({"==", "!=", "<", "<=", ">", ">=", "~=", "in", "matches"})

_MAX_DEPTH = 32


def parse(expression: str) -> Expr:
    """Parse an expression, or raise :class:`ExpressionSyntaxError`."""
    return _Parser(expression, tokenize(expression)).parse()


class _Parser:
    def __init__(self, source: str, tokens: list[Token]) -> None:
        self._source = source
        self._tokens = tokens
        self._pos = 0
        self._depth = 0

    # -- helpers ---------------------------------------------------------
    @property
    def _current(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _at_keyword(self, word: str) -> bool:
        tok = self._current
        return tok.type is TokenType.IDENT and tok.value == word

    def _expect(self, ttype: TokenType, what: str) -> Token:
        if self._current.type is not ttype:
            raise self._error(f"expected {what}")
        return self._advance()

    def _error(self, message: str) -> ExpressionSyntaxError:
        return ExpressionSyntaxError(message, self._current.position, self._source)

    # -- grammar ---------------------------------------------------------
    def parse(self) -> Expr:
        node = self._or_expr()
        if self._current.type is not TokenType.EOF:
            raise self._error(f"unexpected trailing input {self._current.value!r}")
        return node

    def _guard_depth(self) -> None:
        if self._depth > _MAX_DEPTH:
            raise self._error("expression nested too deeply")

    def _or_expr(self) -> Expr:
        self._depth += 1
        self._guard_depth()
        operands = [self._and_expr()]
        position = self._current.position
        while self._at_keyword("or"):
            self._advance()
            operands.append(self._and_expr())
        self._depth -= 1
        return operands[0] if len(operands) == 1 else BoolOp("or", tuple(operands), position)

    def _and_expr(self) -> Expr:
        operands = [self._not_expr()]
        position = self._current.position
        while self._at_keyword("and"):
            self._advance()
            operands.append(self._not_expr())
        return operands[0] if len(operands) == 1 else BoolOp("and", tuple(operands), position)

    def _not_expr(self) -> Expr:
        if self._at_keyword("not"):
            token = self._advance()
            return Not(self._not_expr(), token.position)
        return self._comparison()

    def _comparison(self) -> Expr:
        left = self._primary()
        tok = self._current
        if tok.type is TokenType.OP and tok.value == "=":
            raise self._error("'=' is not an operator; use '==' for equality")
        op: str | None = None
        is_symbolic = tok.type is TokenType.OP and tok.value in COMPARE_OPS
        is_worded = tok.type is TokenType.IDENT and tok.value in ("in", "matches")
        if is_symbolic or is_worded:
            op = tok.value
        if op is None:
            return left
        self._advance()
        right = self._primary()
        return Compare(op, left, right, tok.position)

    def _primary(self) -> Expr:
        tok = self._current

        if tok.type is TokenType.LPAREN:
            self._depth += 1
            self._guard_depth()
            self._advance()
            inner = self._or_expr()
            self._expect(TokenType.RPAREN, "')'")
            self._depth -= 1
            return inner

        if tok.type is TokenType.LBRACKET:
            self._depth += 1
            self._guard_depth()
            self._advance()
            items: list[Expr] = []
            if self._current.type is not TokenType.RBRACKET:
                while True:
                    items.append(self._or_expr())
                    if self._current.type is TokenType.COMMA:
                        self._advance()
                        continue
                    break
            self._expect(TokenType.RBRACKET, "']'")
            self._depth -= 1
            return ListLit(tuple(items), tok.position)

        if tok.type is TokenType.NUMBER:
            self._advance()
            assert tok.number is not None
            return Literal(tok.number, tok.position)

        if tok.type is TokenType.STRING:
            self._advance()
            return Literal(tok.value, tok.position)

        if tok.type is TokenType.IDENT:
            if tok.value == "true":
                self._advance()
                return Literal(True, tok.position)
            if tok.value == "false":
                self._advance()
                return Literal(False, tok.position)
            if tok.value == "null":
                self._advance()
                return Literal(None, tok.position)
            if tok.value in ("and", "or", "not"):
                raise self._error(f"unexpected keyword {tok.value!r}")

            self._advance()
            if self._current.type is TokenType.LPAREN:
                return self._call(tok)
            return self._path(tok)

        raise self._error(f"unexpected token {tok.value!r}" if tok.value else "unexpected end")

    def _path(self, first: Token) -> Path:
        segments = [first.value]
        while self._current.type is TokenType.DOT:
            self._advance()
            segments.append(self._expect(TokenType.IDENT, "an identifier after '.'").value)
        return Path(tuple(segments), first.position)

    def _call(self, name: Token) -> Call:
        self._depth += 1
        self._guard_depth()
        self._expect(TokenType.LPAREN, "'('")
        args: list[Expr] = []
        kwargs: list[tuple[str, Expr]] = []

        if self._current.type is not TokenType.RPAREN:
            while True:
                # keyword argument:  IDENT ( ':' | '=' ) expression
                # '=' is supported because ': ' inside a YAML scalar starts a
                # mapping, which makes the colon form awkward in contract files.
                ahead = self._pos + 1
                nxt = self._tokens[ahead] if ahead < len(self._tokens) else None
                is_kwarg = (
                    self._current.type is TokenType.IDENT
                    and nxt is not None
                    and (
                        nxt.type is TokenType.COLON
                        or (nxt.type is TokenType.OP and nxt.value == "=")
                    )
                )
                if is_kwarg:
                    key = self._advance().value
                    self._advance()  # ':' or '='
                    kwargs.append((key, self._or_expr()))
                else:
                    if kwargs:
                        raise self._error("positional argument after keyword argument")
                    args.append(self._or_expr())
                if self._current.type is TokenType.COMMA:
                    self._advance()
                    continue
                break

        self._expect(TokenType.RPAREN, "')'")
        self._depth -= 1
        return Call(name.value, tuple(args), tuple(kwargs), name.position)
