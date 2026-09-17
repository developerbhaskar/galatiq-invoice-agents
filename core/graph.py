"""Lightweight state-graph orchestrator (LangGraph-style, zero heavy deps).

Typed shared state, named nodes, conditional edges. Flow:
    ingestion : validation : approval : (route) : payment : END
Route on the approval decision: APPROVE : pay, ESCALATE : hold, REJECT : reject.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from core.state import InvoiceState

END = "__end__"
NodeFn = Callable[[InvoiceState], InvoiceState]
Router = Callable[[InvoiceState], str]


class StateGraph:
    def __init__(self):
        self.nodes: Dict[str, NodeFn] = {}
        self.edges: Dict[str, str] = {}
        self.conditional: Dict[str, Tuple[Router, Dict[str, str]]] = {}
        self.entry: Optional[str] = None

    def add_node(self, name: str, fn: NodeFn) -> "StateGraph":
        self.nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self.edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router: Router, mapping: Dict[str, str]) -> "StateGraph":
        self.conditional[src] = (router, mapping)
        return self

    def set_entry(self, name: str) -> "StateGraph":
        self.entry = name
        return self

    def run(self, state: InvoiceState, max_steps: int = 50) -> Tuple[InvoiceState, List[str]]:
        if not self.entry:
            raise ValueError("graph has no entry node")
        path: List[str] = []
        current = self.entry
        steps = 0
        while current != END and steps < max_steps:
            steps += 1
            path.append(current)
            state = self.nodes[current](state)
            if current in self.conditional:
                router, mapping = self.conditional[current]
                branch = router(state)
                current = mapping.get(branch, END)
            else:
                current = self.edges.get(current, END)
        return state, path


# ---------------------------------------------------------------------------
# Pipeline assembly
# ---------------------------------------------------------------------------
def _route_after_approval(state: InvoiceState) -> str:
    return {"APPROVE": "pay", "ESCALATE": "hold", "REJECT": "reject"}.get(state.decision, "reject")


def build_pipeline(engine, db_path: Optional[str] = None) -> StateGraph:
    from agents import IngestionAgent, ValidationAgent, ApprovalAgent, PaymentAgent
    from config import DB_PATH

    ingestion = IngestionAgent(engine)
    validation = ValidationAgent(engine, db_path or DB_PATH)
    approval = ApprovalAgent(engine)
    payment = PaymentAgent(engine)

    g = StateGraph()
    g.add_node("ingestion", ingestion.run)
    g.add_node("validation", validation.run)
    g.add_node("approval", approval.run)
    g.add_node("payment", payment.run)

    g.set_entry("ingestion")
    g.add_edge("ingestion", "validation")
    g.add_edge("validation", "approval")
    g.add_conditional_edges(
        "approval",
        _route_after_approval,
        {"pay": "payment", "hold": "payment", "reject": "payment"},
    )
    g.add_edge("payment", END)
    return g


def process_invoice(path: str, engine, db_path: Optional[str] = None) -> Tuple[InvoiceState, List[str]]:
    """Run one invoice through the full pipeline. Returns (final_state, node_path)."""
    graph = build_pipeline(engine, db_path)
    state = InvoiceState(source_path=path)
    return graph.run(state)
