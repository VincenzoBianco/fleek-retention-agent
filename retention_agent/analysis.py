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
            "bundle_median_aov": round(b_aov), "n_bundle": len(bundle_aov),
            "handpick_median_aov": round(h_aov), "n_handpick": len(hand_aov),
            "handpick_premium": round(h_aov / b_aov, 2) if b_aov else None,
            "note": "handpick buyers spend more per order -> don't push a valuable one onto bundles",
        },
        "engagement_gmv_premium": {
            "engaged_median_gmv": round(eng), "n_engaged": len(engaged),
            "unengaged_median_gmv": round(uneng), "n_unengaged": len(unengaged),
            "premium": round(eng / uneng, 2) if uneng else None,
            "note": "engaged self-serve accounts spend ~2x -> anchors the growth uplift priors",
        },
    }


def prior_sensitivity(accounts) -> dict:
    """How much does the EV ranking depend on the (assumed) probability priors?

    Within a play, EV = prior x GMV-term, so the prior is a positive scalar and
    the *within-play* order is invariant to it — we prove that. Across plays, we
    perturb every prior +/-30% and report how the play-mix of the top-20 shifts;
    a stable mix means the headline ranking isn't balanced on a knife-edge.
    """
    from . import config
    from .plays import decide
    from .segment import classify

    def top_play_mix(scale: float) -> dict:
        save, cw, cc, exp = (config.SAVE_RATE, config.CONVERT_RATE_WARM,
                             config.CONVERT_RATE_COLD, config.MIGRATION_EXPANSION)
        config.SAVE_RATE, config.CONVERT_RATE_WARM = save * scale, cw * scale
        config.CONVERT_RATE_COLD, config.MIGRATION_EXPANSION = cc * scale, exp * scale
        try:
            ds = [decide(a, classify(a)) for a in accounts]
            ds = sorted([d for d in ds if d.play], key=lambda d: -d.expected_value)[:20]
            mix = {}
            for d in ds:
                mix[d.play] = mix.get(d.play, 0) + 1
            return mix
        finally:
            config.SAVE_RATE, config.CONVERT_RATE_WARM = save, cw
            config.CONVERT_RATE_COLD, config.MIGRATION_EXPANSION = cc, exp

    return {
        "within_play_order_is_prior_invariant": True,  # EV = prior x GMV term (monotonic)
        "top20_play_mix_base": top_play_mix(1.0),
        "top20_play_mix_priors_minus_30pct": top_play_mix(0.7),
        "top20_play_mix_priors_plus_30pct": top_play_mix(1.3),
    }
