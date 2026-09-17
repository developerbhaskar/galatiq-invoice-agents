"""The exact data the dashboard renders, tested against real pipeline states."""

import os

from core.graph import process_invoice
from core import view
from llm.engine import MockReasoningEngine
from config import DB_PATH

INV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "invoices")
ENGINE = MockReasoningEngine()


def _run(name):
    return process_invoice(os.path.join(INV, name), ENGINE, DB_PATH)[0]


def test_stage_chips_and_pill():
    s = _run("invoice_1001.txt")
    chips = view.stage_chips_html(s)
    for stage in ("Ingestion", "Validation", "Approval", "Payment"):
        assert stage in chips
    assert "PAID" in view.pill_html(s.final_status)


def test_batch_row_shape():
    s = _run("invoice_1003.txt")
    row = view.batch_row(s)
    assert set(row) == {"Invoice", "Vendor", "Fmt", "Amount", "Conf", "Status", "Reason"}
    assert row["Invoice"] == "INV-1003"
    assert "REJECTED" in row["Status"]


def test_batch_metrics_add_up():
    results = [_run(f"invoice_{n}") for n in
               ("1001.txt", "1003.txt", "1008.txt", "1014.xml", "1017.json")]
    m = view.batch_metrics(results)
    assert m["total"] == 5
    assert m["paid"] + m["escalated"] + m["rejected"] == 5
    assert m["held"] > 0


def test_extracted_fields_keys():
    s = _run("invoice_1014.xml")
    f = view.extracted_fields(s)
    assert f["currency"] == "EUR"
    assert "total" in f and "due_date" in f
