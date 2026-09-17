"""Stage 2 : Validation.

Check data against the SQLite inventory and internal consistency. Findings sort
into errors (block : REJECT), review_flags (flag for review : ESCALATE) and warnings
(soft). Also scores fraud from independent signals.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections import defaultdict
from typing import Dict, Tuple

from core import parsers
from core.state import InvoiceState
from config import DB_PATH, PRICE_TOLERANCE

_URGENCY_RE = re.compile(
    r"(urgent|immediately|asap|avoid penalt|wire transfer|act now|final notice|!!!)",
    re.IGNORECASE,
)
_PAST_DUE_RE = re.compile(r"(yesterday|immediate|overdue|past due|on receipt)", re.IGNORECASE)


class ValidationAgent:
    stage = "validation"

    def __init__(self, engine=None, db_path: str = DB_PATH):
        self.engine = engine
        self.db_path = db_path

    # ------------------------------------------------------------------ public
    def run(self, state: InvoiceState) -> InvoiceState:
        inventory = self._load_inventory()

        self._check_data_integrity(state)
        self._check_inventory(state, inventory)
        self._check_math(state)
        self._check_fraud(state)

        state.fraud_score = min(100, state.fraud_score)
        state.log(
            self.stage,
            f"Validation: {len(state.errors)} error(s), {len(state.review_flags)} flag(s), "
            f"{len(state.warnings)} warning(s), fraud_score={state.fraud_score}",
            errors=state.errors,
            review_flags=state.review_flags,
            warnings=state.warnings,
            fraud_signals=state.fraud_signals,
            inventory_checks=state.inventory_checks,
        )
        return state

    # ------------------------------------------------------------------ checks
    def _load_inventory(self) -> Dict[str, Tuple[str, int, float]]:
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"Inventory DB not found at {self.db_path}. Run `python setup_db.py` first."
            )
        conn = sqlite3.connect(self.db_path)
        inv: Dict[str, Tuple[str, int, float]] = {}
        for item, stock, price in conn.execute("SELECT item, stock, unit_price FROM inventory"):
            inv[parsers.norm_key(item)] = (item, int(stock), price)
        conn.close()
        return inv

    def _check_data_integrity(self, state: InvoiceState) -> None:
        if not state.vendor:
            state.errors.append("Missing vendor name — cannot verify payee.")
        if state.amount is None:
            state.errors.append("Missing invoice total.")
        elif state.amount <= 0:
            state.errors.append(f"Non-positive invoice total ({state.amount}).")
        if not state.line_items:
            state.review_flags.append("No line items could be extracted.")

    def _check_inventory(self, state: InvoiceState, inventory) -> None:
        # aggregate quantities by base item (strip parentheticals like "(rush order)")
        requested = defaultdict(float)
        display = {}
        prices = defaultdict(list)
        for li in state.line_items:
            base = re.sub(r"\(.*?\)", "", li.item or "").strip()
            key = parsers.norm_key(base)
            if not key:
                continue
            display[key] = parsers.canonical_item(base)
            if li.quantity is not None:
                requested[key] += li.quantity
            if li.unit_price is not None:
                prices[key].append(li.unit_price)

        for key, qty in requested.items():
            name = display.get(key, key)
            if key not in inventory:
                state.review_flags.append(f"Unknown item not in inventory: '{name}'.")
                state.inventory_checks.append(
                    {"item": name, "requested": qty, "stock": None, "status": "unknown"}
                )
                continue

            real_name, stock, cat_price = inventory[key]
            if qty < 0:
                state.errors.append(f"Negative quantity for '{real_name}' ({qty}).")
                status = "invalid_qty"
            elif stock <= 0:
                state.errors.append(
                    f"Item '{real_name}' is out of stock (0 available) but {int(qty)} requested — suspicious."
                )
                state.fraud_signals.append(f"zero-stock item requested: {real_name}")
                state.fraud_score += 40
                status = "out_of_stock"
            elif qty > stock:
                state.review_flags.append(
                    f"Quantity for '{real_name}' ({int(qty)}) exceeds available stock ({stock})."
                )
                status = "insufficient_stock"
            else:
                status = "ok"
            state.inventory_checks.append(
                {"item": real_name, "requested": qty, "stock": stock, "status": status}
            )

            # soft price-anomaly check
            for p in prices.get(key, []):
                if cat_price and abs(p - cat_price) / cat_price > PRICE_TOLERANCE:
                    state.warnings.append(
                        f"Unit price for '{real_name}' (${p:,.2f}) differs from catalog "
                        f"(${cat_price:,.2f}) by >{int(PRICE_TOLERANCE*100)}%."
                    )
                    break

    def _check_math(self, state: InvoiceState) -> None:
        line_sum = sum(li.amount for li in state.line_items if li.amount is not None)
        if state.line_items and state.subtotal is not None and line_sum:
            if abs(line_sum - state.subtotal) > max(1.0, 0.02 * state.subtotal):
                state.warnings.append(
                    f"Line items sum to ${line_sum:,.2f} but subtotal is ${state.subtotal:,.2f} "
                    f"(may include shipping/fees or a data error)."
                )
        if state.subtotal is not None and state.total is not None:
            expected = state.subtotal + (state.tax_amount or 0.0)
            if abs(expected - state.total) > max(1.0, 0.02 * state.total):
                state.warnings.append(
                    f"Subtotal+tax (${expected:,.2f}) ≠ stated total (${state.total:,.2f})."
                )
        if state.currency and state.currency != "USD":
            state.review_flags.append(
                f"Invoice is in {state.currency}; needs FX conversion + sign-off before payment "
                f"(threshold checks assume USD)."
            )

    def _check_fraud(self, state: InvoiceState) -> None:
        blob = " ".join(filter(None, [state.notes, state.raw_text or ""]))
        if _URGENCY_RE.search(blob):
            state.fraud_signals.append("high-pressure/urgent or wire-transfer language")
            state.fraud_score += 25
        due_raw = getattr(state, "_due_date_raw", None) or ""
        if _PAST_DUE_RE.search(str(due_raw)):
            state.fraud_signals.append(f"suspicious due date: '{due_raw}'")
            state.fraud_score += 20
        # invoice date after due date -> impossible timeline
        if state.date and state.due_date and state.due_date < state.date:
            state.fraud_signals.append(
                f"due date {state.due_date} precedes invoice date {state.date}"
            )
            state.fraud_score += 15
        if state.amount is not None and state.amount < 0:
            state.fraud_score += 20
