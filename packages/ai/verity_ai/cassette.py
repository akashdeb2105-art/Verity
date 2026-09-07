"""Recorded model responses, so tests never call a paid endpoint.

The same idea as the connector cassettes, for the same reasons: a test suite
that needs a live API key cannot run on a pull request from a fork, cannot run
offline, and quietly bills somebody every time CI runs. It also could not be
deterministic, and a non-deterministic test of a proposal layer tells you very
little.

Recording a cassette needs a real key once. Everything after that is replay.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any

from .base import Completion, ProviderError


class AiCassetteMode(str, Enum):
    OFF = "off"
    RECORD = "record"
    REPLAY = "replay"


class AiCassetteMissError(ProviderError):
    """Replay was asked for an exchange the cassette does not contain."""


def _key(system: str, user: str) -> str:
    """A cassette entry is identified by the question, not by who answered it.

    Which provider and model produced the answer is recorded alongside it,
    because it is worth knowing -- but it is not part of the identity. Keying
    on it would mean a committed fixture only replays for whoever happens to
    have the same provider configured, and would silently stop matching the
    day a default model changed. Neither is a property a test fixture should
    have.
    """
    digest = hashlib.sha256(
        json.dumps([system, user], sort_keys=True).encode()
    ).hexdigest()
    return digest[:32]


class AiCassette:
    def __init__(self, path: str | Path, mode: AiCassetteMode = AiCassetteMode.OFF) -> None:
        self.path = Path(path)
        self.mode = mode
        self._exchanges: dict[str, dict[str, Any]] = {}
        if mode is AiCassetteMode.REPLAY and self.path.exists():
            raw = json.loads(self.path.read_text("utf-8"))
            self._exchanges = {e["key"]: e for e in raw.get("exchanges", [])}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "cassette_version": "1.0",
                    "exchanges": [self._exchanges[k] for k in sorted(self._exchanges)],
                },
                indent=2, sort_keys=True,
            ),
            encoding="utf-8",
        )

    def record(self, key: str, prompt: dict[str, str], completion: Completion,
               provider: str = "") -> None:
        """Store an exchange and write it out.

        Saved immediately rather than on a later call nobody remembers to
        make. A recorder that keeps the recording in memory and exits quietly
        is worse than no recorder: it reports success and leaves nothing
        behind, which is exactly what happened the first time this was used.
        """
        self._exchanges[key] = {
            "key": key, "prompt": prompt, "provider": provider,
            "model": completion.model, "response": completion.data,
            "usd": completion.usd,
        }
        self.save()

    def replay(self, key: str) -> Completion:
        entry = self._exchanges.get(key)
        if entry is None:
            raise AiCassetteMissError(
                f"cassette {self.path.name} has no recorded exchange for {key}. "
                "Re-record it with VERITY_AI_CASSETTE_MODE=record and a real key."
            )
        return Completion(
            data=entry["response"], model=entry["model"],
            raw=json.dumps(entry["response"]),
            usd=None if entry.get("usd") is None else float(entry["usd"]),
        )

    def __len__(self) -> int:
        return len(self._exchanges)


class CassetteProvider:
    """Wraps a provider with record/replay. Transparent when mode is ``OFF``."""

    def __init__(self, inner: Any, cassette: AiCassette) -> None:
        self._inner = inner
        self._cassette = cassette
        self.name = f"{inner.name}+cassette"
        self.model = inner.model

    def available(self) -> bool:
        if self._cassette.mode is AiCassetteMode.REPLAY:
            return True
        return bool(self._inner.available())

    def complete_json(self, system: str, user: str, *, schema_hint: str = "") -> Completion:
        key = _key(system, user)

        if self._cassette.mode is AiCassetteMode.REPLAY:
            return self._cassette.replay(key)

        completion: Completion = self._inner.complete_json(
            system, user, schema_hint=schema_hint
        )

        if self._cassette.mode is AiCassetteMode.RECORD:
            # The provider is read after the call, not before: with a fallback
            # chain the one that answers is not known until it does.
            self._cassette.record(
                key, {"system": system, "user": user}, completion,
                provider=str(self._inner.name),
            )
        return completion
