"""A generic read-only JSON connector over HTTP.

Configured with a resource map rather than subclassed per system, so binding a
contract to a new system of record is configuration, not code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .base import ConnectorError, ReadResult

DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class ResourceRoute:
    path: str
    """May contain ``{key}``, which is filled from the read's ``key``."""

    records_field: str = "records"
    key_param: str | None = None
    """When set, ``key`` is sent as this query parameter instead of in the path."""


class HttpJsonConnector:
    """Reads records from a JSON HTTP API. Read-only: there is no write method."""

    def __init__(
        self,
        name: str,
        base_url: str,
        routes: dict[str, ResourceRoute],
        *,
        capabilities: frozenset[str] = frozenset(),
        channel: str = "api",
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.routes = routes
        self.capabilities = capabilities
        self.channel = channel
        self._timeout = timeout
        self._client = client

    def _request(self, path: str, params: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            if self._client is not None:
                return self._client.get(url, params=params, timeout=self._timeout)
            with httpx.Client(timeout=self._timeout) as client:
                return client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self.name}: {exc}") from exc

    def read(
        self,
        resource: str,
        *,
        key: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> ReadResult:
        route = self.routes.get(resource)
        if route is None:
            raise ConnectorError(
                f"{self.name} exposes no resource '{resource}' "
                f"(available: {', '.join(sorted(self.routes))})"
            )

        params: dict[str, Any] = dict(query or {})
        path = route.path
        if key is not None:
            if route.key_param:
                params[route.key_param] = key
            elif "{key}" in path:
                path = path.replace("{key}", str(key))
            else:
                raise ConnectorError(f"resource '{resource}' does not accept a key")

        response = self._request(path, params)

        if response.status_code == 404:
            return ReadResult(records=[], channel=self.channel, resource=resource,
                              request={"path": path, "params": params})
        if response.status_code >= 400:
            raise ConnectorError(
                f"{self.name}: {resource} returned HTTP {response.status_code}"
            )

        payload = response.json()
        if isinstance(payload, dict):
            records = payload.get(route.records_field, [])
            if isinstance(records, dict):
                records = [records]
        elif isinstance(payload, list):
            records = payload
        else:  # pragma: no cover - defensive
            raise ConnectorError(f"{self.name}: unexpected payload shape for '{resource}'")

        return ReadResult(
            records=list(records), channel=self.channel, resource=resource,
            request={"path": path, "params": params},
        )

    def health(self) -> bool:
        try:
            return self._request("/api/health", {}).status_code == 200
        except ConnectorError:
            return False


@dataclass(frozen=True)
class DocumentRef:
    """A document to be fetched and parsed, with its bytes hashed for provenance."""

    uri: str
    sha256: str = ""
    data: bytes = field(default=b"", repr=False)


class DocumentConnector:
    """Fetches document bytes over HTTP or from the local filesystem.

    Parsing lives in :mod:`verity_extract`. This class only obtains bytes and
    records where they came from, which keeps the extraction layer testable
    without a network.
    """

    name = "documents"
    capabilities = frozenset({"documents"})
    channel = "document"

    def __init__(self, base_url: str = "", client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    def fetch(self, uri: str) -> DocumentRef:
        import hashlib
        from pathlib import Path

        if uri.startswith(("http://", "https://")):
            target = uri
        elif self.base_url and uri.startswith("/"):
            target = f"{self.base_url}{uri}"
        else:
            path = Path(uri)
            if not path.exists():
                raise ConnectorError(f"document not found: {uri}")
            data = path.read_bytes()
            return DocumentRef(uri=uri, sha256=hashlib.sha256(data).hexdigest(), data=data)

        try:
            if self._client is not None:
                response = self._client.get(target, timeout=DEFAULT_TIMEOUT)
            else:
                with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                    response = client.get(target)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"could not fetch document {uri}: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorError(f"document {uri} returned HTTP {response.status_code}")

        data = response.content
        return DocumentRef(uri=uri, sha256=hashlib.sha256(data).hexdigest(), data=data)

    def read(
        self,
        resource: str,
        *,
        key: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> ReadResult:  # pragma: no cover - documents are fetched, not queried
        raise ConnectorError("document sources are fetched, not read as records")

    def health(self) -> bool:
        return True
