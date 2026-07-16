"""Thin Anthropic wrapper.

Degrades gracefully: if there's no API key / no credentials, `enabled` is False
and callers fall back to the templated heuristics in draft.py. Nothing here
raises into the pipeline — a failed call returns None and the heuristic covers
it, so a run never dies because the LLM was unavailable.
"""
from __future__ import annotations

import json
import os
from typing import Callable

# Default to the most capable model per Anthropic guidance. Override with
# RETENTION_MODEL — for a 30k-account book you'd point this at a cheaper/faster
# model (e.g. claude-haiku-4-5) and/or move drafting to the Batch API.
DEFAULT_MODEL = "claude-opus-4-8"


class LLM:
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("RETENTION_MODEL", DEFAULT_MODEL)
        self._client = None
        self.enabled = False
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                from anthropic import Anthropic
                self._client = Anthropic()
                self.enabled = True
            except Exception:
                self.enabled = False

    def complete(self, system: str, prompt: str, max_tokens: int = 320) -> str | None:
        """Return the model's text, or None to signal 'use the fallback'.

        Note: no temperature/top_p — claude-opus-4-8 rejects sampling params.
        """
        if not self.enabled or self._client is None:
            return None
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            if getattr(resp, "stop_reason", None) == "refusal":
                return None
            parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            text = "\n".join(parts).strip()
            return text or None
        except Exception:
            return None

    def run_agent(self, system: str, user: str, tools: list[dict],
                  dispatch: Callable[[str, dict], dict], final_tool: str,
                  max_turns: int = 14, max_tokens: int = 1024) -> dict | None:
        """Run a tool-use loop and return the input the model passed to `final_tool`.

        The model reads `system` (the analyst brief + strategy), works the account
        by calling the read-only `tools` (executed via `dispatch`), and signals it's
        done by calling `final_tool` (submit_decision) — whose input dict we return.
        Everything degrades to None (→ deterministic fallback) so a run never dies:
        no key, an API error, a refusal, or a loop that never submits all yield None.
        """
        if not self.enabled or self._client is None:
            return None
        # The submitter is just another tool the model can call.
        all_tools = tools + [_final_tool_schema(final_tool)]
        messages = [{"role": "user", "content": user}]
        try:
            for turn in range(max_turns):
                # On the last allowed turn, force the model to submit rather than
                # think further, so we always get a decision out.
                tool_choice = ({"type": "tool", "name": final_tool}
                               if turn == max_turns - 1 else {"type": "auto"})
                resp = self._client.messages.create(
                    model=self.model, max_tokens=max_tokens, system=system,
                    messages=messages, tools=all_tools, tool_choice=tool_choice,
                )
                if getattr(resp, "stop_reason", None) == "refusal":
                    return None
                calls = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
                if not calls:
                    # model ended its turn without a tool call — nudge it to submit
                    messages.append({"role": "assistant", "content": resp.content})
                    messages.append({"role": "user",
                                     "content": f"Call {final_tool} now with your final decision."})
                    continue
                # Did it submit? If so we're done.
                for c in calls:
                    if c.name == final_tool:
                        return dict(c.input)
                # Otherwise execute the read tools and feed results back.
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for c in calls:
                    out = dispatch(c.name, dict(c.input))
                    results.append({"type": "tool_result", "tool_use_id": c.id,
                                    "content": json.dumps(out, default=str)})
                messages.append({"role": "user", "content": results})
            return None
        except Exception:
            return None


def _final_tool_schema(name: str) -> dict:
    """The categorical decision the agent must emit. Deliberately NO £ fields — the
    money is computed deterministically from (play, feature) after the agent decides."""
    return {
        "name": name,
        "description": "Submit the final decision for this account. Call exactly once, when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "segment": {"type": "string",
                            "enum": ["broker_reliant", "assisted_healthy",
                                     "self_serve_growth", "self_serve_healthy"]},
                "health": {"type": "string", "enum": ["healthy", "declining", "dormant"]},
                "play": {"type": ["string", "null"],
                         "enum": ["reengage", "onboard", "migrate_to_selfserve",
                                  "grow_selfserve", None],
                         "description": "the play to fire, or null to leave the account alone"},
                "feature": {"type": ["string", "null"],
                            "enum": ["video", "chat", "bundles", "build_a_bundle", None],
                            "description": "only for grow_selfserve, else null"},
                "channel": {"type": ["string", "null"],
                            "enum": ["whatsapp", "in_app", "call", None]},
                "action": {"type": ["string", "null"],
                           "description": "the concrete next best action (one sentence)"},
                "reason": {"type": "string",
                           "description": "why this account, why this action — the 5-second hand-off read"},
                "rationale": {"type": "string",
                              "description": "your fuller reasoning, incl. any evidence that made this a close call"},
            },
            "required": ["segment", "health", "play", "reason"],
        },
    }
