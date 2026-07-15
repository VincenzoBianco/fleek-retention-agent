"""Domain models. Pydantic for validation at the ingest boundary; everything
downstream (segmentation, plays, drafting) speaks these types, not raw dicts.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Account(BaseModel):
    """One cleaned account row. Raw fields are kept alongside the fields we
    recompute ourselves (reliance, momentum) so a reviewer can see both."""

    account_id: str
    ownership: str                       # "Account Managed" | "Self Serve" (label, used only to sanity-check)
    buyer_persona: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    account_status: Optional[str] = None
    tenure_months: float = 0.0

    # spend
    gmv_total_6m: float = 0.0
    monthly_gmv: list[float] = Field(default_factory=list)  # sep..feb
    orders_6m: int = 0

    # transaction mode (counts are the source of truth for reliance)
    manual_orders: int = 0
    self_serve_orders: int = 0

    # self-serve activity
    app_active_days_6m: float = 0.0
    pdp_views_6m: float = 0.0
    make_an_offer_6m: float = 0.0

    # engagement features
    chat_threads: float = 0.0
    video_call_requests: float = 0.0
    handpick_orders: int = 0
    bundle_orders: int = 0
    bundle_gmv_share_pct: float = 0.0

    # --- recomputed signals (see ingest.py) ---
    broker_reliance: float = 0.0         # manual_orders / orders_6m * 100 (we trust counts)
    broker_reliance_reported: Optional[float] = None  # the provided column, kept for reconciliation
    reliance_discrepancy: bool = False   # True when reported vs computed differ materially
    momentum_pct: Optional[float] = None # robust last-half vs first-half GMV change
    recent_gmv: float = 0.0              # most recent non-null month
    aov: float = 0.0                     # gmv_total_6m / orders_6m

    # --- data quality flags raised during cleaning ---
    data_flags: list[str] = Field(default_factory=list)

    # --- identity for idempotency ---
    fingerprint: str = ""                # hash of the cleaned, decision-relevant fields


class Decision(BaseModel):
    """What the tool decided to do about one account this run."""

    account_id: str
    segment: str                         # behavioural segment
    health: str                          # healthy | declining | dormant
    play: Optional[str] = None           # play name, or None = leave alone
    action: Optional[str] = None         # the concrete next best action
    reason: str = ""                     # why this account, why this action (explainable)
    feature: Optional[str] = None        # for growth play: chat|bundles|video|build_a_bundle
    # Ranking. We rank on expected_value (a comparable £ across plays); prize_gmv
    # is the descriptive headline number and prize_type says what it *is*, so the
    # queue never conflates at-risk GMV with exposure or speculative uplift.
    priority: float = 0.0                # == expected_value; the ranking key
    expected_value: float = 0.0          # risk-adjusted expected £ impact / 6mo
    prize_gmv: float = 0.0               # descriptive headline £ for this account
    prize_type: str = ""                 # "GMV at risk" | "GMV on a human" | "modelled uplift"
    gmv_at_stake: float = 0.0            # kept = prize_gmv, for backwards-compatible reporting
    draft: Optional[str] = None          # the drafted message / nudge / call note
    channel: Optional[str] = None        # whatsapp | in_app | call
    holdout: bool = False                # control group: intended play recorded, no outreach fires
    fingerprint: str = ""                # account fingerprint this decision was made against


class RunReport(BaseModel):
    """Summary of one orchestrator run — the 'what changed' an AM reads each morning."""

    run_id: int
    source: str
    n_seen: int = 0
    n_new: int = 0
    n_changed: int = 0
    n_unchanged_skipped: int = 0
    n_stale_skipped: int = 0             # differing but from an older source — not overwritten
    n_actions: int = 0
    n_holdout: int = 0                   # control-group accounts (intended play, no outreach)
    segment_counts: dict[str, int] = Field(default_factory=dict)
    play_counts: dict[str, int] = Field(default_factory=dict)
    gmv_at_stake_total: float = 0.0      # sum of descriptive prizes (mixed types)
    expected_value_total: float = 0.0    # sum of risk-adjusted EV (the comparable number)
    learned_priors: dict = Field(default_factory=dict)  # priors updated from outcomes this run
    gmv_concentration: dict = Field(default_factory=dict)  # % of GMV that is broker-dependent
