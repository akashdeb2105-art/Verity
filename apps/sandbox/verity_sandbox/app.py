"""The Verity AP sandbox: a small, deterministic accounts-payable stack.

Three surfaces, deliberately:

* ``/api/*``   -- a system of record, read over JSON
* ``/ui/*``    -- the same records rendered as HTML
* ``/admin/*`` -- seed, reset and perturbation control

The API and the UI expose the *same* records through *different* channels.
That is not decoration: it is what makes independent-channel verification
physically possible, so a value written through one surface can be proven by
reading the other.

Everything here is synthetic fixture data.
"""

from __future__ import annotations

import io
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .pdfgen import render_invoice_pdf
from .perturb import PERTURBATIONS, apply_perturbations
from .state import SandboxState, build_seed_state

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class Sandbox:
    """Holds the mutable state and the list of applied perturbations."""

    def __init__(self, seed: int = 1) -> None:
        self.seed = seed
        self.state: SandboxState = build_seed_state(seed)

    def reset(self, seed: int | None = None) -> SandboxState:
        self.seed = self.seed if seed is None else seed
        self.state = build_seed_state(self.seed)
        return self.state

    def set_perturbations(self, names: list[str]) -> SandboxState:
        self.state = apply_perturbations(names, self.seed)
        return self.state

    def perturb(self, name: str) -> SandboxState:
        applied = [*self.state.applied_perturbations]
        if name not in applied:
            applied.append(name)
        return self.set_perturbations(applied)

    def unperturb(self, name: str) -> SandboxState:
        applied = [n for n in self.state.applied_perturbations if n != name]
        return self.set_perturbations(applied)


