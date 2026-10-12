"""AI chat: the model looks things up with tools (or gets search results in one prompt) and cites sources."""
import json
import sqlite3
import time

from specview import chat, intent
from tests.conftest import needs_pandoc


class FakeModel:
    """Asks for the tools in ``plan`` one after another, then answers citing what it was given."""

    name = model = "fake"

    def __init__(self, plan, tools_ok=True):
        self.plan, self.tools_ok, self.seen = list(plan), tools_ok, []

    def complete(self, messages, tools=None):
        self.seen.append(messages)
        if tools and not self.tools_ok:
            raise intent.ToolsUnsupported("no tools")
        if not tools and messages[0]["content"] == chat._QUERY_PROMPT:
            return {"content": json.dumps({"queries": ["sequence generation"], "spec": "99.211"})}
        if tools and self.plan:
            name, args = self.plan.pop(0)
            return {"content": "", "tool_calls": [{"id": "1", "type": "function",
                                                   "function": {"name": name, "arguments": json.dumps(args)}}]}
        tags = [t for m in messages if m["role"] in ("tool", "user") for t in __import__("re").findall(
            r'"source": "([^"]+)"', m["content"])]
        return {"content": f"답 [[{tags[0]}]]" if tags else "자료에 없음", "model": "fake"}


@needs_pandoc
def test_tools_answer_with_sources(library):
    m = FakeModel([("search", {"query": "sequence generation", "spec": "99211"}),
                   ("read_clause", {"spec": "99.211", "clause": "7.4.1.1.1"}),
                   ("clause_history", {"spec": "99.211", "clause": "7.4.1.1.1"}),
                   ("compare_clause", {"spec": "99.211", "clause": "7.4.1.1.1", "old": "15.0.0"})])
    steps = []
    r = chat.answer(m, library, "시퀀스 생성은 어떻게 바뀌었어?", step=steps.append)
    assert r["mode"] == "tools" and len(steps) == 4 and steps[1] == "읽기: 99.211 §7.4.1.1.1"
    tag = r["answer"].split("[[")[1].split("]]")[0]
    assert r["sources"][tag]["type"] == "clause" and r["sources"][tag]["spec"] == "99.211"
    tool_msgs = [x for x in m.seen[-1] if x["role"] == "tool"]
    assert '"text"' in tool_msgs[1]["content"] and '"changed_in"' in tool_msgs[2]["content"]
    assert "[+" in tool_msgs[3]["content"] or "[-" in tool_msgs[3]["content"]


@needs_pandoc
def test_server_without_tools_gets_search_results_in_one_prompt(library):
    m = FakeModel([], tools_ok=False)
    r = chat.answer(m, library, "sequence generation?", context={"spec": "99.211", "clause": "7.4.1.1.1"})
    assert r["mode"] == "retrieval" and r["sources"] and "[[" in r["answer"]
    assert any(t.startswith("99.211 §7.4.1.1.1 v") for t in r["sources"])  # the asked-from clause is a source
    assert "<material>" in m.seen[-1][-1]["content"] and "Selected paragraph" not in m.seen[-1][-1]["content"]


