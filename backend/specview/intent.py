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
        "cr": {"type": "string", "description": "CR number (or draft CR Tdoc such as R1-1813585) most likely responsible, "
                                                "or empty if unclear."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["summary", "motivation", "impact", "cr", "confidence"],
    "additionalProperties": False,
}

CR_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "What the CR does, two or three sentences."},
        "background": {"type": "string", "description": "The problem or motivation behind it, with the meeting/"
                                                          "Tdoc that raised it where the material shows it."},
        "changes": {"type": "array", "description": "One entry per changed clause.",
                    "items": {"type": "object",
                              "properties": {"clause": {"type": "string"}, "what": {"type": "string"}},
                              "required": ["clause", "what"], "additionalProperties": False}},
        "decision": {"type": "string", "description": "What the working group agreed and the alternatives that were "
                                                        "discussed (from the agreements and moderator summaries); "
                                                        "empty if the material does not say."},
        "impact": {"type": "string", "description": "What a UE/modem implementer has to do differently."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["summary", "background", "changes", "decision", "impact", "confidence"],
    "additionalProperties": False,
}

CR_SYSTEM = (
    "You analyse one 3GPP change request (CR) for physical-layer modem engineers. <cover> is its cover page. "
    "<cr_changes> are the revision marks of the CR itself, by clause: [+added+] and [-removed-] text. "
    "<draft_crs> are the working-group draft CRs a combined CR merges; <wg_agreements> are the decisions in the "
    "meeting reports; <feature_lead_summaries> are excerpts of the moderator's summary: issues, proposals and "
    "company views. Explain what the CR changes, why, what was decided and what alternatives were discussed. "
    "Use only the provided material; name the meeting or Tdoc you rely on; when something is not in the material, "
    "leave that field empty and lower the confidence. "
    "Write in {language}; keep 3GPP terms (PDSCH, DM-RS, parameter names) in English."
)


def cr_prompt(ev: dict) -> str:
    parts = [f"Spec {ev['spec']} CR{ev['cr']} ({ev.get('tdoc', '')}).",
             f"<cover>\n{json.dumps(ev['cover'], ensure_ascii=False, indent=1)}\n</cover>"]
    if ev.get("changes"):
        parts.append("<cr_changes>\n" + "\n".join(
            f"[{c['clause']}]\n" + "\n".join(c["lines"]) for c in ev["changes"]) + "\n</cr_changes>")
    if ev.get("drafts"):
        parts.append(f"<draft_crs>\n{json.dumps(ev['drafts'], ensure_ascii=False, indent=1)}\n</draft_crs>")
    if ev.get("agreements"):
        ag = [{k: a.get(k) for k in ("meeting", "agenda", "kind", "text")} for a in ev["agreements"]]
        parts.append(f"<wg_agreements>\n{json.dumps(ag, ensure_ascii=False, indent=1)}\n</wg_agreements>")
    if ev.get("fl_summaries"):
        fl = [{k: f.get(k) for k in ("tdoc", "title", "meeting", "excerpt")} for f in ev["fl_summaries"]]
        parts.append(f"<feature_lead_summaries>\n{json.dumps(fl, ensure_ascii=False, indent=1)}\n"
                     "</feature_lead_summaries>")
    return "\n\n".join(parts)


WI_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string", "description": "What the feature is and what it adds to NR, 3-5 sentences."},
        "motivation": {"type": "string", "description": "Why it was introduced (problem, use case)."},
        "spec_changes": {"type": "array", "description": "Per spec: the clauses the work item changed and what.",
                         "items": {"type": "object",
                                   "properties": {"spec": {"type": "string"}, "clauses": {"type": "string"},
                                                  "what": {"type": "string"}},
                                   "required": ["spec", "clauses", "what"], "additionalProperties": False}},
        "timeline": {"type": "array", "description": "How it entered the specs: the introducing CRs, then the "
                                                     "later corrections, in time order.",
                     "items": {"type": "object",
                               "properties": {"when": {"type": "string", "description": "meeting or version"},
                                              "what": {"type": "string"}},
                               "required": ["when", "what"], "additionalProperties": False}},
        "impact": {"type": "string", "description": "What a UE/modem implementer has to support, across the specs."},
        "open_points": {"type": "string", "description": "Corrections, ambiguities fixed later or known caveats; "
                                                         "empty if none in the material."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["overview", "motivation", "spec_changes", "timeline", "impact", "open_points", "confidence"],
    "additionalProperties": False,
}

