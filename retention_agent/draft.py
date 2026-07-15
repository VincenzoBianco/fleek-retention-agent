"""Draft the next best action so whoever runs it (an AM or an agent) can just act.

Two paths, same interface:
- heuristic_draft() — templated, instant, no API key. This is the default and
  what runs in tests, offline, and at 30k-account scale. The drafts are real
  and personalised on the account's numbers, not placeholders.
- llm_draft() — when --llm is on and a key is present, Claude rewrites the draft
  using the play's markdown guidance as its brief. Cached by fingerprint so a
  re-run never redrafts an unchanged account.

Channel decides the form: whatsapp/in_app -> a short message to send; call -> a
call-prep note for the human.
"""
from __future__ import annotations

from .models import Account, Decision
from .llm import LLM


def _ctx(a: Account) -> str:
    persona = (a.buyer_persona or "buyer").lower()
    where = a.country or a.region or "unknown region"
    return (f"{a.account_id} · {persona} · {where} · £{a.gmv_total_6m:,.0f}/6mo · "
            f"{a.orders_6m} orders · AOV £{a.aov:,.0f} · reliance {a.broker_reliance:.0f}%")


# We do NOT have per-account product category in this dataset, so drafts never
# claim to know what an account buys — they reference persona and order size
# ("your", "the volumes you order"), not a specific category. If category data
# were ingested later, this is the one place the copy would get more specific.
def _who(a: Account) -> str:
    return "shop" if (a.buyer_persona or "").lower() == "retailer" else "store"


def _scale(a: Account) -> str:
    """A size-aware phrase so 100+ growth messages aren't word-for-word identical."""
    if a.aov >= 1000:
        return "the larger volumes you order"
    if a.aov >= 300:
        return "your usual order size"
    return "smaller test runs"


# --------------------------------------------------------------------------
# Heuristic templates
# --------------------------------------------------------------------------
def heuristic_draft(a: Account, d: Decision) -> str:
    if d.play == "reengage":
        if d.health == "dormant":
            gap = "no order in the last quarter after a steady run"
            opener = "acknowledge the gap directly (\"noticed you've paused since the autumn\")"
        else:
            gap = "orders tailing off (run-rate sliding)"
            opener = "note the slowdown gently, not the total (\"seen a few quieter weeks\")"
        who = "their shop's footfall/season" if (a.buyer_persona or "").lower() == "retailer" else "their resale velocity"
        broker = (" NB high broker-reliance — check whether this is the AM easing off vs real demand loss."
                  if a.broker_reliance >= 50 else "")
        return (f"Call note — {_ctx(a)}. {gap.capitalize()}. Open by {opener}; ask what changed for "
                f"{who} (supply gap, pricing, a bad last order, or drifted to an offline wholesaler) and "
                f"bring one concrete hook — fresh stock in their lines. Don't sell hard.{broker}")

    if d.play == "onboard":
        return (f"Onboarding note — {_ctx(a)}. {a.tenure_months:.0f} months in, {a.orders_6m} order(s), "
                f"no AM contact yet. Goal: activation, not a big basket. Welcome call — offer to help "
                f"source and place their next 1-2 orders, walk the app, and set an expectation for cadence. "
                f"Warm and human; this is the first impression. Aim for a confident second order.")

    if d.play == "migrate_to_selfserve":
        if d.channel == "whatsapp":  # warm
            return ("Hi! Noticed you've been browsing the app — want me to set up a ready-to-checkout "
                    "basket of your usual lines so you get first pick the moment new stock lands? "
                    "One tap to reorder, and I'm still here whenever you need me.")
        return (f"Call note — {_ctx(a)}. {a.broker_reliance:.0f}% of orders are AM-placed and they "
                f"barely touch the app. Offer a 10-min walkthrough; pre-load their usual SKUs so "
                f"self-serve is easier than messaging me. Frame as 'first pick of new stock'; keep me "
                f"as safety net for the first order or two. Don't imply the AM is going away.")

    if d.play == "grow_selfserve":
        scale, who = _scale(a), _who(a)
        return {
            "bundles": (f"Hi! You've been buying handpicks — want me to put together a starter bundle at "
                        f"{scale}? Better price per unit and it saves you the picking. Can have one ready today."),
            "build_a_bundle": (f"Hi! Want to try a build-a-bundle? You set the mix and we curate it to spec — "
                               f"a clean way to scale past your ~£{a.aov:,.0f} orders without dropping the "
                               f"quality you pick for your {who}. First one ready whenever you are."),
            "video": (f"Hi! Saw you've put in {a.make_an_offer_6m:.0f} offers recently — fancy a quick 15-min "
                      f"video viewing of this week's fresh stock? Easier to lock in the pieces you want at "
                      f"{scale} and sort pricing live on the call."),
            "chat": (f"Hi! You've been browsing a fair bit ({a.pdp_views_6m:.0f} views lately) — want me to "
                     f"drop a shortlist of what's just landed at {scale} into a chat, so you don't have to "
                     f"hunt for it?"),
        }.get(d.feature or "", "Hi! A few new pieces just landed that suit what you order — worth a look?")

    return ""


# --------------------------------------------------------------------------
# LLM path
# --------------------------------------------------------------------------
def _system(guidance: str, channel: str) -> str:
    form = ("Write a WhatsApp message to send to the buyer (max 55 words, warm, plain, "
            "no emoji spam, GBP)." if channel in ("whatsapp", "in_app")
            else "Write a short call-prep note for the account manager (bullet-style, what to say and why).")
    return ("You are an account manager at Fleek, a B2B marketplace for secondhand fashion. "
            "You draft outreach that a colleague can send or act on as-is. Never invent specific "
            "product names, prices, or stock you don't know. " + form
            + "\n\nPlay brief:\n" + guidance)


def llm_draft(a: Account, d: Decision, llm: LLM, guidance: str) -> str | None:
    facts = (f"Account: {_ctx(a)}\nSegment: {d.segment} ({d.health}). "
             f"Play: {d.play}. Feature: {d.feature or 'n/a'}.\n"
             f"Why this account: {d.reason}\nIntended action: {d.action}")
    return llm.complete(_system(guidance, d.channel or "whatsapp"), facts)


def make_draft(a: Account, d: Decision, llm: LLM | None, guidance: str) -> tuple[str, bool]:
    """Return (draft_text, used_llm). Falls back to the template on any miss."""
    if llm is not None and llm.enabled:
        out = llm_draft(a, d, llm, guidance)
        if out:
            return out, True
    return heuristic_draft(a, d), False
