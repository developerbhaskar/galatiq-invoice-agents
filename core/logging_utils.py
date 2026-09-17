"""Observability: rich console rendering plus a JSON audit trail per invoice."""

from __future__ import annotations

import json
import os
from typing import List

from core.state import InvoiceState
from config import AUDIT_DIR

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    _RICH = True
except Exception:  # pragma: no cover
    _RICH = False

_STATUS_STYLE = {
    "PAID": ("green", "✅"),
    "ESCALATED": ("yellow", "⚠️"),
    "REJECTED": ("red", "⛔"),
}


def get_console():
    return Console() if _RICH else None


# ---------------------------------------------------------------------------
def render_state(state: InvoiceState, path: List[str] | None = None, console=None) -> None:
    if not _RICH:
        _render_plain(state, path)
        return
    console = console or Console()
    style, icon = _STATUS_STYLE.get(state.final_status or "", ("white", "•"))

    header = Text.assemble(
        (f"{icon} {state.invoice_number or '?'}", f"bold {style}"),
        (f"   {state.vendor or 'Unknown vendor'}   ", "bold"),
        (f"{(state.amount or 0):,.2f} {state.currency}", "cyan"),
    )
    body = Table.grid(padding=(0, 1))
    body.add_column(justify="right", style="dim")
    body.add_column()
    body.add_row("Source", f"{os.path.basename(state.source_path)} ({state.source_format})")
    body.add_row("Confidence", f"{state.extraction_confidence:.0%}")
    body.add_row("Decision", Text(state.final_status or "?", style=f"bold {style}"))
    if state.errors:
        body.add_row("Errors", Text("; ".join(state.errors), style="red"))
    if state.review_flags:
        body.add_row("Review", Text("; ".join(state.review_flags), style="yellow"))
    if state.fraud_signals:
        body.add_row("Fraud", Text(f"score {state.fraud_score} — " + "; ".join(state.fraud_signals), style="red"))
    if state.warnings:
        body.add_row("Warnings", Text("; ".join(state.warnings), style="dim yellow"))
    ref = state.reflection or {}
    if ref.get("draft_decision") and ref.get("final_decision") and ref["draft_decision"] != ref["final_decision"]:
        body.add_row("Reflection", Text(f"{ref['draft_decision']} → {ref['final_decision']} (self-corrected)", style="magenta"))
    body.add_row("Rationale", Text(state.approval_reasoning or "", style="italic"))
    if path:
        body.add_row("Path", " → ".join(path))

    console.print(Panel(body, title=header, border_style=style, expand=False))


def _render_plain(state: InvoiceState, path) -> None:
    print(f"\n=== {state.invoice_number or '?'} | {state.vendor} | {(state.amount or 0):,.2f} {state.currency} ===")
    print(f"  Source     : {os.path.basename(state.source_path)} ({state.source_format})")
    print(f"  Confidence : {state.extraction_confidence:.0%}")
    print(f"  Decision   : {state.final_status}")
    if state.errors:
        print(f"  Errors     : {'; '.join(state.errors)}")
    if state.review_flags:
        print(f"  Review     : {'; '.join(state.review_flags)}")
    if state.fraud_signals:
        print(f"  Fraud      : score {state.fraud_score} — {'; '.join(state.fraud_signals)}")
    if state.warnings:
        print(f"  Warnings   : {'; '.join(state.warnings)}")
    print(f"  Rationale  : {state.approval_reasoning}")
    if path:
        print(f"  Path       : {' -> '.join(path)}")


# ---------------------------------------------------------------------------
def save_audit(state: InvoiceState, path: List[str] | None = None, out_dir: str = AUDIT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    payload = state.to_dict()
    payload["node_path"] = path or []
    fname = f"{state.invoice_number or os.path.basename(state.source_path)}.json"
    fpath = os.path.join(out_dir, fname.replace("/", "_"))
    with open(fpath, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return fpath


def summarize(results: List[InvoiceState], console=None) -> dict:
    """Aggregate metrics across a batch and print a summary table."""
    total = len(results)
    paid = [r for r in results if r.final_status == "PAID"]
    escalated = [r for r in results if r.final_status == "ESCALATED"]
    rejected = [r for r in results if r.final_status == "REJECTED"]
    paid_value = sum(r.amount or 0 for r in paid if (r.currency == "USD"))
    flagged_value = sum(r.amount or 0 for r in results if r.final_status != "PAID")

    metrics = {
        "total": total,
        "paid": len(paid),
        "escalated": len(escalated),
        "rejected": len(rejected),
        "paid_value_usd": round(paid_value, 2),
        "value_stopped_from_auto_pay": round(flagged_value, 2),
    }

    if _RICH:
        console = console or Console()
        t = Table(title="Batch summary", title_style="bold", show_lines=False)
        t.add_column("Metric", style="dim")
        t.add_column("Value", justify="right")
        t.add_row("Invoices processed", str(total))
        t.add_row("✅ Auto-paid", f"{len(paid)}")
        t.add_row("⚠️  Escalated for review", f"{len(escalated)}")
        t.add_row("⛔ Rejected", f"{len(rejected)}")
        t.add_row("USD auto-paid", f"${paid_value:,.2f}")
        t.add_row("Value held from auto-pay", f"${flagged_value:,.2f}")
        console.print(t)
    else:
        print("\n--- Batch summary ---")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
    return metrics
