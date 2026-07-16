"""Live dashboard for the Retention Agent.

    uvicorn server.app:app --port 8000   # then open http://localhost:8000

A thin FastAPI layer over the same pieces the CLI uses — it doesn't reimplement
any logic, it just exposes the store and the orchestrator over HTTP so you can
browse the book, click into an account, trigger a run, and log an outcome (which
feeds the learning loop). State is the same SQLite file the CLI writes, so the
web app and `python cli.py` share one book.

A fresh Store is opened per request (SQLite connections aren't shared across the
threadpool), which is cheap — it's a local file.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from retention_agent import config, ingest, plays
from retention_agent.llm import LLM
from retention_agent.models import Account, Decision
from retention_agent.orchestrator import run as run_loop
from retention_agent.report import action_queue_csv
from retention_agent.store import Store

app = FastAPI(title="Fleek Retention Agent")
STATIC = Path(__file__).parent / "static"

# Calendar labels for the six-month GMV window (config.MONTH_COLS order).
MONTH_LABELS = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb"]
# The two datasets summarised on the dashboard; the "Readme" tab is skipped.
DATASET_SHEETS = ["Accounts", "new_accounts"]

# The markdown "skills" the Plays tab visualises and edits. This is a fixed
# whitelist (key → path) so a save can never write outside the two policy dirs —
# no path traversal from the client. Plays carry per-account decision logic;
# the two guides carry the cross-book reasoning and strategy the agent reads.
def _skill_registry() -> dict[str, dict]:
    reg: dict[str, dict] = {}
    for md in sorted(config.PLAYS_DIR.glob("*.md")):
        reg["plays/" + md.stem] = {"path": md, "kind": "play"}
    for stem, title in (("analyst_guide", "Analyst guide — how to reason"),
                        ("portfolio_overview", "Portfolio overview — strategy & facts")):
        p = config.DATA_DIR / (stem + ".md")
        if p.exists():
            reg[stem] = {"path": p, "kind": "guide", "title": title}
    return reg


def _store() -> Store:
    return Store(config.STATE_DB)


def _workbook() -> str | None:
    """The workbook to run against: the most recently modified .xlsx across the
    upload dir and data/raw. Upload dir first so a freshly uploaded file wins;
    on serverless data/raw is read-only/empty and only the upload dir matters."""
    seen: dict[str, Path] = {}
    for d in (config.UPLOAD_DIR, config.RAW_DIR):
        if d.exists():
            for p in d.glob("*.xlsx"):
                seen.setdefault(p.resolve().as_posix(), p)
    if not seen:
        return None
    return str(max(seen.values(), key=lambda p: p.stat().st_mtime))


def _s(v):
    """String or None (pandas NA -> None so it serialises cleanly)."""
    return None if pd.isna(v) else str(v)


def _f(v):
    return None if pd.isna(v) else float(v)


def _dataset_records(df: pd.DataFrame) -> list[dict]:
    """Lite per-account records for the dashboard — the cleaned, decision-relevant
    fields only, small enough to ship both sheets to the browser in one payload so
    tier-filtering and account selection are instant (no round-trips)."""
    out = []
    for _, r in df.iterrows():
        monthly = [float(r[c]) for c in config.MONTH_COLS]
        out.append({
            "account_id": r["account_id"],
            "tier": r["transaction_mode"],            # self_serve | hybrid | manual
            "ownership": r["ownership"] or "Unknown",
            "persona": _s(r["buyer_persona"]),
            "region": _s(r["region"]),
            "country": _s(r["country"]),
            "tenure": float(r["tenure_months"]),
            "gmv_total": float(r["gmv_total_6m"]),
            "orders": int(r["orders_6m"]),
            "aov": float(r["aov"]),
            "manual_pct": float(r["broker_reliance"]),    # recomputed, trusted
            "bundle_share": float(r["bundle_gmv_share_pct"]),
            "monthly": monthly,                           # GMV Sep..Feb — the only monthly series
            "active_months": int(sum(1 for x in monthly if x > 0)),
            "app_active_days": float(r["app_active_days_6m"]),
            "pdp_views": float(r["pdp_views_6m"]),
            "offers": float(r["make_an_offer_6m"]),
            "chat": float(r["chat_threads"]),
            "video": float(r["video_call_requests"]),
            "handpick_orders": int(r["handpick_orders"]),
            "bundle_orders": int(r["bundle_orders"]),
            "momentum": _f(r["momentum_pct"]),
            "data_flags": list(r["data_flags"]),      # cleaning/quality flags per row
        })
    return out


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/api/dashboard")
def dashboard():
    """Descriptive summary of the source datasets, read straight from the workbook
    (via the same cleaning the pipeline uses) so the dashboard works with zero
    dependence on a run having happened. Ships both sheets' lite records at once;
    the browser does the tier-filtering, bucketing and charting."""
    path = _workbook()
    if not path:
        return JSONResponse({"error": "no .xlsx in data/raw"}, status_code=400)
    out = {"workbook": Path(path).name, "months": MONTH_LABELS, "sheets": {}}
    for sheet in DATASET_SHEETS:
        try:
            df = ingest.clean(ingest.load_sheet(path, sheet))
        except Exception as e:  # a missing/renamed sheet shouldn't 500 the whole board
            out["sheets"][sheet] = {"error": str(e)}
            continue
        out["sheets"][sheet] = _dataset_records(df)
    return out


@app.get("/api/skills")
def skills():
    """The editable markdown behind every decision — the four plays plus the two
    strategy guides. Plays are returned with their frontmatter parsed out (channel,
    priority metric, the section headings) so the tab can render a plain-language
    card, and with the raw source so it can be edited in place."""
    out = []
    for key, meta in _skill_registry().items():
        text = meta["path"].read_text()
        fm, body = plays._split_frontmatter(text)
        out.append({
            "key": key,
            "kind": meta["kind"],
            "title": meta.get("title") or fm.get("label") or fm.get("name") or key,
            "path": meta["path"].relative_to(config.ROOT).as_posix(),
            "frontmatter": fm,
            "body": body.strip(),
            "content": text,
        })
    return {"skills": out}


@app.post("/api/skills/{key:path}")
def save_skill(key: str, payload: dict = Body(...)):
    """Persist an edited skill back to disk and drop the play cache so the next run
    reasons over the new text. The key must be one we handed out — anything else is
    rejected, so this can't be used to write arbitrary files."""
    meta = _skill_registry().get(key)
    if not meta:
        return JSONResponse({"error": "unknown skill"}, status_code=404)
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return JSONResponse({"error": "content required"}, status_code=400)
    try:
        meta["path"].write_text(content)
    except OSError as e:  # read-only fs (serverless) or perms — surface, don't 500
        return JSONResponse({"error": f"could not write: {e}"}, status_code=400)
    plays.load_plays.cache_clear()   # next Analyst / fallback re-reads the file
    return {"ok": True, "key": key}


