"""Connectors: typed access to systems of record.

Reading and writing are separate protocols and separate types. Verification
depends only on the read side, so no refactoring inside it can reach a write
-- the boundary is enforced by the type system rather than by a convention.

Every write passes through a :class:`WriteGuard`, which in dry-run mode
records the intent and performs nothing. That is what makes "point it at your
real system and see what it would do" a safe sentence.
"""

from .base import (
    Connector,
    ConnectorError,
    ConnectorRegistry,
    ReadResult,
    ResourceNotFoundError,
)
from .cassette import Cassette, CassetteConnector, CassetteMissError, CassetteMode
from .http_connector import (
    DocumentConnector,
    DocumentRef,
    HttpJsonConnector,
    ResourceRoute,
)
from .sandbox import (
    DEFAULT_SANDBOX_URL,
    build_sandbox_registry,
    inbox_connector,
    ledger_connector,
    ledger_writer,
    purchase_order_connector,
)
from .write import (
    HttpWriteConnector,
    WritableConnector,
    WriteGuard,
    WriteIntent,
    WriteMode,
    WriteRefusedError,
    WriteResult,
    payload_digest,
)

__all__ = [
    "DEFAULT_SANDBOX_URL",
    "Cassette",
    "CassetteConnector",
    "CassetteMissError",
    "CassetteMode",
    "Connector",
    "ConnectorError",
    "ConnectorRegistry",
    "DocumentConnector",
    "DocumentRef",
    "HttpJsonConnector",
    "HttpWriteConnector",
    "ReadResult",
    "ResourceNotFoundError",
    "ResourceRoute",
    "WritableConnector",
    "WriteGuard",
    "WriteIntent",
    "WriteMode",
    "WriteRefusedError",
    "WriteResult",
    "build_sandbox_registry",
    "inbox_connector",
    "ledger_connector",
    "ledger_writer",
    "payload_digest",
    "purchase_order_connector",
]
