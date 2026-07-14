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
# quiet recent month is normal, not a churn signal. Two gates before we cry
# "churn": the account must be *material* (real GMV) AND have shown an actual
# ordering *rhythm* that then stopped. Materiality alone is not enough — a big
# intermittent buyer (e.g. two large orders then a gap) clears a GMV gate but
# isn't churning, it's just lumpy. Requiring a cadence separates the two.
DECLINE_MOMENTUM = -40.0         # last-half vs first-half GMV change % that flags a slide
DORMANT_RECENT_GMV = 1.0         # <= this over the last 3 months = silent
MATERIAL_ACCOUNT_GMV = 2000.0    # a "real" buyer worth flagging on health
RHYTHM_MIN_ORDERS = 4            # ...and an established cadence: this many orders
RHYTHM_MIN_ACTIVE_MONTHS = 3     # ...across at least this many distinct months
# ...and it must NOT have bounced back: if the latest month already recovered to
# this fraction of the earlier run-rate, the mid-window dip was noise, not a
# slide. Without this, an account that dipped then rebounded in Feb still trips
# the momentum gate and (worse) tops the queue.
RECOVERY_FRACTION = 0.6

# --- Consistency / cleaning -----------------------------------------------
# The provided broker_reliance_pct disagrees with the raw order counts on a
# chunk of the book; flag when the gap is material so we can trust the counts.
RELIANCE_RECONCILE_TOLERANCE = 10.0  # percentage points

# --- Feature-nudge selection (grow self-serve) -----------------------------
# Which feature to push is a decision tree over behaviour. Thresholds here; the
# tree is in plays.choose_feature().
VIDEO_OFFER_MIN = 3.0            # making this many offers but stalling = wants a call to close
CHAT_VIEWS_MIN = 150.0          # heavy browser...
CHAT_THREADS_MAX = 2.0          # ...who isn't talking to us yet = open a chat
# Handpick buyers are the HIGHER-value cohort in this book (median AOV £682 vs
# £281 for bundle-led buyers), so we do NOT push a valuable handpick buyer onto
# generic bundles — that would dilute their AOV. Above this AOV, a handpick-led
# buyer is nudged to build-a-bundle (scale volume, keep curation); below it,
# to bundles (a volume play for the price-led).
HANDPICK_HIGH_AOV = 500.0

# Growth uplift, expressed as expected % gain on the account's current GMV.
# ANCHORED IN THIS BOOK, not invented: self-serve accounts that engage (chat>=5
# or a video call) have ~2.0x the median GMV of unengaged ones (£493 vs £242 —
# see analysis.calibrate / `python cli.py calibrate`). That +100% is the ceiling
# of the engagement gap; each feature is assumed to capture a conservative slice
# of it. These are correlational priors to size the prize, NOT measured causal
# lift — the outcomes table (store.record_outcome) is what replaces them.
GROWTH_UPLIFT_PCT = {
    "build_a_bundle": 0.35,   # scale a valuable handpick buyer without dropping their AOV
    "video": 0.30,            # a call converts a stalling, offer-heavy buyer
    "bundles": 0.20,          # volume play for price-led, low-AOV buyers
    "chat": 0.20,             # open a conversation with a silent browser
}

# --- Expected-value ranking -----------------------------------------------
# The three plays protect/create different kinds of money, so we do NOT rank them
# on one raw "£ at stake" number (that would put migrate's exposure next to
# reengage's at-risk GMV next to growth's speculative uplift). Instead each play
# converts its prize to a comparable *expected £ impact over the next 6 months*
# via an explicit probability. All priors below are assumptions pending the
# feedback loop; they're here, labelled, so the ranking is honest.
SAVE_RATE = 0.35                 # reengage: P(win back a churning account)
CONVERT_RATE_WARM = 0.50         # migrate: P(warm account self-serves)
CONVERT_RATE_COLD = 0.30         # migrate: P(cold account self-serves)
# Migrating doesn't rescue at-risk GMV (that spend continues anyway) — its
# measurable prize is modest expansion: self-serve buyers who see full stock buy
# a bit more. Priced conservatively off the same engagement premium.
MIGRATION_EXPANSION = 0.12       # expected GMV expansion on converted spend

# --- Prioritisation --------------------------------------------------------
MIN_GMV_FOR_MIGRATION = 2000.0   # don't spend effort migrating tiny brokered accounts
# Above this, a broker-reliant account is a "whale": the downside of a botched
# migration (spend wobbles while they learn the app) outweighs the modelled
# expansion, so we never auto-nudge it — it's a phased, AM-shadowed handover.
WHALE_GMV = 25000.0
