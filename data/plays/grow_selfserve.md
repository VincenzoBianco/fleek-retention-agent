---
name: grow_selfserve
label: Grow self-serve spend
problem: grow_selfserve_spend
channel: whatsapp
priority_metric: estimated_gmv_uplift
---

## When to fire

The account **buys for itself** (low reliance) and has visible **headroom** —
high intent but low spend, or it only buys handpicks — and isn't churning. The
job is to grow spend while keeping it self-serving, by getting it onto the one
feature most likely to move its basket.

## Feature selection (which nudge)

One feature per account, picked by behaviour, in this code order (see
`plays.choose_feature`):

1. **video** — making lots of offers but not converting (offers high, orders
   low). They're stuck on price or trust; a quick video call closes it.
2. **chat** — heavy browser who hasn't started a conversation. Open a chat to
   surface stock and answer the question keeping them from buying.
3. **handpick-led buyer → split by value.** In this book handpick buyers have
   the *higher* AOV (£682 vs £281 for bundle-led), so we don't dilute a valuable
   one with generic bundles:
   - **build_a_bundle** if their AOV is high — scale volume, keep the curation.
   - **bundles** if their AOV is low — a volume play for a price-led buyer.
4. **build_a_bundle** (fallback) — engaged with headroom; a curated bundle is
   the natural next step.

## Offer

Feature-specific: a starter bundle in their category; a 15-min video viewing of
fresh stock; a chat thread with a curated shortlist; or a build-a-bundle tuned
to what they browse.

## Message guidance

Anchor on what they already do (the categories they view/buy), then introduce
the one feature as the obvious next step — not a menu. Make the first use
trivial (a pre-built bundle, a booked slot, a ready shortlist). Keep it short;
these are self-serve buyers who don't want hand-holding.
