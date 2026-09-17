"""Validation-agent rules against the mock inventory."""

import os

from core.state import InvoiceState, LineItem
from agents.validation import ValidationAgent
from config import DB_PATH

AGENT = ValidationAgent(db_path=DB_PATH)


def _state(items, **kw):
    s = InvoiceState(source_path="x", vendor=kw.get("vendor", "V"), total=kw.get("total", 100.0))
    s.currency = kw.get("currency", "USD")
    s.line_items = [LineItem(**it) for it in items]
    s.raw_text = kw.get("raw_text", "")
    s._due_date_raw = kw.get("due_raw")
    return s


def test_within_stock_passes():
    s = _state([{"item": "WidgetA", "quantity": 5, "unit_price": 250.0}])
    AGENT.run(s)
    assert not s.errors and not s.review_flags


def test_quantity_exceeds_stock_flagged():
    s = _state([{"item": "GadgetX", "quantity": 20, "unit_price": 750.0}])
    AGENT.run(s)
    assert any("exceeds available stock" in f for f in s.review_flags)


def test_zero_stock_item_is_fraud():
    s = _state([{"item": "FakeItem", "quantity": 100, "unit_price": 1000.0}])
    AGENT.run(s)
    assert any("out of stock" in e for e in s.errors)
    assert s.fraud_score >= 40


def test_unknown_item_flagged():
    s = _state([{"item": "SuperGizmo", "quantity": 2, "unit_price": 400.0}])
    AGENT.run(s)
    assert any("Unknown item" in f for f in s.review_flags)


def test_negative_quantity_is_error():
    s = _state([{"item": "WidgetA", "quantity": -5, "unit_price": 250.0}], total=-250.0)
    AGENT.run(s)
    assert any("Negative quantity" in e for e in s.errors)


def test_duplicate_lines_aggregate_for_stock():
    # WidgetA 8 + WidgetA(rush) 4 = 12 <= 15 -> OK
    s = _state([
        {"item": "WidgetA", "quantity": 8, "unit_price": 250.0},
        {"item": "WidgetA (rush order)", "quantity": 4, "unit_price": 300.0},
    ])
    AGENT.run(s)
    assert not any("exceeds" in f for f in s.review_flags)


def test_urgent_language_is_fraud_signal():
    s = _state([{"item": "WidgetA", "quantity": 1, "unit_price": 250.0}],
               raw_text="URGENT - Pay immediately! Wire transfer preferred.")
    AGENT.run(s)
    assert s.fraud_signals and s.fraud_score >= 25


def test_missing_vendor_is_error():
    s = _state([{"item": "WidgetA", "quantity": 1, "unit_price": 250.0}], vendor=None)
    AGENT.run(s)
    assert any("vendor" in e.lower() for e in s.errors)


def test_foreign_currency_escalates():
    s = _state([{"item": "WidgetA", "quantity": 1, "unit_price": 250.0}], currency="EUR")
    AGENT.run(s)
    assert any("FX" in f or "EUR" in f for f in s.review_flags)
