"""Stage 4 : Payment.

Terminal stage. APPROVE : call the mock payment tool. ESCALATE : park for a
review. REJECT : log the rejection, no payment attempted.
"""

from __future__ import annotations

from core.state import InvoiceState
from core.tools import mock_payment


class PaymentAgent:
    stage = "payment"

    def __init__(self, engine=None):
        self.engine = engine

    def run(self, state: InvoiceState) -> InvoiceState:
        decision = state.decision

        if decision == "APPROVE":
            result = mock_payment(state.vendor or "UNKNOWN", state.amount or 0.0, state.currency)
            state.payment_status = "paid"
            state.payment_reference = result["reference"]
            state.payment_message = (
                f"Paid {state.amount:,.2f} {state.currency} to {state.vendor} "
                f"(ref {result['reference']})."
            )
            state.final_status = "PAID"

        elif decision == "ESCALATE":
            state.payment_status = "not_attempted"
            state.payment_message = "Flagged for review — payment not auto-executed."
            state.final_status = "ESCALATED"

        else:  # REJECT (or anything unexpected -> safe default)
            state.payment_status = "not_attempted"
            reason = state.approval_reasoning or "; ".join(state.errors + state.fraud_signals)
            state.payment_message = f"Rejected — no payment. Reason: {reason}"
            state.final_status = "REJECTED"

        state.log(
            self.stage,
            f"{state.final_status}: {state.payment_message}",
            payment_status=state.payment_status,
            reference=state.payment_reference,
        )
        return state
