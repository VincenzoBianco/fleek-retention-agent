"""The account-analyst agent.

One agent, invoked on ONE account at a time (`evaluate`). It reads the strategy
+ reasoning skills, works the account through the read-only tools in tools.py,
and emits the categorical decision (segment, health, play, feature, channel,
action, reason). This module then does the parts that must stay deterministic:

  - sizes the money with ev.prize_for (the agent never sets £),
  - enforces the key-account guard (a big relationship is never auto-queued),
  - enforces the deterministic holdout (control group, for causal measurement).

It degrades safely: with no API key, an agent error, or an unparseable result, it
falls back to the deterministic `plays.decide` — the exact engine that shipped
before — so a run always produces a full, valid decision. `decided_by` records
which path ran, so you can see (and measure) how often the agent departs from the
deterministic baseline.
"""
from __future__ import annotations

from . import config, ev
from .llm import LLM
from .models import Account, Decision
from .plays import choose_feature, decide, is_holdout, load_plays
from .segment import classify
from .tools import TOOL_SCHEMAS, ToolKit

FINAL_TOOL = "submit_decision"
_ALLOWED_PLAYS = {"reengage", "onboard", "migrate_to_selfserve", "grow_selfserve"}


def _read(path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


class Analyst:
    def __init__(self, llm: LLM | None = None):
        self.llm = llm or LLM()
        self.guide = _read(config.DATA_DIR / "analyst_guide.md")
        self.portfolio_md = _read(config.DATA_DIR / "portfolio_overview.md")
        self.plays_md = load_plays()

    @property
    def enabled(self) -> bool:
        return self.llm.enabled

    # -- public: decide one account --------------------------------------
    def evaluate(self, a: Account, book: list[Account]) -> Decision:
        if not self.llm.enabled:
            return self._fallback(a)
        kit = ToolKit(a, book)
        out = self.llm.run_agent(
            system=self._system(),
            user=(f"Evaluate account {a.account_id} and decide its next best action. "
                  f"Start by gathering its signals, then reason it through."),
            tools=TOOL_SCHEMAS,
            dispatch=lambda name, args: kit.dispatch(name, args, self.portfolio_md),
            final_tool=FINAL_TOOL,
        )
        if not out:
            return self._fallback(a)
        try:
            return self._assemble(a, book, out)
        except Exception:
            return self._fallback(a)

    # -- deterministic fallback (the pre-agent engine) -------------------
    def _fallback(self, a: Account) -> Decision:
        d = decide(a, classify(a))
        d.decided_by = "deterministic"
        return d

    # -- turn the agent's categorical call into a full Decision ----------
    def _assemble(self, a: Account, book: list[Account], out: dict) -> Decision:
        segment = out.get("segment") or "self_serve_healthy"
        health = out.get("health") or "healthy"
        play = out.get("play")
        if play not in _ALLOWED_PLAYS:
            play = None
        feature = out.get("feature") if play == "grow_selfserve" else None
        channel = out.get("channel")
        action = out.get("action")
        reason = (out.get("reason") or "").strip()
        rationale = (out.get("rationale") or "").strip()

        d = Decision(account_id=a.account_id, segment=segment, health=health,
                     fingerprint=a.fingerprint, decided_by="agent",
                     agent_rationale=rationale or None)

        # Guardrail: a key account is a named relationship — never an auto-queued
        # play, whatever the agent proposed.
        total_gmv = sum(x.gmv_total_6m for x in book) or 1.0
        if a.gmv_total_6m / total_gmv >= config.KEY_ACCOUNT_GMV_SHARE:
            d.play = None
            d.reason = (f"KEY ACCOUNT ({a.gmv_total_6m / total_gmv * 100:.0f}% of book GMV) — "
                        f"named, human-owned relationship, not auto-queued")
            return d

        if not play:
            d.reason = reason or f"{segment} — no action needed"
            return d

        # Money is deterministic: the agent chose the play; ev.py sizes it.
        if play == "grow_selfserve" and feature not in config.GROWTH_UPLIFT_PCT:
            feature, _ = choose_feature(a)      # agent picked grow but no/invalid feature
        prize_gmv, expected_value, prize_type = ev.prize_for(a, play, feature)

        d.play = play
        d.feature = feature
        d.channel = channel or _default_channel(a, play)
        d.action = action or _default_action(play, feature)
        d.reason = reason or f"{play} — {prize_type} £{prize_gmv:,.0f}"
        d.prize_type = prize_type
        d.prize_gmv = prize_gmv
        d.gmv_at_stake = prize_gmv
        d.expected_value = expected_value
        d.priority = expected_value

        # Guardrail: deterministic control group. Intended play is kept (so lift can
        # be measured), but suppressed so no outreach fires and it leaves the queue.
        if is_holdout(a.account_id):
            d.holdout = True
            d.reason = f"HOLDOUT (control) — intended: {play}; suppressed to measure lift"
        return d

    # -- assemble the system prompt from the skills ----------------------
    def _system(self) -> str:
        briefs = "\n\n".join(
            f"### Play: {name}\n{md.get('guidance', '')}"
            for name, md in sorted(self.plays_md.items())
        )
        return (
            self.guide
            + "\n\n---\n\n# Portfolio strategy (also available live via the portfolio_context tool)\n\n"
            + self.portfolio_md
            + "\n\n---\n\n# Play playbooks\n\n" + briefs
        )


def _default_channel(a: Account, play: str) -> str:
    if play in ("reengage", "onboard"):
        return "call"
    if play == "migrate_to_selfserve":
        return "whatsapp" if (a.transaction_mode == "hybrid" and a.gmv_total_6m < config.WHALE_GMV) else "call"
    return "whatsapp"   # grow


def _default_action(play: str, feature: str | None) -> str:
    return {
        "reengage": "Win-back call: find what changed, bring one concrete hook",
        "onboard": "Proactive early-success call: help land the first orders, set a cadence",
        "migrate_to_selfserve": "Guide the next order in-app; pre-load usual lines",
        "grow_selfserve": f"Nudge {(feature or 'a feature').replace('_', ' ')}",
    }.get(play, "")
