"""Multi-format invoice parsers (TXT, JSON, CSV, XML, PDF) into one normalized dict.

Handles typos, OCR artifacts, odd spacing, header-less tables and email-wrapped
invoices. Key entry point: parse(path).
"""

from __future__ import annotations

import csv
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

# Tidy OCR/spacing for display only; inventory membership is decided by the DB.
_CANON = {
    "widgeta": "WidgetA",
    "widgetb": "WidgetB",
    "widgetc": "WidgetC",
    "gadgetx": "GadgetX",
    "fakeitem": "FakeItem",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def norm_key(name: Optional[str]) -> str:
    """Lowercased alphanumeric key for robust item matching ('Widget A'->'widgeta')."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def canonical_item(name: Optional[str]) -> str:
    if not name:
        return ""
    key = norm_key(name)
    if key in _CANON:
        return _CANON[key]
    return re.sub(r"\s+", " ", name).strip()


def _fix_ocr_digits(s: str) -> str:
    """Repair OCR letter/digit confusions inside numeric tokens only."""
    def repl(m: re.Match) -> str:
        tok = m.group(0)
        tok = tok.replace("O", "0").replace("o", "0")
        tok = tok.replace("l", "1").replace("I", "1")
        return tok
    # tokens that look numeric but contain O/o/l/I (e.g. 2O26, 3,500.O0)
    return re.sub(r"[\dOolI][\dOolI.,]*[\dOolI]", repl, s)


def to_number(val: Any) -> Optional[float]:
    """Parse a money/quantity value from many shapes. Returns None if not numeric."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = _fix_ocr_digits(s)
    s = s.replace("$", "").replace("€", "").replace("£", "").replace(",", "")
    s = s.replace("(", "").replace(")", "").strip()
    s = re.sub(r"\s+", "", s)
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    num = float(m.group(0))
    return -num if neg else num


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def normalize_date(raw: Optional[str]) -> Tuple[Optional[str], str]:
    """
    Return (iso_date_or_None, raw). Non-dates like 'yesterday' / 'immediate'
    return (None, raw) so the validator can flag them.
    """
    if not raw:
        return None, ""
    r = raw.strip()
    fixed = _fix_ocr_digits(r)
    # ISO already
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", fixed)
    if m:
        y, mo, d = map(int, m.groups())
        return _iso(y, mo, d), r
    # MM/DD/YYYY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", fixed)
    if m:
        mo, d, y = map(int, m.groups())
        return _iso(y, mo, d), r
    # DD-Mon-YYYY  (26-Jan-2026)
    m = re.search(r"(\d{1,2})-([A-Za-z]{3,})-(\d{4})", fixed)
    if m:
        d = int(m.group(1)); mo = _MONTHS.get(m.group(2)[:3].lower()); y = int(m.group(3))
        if mo:
            return _iso(y, mo, d), r
    # Month DD, YYYY  /  Mon DD YYYY
    m = re.search(r"([A-Za-z]{3,})\.?\s+(\d{1,2}),?\s+(\d{4})", fixed)
    if m:
        mo = _MONTHS.get(m.group(1)[:3].lower()); d = int(m.group(2)); y = int(m.group(3))
        if mo:
            return _iso(y, mo, d), r
    return None, r


def _iso(y: int, mo: int, d: int) -> Optional[str]:
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _currency_from_text(text: str) -> str:
    if "€" in text or re.search(r"\bEUR\b", text):
        return "EUR"
    if "£" in text or re.search(r"\bGBP\b", text):
        return "GBP"
    return "USD"


# ---------------------------------------------------------------------------
# format detection + raw loading
# ---------------------------------------------------------------------------
def detect_format(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".") or "txt"


def load_raw(path: str) -> str:
    fmt = detect_format(path)
    if fmt == "pdf":
        return _pdf_text(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _pdf_text(path: str) -> str:
    try:
        import pdfplumber
        out = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                out.append(page.extract_text() or "")
        return "\n".join(out)
    except Exception as exc:  # noqa: BLE001
        return f"[PDF extraction failed: {exc}]"


# ---------------------------------------------------------------------------
# top-level dispatch
# ---------------------------------------------------------------------------
def parse(path: str) -> Dict[str, Any]:
    fmt = detect_format(path)
    if fmt == "json":
        data = _parse_json(path)
    elif fmt == "csv":
        data = _parse_csv(path)
    elif fmt == "xml":
        data = _parse_xml(path)
    else:  # txt, pdf, anything -> text heuristics
        data = _parse_text(load_raw(path))
    data["source_format"] = fmt
    data.setdefault("raw_text", "")
    # normalize item names for display
    for li in data.get("line_items", []):
        li["item"] = canonical_item(li.get("item"))
    return data


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def _parse_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        obj = json.load(fh)
    vendor = obj.get("vendor")
    v_name, v_addr = "", None
    if isinstance(vendor, dict):
        v_name = vendor.get("name") or ""
        v_addr = vendor.get("address")
    elif isinstance(vendor, str):
        v_name = vendor

    items = []
    for li in obj.get("line_items", obj.get("items", [])) or []:
        items.append({
            "item": li.get("item") or li.get("name") or "",
            "quantity": to_number(li.get("quantity", li.get("qty"))),
            "unit_price": to_number(li.get("unit_price", li.get("price"))),
            "amount": to_number(li.get("amount")),
            "note": li.get("note", ""),
        })
    return {
        "invoice_number": obj.get("invoice_number") or obj.get("invoice"),
        "vendor": v_name,
        "vendor_address": v_addr,
        "date": (normalize_date(obj.get("date"))[0] or obj.get("date")),
        "due_date": (normalize_date(obj.get("due_date"))[0] if obj.get("due_date") else None),
        "due_date_raw": obj.get("due_date"),
        "currency": obj.get("currency", "USD"),
        "line_items": items,
        "subtotal": to_number(obj.get("subtotal")),
        "tax_amount": to_number(obj.get("tax_amount")),
        "total": to_number(obj.get("total")),
        "payment_terms": obj.get("payment_terms"),
        "revision": obj.get("revision"),
        "notes": obj.get("notes", ""),
        "raw_text": json.dumps(obj, indent=2),
    }


# ---------------------------------------------------------------------------
# CSV  (handles both vertical field,value and wide multi-row layouts)
# ---------------------------------------------------------------------------
def _parse_csv(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return _empty("csv")
    header = [c.strip().lower() for c in rows[0]]
    raw = "\n".join(",".join(r) for r in rows)

    # vertical: field,value
    if header[:2] == ["field", "value"]:
        return _parse_csv_vertical(rows[1:], raw)
    return _parse_csv_wide(header, rows[1:], raw)


def _parse_csv_vertical(body: List[List[str]], raw: str) -> Dict[str, Any]:
    out = _empty("csv"); out["raw_text"] = raw
    items: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {}
    for row in body:
        if len(row) < 2:
            continue
        key = row[0].strip().lower(); val = row[1].strip()
        if key == "item":
            if cur:
                items.append(cur)
            cur = {"item": val, "quantity": None, "unit_price": None, "amount": None, "note": ""}
        elif key in ("quantity", "qty"):
            cur["quantity"] = to_number(val)
        elif key in ("unit_price", "unit price", "price"):
            cur["unit_price"] = to_number(val)
        elif key == "invoice_number":
            out["invoice_number"] = val
        elif key == "vendor":
            out["vendor"] = val
        elif key == "date":
            out["date"] = normalize_date(val)[0] or val
        elif key == "due_date":
            out["due_date"] = normalize_date(val)[0]; out["due_date_raw"] = val
        elif key == "subtotal":
            out["subtotal"] = to_number(val)
        elif key == "tax":
            out["tax_amount"] = to_number(val)
        elif key == "total":
            out["total"] = to_number(val)
        elif key in ("payment_terms", "terms"):
            out["payment_terms"] = val
    if cur:
        items.append(cur)
    out["line_items"] = items
    out["currency"] = _currency_from_text(raw)
    return out


def _parse_csv_wide(header: List[str], body: List[List[str]], raw: str) -> Dict[str, Any]:
    out = _empty("csv"); out["raw_text"] = raw
    idx = {name: i for i, name in enumerate(header)}

    def cell(row, *names):
        for n in names:
            if n in idx and idx[n] < len(row):
                v = row[idx[n]].strip()
                if v:
                    return v
        return ""

    items = []
    for row in body:
        item = cell(row, "item")
        # summary rows (Subtotal:/Tax:/Total:) live in the price/total columns
        joined = " ".join(c.strip() for c in row).lower()
        if not item:
            if "subtotal" in joined:
                out["subtotal"] = _last_number(row)
            elif "tax" in joined:
                out["tax_amount"] = _last_number(row)
            elif "total" in joined:
                out["total"] = _last_number(row)
            continue
        items.append({
            "item": item,
            "quantity": to_number(cell(row, "qty", "quantity")),
            "unit_price": to_number(cell(row, "unit price", "unit_price", "price", "rate")),
            "amount": to_number(cell(row, "line total", "amount", "total")),
            "note": "",
        })
        if not out["invoice_number"]:
            out["invoice_number"] = cell(row, "invoice number", "invoice_number", "invoice")
        if not out["vendor"]:
            out["vendor"] = cell(row, "vendor")
        if not out["date"]:
            d = cell(row, "date")
            out["date"] = normalize_date(d)[0] or d or None
        if not out["due_date"]:
            dd = cell(row, "due date", "due_date")
            if dd:
                out["due_date"] = normalize_date(dd)[0]; out["due_date_raw"] = dd
    out["line_items"] = items
    out["currency"] = _currency_from_text(raw)
    return out


def _last_number(row: List[str]) -> Optional[float]:
    for c in reversed(row):
        n = to_number(c)
        if n is not None:
            return n
    return None


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------
def _parse_xml(path: str) -> Dict[str, Any]:
    out = _empty("xml")
    tree = ET.parse(path); root = tree.getroot()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        out["raw_text"] = fh.read()

    def find(tag):
        el = root.find(f".//{tag}")
        return el.text.strip() if el is not None and el.text else None

    out["invoice_number"] = find("invoice_number")
    out["vendor"] = find("vendor")
    d = find("date"); out["date"] = normalize_date(d)[0] or d
    dd = find("due_date"); out["due_date"] = normalize_date(dd)[0]; out["due_date_raw"] = dd
    out["currency"] = find("currency") or "USD"
    out["payment_terms"] = find("payment_terms")
    out["subtotal"] = to_number(find("subtotal"))
    out["tax_amount"] = to_number(find("tax_amount"))
    out["total"] = to_number(find("total"))

    items = []
    for it in root.findall(".//line_items/item"):
        def sub(tag):
            el = it.find(tag)
            return el.text.strip() if el is not None and el.text else None
        items.append({
            "item": sub("name") or sub("item") or "",
            "quantity": to_number(sub("quantity")),
            "unit_price": to_number(sub("unit_price")),
            "amount": to_number(sub("amount")),
            "note": "",
        })
    out["line_items"] = items
    return out


# ---------------------------------------------------------------------------
# free-form TEXT / PDF text
# ---------------------------------------------------------------------------
_LABELS = {
    "invoice_number": [r"invoice\s*number", r"invoice\s*#?\s*inv", r"inv\s*no", r"inv\s*#", r"invoice"],
    "vendor": [r"vendor", r"vndr", r"from"],
    "date": [r"^\s*date", r"^\s*dt\b", r"^\s*date\b"],
    "due_date": [r"due\s*date", r"due\s*dt", r"^\s*due\b"],
    "payment_terms": [r"payment\s*terms", r"pymnt\s*terms", r"^\s*terms"],
}

_SKIP_ITEM_KW = re.compile(
    r"(subtotal|sub total|sales tax|\btax\b|shipping|\btotal\b|amount:|amt:|terms|thank|"
    r"notes?:|payment|ref |deliver|contact|attn|bill to|vendor|invoice|due|\bdate\b|"
    r"^\s*from\b|^\s*to\b|^\s*item\s+qty|^\s*description\s+qty|^\s*qty\s+unit|@ ea)",
    re.IGNORECASE,
)


def _parse_text(text: str) -> Dict[str, Any]:
    # OCR repair happens only where numbers are parsed, never globally
    # (that would corrupt words like "Consolidated").
    out = _empty("txt")
    out["raw_text"] = text
    lines = [ln.rstrip() for ln in text.splitlines()]

    # invoice number
    m = re.search(r"INV[-\s]?(\d{3,6})", text, re.IGNORECASE)
    if m:
        out["invoice_number"] = f"INV-{m.group(1)}"
    else:
        m = re.search(r"inv(?:oice)?\s*(?:number|no|#)\s*[:#]?\s*([A-Za-z0-9-]+)", text, re.IGNORECASE)
        if m:
            out["invoice_number"] = _normalize_inv_number(m.group(1))

    # labeled scalar fields
    out["vendor"] = _find_labeled(lines, ["vendor", "vndr"]) or _find_from_vendor(lines)
    out["date"] = _norm_or_raw(_find_labeled(lines, ["date", "dt"], avoid=["due"]))
    dd_raw = _find_labeled(lines, ["due date", "due dt", "due"])
    out["due_date"] = normalize_date(dd_raw)[0]
    out["due_date_raw"] = dd_raw
    out["payment_terms"] = _find_labeled(lines, ["payment terms", "pymnt terms", "terms"])
    out["currency"] = _currency_from_text(text)

    # notes (fraud language often lives here)
    nm = re.search(r"notes?:\s*(.+)", text, re.IGNORECASE)
    if nm:
        out["notes"] = nm.group(1).strip()

    # line items + totals
    out["line_items"] = _extract_text_items(lines)
    out["subtotal"] = _find_money(text, ["subtotal", "sub total"])
    out["tax_amount"] = _find_money(text, ["sales tax", "tax"])
    total = _find_money(text, ["total amount", "grand total", "total", "amt"])
    out["total"] = total
    return out


def _normalize_inv_number(num: str) -> str:
    num = num.strip()
    if re.fullmatch(r"\d{3,6}", num):
        return f"INV-{num}"
    return num


def _find_labeled(lines, labels, avoid=None) -> Optional[str]:
    avoid = avoid or []
    for ln in lines:
        low = ln.lower()
        if any(a in low for a in avoid):
            continue
        for lab in labels:
            m = re.match(rf"\s*{lab}\s*[:#]\s*(.+)", ln, re.IGNORECASE)
            if m:
                return m.group(1).strip()
    return None


def _find_from_vendor(lines) -> Optional[str]:
    """For email/scanned invoices: 'FROM: QuickShip ...' but skip email addresses."""
    for ln in lines:
        m = re.match(r"\s*from\s*[:#]\s*(.+)", ln, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if "@" not in cand:
                return cand
    return None


def _norm_or_raw(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    iso, r = normalize_date(raw)
    return iso or r


def _find_money(text: str, labels) -> Optional[float]:
    for lab in labels:
        m = re.search(rf"{lab}\s*[:]?\s*\$?([\d,OolI]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            return to_number(m.group(1))
    return None


_ITEM_PATTERNS = [
    # name  qty:/qty  N   unit price:/@  $P
    re.compile(r"^\s*(?P<item>[A-Za-z][\w .()/-]*?)\s+qty:?\s*(?P<qty>-?\d+)\s+(?:unit\s*price:?|@)\s*\$?(?P<price>[\d,]+(?:\.\d+)?)", re.IGNORECASE),
    # bullet/email:  - Item  x12  $400.00   (x must be attached to the digits so
    # a standalone 'X' in a name like "Gadget X" is not mistaken for a quantity)
    re.compile(r"^\s*[-*]?\s*(?P<item>[A-Za-z][\w .()/-]*?)\s+x(?P<qty>\d+)\b\s+\$?(?P<price>[\d,]+(?:\.\d+)?)"),
    # columnar:  Item  qty  $price  $amount
    re.compile(r"^\s*(?P<item>[A-Za-z][\w .()/-]*?)\s+(?P<qty>-?\d+)\s+\$?(?P<price>[\d,]+(?:\.\d+)?)\s+\$?(?P<amount>[\d,]+(?:\.\d+)?)"),
    # columnar without amount:  Item  qty  $price
    re.compile(r"^\s*(?P<item>[A-Za-z][\w .()/-]*?)\s+(?P<qty>-?\d+)\s+\$?(?P<price>[\d,]+(?:\.\d+)?)\s*$"),
]


def _extract_text_items(lines) -> List[Dict[str, Any]]:
    items = []
    for ln in lines:
        if not ln.strip() or set(ln.strip()) <= set("-=_| "):
            continue
        if _SKIP_ITEM_KW.search(ln):
            continue
        for pat in _ITEM_PATTERNS:
            m = pat.search(ln)
            if m:
                gd = m.groupdict()
                name = gd["item"].strip()
                if norm_key(name) == "" or len(norm_key(name)) < 2:
                    break
                items.append({
                    "item": name,
                    "quantity": to_number(gd.get("qty")),
                    "unit_price": to_number(gd.get("price")),
                    "amount": to_number(gd.get("amount")),
                    "note": "",
                })
                break
    return items


# ---------------------------------------------------------------------------
def _empty(fmt: str) -> Dict[str, Any]:
    return {
        "invoice_number": None, "vendor": None, "vendor_address": None,
        "date": None, "due_date": None, "due_date_raw": None, "currency": "USD",
        "line_items": [], "subtotal": None, "tax_amount": None, "total": None,
        "payment_terms": None, "revision": None, "notes": "", "raw_text": "",
        "source_format": fmt,
    }
