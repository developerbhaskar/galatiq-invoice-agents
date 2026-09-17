"""
Pure presentation helpers shared by the dashboard (app.py) and tests.

Kept free of any Streamlit import so the exact data the UI renders — status
pills, stage chips, table rows — can be unit-tested against real pipeline
states without a UI runtime.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from core.state import InvoiceState

STATUS_EMOJI = {"PAID": "✅", "ESCALATED": "⚠️", "REJECTED": "⛔"}
STATUS_COLOR = {"PAID": "#16a34a", "ESCALATED": "#d97706", "REJECTED": "#dc2626"}
STAGES = ["ingestion", "validation", "approval", "payment"]


def pill_html(status: str) -> str:
    return f"<span class='pill p-{status}'>{STATUS_EMOJI.get(status,'')} {status}</span>"


def stage_chips_html(state: InvoiceState) -> str:
    done = {a.stage for a in state.audit_trail}
    final_cls = {"PAID": "s-pay", "ESCALATED": "s-hold", "REJECTED": "s-rej"}.get(state.final_status, "s-idle")
    chips = []
    for s in STAGES:
        cls = "s-done" if s in done else "s-idle"
        if s == "payment" and s in done:
            cls = final_cls
        chips.append(f"<span class='stage {cls}'>{s.title()}</span>")
    return " ➜ ".join(chips)


def batch_row(state: InvoiceState) -> Dict[str, Any]:
    return {
        "Invoice": state.invoice_number,
        "Vendor": state.vendor,
        "Fmt": state.source_format,
        "Amount": f"{(state.amount or 0):,.2f} {state.currency}",
        "Conf": f"{state.extraction_confidence:.0%}",
        "Status": f"{STATUS_EMOJI.get(state.final_status,'')} {state.final_status}",
        "Reason": (state.approval_reasoning or "")[:90],
    }


def batch_metrics(results: List[InvoiceState]) -> Dict[str, Any]:
    paid = [r for r in results if r.final_status == "PAID"]
    esc = [r for r in results if r.final_status == "ESCALATED"]
    rej = [r for r in results if r.final_status == "REJECTED"]
    return {
        "total": len(results),
        "paid": len(paid),
        "escalated": len(esc),
        "rejected": len(rej),
        "paid_usd": sum(r.amount or 0 for r in paid if r.currency == "USD"),
        "held": sum(r.amount or 0 for r in results if r.final_status != "PAID"),
    }


def extracted_fields(state: InvoiceState) -> Dict[str, Any]:
    return {
        "invoice_number": state.invoice_number,
        "vendor": state.vendor,
        "date": state.date,
        "due_date": state.due_date or getattr(state, "_due_date_raw", None),
        "currency": state.currency,
        "subtotal": state.subtotal,
        "tax_amount": state.tax_amount,
        "total": state.total,
        "payment_terms": state.payment_terms,
    }
