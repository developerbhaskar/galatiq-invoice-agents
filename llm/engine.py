"""Reasoning engine.

The approval step calls a small engine interface. When a Gemini API key is set,
`GeminiEngine` handles the reasoning; if a call ever fails it falls back to the
deterministic rule engine, so a payment run never breaks on the model layer.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config import HIGH_VALUE_THRESHOLD, get_llm_config


# ===========================================================================
# Base interface + deterministic rule engine
# ===========================================================================
class ReasoningEngine:
    name = "base"
    last_used_llm = False

    def decide_approval(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def reflect(self, role: str, draft: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class MockReasoningEngine(ReasoningEngine):
    """Deterministic rule engine — the fallback when the model is unavailable."""

    name = "rule-based reasoner"

    def decide_approval(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        amount = float(facts.get("amount") or 0.0)
        currency = facts.get("currency", "USD")
        errors: List[str] = list(facts.get("errors", []))
        review_flags: List[str] = list(facts.get("review_flags", []))
        warnings: List[str] = list(facts.get("warnings", []))
        fraud_signals: List[str] = list(facts.get("fraud_signals", []))
        fraud_score = int(facts.get("fraud_score", 0))
        confidence = float(facts.get("extraction_confidence", 1.0))
        high_value = amount >= HIGH_VALUE_THRESHOLD

        reasons: List[str] = []
        if fraud_score >= 60 or fraud_signals:
            decision, risk = "REJECT", "high"
            reasons.append(f"Fraud signals present (score {fraud_score}): " + "; ".join(fraud_signals[:4]))
        elif errors:
            decision, risk = "REJECT", "high"
            reasons.append("Blocking validation errors: " + "; ".join(errors[:4]))
        elif review_flags:
            decision, risk = "ESCALATE", "medium"
            reasons.append("Flagged for review: " + "; ".join(review_flags[:4]))
        elif confidence < 0.5:
            decision, risk = "ESCALATE", "medium"
            reasons.append(f"Low extraction confidence ({confidence:.0%}); data should be verified.")
        elif high_value:
            decision, risk = "ESCALATE", "medium"
            reasons.append(
                f"High-value invoice ${amount:,.2f} {currency} (>= ${HIGH_VALUE_THRESHOLD:,.0f}); "
                f"requires extra review."
            )
            if warnings:
                reasons.append("Open warnings: " + "; ".join(warnings[:3]))
        elif warnings:
            decision, risk = "APPROVE", "low"
            reasons.append("Within stock and under threshold; minor warnings noted, not blocking: "
                           + "; ".join(warnings[:3]))
        else:
            decision, risk = "APPROVE", "low"
            reasons.append(f"Clean invoice ${amount:,.2f} {currency}, under ${HIGH_VALUE_THRESHOLD:,.0f}, "
                           f"all items valid and in stock.")

        return {
            "decision": decision,
            "risk_level": risk,
            "reasons": reasons,
            "reasoning": self._narrative(decision, reasons),
        }

    def reflect(self, role: str, draft: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
        decision = draft.get("decision")
        amount = float(facts.get("amount") or 0.0)
        errors = list(facts.get("errors", []))
        review_flags = list(facts.get("review_flags", []))
        warnings = list(facts.get("warnings", []))
        fraud_signals = list(facts.get("fraud_signals", []))
        concerns: List[str] = []
        revised, note = decision, ""

        if decision == "APPROVE" and (errors or fraud_signals):
            revised = "REJECT"
            concerns.append("First pass approved an invoice that still has blocking issues — correcting to REJECT.")
        elif decision == "REJECT" and not errors and not fraud_signals:
            revised = "APPROVE"
            concerns.append("Rejection rested only on soft warnings; downgrading to APPROVE with note.")
        elif (decision == "ESCALATE" and amount >= HIGH_VALUE_THRESHOLD
              and not errors and not fraud_signals and not warnings and not review_flags):
            revised = "APPROVE"
            concerns.append("High-value but fully clean after review (stock OK, data OK, no fraud) — approving.")
            note = "high-value, reviewed"
        elif decision == "ESCALATE":
            open_items = review_flags or warnings or ["high value / low confidence"]
            concerns.append("Holding for review: open item(s) require sign-off before payment — "
                            + "; ".join(open_items[:2]))

        return {
            "agree": revised == decision,
            "revised_decision": revised,
            "concerns": concerns or ["Draft decision is consistent with the evidence; no change."],
            "note": note,
            "reasoning": "Review: " + (" ".join(concerns) if concerns else "no issues found; decision stands."),
        }

    @staticmethod
    def _narrative(decision, reasons) -> str:
        head = {"APPROVE": "Approving payment.", "REJECT": "Rejecting invoice.",
                "ESCALATE": "Holding for review."}.get(decision, decision)
        return f"{head} " + " ".join(reasons)


# ===========================================================================
# Gemini engine (OpenAI-compatible API)
# ===========================================================================
_APPROVAL_SYS = (
    "You are reviewing a supplier invoice for payment approval at a manufacturing "
    "company. Be strict about fraud and data integrity. Respond ONLY with compact "
    "JSON: {\"decision\": \"APPROVE|REJECT|ESCALATE\", \"risk_level\": "
    "\"low|medium|high\", \"reasons\": [str], \"reasoning\": str}. "
    f"Invoices >= ${HIGH_VALUE_THRESHOLD:,.0f} require extra review. Reject on "
    "unknown items, stock overrun, negative/invalid amounts, or fraud signals."
)
_REFLECT_SYS = (
    "You are double-checking a draft invoice decision. Look for over-lenient "
    "approvals (ignored blockers) and over-harsh rejections (soft warnings only). "
    "Respond ONLY with JSON: {\"agree\": bool, \"revised_decision\": "
    "\"APPROVE|REJECT|ESCALATE\", \"concerns\": [str], \"reasoning\": str}."
)


class GeminiEngine(ReasoningEngine):
    """Google Gemini via its OpenAI-compatible API, with a rule-based fallback."""

    def __init__(self, cfg: dict):
        self.model = cfg["model"]
        self.name = f"Gemini ({cfg['model']})"
        self._fallback = MockReasoningEngine()
        self._last_error = ""
        from openai import OpenAI  # lazy import
        self._client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])

    def _call(self, system: str, user: str) -> Optional[dict]:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — degrade, never crash
            self._last_error = str(exc)
            return None

    def decide_approval(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        out = self._call(_APPROVAL_SYS, json.dumps(facts, default=str))
        if not out or "decision" not in out:
            self.last_used_llm = False
            return self._fallback.decide_approval(facts)
        self.last_used_llm = True
        out.setdefault("risk_level", "medium")
        out.setdefault("reasons", [])
        out.setdefault("reasoning", "")
        return out

    def reflect(self, role: str, draft: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
        out = self._call(_REFLECT_SYS, json.dumps({"draft": draft, "facts": facts}, default=str))
        if not out or "revised_decision" not in out:
            return self._fallback.reflect(role, draft, facts)
        out.setdefault("agree", out["revised_decision"] == draft.get("decision"))
        out.setdefault("concerns", [])
        out.setdefault("reasoning", "")
        out.setdefault("note", "")
        return out


def get_engine(verbose: bool = True) -> ReasoningEngine:
    """Return the Gemini engine when a key is set, else the rule engine."""
    cfg = get_llm_config()

    def log(msg):
        if verbose:
            print(f"[engine] {msg}")

    if not cfg["api_key"]:
        eng = MockReasoningEngine()
        log(f"engine: {eng.name} (no API key set)")
        return eng
    try:
        eng = GeminiEngine(cfg)
        log(f"engine: {eng.name}")
        return eng
    except Exception as exc:  # noqa: BLE001
        log(f"could not init Gemini ({exc}); using rule-based reasoner")
        return MockReasoningEngine()
