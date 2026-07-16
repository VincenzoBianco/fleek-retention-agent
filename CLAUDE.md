# CLAUDE.md

Guidance for working in this repo. Read alongside [README.md](README.md), which
has the full narrative and the commercial rationale behind every threshold.

## What this is

A retention decisioning tool for Fleek (a B2B secondhand-fashion marketplace). It
ingests a portfolio workbook, decides the next best action per account from
**behaviour, not the ownership label**, drafts the outreach, and persists to SQLite
idempotently. Re-running the same file is a no-op; only new/changed accounts are
touched.

## Architecture (the shape to keep in your head)

```
ingest → diff (fingerprints) → decide (new/changed only) → draft → persist → report
```

Decisioning has **two layers, and this separation is the point**:

- **Deterministic tools + fallback.** `ingest`, `segment`, `plays`, `ev`, `store`,
  `learning` are pure/vectorised code. Together `plays.decide(a, classify(a))` is
  the **fallback engine** — the exact logic that shipped before the agent, still
  covered by `tests/test_pipeline.py`.
- **The account-analyst agent** (`agent.Analyst`) decides each account by reasoning
  over tools (`tools.py`) and skills (the markdown in `data/`). It's the default
  when `ANTHROPIC_API_KEY` is set; otherwise, and on any failure, it falls back to
  the deterministic engine.

### The clean seam
- **Tools compute quantities, never verdicts** (`tools.py`). If you add a tool,
  return raw numbers + reference thresholds — do *not* return "segment=X".
- **Skills carry policy** (`data/analyst_guide.md` = how to reason;
  `data/portfolio_overview.md` = strategy + the key data facts). Tune behaviour
  here first — it needs no code change.
- **The agent binds them** into the categorical call and emits `submit_decision`.
- **Guardrails stay deterministic and wrap the agent** (`agent._assemble`).

## Invariants — do not break these

1. **A run never dies for lack of the model.** Every LLM path degrades to `None`
   → deterministic fallback (`llm.complete`, `llm.run_agent`, `agent.evaluate`,
   `draft.make_draft`). Keep new LLM code inside try/except that returns `None`.
2. **The agent never sets money.** All £ figures come from `ev.py` (one source of
   truth, shared by the fallback and the `expected_value` tool). The agent picks
   the *play*; the code sizes it, so the queue stays comparable across the three
   prize types. `submit_decision` deliberately has **no £ fields**.
3. **Key accounts and the holdout are deterministic guardrails.** `agent._assemble`
   forces `play=None` for any account ≥10% of book GMV, and applies the
   `plays.is_holdout` hash. The agent decides *around* these, never through them.
   The holdout must stay a stable hash (no randomness) — it underpins causal-lift
   measurement and idempotency.
4. **Ownership label is never a decision input.** Segmentation reads recomputed
   `broker_reliance` (from order counts, which we trust over the supplied
   `broker_reliance_pct`), activity, and momentum. `ownership_label` is context only.
5. **Idempotency.** `account_id` is the primary key; writes are upserts; the diff is
   by fingerprint. Don't introduce per-run nondeterminism into the fingerprint or
   the deterministic decisions.
6. **Thresholds live in `config.py`**, each with a one-line rationale. When the
   agent path uses a heuristic, mirror it as prose in the skill docs — but
   `config.py` stays the source of truth for the fallback.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

python -m pytest -q                     # 41 tests; keep them green
python cli.py run <workbook.xlsx>       # agent decides if key present, else deterministic
python cli.py run <workbook.xlsx> --no-agent   # force deterministic
python cli.py run <workbook.xlsx> --llm        # also LLM-write the drafts
python cli.py calibrate <workbook.xlsx>        # empirical priors + prior-sensitivity
python cli.py status
uvicorn server.app:app --port 8000             # the live dashboard
```

The workbook is **not committed** (real anonymised customer data). Put it at
`data/raw/Fleek_-_Retention_Case_Study_-_Portfolio_Data.xlsx`. Tests use synthetic
data, so they run without it.

## Testing expectations

- `tests/test_pipeline.py` — the deterministic engine (unchanged by the agent work).
- `tests/test_agent.py` — EV-extraction equivalence, tools return quantities not
  verdicts, the guardrail/money assembly, and **offline fallback parity** (agent off
  ⇒ decisions byte-identical to `plays.decide`).
- The **live tool-calling loop needs a real key** and is not exercised in CI. When
  changing `agent.py` / `llm.run_agent` / `tools.py`, keep the offline tests green
  and validate one live account manually if a key is available.

## Where to change what

- **Decision behaviour / strategy** → `data/portfolio_overview.md` and
  `data/analyst_guide.md` first (no code change), then `config.py` thresholds
  (which also drive the fallback).
- **A play's copy guidance** → `data/plays/*.md`.
- **A new signal for the agent** → add a method + schema in `tools.py`.
- **The £ model** → `ev.py` (and it flows to both the fallback and the tool).
- **Outreach wording** → `draft.py`.
