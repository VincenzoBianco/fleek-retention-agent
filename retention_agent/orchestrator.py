"""The daily loop. One entrypoint: run().

    ingest -> diff against stored state -> (new+changed) segment, decide, draft
           -> persist (upsert) -> mark unchanged as seen -> report

Picture it running every morning. The first run decides the whole book; every
run after only touches what's new or changed, and reuses the rest.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .draft import make_draft
from .ingest import load_accounts
from .llm import LLM
from .models import RunReport
from .plays import decide, load_plays
from .segment import classify
from .store import Store


def run(source_path: str | Path, sheet: str, store: Store,
        use_llm: bool = False, workers: int = 8) -> RunReport:
    accounts = load_accounts(source_path, sheet)
    run_id = store.start_run(f"{Path(source_path).name}:{sheet}")

    split = store.diff(accounts)
    to_decide = split["new"] + split["changed"]

    decisions = {a.account_id: decide(a, classify(a)) for a in to_decide}

    # Draft only accounts that have a play. Heuristics are instant; when the LLM
    # is on we fan the calls out across a thread pool so a live run stays snappy.
    llm = LLM() if use_llm else None
    plays_md = load_plays()
    actioned = [a for a in to_decide if decisions[a.account_id].play]

    def _mk(a):
        dec = decisions[a.account_id]
        guidance = plays_md.get(dec.play, {}).get("guidance", "")
        return a.account_id, make_draft(a, dec, llm, guidance)

    drafts: dict[str, tuple[str, bool]] = {}
    if llm is not None and llm.enabled and actioned:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for aid, res in ex.map(_mk, actioned):
                drafts[aid] = res
    else:
        for a in actioned:
            aid, res = _mk(a)
            drafts[aid] = res

    for a in to_decide:
        text, used = drafts.get(a.account_id, ("", False))
        store.upsert(a, decisions[a.account_id], text, used, run_id)
    store.touch_seen([a.account_id for a in split["unchanged"]], run_id)
    store.commit()

    # The report is the *current state of the book*, not just this run's deltas.
    queue = store.action_queue()
    report = RunReport(
        run_id=run_id,
        source=f"{Path(source_path).name}:{sheet}",
        n_seen=len(accounts),
        n_new=len(split["new"]),
        n_changed=len(split["changed"]),
        n_unchanged_skipped=len(split["unchanged"]),
        n_actions=len(queue),
        segment_counts=store.segment_counts(),
        play_counts=store.play_counts(),
        gmv_at_stake_total=sum(r["gmv_at_stake"] or 0 for r in queue),
        expected_value_total=sum(r["expected_value"] or 0 for r in queue),
    )
    store.finish_run(report)
    return report
