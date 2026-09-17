"""Approval decisions + reflection loop, and full end-to-end outcomes."""

import os

from core.state import InvoiceState
from agents.approval import ApprovalAgent
from core.graph import process_invoice
from llm.engine import MockReasoningEngine
from config import DB_PATH

ENGINE = MockReasoningEngine()
INV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "invoices")


def _decide(**facts):
    base = {"amount": 100, "currency": "USD", "errors": [], "review_flags": [],
            "warnings": [], "fraud_signals": [], "fraud_score": 0, "extraction_confidence": 1.0}
    base.update(facts)
    s = InvoiceState(source_path="x")
    # inject facts via a tiny shim
    s.total = base["amount"]
    s.currency = base["currency"]
    s.errors = base["errors"]; s.review_flags = base["review_flags"]
    s.warnings = base["warnings"]; s.fraud_signals = base["fraud_signals"]
    s.fraud_score = base["fraud_score"]; s.extraction_confidence = base["extraction_confidence"]
    ApprovalAgent(ENGINE).run(s)
    return s


def test_clean_low_value_approves():
    assert _decide(amount=5000).decision == "APPROVE"


def test_fraud_rejects():
    s = _decide(amount=100000, fraud_signals=["zero-stock item"], fraud_score=85)
    assert s.decision == "REJECT"


def test_blocking_error_rejects():
    assert _decide(errors=["Missing vendor"]).decision == "REJECT"


def test_review_flag_escalates():
    assert _decide(review_flags=["exceeds stock"]).decision == "ESCALATE"


def test_high_value_clean_escalate_then_reflect_to_approve():
    s = _decide(amount=12500)
    # reflection should upgrade the high-value-but-clean escalation
    assert s.reflection["draft_decision"] == "ESCALATE"
    assert s.decision == "APPROVE"


def test_warnings_only_do_not_block():
    assert _decide(amount=5000, warnings=["price anomaly"]).decision == "APPROVE"


# ---- end-to-end expected outcomes for the sample set -----------------------
EXPECTED = {
    "invoice_1001.txt": "PAID",
    "invoice_1002.txt": "ESCALATED",     # GadgetX 20 > stock 5
    "invoice_1003.txt": "REJECTED",      # FakeItem + fraud language
    "invoice_1004.json": "PAID",
    "invoice_1005.json": "ESCALATED",    # GadgetX 8 > 5
    "invoice_1006.csv": "PAID",
    "invoice_1007.csv": "ESCALATED",     # WidgetA 20>15, WidgetB 15>10
    "invoice_1008.txt": "ESCALATED",     # unknown items
    "invoice_1009.json": "REJECTED",     # negative qty / no vendor
    "invoice_1010.txt": "PAID",          # duplicate WidgetA aggregates to 12<=15
    "invoice_1011.txt": "PAID",
    "invoice_1012.txt": "PAID",          # messy OCR but clean & in stock
    "invoice_1013.json": "ESCALATED",    # bulk overruns
    "invoice_1014.xml": "ESCALATED",     # EUR -> FX sign-off
    "invoice_1015.csv": "PAID",
    "invoice_1016.json": "ESCALATED",    # WidgetC unknown
    "invoice_1017.json": "PAID",         # high-value clean -> reflection approves
}


def test_end_to_end_outcomes():
    # deterministic rule engine, so outcomes are stable and tests never hit the network
    engine = MockReasoningEngine()
    for fname, expected in EXPECTED.items():
        state, _ = process_invoice(os.path.join(INV, fname), engine, DB_PATH)
        assert state.final_status == expected, f"{fname}: got {state.final_status}, want {expected}"
