"""Agentic path tests.

The live tool-calling loop needs an API key, so it isn't exercised here. What IS
tested — and is where the risk lives — is everything deterministic around the
model: the EV extraction still matches the reference maths, the tools expose
quantities (not verdicts), the money/guardrail assembly is correct, and the whole
thing falls back to the pre-agent engine identically when the model is off.
"""
from __future__ import annotations

from retention_agent import config, ev
from retention_agent.agent import Analyst
from retention_agent.models import Account
from retention_agent.plays import decide, is_holdout
from retention_agent.segment import classify
from retention_agent.tools import ToolKit


def acct(**kw) -> Account:
    d = dict(account_id="ACC-X", ownership="Account Managed", gmv_total_6m=10000, orders_6m=10,
             tenure_months=12, monthly_gmv=[3000, 3000, 3000, 500, 0, 0], broker_reliance=70,
             transaction_mode="hybrid", app_active_days_6m=1, pdp_views_6m=2, make_an_offer_6m=0,
             chat_threads=0, handpick_orders=0, bundle_orders=5, bundle_gmv_share_pct=80,
             momentum_pct=-80, recent_gmv=0, aov=1000, fingerprint="fp")
    d.update(kw)
    return Account(**d)


def small_book(anchor: Account, n: int = 20) -> list[Account]:
    """A book where `anchor` is a small fraction of GMV (not a key account)."""
    others = [acct(account_id=f"O-{i}", fingerprint=f"o{i}", gmv_total_6m=10000,
                   broker_reliance=0, transaction_mode="self_serve") for i in range(n)]
    return [anchor] + others


def _disabled_analyst() -> Analyst:
    an = Analyst()
    an.llm.enabled = False          # force the offline path regardless of local env
    return an


# --- EV extraction still matches the reference maths ----------------------
def test_ev_extraction_matches_reference():
    a = acct(gmv_total_6m=10000, monthly_gmv=[3000, 3000, 3000, 500, 0, 0])
    at_risk, evv, ptype = ev.reengage_prize(a)
    assert at_risk == 10000 and evv == round(config.SAVE_RATE * 10000, 0)
    on_human, evm, _ = ev.migrate_prize(a, warm=True)
    assert on_human == 7000 and evm == round(config.CONVERT_RATE_WARM * 7000 * config.MIGRATION_EXPANSION, 0)
    up, evg, _ = ev.grow_prize(a, "video")
    assert up == round(10000 * config.GROWTH_UPLIFT_PCT["video"], 0) and evg == up


# --- tools return quantities, not the categorical verdict -----------------
def test_tools_expose_quantities_not_verdicts():
    a = acct()
    kit = ToolKit(a, small_book(a))
    sig = kit.account_signals()
    assert "segment" not in sig and "health" not in sig          # no verdict leaked
    assert sig["transaction_mode"]["broker_reliance_pct_recomputed"] == 70
    trend = kit.trend_signals()
    assert "cagr_monthly_pct" in trend and "activity_ratio_last4_vs_first3" in trend
    assert "dormant" not in trend and "declining" not in trend    # agent decides health
    assert kit.expected_value("reengage")["expected_value"] == ev.reengage_prize(a)[1]
    assert "error" in kit.expected_value("not_a_play")


# --- the deterministic assembly around the model -------------------------
def test_assemble_sizes_money_and_marks_agent():
    a = acct(account_id="R", fingerprint="fp")
    an = _disabled_analyst()
    out = {"segment": "broker_reliant", "health": "dormant", "play": "reengage",
           "channel": "call", "action": "call them", "reason": "gone quiet",
           "rationale": "signals agree"}
    d = an._assemble(a, small_book(a), out)
    assert d.decided_by == "agent" and d.agent_rationale == "signals agree"
    # money is deterministic, computed from ev.py — not from the model
    assert (d.prize_gmv, d.expected_value) == ev.reengage_prize(a)[:2]
    assert d.priority == d.expected_value


def test_assemble_key_account_guard_suppresses_play():
    whale = acct(account_id="WHALE", gmv_total_6m=500000, fingerprint="fp")
    book = small_book(whale)          # whale dominates the book (> 10% of GMV)
    an = _disabled_analyst()
    out = {"segment": "broker_reliant", "health": "healthy",
           "play": "migrate_to_selfserve", "reason": "big broker account"}
    d = an._assemble(whale, book, out)
    assert d.play is None                         # guard overrides the agent
    assert "KEY ACCOUNT" in d.reason


def test_assemble_respects_deterministic_holdout():
    held = next(f"ACC-{i}" for i in range(500) if is_holdout(f"ACC-{i}"))
    a = acct(account_id=held, fingerprint="fp")
    an = _disabled_analyst()
    out = {"segment": "self_serve_growth", "health": "healthy",
           "play": "grow_selfserve", "feature": "chat", "reason": "headroom"}
    d = an._assemble(a, small_book(a), out)
    assert d.holdout is True and "HOLDOUT" in d.reason
    assert d.play == "grow_selfserve"             # intended play kept for lift measurement


def test_assemble_fills_missing_growth_feature():
    a = acct(account_id="G", broker_reliance=0, transaction_mode="self_serve",
             bundle_gmv_share_pct=10, handpick_orders=3, aov=900, fingerprint="fp")
    an = _disabled_analyst()
    out = {"segment": "self_serve_growth", "health": "healthy",
           "play": "grow_selfserve", "feature": None, "reason": "grow it"}
    d = an._assemble(a, small_book(a), out)
    assert d.feature in config.GROWTH_UPLIFT_PCT   # backfilled from the decision tree


# --- offline: identical to the pre-agent engine ---------------------------
def test_agent_disabled_falls_back_to_deterministic_identically():
    a = acct(account_id="R", fingerprint="fp")
    an = _disabled_analyst()
    dg = an.evaluate(a, small_book(a))
    dd = decide(a, classify(a))
    assert dg.decided_by == "deterministic"
    assert (dg.play, dg.expected_value, dg.prize_type, dg.reason) == \
           (dd.play, dd.expected_value, dd.prize_type, dd.reason)


def test_run_agent_returns_none_when_disabled():
    an = _disabled_analyst()
    assert an.llm.run_agent("s", "u", [], lambda n, a: {}, "submit_decision") is None
