"""Typed shared state passed through the agent graph.

One InvoiceState per invoice: every agent reads and updates it, and it
serializes straight to the audit trail and dashboard.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class LineItem:
    item: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEntry:
    stage: str
    summary: str
    detail: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "summary": self.summary, "detail": self.detail, "ts": self.ts}


@dataclass
class InvoiceState:
    # --- source -------------------------------------------------------------
    source_path: str = ""
    source_format: str = ""
    raw_text: str = ""

    # --- extracted (ingestion) ---------------------------------------------
    invoice_number: Optional[str] = None
    vendor: Optional[str] = None
    vendor_address: Optional[str] = None
    date: Optional[str] = None
    due_date: Optional[str] = None
    currency: str = "USD"
    line_items: List[LineItem] = field(default_factory=list)
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total: Optional[float] = None
    payment_terms: Optional[str] = None
    revision: Optional[str] = None
    notes: str = ""

    extraction_confidence: float = 1.0
    extraction_issues: List[str] = field(default_factory=list)

    # --- validation ---------------------------------------------------------
    errors: List[str] = field(default_factory=list)        # blocking -> REJECT
    review_flags: List[str] = field(default_factory=list)  # flag for review -> ESCALATE
    warnings: List[str] = field(default_factory=list)      # soft / informational
    inventory_checks: List[Dict[str, Any]] = field(default_factory=list)
    fraud_signals: List[str] = field(default_factory=list)
    fraud_score: int = 0

    # --- approval -----------------------------------------------------------
    decision: Optional[str] = None            # APPROVE | REJECT | ESCALATE
    risk_level: Optional[str] = None
    approval_reasons: List[str] = field(default_factory=list)
    approval_reasoning: str = ""
    reasoning_source: str = ""          # which engine actually produced the decision
    reflection: Dict[str, Any] = field(default_factory=dict)

    # --- payment ------------------------------------------------------------
    payment_status: Optional[str] = None      # paid | not_attempted | failed
    payment_reference: Optional[str] = None
    payment_message: str = ""

    # --- meta ---------------------------------------------------------------
    final_status: Optional[str] = None
    audit_trail: List[AuditEntry] = field(default_factory=list)

    # ------------------------------------------------------------------ utils
    def log(self, stage: str, summary: str, **detail) -> None:
        self.audit_trail.append(AuditEntry(stage=stage, summary=summary, detail=detail))

    @property
    def amount(self) -> Optional[float]:
        """Canonical payable amount (total, falling back to subtotal)."""
        return self.total if self.total is not None else self.subtotal

    def approval_facts(self) -> Dict[str, Any]:
        """The structured payload handed to the reasoning engine."""
        return {
            "invoice_number": self.invoice_number,
            "vendor": self.vendor,
            "amount": self.amount,
            "currency": self.currency,
            "errors": self.errors,
            "review_flags": self.review_flags,
            "warnings": self.warnings,
            "fraud_signals": self.fraud_signals,
            "fraud_score": self.fraud_score,
            "extraction_confidence": self.extraction_confidence,
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["line_items"] = [li.to_dict() if isinstance(li, LineItem) else li for li in self.line_items]
        d["audit_trail"] = [a.to_dict() if isinstance(a, AuditEntry) else a for a in self.audit_trail]
        d["amount"] = self.amount
        return d
