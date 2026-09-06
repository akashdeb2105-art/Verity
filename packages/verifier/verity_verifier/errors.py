"""Errors raised while loading or checking a contract.

Every error in this module is a *compile-time* error: it is raised before any
fact is read. A contract that cannot be evaluated must never silently pass, so
these are surfaced by ``verity contract lint`` and fail CI.
"""

from __future__ import annotations


class ContractError(Exception):
    """Base class for contract loading and checking failures."""


class ContractLoadError(ContractError):
    """The file is not a valid Outcome Contract."""


class ContractTypeError(ContractError):
    """The contract parses but does not type-check.

    Undefined facts, unknown functions, impossible references and malformed
    expressions all land here.
    """

    def __init__(self, message: str, *, location: str = "") -> None:
        self.location = location
        super().__init__(f"{location}: {message}" if location else message)