@needs_pandoc
def test_chat_api_keeps_the_conversation(library, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from specview.api import create_app

    dst = sqlite3.connect(tmp_path / "library.sqlite")
    library.db.backup(dst)
    dst.close()
    seen = []

    def fake_answer(model, lib, question, history, context, **kw):
        seen.append((question, [h["content"] for h in history], context))
        kw["step"]("검색: x")
        return {"answer": f"A:{question}", "sources": {}, "steps": ["검색: x"], "mode": "tools", "model": "glm"}

    monkeypatch.setattr(chat, "answer", fake_answer)
    c = TestClient(create_app(tmp_path))
    c.put("/api/llm", json={"provider": "openai", "base_url": "http://llm:8000/v1", "model": "glm"})

    def ask(body):
        r = c.post("/api/chat", json=body).json()
        while (j := next(x for x in c.get("/api/jobs").json() if x["id"] == r["job"]["id"]))["state"] == "running":
            time.sleep(0.05)
        return r["chat_id"], j

    cid, j = ask({"question": "Q1", "context": {"spec": "99.211", "clause": "4.3.1"}})
    assert j["state"] == "done" and j["steps"] == ["검색: x"]
    cid2, _ = ask({"question": "Q2", "chat_id": cid})
    assert cid2 == cid and seen[1] == ("Q2", ["Q1", "A:Q1"], None)
    conv = c.get(f"/api/chats/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]
    assert conv["title"] == "Q1" and conv["messages"][0]["context"]["clause"] == "4.3.1"
    assert [x["id"] for x in c.get("/api/chats").json()] == [cid]
    # a question left without an answer (the app closed meanwhile) does not shift the turns after it
    from specview.store import Library
    lib = Library(tmp_path / "library.sqlite")
    msgs = c.get(f"/api/chats/{cid}").json()["messages"] + [{"role": "user", "content": "lost"}]
    lib.save_chat(cid, "Q1", msgs, "2026-01-01T00:00:00")
    ask({"question": "Q3", "chat_id": cid})
    ask({"question": "Q4", "chat_id": cid})  # the old pairing sent ("lost", "Q3") as a turn here
    assert seen[-1][1] == ["Q1", "A:Q1", "Q2", "A:Q2", "Q3", "A:Q3"]
    c.delete(f"/api/chats/{cid}")
    assert c.get("/api/chats").json() == []


@needs_pandoc
def test_tool_calls_from_loose_servers_are_normalised(library):
    sent = []

    class Loose:
        model = name = "loose"

        def __init__(self):
            self.n = 0

        def complete(self, messages, tools=None):
            sent.append([dict(m) for m in messages])
            self.n += 1
            if self.n == 1:  # dict arguments, no id, no type, a number for the clause
                return {"content": "", "tool_calls": [{"function": {"name": "read_clause",
                                                                    "arguments": {"spec": 99.211, "clause": 7.4}}}]}
            if self.n == 2:  # arguments that are not an object
                return {"content": "", "tool_calls": [{"id": "x", "function": {"name": "search", "arguments": "null"}}]}
            return {"content": "끝"}

    r = chat.answer(Loose(), library, "q")
    assert r["answer"] == "끝"
    echoed = [m for m in sent[1] if m["role"] == "assistant"][0]["tool_calls"][0]
    assert echoed["id"] and echoed["type"] == "function" and isinstance(echoed["function"]["arguments"], str)
    tool_reply = [m for m in sent[1] if m["role"] == "tool"][0]
    assert tool_reply["tool_call_id"] == echoed["id"] and "error" in tool_reply["content"] or "text" in tool_reply["content"]


@needs_pandoc
def test_stop_ends_the_answer_between_requests(library):
    import pytest

    m = FakeModel([("list_specs", {}), ("list_specs", {}), ("list_specs", {})])
    asked = []
    with pytest.raises(chat.Stopped):
        chat.answer(m, library, "스펙 목록?", should_stop=lambda: len(m.seen) >= 2, step=asked.append)
    assert len(m.seen) == 2  # no request after the stop


@needs_pandoc
def test_referenced_by_finds_citing_paragraphs(library):
    # the fixture's 7.4.1.1.1 says "... clause 5.2.1 of [4, TS 38.211]"; "Table 4.2-1" is no citation of clause 1
    assert library.referenced_by("99.211", "1") == []
    hits = library.referenced_by("38.211", "5.2.1")
    assert [(h["spec"], h["number"]) for h in hits] == [("99.211", "7.4.1.1.1")] and "[[5.2.1]]" in hits[0]["snippet"]
    out = json.loads(chat.SpecTools(library).call("referenced_by", {"spec": "38.211", "clause": "5.2.1"}))
    assert out["count"] == 1 and out["citing"][0]["source"].startswith("99.211 §7.4.1.1.1")


def test_answer_cut_at_the_length_limit_says_so():
    assert chat._cut_note({"content": "절반", "finish_reason": "length"}).startswith("절반\n\n_(답변이")
    assert chat._cut_note({"content": "전부", "finish_reason": "stop"}) == "전부"


@needs_pandoc
def test_tool_call_written_as_text_falls_back_to_search_results(library):
    class TextCalls(FakeModel):
        def complete(self, messages, tools=None):
            if tools:
                self.seen.append(messages)
                return {"content": '<tool_call>search\n<arg_key>query</arg_key><arg_value>x</arg_value></tool_call>'}
            return super().complete(messages, tools)

    r = chat.answer(TextCalls([]), library, "sequence generation?")
    assert r["mode"] == "retrieval" and "<tool_call>" not in r["answer"]
