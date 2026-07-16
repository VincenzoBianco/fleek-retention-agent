# Portfolio overview — principles & strategy

> **This file is yours to edit.** It's the strategic context every account
> decision is made against — the analyst agent loads it verbatim on every account
> via the `portfolio_context` tool. Change the priorities here and the book
> re-decides in line with them, no code change. The facts below were extracted
> from the 300-account portfolio; update them when the book moves.

## The book in one line

A **short head and a long tail** whose revenue runs on **people, not the
product**: a handful of accounts and a single buying mechanism (an AM placing
orders) carry most of the GMV. The whole strategy follows from that.

## Key facts from the data (reason from these)

1. **The business runs on brokering — ~80% of GMV depends on an AM placing
   orders.** The 74 genuinely broker-reliant accounts are only **25% of the book
   but 80.7% of GMV**. This is the single most important fact: revenue is
   concentrated where a human is the buying mechanism — the "we're the bottleneck,
   it doesn't scale" risk made concrete. It reframes **migration from a tidy-up
   task into the core scalability lever**. Everything else is secondary to moving
   that GMV onto the product without losing it.

2. **The hybrid tier is the prize — hiding in the middle.** Cut the book by
   manual-order share into self-serve (<25%) / hybrid (25–75%) / manual (>75%):
   **hybrid is ~25% of accounts but ~70% of GMV and the highest AOV** (£1,002 vs
   £631 manual, £342 self-serve). Hybrids already self-serve part of the time —
   they've **proven they can use the product**, so they're the highest-value,
   lowest-friction migration target. Don't start with the fully-manual whales
   (harder, fewer, lower AOV) or the self-serve minnows — **go for the hybrids**
   (they're the `warm` migrate subtype).

3. **The ownership label lies — read behaviour.** 128 of 210 "account-managed"
   accounts already buy for themselves; only **74 are truly broker-reliant**.
   Chasing the label wastes effort on 128 accounts that need nothing and misses
   the real target. The supplied `broker_reliance_pct` even disagreed with actual
   order counts on **85/300 accounts**, so reliance is recomputed from behaviour.
   **Never treat `ownership_label` as an input to the decision.**

4. **Extreme single-account concentration — one whale is a fifth of the book.**
   ACC-001 alone = **20% of GMV**; top 10 = 51%; top 30 = 72%; the other 270
   accounts = 28%. Implication: **the head gets named, human-owned, white-glove
   treatment (never automated); the tail is what the agent runs.** The tool's job
   is to free the AM's scarce attention for the head. (Enforced: any account ≥10%
   of book GMV is blocked from an automated play — see guardrails.)

5. **There's an onboarding gap — the AM shows up ~12 months too late.** All **63
   accounts under 5 months old have zero manual orders and stall at ~1 order
   (£242)**. Manual involvement and GMV both climb ~6× with tenure, so the AM
   relationship forms organically and late, after value has compounded — new
   accounts are left to sink-or-swim. Proactive early CS is both a **retention
   play** (fewer early deaths) and a **growth accelerant** (faster ramp). It
   completes the lifecycle: **activate high-touch → wean to self-serve → grow.**

6. **Growth levers are behaviour-specific — and one instinct was backwards.**
   Handpick buyers **out-spend** bundle buyers per order (£682 vs £281), so the
   naive "push handpick-only accounts to bundles" move would **dilute your best
   buyers' AOV**. The right lever for a *valuable* handpick account is
   **build-a-bundle** (scale volume, keep curation); generic **bundles** are for
   the *price-led*. Separately, **engaged self-serve accounts spend ~2×**
   (chat/video) — correlational, so engagement features are the self-serve growth
   handle, and a holdout is needed before calling the lift causal.

7. **Churn is rare but concentrated — materiality is everything.** With **158
   single-order accounts**, most "quiet" accounts are just lumpy, not churning.
   Real churn is a handful of material accounts — e.g. **ACC-002 (£70k, silent
   since October)**. **Scale the alarm by money at risk:** a £3k account going
   quiet is noise; a £70k account going quiet is an emergency even off two orders.
   Flag churn on **two agreeing signals** (declining CAGR *and* dropping activity),
   gated by value — never a blanket rule.

8. **Don't conflate three kinds of money.** Migration's "£ on a human" (~£364k) is
   **exposure, not at-risk** — that spend continues if you do nothing. Reengage's
   ~£120k is **genuinely at-risk**. Growth is **speculative upside**. They belong
   on different axes: a **churn-horizon queue** (what to touch today) and a
   **scalability-horizon view** (where the strategic bet is). Migration ranks low
   on the first and highest on the second — collapsing them into one "£ at stake"
   number would mislead.

## What this means for prioritisation

**Play precedence:** `reengage › onboard › migrate_to_selfserve › grow_selfserve
› leave alone`. Stabilise the churning, activate the new, wean the reliant, grow
the healthy, and deliberately leave good accounts alone.

**Two lenses, kept separate (fact 8):**
- *Operational (the daily queue):* rank on risk-adjusted expected value over the
  next quarter — who to touch today. Migration is deliberately down-weighted here
  because that GMV isn't leaving next month.
- *Strategic (the standing bet):* broker-dependency concentration (fact 1).
  Migration is the biggest long-run lever even when its short-horizon EV is modest.

**Where to aim migration (facts 1, 2, 4):** the hybrid/warm accounts first — proven
product users, highest AOV. Manual/cold accounts need a guided, AM-shadowed
handover. The largest (whales) get a phased handover, never an automated nudge.

## Guardrails (do not cross)

- **Key accounts are relationships, not queue items (fact 4).** Any account ≥10%
  of book GMV is human-owned — no automated play. (Enforced deterministically.)
- **Behaviour over labels (fact 3).** Ownership is never a decision input.
- **Money isn't yours to set.** The £ figures are computed deterministically; you
  choose the play, the tool sizes it.
- **Don't invent data.** No product categories, prices, or stock — reason only
  from the tool signals.

## Risk appetite

- **Head vs tail (fact 4):** the top ~10 accounts are white-glove and human-owned;
  be conservative — flag for a human rather than auto-nudge. The long tail is where
  the agent should act decisively.
- **Migration on hybrids (fact 2):** be assertive — a light in-app nudge is low
  downside. On whales, be cautious: the cost of a wobble outweighs the modest
  modelled expansion, so hand over in phases with the AM shadowing.
- **Onboarding (fact 5):** lean in — no new account should stall after one order.
- **Churn (fact 7):** high-value silence flags on its own; small lumpy accounts
  need a broken rhythm before you cry wolf.
