"""Intent model for any OpenAI-compatible chat-completions endpoint.

Covers self-hosted models (vLLM / SGLang / Ollama / LM Studio serving GLM, Qwen ...)
and hosted ones that expose the same API (Gemini's ``/v1beta/openai`` endpoint).
Plain HTTP so no vendor SDK is needed on the annotation server.
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

from .prompting import INTENT_SCHEMA, SYSTEM, user_prompt

PRESETS = {
    # name: (base_url, default model, env var holding the key)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "GEMINI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "glm4", None),
    "vllm": ("http://localhost:8000/v1", "glm", None),
}

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def _parse_json(text: str) -> dict:
    """Reasoning models may prepend <think> blocks or wrap JSON in fences."""
    t = _FENCE.sub("", _THINK.sub("", text).strip()).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


class OpenAICompatIntentModel:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, language: str = "Korean",
                 timeout: float = 300, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = model
        self.api_key = api_key
        self.language = language
        self.timeout = timeout
        self.session = session or requests.Session()
        self._schema_mode = "json_schema"  # downgraded to json_object if the server rejects it

    @classmethod
    def from_preset(cls, preset: str, model: str | None = None, base_url: str | None = None,
                    api_key: str | None = None, **kw) -> "OpenAICompatIntentModel":
        url, default_model, key_env = PRESETS.get(preset, (None, None, None))
        if not (base_url or url):
            raise ValueError(f"unknown preset {preset!r}; pass --llm-base-url")
        key = api_key or (os.environ.get(key_env) if key_env else None) or os.environ.get("LLM_API_KEY")
        return cls(base_url or url, model or default_model, key, **kw)

    def _request(self, messages: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        if self._schema_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "change_intent", "schema": INTENT_SCHEMA, "strict": True}}
        else:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(4):
            r = self.session.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
            if r.status_code == 400 and self._schema_mode == "json_schema":
                self._schema_mode = "json_object"  # older servers: fall back to plain JSON mode
                return self._request(messages)
            if r.status_code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return r.json()

    def intent(self, change: dict) -> dict | None:
        system = SYSTEM.format(language=self.language)
        if self._schema_mode != "json_schema":
            system += "\nReply with only a JSON object matching this schema:\n" + json.dumps(INTENT_SCHEMA)
        data = self._request([{"role": "system", "content": system},
                              {"role": "user", "content": user_prompt(change)}])
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content")
        if not text:
            return None
        out = _parse_json(text)
        out["model"] = data.get("model") or self.model
        return out
