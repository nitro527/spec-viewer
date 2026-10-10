"""Prompt and output schema shared by every intent model (Claude, local OpenAI-compatible LLMs, Gemini)."""
from __future__ import annotations

import json

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


def user_prompt(ch: dict) -> str:
    covers = [{k: c.get(k) for k in ("cr", "title", "wi_code", "category", "reason", "summary", "clauses_raw")}
              for c in ch.get("cr_covers", [])]
    return (
        f"Spec clause {ch['clause']} ({ch['title']}), change type: {ch['change']}.\n\n"
        f"<old_text>\n{ch.get('old_text') or '(none - newly added)'}\n</old_text>\n\n"
        f"<new_text>\n{ch['text']}\n</new_text>\n\n"
        f"<cr_cover_pages>\n{json.dumps(covers, ensure_ascii=False, indent=1)}\n</cr_cover_pages>"
    )
