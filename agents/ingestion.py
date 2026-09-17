"""Stage 1 : Ingestion.

Extract structured fields from any format, score confidence, and self-correct
recoverable gaps (missing line amounts / subtotal / total). Unrecoverable
problems are recorded, not guessed.
"""

from __future__ import annotations

from typing import Any, Dict

from core import parsers
from core.state import InvoiceState, LineItem
from config import MAX_INGESTION_RETRIES


class IngestionAgent:
    stage = "ingestion"

    def __init__(self, engine=None):
        self.engine = engine

    def run(self, state: InvoiceState) -> InvoiceState:
        parsed = parsers.parse(state.source_path)
        self._apply(state, parsed)

        # ---- self-correction loop -------------------------------------------
        for attempt in range(MAX_INGESTION_RETRIES + 1):
            issues = self._audit_fields(state)
            repaired = self._repair(state, issues)
            state.extraction_issues = self._audit_fields(state)
            state.extraction_confidence = self._score(state)
            if not repaired:
                break

        crit = "; ".join(state.extraction_issues) if state.extraction_issues else "none"
        state.log(
            self.stage,
            f"Extracted {state.invoice_number or '?'} from {state.source_format.upper()} "
            f"— {len(state.line_items)} line item(s), confidence {state.extraction_confidence:.0%}",
            format=state.source_format,
            confidence=round(state.extraction_confidence, 3),
            issues=state.extraction_issues,
            vendor=state.vendor,
            amount=state.amount,
        )
        return state

    # ------------------------------------------------------------------ helpers
    def _apply(self, state: InvoiceState, parsed: Dict[str, Any]) -> None:
        state.source_format = parsed.get("source_format", state.source_format)
        state.raw_text = parsed.get("raw_text", "")
        state.invoice_number = parsed.get("invoice_number")
        state.vendor = (parsed.get("vendor") or "").strip() or None
        state.vendor_address = parsed.get("vendor_address")
        state.date = parsed.get("date")
        state.due_date = parsed.get("due_date")
        state._due_date_raw = parsed.get("due_date_raw")  # type: ignore[attr-defined]
        state.currency = parsed.get("currency") or "USD"
        state.subtotal = parsed.get("subtotal")
        state.tax_amount = parsed.get("tax_amount")
        state.total = parsed.get("total")
        state.payment_terms = parsed.get("payment_terms")
        state.revision = parsed.get("revision")
        state.notes = parsed.get("notes") or ""
        state.line_items = [
            LineItem(
                item=li.get("item", ""),
                quantity=li.get("quantity"),
                unit_price=li.get("unit_price"),
                amount=li.get("amount"),
                note=li.get("note", ""),
            )
            for li in parsed.get("line_items", [])
        ]

    def _audit_fields(self, state: InvoiceState):
        issues = []
        if not state.invoice_number:
            issues.append("missing invoice number")
        if not state.vendor:
            issues.append("missing vendor name")
        if not state.line_items:
            issues.append("no line items extracted")
        if state.amount is None:
            issues.append("missing invoice total")
        due_raw = getattr(state, "_due_date_raw", None)
        if state.due_date is None and (due_raw or state.source_format in ("txt", "pdf")):
            issues.append(f"unparseable/invalid due date ({due_raw!r})")
        for li in state.line_items:
            if li.amount is None and li.quantity is not None and li.unit_price is not None:
                issues.append(f"missing line amount for {li.item}")
        return issues

    def _repair(self, state: InvoiceState, issues) -> bool:
        """Fill recoverable gaps deterministically. Returns True if anything changed."""
        changed = False
        # line amounts from qty * price
        for li in state.line_items:
            if li.amount is None and li.quantity is not None and li.unit_price is not None:
                li.amount = round(li.quantity * li.unit_price, 2)
                changed = True
        # subtotal from line amounts
        if state.subtotal is None and state.line_items:
            amts = [li.amount for li in state.line_items if li.amount is not None]
            if amts:
                state.subtotal = round(sum(amts), 2)
                changed = True
        # total from subtotal (+ tax)
        if state.total is None and state.subtotal is not None:
            state.total = round(state.subtotal + (state.tax_amount or 0.0), 2)
            state.log(self.stage, f"Recovered missing total = {state.total}")
            changed = True
        return changed

    def _score(self, state: InvoiceState) -> float:
        score = 1.0
        if not state.invoice_number:
            score -= 0.15
        if not state.vendor:
            score -= 0.20
        if not state.line_items:
            score -= 0.25
        if state.amount is None:
            score -= 0.25
        elif state.amount <= 0:
            score -= 0.20
        if state.due_date is None:
            score -= 0.15
        # arithmetic consistency: does total ~= subtotal + tax?
        if state.total is not None and state.subtotal is not None:
            expected = state.subtotal + (state.tax_amount or 0.0)
            if abs(expected - state.total) > max(1.0, 0.02 * abs(state.total)):
                score -= 0.10
        return max(0.0, min(1.0, round(score, 3)))
