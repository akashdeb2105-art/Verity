"""The single controlled side-effect path.

Reading and writing are deliberately separate protocols. The verifier depends
on :class:`~verity_connectors.base.Connector` and nothing here, so no amount of
refactoring inside verification can reach a write -- the boundary is the type
system, not a convention.

Every write goes through :class:`WriteGuard`. In ``DRY_RUN`` mode it records
what would have happened and performs nothing, which is what makes "point it
at your real system and see what it would do" a safe sentence to say.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .base import ConnectorError


class WriteMode(str, Enum):
    """Whether a write actually happens."""

    DRY_RUN = "dry_run"
    """Nothing is sent. The intent is recorded and returned as if it were."""

    LIVE = "live"
    """The write is performed."""


class WriteRefusedError(ConnectorError):
    """A write was attempted that the guard would not allow through."""


def payload_digest(resource: str, payload: dict[str, Any]) -> str:
    """A stable hash of exactly what would be written.

    Approval is later bound to this value, so that approving a $14,800 bill
    cannot be replayed to create a $148,000 one. It hashes the resource too,
    because the same payload sent somewhere else is a different act.
    """
    body = json.dumps(
        {"resource": resource, "payload": payload}, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WriteIntent:
    """What a runtime wants to do, before anything is done."""

    connector: str
    resource: str
    payload: dict[str, Any]

    @property
    def digest(self) -> str:
        return payload_digest(self.resource, self.payload)

    def describe(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in sorted(self.payload.items()))
        return f"{self.connector}.{self.resource}({fields})"


@dataclass(frozen=True)
class WriteResult:
    """What happened -- or, in a dry run, what would have."""

    intent: WriteIntent
    performed: bool
    record: dict[str, Any] = field(default_factory=dict)
    channel: str = "api"

    @property
    def id(self) -> str:
        return str(self.record.get("id") or "")


@runtime_checkable
class WritableConnector(Protocol):
    """A connector that can change the world. Kept apart from reading on purpose."""

    name: str
    channel: str

    def write(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class WriteGuard:
    """The only place a write can be performed.

    Holds the mode and the log. A dry run returns a result shaped exactly like
    a live one, so nothing downstream branches on whether the write was real
    -- code that behaves differently in a dry run is code the dry run has not
    actually tested.
    """

    mode: WriteMode = WriteMode.DRY_RUN
    attempts: list[WriteResult] = field(default_factory=list)

    @property
    def performed(self) -> list[WriteResult]:
        return [w for w in self.attempts if w.performed]

    @property
    def wrote_nothing(self) -> bool:
        return not self.performed

    def perform(self, connector: WritableConnector, intent: WriteIntent) -> WriteResult:
        if intent.connector != connector.name:
            raise WriteRefusedError(
                f"intent names {intent.connector!r} but the connector is "
                f"{connector.name!r}; a write must go where it says it goes"
            )

        if self.mode is WriteMode.DRY_RUN:
            result = WriteResult(
                intent=intent, performed=False, channel=connector.channel,
                record={"id": "", "dry_run": True, **intent.payload},
            )
        else:
            result = WriteResult(
                intent=intent, performed=True, channel=connector.channel,
                record=connector.write(intent.resource, dict(intent.payload)),
            )
        self.attempts.append(result)
        return result


@dataclass
class HttpWriteConnector:
    """Performs one kind of write over HTTP: POST a JSON body, get a record back.

    A separate class from :class:`~verity_connectors.http_connector.HttpJsonConnector`
    rather than a method on it. Reading is the common case and must stay
    incapable of writing; making that a different type means a read-only
    connector cannot acquire a write path by accident.
    """

    name: str
    base_url: str
    routes: dict[str, str]
    channel: str = "api"
    timeout: float = 10.0
    client: Any = None

    def write(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        path = self.routes.get(resource)
        if path is None:
            raise WriteRefusedError(
                f"{self.name} exposes no writable resource {resource!r} "
                f"(has: {', '.join(sorted(self.routes)) or 'none'})"
            )

        url = self.base_url.rstrip("/") + path
        try:
            if self.client is not None:
                response = self.client.post(url, json=payload, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self.name}: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorError(
                f"{self.name}: {resource} write returned HTTP {response.status_code} "
                f"-- {response.text[:200]}"
            )

        body = response.json()
        record = body.get("record") if isinstance(body, dict) else None
        if not isinstance(record, dict):
            raise ConnectorError(f"{self.name}: unexpected write response for {resource!r}")
        return record
