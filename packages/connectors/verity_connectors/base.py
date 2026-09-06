"""The connector protocol: typed, read-only access to a system of record.

M0 connectors are read-only by design. Verification never writes, so there is
no side-effect path in this package at all -- the single controlled
side-effect path arrives with the runtime, in a later milestone, and it cannot
be reached from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ConnectorError(Exception):
    """A connector could not answer. Always yields an INCONCLUSIVE fact, never a pass."""


class ResourceNotFoundError(ConnectorError):
    pass


@dataclass(frozen=True)
class ReadResult:
    """The result of one connector read."""

    records: list[dict[str, Any]] = field(default_factory=list)
    channel: str = "api"
    """Which channel served this read. Used to detect self-confirmation: a value
    written through a UI and read back through that same UI proves very little."""

    resource: str = ""
    request: dict[str, Any] = field(default_factory=dict)

    @property
    def cardinality(self) -> int:
        return len(self.records)

    @property
    def one(self) -> dict[str, Any]:
        if len(self.records) != 1:
            raise ConnectorError(f"expected exactly one record, got {len(self.records)}")
        return self.records[0]


@runtime_checkable
class Connector(Protocol):
    """Anything Verity can read facts from."""

    name: str
    capabilities: frozenset[str]
    channel: str

    def read(
        self,
        resource: str,
        *,
        key: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> ReadResult: ...

    def health(self) -> bool: ...


class ConnectorRegistry:
    """Binds contract source *roles* to concrete connectors at verification time.

    This indirection is why one contract runs unchanged against a sandbox, a
    staging system and production.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._by_name[connector.name] = connector

    def get(self, name: str) -> Connector | None:
        return self._by_name.get(name)

    def find_by_capability(self, capability: str) -> Connector | None:
        for connector in self._by_name.values():
            if capability in connector.capabilities:
                return connector
        return None

    def resolve(self, role: str, capability: str | None) -> Connector:
        """Prefer an explicit binding by role name, then fall back to capability."""
        connector = self._by_name.get(role)
        if connector is not None:
            return connector
        if capability:
            found = self.find_by_capability(capability)
            if found is not None:
                return found
        raise ConnectorError(
            f"no connector bound for source '{role}'"
            + (f" (capability '{capability}')" if capability else "")
        )

    @property
    def names(self) -> list[str]:
        return sorted(self._by_name)