@app.get("/api/state")
def state():
    """Everything the dashboard needs in one shot: banners + queue + history."""
    s = _store()
    try:
        return {
            "workbook": Path(_workbook()).name if _workbook() else None,
            "llm_available": LLM().enabled,   # is a usable ANTHROPIC_API_KEY loaded?
            "counts": s.counts(),
            "concentration": s.gmv_concentration(),
            "key_accounts": s.key_accounts(),
            "segments": s.segment_counts(),
            "plays": s.play_counts(),
            "holdout": s.holdout_count(),
            "runs": s.runs(),
            "queue": s.action_queue(),
            "realized_rates": s.realized_rates(),
        }
    finally:
        s.close()


@app.get("/api/account/{aid}")
def account(aid: str):
    s = _store()
    try:
        row = next((r for r in s.all_accounts() if r["account_id"] == aid), None)
        return row or JSONResponse({"error": "not found"}, status_code=404)
    finally:
        s.close()


def _queue_row(a: Account, d: Decision) -> dict:
    """The subset of a decision the live Action Queue card renders — shaped to match
    the fields `store.action_queue()` returns, so a streamed row and a reloaded row
    look identical. Only actionable, non-holdout accounts are streamed (the queue an
    AM works never shows the control group or accounts left alone)."""
    return {
        "account_id": a.account_id,
        "segment": d.segment, "health": d.health,
        "play": d.play, "feature": d.feature,
        "action": d.action, "reason": d.reason, "channel": d.channel,
        "expected_value": d.expected_value, "gmv_at_stake": d.gmv_at_stake,
        "prize_type": d.prize_type,
        "decided_by": d.decided_by, "agent_rationale": d.agent_rationale,
    }


