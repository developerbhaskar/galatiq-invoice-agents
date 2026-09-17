# 🧾 Galatiq — Multi-Agent Invoice Processing

A working prototype that automates end-to-end invoice processing for Acme Corp:
**ingest → validate → approve → pay**, with four cooperating agents, tool use,
structured outputs, and a self-correction (reflection) loop.

> **The business problem.** Acme loses ~$2M/year on manual invoice processing:
> a 30% error rate, 5-day delays, invoices arriving as messy PDFs. This system
> reads any-format invoices, checks them against inventory, applies the
> approval rules, and pays or blocks — in seconds, with every decision recorded.

The approval reasoning runs on **Google Gemini** (via its OpenAI-compatible API).
See [Where the API key goes](#-where-the-api-key-goes).

---

## What it does (on the provided sample set)

Running all 17 distinct sample invoices produces:

| Outcome | Meaning |
|---|---|
| ✅ **Auto-paid** | clean & in-stock, under the $10K bar (or high-value cleared by reflection) |
| ⚠️ **Escalated** | needs a human — stock overrun, unknown item, foreign currency, low confidence |
| ⛔ **Rejected** | fraud signals or blocking data errors — no payment attempted |

Representative decisions:

| Invoice | Format | Amount | Decision | Why |
|---|---|---|---|---|
| INV-1001 | txt | $5,000 | ✅ PAID | clean, in stock, under threshold |
| INV-1002 | txt (typos) | $15,000 | ⚠️ ESCALATED | GadgetX 20 requested > 5 in stock |
| INV-1003 | txt | $100,000 | ⛔ REJECTED | zero-stock "FakeItem" + "pay immediately/wire" + due "yesterday" (fraud score 85) |
| INV-1008 | email txt | $9,900 | ⚠️ ESCALATED | SuperGizmo / MegaSprocket not in inventory |
| INV-1009 | json | −$250 | ⛔ REJECTED | negative quantity, missing vendor |
| INV-1010 | txt | $6,700 | ✅ PAID | duplicate WidgetA lines aggregate to 12 ≤ 15 |
| INV-1012 | **pdf** (OCR noise) | $9,975 | ✅ PAID | "2O26", "Gadget X", "$3,500.O0" all normalized |
| INV-1014 | xml | €4,125 | ⚠️ ESCALATED | foreign currency needs FX sign-off |
| INV-1017 | json | $12,500 | ✅ PAID | high-value but clean → **reflection self-corrects** ESCALATE → APPROVE |

`INV-1017` is an added test case that exercises the reflection loop end-to-end.

---

## Architecture

A typed shared `InvoiceState` flows through a small **state graph** (same model
as LangGraph — typed state, named nodes, conditional edges — implemented in
~80 dependency-free lines in `core/graph.py`, so a reviewer can run it anywhere).

```
                ┌───────────┐   ┌────────────┐   ┌───────────┐
  invoice ─────▶│ Ingestion │──▶│ Validation │──▶│ Approval  │
  (any format)  └───────────┘   └────────────┘   └─────┬─────┘
                 extract +        SQLite inventory      │ route on decision
                 self-correct     + fraud + math        │
                                                        ▼
                        APPROVE → pay   ┌────────────────────────┐
                        ESCALATE → hold │        Payment         │
                        REJECT → log    └────────────────────────┘
```

### The four agents

1. **Ingestion** (`agents/ingestion.py`) — parses TXT / JSON / CSV / XML / PDF
   into structured fields (vendor, amount, line items, due date). Handles typos
   (`INVOCE`, `Vndr`, `Itms`), OCR artifacts (`2O26`), and header-less tables.
   Scores its own **confidence** and runs a **self-correction pass** that
   recovers missing line amounts / subtotal / total instead of failing.

2. **Validation** (`agents/validation.py`) — checks each item against a mock
   **SQLite** inventory (`inventory.db`). Sorts findings into *errors* (block →
   reject), *review flags* (→ escalate), and *warnings* (soft). Aggregates
   duplicate line items before the stock check, validates arithmetic, and scores
   **fraud** from independent signals (zero-stock trap item, urgent/wire
   language, impossible due dates, negatives).

3. **Approval** (`agents/approval.py`) — the approval step. Asks the reasoning
   engine for a first-pass decision (invoices ≥ **$10K** get extra scrutiny),
   then runs a **reflection/critique loop** that can overturn it — e.g. clearing
   a high-value-but-clean invoice, or catching an approval that ignored a
   blocker. Both passes are recorded.

4. **Payment** (`agents/payment.py`) — on APPROVE calls the mock payment tool and
   stores the reference; on ESCALATE parks it for a human; on REJECT logs the
   rejection with full reasoning. No payment is ever attempted on a non-approval.

Every run writes a JSON **audit trail** to `audit_logs/` — the compliance record
a finance team needs.

---

## Quickstart

```bash
# 1. install (core deps are light; the dashboard adds streamlit/pandas)
pip install -r requirements.txt

# 2. build the mock inventory database (once)
python setup_db.py

# 3a. process one invoice
python main.py --invoice_path=data/invoices/invoice_1003.txt

# 3b. process everything + a business-impact summary
python main.py --all

# 3c. machine-readable output
python main.py --invoice_path=data/invoices/invoice_1001.txt --json
```

### Visual dashboard (recommended)

```bash
streamlit run app.py
```

Pick or upload an invoice, watch it flow through the four agents, and see the
extracted data, inventory checks, fraud signals, the approval reasoning + self-review,
and the payment result. **Process ALL samples** renders the business-impact view
(auto-paid vs escalated vs rejected, and dollars held from auto-pay).

For a full plain-English walkthrough of the system and every dashboard control,
see **`docs/Galatiq-Explainer.pdf`**.

### Shareable HTML report (no server needed)

```bash
python report.py   # writes report_output/report.html — open in any browser
```

---

## 🔑 Where the API key goes

The reasoning runs on **Google Gemini**. You only add your key. Do **either**:

**Option A — edit `config.py`:** paste your key on **line 7**:

```python
GEMINI_API_KEY = "AQ.Ab8...your-key"
GEMINI_MODEL = "gemini-3.5-flash"
```

**Option B — environment variable (edit nothing):**

```bash
export GEMINI_API_KEY="AQ.Ab8...your-key"
```

Install the client once: `pip install openai` (Gemini exposes an OpenAI-compatible
API). The endpoint is set in `config.py` → `GEMINI_BASE_URL`, and the call lives
in `llm/engine.py` (`GeminiEngine`). See `GEMINI_API_KEY_SETUP.md` for details.

**Resilient:** if the key is missing or a call fails, it falls back to
deterministic rules so a run never breaks.

---

## Design decisions & scope (shipping mindset)

- **Deterministic default over API dependency.** A graded prototype must run on
  the reviewer's machine with zero setup. The offline engine encodes the same
  approval judgment as rules; the LLM is a drop-in upgrade, not a requirement.
- **Three outcomes, not two.** `APPROVE / ESCALATE / REJECT` mirrors real AP:
  most exceptions want a human, not an outright rejection. Only fraud and bad
  data hard-reject.
- **Errors vs. flags vs. warnings.** Stock overruns and unknown items *escalate*
  (a vendor typo shouldn't be treated as fraud); zero-stock traps, negatives and
  urgent-wire language *reject*; price/math/currency notes are *soft*.
- **Custom graph over a heavy framework.** LangGraph/CrewAI would add install
  friction and a network surface for little gain at this size. The orchestrator
  matches LangGraph's API, so migrating later is mechanical.
- **Cut for now:** real OCR on scanned images (we rely on `pdfplumber` text; the
  sample PDFs are text-based), multi-currency FX rates (we escalate instead),
  and a persistent queue/DB for state (audit JSON is enough for an MVP).

---

## Testing

```bash
pytest -q
```

27 tests cover parser robustness (every format + OCR/typo cases), each
validation rule, the approval + reflection logic, and an **end-to-end assertion
of the expected outcome for all 17 sample invoices**.

---

## Project structure

```
galatiq-invoice-agents/
├── config.py             # ← all config incl. the API-key location (line 9)
├── setup_db.py           # builds inventory.db (SQLite)
├── main.py               # CLI entry point
├── app.py                # Streamlit dashboard (dark, block UI)
├── report.py             # static HTML report generator
├── requirements.txt
├── docs/
│   └── Galatiq-Explainer.pdf   # full walkthrough : what every part does
├── .streamlit/
│   └── config.toml       # dashboard dark theme
├── agents/               # the four agents
│   ├── ingestion.py  validation.py  approval.py  payment.py
├── core/
│   ├── state.py          # typed InvoiceState (shared graph state)
│   ├── parsers.py        # TXT/JSON/CSV/XML/PDF parsing + normalization
│   ├── graph.py          # LangGraph-style state-graph orchestrator
│   ├── tools.py          # mock payment API (function-calling target)
│   └── logging_utils.py  # rich console + JSON audit trail
├── llm/
│   └── engine.py         # Gemini engine + rule-based fallback
├── tests/                # pytest suite (27 tests)
└── data/
    └── invoices/         # sample invoices (+ INV-1017 added test case)
```

## Above & beyond

- Pluggable LLM layer with three real providers **and** an always-on offline
  fallback; one documented key location.
- A visible **self-correction loop** (INV-1017: ESCALATE → APPROVE).
- Streamlit dashboard **and** a self-contained HTML report for stakeholders.
- Fraud scoring from independent weighted signals, duplicate-line aggregation,
  arithmetic consistency checks, foreign-currency handling, per-invoice audit
  JSON, and clean/JSON CLI output for automation.
