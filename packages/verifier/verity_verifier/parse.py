"""Loading Outcome Contracts from YAML.

``yaml.safe_load`` only, always. A contract is data; loading one must never be
able to construct a Python object of the author's choosing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from verity_schema import OutcomeContract

from .errors import ContractLoadError


def load_contract_text(text: str, *, origin: str = "<string>") -> OutcomeContract:
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractLoadError(f"{origin}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ContractLoadError(f"{origin}: a contract must be a YAML mapping")

    try:
        return OutcomeContract.model_validate(raw)
    except ValidationError as exc:
        raise ContractLoadError(f"{origin}: {_format_validation_error(exc)}") from exc


def load_contract(path: str | Path) -> OutcomeContract:
    target = Path(path)
    if not target.exists():
        raise ContractLoadError(f"no such contract file: {target}")
    return load_contract_text(target.read_text("utf-8"), origin=str(target))


def discover_contracts(root: str | Path) -> list[Path]:
    """Every ``*.yaml`` / ``*.yml`` under ``root``, sorted for deterministic order."""
    base = Path(root)
    if base.is_file():
        return [base]
    found = [*base.rglob("*.yaml"), *base.rglob("*.yml")]
    return sorted(p for p in found if not p.name.startswith("."))


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"{location}: {error['msg']}")
    return "; ".join(lines)


def contract_line_of(path: str | Path, assertion_id: str) -> int | None:
    """Find the 1-based line where an assertion is defined.

    Used to anchor CI annotations to the line the developer actually wrote,
    rather than to the top of the file.
    """
    try:
        lines = Path(path).read_text("utf-8").splitlines()
    except OSError:
        return None
    needle = f"id: {assertion_id}"
    for index, line in enumerate(lines, start=1):
        if needle in line:
            return index
    return None
