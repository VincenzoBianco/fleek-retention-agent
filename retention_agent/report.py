"""Run outputs: a JSON dump, a CSV action queue, and a self-contained HTML view.

These are *outputs* of a run, not the product — the product is the persisted
decision per account. But the CSV is what an AM would actually work from, and
the HTML is what you'd glance at each morning / show in the demo.
"""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .models import RunReport
from .store import Store

_FIELDS = ["account_id", "ownership", "region", "persona", "gmv_total", "segment",
           "health", "play", "feature", "channel", "priority", "gmv_at_stake",
           "action", "reason", "draft"]


def write_json(store: Store, report: RunReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "run.json"
    path.write_text(json.dumps({
        "report": report.model_dump(),
        "counts": store.counts(),
        "runs": store.runs(),
        "action_queue": store.action_queue(),
    }, indent=2))
    return path


def write_csv(store: Store, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "action_queue.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in store.action_queue():
            w.writerow(row)
    return path


def write_html(store: Store, report: RunReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    queue = store.action_queue()
    segs = store.segment_counts()
    plays = store.play_counts()

    def chips(d):
        return "".join(
            f'<span class="chip"><b>{v}</b> {html.escape(k)}</span>' for k, v in sorted(d.items(), key=lambda x: -x[1]))

    rows = []
    for r in queue:
        rows.append(f"""<tr>
          <td class="mono">{html.escape(r['account_id'])}</td>
          <td>{html.escape(r['segment'] or '')}<br><span class="muted">{html.escape(r['health'] or '')}</span></td>
          <td><span class="play play-{html.escape(r['play'] or '')}">{html.escape((r['play'] or '').replace('_',' '))}</span>
              {('<br><span class="muted">'+html.escape(r['feature'].replace('_',' '))+'</span>') if r.get('feature') else ''}</td>
          <td class="num">£{(r['gmv_at_stake'] or 0):,.0f}</td>
          <td>{html.escape(r['reason'] or '')}</td>
          <td class="draft">{html.escape(r['draft'] or '')}</td>
        </tr>""")

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleek Retention — action queue</title>
<style>
  :root {{ --ink:#141414; --muted:#6b7280; --line:#e5e7eb; --accent:#111; }}
  * {{ box-sizing:border-box; }}
  body {{ font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; color:var(--ink); margin:0; background:#fafafa; }}
  .wrap {{ max-width:1200px; margin:0 auto; padding:32px 24px 64px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); margin:0 0 24px; }}
  .cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px 18px; min-width:130px; }}
  .card .big {{ font-size:24px; font-weight:700; }}
  .card .lbl {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  .chips {{ margin:6px 0 22px; }}
  .chip {{ display:inline-block; background:#fff; border:1px solid var(--line); border-radius:999px; padding:3px 10px; margin:0 6px 6px 0; font-size:12px; color:var(--muted); }}
  .chip b {{ color:var(--ink); }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:#f3f4f6; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }}
  .mono {{ font-family:ui-monospace,Menlo,monospace; font-size:12px; }}
  .muted {{ color:var(--muted); font-size:12px; }}
  .draft {{ color:#374151; max-width:340px; font-size:13px; }}
  .play {{ font-weight:600; }}
  .play-migrate_to_selfserve {{ color:#b45309; }}
  .play-grow_selfserve {{ color:#047857; }}
  .play-reengage {{ color:#b91c1c; }}
  .foot {{ color:var(--muted); font-size:12px; margin-top:16px; }}
</style></head><body><div class="wrap">
  <h1>Fleek Retention — action queue</h1>
  <p class="sub">Run #{report.run_id} · {html.escape(report.source)} · ranked by £ at stake</p>
  <div class="cards">
    <div class="card"><div class="big">{report.n_seen}</div><div class="lbl">accounts seen</div></div>
    <div class="card"><div class="big">{report.n_new}</div><div class="lbl">new</div></div>
    <div class="card"><div class="big">{report.n_changed}</div><div class="lbl">changed</div></div>
    <div class="card"><div class="big">{report.n_unchanged_skipped}</div><div class="lbl">unchanged (skipped)</div></div>
    <div class="card"><div class="big">{report.n_actions}</div><div class="lbl">actions queued</div></div>
    <div class="card"><div class="big">£{report.gmv_at_stake_total:,.0f}</div><div class="lbl">GMV at stake</div></div>
  </div>
  <div class="chips"><b>Segments:</b> {chips(segs)}</div>
  <div class="chips"><b>Plays:</b> {chips(plays)}</div>
  <table>
    <thead><tr><th>Account</th><th>Segment</th><th>Play</th><th>£ at stake</th><th>Why</th><th>Drafted next best action</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p class="foot">Generated by the Fleek Retention Agent. Drafts are {'LLM-written' if any(r.get('used_llm') for r in queue) else 'templated'}; re-run drops in new data and updates in place.</p>
</div></body></html>"""
    path.write_text(doc)
    return path


def write_all(store: Store, report: RunReport, out_dir: Path) -> dict[str, Path]:
    return {
        "json": write_json(store, report, out_dir),
        "csv": write_csv(store, out_dir),
        "html": write_html(store, report, out_dir),
    }
