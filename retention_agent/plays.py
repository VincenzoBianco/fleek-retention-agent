"""Play selection and next-best-action.

Plays are authored as markdown skills in data/plays/ (frontmatter + guidance).
This module loads them and contains the deterministic mechanics: which play
fires for an account, the concrete action, the feature to nudge, the channel,
and the £ prize used to rank the queue. Drafting the actual message lives in
draft.py.

Precedence is a commercial judgement call, encoded once here:
    reengage  >  migrate_to_selfserve  >  grow_selfserve  >  leave alone
Stabilise a churning material account before trying to migrate or upsell it.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from . import config
from .models import Account, Decision
from .segment import SegmentResult


@lru_cache(maxsize=1)
def load_plays() -> dict[str, dict]:
    """Parse each data/plays/*.md into {name: {**frontmatter, guidance}}."""
    plays: dict[str, dict] = {}
    for md in sorted(config.PLAYS_DIR.glob("*.md")):
        text = md.read_text()
        fm, body = _split_frontmatter(text)
        fm["guidance"] = body.strip()
        plays[fm.get("name", md.stem)] = fm
    return plays


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        return yaml.safe_load(fm) or {}, body
    return {}, text


# --------------------------------------------------------------------------
# Feature selection for the grow play (the "which nudge" decision tree)
# --------------------------------------------------------------------------
def choose_feature(a: Account) -> tuple[str, str]:
    """Return (feature, reason). One feature per account, matched to its blocker.

    Note the handpick split: handpick buyers are the higher-AOV cohort here, so a
    valuable one is scaled via build-a-bundle (keeps curation), not pushed onto
    generic bundles (which would drop their AOV). Only the price-led, low-AOV
    handpick buyer gets bundles."""
    if a.make_an_offer_6m >= config.VIDEO_OFFER_MIN and a.orders_6m <= 3:
        return "video", f"{a.make_an_offer_6m:.0f} offers but only {a.orders_6m} orders — a call closes it"
    if a.pdp_views_6m >= config.CHAT_VIEWS_MIN and a.chat_threads <= config.CHAT_THREADS_MAX:
        return "chat", f"{a.pdp_views_6m:.0f} views but {a.chat_threads:.0f} chats — open a conversation"
    if a.bundle_gmv_share_pct <= config.HANDPICK_ONLY_BUNDLE_SHARE and a.handpick_orders >= 1:
        if a.aov >= config.HANDPICK_HIGH_AOV:
            return "build_a_bundle", f"handpick-led, £{a.aov:,.0f} AOV — scale via curated bundles, keep the AOV"
        return "bundles", f"handpick-led, £{a.aov:,.0f} AOV — a volume bundle play fits a price-led buyer"
    return "build_a_bundle", "engaged with headroom — a curated bundle is the next step"


# --------------------------------------------------------------------------
# Play selection
# --------------------------------------------------------------------------
def decide(a: Account, seg: SegmentResult) -> Decision:
    material = a.gmv_total_6m >= config.MATERIAL_ACCOUNT_GMV
    base = dict(account_id=a.account_id, segment=seg.segment, health=seg.health,
                fingerprint=a.fingerprint)

    # 1. Retention guardrail — churning material account, any segment.
    #    At-risk is FORWARD exposure — the run-rate we're losing, projected 6
    #    months — not the full lifetime GMV (which overstates: an account still
    #    ordering £1.8k/mo hasn't got its whole £18k window "at risk"). Capped at
    #    what they actually spent in the window. EV discounts by the win-back rate.
    if material and seg.health in ("dormant", "declining"):
        prior_monthly = sum(a.monthly_gmv[:3]) / 3
        recent_monthly = sum(a.monthly_gmv[-3:]) / 3
        lost_monthly = max(0.0, prior_monthly - recent_monthly)
        at_risk = round(min(lost_monthly * 6, a.gmv_total_6m), 0)
        ev = round(config.SAVE_RATE * at_risk, 0)
        gap = "silent for a quarter" if seg.health == "dormant" else f"run-rate down £{lost_monthly:,.0f}/mo"
        # A broker-reliant account's drop may be the AM easing off, not the
        # customer disengaging — flag it so the call checks the right actor.
        actor = (" — confirm AM cadence vs genuine demand before the call"
                 if a.broker_reliance >= config.BROKER_RELIANCE_HIGH else "")
        return Decision(**base, play="reengage", channel="call",
                        action="Win-back call: find what changed, bring one concrete hook (fresh stock in their lines)" + actor,
                        reason=f"£{at_risk:,.0f} of forward GMV at risk — {gap}",
                        prize_type="GMV at risk (fwd)", prize_gmv=at_risk, gmv_at_stake=at_risk,
                        expected_value=ev, priority=ev)

    # 2. Migrate broker-reliant material accounts. NB the £ on a human is NOT at
    #    risk — that spend continues if we do nothing. The prize of migrating is
    #    modest expansion on the converted spend, discounted by conversion rate.
    if seg.segment == "broker_reliant":
        if not material:
            return Decision(**base, play=None,
                            reason=f"broker-reliant but only £{a.gmv_total_6m:,.0f} — below migration floor, batch later")
        on_a_human = round(a.gmv_total_6m * a.broker_reliance / 100, 0)
        convert = config.CONVERT_RATE_WARM if seg.subtype == "warm" else config.CONVERT_RATE_COLD
        ev = round(convert * on_a_human * config.MIGRATION_EXPANSION, 0)
        if a.gmv_total_6m >= config.WHALE_GMV:
            # Too much spend to risk on a self-serve nudge — hand over in phases
            # with the AM shadowing, so a wobble doesn't cost the account.
            action = ("Phased, AM-shadowed handover: co-place the next 1-2 orders in-app together, "
                      "then hand the reins — keep the AM on every order until self-serve sticks")
            channel = "call"
        elif seg.subtype == "warm":
            action = "Nudge next reorder in-app: pre-load usual lines into a ready basket"
            channel = "whatsapp"
        else:
            action = "Book a 10-min guided first order; pre-load usual lines so the app beats messaging"
            channel = "call"
        # Reliance drives this whole play, so if the reported figure disagreed
        # with the order counts, say so — the decision uses the recomputed one.
        conf = " · reliance recomputed from counts (reported disagreed)" if a.reliance_discrepancy else ""
        return Decision(**base, play="migrate_to_selfserve", channel=channel, action=action,
                        reason=f"£{on_a_human:,.0f} of GMV riding on a human ({a.broker_reliance:.0f}% broker-placed); {seg.reasons[-1]}{conf}",
                        prize_type="GMV on a human", prize_gmv=on_a_human, gmv_at_stake=on_a_human,
                        expected_value=ev, priority=ev)

    # 3. Grow self-serve accounts with headroom. Prize is MODELLED uplift (a
    #    conservative slice of the engagement premium), not money in hand.
    if seg.segment == "self_serve_growth":
        feature, why = choose_feature(a)
        uplift = round(a.gmv_total_6m * config.GROWTH_UPLIFT_PCT[feature], 0)
        return Decision(**base, play="grow_selfserve", channel="whatsapp", feature=feature,
                        action=f"Nudge {feature.replace('_', ' ')}: {_feature_offer(feature)}",
                        reason=why,
                        prize_type="modelled uplift", prize_gmv=uplift, gmv_at_stake=uplift,
                        expected_value=uplift, priority=uplift)

    # 4. Everyone else — healthy and self-serving, or assisted-but-fine. Leave alone.
    return Decision(**base, play=None,
                    reason=f"{seg.segment} — healthy and self-serving, no action needed")


def _feature_offer(feature: str) -> str:
    return {
        "bundles": "send a starter bundle in their top category",
        "build_a_bundle": "offer a build-a-bundle tuned to what they browse",
        "video": "offer a 15-min video viewing of fresh stock",
        "chat": "open a chat with a curated shortlist",
    }[feature]


def decide_all(accounts: list[Account], segs: dict[str, SegmentResult]) -> list[Decision]:
    return [decide(a, segs[a.account_id]) for a in accounts]
