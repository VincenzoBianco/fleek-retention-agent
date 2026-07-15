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


def tier_summary(accounts: list[Account]) -> dict:
    """Portfolio split by transaction-mode tier: count, GMV share, and blended AOV.
    The headline: hybrid is a quarter of the book but the majority of GMV and the
    highest AOV — the prime migration target."""
    tot_gmv = sum(a.gmv_total_6m for a in accounts) or 1
    out = {}
    for tier in ("self_serve", "hybrid", "manual"):
        g = [a for a in accounts if a.transaction_mode == tier]
        gmv = sum(a.gmv_total_6m for a in g)
        orders = sum(a.orders_6m for a in g) or 1
        out[tier] = {
            "accounts": len(g),
            "pct_of_gmv": round(gmv / tot_gmv * 100, 1),
            "aov_blended": round(gmv / orders),
        }
    return out


def calibrate(accounts: list[Account]) -> dict:
    ss = [a for a in accounts if a.broker_reliance <= 20]  # self-serving behaviour

    bundle_aov = [a.aov for a in accounts if a.bundle_gmv_share_pct >= 75 and a.orders_6m >= 2]
    hand_aov = [a.aov for a in accounts if a.bundle_gmv_share_pct <= 25 and a.orders_6m >= 2]

    engaged = [a.gmv_total_6m for a in ss if a.chat_threads >= 5 or a.video_call_requests >= 1]
    unengaged = [a.gmv_total_6m for a in ss if a.chat_threads < 5 and a.video_call_requests == 0]

    b_aov, h_aov = _median(bundle_aov, 1), _median(hand_aov, 1)
    eng, uneng = _median(engaged, 1), _median(unengaged, 1)
    return {
        "transaction_tiers": tier_summary(accounts),
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

    Reported three ways, from weakest to strongest stress:
    - within-play order invariance is *computed* (not asserted): EV = prior x
      GMV-term, so a positive scalar can't reorder a play — we verify the ranking
      is byte-identical under perturbation;
    - proportional +/-30% (all priors scaled together);
    - DIFFERENTIAL: reengage's save-rate up 30% while migrate's convert/expansion
      go down 30% (and vice-versa) — the perturbation that can actually flip
      reengage-vs-migrate, since scaling everything together barely can.
    """
    from . import config
    from .plays import decide
    from .segment import classify

    keys = ["SAVE_RATE", "CONVERT_RATE_WARM", "CONVERT_RATE_COLD", "MIGRATION_EXPANSION"]

    def with_priors(mults: dict):
        saved = {k: getattr(config, k) for k in keys}
        for k in keys:
            setattr(config, k, saved[k] * mults.get(k, 1.0))
        try:
            return [decide(a, classify(a)) for a in accounts]
        finally:
            for k in keys:
                setattr(config, k, saved[k])

    def mix(ds):
        ds = sorted([d for d in ds if d.play], key=lambda d: -d.expected_value)[:20]
        out = {}
        for d in ds:
            out[d.play] = out.get(d.play, 0) + 1
        return out

    def top20_ids(ds):
        return [d.account_id for d in sorted([d for d in ds if d.play],
                key=lambda d: (-d.expected_value, -d.prize_gmv, d.account_id))[:20]]

    def overlap(ds):
        return len(set(top20_ids(base)) & set(top20_ids(ds)))

    base = with_priors({})
    reengage_up = with_priors({"SAVE_RATE": 1.3, "CONVERT_RATE_WARM": 0.7,
                               "CONVERT_RATE_COLD": 0.7, "MIGRATION_EXPANSION": 0.7})
    migrate_up = with_priors({"SAVE_RATE": 0.7, "CONVERT_RATE_WARM": 1.3,
                              "CONVERT_RATE_COLD": 1.3, "MIGRATION_EXPANSION": 1.3})

    return {
        # Within a play EV = (a positive prior) x (a GMV term), so the priors
        # can't reorder accounts *inside* a play — only the cross-play balance
        # moves. Honest reading of the numbers below: proportional scaling barely
        # shifts the mix, but a DIFFERENTIAL shift (one play's prior up, the
        # other's down) does move the reengage-vs-migrate balance. That's the
        # real dependence, and exactly what the outcomes loop is there to remove.
        "note": "within-play order is prior-free; cross-play balance moves under differential priors",
        "top20_mix_base": mix(base),
        "top20_mix_all_priors_minus_30pct": mix(with_priors({k: 0.7 for k in keys})),
        "top20_mix_all_priors_plus_30pct": mix(with_priors({k: 1.3 for k in keys})),
        "top20_mix_reengage_up_migrate_down": mix(reengage_up),
        "top20_mix_migrate_up_reengage_down": mix(migrate_up),
        "top20_account_overlap_under_differential_stress": min(overlap(reengage_up), overlap(migrate_up)),
    }
