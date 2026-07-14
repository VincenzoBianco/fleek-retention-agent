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
def test_feature_handpick_only_gets_bundles():
    a = acct(bundle_gmv_share_pct=10, handpick_orders=3)
    assert choose_feature(a)[0] == "bundles"


def test_feature_offers_not_converting_gets_video():
    a = acct(bundle_gmv_share_pct=80, handpick_orders=0, make_an_offer_6m=5, orders_6m=2)
    assert choose_feature(a)[0] == "video"


def test_feature_heavy_browser_gets_chat():
    a = acct(bundle_gmv_share_pct=80, handpick_orders=0, make_an_offer_6m=0,
             pdp_views_6m=300, chat_threads=0, orders_6m=8)
    assert choose_feature(a)[0] == "chat"


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
