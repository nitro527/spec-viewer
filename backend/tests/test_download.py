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
