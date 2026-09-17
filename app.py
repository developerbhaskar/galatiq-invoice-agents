"""Streamlit dashboard for the Galatiq invoice-processing agents.

    streamlit run app.py

Pick or upload an invoice to run it through the four agents, or run the whole
batch and drill into any outcome.
"""

from __future__ import annotations

import os
import glob
import tempfile

import pandas as pd
import streamlit as st

from config import DB_PATH, INVOICES_DIR, HIGH_VALUE_THRESHOLD
from core.graph import process_invoice
from core import view
from llm import get_engine

st.set_page_config(page_title="Galatiq Invoice Agents", page_icon="🧾", layout="wide")

# ── styling ─────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&display=swap');
      html, body, [class*="css"], .stMarkdown, p, span, div { font-family:'Inter',system-ui,sans-serif; }
      h1,h2,h3,h4,.mono{ font-family:'Space Grotesk',sans-serif !important; letter-spacing:-.01em; color:#0f172a; }
      .block-container{ padding-top:2rem; }
      .banner{background:#ffffff;border:1px solid #e5e7eb;border-top:3px solid #2563eb;
              border-radius:14px;padding:20px 24px;margin-bottom:16px;
              box-shadow:0 1px 3px rgba(15,23,42,.06)}
      .banner h1{margin:0;font-size:1.5rem;color:#0f172a}
      .banner p{margin:6px 0 0;color:#6b7280;font-size:.85rem}
      [data-testid="stMetric"]{background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;
              padding:14px 16px;box-shadow:0 1px 3px rgba(15,23,42,.05)}
      [data-testid="stMetricValue"]{font-family:'Space Grotesk',sans-serif;font-weight:700;color:#0f172a}
      [data-testid="stVerticalBlockBorderWrapper"]{background:#ffffff;border-radius:12px;
              box-shadow:0 1px 3px rgba(15,23,42,.05)}
      .stage{display:inline-block;padding:6px 13px;border-radius:20px;margin:2px;font-weight:600;font-size:.8rem;color:#fff}
      .s-done{background:#94a3b8}.s-pay{background:#16a34a}.s-hold{background:#f59e0b}
      .s-rej{background:#dc2626}.s-idle{background:#e5e7eb;color:#64748b}
      .pill{display:inline-block;padding:4px 12px;border-radius:20px;font-weight:700;font-size:.8rem}
      .p-PAID{color:#16a34a;border:1px solid #16a34a;background:#f0fdf4}
      .p-ESCALATED{color:#b45309;border:1px solid #f59e0b;background:#fff7ed}
      .p-REJECTED{color:#dc2626;border:1px solid #dc2626;background:#fef2f2}
      .stButton>button{border-radius:8px;font-weight:600;border:1px solid #d1d5db;background:#ffffff;color:#111827}
      .stButton>button[kind="primary"]{background:#2563eb;border-color:#2563eb;color:#ffffff}
    </style>
    """,
    unsafe_allow_html=True,
)

EMOJI = view.STATUS_EMOJI
pill = view.pill_html
stage_chips = view.stage_chips_html


@st.cache_resource
def _engine():
    return get_engine(verbose=False)


def _ensure_db():
    if not os.path.exists(DB_PATH):
        from setup_db import build_db
        build_db()


def _init():
    st.session_state.setdefault("view", "home")
    st.session_state.setdefault("batch", None)
    st.session_state.setdefault("single", None)
    st.session_state.setdefault("flt", None)
    st.session_state.setdefault("single_from", "home")


_ensure_db()
_init()
engine = _engine()


def process_path(path):
    return process_invoice(path, engine, DB_PATH)


def dedup(results):
    """Keep one entry per invoice number for a clean batch summary."""
    seen, out = set(), []
    for s, p in results:
        key = s.invoice_number or s.source_path
        if key in seen:
            continue
        seen.add(key)
        out.append((s, p))
    return out


# ── header ──────────────────────────────────────────────────────────────────
st.markdown(
    "<div class='banner'><h1>🧾 Galatiq : Invoice Processing</h1>"
    "<p>Read any-format invoice, check it against inventory, approve it, and pay it — with every decision recorded.</p></div>",
    unsafe_allow_html=True,
)

with st.expander("ℹ️  How this works", expanded=False):
    st.markdown(
        """
        Each invoice goes through four stages:

        1. **Ingestion** : reads the file (TXT / JSON / CSV / XML / PDF) and pulls out the vendor, amount, line items
           and due date — fixing typos and scan noise, and scoring how confident it is in the data.
        2. **Validation** : checks each item against the inventory database, flags stock shortfalls and unknown
           items, and scores risk from signals like a zero-stock item or urgent "wire transfer" wording.
        3. **Approval** : applies the approval rules (invoices **≥ $10,000** get extra review), then a second
           review pass double-checks the decision and can revise it.
        4. **Payment** : approved invoices are paid; the rest are held for review or rejected.

        **Outcomes:** ✅ **PAID** · ⚠️ **ESCALATED** (flagged for review) · ⛔ **REJECTED** (fraud or bad data).
        """
    )

# ── sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Controls")
    files = sorted(f for f in glob.glob(os.path.join(INVOICES_DIR, "*")) if not f.endswith(".py"))
    labels = [os.path.basename(f) for f in files]
    choice = st.selectbox("Sample invoice", labels, index=0 if labels else None,
                          help="One of the provided test invoices — clean, messy, and fraudulent ones.")
    uploaded = st.file_uploader("…or upload your own", type=["txt", "json", "csv", "xml", "pdf"],
                                help="Drop in any invoice file; the agents parse it the same way.")
    run_one = st.button("▶  Process invoice", use_container_width=True, type="primary")
    run_all = st.button("⚙  Process all samples", use_container_width=True)

    with st.expander("What do these do?"):
        st.markdown(
            "- **Sample invoice** : pick a provided test file.\n"
            "- **Upload** : use your own invoice instead.\n"
            "- **Process invoice** : run the selected/uploaded one.\n"
            "- **Process all samples** : run everything, then drill into any outcome."
        )

# ── button handlers (drive the view) ────────────────────────────────────────
if run_one:
    if uploaded is not None:
        suffix = os.path.splitext(uploaded.name)[1]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(uploaded.getbuffer()); tmp.close()
        target = tmp.name
    else:
        target = os.path.join(INVOICES_DIR, choice) if choice else None
    if target:
        st.session_state.single = process_path(target)
        st.session_state.single_from = "home"
        st.session_state.view = "single"
        st.rerun()

if run_all:
    results = [process_path(f) for f in files]
    st.session_state.batch = dedup(results)
    st.session_state.flt = None
    st.session_state.view = "batch"
    st.rerun()


# ── renderers ───────────────────────────────────────────────────────────────
def render_single(state, node_path, back_to):
    if st.button("←  Back" + (" to summary" if back_to == "batch" else ""), key="back"):
        st.session_state.view = back_to if back_to == "batch" else "home"
        st.rerun()

    st.subheader(f"{state.invoice_number or '?'} : {state.vendor or 'Unknown vendor'}")
    st.markdown(stage_chips(state), unsafe_allow_html=True)

    top = st.columns(4)
    top[0].markdown("**Decision**<br>" + pill(state.final_status or "?"), unsafe_allow_html=True)
    top[1].metric(f"Amount · {state.currency}", f"{(state.amount or 0):,.0f}")
    top[2].metric("Confidence", f"{state.extraction_confidence:.0%}")
    top[3].metric("Fraud score", state.fraud_score)

    a, b = st.columns(2)
    with a:
        with st.container(border=True):
            st.markdown("#### 1 · Ingestion")
            st.caption("Details read from the file (typos and scan noise fixed).")
            st.json(view.extracted_fields(state), expanded=False)
            if state.line_items:
                st.dataframe(pd.DataFrame([li.to_dict() for li in state.line_items]),
                             use_container_width=True, hide_index=True)
            if state.extraction_issues:
                st.warning("Ingestion notes : " + "; ".join(state.extraction_issues))
    with b:
        with st.container(border=True):
            st.markdown("#### 2 · Validation")
            st.caption("Checked against inventory. Errors block; flags go to review; warnings are soft.")
            if state.inventory_checks:
                st.dataframe(pd.DataFrame(state.inventory_checks), use_container_width=True, hide_index=True)
            if state.errors:
                st.error("**Blocking errors**\n\n- " + "\n- ".join(state.errors))
            if state.review_flags:
                st.warning("**Flagged for review**\n\n- " + "\n- ".join(state.review_flags))
            if state.fraud_signals:
                st.error(f"**Fraud signals (score {state.fraud_score})**\n\n- " + "\n- ".join(state.fraud_signals))
            if state.warnings:
                st.info("**Warnings**\n\n- " + "\n- ".join(state.warnings))
            if not (state.errors or state.review_flags or state.fraud_signals or state.warnings):
                st.success("No validation issues.")

    with st.container(border=True):
        st.markdown("#### 3 · Approval")
        st.caption("The decision, with a second review pass that can revise the first call.")
        src = state.reasoning_source or "rule-based reasoner"
        src_disp = src[:1].upper() + src[1:]
        st.markdown(
            f"<span style='background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;"
            f"padding:3px 11px;border-radius:14px;font-size:.78rem;font-weight:700'>"
            f"🧠 Reasoned by {src_disp}</span>",
            unsafe_allow_html=True,
        )
        st.write("")
        ref = state.reflection or {}
        if ref.get("draft_decision") != ref.get("final_decision"):
            st.markdown(f"🔁 **Self-corrected :** `{ref.get('draft_decision')}` → `{ref.get('final_decision')}`")
        st.write(state.approval_reasoning)

    with st.container(border=True):
        st.markdown("#### 4 · Payment")
        st.caption("Approved invoices are paid via the payment API; others are held or logged.")
        if state.final_status == "PAID":
            st.success(state.payment_message)
        elif state.final_status == "ESCALATED":
            st.warning(state.payment_message)
        else:
            st.error(state.payment_message)


def render_batch(rows):
    m = view.batch_metrics([s for s, _ in rows])
    st.subheader("Batch overview")

    cols = st.columns(5)
    cols[0].metric("Invoices", m["total"])
    cols[1].metric("✅ Paid", m["paid"])
    cols[2].metric("⚠️ Escalated", m["escalated"])
    cols[3].metric("⛔ Rejected", m["rejected"])
    held = m["held"]
    held_disp = f"${held/1000:,.0f}K" if held >= 1000 else f"${held:,.0f}"
    cols[4].metric("Held from auto-pay", held_disp)

    # per-outcome drill-in buttons, aligned under the blocks
    b = st.columns(5)
    b[0].caption("full list ↓")
    if b[1].button("View paid", use_container_width=True, key="f_paid"):
        st.session_state.flt = "PAID"; st.rerun()
    if b[2].button("View escalated", use_container_width=True, key="f_esc"):
        st.session_state.flt = "ESCALATED"; st.rerun()
    if b[3].button("View rejected", use_container_width=True, key="f_rej"):
        st.session_state.flt = "REJECTED"; st.rerun()
    b[4].caption("")

    st.divider()
    flt = st.session_state.get("flt")

    if flt:
        subset = [(s, p) for s, p in rows if s.final_status == flt]
        head = st.columns([3, 1])
        head[0].markdown(f"### {EMOJI.get(flt,'')} {flt} — {len(subset)} invoice(s)")
        if head[1].button("←  All outcomes", use_container_width=True, key="clear_flt"):
            st.session_state.flt = None; st.rerun()
        st.caption("Click **Open** on any invoice to see its full breakdown.")
        _invoice_rows(subset, keyp="flt")
    else:
        st.markdown("### All invoices")
        st.caption("Click an outcome block above to drill in, or open any invoice below.")
        _invoice_rows(rows, keyp="all")
        st.divider()
        dist = pd.DataFrame({"outcome": ["PAID", "ESCALATED", "REJECTED"],
                             "count": [m["paid"], m["escalated"], m["rejected"]]}).set_index("outcome")
        st.bar_chart(dist, color="#10b981")


def _invoice_rows(rows, keyp):
    for i, (s, p) in enumerate(rows):
        with st.container(border=True):
            c = st.columns([2.4, 1.3, 1.3, 1])
            c[0].markdown(f"**{s.invoice_number}**  ·  {s.vendor or 'Unknown'}  \n<span style='color:#64748b;font-size:.8rem'>{s.source_format.upper()}</span>", unsafe_allow_html=True)
            c[1].markdown(f"{(s.amount or 0):,.0f} {s.currency}")
            c[2].markdown(pill(s.final_status or "?"), unsafe_allow_html=True)
            if c[3].button("Open →", key=f"open_{keyp}_{i}", use_container_width=True):
                st.session_state.single = (s, p)
                st.session_state.single_from = "batch"
                st.session_state.view = "single"
                st.rerun()


# ── route ───────────────────────────────────────────────────────────────────
v = st.session_state.view
if v == "single" and st.session_state.single:
    render_single(*st.session_state.single, st.session_state.single_from)
elif v == "batch" and st.session_state.batch:
    render_batch(st.session_state.batch)
else:
    st.info("👈 Pick a sample invoice and hit **Process invoice**, or **Process all samples** for the overview.")
