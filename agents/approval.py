"""Stage 3 : Approval.

VP-level review: a first-pass decision, then a reflection loop that can overturn
it (clear a clean high-value invoice, or catch an approval that ignored a
blocker). Both passes are recorded.
"""

from __future__ import annotations

from core.state import InvoiceState


class ApprovalAgent:
    stage = "approval"

    def __init__(self, engine):
        self.engine = engine

    def run(self, state: InvoiceState) -> InvoiceState:
        facts = state.approval_facts()

        # --- pass 1: initial decision ---------------------------------------
        draft = self.engine.decide_approval(facts)
        used_llm = getattr(self.engine, "last_used_llm", False)
        state.reasoning_source = getattr(self.engine, "name", "engine") if used_llm else "rule-based reasoner"

        # --- pass 2: self-critique / reflection -----------------------------
        reflection = self.engine.reflect(self.stage, draft, facts)
        final = reflection.get("revised_decision", draft["decision"])

        state.decision = final
        state.risk_level = draft.get("risk_level", "medium")
        state.approval_reasons = list(draft.get("reasons", []))
        state.reflection = {
            "draft_decision": draft["decision"],
            "final_decision": final,
            "agree": reflection.get("agree", True),
            "concerns": reflection.get("concerns", []),
            "note": reflection.get("note", ""),
        }
        # compose a readable rationale from both passes
        parts = [draft.get("reasoning", "")]
        if reflection.get("concerns"):
            parts.append("Reflection — " + " ".join(reflection["concerns"]))
        if final != draft["decision"]:
            parts.append(f"Decision revised {draft['decision']} → {final} after reflection.")
        state.approval_reasoning = " ".join(p for p in parts if p).strip()

        changed = "revised" if final != draft["decision"] else "confirmed"
        state.log(
            self.stage,
            f"VP decision: {final} (risk {state.risk_level}); reflection {changed} the call.",
            draft_decision=draft["decision"],
            final_decision=final,
            reasons=state.approval_reasons,
            reflection=state.reflection,
            engine=getattr(self.engine, "name", "?"),
        )
        return state
