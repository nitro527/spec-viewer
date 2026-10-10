from tests.conftest import needs_pandoc

pytestmark = needs_pandoc


def _clause(lib, version, key):
    return next(c for c in lib.clauses("99.211", version, 0, 100) if c["key"] == key)


def test_versions_and_toc(library):
    assert [v["version"] for v in library.versions("99.211")] == ["15.0.0", "16.0.0", "16.1.0", "16.2.0"]
    toc = {t["key"]: t for t in library.toc("99.211", "16.2.0")}
    assert toc["7.4.1.1.3"]["status"] == "added"
    assert toc["4.3.1"]["last_changed"] == "16.1.0"
    assert toc["1"]["last_changed"] is None


def test_word_level_blame(library):
    c = _clause(library, "16.2.0", "7.4.1.1.1")
    first = c["blocks"][0]
    assert first["introduced"] == "15.0.0" and first["changed"] == "16.0.0"
    assert '<span class="vc" data-v="16.0.0"> of TS 38.211</span>' in first["html"]


def test_deletion_recorded_in_version_it_happened(library):
    assert _clause(library, "16.1.0", "7.4.1.1.1")["deleted"][0]["version"] == "16.1.0"
    assert _clause(library, "16.2.0", "7.4.1.1.1")["deleted"] == []


def test_equation_survives(library):
    c = _clause(library, "16.2.0", "4.3.1")
    assert any("math inline" in b["html"] for b in c["blocks"])


def test_change_history(library):
    crs = library.crs("99.211", "16.1.0")
    assert crs and crs[0]["cr"] == "0123" and crs[0]["cat"] == "F"


def test_compare(library):
    cmp = library.compare("99.211", "16.0.0", "16.2.0")
    keys = {c["key"]: c["status"] for c in cmp["clauses"]}
    assert keys["7.4.1.1.3"] == "added"
    assert keys["4.3.1"] == "modified"
    assert "4.2" not in keys
    assert [c["cr"] for c in cmp["crs"]] == ["0123", "0140"]


def test_search(library):
    hits = library.search("99.211", "16.2.0", "pseudo-random")
    assert hits and all(h["key"] == "7.4.1.1.1" for h in hits)


def test_storage_is_deduplicated(library):
    blocks = library.db.execute("SELECT COUNT(*) FROM block_ver").fetchone()[0]
    blobs = library.db.execute("SELECT COUNT(*) FROM blob").fetchone()[0]
    assert blobs < blocks / 2
