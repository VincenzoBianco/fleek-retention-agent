"""CSV export of the action queue.

The tool has no static HTML output — the live app (server/app.py) is the UI, and
the SQLite store is the source of truth. This module just turns the current
action queue into CSV text (an AM's working list), which the app serves as a
download and the CLI can optionally write to a file with `--export`.
"""
from __future__ import annotations

import csv
import io

from .store import Store

_FIELDS = ["account_id", "ownership", "region", "persona", "transaction_mode",
           "gmv_total", "segment", "health", "play", "feature", "channel",
           "expected_value", "prize_type", "gmv_at_stake", "action", "reason", "draft"]


def action_queue_csv(store: Store) -> str:
    """Return the ranked action queue as CSV text."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_FIELDS, extrasaction="ignore")
    w.writeheader()
    for row in store.action_queue():
        w.writerow(row)
    return buf.getvalue()
