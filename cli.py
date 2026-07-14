#!/usr/bin/env python3
"""Fleek Retention Agent — command line.

    python cli.py run    <workbook.xlsx> [--sheet Accounts] [--llm] [--out out]
    python cli.py status
    python cli.py reset

`run` is the morning job: ingest a tab, update the book idempotently, write the
action queue (CSV/JSON/HTML). Point it at the same file twice and the second run
skips everything; point it at the new_accounts tab and only new/changed accounts
are touched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from retention_agent import config
from retention_agent.orchestrator import run as run_loop
from retention_agent.report import write_all
from retention_agent.store import Store


def _run(args):
    store = Store(args.db)
    report = run_loop(args.workbook, args.sheet, store, use_llm=args.llm)
    out = write_all(store, report, Path(args.out))
    print(f"Run #{report.run_id}  ({report.source})")
    print(f"  seen={report.n_seen}  new={report.n_new}  changed={report.n_changed}  "
          f"unchanged/skipped={report.n_unchanged_skipped}")
    print(f"  actions queued={report.n_actions}  expected value (risk-adj)=£{report.expected_value_total:,.0f}")
    print(f"  plays: {report.play_counts}")
    print(f"  wrote: {out['csv']}  {out['html']}")
    store.close()


def _calibrate(args):
    """Print the empirical priors derived from the workbook (the anchors behind
    the growth uplift numbers in config)."""
    import json
    from retention_agent.analysis import calibrate
    from retention_agent.ingest import load_accounts
    accts = load_accounts(args.workbook, args.sheet)
    print(json.dumps(calibrate(accts), indent=2))


def _outcome(args):
    """Log the outcome of an action (feeds the learning loop)."""
    store = Store(args.db)
    row = next((r for r in store.all_accounts() if r["account_id"] == args.account_id), None)
    if not row:
        print(f"unknown account {args.account_id}"); store.close(); return
    store.record_outcome(args.account_id, row["decided_run"], row["play"], row["feature"],
                         sent=True, responded=args.responded, converted=args.converted,
                         gmv_delta=args.gmv_delta, ts=args.ts or "")
    print(f"logged outcome for {args.account_id}: responded={args.responded} converted={args.converted}")
    print("realized rates so far:", store.realized_rates())
    store.close()


def _status(args):
    store = Store(args.db)
    print("counts:", store.counts())
    print("segments:", store.segment_counts())
    print("plays:", store.play_counts())
    print("runs:")
    for r in store.runs():
        print(f"  #{r['run_id']} {r['source']}  new={r['n_new']} changed={r['n_changed']} "
              f"unchanged={r['n_unchanged']} actions={r['n_actions']}")
    store.close()


def _reset(args):
    p = Path(args.db)
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(p) + suffix)
        if f.exists():
            f.unlink()
    print(f"cleared {p}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cli.py")
    ap.add_argument("--db", default=str(config.STATE_DB), help="state db path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="ingest a tab and update the book")
    r.add_argument("workbook")
    r.add_argument("--sheet", default="Accounts")
    r.add_argument("--llm", action="store_true", help="use Claude to draft (needs ANTHROPIC_API_KEY)")
    r.add_argument("--out", default=str(config.OUT_DIR))
    r.set_defaults(fn=_run)

    s = sub.add_parser("status", help="show current book state and run history")
    s.set_defaults(fn=_status)

    c = sub.add_parser("calibrate", help="print empirical priors derived from the book")
    c.add_argument("workbook")
    c.add_argument("--sheet", default="Accounts")
    c.set_defaults(fn=_calibrate)

    o = sub.add_parser("outcome", help="log an action's outcome (feeds the learning loop)")
    o.add_argument("account_id")
    o.add_argument("--responded", action="store_true")
    o.add_argument("--converted", action="store_true")
    o.add_argument("--gmv-delta", type=float, default=0.0, dest="gmv_delta")
    o.add_argument("--ts", default="")
    o.set_defaults(fn=_outcome)

    x = sub.add_parser("reset", help="wipe persisted state")
    x.set_defaults(fn=_reset)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
