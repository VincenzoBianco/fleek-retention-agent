"""Central configuration: paths and the decision thresholds.

Every threshold that drives a segment or a play lives here, with a one-line
commercial rationale. Keeping them in one file (not scattered through the code)
means an account manager can retune the book's behaviour without reading Python,
and the interview conversation about "why 50%?" has a single place to point at.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PLAYS_DIR = DATA_DIR / "plays"
OUT_DIR = ROOT / "out"
STATE_DB = DATA_DIR / "state.db"

# Monthly GMV columns, in calendar order. The window is Sep 2025 -> Feb 2026.
MONTH_COLS = ["gmv_sep", "gmv_oct", "gmv_nov", "gmv_dec", "gmv_jan", "gmv_feb"]

# --- Broker reliance -------------------------------------------------------
# An account is "broker-reliant" when a person (the AM) is placing most of its
# orders AND it shows little sign of buying on its own. We read this from
# behaviour, never from the ownership label.
BROKER_RELIANCE_HIGH = 50.0      # >= this % of orders placed by an AM = person is the buyer
BROKER_RELIANCE_LOW = 20.0       # <= this % = effectively self-serving already
SELFSERVE_ACTIVE_DAYS_LOW = 6.0  # < this many active app-days/6mo = not really using the product
SELFSERVE_PDP_LOW = 40.0         # < this many product views/6mo = not browsing on their own

# --- Self-serve growth (headroom) -----------------------------------------
# High intent, low realised spend = money left on the table.
INTENT_PDP_HIGH = 150.0          # heavy browser
INTENT_OFFER_HIGH = 2.0          # actively making offers
GROWTH_GMV_CEILING = 5000.0      # below this 6mo GMV there's room to grow
HANDPICK_ONLY_BUNDLE_SHARE = 25.0  # <= this % bundle spend = basically handpick-only

# --- Health overlays -------------------------------------------------------
# This book is lumpy: 158/300 accounts placed a single order in 6 months, so a
# quiet recent month is normal, not a churn signal. We only flag "dormant" when
# a *material* buyer that spent in the first half has gone silent for a full
# quarter — that's a retention emergency worth an AM's time.
DECLINE_MOMENTUM = -40.0         # last-half vs first-half GMV change % that flags a slide
DORMANT_RECENT_GMV = 1.0         # <= this over the last 3 months = silent
MATERIAL_ACCOUNT_GMV = 2000.0    # a "real" buyer worth flagging on health

# --- Consistency / cleaning -----------------------------------------------
# The provided broker_reliance_pct disagrees with the raw order counts on a
# chunk of the book; flag when the gap is material so we can trust the counts.
RELIANCE_RECONCILE_TOLERANCE = 10.0  # percentage points

# --- Prioritisation --------------------------------------------------------
# We rank the action queue by GMV at stake, so the AM's scarce time (and the
# agent's outreach budget) goes to the accounts that move the number most.
MIN_GMV_FOR_MIGRATION = 2000.0   # don't spend effort migrating tiny brokered accounts
