"""The agent's instruments.

Each of the deterministic computations the old pipeline used to *decide* with is
exposed here as a tool the account-analyst agent can call to *get facts* about the
account it's evaluating. The split that makes this agentic rather than a rubber
stamp:

    tools return QUANTITIES (recomputed reliance, tier, CAGR, activity ratio, the
    £ maths, peer medians) and the config THRESHOLDS as reference — never the
    categorical verdict (segment / health / play / feature). Those calls are the
    agent's, made from this evidence plus the strategy in the skill docs.

Two guardrails are kept out of the agent's hands and computed here so they stay
deterministic: `expected_value` (all £ figures) and the key-account share in
`portfolio_context` (so a fifth of the book can't be auto-nudged on a whim).

A ToolKit is bound to ONE account for one evaluation. It never lets the agent
query or mutate other accounts — it only reads the book to place this account in
context (peer medians, concentration). Every method returns plain JSON-able dicts.
"""
from __future__ import annotations

import statistics as st

from . import config, ev
from .models import Account
from .segment import _trend_signals


def _median(xs: list[float], default: float = 0.0) -> float:
    return st.median(xs) if xs else default


class ToolKit:
    def __init__(self, account: Account, book: list[Account]):
        self.a = account
        self.book = book
        self._total_gmv = sum(x.gmv_total_6m for x in book) or 1.0

    # -- 1. raw account signals (facts, no verdict) -----------------------
    def account_signals(self) -> dict:
        a = self.a
        return {
            "account_id": a.account_id,
            "tenure_months": a.tenure_months,
            "ownership_label": a.ownership,   # for context ONLY — never an input to the call
            "buyer_persona": a.buyer_persona,
            "region": a.region, "country": a.country,
            "account_status": a.account_status,
            "spend": {
                "gmv_total_6m": a.gmv_total_6m,
                "monthly_gmv_sep_to_feb": a.monthly_gmv,
                "orders_6m": a.orders_6m,
                "aov": a.aov,
                "recent_gmv_feb": a.recent_gmv,
                "momentum_pct": a.momentum_pct,
            },
            "transaction_mode": {
                "manual_orders": a.manual_orders,
                "self_serve_orders": a.self_serve_orders,
                "broker_reliance_pct_recomputed": a.broker_reliance,
                "tier": a.transaction_mode,
                "broker_reliance_pct_reported": a.broker_reliance_reported,
                "reliance_discrepancy": a.reliance_discrepancy,
            },
            "self_serve_activity": {
                "app_active_days_6m": a.app_active_days_6m,
                "pdp_views_6m": a.pdp_views_6m,
                "make_an_offer_6m": a.make_an_offer_6m,
            },
            "engagement": {
                "chat_threads": a.chat_threads,
                "video_call_requests": a.video_call_requests,
                "handpick_orders": a.handpick_orders,
                "bundle_orders": a.bundle_orders,
                "bundle_gmv_share_pct": a.bundle_gmv_share_pct,
            },
            "data_flags": a.data_flags,
            "reference_thresholds": {
                "broker_reliance_high": config.BROKER_RELIANCE_HIGH,
                "broker_reliance_low": config.BROKER_RELIANCE_LOW,
                "growth_gmv_ceiling": config.GROWTH_GMV_CEILING,
                "handpick_only_bundle_share": config.HANDPICK_ONLY_BUNDLE_SHARE,
                "onboarding_tenure_max": config.ONBOARDING_TENURE_MAX,
                "note": "guidelines, not gates — you decide the classification",
            },
        }

    # -- 2. trend / churn signals (quantities, no dormant/declining call) --
    def trend_signals(self) -> dict:
        a = self.a
        cagr, activity = _trend_signals(a.monthly_gmv)
        first3 = sum(a.monthly_gmv[:3]) / 3
        active_months = sum(1 for m in a.monthly_gmv if m > 0)
        return {
            "cagr_monthly_pct": cagr,
            "activity_ratio_last4_vs_first3": activity,
            "monthly_gmv_sep_to_feb": a.monthly_gmv,
            "first3_month_avg": round(first3, 0),
            "last3_month_avg": round(sum(a.monthly_gmv[-3:]) / 3, 0),
            "latest_month": a.monthly_gmv[-1],
            "active_months": active_months,
            "orders_6m": a.orders_6m,
            "gmv_total_6m": a.gmv_total_6m,
            "reference_thresholds": {
                "material_account_gmv": config.MATERIAL_ACCOUNT_GMV,
                "high_value_gmv": config.HIGH_VALUE_GMV,
                "decline_cagr_monthly": config.DECLINE_CAGR_MONTHLY,
                "activity_drop_ratio": config.ACTIVITY_DROP_RATIO,
                "dormant_recent_gmv": config.DORMANT_RECENT_GMV,
                "rhythm_min_orders": config.RHYTHM_MIN_ORDERS,
                "rhythm_min_active_months": config.RHYTHM_MIN_ACTIVE_MONTHS,
                "recovery_fraction": config.RECOVERY_FRACTION,
                "note": "the materiality + rhythm + recovery reasoning is yours to apply",
            },
        }

    # -- 3. expected value (£ stays deterministic — a guardrail) ----------
    def expected_value(self, play: str, feature: str | None = None) -> dict:
        valid = {"reengage", "onboard", "migrate_to_selfserve", "grow_selfserve"}
        if play not in valid:
            return {"error": f"unknown play '{play}'", "valid_plays": sorted(valid)}
        prize_gmv, expected_value, prize_type = ev.prize_for(self.a, play, feature)
        return {
            "play": play, "feature": feature,
            "prize_gmv": prize_gmv,          # descriptive headline
            "expected_value": expected_value,  # the comparable, risk-adjusted ranking number
            "prize_type": prize_type,
            "note": "£ figures are computed deterministically from config priors — "
                    "you choose the play; the money is not yours to set",
        }

    # -- 4. peer benchmarks: this account vs its tier ---------------------
    def peer_benchmarks(self) -> dict:
        a = self.a
        tiers = {}
        for t in ("self_serve", "hybrid", "manual"):
            aovs = [x.aov for x in self.book if x.transaction_mode == t and x.orders_6m >= 2]
            gmvs = [x.gmv_total_6m for x in self.book if x.transaction_mode == t]
            tiers[t] = {"median_aov": round(_median(aovs)), "median_gmv": round(_median(gmvs)),
                        "accounts": len(gmvs)}
        my = tiers.get(a.transaction_mode, {})
        return {
            "this_account_tier": a.transaction_mode,
            "this_account_aov": a.aov,
            "this_account_gmv_6m": a.gmv_total_6m,
            "tier_median_aov": my.get("median_aov"),
            "tier_median_gmv": my.get("median_gmv"),
            "aov_vs_tier_median": (round(a.aov / my["median_aov"], 2)
                                   if my.get("median_aov") else None),
            "all_tiers": tiers,
            "note": "an AOV well below tier median with volume = a basket-size (bundle) opportunity",
        }

    # -- 5. portfolio context: strategy doc + live book stats -------------
    def portfolio_context(self, portfolio_md: str) -> dict:
        a = self.a
        share = a.gmv_total_6m / self._total_gmv
        br = [x for x in self.book if x.broker_reliance >= config.BROKER_RELIANCE_HIGH]
        br_gmv = sum(x.gmv_total_6m for x in br)
        keys = sorted((x for x in self.book
                       if x.gmv_total_6m / self._total_gmv >= config.KEY_ACCOUNT_GMV_SHARE),
                      key=lambda x: -x.gmv_total_6m)
        return {
            "strategy_doc": portfolio_md or "(no portfolio_overview.md provided)",
            "book_stats": {
                "total_accounts": len(self.book),
                "total_gmv_6m": round(self._total_gmv),
                "broker_reliant_accounts": len(br),
                "broker_reliant_pct_of_gmv": round(br_gmv / self._total_gmv * 100, 1),
                "n_key_accounts": len(keys),
            },
            "this_account": {
                "share_of_book_gmv_pct": round(share * 100, 2),
                "is_key_account": share >= config.KEY_ACCOUNT_GMV_SHARE,
                "key_account_threshold_pct": round(config.KEY_ACCOUNT_GMV_SHARE * 100, 1),
                "note": ("KEY ACCOUNT — a named, human-owned relationship; do NOT queue an "
                         "automated play. Recommend human ownership." if share >= config.KEY_ACCOUNT_GMV_SHARE
                         else "not a key account"),
            },
        }

    # -- dispatch a tool call by name -------------------------------------
    def dispatch(self, name: str, args: dict, portfolio_md: str = "") -> dict:
        if name == "account_signals":
            return self.account_signals()
        if name == "trend_signals":
            return self.trend_signals()
        if name == "expected_value":
            return self.expected_value(args.get("play", ""), args.get("feature"))
        if name == "peer_benchmarks":
            return self.peer_benchmarks()
        if name == "portfolio_context":
            return self.portfolio_context(portfolio_md)
        return {"error": f"unknown tool '{name}'"}


