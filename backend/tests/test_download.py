import zipfile
from pathlib import Path

import pytest

from specview import download


def test_legacy_doc_without_converter_is_skippable(tmp_path, monkeypatch):
    monkeypatch.setattr(download.shutil, "which", lambda name: None)
    monkeypatch.setattr(download.os, "name", "posix")
    zp = tmp_path / "38211-f00.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("38211-f00.doc", b"\xd0\xcf\x11\xe0")
    with pytest.raises(download.LegacyDocError):
        download._extract_docx(zp, tmp_path)
    assert not list(tmp_path.glob("*.docx"))


def test_split_spec_parts_extracted_in_order(tmp_path):
    zp = tmp_path / "36211-ef0.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for n in ("36211-ef0_s06-s08.docx", "36211-ef0_cover.docx", "36211-ef0_s09-sxx.docx", "36211-ef0_s00-s05.docx"):
            z.writestr(n, b"x" * (5 if "cover" in n else 50))
    got = download._extract_docx(zp, tmp_path)
    assert [p.name for p in got] == ["36211-ef0_cover.docx", "36211-ef0_s00-s05.docx",
                                     "36211-ef0_s06-s08.docx", "36211-ef0_s09-sxx.docx"]
    assert download._cached_parts(zp, tmp_path) == got
    got[2].unlink()  # an older run kept only one part: extract again
    assert download._cached_parts(zp, tmp_path) is None


def test_numbered_parts_in_natural_order(tmp_path):
    names = ["38133-k10_10_sA0606-sA0611.docx", "38133-k10_0_cover.docx", "38133-k10_2_s8-s8.docx",
             "38133-k10_1_s0-s7.docx", "38133-k10_21_sB01-sXX.docx"]
    zp = tmp_path / "38133-k10.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for n in names:
            z.writestr(n, b"x")
    with zipfile.ZipFile(zp) as z:
        assert download._word_parts(z) == ["38133-k10_0_cover.docx", "38133-k10_1_s0-s7.docx", "38133-k10_2_s8-s8.docx",
                                           "38133-k10_10_sA0606-sA0611.docx", "38133-k10_21_sB01-sXX.docx"]


def test_catalog_kept_until_refresh(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from specview.api import create_app

    calls = []

    def fake(series):
        calls.append(series)
        if len(calls) == 3:
            raise RuntimeError("offline")
        return [{"number": f"{series}.211", "type": "TS", "title": f"v{len(calls)}", "withdrawn": False}]

    monkeypatch.setattr(download, "catalog", fake)
    c = TestClient(create_app(tmp_path))
    assert c.get("/api/catalog/38").json()[0]["title"] == "v1"
    assert c.get("/api/catalog/38").json()[0]["title"] == "v1" and len(calls) == 1  # no network after the first time
    assert c.get("/api/catalog/38?refresh=true").json()[0]["title"] == "v2"
    assert c.get("/api/catalog/38?refresh=true").status_code == 502  # refresh failed ...
    assert c.get("/api/catalog/38").json()[0]["title"] == "v2"  # ... the saved list stays


def test_more_split_part_names(tmp_path):
    names = ["38101-1-k10_s07-XX.docx", "38101-1-k10_cover.docx", "38101-1-k10_sAnnexes.docx",
             "38101-1-k10_s00-05.docx", "38101-1-k10_s06-06.docx", "Band Combinations.zip"]
    zp = tmp_path / "38101-1-k10.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for n in names:
            z.writestr(n, b"x")
    with zipfile.ZipFile(zp) as z:
        assert download._word_parts(z) == ["38101-1-k10_cover.docx", "38101-1-k10_s00-05.docx", "38101-1-k10_s06-06.docx",
                                           "38101-1-k10_s07-XX.docx", "38101-1-k10_sAnnexes.docx"]


def test_fetch_job_reports_specs_without_versions_and_uses_official_title(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from specview.api import create_app

    monkeypatch.setattr(download, "list_remote", lambda spec, *a: [])
    monkeypatch.setattr(download, "catalog", lambda series: [])
    c = TestClient(create_app(tmp_path))
    job = c.post("/api/fetch", json={"specs": ["38.141"], "with_crs": False}).json()
    while (j := c.get("/api/jobs").json()[0])["state"] == "running":
        time.sleep(0.05)
    assert j["state"] == "done" and "받을 버전 없음: 38.141" in j["message"]


def test_tr_with_separate_annex_files(tmp_path):
    zp = tmp_path / "38857-h00.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for n, size in (("38857-h00 annex C1.1.8-11.docx", 900), ("38857-h00.docx", 100),
                        ("38857-h00 annex C1.1.1-7.docx", 800), ("38857-h00 annex C3.docx", 10)):
            z.writestr(n, b"x" * size)
    with zipfile.ZipFile(zp) as z:
        assert download._word_parts(z) == ["38857-h00.docx", "38857-h00 annex C1.1.1-7.docx",
                                           "38857-h00 annex C1.1.8-11.docx", "38857-h00 annex C3.docx"]


def test_dotted_part_names_ordered_by_clause():
    # 38.133 v20.0.0: no index in the names, and "A.406" means A.4.6 while "A.10" means A.10
    from specview.parse_docx import Clause, ParsedSpec, _part_order

    names = ["38133-k00_sB.1-XX.docx", "38133-k00_cover.docx", "38133-k00_s0-7.docx", "38133-k00_sA.10-A.10.docx",
             "38133-k00_sA.4-A.406.docx", "38133-k00_sA.407-A.5.docx", "38133-k00_sA.1-A.3.docx"]
    assert all(download._PART.search(n) for n in names)
    first = {"cover": None, "s0-7": "1", "sA.1-A.3": "Annex A", "sA.4-A.406": "A.4", "sA.407-A.5": "A.4.7",
             "sA.10-A.10": "A.10", "sB.1-XX": "B.1"}
    parts = [ParsedSpec(n, [Clause("front", "", "", 0)] + ([Clause(first[n], first[n], "", 1)] if first[n] else []), {})
             for n in ["sB.1-XX", "cover", "s0-7", "sA.10-A.10", "sA.4-A.406", "sA.407-A.5", "sA.1-A.3"]]
    assert [p.title for p in sorted(parts, key=_part_order)] == [
        "cover", "s0-7", "sA.1-A.3", "sA.4-A.406", "sA.407-A.5", "sA.10-A.10", "sB.1-XX"]


def test_libreoffice_gets_its_own_profile(tmp_path, monkeypatch):
    # two LibreOffice runs on one profile: the second fails (seen converting .doc files in parallel)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).with_suffix(".docx").write_bytes(b"PK")

    monkeypatch.setattr(download.shutil, "which", lambda name: "/usr/bin/soffice")
    monkeypatch.setattr(download.subprocess, "run", run)
    for name in ("a.doc", "b.doc"):
        (tmp_path / name).write_bytes(b"x")
        assert download._convert_doc(tmp_path / name).suffix == ".docx"
    profiles = [next(a for a in c if a.startswith("-env:UserInstallation=")) for c in calls]
    assert len(set(profiles)) == 2
