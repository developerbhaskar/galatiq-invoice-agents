#!/usr/bin/env python3
"""CLI entry point. The inventory DB is built on first run if missing.

    python main.py --invoice_path=data/invoices/invoice_1001.txt
    python main.py --all            # every sample + summary
    python main.py --invoice_path=... --json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from config import DB_PATH, INVOICES_DIR
from core import logging_utils as log
from core.graph import process_invoice
from llm import get_engine


def _ensure_db() -> None:
    if not os.path.exists(DB_PATH):
        from setup_db import build_db
        build_db()
        print(f"[setup] Built inventory DB at {DB_PATH}", file=sys.stderr)


def _collect(all_flag: bool, invoice_path: str | None) -> list[str]:
    if invoice_path:
        return [invoice_path]
    if all_flag:
        files = sorted(
            f for f in glob.glob(os.path.join(INVOICES_DIR, "*"))
            if not f.endswith(".py")
        )
        return files
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Galatiq multi-agent invoice processor")
    ap.add_argument("--invoice_path", help="path to a single invoice file")
    ap.add_argument("--all", action="store_true", help="process every invoice in data/invoices/")
    ap.add_argument("--json", action="store_true", help="print JSON result(s) instead of panels")
    ap.add_argument("--no-audit", action="store_true", help="do not write audit_logs/*.json")
    ap.add_argument("--quiet-llm", action="store_true", help="suppress engine banner")
    args = ap.parse_args(argv)

    targets = _collect(args.all, args.invoice_path)
    if not targets:
        ap.error("provide --invoice_path=<file> or --all")

    _ensure_db()
    engine = get_engine(verbose=not args.quiet_llm)
    console = log.get_console()

    results = []
    json_out = []
    for path in targets:
        if not os.path.exists(path):
            print(f"[skip] not found: {path}", file=sys.stderr)
            continue
        state, node_path = process_invoice(path, engine, DB_PATH)
        results.append(state)
        if args.json:
            d = state.to_dict()
            d["node_path"] = node_path
            json_out.append(d)
        else:
            log.render_state(state, node_path, console)
        if not args.no_audit:
            log.save_audit(state, node_path)

    if args.json:
        print(json.dumps(json_out if len(json_out) != 1 else json_out[0], indent=2, default=str))
    elif len(results) > 1:
        log.summarize(results, console)

    # exit code reflects whether anything was rejected (useful in CI/automation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
