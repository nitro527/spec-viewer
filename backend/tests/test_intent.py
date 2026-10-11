import time

import pytest

from specview import intent
from specview.pack import build_pack
from tests.conftest import needs_pandoc

DAILY_429 = [{"error": {"code": 429, "message": "You exceeded your current quota, please check your plan and billing details.",
                        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                     "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "61644s"}]}}]


class Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.text = code, body, ""

    def json(self):
        return self._body


def test_daily_quota_is_not_retried():
    posts = []

    class Session:
        def post(self, url, json, headers, timeout):
            posts.append(url)
            return Resp(429, DAILY_429)

    m = intent.OpenAICompatIntentModel("https://x/v1beta/openai", "gemini-3.8-flash", "k", session=Session())
    with pytest.raises(intent.QuotaExceeded, match="17시간"):
        m.intent({"clause": "1", "title": "t", "change": "added", "text": "x"})
    assert len(posts) == 1


def test_http_errors_are_readable():
    class Session:
        def post(self, url, json, headers, timeout):
            return Resp(401, {"error": {"message": "API key not valid"}})

    m = intent.OpenAICompatIntentModel("https://x/v1", "m", "bad", session=Session())
    with pytest.raises(intent.LLMError, match="API 키가 잘못됐습니다 — API key not valid"):
        m.ping()


@needs_pandoc
def test_pack_stops_after_quota(library):
    class Model:
        name = "m"
        calls = 0

        def intent(self, ch):
            self.calls += 1
            raise intent.QuotaExceeded("요청 한도 초과")

    model = Model()
    pack = build_pack(library, "99.211", "16.1.0", model, skip_uncovered=False)
    assert model.calls == 1 and pack["generator"]["stopped"] == "요청 한도 초과"
    assert len(pack["changes"]) > 1


@needs_pandoc
def test_llm_settings_and_intent_job(library, monkeypatch, tmp_path):
    import sqlite3

    from fastapi.testclient import TestClient

    from specview.api import create_app
    from specview.store import Library

    dst = sqlite3.connect(tmp_path / "library.sqlite")  # own copy: this test writes annotations and settings
    library.db.backup(dst)
    dst.close()
    library = Library(tmp_path / "library.sqlite")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    seen = []

    def fake_intent(self, ch):
        seen.append((self.model, self.api_key, ch["clause"]))
        return {"summary": "s", "motivation": "m", "impact": "i", "cr": "", "confidence": "low", "model": self.model}

    monkeypatch.setattr(intent.OpenAICompatIntentModel, "intent", fake_intent)
    c = TestClient(create_app(tmp_path))
    assert c.get("/api/llm").json()["has_key"] is False
    assert c.post("/api/intent", json={"spec": "99.211", "version": "16.1.0"}).status_code == 400  # no key yet

    s = c.put("/api/llm", json={"provider": "gemini", "model": "gemini-x", "api_key": "AIza-secret-1234"}).json()
    assert s["has_key"] and s["key_hint"] == "…1234" and "secret" not in str(s)

    def run(**kw):
        job = c.post("/api/intent", json={"spec": "99.211", "version": "16.1.0", "skip_uncovered": False, **kw}).json()
        while (j := next(x for x in c.get("/api/jobs").json() if x["id"] == job["id"]))["state"] == "running":
            time.sleep(0.05)
        return j

    j = run(limit=2)
    assert j["state"] == "done" and j["message"].startswith("AI 요약 2개") and len(seen) == 2
    assert seen[0][:2] == ("gemini-x", "AIza-secret-1234")
    assert len(library.intent_hashes("99.211", "16.1.0")) == 2
    run(limit=None)  # already summarized blocks are not requested again
    assert len(seen) == len(library.intent_hashes("99.211", "16.1.0"))
    # the Gemini key never goes to another provider's server
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "ping", lambda self: {"key": self.api_key, "url": self.base_url})
    r = c.post("/api/llm/test", json={"provider": "openai", "base_url": "http://llm:8000/v1", "model": "glm-5.3"}).json()
    assert r == {"key": None, "url": "http://llm:8000/v1"}
    s = c.put("/api/llm", json={"provider": "openai", "base_url": "http://llm:8000/v1", "model": "glm-5.3"}).json()
    assert s["provider"] == "openai" and not s["has_key"] and s["providers"]["gemini"]["key_hint"] == "…1234"
    assert c.post("/api/llm/test", json={"provider": "gemini"}).json()["key"] == "AIza-secret-1234"
    # nor to an address typed into the form but not saved
    r = c.post("/api/llm/test", json={"provider": "gemini", "base_url": "http://evil.example/v1"}).json()
    assert r == {"key": None, "url": "http://evil.example/v1"}
    assert c.put("/api/llm", json={"provider": "gemini", "clear_key": True}).json()["has_key"] is False


@needs_pandoc
def test_server_sync_keeps_local_summaries(library, tmp_path):
    import sqlite3

    from specview.store import Library

    dst = sqlite3.connect(tmp_path / "l.sqlite")
    library.db.backup(dst)
    dst.close()
    lib = Library(tmp_path / "l.sqlite")
    ch = build_pack(lib, "99.211", "16.1.0")["changes"][0]
    local = {"format": "specview-annotations/1", "spec": "99.211", "version": "16.1.0", "generated": "a",
             "changes": [{**ch, "intent": {"summary": "mine"}}]}
    assert lib.merge_pack(local) == 1
    server = {**local, "generated": "b", "changes": [{**ch, "intent": None, "agreements": [{"text": "agreed"}]}]}
    lib.import_pack(server)
    row = next(a for a in lib.clause_annotations("99.211", "16.1.0", ch["clause"]) if a["raw_hash"] == ch["raw_hash"])
    assert row["intent"]["summary"] == "mine" and row["agreements"] == [{"text": "agreed"}]
    lib.merge_pack({**local, "changes": [{**ch, "intent": {"summary": "again"}, "agreements": []}]})
    row = next(a for a in lib.clause_annotations("99.211", "16.1.0", ch["clause"]) if a["raw_hash"] == ch["raw_hash"])
    assert row["intent"]["summary"] == "again" and row["agreements"] == [{"text": "agreed"}]


def test_gemini_bad_key_400_and_think_in_ping():
    class Session:
        def __init__(self, resp):
            self.resp, self.n = resp, 0

        def post(self, url, json, headers, timeout):
            self.n += 1
            return self.resp

    bad = Session(Resp(400, [{"error": {"message": "API key not valid. Please pass a valid API key."}}]))
    with pytest.raises(intent.LLMError, match="^API 키가 잘못됐습니다"):
        intent.OpenAICompatIntentModel("https://x", "m", "k", session=bad).intent(
            {"clause": "1", "title": "t", "change": "added", "text": "x"})
    assert bad.n == 1  # a key error is not mistaken for missing json_schema support
    ok = Session(Resp(200, {"choices": [{"message": {"content": "<think>hmm</think>OK"}}]}))
    assert intent.OpenAICompatIntentModel("https://x", "m", session=ok).ping()["reply"] == "OK"


@needs_pandoc
def test_pack_counts_skips_and_limits(library):
    class Model:
        name = "m"

        def intent(self, ch):
            return {"summary": "s"}

    pack = build_pack(library, "99.211", "16.1.0", Model(), skip_uncovered=False, limit=1)
    g = pack["generator"]
    assert g["requested"] == 1 and g["over_limit"] == len(pack["changes"]) - 1
    one = pack["changes"][0]["raw_hash"]
    assert [c["raw_hash"] for c in build_pack(library, "99.211", "16.1.0", only_hashes={one})["changes"]] == [one]


def test_change_evidence_helpers():
    from specview import crdocs
    from specview.pack import relevant_lines, word_changes

    assert word_changes("if neither group, nor sequence hopping shall be used",
                        "if neither group, nor sequence hopping is enabled") == [
        "- …group, nor sequence hopping [shall be used]", "+ …group, nor sequence hopping [is enabled]"]
    cover = {"cr": "0004", "summary": "Clarification of the definition on Point A in 4.4.4.2.\n"
             "Clarification to PUSCH DM-RS sequence hopping; a UE does not support combined sequence and group hopping.\n"
             "R1-1813585 Differentiation of PxSCH allocation length", "reason": "", "consequences": "", "wg_tdoc": ""}
    ch = {"text": "if group hopping is enabled and sequence hopping is disabled", "title": "Sequence generation",
          "changes": ["+ …if group hopping [is enabled and]", "- …but not sequence hopping [shall be used]"]}
    assert relevant_lines([cover], ch)[0].startswith("[CR0004 summary] Clarification to PUSCH DM-RS sequence hopping")
    assert crdocs.listed_tdocs(cover) == ["R1-1813585"]
    assert crdocs.wg_meetings("from RAN1#94bis and RAN1#95") == [
        "https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_94b", "https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_95"]
