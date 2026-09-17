"""Parser robustness across formats, typos, and OCR artifacts."""

import os

import pytest

from core import parsers

INV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "invoices")


def p(name):
    return parsers.parse(os.path.join(INV, name))


def test_number_helpers_fix_ocr():
    assert parsers.to_number("$3,500.O0") == 3500.00      # letter O -> 0
    assert parsers.to_number("$1,000.00") == 1000.00
    assert parsers.to_number("(250)") == -250.0            # parenthesised negative
    assert parsers.to_number("") is None


def test_date_normalisation_variants():
    assert parsers.normalize_date("2026-01-15")[0] == "2026-01-15"
    assert parsers.normalize_date("01/28/2026")[0] == "2026-01-28"
    assert parsers.normalize_date("26-Jan-2O26")[0] == "2026-01-26"   # OCR O + Mon
    assert parsers.normalize_date("January 27, 2026")[0] == "2026-01-27"
    assert parsers.normalize_date("yesterday")[0] is None            # not a date


def test_item_key_matching():
    assert parsers.norm_key("Widget A") == parsers.norm_key("WidgetA") == "widgeta"
    assert parsers.canonical_item("gadget x") == "GadgetX"


def test_txt_clean():
    d = p("invoice_1001.txt")
    assert d["invoice_number"] == "INV-1001"
    assert d["vendor"] == "Widgets Inc."
    assert d["total"] == 5000.0
    assert len(d["line_items"]) == 2


def test_txt_typos_invoce():
    d = p("invoice_1002.txt")            # "INVOCE", "Vndr", "Itms", "Amt"
    assert d["invoice_number"] == "INV-1002"
    assert d["line_items"][0]["item"] == "GadgetX"
    assert d["line_items"][0]["quantity"] == 20
    assert d["total"] == 15000.0


def test_vendor_name_not_corrupted_by_ocr():
    d = p("invoice_1010.txt")
    assert d["vendor"] == "Consolidated Materials Group"   # not "Cons01idated"


def test_email_wrapped_unknown_items():
    d = p("invoice_1008.txt")
    names = {li["item"] for li in d["line_items"]}
    assert names == {"SuperGizmo", "MegaSprocket"}


def test_json_nested_vendor_and_negative():
    d = p("invoice_1009.json")
    assert d["vendor"] in ("", None)
    assert d["line_items"][0]["quantity"] == -5


def test_csv_vertical_and_wide():
    v = p("invoice_1006.csv")
    assert v["invoice_number"] == "INV-1006" and len(v["line_items"]) == 2
    w = p("invoice_1007.csv")
    assert w["vendor"] == "MegaWidgets Corp" and w["total"] == 15525.0


def test_xml_currency():
    d = p("invoice_1014.xml")
    assert d["currency"] == "EUR"
    assert len(d["line_items"]) == 2


def test_pdf_messy_ocr_items():
    d = p("invoice_1012.pdf")
    names = [li["item"] for li in d["line_items"]]
    assert names == ["WidgetA", "WidgetB", "GadgetX"]      # "Gadget X" not split