def create_app(seed: int = 1) -> FastAPI:
    app = FastAPI(
        title="Verity AP sandbox",
        description="Deterministic synthetic accounts-payable fixture.",
        version="0.0.1",
    )
    sandbox = Sandbox(seed)
    app.state.sandbox = sandbox

    # -- health --------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "seed": sandbox.seed,
            "state_hash": sandbox.state.hash(),
            "perturbations": sandbox.state.applied_perturbations,
        }

    # -- purchase orders ------------------------------------------------
    @app.get("/api/purchase_orders")
    def list_purchase_orders(
        number: str | None = Query(default=None),
        vendor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        records = [asdict(p) for p in sandbox.state.purchase_orders]
        if number:
            records = [r for r in records if r["number"] == number]
        if vendor:
            records = [r for r in records if r["vendor"] == vendor]
        return {"records": records, "count": len(records)}

    @app.get("/api/purchase_orders/{number}")
    def get_purchase_order(number: str) -> dict[str, Any]:
        matches = [asdict(p) for p in sandbox.state.purchase_orders if p.number == number]
        if not matches:
            raise HTTPException(status_code=404, detail=f"no purchase order {number}")
        # Deliberately returns every match. An ambiguous record is a finding for
        # the verifier to report, not something the fixture should hide.
        return {"records": matches, "count": len(matches)}

    # -- goods receipts --------------------------------------------------
    @app.get("/api/goods_receipts")
    def list_goods_receipts(po_number: str | None = Query(default=None)) -> dict[str, Any]:
        records = [asdict(g) for g in sandbox.state.goods_receipts]
        if po_number:
            records = [r for r in records if r["po_number"] == po_number]
        return {"records": records, "count": len(records)}

    # -- invoices --------------------------------------------------------
    @app.get("/api/invoices")
    def list_invoices(number: str | None = Query(default=None)) -> dict[str, Any]:
        records = [asdict(i) for i in sandbox.state.invoices]
        if number:
            records = [r for r in records if r["number"] == number]
        return {"records": records, "count": len(records)}

    @app.get("/docs/invoices/{number}.pdf")
    def invoice_pdf(number: str) -> Response:
        invoice = sandbox.state.invoice(number)
        if invoice is None:
            raise HTTPException(status_code=404, detail=f"no invoice {number}")
        return Response(content=render_invoice_pdf(invoice), media_type="application/pdf")

    # -- ledger ----------------------------------------------------------
    @app.get("/api/bills")
    def list_bills(
        vendor: str | None = Query(default=None),
        ref: str | None = Query(default=None),
        number: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        records = [asdict(b) for b in sandbox.state.bills]
        for field_name, value in (
            ("vendor", vendor), ("ref", ref), ("number", number), ("status", status)
        ):
            if value:
                records = [r for r in records if r[field_name] == value]
        return {"records": records, "count": len(records)}

    @app.get("/api/ledger_events")
    def list_events(ref: str | None = Query(default=None)) -> dict[str, Any]:
        records = [asdict(e) for e in sandbox.state.events]
        if ref:
            records = [r for r in records if r["ref"] == ref]
        return {"records": records, "count": len(records)}

    @app.get("/api/inbox")
    def list_inbox() -> dict[str, Any]:
        records = [asdict(m) for m in sandbox.state.inbox]
        return {"records": records, "count": len(records)}

    # -- UI: the same records, a different channel ------------------------
    @app.get("/ui/inbox", response_class=HTMLResponse)
    def ui_inbox(request: Request) -> Any:
        return TEMPLATES.TemplateResponse(
            request, "inbox.html",
            {"messages": sandbox.state.inbox, "labels": sandbox.state.ui_labels},
        )

    @app.get("/ui/purchase-orders/{number}", response_class=HTMLResponse)
    def ui_purchase_order(request: Request, number: str) -> Any:
        matches = [p for p in sandbox.state.purchase_orders if p.number == number]
        if not matches:
            raise HTTPException(status_code=404, detail=f"no purchase order {number}")
        return TEMPLATES.TemplateResponse(
            request, "purchase_order.html",
            {"orders": matches, "labels": sandbox.state.ui_labels},
        )

    @app.get("/ui/bills", response_class=HTMLResponse)
    def ui_bills(request: Request) -> Any:
        return TEMPLATES.TemplateResponse(
            request, "bills.html",
            {"bills": sandbox.state.bills, "labels": sandbox.state.ui_labels},
        )

    # -- admin -----------------------------------------------------------
    @app.get("/admin/state")
    def admin_state() -> dict[str, Any]:
        return sandbox.state.to_dict()

    @app.get("/admin/state-hash")
    def admin_state_hash() -> dict[str, str]:
        return {"state_hash": sandbox.state.hash()}

    @app.get("/admin/perturbations")
    def admin_list_perturbations() -> dict[str, Any]:
        return {
            "available": [
                {
                    "name": p.name,
                    "summary": p.summary,
                    "expected_live": p.expected_live,
                    "expected_trace": p.expected_trace or p.expected_live,
                    "expected_divergence": p.expected_divergence,
                }
                for p in PERTURBATIONS.values()
            ],
            "applied": sandbox.state.applied_perturbations,
        }

    @app.post("/admin/reset")
    def admin_reset(seed: int | None = Query(default=None)) -> dict[str, Any]:
        state = sandbox.reset(seed)
        return {"state_hash": state.hash(), "seed": sandbox.seed}

    @app.post("/admin/perturb/{name}")
    def admin_perturb(name: str) -> dict[str, Any]:
        if name not in PERTURBATIONS:
            raise HTTPException(status_code=404, detail=f"unknown perturbation '{name}'")
        state = sandbox.perturb(name)
        return {"applied": state.applied_perturbations, "state_hash": state.hash()}

    @app.post("/admin/unperturb/{name}")
    def admin_unperturb(name: str) -> dict[str, Any]:
        state = sandbox.unperturb(name)
        return {"applied": state.applied_perturbations, "state_hash": state.hash()}

    @app.get("/", response_class=JSONResponse)
    def index() -> dict[str, Any]:
        return {
            "name": "Verity AP sandbox",
            "note": "Synthetic fixture data. Not a product surface.",
            "api": ["/api/health", "/api/purchase_orders", "/api/bills",
                    "/api/invoices", "/api/ledger_events", "/api/inbox"],
            "ui": ["/ui/inbox", "/ui/purchase-orders/PO-2211", "/ui/bills"],
            "admin": ["/admin/state-hash", "/admin/perturbations", "/admin/reset"],
            "documents": ["/docs/invoices/INV-4471.pdf"],
        }

    return app


app = create_app()


def _unused(_: io.BytesIO) -> None:  # pragma: no cover
    return None
