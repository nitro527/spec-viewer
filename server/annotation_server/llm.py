"""Change-intent summaries with Claude.

Given one changed paragraph (old/new text) and the cover pages of the CRs that
touched its clause, ask Claude for a short structured explanation of what
changed and why. Runs offline on the server; clients only download results.
"""
from __future__ import annotations

import json

import anthropic

from .prompting import INTENT_SCHEMA, SYSTEM, user_prompt

MODEL = "claude-opus-5-5"

class ClaudeIntentModel:
    """IntentModel backed by the Claude API (credentials from the environment)."""

    name = MODEL

    def __init__(self, language: str = "Korean", effort: str = "medium", client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.language = language
        self.effort = effort

    def intent(self, change: dict) -> dict | None:
        response = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM.format(language=self.language),
            messages=[{"role": "user", "content": user_prompt(change)}],
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": INTENT_SCHEMA}},
            # On a safety decline the API re-runs the request on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return None
        data = json.loads(text)
        data["model"] = response.model
        return data
