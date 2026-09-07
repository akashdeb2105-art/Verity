"""The model provider boundary.

One rule governs everything in this package: **a model proposes, deterministic
code decides.** A provider returns structured data and nothing more. It cannot
cause an action, it cannot satisfy an assertion, and every value it produces is
labelled ``INFERRED`` so that the verifier's strength rules treat it as the
weaker evidence it is.

The verifier does not import this package, and an import-linter contract keeps
it that way. Verification stays deterministic; intelligence lives on the
authoring side.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

DEFAULT_TIMEOUT = 60.0
MAX_ATTEMPTS = 3


class ProviderError(Exception):
    """The model could not be reached, or would not return usable output.

    Never fatal to the caller. Enrichment is an improvement on a deterministic
    result that already exists, so a provider failure degrades to that result
    rather than failing the command.
    """


class BudgetExceededError(ProviderError):
    pass


@dataclass
class Budget:
    """A hard ceiling. Authoring may cost money; it may not cost surprises."""

    max_calls: int = 6
    max_input_chars: int = 120_000
    max_usd: float = 0.50

    calls: int = 0
    input_chars: int = 0
    usd: float = 0.0
    #: False once a call was made whose cost this build could not price.
    priced: bool = True

    def charge(self, prompt_chars: int, usd: float | None = 0.0) -> None:
        if self.calls + 1 > self.max_calls:
            raise BudgetExceededError(f"call budget exhausted ({self.max_calls} calls)")
        if self.input_chars + prompt_chars > self.max_input_chars:
            raise BudgetExceededError("input budget exhausted")
        # An unknown price cannot be added to a running total, so a call whose
        # cost this build cannot price is capped by the call ceiling alone.
        if usd is not None and self.usd + usd > self.max_usd:
            raise BudgetExceededError(f"cost budget exhausted (${self.max_usd:.2f})")
        self.calls += 1
        self.input_chars += prompt_chars
        if usd is None:
            self.priced = False
        else:
            self.usd += usd

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_chars": self.input_chars,
            "usd": round(self.usd, 4) if self.priced else None,
        }


@dataclass(frozen=True)
class Completion:
    """One model response, already parsed into data."""

    data: dict[str, Any]
    model: str
    raw: str = field(repr=False, default="")
    #: None when this build cannot price the model that answered.
    usd: float | None = 0.0


@runtime_checkable
class Provider(Protocol):
    """Anything that can turn a prompt into structured data."""

    name: str
    model: str

    def complete_json(self, system: str, user: str, *, schema_hint: str = "") -> Completion: ...

    def available(self) -> bool: ...


class NullProvider:
    """No model configured. Every call fails politely and immediately.

    This is the default. Verity works without a provider; the AI layer only
    ever adds suggestions on top of a result that already exists.
    """

    name = "none"
    model = "none"

    def complete_json(self, system: str, user: str, *, schema_hint: str = "") -> Completion:
        raise ProviderError(
            "no model provider is configured. Set VERITY_LLM_PROVIDER and the "
            "matching API key, or run without --ai."
        )

    def available(self) -> bool:
        return False


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Models wrap JSON in prose and fences no matter how firmly they are asked
    not to. Recovering it here is not a hack; it is the difference between a
    usable layer and one that fails a third of the time.
    """
    candidate = text.strip()

    fenced = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError(f"no JSON object in response: {text[:200]!r}") from None
        try:
            parsed = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError(f"malformed JSON in response: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ProviderError("expected a JSON object at the top level")
    return parsed


def env(*names: str) -> str | None:
    """First non-empty environment variable, loading .env if present."""
    _load_dotenv()
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Read a local .env once. Never logs a value, never writes one anywhere."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    from pathlib import Path

    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / ".env"
        if not candidate.is_file():
            continue
        try:
            for line in candidate.read_text("utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
        except OSError:  # pragma: no cover - unreadable .env is not fatal
            pass
        return