WI_SYSTEM = (
    "You explain one 3GPP work item (a feature) to physical-layer modem engineers, from every change request "
    "(CR) it made to the specifications. <crs> lists them oldest first: spec, CR number, meeting, version, "
    "category (B/C = feature addition or change, F/A = correction, D = editorial), cover page reason and summary, "
    "the clauses it changed, and where available an earlier detailed analysis of the CR. Describe the feature as a "
    "whole: what it is, why, what each spec got, how it evolved (first the introducing CRs, then corrections), and "
    "what an implementer has to support. Use only the provided material; name CRs and meetings you rely on; when "
    "something is not in the material, leave it out and lower the confidence. "
    "Write in {language}; keep 3GPP terms (PDSCH, DM-RS, parameter names) in English."
)


def wi_prompt(wi: dict, max_chars: int = 60000) -> str:
    rows, used = [], 0
    for c in wi["crs"]:
        row = {"spec": c["spec"], "cr": c["cr"], "meeting": c["meeting"], "version": c["new_version"],
               "cat": c["cat"], "title": c["title"], "clauses": c["clauses_raw"],
               "reason": (c["reason"] or "")[:700], "summary": (c["summary"] or "")[:900]}
        if c.get("analysis"):
            row["analysis"] = {k: (v or "")[:700] for k, v in c["analysis"].items()}
        text = json.dumps(row, ensure_ascii=False)
        if used + len(text) > max_chars:  # very large work items: keep the oldest (introducing) CRs whole
            row = {k: row[k] for k in ("spec", "cr", "meeting", "version", "cat", "title", "clauses")}
            text = json.dumps(row, ensure_ascii=False)
        used += len(text)
        rows.append(text)
    return f"Work item {wi['wi']}: {len(rows)} CRs.\n<crs>\n" + "\n".join(rows) + "\n</crs>"


SYSTEM = (
    "You explain changes in 3GPP specifications to physical-layer modem engineers. "
    "<changes> lists exactly which words were removed (-) and added (+) in the paragraph. "
    "A CR often bundles many corrections (\"Combined CR of all essential corrections ...\"): find the item that "
    "matches these changes - first in <draft_crs> (the individual draft CRs that touched this clause), then in "
    "<relevant_cr_lines> (cover-page lines ranked by overlap with the change) - and base the motivation on it. "
    "<wg_agreements> are the working-group decisions (meeting report) behind it; use them for the technical "
    "background and name the meeting when you rely on one. <feature_lead_summaries> are excerpts of the moderator's "
    "summary of that agenda item: issues, proposals and company views - use them to explain why this option was "
    "chosen and what the alternatives were. "
    "<cr_analysis> is an earlier, detailed analysis of the CR(s) behind it: keep the summary consistent with it "
    "and say which part of the CR this paragraph belongs to. "
    "Use only the provided material; when nothing in it explains the change, say so and set confidence to low. "
    "Write in {language}; keep 3GPP terms (PDSCH, DM-RS, parameter names) in English."
)


def user_prompt(ch: dict) -> str:
    covers = [{k: c.get(k) for k in ("cr", "title", "wi_code", "category", "reason", "summary", "clauses_raw")}
              for c in ch.get("cr_covers", [])]
    drafts = [{k: c.get(k) for k in ("wg_tdoc", "title", "reason", "summary", "clauses_raw")}
              for c in ch.get("draft_covers", [])]
    parts = [f"Spec clause {ch['clause']} ({ch['title']}), change type: {ch['change']}."]
    if ch.get("changes"):
        parts.append("<changes>\n" + "\n".join(ch["changes"]) + "\n</changes>")
    parts.append(f"<old_text>\n{ch.get('old_text') or '(none - newly added)'}\n</old_text>")
    parts.append(f"<new_text>\n{ch['text']}\n</new_text>")
    if drafts:
        parts.append(f"<draft_crs>\n{json.dumps(drafts, ensure_ascii=False, indent=1)}\n</draft_crs>")
    if ch.get("agreements"):
        ag = [{k: a.get(k) for k in ("meeting", "agenda", "kind", "text")} for a in ch["agreements"]]
        parts.append(f"<wg_agreements>\n{json.dumps(ag, ensure_ascii=False, indent=1)}\n</wg_agreements>")
    if ch.get("fl_summaries"):
        fl = [{k: f.get(k) for k in ("tdoc", "title", "meeting", "excerpt")} for f in ch["fl_summaries"]]
        parts.append(f"<feature_lead_summaries>\n{json.dumps(fl, ensure_ascii=False, indent=1)}\n</feature_lead_summaries>")
    if ch.get("cr_analyses"):
        ca = [{k: a.get(k) for k in ("cr", "summary", "background", "changes", "decision")} for a in ch["cr_analyses"]]
        parts.append(f"<cr_analysis>\n{json.dumps(ca, ensure_ascii=False, indent=1)}\n</cr_analysis>")
    if ch.get("relevant_lines"):
        parts.append("<relevant_cr_lines>\n" + "\n".join(ch["relevant_lines"]) + "\n</relevant_cr_lines>")
    parts.append(f"<cr_cover_pages>\n{json.dumps(covers, ensure_ascii=False, indent=1)}\n</cr_cover_pages>")
    return "\n\n".join(parts)


