"""Record/replay for connector reads, so a suite can run hermetically in CI.

Without this, every contract needs live credentials and ``verity eval`` cannot
run as a pull-request check on a fork. With it, a recorded cassette makes the
whole suite reproducible offline -- which is also what makes the determinism
tests meaningful.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from .base import Connector, ConnectorError, ReadResult


class CassetteMode(str, Enum):
    OFF = "off"
    RECORD = "record"
    REPLAY = "replay"


class CassetteMissError(ConnectorError):
    """Replay was asked for an interaction the cassette does not contain."""


def _interaction_key(connector: str, resource: str, key: str | None, query: dict[str, Any]) -> str:
    payload = json.dumps(
        {"connector": connector, "resource": resource, "key": key, "query": query},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return payload


class Cassette:
    """A JSON file of recorded connector interactions."""

    def __init__(self, path: str | Path, mode: CassetteMode = CassetteMode.OFF) -> None:
        self.path = Path(path)
        self.mode = mode
        self._interactions: dict[str, dict[str, Any]] = {}
        if mode is CassetteMode.REPLAY and self.path.exists():
            self._load()

    def _load(self) -> None:
        raw = json.loads(self.path.read_text("utf-8"))
        for item in raw.get("interactions", []):
            self._interactions[item["key"]] = item["result"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cassette_version": "1.0",
            "interactions": [
                {"key": key, "result": result}
                for key, result in sorted(self._interactions.items())
            ],
        }
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def record(self, key: str, result: ReadResult) -> None:
        self._interactions[key] = {
            "records": result.records,
            "channel": result.channel,
            "resource": result.resource,
            "request": result.request,
        }

    def replay(self, key: str) -> ReadResult:
        if key not in self._interactions:
            raise CassetteMissError(
                f"cassette {self.path.name} has no recorded interaction for {key}"
            )
        item = self._interactions[key]
        return ReadResult(
            records=item["records"], channel=item["channel"],
            resource=item["resource"], request=item["request"],
        )

    def __len__(self) -> int:
        return len(self._interactions)


class CassetteConnector:
    """Wraps a connector with record/replay. Transparent when mode is ``OFF``."""

    def __init__(self, inner: Connector, cassette: Cassette) -> None:
        self._inner = inner
        self._cassette = cassette
        self.name = inner.name
        self.capabilities = inner.capabilities
        self.channel = inner.channel

    def read(
        self,
        resource: str,
        *,
        key: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> ReadResult:
        interaction = _interaction_key(self.name, resource, key, query or {})

        if self._cassette.mode is CassetteMode.REPLAY:
            return self._cassette.replay(interaction)

        result = self._inner.read(resource, key=key, query=query)

        if self._cassette.mode is CassetteMode.RECORD:
            self._cassette.record(interaction, result)
        return result

    def health(self) -> bool:
        if self._cassette.mode is CassetteMode.REPLAY:
            return True
        return self._inner.health()
