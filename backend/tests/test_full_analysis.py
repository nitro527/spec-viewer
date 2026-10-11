"""The overnight "analyse everything" job: every version, then CRs, then work items; resumable and stoppable."""
import json
import sqlite3
import time

from specview import api as api_mod
from specview import cranalysis, intent
from tests.conftest import needs_pandoc


def _client(library, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from specview.store import Library

    dst = sqlite3.connect(tmp_path / "library.sqlite")
    library.db.backup(dst)
    dst.close()
    lib = Library(tmp_path / "library.sqlite")
    for i, c in enumerate([c for c in lib.crs("99.211") if c["cr"]][:3]):
        cover = {"title": f"t{i}", "reason": "r", "summary": "s", "clauses_raw": "7.4.1.1.1", "clauses": ["7.4.1.1.1"],
                 "wi_code": "NR_X-Core", "wg_tdoc": "", "category": c["cat"]}
        with lib.db:
            lib.db.execute("INSERT OR REPLACE INTO cr_detail VALUES (?,?,?,?,?,?,?)",
                           ("99.211", c["cr"], c["rev"], c["tdoc"], "ok", json.dumps(cover), None))
    lib.close()
    monkeypatch.setattr(cranalysis, "evidence", lambda lib_, spec, row, cache, finder=None: {
        "spec": spec, "cr": row["cr"], "tdoc": row["tdoc"], "cover": row["detail"], "changes": [], "drafts": [],
        "agreements": [], "fl_summaries": []})
    monkeypatch.setattr(api_mod, "FULL_RETRY_WAIT", 0.01)
    c = TestClient(api_mod.create_app(tmp_path))
    c.put("/api/llm", json={"provider": "openai", "base_url": "http://llm:8000/v1", "model": "glm"})
    return c


def _run(c, body):
    job = c.post("/api/full-analysis", json=body).json()
    while (j := next(x for x in c.get("/api/jobs").json() if x["id"] == job["id"]))["state"] == "running":
        time.sleep(0.05)
    return j


@needs_pandoc
def test_full_analysis_covers_everything_once(library, tmp_path, monkeypatch):
    calls = {"intent": 0, "cr": 0, "wi": 0}

    def fake_intent(self, ch):
        calls["intent"] += 1
        return {"summary": "p", "motivation": "m", "impact": "i", "cr": "", "confidence": "low"}

    monkeypatch.setattr(intent.OpenAICompatIntentModel, "intent", fake_intent)
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "analyze_cr", lambda self, ev: calls.__setitem__(
        "cr", calls["cr"] + 1) or {"summary": "c", "background": "", "changes": [], "decision": "", "impact": "",
                                   "confidence": "low"})
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "summarize_wi", lambda self, wi: calls.__setitem__(
        "wi", calls["wi"] + 1) or {"overview": "o", "motivation": "", "spec_changes": [], "timeline": [], "impact": "",
                                   "open_points": "", "confidence": "low"})
    c = _client(library, tmp_path, monkeypatch)
    j = _run(c, {"skip_uncovered": False})
    assert j["state"] == "done" and "완료" in j["message"]
    assert j["counts"]["intents"] == calls["intent"] > 0  # every changed paragraph of every version
    assert j["counts"]["cr_analyses"] == calls["cr"] == 3 and j["counts"]["wi_summaries"] == calls["wi"] == 1
    before = dict(calls)
    again = _run(c, {"skip_uncovered": False})  # nothing left: no request at all
    assert again["state"] == "done" and calls == before and again["counts"]["intents"] == 0


@needs_pandoc
def test_full_analysis_stops_when_asked_and_when_the_server_keeps_failing(library, tmp_path, monkeypatch):
    c = _client(library, tmp_path, monkeypatch)

    def failing(self, ch):
        raise intent.LLMError("LLM 서버에 연결할 수 없습니다")

    monkeypatch.setattr(intent.OpenAICompatIntentModel, "intent", failing)
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "ping", failing)  # the server is down
    j = _run(c, {"skip_uncovered": False, "cr_analyses": False, "wi_summaries": False})
    assert j["state"] == "error" and "계속 실패" in j["message"] and "이어서" in j["message"], j

    def slow(self, ch):
        time.sleep(0.05)
        return {"summary": "p", "motivation": "m", "impact": "i", "cr": "", "confidence": "low"}

    monkeypatch.setattr(intent.OpenAICompatIntentModel, "intent", slow)
    job = c.post("/api/full-analysis", json={"skip_uncovered": False}).json()
    assert c.post("/api/intent", json={"spec": "99.211", "version": "16.2.0"}).status_code == 409  # one AI job
    time.sleep(0.3)
    c.post(f"/api/jobs/{job['id']}/cancel")
    while (j := next(x for x in c.get("/api/jobs").json() if x["id"] == job["id"]))["state"] == "running":
        time.sleep(0.05)
    assert j["state"] == "done" and "중지" in j["message"] and 0 < j["counts"]["intents"]


@needs_pandoc
def test_full_analysis_skips_paragraphs_that_fail_while_the_server_answers(library, tmp_path, monkeypatch):
    c = _client(library, tmp_path, monkeypatch)
    seen = []

    def bad_json(self, ch):
        seen.append(ch["raw_hash"])
        raise ValueError("not JSON")

    monkeypatch.setattr(intent.OpenAICompatIntentModel, "intent", bad_json)
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "ping", lambda self: {"reply": "OK"})
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "analyze_cr", lambda self, ev: {
        "summary": "c", "background": "", "changes": [], "decision": "", "impact": "", "confidence": "low"})
    j = _run(c, {"skip_uncovered": False, "cr_analyses": False, "wi_summaries": False})
    assert j["state"] == "done" and j["counts"]["failed"] == len(seen) > 0
    # each failing paragraph is asked once per version and then left for the next run (no endless retry)
