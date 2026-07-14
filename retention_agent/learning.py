"""Close the loop: turn logged outcomes into updated priors.

The EV ranking runs on probability priors (SAVE_RATE, conversion, ...). Those
start as documented assumptions; this module blends them toward what actually
happened, using shrinkage so a handful of outcomes can't yank a prior around:

    learned = (prior * k + observed_rate * n) / (k + n)

with a pseudo-count k (default 20). n=0 -> learned == prior (no data, no move);
large n -> learned -> observed. `apply()` writes the learned values back onto
config for the run, so each morning's run re-ranks on the latest evidence. That's
the learning loop the diagram draws — now wired, not gestured at.

Deterministic: no randomness, no clock, so a re-run with the same outcomes yields
the same priors (idempotency holds).
"""
from __future__ import annotations

from . import config

# which play's realized conversion informs which prior(s)
_PRIOR_SOURCES = {
    "SAVE_RATE": ("reengage", ["SAVE_RATE"]),
    "CONVERT": ("migrate_to_selfserve", ["CONVERT_RATE_WARM", "CONVERT_RATE_COLD"]),
}


def _shrink(prior: float, observed: float, n: int, k: int) -> float:
    if n <= 0:
        return prior
    return round((prior * k + observed * n) / (k + n), 4)


def learned_priors(store, k: int = 20) -> dict:
    """Return {config_key: (old, new, n)} for priors we have evidence to update."""
    rates = store.realized_rates()
    out: dict[str, tuple] = {}
    for _, (play, keys) in _PRIOR_SOURCES.items():
        r = rates.get(play)
        if not r or not r.get("n"):
            continue
        observed = r.get("conversion_rate")
        if observed is None:
            continue
        for key in keys:
            old = getattr(config, key)
            out[key] = (old, _shrink(old, observed, r["n"], k), r["n"])
    return out


def apply(store, k: int = 20) -> dict:
    """Blend priors toward observed outcomes and write them onto config for this
    run. Returns the change summary (empty if there are no outcomes yet)."""
    changes = learned_priors(store, k=k)
    for key, (_, new, _) in changes.items():
        setattr(config, key, new)
    return {key: {"from": old, "to": new, "n": n} for key, (old, new, n) in changes.items()}
