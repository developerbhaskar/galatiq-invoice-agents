"""Generate a self-contained HTML report from a full batch run.

    python report.py     # writes report_output/report.html
"""

from __future__ import annotations

import glob
import html
import os
from datetime import datetime

from config import INVOICES_DIR, DB_PATH, HIGH_VALUE_THRESHOLD
from core.graph import process_invoice
from llm import get_engine

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_output")

COLOR = {"PAID": "#10b981", "ESCALATED": "#f59e0b", "REJECTED": "#f43f5e"}
EMOJI = {"PAID": "✓", "ESCALATED": "!", "REJECTED": "✕"}


def _ensure_db():
    if not os.path.exists(DB_PATH):
        from setup_db import build_db
        build_db()


def run_batch():
    _ensure_db()
    engine = get_engine(verbose=False)
    files = sorted(f for f in glob.glob(os.path.join(INVOICES_DIR, "*")) if not f.endswith(".py"))
    return [process_invoice(f, engine, DB_PATH)[0] for f in files], getattr(engine, "name", "?")


def _flags(s):
    out = []
    for e in s.errors:
        out.append(("err", e))
    for f in s.fraud_signals:
        out.append(("err", f))
    for r in s.review_flags:
        out.append(("warn", r))
    return out[:3]


def _card(s) -> str:
    color = COLOR.get(s.final_status, "#94a3b8")
    flags = "".join(
        f"<div class='flag {c}'>{html.escape(t)}</div>" for c, t in _flags(s)
    ) or "<div class='flag ok'>No issues found</div>"

    ref = s.reflection or {}
    reflect = ""
    if ref.get("draft_decision") != ref.get("final_decision"):
        reflect = (
            f"<div class='reflect'>self&#8202;corrected&#8202;: "
            f"{ref.get('draft_decision')} &#8594; {ref.get('final_decision')}</div>"
        )
    items = ", ".join(f"{li.item} &times;{int(li.quantity) if li.quantity else '?'}" for li in s.line_items[:4])

    return f"""
    <article class='card' style='--c:{color}'>
      <header>
        <span class='inv'>{html.escape(str(s.invoice_number))}</span>
        <span class='status'>{EMOJI.get(s.final_status,'')} {s.final_status}</span>
      </header>
      <div class='vendor'>{html.escape(str(s.vendor or 'Unknown vendor'))}</div>
      <div class='amount'>{(s.amount or 0):,.0f} <span>{s.currency}</span></div>
      <div class='meta'>{s.source_format.upper()} : {s.extraction_confidence:.0%} confidence : fraud {s.fraud_score}</div>
      <div class='items'>{items}</div>
      {flags}
      {reflect}
    </article>"""


def build_html(results, engine_name) -> str:
    paid = [r for r in results if r.final_status == "PAID"]
    esc = [r for r in results if r.final_status == "ESCALATED"]
    rej = [r for r in results if r.final_status == "REJECTED"]
    paid_usd = sum(r.amount or 0 for r in paid if r.currency == "USD")
    held = sum(r.amount or 0 for r in results if r.final_status != "PAID")

    def metric(label, value, color="#e2e8f0", data=""):
        return (
            f"<div class='metric'><div class='mv' style='color:{color}' {data}>{value}</div>"
            f"<div class='ml'>{label}</div></div>"
        )

    cards = "".join(_card(s) for s in results)
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Galatiq Report</title>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>
<link href='https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&display=swap' rel='stylesheet'>
<style>
  :root{{color-scheme:dark}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:'Inter',system-ui,sans-serif;background:#0b1120;color:#e2e8f0}}
  .hero{{padding:48px 24px 32px;background:radial-gradient(120% 120% at 0% 0%,#1e293b,#0b1120);
         border-bottom:1px solid #1e293b}}
  .hero h1{{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:1.8rem;margin:0;letter-spacing:-.02em}}
  .hero .sub{{color:#64748b;font-size:.85rem;margin-top:8px}}
  .wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 56px}}
  .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:32px}}
  .metric{{background:#111a2e;border:1px solid #1e293b;border-radius:16px;padding:20px}}
  .mv{{font-family:'Space Grotesk',sans-serif;font-size:2rem;font-weight:700;line-height:1}}
  .ml{{color:#64748b;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin-top:8px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
  .card{{background:#111a2e;border:1px solid #1e293b;border-left:3px solid var(--c);border-radius:16px;
         padding:18px;transition:transform .18s ease,border-color .18s ease}}
  .card:hover{{transform:translateY(-4px);border-color:var(--c)}}
  .card header{{display:flex;justify-content:space-between;align-items:center}}
  .inv{{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:1.05rem}}
  .status{{font-size:.72rem;font-weight:600;color:var(--c);border:1px solid var(--c);
           border-radius:20px;padding:3px 10px}}
  .vendor{{color:#cbd5e1;font-weight:500;margin-top:6px}}
  .amount{{font-family:'Space Grotesk',sans-serif;font-size:1.6rem;font-weight:700;margin:6px 0 2px}}
  .amount span{{font-size:.8rem;color:#64748b;font-weight:500}}
  .meta{{color:#64748b;font-size:.75rem;margin-bottom:12px}}
  .items{{color:#94a3b8;font-size:.8rem;margin-bottom:12px}}
  .flag{{font-size:.78rem;padding:6px 10px;border-radius:8px;margin-top:6px;line-height:1.35}}
  .flag.err{{background:rgba(244,63,94,.12);color:#fda4af}}
  .flag.warn{{background:rgba(245,158,11,.12);color:#fcd34d}}
  .flag.ok{{background:rgba(16,185,129,.1);color:#6ee7b7}}
  .reflect{{margin-top:10px;font-size:.75rem;color:#c4b5fd;font-weight:500}}
  @keyframes rise{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:none}}}}
  .card,.metric{{animation:rise .4s ease both}}
</style></head><body>
<div class='hero'>
  <h1>Galatiq : Invoice Processing Report</h1>
  <div class='sub'>{datetime.now():%d %b %Y, %H:%M} : engine {html.escape(engine_name)} : threshold ${HIGH_VALUE_THRESHOLD:,.0f}</div>
</div>
<div class='wrap'>
  <div class='metrics'>
    {metric('Processed', len(results))}
    {metric('Auto&#8202;paid', len(paid), '#10b981')}
    {metric('Escalated', len(esc), '#f59e0b')}
    {metric('Rejected', len(rej), '#f43f5e')}
    {metric('Paid (USD)', f'${paid_usd:,.0f}', '#10b981')}
    {metric('Held', f'${held:,.0f}', '#f43f5e')}
  </div>
  <div class='grid'>{cards}</div>
</div></body></html>"""


def main():
    results, engine_name = run_batch()
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "report.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build_html(results, engine_name))
    print(f"[report] wrote {out} ({len(results)} invoices)")
    return out


if __name__ == "__main__":
    main()
