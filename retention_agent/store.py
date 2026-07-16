"""Persisted state — the reason this is a process, not a one-off dashboard.

SQLite keyed by account_id. Each account carries the fingerprint of the data
its last decision was made against. On every run we diff the incoming batch
against stored fingerprints:

    new        account_id not seen before        -> decide + draft
    changed    seen, but fingerprint differs      -> re-decide + re-draft
    unchanged  seen, fingerprint identical        -> skip, keep prior decision

That's the idempotency contract: drop the same file in twice and the second run
does nothing new; drop in new_accounts and only the genuinely new/changed
accounts are touched — never duplicated (account_id is the primary key, writes
are upserts). SQLite handles 30k rows without noticing; the diff is a dict
lookup per account, so a re-run is O(n) with almost all n skipped.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config
from .models import Account, Decision, RunReport


class Store:
    def __init__(self, path: Path | str = config.STATE_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id   TEXT PRIMARY KEY,
                fingerprint  TEXT NOT NULL,
                ownership    TEXT, region TEXT, persona TEXT, transaction_mode TEXT,
                gmv_total    REAL,
                segment      TEXT, health TEXT,
                play         TEXT, feature TEXT, channel TEXT,
                action       TEXT, reason TEXT,
                priority     REAL, gmv_at_stake REAL,
                expected_value REAL, prize_type TEXT,
                draft        TEXT, used_llm INTEGER DEFAULT 0,
                decided_by   TEXT DEFAULT 'deterministic', agent_rationale TEXT,
                holdout      INTEGER DEFAULT 0, source_ts REAL DEFAULT 0,
                first_seen_run INTEGER, last_seen_run INTEGER, decided_run INTEGER
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT, n_seen INTEGER, n_new INTEGER, n_changed INTEGER,
                n_unchanged INTEGER, n_actions INTEGER, gmv_at_stake REAL
            );
            -- The feedback loop's landing pad: log what happened to each action so
            -- the play priors (accept rates, uplift) become measured, not assumed.
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT, run_id INTEGER, play TEXT, feature TEXT,
                treated INTEGER DEFAULT 1, sent INTEGER DEFAULT 0, responded INTEGER DEFAULT 0,
                converted INTEGER DEFAULT 0, gmv_delta REAL, ts TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_priority ON accounts(expected_value DESC);
            """
        )
        # Defensive migration: add columns introduced after a DB was first created,
        # so an existing state.db doesn't break on upsert (CREATE IF NOT EXISTS
        # won't add a column to an existing table).
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(accounts)")}
        if "transaction_mode" not in cols:
            self.db.execute("ALTER TABLE accounts ADD COLUMN transaction_mode TEXT")
        if "decided_by" not in cols:
            self.db.execute("ALTER TABLE accounts ADD COLUMN decided_by TEXT DEFAULT 'deterministic'")
        if "agent_rationale" not in cols:
            self.db.execute("ALTER TABLE accounts ADD COLUMN agent_rationale TEXT")
        self.db.commit()

    # --- run lifecycle ---
    def start_run(self, source: str) -> int:
        cur = self.db.execute("INSERT INTO runs(source) VALUES (?)", (source,))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, r: RunReport):
        self.db.execute(
            """UPDATE runs SET n_seen=?, n_new=?, n_changed=?, n_unchanged=?,
                   n_actions=?, gmv_at_stake=? WHERE run_id=?""",
            (r.n_seen, r.n_new, r.n_changed, r.n_unchanged_skipped,
             r.n_actions, r.gmv_at_stake_total, r.run_id),
        )
        self.db.commit()

    # --- the idempotency diff ---
    def diff(self, accounts: list[Account], source_ts: float = 0.0) -> dict[str, list[Account]]:
        """Split an incoming batch into new / changed / unchanged / stale.

        `stale` = the account changed, but this batch's source is OLDER than the
        one behind the stored decision, so we refuse to overwrite fresher data
        with staler (enforced newest-source-wins, not just a README caveat).
        Pass source_ts=0 to disable the guard (every differing row is 'changed')."""
        rows = self.db.execute("SELECT account_id, fingerprint, source_ts FROM accounts").fetchall()
        known = {r["account_id"]: (r["fingerprint"], r["source_ts"] or 0) for r in rows}
        out = {"new": [], "changed": [], "unchanged": [], "stale": []}
        for a in accounts:
            if a.account_id not in known:
                out["new"].append(a)
                continue
            stored_fp, stored_ts = known[a.account_id]
            if stored_fp == a.fingerprint:
                out["unchanged"].append(a)
            elif source_ts and source_ts < stored_ts:
                out["stale"].append(a)      # older source — don't clobber fresher data
            else:
                out["changed"].append(a)
        return out

    # --- writes ---
    def upsert(self, a: Account, d: Decision, draft: str, used_llm: bool, run_id: int,
               source_ts: float = 0.0):
        self.db.execute(
            """
            INSERT INTO accounts (account_id, fingerprint, ownership, region, persona,
                transaction_mode, gmv_total, segment, health, play, feature, channel, action, reason,
                priority, gmv_at_stake, expected_value, prize_type, draft, used_llm,
                decided_by, agent_rationale,
                holdout, source_ts, first_seen_run, last_seen_run, decided_run)
            VALUES (:aid, :fp, :own, :reg, :per, :tmode, :gmv, :seg, :hea, :play, :feat, :chan,
                :act, :rea, :pri, :stake, :ev, :ptype, :draft, :llm, :dby, :arat, :hold, :ts, :run, :run, :run)
            ON CONFLICT(account_id) DO UPDATE SET
                fingerprint=:fp, ownership=:own, region=:reg, persona=:per, transaction_mode=:tmode,
                gmv_total=:gmv, segment=:seg, health=:hea, play=:play, feature=:feat, channel=:chan,
                action=:act, reason=:rea, priority=:pri, gmv_at_stake=:stake,
                expected_value=:ev, prize_type=:ptype, draft=:draft, used_llm=:llm,
                decided_by=:dby, agent_rationale=:arat,
                holdout=:hold, source_ts=:ts, last_seen_run=:run, decided_run=:run
            """,
            dict(aid=a.account_id, fp=a.fingerprint, own=a.ownership, reg=a.region,
                 per=a.buyer_persona, tmode=a.transaction_mode, gmv=a.gmv_total_6m, seg=d.segment, hea=d.health,
                 play=d.play, feat=d.feature, chan=d.channel, act=d.action, rea=d.reason,
                 pri=d.priority, stake=d.gmv_at_stake, ev=d.expected_value, ptype=d.prize_type,
                 draft=draft, llm=int(used_llm), dby=d.decided_by, arat=d.agent_rationale,
                 hold=int(d.holdout), ts=source_ts, run=run_id),
        )

    def touch_seen(self, account_ids: list[str], run_id: int):
        """Mark unchanged accounts as seen this run without rewriting the decision."""
        self.db.executemany(
            "UPDATE accounts SET last_seen_run=? WHERE account_id=?",
            [(run_id, aid) for aid in account_ids],
        )

    def commit(self):
        self.db.commit()

    # --- reads (for reporting / export) ---
    def action_queue(self, limit: int | None = None) -> list[dict]:
        # Holdout accounts have an intended play but are a control group — no
        # outreach, so they never appear in the queue an AM works from.
        q = "SELECT * FROM accounts WHERE play IS NOT NULL AND holdout=0 ORDER BY expected_value DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.db.execute(q).fetchall()]

    def holdout_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM accounts WHERE holdout=1").fetchone()["c"]

    def key_accounts(self, share: float = None) -> list[dict]:
        """Accounts so concentrated they're named, human-owned relationships — not
        queue items. Returns them with their share of book GMV, largest first."""
        share = config.KEY_ACCOUNT_GMV_SHARE if share is None else share
        tot = self.db.execute("SELECT COALESCE(SUM(gmv_total),0) g FROM accounts").fetchone()["g"] or 1
        rows = self.db.execute(
            "SELECT * FROM accounts WHERE gmv_total >= ? ORDER BY gmv_total DESC",
            (share * tot,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["pct_of_gmv"] = round(d["gmv_total"] / tot * 100, 1)
            out.append(d)
        return out

    def gmv_concentration(self) -> dict:
        """The strategic headline: how much of the book's GMV depends on an AM
        placing orders. Broker-reliant accounts are a minority by count but hold
        the majority of revenue — that concentration is the reason migration is a
        scalability play, not a nice-to-have."""
        tot = self.db.execute("SELECT COALESCE(SUM(gmv_total),0) g FROM accounts").fetchone()["g"] or 1
        br = self.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(gmv_total),0) g FROM accounts WHERE segment='broker_reliant'"
        ).fetchone()
        n_tot = self.db.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] or 1
        return {
            "broker_reliant_accounts": br["n"],
            "pct_of_accounts": round(br["n"] / n_tot * 100, 1),
            "broker_reliant_gmv": br["g"],
            "pct_of_gmv": round(br["g"] / tot * 100, 1),
        }

    def record_outcome(self, account_id: str, run_id: int, play: str, feature: str | None,
                       treated=True, sent=True, responded=False, converted=False,
                       gmv_delta=0.0, ts="") -> None:
        """Log what happened to an action. `treated` distinguishes accounts we
        actually contacted from holdout controls — the difference between their
        conversion rates is the *causal* lift the priors should learn (not the raw
        treated rate, which is confounded)."""
        self.db.execute(
            """INSERT INTO outcomes (account_id, run_id, play, feature, treated, sent,
                   responded, converted, gmv_delta, ts) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (account_id, run_id, play, feature, int(treated), int(sent), int(responded),
             int(converted), gmv_delta, ts),
        )
        self.db.commit()

    def realized_rates(self) -> dict[str, dict]:
        """Per-play rates from outcomes, split treated vs holdout. `causal_rate` is
        treated − holdout conversion (the incremental effect) when both arms have
        data; else it falls back to the treated rate and flags itself uncertain."""
        rows = self.db.execute(
            """SELECT play, treated, COUNT(*) n, SUM(responded) resp, SUM(converted) conv,
                      SUM(gmv_delta) gmv FROM outcomes GROUP BY play, treated""").fetchall()
        agg: dict[str, dict] = {}
        for r in rows:
            p = agg.setdefault(r["play"], {"treated": None, "holdout": None})
            arm = "treated" if r["treated"] else "holdout"
            n = r["n"] or 0
            p[arm] = {"n": n, "conv": (r["conv"] or 0) / n if n else 0.0,
                      "resp": (r["resp"] or 0) / n if n else 0.0, "gmv": r["gmv"] or 0}
        out = {}
        for play, p in agg.items():
            t, h = p["treated"], p["holdout"]
            if not t:
                continue
            if h and h["n"]:
                causal = max(0.0, t["conv"] - h["conv"])   # incremental lift
                out[play] = {"n": t["n"], "n_holdout": h["n"], "conversion_rate": round(t["conv"], 2),
                             "holdout_conversion": round(h["conv"], 2), "causal_rate": round(causal, 2),
                             "causal": True, "response_rate": round(t["resp"], 2), "gmv_delta": t["gmv"]}
            else:
                out[play] = {"n": t["n"], "n_holdout": 0, "conversion_rate": round(t["conv"], 2),
                             "causal_rate": round(t["conv"], 2), "causal": False,
                             "response_rate": round(t["resp"], 2), "gmv_delta": t["gmv"]}
        return out

    def all_accounts(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM accounts").fetchall()]

    def counts(self) -> dict:
        n = self.db.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"]
        acted = self.db.execute("SELECT COUNT(*) c FROM accounts WHERE play IS NOT NULL").fetchone()["c"]
        return {"accounts": n, "with_action": acted}

    def segment_counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT segment, COUNT(*) c FROM accounts GROUP BY segment").fetchall()
        return {r["segment"]: r["c"] for r in rows}

    def play_counts(self) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT COALESCE(play,'(none)') p, COUNT(*) c FROM accounts GROUP BY play").fetchall()
        return {r["p"]: r["c"] for r in rows}

    def runs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY run_id").fetchall()]

    def close(self):
        self.db.close()
