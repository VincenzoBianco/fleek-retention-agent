"""Tests run on synthetic data (the real workbook isn't in the repo).

Coverage: cleaning correctness, behaviour-over-label segmentation, the feature
decision tree, the idempotency contract (no dupes / skip unchanged / re-decide
changed), and a 30k-row scale smoke test.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from retention_agent import config
from retention_agent.ingest import clean, to_accounts
from retention_agent.models import Account
from retention_agent.plays import choose_feature, decide
from retention_agent.segment import classify
from retention_agent.store import Store

MONTHS = config.MONTH_COLS


def raw_row(**kw):
    base = dict(account_id="ACC-X", ownership="Account Managed", buyer_persona="Reseller",
                region="EU", country="Germany", account_status="Active", tenure_months=12,
                gmv_total_6m=0, orders_6m=0, manual_orders=0, self_serve_orders=0,
                app_active_days_6m=0, pdp_views_6m=0, make_an_offer_6m=0, chat_threads=0,
                video_call_requests=0, handpick_orders=0, bundle_orders=0, bundle_gmv_share_pct=0,
                broker_reliance_pct=0, gmv_sep=0, gmv_oct=0, gmv_nov=0, gmv_dec=0, gmv_jan=0, gmv_feb=0)
    base.update(kw)
    return base


def acct(**kw) -> Account:
    d = dict(account_id="ACC-X", ownership="Self Serve", gmv_total_6m=1000, orders_6m=4,
             monthly_gmv=[200, 200, 200, 200, 100, 100], broker_reliance=0,
             app_active_days_6m=10, pdp_views_6m=50, make_an_offer_6m=0, chat_threads=1,
             handpick_orders=1, bundle_orders=1, bundle_gmv_share_pct=60, momentum_pct=0,
             recent_gmv=100, aov=250, fingerprint="fp")
    d.update(kw)
    return Account(**d)


# --- cleaning -------------------------------------------------------------
def test_reliance_recomputed_from_counts():
    # reported says 10%, but counts say 8/10 = 80% -> we trust the counts + flag
    df = clean(pd.DataFrame([raw_row(orders_6m=10, manual_orders=8, self_serve_orders=2,
                                     broker_reliance_pct=10, gmv_total_6m=1000, gmv_sep=1000)]))
    a = to_accounts(df)[0]
    assert a.broker_reliance == 80.0
    assert a.reliance_discrepancy is True
    assert "reliance_mismatch_recomputed" in a.data_flags


def test_blanks_and_duplicate_status_handled():
    df = clean(pd.DataFrame([
        raw_row(account_id="A", account_status=None, app_active_days_6m=np.nan),
        raw_row(account_id="B", account_status="Duplicate"),
    ]))
    accts = {a.account_id: a for a in to_accounts(df)}
    assert accts["A"].account_status == "Unknown"        # blanks not invented as Active
    assert accts["A"].app_active_days_6m == 0            # missing activity -> 0
    assert "status_missing" in accts["A"].data_flags
    assert "status_duplicate" in accts["B"].data_flags


def test_momentum_recomputed_and_robust_to_zero_sep():
    # Sep=0 would make the provided gmv_trend_pct blow up; our momentum still works
    df = clean(pd.DataFrame([raw_row(gmv_sep=0, gmv_oct=0, gmv_nov=0,
                                     gmv_dec=100, gmv_jan=100, gmv_feb=100, gmv_total_6m=300)]))
    a = to_accounts(df)[0]
    assert a.momentum_pct == 100.0                       # 0 -> spend reads as ramping up


def test_fingerprint_deterministic():
    df = clean(pd.DataFrame([raw_row(gmv_total_6m=500, gmv_sep=500, orders_6m=2, manual_orders=1, self_serve_orders=1)]))
    assert to_accounts(df)[0].fingerprint == to_accounts(df)[0].fingerprint


def test_defensive_cleaning_paths_fire_on_messy_input():
    # These paths don't trigger on the real book (it's clean-ish there), so we
    # prove them on deliberately messy rows: variant ownership spelling, a
    # negative value, and a gmv-total that disagrees with the monthly sum.
    df = clean(pd.DataFrame([
        raw_row(account_id="V", ownership="  self-serve ", app_active_days_6m=-5),
        raw_row(account_id="M", gmv_total_6m=9999, gmv_sep=100, gmv_oct=100),  # total != sum
    ]))
    accts = {a.account_id: a for a in to_accounts(df)}
    assert accts["V"].ownership == "Self Serve"          # variant + whitespace normalised
    assert accts["V"].app_active_days_6m == 0            # negative clipped to 0
    assert "gmv_total_mismatch" in accts["M"].data_flags  # inconsistency flagged


def test_duplicate_account_id_does_not_duplicate():
    # same id twice in one load -> last wins, one account out
    df = clean(pd.DataFrame([
        raw_row(account_id="D", gmv_total_6m=100, gmv_sep=100),
        raw_row(account_id="D", gmv_total_6m=200, gmv_sep=200),
    ]))
    accts = to_accounts(df)
    assert len(accts) == 1 and accts[0].gmv_total_6m == 200


# --- segmentation reads behaviour, not the label -------------------------
def test_account_managed_but_self_serving_is_not_broker_reliant():
    a = acct(ownership="Account Managed", broker_reliance=5, app_active_days_6m=20, pdp_views_6m=200)
    assert classify(a).segment != "broker_reliant"


def test_high_reliance_low_activity_is_broker_reliant():
    a = acct(ownership="Account Managed", broker_reliance=75, app_active_days_6m=2,
             pdp_views_6m=5, gmv_total_6m=10000, monthly_gmv=[2000]*5+[0])
    assert classify(a).segment == "broker_reliant"


def test_high_intent_low_spend_is_growth():
    a = acct(broker_reliance=0, pdp_views_6m=300, make_an_offer_6m=5, gmv_total_6m=800)
    s = classify(a)
    assert s.segment == "self_serve_growth"


# --- feature decision tree ------------------------------------------------
def test_feature_low_aov_handpick_gets_bundles():
    a = acct(bundle_gmv_share_pct=10, handpick_orders=3, aov=200)
    assert choose_feature(a)[0] == "bundles"


def test_feature_high_aov_handpick_gets_build_a_bundle():
    # a valuable handpick buyer should NOT be pushed onto generic bundles
    a = acct(bundle_gmv_share_pct=10, handpick_orders=3, aov=900)
    assert choose_feature(a)[0] == "build_a_bundle"


def test_feature_offers_not_converting_gets_video():
    a = acct(bundle_gmv_share_pct=80, handpick_orders=0, make_an_offer_6m=5, orders_6m=2)
    assert choose_feature(a)[0] == "video"


def test_feature_heavy_browser_gets_chat():
    a = acct(bundle_gmv_share_pct=80, handpick_orders=0, make_an_offer_6m=0,
             pdp_views_6m=300, chat_threads=0, orders_6m=8)
    assert choose_feature(a)[0] == "chat"


# --- health: cadence gate separates churn from lumpy buying ---------------
def test_lumpy_whale_not_flagged_dormant():
    # two big early orders then silence — clears the GMV gate, but no rhythm
    a = acct(account_id="WHALE", gmv_total_6m=70000, orders_6m=2,
             monthly_gmv=[36000, 34000, 0, 0, 0, 0], momentum_pct=-100)
    assert classify(a).health == "healthy"   # not "dormant"


def test_rhythmic_buyer_gone_silent_is_dormant():
    # a real cadence (orders across the first half) that then stopped
    a = acct(account_id="CHURN", gmv_total_6m=9000, orders_6m=6,
             monthly_gmv=[3000, 3000, 3000, 0, 0, 0], momentum_pct=-100)
    assert classify(a).health == "dormant"


def test_rebounded_account_not_flagged_declining():
    # dipped mid-window but the latest month recovered -> not "at risk"
    a = acct(account_id="REBOUND", gmv_total_6m=24000, orders_6m=6,
             monthly_gmv=[5000, 5000, 6000, 1000, 500, 6000], momentum_pct=-45)
    assert classify(a).health == "healthy"


def test_at_risk_is_forward_exposure_not_lifetime_gmv():
    # still ordering, just sliding: at-risk must be the lost run-rate, not the
    # whole 6-month GMV
    a = acct(account_id="SLIDE", gmv_total_6m=18000, orders_6m=6,
             monthly_gmv=[4000, 4000, 4000, 2000, 2000, 0], momentum_pct=-50)
    d = decide(a, classify(a))
    assert d.play == "reengage"
    assert 0 < d.prize_gmv < a.gmv_total_6m       # forward exposure, not lifetime


# --- expected-value ranking is comparable across plays --------------------
def test_migrate_ev_is_far_below_its_exposure_prize():
    # £ on a human is NOT at-risk; EV must reflect that, not the raw exposure
    a = acct(account_id="BIG", ownership="Account Managed", broker_reliance=70,
             app_active_days_6m=1, pdp_views_6m=2, gmv_total_6m=100000,
             orders_6m=20, monthly_gmv=[20000]*5 + [0])
    d = decide(a, classify(a))
    assert d.play == "migrate_to_selfserve"
    assert d.prize_type == "GMV on a human"
    assert d.expected_value < d.prize_gmv * 0.1   # discounted hard, not conflated
    assert d.priority == d.expected_value


def test_reengage_prize_is_at_risk_and_ev_uses_save_rate():
    a = acct(account_id="R", gmv_total_6m=10000, orders_6m=6,
             monthly_gmv=[3000, 3000, 3000, 500, 0, 0], momentum_pct=-80)
    d = decide(a, classify(a))
    assert d.play == "reengage" and d.prize_type.startswith("GMV at risk")
    assert d.expected_value == round(config.SAVE_RATE * d.prize_gmv, 0)


# --- causal holdout -------------------------------------------------------
def test_holdout_is_deterministic_and_excluded_from_queue(tmp_path):
    from retention_agent.plays import is_holdout
    # deterministic: same id -> same verdict across calls
    ids = [f"ACC-{i}" for i in range(200)]
    assert [is_holdout(i) for i in ids] == [is_holdout(i) for i in ids]
    held = [i for i in ids if is_holdout(i)]
    assert 0 < len(held) < len(ids)               # some held out, not all
    # a held-out account keeps its intended play but never enters the queue
    store = Store(tmp_path / "s.db")
    r = store.start_run("run")
    for i in ids:
        a = acct(account_id=i, fingerprint=i, ownership="Account Managed", broker_reliance=70,
                 app_active_days_6m=1, pdp_views_6m=2, gmv_total_6m=40000, orders_6m=10,
                 monthly_gmv=[8000, 8000, 8000, 8000, 8000, 0])
        d = decide(a, classify(a))
        store.upsert(a, d, "", False, r)
    store.commit()
    queue_ids = {row["account_id"] for row in store.action_queue()}
    assert not (set(held) & queue_ids)            # no holdout account in the queue
    assert store.holdout_count() == len(held)


# --- GMV concentration (the broker-dependency headline) -------------------
def test_gmv_concentration_flags_broker_dependency(tmp_path):
    store = Store(tmp_path / "s.db")
    r = store.start_run("run")
    # one big broker-reliant account + several small self-serving ones
    big = acct(account_id="BIG", ownership="Account Managed", broker_reliance=70,
               app_active_days_6m=1, pdp_views_6m=2, gmv_total_6m=80000, orders_6m=10,
               monthly_gmv=[16000, 16000, 16000, 16000, 16000, 0])
    store.upsert(big, decide(big, classify(big)), "", False, r)
    for i in range(9):
        s = acct(account_id=f"S-{i}", fingerprint=f"s{i}", broker_reliance=0,
                 gmv_total_6m=1000, pdp_views_6m=200)
        store.upsert(s, decide(s, classify(s)), "", False, r)
    store.commit()
    c = store.gmv_concentration()
    assert c["broker_reliant_accounts"] == 1
    assert c["pct_of_accounts"] == 10.0          # 1 of 10 accounts
    assert c["pct_of_gmv"] > 80.0                # but the large majority of GMV


# --- newest-source-wins guard --------------------------------------------
def test_stale_source_does_not_overwrite_fresher_data(tmp_path):
    store = Store(tmp_path / "s.db")
    a = acct(account_id="ACC-1", fingerprint="v2")
    r = store.start_run("fresh")
    store.upsert(a, decide(a, classify(a)), "", False, r, source_ts=100.0)
    store.commit()
    # an older source restating the same account with different data -> stale, skipped
    older = acct(account_id="ACC-1", fingerprint="v1-old")
    split = store.diff([older], source_ts=50.0)
    assert split["stale"] == [older] and not split["changed"]
    # a newer source is applied normally
    newer = acct(account_id="ACC-1", fingerprint="v3-new")
    split = store.diff([newer], source_ts=150.0)
    assert split["changed"] == [newer] and not split["stale"]


# --- feedback loop: outcomes table ---------------------------------------
def test_outcomes_recorded_and_rates_computed(tmp_path):
    store = Store(tmp_path / "s.db")
    r = store.start_run("run")
    a = acct(account_id="ACC-1", fingerprint="fp")
    store.upsert(a, decide(a, classify(a)), "d", False, r)
    store.commit()
    store.record_outcome("ACC-1", r, "grow_selfserve", "chat", responded=True, converted=True, gmv_delta=120)
    rates = store.realized_rates()
    assert rates["grow_selfserve"]["n"] == 1
    assert rates["grow_selfserve"]["conversion_rate"] == 1.0


def test_learning_loop_shrinks_prior_toward_observed(tmp_path):
    from retention_agent import config, learning
    store = Store(tmp_path / "s.db")
    r = store.start_run("run")
    base = config.SAVE_RATE
    # no outcomes yet -> no change (shrinkage to prior)
    assert learning.learned_priors(store) == {}
    # log 3 successful reengage win-backs
    for i in range(3):
        store.record_outcome(f"ACC-{i}", r, "reengage", None, responded=True, converted=True, gmv_delta=1000)
    learned = learning.learned_priors(store, k=20)
    old, new, n, causal = learned["SAVE_RATE"]
    assert old == base and n == 3 and causal is False    # no holdout arm yet
    assert base < new < 1.0                    # moved toward observed 1.0, but shrunk by k
    assert abs(new - (base * 20 + 1.0 * 3) / 23) < 1e-3   # 4-dp rounded in _shrink
    # apply writes it onto config; restore after so we don't leak into other tests
    try:
        learning.apply(store, k=20)
        assert config.SAVE_RATE == new
    finally:
        config.SAVE_RATE = base


def test_learning_uses_causal_lift_when_holdout_present(tmp_path):
    from retention_agent import config, learning
    store = Store(tmp_path / "s.db")
    r = store.start_run("run")
    base = config.SAVE_RATE
    # treated: 4/4 converted (rate 1.0); holdout control: 1/4 recovered on their
    # own (rate 0.25). Causal lift = 0.75, NOT the raw treated 1.0.
    for i in range(4):
        store.record_outcome(f"T-{i}", r, "reengage", None, treated=True, converted=True)
    for i in range(4):
        store.record_outcome(f"H-{i}", r, "reengage", None, treated=False, converted=(i == 0))
    rates = store.realized_rates()["reengage"]
    assert rates["causal"] is True and rates["causal_rate"] == 0.75 and rates["conversion_rate"] == 1.0
    _, new, n, causal = learning.learned_priors(store, k=20)["SAVE_RATE"]
    assert causal is True
    # prior blends toward the causal 0.75, not the confounded 1.0
    assert abs(new - (base * 20 + 0.75 * 4) / 24) < 1e-3


# --- idempotency contract -------------------------------------------------
def _upsert(store, a, run_id):
    d = decide(a, classify(a))
    store.upsert(a, d, "draft", False, run_id)


def test_store_idempotent_and_dedupes(tmp_path):
    store = Store(tmp_path / "s.db")
    accts = [acct(account_id=f"ACC-{i}", fingerprint=f"fp{i}") for i in range(10)]

    # run 1: all new
    r1 = store.start_run("run1")
    split = store.diff(accts)
    assert len(split["new"]) == 10 and not split["changed"] and not split["unchanged"]
    for a in split["new"]:
        _upsert(store, a, r1)
    store.commit()

    # run 2: identical batch -> all unchanged, nothing to write
    split = store.diff(accts)
    assert not split["new"] and not split["changed"] and len(split["unchanged"]) == 10

    # run 3: one account's data changed (new fingerprint) + one brand new account
    accts[0] = acct(account_id="ACC-0", fingerprint="CHANGED")
    accts.append(acct(account_id="ACC-99", fingerprint="fp99"))
    split = store.diff(accts)
    assert [a.account_id for a in split["new"]] == ["ACC-99"]
    assert [a.account_id for a in split["changed"]] == ["ACC-0"]
    assert len(split["unchanged"]) == 9

    r3 = store.start_run("run3")
    for a in split["new"] + split["changed"]:
        _upsert(store, a, r3)
    store.commit()

    # no duplicates: 11 distinct accounts, 11 rows
    assert store.counts()["accounts"] == 11
    rows = store.all_accounts()
    assert len({r["account_id"] for r in rows}) == len(rows) == 11


# --- scale ----------------------------------------------------------------
def test_scale_30k_under_budget():
    n = 30_000
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "account_id": [f"ACC-{i}" for i in range(n)],
        "ownership": rng.choice(["Account Managed", "Self Serve"], n),
        "buyer_persona": "Reseller", "region": "EU", "country": "Germany",
        "account_status": "Active", "tenure_months": rng.integers(1, 40, n),
        "orders_6m": rng.integers(1, 20, n), "manual_orders": rng.integers(0, 10, n),
        "self_serve_orders": rng.integers(0, 10, n),
        "app_active_days_6m": rng.integers(0, 60, n), "pdp_views_6m": rng.integers(0, 500, n),
        "make_an_offer_6m": rng.integers(0, 10, n), "chat_threads": rng.integers(0, 30, n),
        "video_call_requests": 0, "handpick_orders": rng.integers(0, 5, n),
        "bundle_orders": rng.integers(0, 5, n), "bundle_gmv_share_pct": rng.integers(0, 100, n),
        "broker_reliance_pct": rng.integers(0, 100, n), "gmv_total_6m": rng.integers(50, 50000, n),
        **{m: rng.integers(0, 8000, n) for m in MONTHS},
    })
    t0 = time.time()
    accts = to_accounts(clean(df))
    decisions = [decide(a, classify(a)) for a in accts]
    elapsed = time.time() - t0
    assert len(decisions) == n
    assert elapsed < 20  # generous ceiling; typically a few seconds


def test_store_path_scale_30k(tmp_path):
    # exercise the persistence path (diff + upsert), not just the compute path
    store = Store(tmp_path / "s.db")
    accts = [acct(account_id=f"ACC-{i}", fingerprint=f"fp{i}") for i in range(30_000)]
    r = store.start_run("scale")
    t0 = time.time()
    split = store.diff(accts)                       # first run: all new
    for a in split["new"]:
        store.upsert(a, decide(a, classify(a)), "d", False, r)
    store.commit()
    write_elapsed = time.time() - t0
    # second run: identical batch -> diff must classify all as unchanged, fast
    t1 = time.time()
    split2 = store.diff(accts)
    diff_elapsed = time.time() - t1
    assert len(split2["unchanged"]) == 30_000 and not split2["new"]
    assert store.counts()["accounts"] == 30_000    # no duplicates
    assert write_elapsed < 30 and diff_elapsed < 5
