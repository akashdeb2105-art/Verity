"""Connector bindings for the Verity AP sandbox.

These exist so the reference contract can be evaluated with no external
system. Binding the same contract to QuickBooks or Xero later is a different
:class:`~verity_connectors.http_connector.HttpJsonConnector` configuration,
not a different contract.
"""

from __future__ import annotations

import httpx

from .base import ConnectorRegistry
from .http_connector import DocumentConnector, HttpJsonConnector, ResourceRoute

DEFAULT_SANDBOX_URL = "http://127.0.0.1:8099"


def purchase_order_connector(
    base_url: str = DEFAULT_SANDBOX_URL, client: httpx.Client | None = None
) -> HttpJsonConnector:
    return HttpJsonConnector(
        name="po_system",
        base_url=base_url,
        routes={
            "purchase_order": ResourceRoute("/api/purchase_orders/{key}"),
            "purchase_orders": ResourceRoute("/api/purchase_orders"),
            "goods_receipt": ResourceRoute("/api/goods_receipts"),
        },
        capabilities=frozenset({"purchase_orders", "goods_receipts"}),
        channel="api",
        client=client,
    )


def ledger_connector(
    base_url: str = DEFAULT_SANDBOX_URL, client: httpx.Client | None = None
) -> HttpJsonConnector:
    return HttpJsonConnector(
        name="ledger",
        base_url=base_url,
        routes={
            # `key` looks a bill up by the invoice reference it was raised for,
            # which is how a person finds one on the screen.
            "bill": ResourceRoute("/api/bills", key_param="ref"),
            "bills": ResourceRoute("/api/bills"),
            "event": ResourceRoute("/api/ledger_events"),
            "events": ResourceRoute("/api/ledger_events"),
        },
        capabilities=frozenset({"accounting", "ledger"}),
        channel="api",
        client=client,
    )


def inbox_connector(
    base_url: str = DEFAULT_SANDBOX_URL, client: httpx.Client | None = None
) -> HttpJsonConnector:
    return HttpJsonConnector(
        name="inbox",
        base_url=base_url,
        routes={"message": ResourceRoute("/api/inbox"), "messages": ResourceRoute("/api/inbox")},
        capabilities=frozenset({"mail"}),
        channel="api",
        client=client,
    )


def build_sandbox_registry(
    base_url: str = DEFAULT_SANDBOX_URL, client: httpx.Client | None = None
) -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(purchase_order_connector(base_url, client))
    registry.register(ledger_connector(base_url, client))
    registry.register(inbox_connector(base_url, client))
    registry.register(DocumentConnector(base_url, client))
    return registry