# --- Anthropic tool-use schemas (loaded by agent.py) ----------------------
TOOL_SCHEMAS = [
    {
        "name": "account_signals",
        "description": "Raw behavioural facts about this account: recomputed broker "
                       "reliance and transaction tier, spend and monthly GMV, self-serve "
                       "activity, engagement, and data-quality flags. No classification — "
                       "the numbers you reason from.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "trend_signals",
        "description": "Churn/health quantities from the monthly GMV series: monthly CAGR, "
                       "last-4-vs-first-3 activity ratio, active months, latest month, plus "
                       "the reference thresholds. You decide healthy / declining / dormant.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "expected_value",
        "description": "Compute the deterministic £ prize and risk-adjusted expected value "
                       "for a candidate play on this account. Call it to compare plays (e.g. "
                       "reengage vs migrate) before you commit. You pick the play; this returns the money.",
        "input_schema": {
            "type": "object",
            "properties": {
                "play": {"type": "string",
                         "enum": ["reengage", "onboard", "migrate_to_selfserve", "grow_selfserve"]},
                "feature": {"type": "string",
                            "enum": ["video", "chat", "bundles", "build_a_bundle"],
                            "description": "only for grow_selfserve"},
            },
            "required": ["play"],
        },
    },
    {
        "name": "peer_benchmarks",
        "description": "Place this account against its transaction-tier peers: its AOV and GMV "
                       "vs the tier medians. Useful for spotting basket-size (AOV) headroom.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "portfolio_context",
        "description": "The book-level strategy doc (principles, priorities) plus live stats: "
                       "GMV concentration, key-account count, and whether THIS account is a "
                       "key account (which must be human-owned, not auto-queued).",
        "input_schema": {"type": "object", "properties": {}},
    },
]
