"""Mock external tools (function-calling targets). Simulated so it runs offline."""

from __future__ import annotations

import sys
import uuid
from typing import Any, Dict


def mock_payment(vendor: str, amount: float, currency: str = "USD") -> Dict[str, Any]:
    """Simulate a banking-API payment call."""
    ref = f"PAY-{uuid.uuid4().hex[:10].upper()}"
    # side-effect log goes to stderr so `--json` stdout stays pure data
    print(f"[payment-api] Paid {amount:,.2f} {currency} to {vendor}  (ref {ref})", file=sys.stderr)
    return {"status": "success", "reference": ref, "vendor": vendor, "amount": amount, "currency": currency}
