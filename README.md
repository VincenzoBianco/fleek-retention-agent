# Fleek Retention Agent

A tool for Fleek's GTM–Retention team: given a portfolio of account-managed and
self-serve buyers, it segments the book from actual behaviour (not the ownership
label), decides a next-best-action per account, and drafts the outreach — so an
account manager (or an agent) can just act on it. Re-running it against a new
batch of accounts updates the book without reprocessing or duplicating anything
it's already seen.

## The two problems

1. **Reduce brokering reliance** (account-managed). Some account-managed
   customers barely touch the product themselves — the AM is buying for them
   (high `broker_reliance_pct`, low `app_active_days_6m` / `pdp_views_6m` /
   `make_an_offer_6m`). Find them and move them onto self-serve without losing
   the spend.
2. **Grow self-serve spend** (self-serve). Find self-serve accounts with
   headroom — high intent, low spend, or handpick-only buying — and decide
   which feature to nudge them toward: chat, bundles, video calls, or
   build-a-bundle.

## Data

Not committed — Fleek's portfolio workbook is real, anonymised customer data,
so it stays out of git history even though this repo is public. Drop the file
Fleek sent you at `data/raw/Fleek_-_Retention_Case_Study_-_Portfolio_Data.xlsx`
before running. It has two tabs: 300 accounts (`Accounts`) plus a 50-account
second batch (`new_accounts`) for testing incremental ingestion, and a
`Readme` tab with the column dictionary.

## Architecture (in progress)

```
retention_agent/
  ingest.py         load both tabs, clean blanks/inconsistencies, merge by
                     account_id without duplicating accounts already seen
  segment.py        classify accounts from behaviour: broker-reliant,
                     healthy account-managed, self-serve-healthy,
                     self-serve-headroom
  plays.py          the two plays (migrate-to-self-serve, grow-self-serve) —
                     scoring + which feature to recommend, priors as
                     markdown skills under data/plays/
  llm.py            thin Anthropic wrapper for drafting the outreach message;
                     degrades to templated heuristics with no API key
  store.py          persisted run state (account IDs already processed, last
                     decision per account) so re-runs are idempotent
  orchestrator.py   the daily loop: ingest → segment → decide NBA → draft →
                     persist → report
cli.py              entrypoint to run the loop against a workbook
data/plays/         the play skills (edit behaviour here, no code change)
```

Built to stay correct from 300 accounts to 30,000: segmentation runs as
vectorised pandas operations (no per-row Python loops), and idempotency is a
set-membership check on account ID, not a full recompute.

## Status

Scaffolding only — data in place, architecture decided. Ingestion, segmentation,
plays, and the orchestrator loop are next.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY (optional — falls back to heuristics)
python cli.py run data/raw/Fleek_-_Retention_Case_Study_-_Portfolio_Data.xlsx
```
