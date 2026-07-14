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
    print(f"  actions queued={report.n_actions}  GMV at stake=£{report.gmv_at_stake_total:,.0f}")
    print(f"  plays: {report.play_counts}")
    print(f"  wrote: {out['csv']}  {out['html']}")
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

    x = sub.add_parser("reset", help="wipe persisted state")
    x.set_defaults(fn=_reset)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
