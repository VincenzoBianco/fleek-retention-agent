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
STATE_DB = DATA_DIR / "state.db"

# Monthly GMV columns, in calendar order. The window is Sep 2025 -> Feb 2026.
MONTH_COLS = ["gmv_sep", "gmv_oct", "gmv_nov", "gmv_dec", "gmv_jan", "gmv_feb"]

# --- Broker reliance -------------------------------------------------------
# An account is "broker-reliant" when a person (the AM) is placing most of its
# orders AND it shows little sign of buying on its own. We read this from
# behaviour, never from the ownership label.
BROKER_RELIANCE_HIGH = 50.0      # >= this % of orders placed by an AM = person is the buyer
BROKER_RELIANCE_LOW = 20.0       # <= this % = effectively self-serving already

# Transaction-mode tiers, cut on the same manual-order share. This is the read
# that matters commercially: HYBRID accounts (already self-serve 25-75% of their
# orders) are 25% of the book but ~70% of GMV *and* the highest AOV — they've
# proven they can use the product, so they're the prime, low-friction migration
# target. MANUAL (>75%) accounts rarely self-serve, so they need a hands-on
# handover. SELF_SERVE (<25%) are the growth pool.
TIER_SELF_SERVE_MAX = 25.0       # < this % manual = self-serve tier
TIER_MANUAL_MIN = 75.0           # > this % manual = manual tier; between = hybrid

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
# A slide is flagged only when TWO signals agree, which anecdotally captures
# "the account went quiet" without over-firing on lumpy noise:
#   (1) GMV compound growth over the window (CAGR, monthly) is negative, AND
#   (2) recent activity (last 4 months) has dropped vs early (first 3 months).
# NB activity here is proxied by monthly GMV — the data has no monthly order
# counts (only 6-month totals), so a month with spend stands in for "transacted".
DECLINE_CAGR_MONTHLY = -15.0     # <= this monthly compound GMV growth = shrinking
ACTIVITY_DROP_RATIO = 0.60       # last-4-mo activity <= this x first-3-mo = pulled back
DORMANT_RECENT_GMV = 1.0         # <= this over the last 3 months = silent
MATERIAL_ACCOUNT_GMV = 2000.0    # a "real" buyer worth flagging on health
RHYTHM_MIN_ORDERS = 4            # ...and an established cadence: this many orders
RHYTHM_MIN_ACTIVE_MONTHS = 3     # ...across at least this many distinct months
# Materiality governs the precision/recall trade. The rhythm gate above stops us
# crying wolf on tiny lumpy buyers — but for a HIGH-VALUE account the cost of a
# missed churn dwarfs a needless check-in, so silence alone flags it regardless
# of order cadence. (Without this, a £70k account silent since October reads as
# "healthy" — a real miss.)
HIGH_VALUE_GMV = 10000.0         # >= this: prolonged silence flags churn, rhythm gate waived

# --- Onboarding / early customer success ----------------------------------
# All 63 accounts under 5 months old have ZERO manual orders and the smallest
# baskets (median £242, ~1 order). The AM relationship forms late — by 12mo+,
# reliance is 35% and GMV ~6x higher. That late arrival is an onboarding gap:
# new accounts are left to sink-or-swim, most stall after one order. We flag
# them as a proactive early-success cohort and rank them on RAMP POTENTIAL (the
# value unlocked by activating them), not their tiny current spend — otherwise a
# £-only queue buries exactly the accounts a human should be nurturing early.
ONBOARDING_TENURE_MAX = 5.0      # < this many months = new, needs proactive onboarding
EARLY_SUCCESS_GMV_TARGET = 1000.0  # a healthy activated new account's ~6mo GMV (ramp target)
ONBOARD_ACTIVATION_RATE = 0.40   # prior: P(a proactive early-CS touch activates the ramp)

# --- Key-account concentration --------------------------------------------
# One account is 20% of this book. Accounts this concentrated aren't queue items
# — they're named, human-owned relationships. Surfaced separately so nobody fires
# an automated nudge at a fifth of the revenue.
KEY_ACCOUNT_GMV_SHARE = 0.10     # >= this share of book GMV = key account
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

# --- Learning loop: causal holdout ----------------------------------------
# A fraction of would-be-actioned accounts are held back as a control group
# (deterministically, by a hash of account_id — no randomness, so it's stable
# across runs). Their intended play is recorded but no outreach fires, so
# comparing treated-vs-holdout GMV in the outcomes table turns the growth uplift
# priors from a correlation into a measured, causal lift. Set 0 to disable.
HOLDOUT_FRACTION = 0.10

# --- Prioritisation --------------------------------------------------------
MIN_GMV_FOR_MIGRATION = 2000.0   # don't spend effort migrating tiny brokered accounts
# Above this, a broker-reliant account is a "whale": the downside of a botched
# migration (spend wobbles while they learn the app) outweighs the modelled
# expansion, so we never auto-nudge it — it's a phased, AM-shadowed handover.
WHALE_GMV = 25000.0
