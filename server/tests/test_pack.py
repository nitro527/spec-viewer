import json
from types import SimpleNamespace

from annotation_server.app import create_app
from annotation_server.chairnotes import meeting_folder, split_agreements
from annotation_server.pack import build_pack, dumps


class FakeModel:
    name = "fake"

    def __init__(self):
        self.seen = []

    def intent(self, change):
        self.seen.append(change)
        return {"summary": "note added", "motivation": change["cr_covers"][0]["reason"] if change["cr_covers"] else "",
                "impact": "-", "cr": "0123", "confidence": "high"}


def test_pack_maps_changes_to_crs_and_intent(library):
    model = FakeModel()
    pack = build_pack(library, "99.211", "16.1.0", model)
    assert pack["format"] == "specview-annotations/1" and pack["generator"]["intent_model"] == "fake"
    note = next(c for c in pack["changes"] if c["clause"] == "4.3.1")
    assert note["change"] == "added" and len(note["raw_hash"]) == 20
    assert [c["cr"] for c in note["crs"]] == ["0123"]
    assert note["intent"]["motivation"] == "Missing note on shared spectrum."
    assert "shared spectrum" in model.seen[0]["text"]
    json.loads(dumps(pack))


def test_pack_without_model_has_no_intent(library):
    pack = build_pack(library, "99.211", "16.2.0")
    assert all(c["intent"] is None for c in pack["changes"])
    assert any(c["change"] == "moved" for c in pack["changes"])


def test_server_serves_packs(library, tmp_path):
    from fastapi.testclient import TestClient

    (tmp_path / "99.211").mkdir()
    (tmp_path / "99.211" / "16.1.0.json").write_text(dumps(build_pack(library, "99.211", "16.1.0")), encoding="utf-8")
    client = TestClient(create_app(tmp_path))
    idx = client.get("/v1/packs").json()
    assert idx[0]["spec"] == "99.211" and idx[0]["url"].endswith("16.1.0.json")
    assert client.get(idx[0]["url"]).json()["version"] == "16.1.0"
    assert client.get("/v1/packs/99.211/9.9.9.json").status_code == 404


def test_chairnotes_helpers():
    assert meeting_folder("RAN1#100b-e").endswith("/TSGR1_100b_e")
    blocks = split_agreements("intro\nAgreement:\nA uses X.\nConclusion:\nNo change.\n")
    assert blocks == ["Agreement:\nA uses X.", "Conclusion:\nNo change."]


def test_claude_model_request_shape():
    from annotation_server.llm import ClaudeIntentModel

    calls = {}

    class FakeMessages:
        def create(self, **kw):
            calls.update(kw)
            text = json.dumps({"summary": "s", "motivation": "m", "impact": "i", "cr": "0123", "confidence": "high"})
            return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                                   content=[SimpleNamespace(type="text", text=text)])

    client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    out = ClaudeIntentModel(client=client).intent({"clause": "4.3.1", "title": "Frames", "change": "added",
                                                   "text": "new note", "old_text": None, "cr_covers": []})
    assert out["cr"] == "0123" and out["model"] == "claude-opus-5-5"
    assert calls["model"] == "claude-opus-5-5" and calls["output_config"]["format"]["type"] == "json_schema"
    assert calls["fallbacks"] == "default"


def test_openai_compatible_model(monkeypatch):
    from annotation_server.llm_openai import OpenAICompatIntentModel, _parse_json

    sent = []

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    class Session:
        def post(self, url, json, headers, timeout):
            sent.append((url, json["response_format"]["type"], headers.get("Authorization")))
            if json["response_format"]["type"] == "json_schema":
                return Resp(400, {})  # server without json_schema support
            content = '<think>reasoning</think>```json\n{"summary":"s","motivation":"m","impact":"i","cr":"0123","confidence":"low"}\n```'
            return Resp(200, {"model": "glm-5.3", "choices": [{"message": {"content": content}}]})

    m = OpenAICompatIntentModel("http://llm:8000/v1/", "glm-5.3", api_key="k", session=Session())
    out = m.intent({"clause": "4.3.1", "title": "Frames", "change": "added", "text": "t", "cr_covers": []})
    assert out["cr"] == "0123" and out["model"] == "glm-5.3"
    assert [s[1] for s in sent] == ["json_schema", "json_object"]
    assert sent[0][0] == "http://llm:8000/v1/chat/completions" and sent[0][2] == "Bearer k"
    assert _parse_json('noise {"a": 1} tail') == {"a": 1}
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    g = OpenAICompatIntentModel.from_preset("gemini")
    assert g.api_key == "g" and g.base_url.endswith("/v1beta/openai")
