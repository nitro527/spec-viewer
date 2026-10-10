"""Change-intent summaries with Claude.

Given one changed paragraph (old/new text) and the cover pages of the CRs that
touched its clause, ask Claude for a short structured explanation of what
changed and why. Runs offline on the server; clients only download results.
"""
from __future__ import annotations

import json

import anthropic

MODEL = "claude-opus-5-5"

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "What changed in this paragraph, one or two sentences."},
        "motivation": {"type": "string", "description": "Why it changed, grounded in the CR reason/summary."},
        "impact": {"type": "string", "description": "What a UE/modem PHY implementer has to do differently."},
        "cr": {"type": "string", "description": "CR number most likely responsible, or empty if unclear."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["summary", "motivation", "impact", "cr", "confidence"],
    "additionalProperties": False,
}

SYSTEM = (
    "You explain changes in 3GPP specifications to physical-layer modem engineers. "
    "Use only the provided text and CR cover pages; when they do not explain the change, "
    "say so and set confidence to low. Write in {language}; keep 3GPP terms (PDSCH, DM-RS, "
    "parameter names) in English."
)


class ClaudeIntentModel:
    """IntentModel backed by the Claude API (credentials from the environment)."""

    name = MODEL

    def __init__(self, language: str = "Korean", effort: str = "medium", client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.language = language
        self.effort = effort

    def _prompt(self, ch: dict) -> str:
        covers = [{k: c.get(k) for k in ("cr", "title", "wi_code", "category", "reason", "summary", "clauses_raw")}
                  for c in ch.get("cr_covers", [])]
        return (
            f"Spec clause {ch['clause']} ({ch['title']}), change type: {ch['change']}.\n\n"
            f"<old_text>\n{ch.get('old_text') or '(none - newly added)'}\n</old_text>\n\n"
            f"<new_text>\n{ch['text']}\n</new_text>\n\n"
            f"<cr_cover_pages>\n{json.dumps(covers, ensure_ascii=False, indent=1)}\n</cr_cover_pages>"
        )

    def intent(self, change: dict) -> dict | None:
        response = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM.format(language=self.language),
            messages=[{"role": "user", "content": self._prompt(change)}],
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
