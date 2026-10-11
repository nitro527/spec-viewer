"""Paragraph history (every text a paragraph had) and CR-level analyses reused by paragraph summaries."""
import json
import sqlite3
import time

from specview import cranalysis, intent
from tests.conftest import needs_pandoc


def _hash(lib, version, key, ord_):
    return lib.db.execute("SELECT raw_hash FROM block_ver WHERE version_id=? AND clause_key=? AND ord=?",
                          (lib.version_id("99.211", version), key, ord_)).fetchone()[0]


def _block_ord(lib, version, key, introduced):
    c = next(c for c in lib.clauses("99.211", version, 0, 1000) if c["key"] == key)
    return next(i for i, b in enumerate(c["blocks"]) if b["introduced"] == introduced and b["history"])


@needs_pandoc
def test_paragraph_lineage_follows_changes_and_moves(library):
    j = _block_ord(library, "16.2.0", "7.4.1.1.1", "15.0.0")
    steps = library.block_lineage("99.211", "16.2.0", "7.4.1.1.1", _hash(library, "16.2.0", "7.4.1.1.1", j))
    assert [(s["version"], s["change"]) for s in steps] == [("15.0.0", "base"), ("16.0.0", "modified")]
    assert "<ins" in steps[1]["diff"] or "<del" in steps[1]["diff"]
    assert steps[1]["raw_hash"] == _hash(library, "16.2.0", "7.4.1.1.1", j)  # unchanged since: same text

    j = _block_ord(library, "16.2.0", "7.4.1.1.1", "16.1.0")
    steps = library.block_lineage("99.211", "16.2.0", "7.4.1.1.1", _hash(library, "16.2.0", "7.4.1.1.1", j))
    assert [(s["version"], s["change"], s["clause_key"]) for s in steps] == [
        ("16.1.0", "added", "4.3.1"), ("16.2.0", "moved", "7.4.1.1.1")]


def test_cr_changes_reads_revision_marks_by_heading():
    html = ("<h2>5.1 General</h2><p>unchanged</p><p>The UE <del>may</del><ins>shall</ins> report.</p>"
            "<table><tr><td><p>row <ins>new value</ins></p></td></tr></table>"
            "<h3>5.2 Other</h3><p><ins>A new paragraph.</ins></p>")
    groups = cranalysis.cr_changes(html)
    assert groups == [{"clause": "5.1 General", "lines": ["The UE [-may-][+shall+] report.", "row [+new value+]"]},
                      {"clause": "5.2 Other", "lines": ["[+A new paragraph.+]"]}]


@needs_pandoc
def test_cr_analysis_made_once_and_reused_by_paragraph_summaries(library, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from specview.api import create_app
    from specview.store import Library

    dst = sqlite3.connect(tmp_path / "library.sqlite")  # own copy: this test writes
    library.db.backup(dst)
    dst.close()
    lib = Library(tmp_path / "library.sqlite")
    cover = {"title": "Correction to sequences", "reason": "r", "summary": "s", "clauses_raw": "7.4.1.1.1",
             "clauses": ["7.4.1.1.1"], "wi_code": "NR-Core", "wg_tdoc": "", "spec": "99.211", "cr": "0100"}
    with lib.db:
        lib.db.execute("INSERT INTO cr_detail VALUES (?,?,?,?,?,?,?)",
                       ("99.211", "0100", "", "RP-192620", "ok", json.dumps(cover), None))
    rev = next(c["rev"] for c in lib.crs("99.211") if c["cr"] == "0100")
    with lib.db:
        lib.db.execute("UPDATE cr_detail SET rev=? WHERE cr='0100'", (rev,))

    calls, seen = [], []
    monkeypatch.setattr(cranalysis, "evidence", lambda lib_, spec, row, cache, finder=None: {
        "spec": spec, "cr": row["cr"], "tdoc": row["tdoc"], "cover": cover, "changes": [], "drafts": [],
        "agreements": [], "fl_summaries": []})
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "analyze_cr", lambda self, ev: calls.append(ev["cr"]) or {
        "summary": "CR summary", "background": "b", "changes": [{"clause": "7.4.1.1.1", "what": "w"}],
        "decision": "d", "impact": "i", "confidence": "medium", "model": self.model})

    def fake_intent(self, ch):
        seen.append([a["cr"] for a in ch.get("cr_analyses", [])])
        return {"summary": "p", "motivation": "m", "impact": "i", "cr": "0100", "confidence": "low"}

    monkeypatch.setattr(intent.OpenAICompatIntentModel, "intent", fake_intent)
    c = TestClient(create_app(tmp_path))
    c.put("/api/llm", json={"provider": "openai", "base_url": "http://llm:8000/v1", "model": "glm"})

    def run(path, body):
        job = c.post(path, json=body).json()
        while (j := next(x for x in c.get("/api/jobs").json() if x["id"] == job["id"]))["state"] == "running":
            time.sleep(0.05)
        return j

    j = _block_ord(lib, "16.2.0", "7.4.1.1.1", "15.0.0")
    target = {"version": "16.0.0", "clause_key": "7.4.1.1.1", "raw_hash": _hash(lib, "16.0.0", "7.4.1.1.1", j)}
    done = run("/api/intent", {"spec": "99.211", "version": "16.2.0", "targets": [target], "skip_uncovered": False})
    assert done["state"] == "done" and "CR 분석 1개" in done["message"]
    assert calls == ["0100"] and seen == [["0100"]]  # the CR first, then the paragraph with it as evidence
    run("/api/intent", {"spec": "99.211", "version": "16.2.0", "targets": [target], "force": True})
    assert calls == ["0100"] and seen[-1] == ["0100"]  # stored analysis reused, no second CR request

    stored = c.get("/api/specs/99.211/cr-analyses").json()
    assert [(a["cr"], a["summary"], a["version"]) for a in stored] == [("0100", "CR summary", "16.0.0")]
    steps = c.get(f"/api/specs/99.211/16.2.0/clause/7.4.1.1.1/lineage", params={"hash": target["raw_hash"]}).json()
    assert steps[-1]["crs"][0]["cr"] == "0100" and steps[-1]["annotation"]["cr_analyses"][0]["cr"] == "0100"

    again = run("/api/cr-analysis", {"spec": "99.211", "cr": "0100", "tdoc": "RP-192620", "force": True})
    assert again["state"] == "done" and calls == ["0100", "0100"]
    assert c.post("/api/cr-analysis", json={"spec": "99.211", "cr": "0999", "tdoc": "RP-1"}).status_code == 404


