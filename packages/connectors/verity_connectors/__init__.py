"""Read-only connectors: typed access to systems of record.

There is no write path in this package. Verification never writes; the single
controlled side-effect path belongs to the runtime, in a later milestone.
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
    purchase_order_connector,
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
    "ReadResult",
    "ResourceNotFoundError",
    "ResourceRoute",
    "build_sandbox_registry",
    "inbox_connector",
    "ledger_connector",
    "purchase_order_connector",
]
