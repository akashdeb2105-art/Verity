"""Deterministic state for the AP sandbox.

The sandbox is a fixture, not a product: it is simultaneously the demo
environment, the end-to-end test environment, the benchmark environment and
the regression environment. Everything about it is therefore deterministic and
resettable -- the same seed produces byte-identical state, every time, on
every machine.

All data here is obviously synthetic. No real vendor, person or account
appears anywhere in this file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

FIXED_DATE = "2026-08-14"
FIXED_TIMESTAMP = "2026-08-14T09:00:00+00:00"


@dataclass
class LineItem:
    sku: str
    description: str
    quantity: int
    unit_price: str
    amount: str


@dataclass
class PurchaseOrder:
    number: str
    vendor: str
    currency: str
    total: str
    status: str
    issued_on: str
    line_items: list[LineItem] = field(default_factory=list)


@dataclass
class GoodsReceipt:
    """Present in the data model but NOT wired into the reference contract.

    Until a contract actually compares all three legs, this remains
    invoice-to-PO verification. Calling it three-way matching before then would
    be inaccurate.
    """

    number: str
    po_number: str
    received_on: str
    quantity_received: int


@dataclass
class Invoice:
    number: str
    vendor: str
    currency: str
    total: str
    po_number: str
    issued_on: str
    template: str = "v1"
    line_items: list[LineItem] = field(default_factory=list)
    note: str = ""


@dataclass
class Bill:
    id: str
    vendor: str
    ref: str
    number: str
    amount: str
    currency: str
    status: str
    created_at: str


@dataclass
class LedgerEvent:
    id: str
    kind: str
    ref: str
    at: str
    detail: str = ""


@dataclass
class InboxMessage:
    id: str
    subject: str
    sender: str
    received_at: str
    body: str
    attachment: str | None = None


@dataclass
class SandboxState:
    seed: int = 1
    purchase_orders: list[PurchaseOrder] = field(default_factory=list)
    goods_receipts: list[GoodsReceipt] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    events: list[LedgerEvent] = field(default_factory=list)
    inbox: list[InboxMessage] = field(default_factory=list)
    ui_labels: dict[str, str] = field(default_factory=dict)
    applied_perturbations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def hash(self) -> str:
        """Stable content hash. ``make seed`` twice must produce the same value."""
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # -- lookups -----------------------------------------------------------
    def po(self, number: str) -> PurchaseOrder | None:
        return next((p for p in self.purchase_orders if p.number == number), None)

    def invoice(self, number: str) -> Invoice | None:
        return next((i for i in self.invoices if i.number == number), None)


DEFAULT_UI_LABELS = {
    "bill_status": "Status",
    "bill_amount": "Amount",
    "po_total": "PO Total",
    "submit": "Create Bill",
}


def _line(sku: str, description: str, quantity: int, unit_price: str) -> LineItem:
    amount = (Decimal(unit_price) * quantity).quantize(Decimal("0.01"))
    return LineItem(sku, description, quantity, unit_price, str(amount))


def build_seed_state(seed: int = 1) -> SandboxState:
    """Construct the canonical sandbox state.

    Deliberately not random: ``seed`` selects among fixed scenarios rather than
    driving a pseudo-random generator, because a benchmark whose fixtures shift
    with a PRNG implementation is not reproducible across Python versions.
    """
    po_lines = [
        _line("SKU-100", "Steel bracket, 40mm", 400, "22.00"),
        _line("SKU-221", "Neoprene gasket set", 125, "48.00"),
    ]
    po_total = str(
        sum((Decimal(item.amount) for item in po_lines), Decimal("0")).quantize(
            Decimal("0.01")
        )
    )

    purchase_orders = [
        PurchaseOrder(
            number="PO-2211", vendor="Acme Supplies", currency="USD",
            total=po_total, status="OPEN", issued_on="2026-07-30", line_items=po_lines,
        ),
        PurchaseOrder(
            number="PO-2212", vendor="Borealis Fabrication", currency="USD",
            total="9250.00", status="OPEN", issued_on="2026-08-02",
            line_items=[_line("SKU-880", "Anodized panel", 50, "185.00")],
        ),
    ]

    invoices = [
        Invoice(
            number="INV-4471", vendor="Acme Supplies", currency="USD", total=po_total,
            po_number="PO-2211", issued_on="2026-08-12", template="v1",
            line_items=po_lines,
        ),
    ]

    bills = [
        Bill(
            id="BILL-0001", vendor="Acme Supplies", ref="INV-4471", number="INV-4471",
            amount=po_total, currency="USD", status="DRAFT", created_at=FIXED_TIMESTAMP,
        ),
    ]

    events = [
        LedgerEvent(id="EV-0001", kind="create", ref="INV-4471", at=FIXED_TIMESTAMP,
                    detail="bill staged as draft"),
    ]

    inbox = [
        InboxMessage(
            id="MSG-0001", subject="Invoice INV-4471 for PO-2211",
            sender="billing@acme-supplies.example", received_at=FIXED_TIMESTAMP,
            body="Please find attached our invoice for purchase order PO-2211.",
            attachment="INV-4471.pdf",
        ),
    ]

    return SandboxState(
        seed=seed,
        purchase_orders=purchase_orders,
        goods_receipts=[GoodsReceipt("GR-0091", "PO-2211", "2026-08-08", 400)],
        invoices=invoices,
        bills=bills,
        events=events,
        inbox=inbox,
        ui_labels=dict(DEFAULT_UI_LABELS),
        applied_perturbations=[],
    )