@app.post("/api/run")
def do_run(payload: dict = Body(default={})):
    """Stream the run as newline-delimited JSON so the dashboard fills the table as
    each account is decided — an agent run evaluates one account at a time and is
    slow, so waiting for the whole book before showing anything felt like a hang.

    Events: {type:progress, done, total, account_id} for every decided account;
    {type:row, row:{…}} for each one that lands in the queue; {type:done, report}
    at the end; {type:error, error} on failure. Drafts are LLM-written by default
    whenever a key is present (no user toggle) — otherwise templated."""
    wb = _workbook()
    if not wb:
        return JSONResponse({"error": "no workbook — upload an .xlsx first"}, status_code=400)
    sheet = payload.get("sheet", "Accounts")
    use_agent = payload.get("agent", True)
    use_llm = LLM().enabled          # a key present ⇒ the agent also drafts

    events: "queue.Queue" = queue.Queue()

    def worker():
        # Own the Store on this thread — SQLite connections aren't shared across the
        # threadpool. All DB writes happen here; the streamer only reads the queue.
        s = _store()
        try:
            def on_decision(a, d, done, total):
                events.put({"type": "progress", "done": done, "total": total,
                            "account_id": a.account_id})
                if d.play and not d.holdout:
                    events.put({"type": "row", "row": _queue_row(a, d)})
            report = run_loop(wb, sheet, s, use_llm=use_llm, use_agent=use_agent,
                              source_ts=os.path.getmtime(wb), on_decision=on_decision)
            events.put({"type": "done", "report": report.model_dump()})
        except Exception as e:  # noqa: BLE001 — surface any failure to the client
            events.put({"type": "error", "error": str(e)})
        finally:
            s.close()
            events.put(None)     # sentinel: closes the stream

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield json.dumps(item) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Accept an .xlsx and drop it into the writable upload dir, so a hosted
    instance isn't tied to a file baked into the deploy. Next `/api/run` picks
    it up as the newest workbook. Locally this writes into data/raw, shared with
    the CLI; on serverless it writes into /tmp (ephemeral)."""
    name = Path(file.filename or "").name
    # .xlsx only: it's what openpyxl (our only Excel reader) handles and what
    # `_workbook()` globs. Accepting legacy .xls here would write a file that is
    # then silently never picked up (no xlrd, wrong glob) — so reject it up front.
    if not name.lower().endswith(".xlsx"):
        return JSONResponse({"error": "expected an .xlsx file (legacy .xls isn't supported)"},
                            status_code=400)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (config.UPLOAD_DIR / name).write_bytes(await file.read())
    return {"ok": True, "workbook": name}


@app.post("/api/outcome")
def do_outcome(payload: dict = Body(...)):
    aid = payload.get("account_id")
    s = _store()
    try:
        row = next((r for r in s.all_accounts() if r["account_id"] == aid), None)
        if not row:
            return JSONResponse({"error": "not found"}, status_code=404)
        treated = not bool(row["holdout"])
        s.record_outcome(aid, row["decided_run"], row["play"], row["feature"],
                         treated=treated, sent=treated,
                         responded=bool(payload.get("responded")),
                         converted=bool(payload.get("converted")),
                         gmv_delta=float(payload.get("gmv_delta", 0) or 0))
        return {"ok": True, "realized_rates": s.realized_rates()}
    finally:
        s.close()


@app.get("/api/queue.csv")
def queue_csv():
    s = _store()
    try:
        return Response(action_queue_csv(s), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=action_queue.csv"})
    finally:
        s.close()


@app.post("/api/reset")
def do_reset():
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(config.STATE_DB) + suffix)
        if p.exists():
            p.unlink()
    return {"ok": True}
