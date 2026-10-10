import zipfile

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
