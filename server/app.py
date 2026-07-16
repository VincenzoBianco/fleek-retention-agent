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

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

from retention_agent import config
from retention_agent.llm import LLM
from retention_agent.orchestrator import run as run_loop
from retention_agent.report import action_queue_csv
from retention_agent.store import Store

app = FastAPI(title="Fleek Retention Agent")
STATIC = Path(__file__).parent / "static"


def _store() -> Store:
    return Store(config.STATE_DB)


def _workbook() -> str | None:
    xls = sorted(config.RAW_DIR.glob("*.xlsx"))
    return str(xls[0]) if xls else None


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


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
        return JSONResponse({"error": "no .xlsx in data/raw"}, status_code=400)
    sheet = payload.get("sheet", "Accounts")
    s = _store()
    try:
        import os
        report = run_loop(wb, sheet, s, use_llm=bool(payload.get("llm")),
                          source_ts=os.path.getmtime(wb))
        return report.model_dump()
    finally:
        s.close()


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
