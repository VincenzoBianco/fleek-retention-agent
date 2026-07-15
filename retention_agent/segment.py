"""Behavioural segmentation.

The ownership label ("Account Managed" / "Self Serve") is deliberately NOT an
input here. We classify from what the account actually does:

  broker_reliant     a person places most of its orders and it barely touches
                     the product  -> the migration target (problem 1)
  assisted_healthy   gets some AM help but is largely buying for itself already
                     -> leave alone / light monitor
  self_serve_growth  buys for itself, with visible headroom (high intent + low
                     spend, or handpick-only) -> the growth target (problem 2)
  self_serve_healthy buys for itself, engaged, spending -> protect

A health overlay (healthy / declining / dormant) sits on top of the segment and
gates whether a play actually fires. One account = one segment; the function is
a single pass with no cross-account dependency, so it scales linearly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .models import Account


@dataclass
class SegmentResult:
    segment: str
    subtype: str = ""          # e.g. broker_reliant warm/cold, growth flavour
    health: str = "healthy"    # healthy | declining | dormant
    reasons: list[str] = field(default_factory=list)


def _health(a: Account) -> str:
    first_half = sum(a.monthly_gmv[:3])
    last_quarter = sum(a.monthly_gmv[-3:])
    latest_month = a.monthly_gmv[-1]
    active_months = sum(1 for m in a.monthly_gmv if m > 0)
    material = a.gmv_total_6m >= config.MATERIAL_ACCOUNT_GMV
    # A real churn signal needs materiality AND either a broken rhythm OR enough
    # £ that we'd want eyes regardless. The rhythm gate tells a lumpy small buyer
    # apart from a churner; a high-value account skips it — silence on a £70k
    # account is an emergency even off two big orders.
    rhythm = (a.orders_6m >= config.RHYTHM_MIN_ORDERS
              and active_months >= config.RHYTHM_MIN_ACTIVE_MONTHS)
    flag_ok = rhythm or a.gmv_total_6m >= config.HIGH_VALUE_GMV
    if material and flag_ok and first_half > 0 and last_quarter <= config.DORMANT_RECENT_GMV:
        return "dormant"
    # Declining only if the slide hasn't already reversed: the latest month must
    # still be below RECOVERY_FRACTION of the earlier run-rate. An account that
    # dipped mid-window but rebounded in the last month is not "at risk".
    if material and flag_ok and a.momentum_pct is not None and a.momentum_pct <= config.DECLINE_MOMENTUM:
        rebounded = latest_month >= config.RECOVERY_FRACTION * (first_half / 3)
        if not rebounded:
            return "declining"
    return "healthy"


def classify(a: Account) -> SegmentResult:
    health = _health(a)
    r = a.broker_reliance

    # --- PRIMARY split on reliance (recomputed from order counts) ---
    if r >= config.BROKER_RELIANCE_HIGH:
        # A person is placing most orders. The readiness signal is the transaction
        # tier: HYBRID accounts already self-serve 25-75% of their orders — they've
        # proven they can use the product, so migration is a low-friction nudge
        # (warm). MANUAL accounts (>75%) rarely self-serve — a hands-on handover
        # (cold). This tier read is more principled than app-activity alone, and
        # it's where the value is: hybrid is the highest-AOV, highest-GMV tier.
        warm = a.transaction_mode == "hybrid"
        reasons = [
            f"AM places {r:.0f}% of orders",
            f"already self-serves {100 - r:.0f}% of orders — migration-ready" if warm
            else f"self-serves only {100 - r:.0f}% — needs a guided handover",
        ]
        return SegmentResult("broker_reliant", "warm" if warm else "cold", health, reasons)

    if r <= config.BROKER_RELIANCE_LOW:
        # Behaves as self-serve regardless of label. Growth vs healthy.
        intent = (a.pdp_views_6m >= config.INTENT_PDP_HIGH
                  or a.make_an_offer_6m >= config.INTENT_OFFER_HIGH)
        headroom = a.gmv_total_6m < config.GROWTH_GMV_CEILING
        handpick_only = (a.bundle_gmv_share_pct <= config.HANDPICK_ONLY_BUNDLE_SHARE
                         and a.handpick_orders > 0)
        if (intent and headroom) or handpick_only:
            if handpick_only and not (intent and headroom):
                sub, why = "handpick_only", [f"{a.bundle_gmv_share_pct:.0f}% bundle spend — buys handpicks only"]
            elif handpick_only:
                sub, why = "intent_and_handpick", [
                    f"{a.pdp_views_6m:.0f} views / {a.make_an_offer_6m:.0f} offers but £{a.gmv_total_6m:,.0f} spend",
                    f"only {a.bundle_gmv_share_pct:.0f}% bundle spend",
                ]
            else:
                sub, why = "high_intent_low_spend", [
                    f"{a.pdp_views_6m:.0f} views / {a.make_an_offer_6m:.0f} offers but £{a.gmv_total_6m:,.0f} spend"
                ]
            return SegmentResult("self_serve_growth", sub, health, why)
        return SegmentResult("self_serve_healthy", "", health,
                             [f"self-serving, £{a.gmv_total_6m:,.0f} over 6mo"])

    # --- mid reliance (20–50%): partly assisted, mostly self ---
    return SegmentResult("assisted_healthy", "", health,
                         [f"AM places {r:.0f}% of orders, self-serves the rest"])


def classify_all(accounts: list[Account]) -> dict[str, SegmentResult]:
    return {a.account_id: classify(a) for a in accounts}
