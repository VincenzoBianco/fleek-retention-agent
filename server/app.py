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

from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from retention_agent import config, ingest
from retention_agent.llm import LLM
from retention_agent.orchestrator import run as run_loop
from retention_agent.report import action_queue_csv
from retention_agent.store import Store

app = FastAPI(title="Fleek Retention Agent")
STATIC = Path(__file__).parent / "static"

# Calendar labels for the six-month GMV window (config.MONTH_COLS order).
MONTH_LABELS = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb"]
# The two datasets summarised on the dashboard; the "Readme" tab is skipped.
DATASET_SHEETS = ["Accounts", "new_accounts"]


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


@app.post("/api/run")
def do_run(payload: dict = Body(default={})):
    wb = _workbook()
    if not wb:
        return JSONResponse({"error": "no workbook — upload an .xlsx first"}, status_code=400)
    sheet = payload.get("sheet", "Accounts")
    s = _store()
    try:
        import os
        report = run_loop(wb, sheet, s, use_llm=bool(payload.get("llm")),
                          use_agent=payload.get("agent", True),
                          source_ts=os.path.getmtime(wb))
        return report.model_dump()
    finally:
        s.close()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Accept an .xlsx and drop it into the writable upload dir, so a hosted
    instance isn't tied to a file baked into the deploy. Next `/api/run` picks
    it up as the newest workbook. Locally this writes into data/raw, shared with
    the CLI; on serverless it writes into /tmp (ephemeral)."""
    name = Path(file.filename or "").name
    if not name.lower().endswith((".xlsx", ".xls")):
        return JSONResponse({"error": "expected an .xlsx file"}, status_code=400)
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
