"""Calibration: derive the growth priors from the book itself, so the numbers in
config.py are reproducible rather than asserted. `python cli.py calibrate` prints
these; they're the empirical anchors cited in the README.

Important honesty caveat: these are *correlations* in a cross-section, not measured
causal lift. They size the prize and justify which feature to pick; the outcomes
table (store) is what turns them into measured lift over time.
"""
from __future__ import annotations

import statistics as st

from .models import Account


def _median(xs: list[float], default: float = 0.0) -> float:
    return st.median(xs) if xs else default


def calibrate(accounts: list[Account]) -> dict:
    ss = [a for a in accounts if a.broker_reliance <= 20]  # self-serving behaviour

    bundle_aov = [a.aov for a in accounts if a.bundle_gmv_share_pct >= 75 and a.orders_6m >= 2]
    hand_aov = [a.aov for a in accounts if a.bundle_gmv_share_pct <= 25 and a.orders_6m >= 2]

    engaged = [a.gmv_total_6m for a in ss if a.chat_threads >= 5 or a.video_call_requests >= 1]
    unengaged = [a.gmv_total_6m for a in ss if a.chat_threads < 5 and a.video_call_requests == 0]

    b_aov, h_aov = _median(bundle_aov, 1), _median(hand_aov, 1)
    eng, uneng = _median(engaged, 1), _median(unengaged, 1)
    return {
        "handpick_vs_bundle_aov": {
            "bundle_median_aov": round(b_aov),
            "handpick_median_aov": round(h_aov),
            "handpick_premium": round(h_aov / b_aov, 2) if b_aov else None,
            "note": "handpick buyers spend more per order -> don't push a valuable one onto bundles",
        },
        "engagement_gmv_premium": {
            "engaged_median_gmv": round(eng),
            "unengaged_median_gmv": round(uneng),
            "premium": round(eng / uneng, 2) if uneng else None,
            "note": "engaged self-serve accounts spend ~2x -> anchors the growth uplift priors",
        },
    }
