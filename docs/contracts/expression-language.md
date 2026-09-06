# The assertion expression language

Small, closed and deterministic. It is hand-tokenised and hand-parsed rather
than lowered onto Python's `ast`, which costs a few hundred lines and buys a
property worth far more: **"no arbitrary code execution" is true by
construction**, not by a whitelist a future contributor might widen. There is
no path from a contract file to the Python interpreter.

## Grammar

Complete. There is nothing else the language can express.

```
expression  := or_expr
or_expr     := and_expr ( "or" and_expr )*
and_expr    := not_expr ( "and" not_expr )*
not_expr    := "not" not_expr | comparison
comparison  := primary ( COMPARE_OP primary )?
primary     := literal | list | call | path | "(" expression ")"
list        := "[" ( expression ( "," expression )* )? "]"
call        := IDENT "(" arguments? ")"
arguments   := argument ( "," argument )*
argument    := IDENT ( ":" | "=" ) expression | expression
path        := IDENT ( "." IDENT )*
literal     := NUMBER | STRING | "true" | "false" | "null"
```

No arithmetic. No assignment. No indexing by expression. No lambda. No method
calls. Aggregation is done by the closed set of builtins below.

## Operators

| Operator | Meaning |
| --- | --- |
| `==` `!=` | Equality, with numeric coercion across strings and decimals |
| `<` `<=` `>` `>=` | Ordering, numeric or temporal |
| `~=` | Normalised string equality: NFKC, case-folded, whitespace collapsed |
| `matches` | Regular expression search |
| `in` | Membership in a list literal or a fact collection |
| `and` `or` `not` | Boolean logic |

`~=` is the one to reach for when comparing names across systems. `"Acme
Supplies "` and `"ACME  supplies"` are the same vendor.

## Functions

| Function | Meaning |
| --- | --- |
| `within(a, b, tolerance = t)` | `abs(a - b) <= t` — the money comparison |
| `abs(x)` | Absolute value |
| `sum(collection, field = "name")` | Sum, optionally over a field |
| `count(collection)` | Size |
| `any(collection, pred…)` | At least one item satisfies every predicate |
| `all(collection, pred…)` | Every item does |
| `none(collection, pred…)` | No item does |
| `unique(collection, field…)` | No two items share the key |
| `exists(x)` / `is_null(x)` | Presence |
| `matches(value, pattern)` | Regular expression, as a function |
| `before(a, b)` / `after(a, b)` | Temporal ordering |
| `within_window(t, start, end)` | Temporal containment |

Inside `any`, `all`, `none` and `unique`, a bare identifier is a field of the
current item:

```
none(ledger_events, kind == "payment", ref == inputs.invoice_number)
```

## Numbers

Every numeric literal is a `Decimal`. Values arriving as numeric strings —
`"14800.00"` from a JSON API — are coerced for comparison, so a decimal from a
PDF and a string from an API compare correctly and their difference renders as
a real delta.

## Errors are compile-time

Undefined facts, unknown functions and malformed expressions are rejected by
`verity lint` before any system is touched. A contract that cannot be
evaluated must never silently pass.

```
$ verity lint contracts/
  FAIL  contracts/broken.yaml
        assertion 'a': references undefined fact 'ghost'; declared facts are: bill, doc, po
```

## Bounds

Expressions are capped at 4,000 characters and 32 levels of nesting. Regular
expression patterns are capped at 512 characters and subjects at 100,000, which
limits — but does not eliminate — catastrophic backtracking. Contracts are
authored by the people running them and reviewed in pull requests; treat a
pattern in a contract with the same care as a pattern in your own code.
