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
    """Return (feature, reason). One feature per account, ordered by lever size."""
    if a.bundle_gmv_share_pct <= config.HANDPICK_ONLY_BUNDLE_SHARE and a.handpick_orders >= 1:
        return "bundles", f"handpick-only ({a.bundle_gmv_share_pct:.0f}% bundle) — bundles lift basket size"
    if a.make_an_offer_6m >= config.VIDEO_OFFER_MIN and a.orders_6m <= 3:
        return "video", f"{a.make_an_offer_6m:.0f} offers but only {a.orders_6m} orders — a call closes it"
    if a.pdp_views_6m >= config.CHAT_VIEWS_MIN and a.chat_threads <= config.CHAT_THREADS_MAX:
        return "chat", f"{a.pdp_views_6m:.0f} views but {a.chat_threads:.0f} chats — open a conversation"
    return "build_a_bundle", "engaged with headroom — a custom bundle is the next step"


# --------------------------------------------------------------------------
# Play selection
# --------------------------------------------------------------------------
def decide(a: Account, seg: SegmentResult) -> Decision:
    material = a.gmv_total_6m >= config.MATERIAL_ACCOUNT_GMV
    base = dict(account_id=a.account_id, segment=seg.segment, health=seg.health,
                fingerprint=a.fingerprint)

    # 1. Retention guardrail — churning material account, any segment.
    if material and seg.health in ("dormant", "declining"):
        at_risk = round(a.gmv_total_6m, 0)
        gap = "silent for a quarter" if seg.health == "dormant" else f"spend down {abs(a.momentum_pct or 0):.0f}%"
        return Decision(**base, play="reengage", channel="call",
                        action="Win-back call with a concrete hook (fresh stock in their category)",
                        reason=f"£{at_risk:,.0f} at risk — {gap}",
                        priority=at_risk, gmv_at_stake=at_risk)

    # 2. Migrate broker-reliant material accounts.
    if seg.segment == "broker_reliant":
        if not material:
            return Decision(**base, play=None,
                            reason=f"broker-reliant but only £{a.gmv_total_6m:,.0f} — below migration floor, batch later")
        on_a_human = round(a.gmv_total_6m * a.broker_reliance / 100, 0)
        if seg.subtype == "warm":
            action = "Nudge next reorder in-app: pre-load usual SKUs into a ready basket"
            channel = "whatsapp"
        else:
            action = "Book a 10-min guided first order; pre-load usual SKUs to make the app easier than messaging"
            channel = "call"
        return Decision(**base, play="migrate_to_selfserve", channel=channel, action=action,
                        reason=f"£{on_a_human:,.0f} of GMV riding on a human ({a.broker_reliance:.0f}% broker-placed); {seg.reasons[-1]}",
                        priority=on_a_human, gmv_at_stake=on_a_human)

    # 3. Grow self-serve accounts with headroom.
    if seg.segment == "self_serve_growth":
        feature, why = choose_feature(a)
        uplift = round(a.gmv_total_6m * config.UPLIFT_FACTORS[feature], 0)
        # a floor so tiny accounts still show a sensible prize to rank by
        uplift = max(uplift, config.UPLIFT_FACTORS[feature] * 500)
        return Decision(**base, play="grow_selfserve", channel="whatsapp", feature=feature,
                        action=f"Nudge {feature.replace('_', ' ')}: {_feature_offer(feature)}",
                        reason=why,
                        priority=uplift, gmv_at_stake=uplift)

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
