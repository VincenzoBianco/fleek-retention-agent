"""Thin Anthropic wrapper.

Degrades gracefully: if there's no API key / no credentials, `enabled` is False
and callers fall back to the templated heuristics in draft.py. Nothing here
raises into the pipeline — a failed call returns None and the heuristic covers
it, so a run never dies because the LLM was unavailable.
"""
from __future__ import annotations

import os

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