PRESETS = {
    # name: (base_url, default model, env var holding the key)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "GEMINI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "glm4", None),
    "vllm": ("http://localhost:8000/v1", "glm", None),
}


class LLMError(RuntimeError):
    """A request failed; the message is meant for the user."""


class ToolsUnsupported(LLMError):
    """The server rejected a request with tools (no function calling): the chat falls back to plain retrieval."""


class _NoJsonSchema(Exception):
    """400 to a json_schema request: maybe the server lacks it, maybe something else is wrong."""

    def __init__(self, error: LLMError):
        self.error = error


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
    text = hint.get(r.status_code, f"LLM 서버 오류 {r.status_code}")
    if r.status_code == 400 and re.search(r"api[ _-]?key", detail, re.I):  # Gemini answers a bad key with 400
        text = hint[401]
    return LLMError(f"{text}{detail}")


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

    def _request(self, messages: list[dict], structured: bool = True, schema: tuple[str, dict] | None = None) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        name, sch = schema or ("change_intent", INTENT_SCHEMA)
        if structured and self._schema_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": name, "schema": sch, "strict": True}}
        elif structured:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(4):
            r = self._post(body)
            if (r.status_code == 400 and structured and self._schema_mode == "json_schema"
                    and not re.search(r"api[ _-]?key", str(_error_body(r).get("message", "")), re.I)):
                raise _NoJsonSchema(_http_error(r))
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

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One chat completion, optionally with function tools; returns the assistant message
        (``content`` with any <think> block removed, ``tool_calls`` when the model asks for tools)."""
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        for attempt in range(4):
            r = self._post(body)
            if r.status_code == 400 and tools and not re.search(
                    r"api[ _-]?key|context|token", str(_error_body(r).get("message", "")), re.I):
                raise ToolsUnsupported(str(_http_error(r)))
            if r.status_code == 429:
                err = _error_body(r)
                if q := _quota_error(err):
                    raise q
                if attempt < 3:
                    time.sleep(min(_retry_delay(err) or 2 ** attempt * 2, 60))
                    continue
            elif r.status_code in (500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code >= 400:
                raise _http_error(r)
            data = r.json()
            msg = dict((data.get("choices") or [{}])[0].get("message") or {})
            msg["content"] = _THINK.sub("", msg.get("content") or "").strip()
            msg["model"] = data.get("model") or self.model
            return msg
        raise _http_error(r)

    def _intent_messages(self, change: dict) -> list[dict]:
        system = SYSTEM.format(language=self.language)
        if self._schema_mode != "json_schema":
            system += "\nReply with only a JSON object matching this schema:\n" + json.dumps(INTENT_SCHEMA)
        # small models drop the language from the system prompt: repeat it last
        return [{"role": "system", "content": system},
                {"role": "user", "content": user_prompt(change) + f"\n\nWrite summary, motivation and impact in {self.language}."}]

    def _structured(self, messages, schema: tuple[str, dict]) -> dict:
        try:
            return self._request(messages(), schema=schema)
        except _NoJsonSchema as first:
            # Older servers reject json_schema: retry in JSON mode (schema in the prompt). If that fails as
            # well, the 400 was about something else - keep json_schema and report the original error.
            self._schema_mode = "json_object"
            try:
                return self._request(messages(), schema=schema)
            except LLMError:
                self._schema_mode = "json_schema"
                raise first.error from None

    def intent(self, change: dict) -> dict | None:
        data = self._structured(lambda: self._intent_messages(change), ("change_intent", INTENT_SCHEMA))
        return self._answer(data)

    def analyze_cr(self, evidence: dict) -> dict | None:
        def messages():
            system = CR_SYSTEM.format(language=self.language)
            if self._schema_mode != "json_schema":
                system += "\nReply with only a JSON object matching this schema:\n" + json.dumps(CR_SCHEMA)
            return [{"role": "system", "content": system},
                    {"role": "user", "content": cr_prompt(evidence) + f"\n\nWrite every field in {self.language}."}]
        return self._answer(self._structured(messages, ("cr_analysis", CR_SCHEMA)))

    def summarize_wi(self, wi: dict) -> dict | None:
        def messages():
            system = WI_SYSTEM.format(language=self.language)
            if self._schema_mode != "json_schema":
                system += "\nReply with only a JSON object matching this schema:\n" + json.dumps(WI_SCHEMA)
            return [{"role": "system", "content": system},
                    {"role": "user", "content": wi_prompt(wi) + f"\n\nWrite every field in {self.language}."}]
        return self._answer(self._structured(messages, ("wi_summary", WI_SCHEMA)))

    def _answer(self, data: dict) -> dict | None:
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
        return {"model": data.get("model") or self.model, "reply": _THINK.sub("", text).strip()[:100],
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
