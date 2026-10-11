"""Keeping up: new versions on 3gpp.org, and bookmarked clauses that changed since they were marked."""
import sqlite3

from specview import download
from tests.conftest import needs_pandoc


def _client(library, tmp_path, monkeypatch, remote):
    from fastapi.testclient import TestClient

    from specview.api import create_app

    dst = sqlite3.connect(tmp_path / "library.sqlite")
    library.db.backup(dst)
    dst.close()
    calls = []

    def list_remote(spec, *a):
        calls.append(spec)
        return [download.RemoteVersion(spec, download.Version.parse(v), f"https://x/{v}") for v in remote]

    monkeypatch.setattr(download, "list_remote", list_remote)
    return TestClient(create_app(tmp_path)), calls


@needs_pandoc
def test_new_versions_on_3gpp_are_listed_and_the_listing_is_cached(library, tmp_path, monkeypatch):
    latest = library.versions("99.211")[-1]["version"]
    c, calls = _client(library, tmp_path, monkeypatch, ["15.0.0", latest, "19.9.0", "1.0.0"])
    r = c.get("/api/updates").json()
    assert r["specs"] == [{"spec": "99.211", "latest": latest, "new": ["19.9.0"]}] and r["checked"]  # no drafts
    c.get("/api/updates")
    assert calls == ["99.211"]  # cached
    c.get("/api/updates?refresh=true")
    assert calls == ["99.211", "99.211"]


@needs_pandoc
def test_watched_clause_changed_after_it_was_bookmarked(library, tmp_path, monkeypatch):
    c, _ = _client(library, tmp_path, monkeypatch, [])
    vers = [v["version"] for v in c.get("/api/specs/99.211/versions").json()]
    toc = c.get(f"/api/specs/99.211/{vers[-1]}/toc").json()
    changed = next(t for t in toc if t["last_changed"] and t["last_changed"] != vers[0])
    same = next(t for t in toc if t["last_changed"] in (None, vers[0]) and t["key"] != changed["key"])
    for t in (changed, same):
        c.put(f"/api/specs/99.211/notes/{t['key']}", json={"bookmark": True, "version": vers[0]})
    w = c.get("/api/watch").json()
    assert [(x["clause_key"], x["status"], x["since"]) for x in w] == [(changed["key"], "changed", vers[0])]
    assert c.post("/api/watch/seen", json={"spec": "99.211", "clause_key": changed["key"]}).json() == []
