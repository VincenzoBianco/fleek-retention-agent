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
    # A material buyer that was spending and has been silent for a quarter.
    material = a.gmv_total_6m >= config.MATERIAL_ACCOUNT_GMV
    if material and first_half > 0 and last_quarter <= config.DORMANT_RECENT_GMV:
        return "dormant"
    # Only alarm on a slide for material buyers — a £150 one-order account
    # bouncing month to month isn't a retention concern.
    if material and a.momentum_pct is not None and a.momentum_pct <= config.DECLINE_MOMENTUM:
        return "declining"
    return "healthy"


def _self_serving_now(a: Account) -> bool:
    """Genuine independent product usage — the counter-evidence to reliance."""
    return (a.app_active_days_6m >= config.SELFSERVE_ACTIVE_DAYS_LOW
            or a.pdp_views_6m >= config.SELFSERVE_PDP_LOW)


def classify(a: Account) -> SegmentResult:
    health = _health(a)
    r = a.broker_reliance
    active = _self_serving_now(a)

    # --- PRIMARY split on reliance (recomputed from order counts) ---
    if r >= config.BROKER_RELIANCE_HIGH:
        # A person is placing most orders. Warm = they at least browse/offer,
        # so migration is a nudge; cold = they never touch the app, harder.
        warm = active or a.make_an_offer_6m > 0
        reasons = [
            f"AM places {r:.0f}% of orders",
            "browses/offers on their own" if warm else f"only {a.app_active_days_6m:.0f} active app-days, {a.pdp_views_6m:.0f} views",
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
