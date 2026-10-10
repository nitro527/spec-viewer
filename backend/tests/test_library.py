from tests.conftest import needs_pandoc

pytestmark = needs_pandoc


def _clause(lib, version, key):
    return next(c for c in lib.clauses("99.211", version, 0, 100) if c["key"] == key)


def test_versions_and_toc(library):
    assert [v["version"] for v in library.versions("99.211")] == ["15.0.0", "16.0.0", "16.1.0", "16.2.0"]
    toc = {t["key"]: t for t in library.toc("99.211", "16.2.0")}
    assert toc["7.4.1.1.3"]["status"] == "added"
    assert toc["4.3.1"]["last_changed"] == "16.2.0"  # note added in 16.1.0, moved out in 16.2.0
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
    c = _clause(library, "16.1.0", "4.3.1")
    assert any("math inline" in b["html"] for b in c["blocks"])


def test_change_history(library):
    crs = library.crs("99.211", "16.1.0")
    assert crs and crs[0]["cr"] == "0123" and crs[0]["cat"] == "F"


def test_compare(library):
    cmp = library.compare("99.211", "16.0.0", "16.2.0")
    keys = {c["key"]: c["status"] for c in cmp["clauses"]}
    assert keys["7.4.1.1.3"] == "added"
    assert "4.3.1" not in keys  # the note was added and then moved away again: net unchanged
    assert keys["7.4.1.1.1"] == "modified"
    assert "4.2" not in keys
    assert [c["cr"] for c in cmp["crs"]] == ["0123", "0140"]


def test_search(library):
    hits = library.search("99.211", "16.2.0", "pseudo-random")
    assert hits and all(h["key"] == "7.4.1.1.1" for h in hits)


def test_storage_is_deduplicated(library):
    blocks = library.db.execute("SELECT COUNT(*) FROM block_ver").fetchone()[0]
    blobs = library.db.execute("SELECT COUNT(*) FROM blob").fetchone()[0]
    assert blobs < blocks / 2


def test_stats(library):
    st = library.stats("99.211")
    by_v = {v["version"]: v for v in st["versions"]}
    assert by_v["16.2.0"]["added"] == 1 and by_v["16.1.0"]["crs"] == {"F": 1}
    seq = next(c for c in st["clauses"] if c["key"] == "7.4.1.1.1")
    assert seq["changes"] == ["16.0.0", "16.1.0", "16.2.0"]


def test_moved_paragraph_keeps_origin(library):
    c = _clause(library, "16.2.0", "7.4.1.1.1")
    moved = next(b for b in c["blocks"] if "shared spectrum" in b["html"])
    assert moved["introduced"] == "16.1.0"
    assert moved["history"][-1] == ["16.2.0", "moved", "4.3.1"]
    src = _clause(library, "16.2.0", "4.3.1")
    assert src["deleted"][0]["moved_to"] == "7.4.1.1.1"


def test_compare_marks_moves(library):
    cmp = library.compare("99.211", "16.1.0", "16.2.0")
    ops = {(c["key"], b["op"]) for c in cmp["clauses"] for b in c["blocks"]}
    assert ("4.3.1", "move-out") in ops and ("7.4.1.1.1", "move-in") in ops


def test_annotation_pack_roundtrip(library):
    c = _clause(library, "16.2.0", "7.4.1.1.1")
    moved = next(b for b in c["blocks"] if "shared spectrum" in b["html"])
    pack = {"format": "specview-annotations/1", "spec": "99.211", "version": "16.2.0", "generated": "now",
            "changes": [{"clause": "7.4.1.1.1", "raw_hash": moved["raw_hash"], "change": "moved",
                         "crs": [], "features": [], "agreements": [], "intent": {"summary": "moved"}}]}
    assert library.import_pack(pack) == 1
    ann = library.clause_annotations("99.211", "16.2.0", "7.4.1.1.1")
    assert ann[0]["intent"]["summary"] == "moved" and ann[0]["version"] == "16.2.0"
    library.set_setting("annotation_url", "http://x")
    assert library.get_setting("annotation_url") == "http://x"
