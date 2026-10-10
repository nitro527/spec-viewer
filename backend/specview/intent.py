"""Change-intent summaries from an LLM behind an OpenAI-compatible chat-completions API.

Covers self-hosted models (vLLM / SGLang / Ollama / LM Studio serving GLM, Qwen ...)
and hosted ones that expose the same API (Gemini's ``/v1beta/openai`` endpoint).
Plain HTTP so no vendor SDK is needed; used by the desktop app and the annotation server.
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

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


PRESETS = {
    # name: (base_url, default model, env var holding the key)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "GEMINI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "glm4", None),
    "vllm": ("http://localhost:8000/v1", "glm", None),
}


class LLMError(RuntimeError):
    """A request failed; the message is meant for the user."""


class QuotaExceeded(LLMError):
    """The provider's (daily) quota is used up: further requests would only fail too."""


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


def _error_body(r) -> dict:
    """Gemini's OpenAI endpoint wraps errors in a list; others return {"error": {...}}."""
    try:
        body = r.json()
    except Exception:
        return {}
    if isinstance(body, list):
        body = body[0] if body else {}
    return (body.get("error") or {}) if isinstance(body, dict) else {}


def _retry_delay(err: dict) -> float | None:
    for d in err.get("details") or []:
        m = re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", "")))
        if m:
            return float(m.group(1))
    return None


def _quota_error(err: dict) -> QuotaExceeded | None:
    """A 429 that will not clear within a minute (daily quota, billing) - retrying only burns requests."""
    ids = [v.get("quotaId", "") for d in err.get("details") or [] for v in d.get("violations") or []]
    delay = _retry_delay(err)
    if any("PerDay" in i for i in ids) or (delay or 0) > 120 or "check your plan and billing" in err.get("message", ""):
        hours = f" (약 {delay / 3600:.0f}시간 후 초기화)" if delay and delay > 3600 else ""
        return QuotaExceeded(f"요청 한도 초과: 오늘 사용할 수 있는 요청을 다 썼습니다{hours}. "
                             "다른 모델을 고르거나 결제를 켜세요.")
    return None


def _http_error(r) -> LLMError:
    err = _error_body(r)
    detail = (err.get("message") or getattr(r, "text", "") or "").strip().splitlines()
    detail = f" — {detail[0][:200]}" if detail else ""
    hint = {401: "API 키가 잘못됐습니다", 403: "API 키에 권한이 없습니다",
            404: "서버 주소 또는 모델 이름을 확인하세요", 429: "요청이 너무 많습니다. 잠시 후 다시 시도하세요"}
    return LLMError(f"{hint.get(r.status_code, f'LLM 서버 오류 {r.status_code}')}{detail}")


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

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post(self, body: dict):
        try:
            return self.session.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers(),
                                     timeout=self.timeout)
        except requests.RequestException as e:
            raise LLMError(f"LLM 서버에 연결할 수 없습니다 (주소·네트워크·프록시 확인): {e}") from e

    def _request(self, messages: list[dict], structured: bool = True) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        if structured and self._schema_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "change_intent", "schema": INTENT_SCHEMA, "strict": True}}
        elif structured:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(4):
            r = self._post(body)
            if r.status_code == 400 and structured and self._schema_mode == "json_schema":
                self._schema_mode = "json_object"  # older servers: fall back to plain JSON mode
                return self._request(messages)
            if r.status_code == 429:
                err = _error_body(r)
                if q := _quota_error(err):
                    raise q
                if attempt < 3:  # per-minute limit: wait as long as the server asks
                    time.sleep(min(_retry_delay(err) or 2 ** attempt * 2, 60))
                    continue
            elif r.status_code in (500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code >= 400:
                raise _http_error(r)
            return r.json()
        raise _http_error(r)

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

    def ping(self) -> dict:
        """One tiny request to check address, key and model."""
        t = time.monotonic()
        data = self._request([{"role": "user", "content": "Reply with the single word: OK"}], structured=False)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"model": data.get("model") or self.model, "reply": text.strip()[:100],
                "seconds": round(time.monotonic() - t, 1)}

    def list_models(self) -> list[str]:
        try:
            r = self.session.get(f"{self.base_url}/models", headers=self._headers(), timeout=30)
        except requests.RequestException as e:
            raise LLMError(f"LLM 서버에 연결할 수 없습니다 (주소·네트워크·프록시 확인): {e}") from e
        if r.status_code >= 400:
            raise _http_error(r)
        ids = [m.get("id", "") for m in r.json().get("data", [])]
        return sorted({i.removeprefix("models/") for i in ids if i})