@needs_pandoc
def test_phrase_history_follows_a_paragraph_across_versions(library):
    j = _block_ord(library, "16.2.0", "7.4.1.1.1", "16.1.0")  # added in 4.3.1 in 16.1.0, moved in 16.2.0
    text = library.db.execute("SELECT text FROM blob WHERE hash=?",
                              (_hash(library, "16.2.0", "7.4.1.1.1", j),)).fetchone()[0]
    phrase = " ".join(text.split()[:6])
    h = library.phrase_history("99.211", phrase)
    assert h["first"] == "16.1.0" and h["latest_clauses"] >= 1
    ev = {e["version"]: e for e in h["events"]}
    assert "4.3.1" in [c["key"] for c in ev["16.1.0"]["added"]]
    assert "7.4.1.1.1" in [c["key"] for c in ev["16.2.0"]["added"]]
    assert "4.3.1" in [c["key"] for c in ev["16.2.0"]["removed"]]
    assert "[[" in ev["16.1.0"]["added"][0]["snippet"]
    assert library.phrase_history("99.211", "no such phrase anywhere")["first"] is None


@needs_pandoc
def test_work_item_gathers_crs_and_is_summarised_once(library, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from specview.api import create_app
    from specview.store import Library

    dst = sqlite3.connect(tmp_path / "library.sqlite")
    library.db.backup(dst)
    dst.close()
    lib = Library(tmp_path / "library.sqlite")
    rows = [c for c in lib.crs("99.211") if c["cr"]][:2]
    for i, c in enumerate(rows):
        cover = {"title": f"t{i}", "reason": "r", "summary": "s", "clauses_raw": "7.4.1.1.1", "clauses": ["7.4.1.1.1"],
                 "wi_code": "NR_X-Core, TEI16" if i else "NR_X-Core", "wg_tdoc": "", "category": c["cat"]}
        with lib.db:
            lib.db.execute("INSERT OR REPLACE INTO cr_detail VALUES (?,?,?,?,?,?,?)",
                           ("99.211", c["cr"], c["rev"], c["tdoc"], "ok", json.dumps(cover), None))
    lib.save_cr_analysis("99.211", rows[0]["cr"], rows[0]["rev"], rows[0]["tdoc"],
                         {"summary": "CR summary", "background": "b", "decision": "d", "impact": "i"})
    wis = {w["wi"]: w for w in lib.work_items()}
    assert wis["NR_X-Core"]["crs"] == 2 and wis["TEI16"]["specs"] == {"99.211": 1}
    wi = lib.work_item("NR_X-Core")
    assert [c["analysis"] is not None for c in wi["crs"]].count(True) == 1
    assert "CR summary" in intent.wi_prompt(wi) and wi["summary"] is None

    seen = []
    monkeypatch.setattr(intent.OpenAICompatIntentModel, "summarize_wi", lambda self, w: seen.append(len(w["crs"])) or {
        "overview": "o", "motivation": "m", "spec_changes": [], "timeline": [], "impact": "i", "open_points": "",
        "confidence": "medium", "model": self.model})
    c = TestClient(create_app(tmp_path))
    c.put("/api/llm", json={"provider": "openai", "base_url": "http://llm:8000/v1", "model": "glm"})
    job = c.post("/api/wi-summary", json={"wi": "NR_X-Core"}).json()
    while (j := next(x for x in c.get("/api/jobs").json() if x["id"] == job["id"]))["state"] == "running":
        time.sleep(0.05)
    assert j["state"] == "done" and seen == [2]
    got = c.get("/api/work-items/NR_X-Core").json()["summary"]
    assert got["overview"] == "o" and got["crs"] == 2 and got["analysed"] == 1
    assert c.post("/api/wi-summary", json={"wi": "NO_SUCH"}).status_code == 404
