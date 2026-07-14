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
                ownership    TEXT, region TEXT, persona TEXT,
                gmv_total    REAL,
                segment      TEXT, health TEXT,
                play         TEXT, feature TEXT, channel TEXT,
                action       TEXT, reason TEXT,
                priority     REAL, gmv_at_stake REAL,
                draft        TEXT, used_llm INTEGER DEFAULT 0,
                first_seen_run INTEGER, last_seen_run INTEGER, decided_run INTEGER
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT, n_seen INTEGER, n_new INTEGER, n_changed INTEGER,
                n_unchanged INTEGER, n_actions INTEGER, gmv_at_stake REAL
            );
            CREATE INDEX IF NOT EXISTS idx_priority ON accounts(priority DESC);
            """
        )
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
    def diff(self, accounts: list[Account]) -> dict[str, list[Account]]:
        """Split an incoming batch into new / changed / unchanged."""
        rows = self.db.execute("SELECT account_id, fingerprint FROM accounts").fetchall()
        known = {row["account_id"]: row["fingerprint"] for row in rows}
        out = {"new": [], "changed": [], "unchanged": []}
        for a in accounts:
            if a.account_id not in known:
                out["new"].append(a)
            elif known[a.account_id] != a.fingerprint:
                out["changed"].append(a)
            else:
                out["unchanged"].append(a)
        return out

    # --- writes ---
    def upsert(self, a: Account, d: Decision, draft: str, used_llm: bool, run_id: int):
        self.db.execute(
            """
            INSERT INTO accounts (account_id, fingerprint, ownership, region, persona,
                gmv_total, segment, health, play, feature, channel, action, reason,
                priority, gmv_at_stake, draft, used_llm,
                first_seen_run, last_seen_run, decided_run)
            VALUES (:aid, :fp, :own, :reg, :per, :gmv, :seg, :hea, :play, :feat, :chan,
                :act, :rea, :pri, :stake, :draft, :llm, :run, :run, :run)
            ON CONFLICT(account_id) DO UPDATE SET
                fingerprint=:fp, ownership=:own, region=:reg, persona=:per, gmv_total=:gmv,
                segment=:seg, health=:hea, play=:play, feature=:feat, channel=:chan,
                action=:act, reason=:rea, priority=:pri, gmv_at_stake=:stake,
                draft=:draft, used_llm=:llm, last_seen_run=:run, decided_run=:run
            """,
            dict(aid=a.account_id, fp=a.fingerprint, own=a.ownership, reg=a.region,
                 per=a.buyer_persona, gmv=a.gmv_total_6m, seg=d.segment, hea=d.health,
                 play=d.play, feat=d.feature, chan=d.channel, act=d.action, rea=d.reason,
                 pri=d.priority, stake=d.gmv_at_stake, draft=draft, llm=int(used_llm), run=run_id),
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
        q = "SELECT * FROM accounts WHERE play IS NOT NULL ORDER BY priority DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.db.execute(q).fetchall()]

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
