import zipfile

from docx import Document

from specview import crdocs


def _cr_docx(path):
    d = Document()
    t = d.add_table(rows=2, cols=7)
    t.cell(0, 0).text = "CHANGE REQUEST"
    for i, v in enumerate(["38.211", "CR", "0123", "rev", "1", "Current version:", "16.0.0"]):
        t.cell(1, i).text = v
    d.add_table(rows=1, cols=2).cell(0, 0).text = "Proposed change affects:"
    t = d.add_table(rows=7, cols=4)
    rows = [("Title:", "Corrections to NR-U"), ("Source to WG:", "Ericsson"),
            ("Category:", "F"), ("Reason for change:", "Misalignment with RAN2."),
            ("Summary of change:", "Fix the LBT parameter."), ("Clauses affected:", "4.3.1, Table 7.4.1.5.3-1")]
    for r, (k, v) in enumerate(rows):
        t.cell(r, 0).text, t.cell(r, 1).text = k, v
    c = t.rows[6].cells
    c[0].text, c[1].text, c[2].text, c[3].text = "Work item code:", "NR_unlic-Core", "Date:", "2020-02-28"
    d.save(path)


def test_meeting_folder():
    assert crdocs.meeting_folder("RP-200186", "RAN#87-e").endswith("/tsg_ran/TSG_RAN/TSGR_87e")
    assert crdocs.meeting_folder("RP-242929", "RAN#106").endswith("/TSGR_106")
    assert crdocs.meeting_folder("R1-1708219", "RAN1#89") is None


def test_parse_clauses():
    assert crdocs.parse_clauses("5.3.1, Table 7.4.1.5.3-1, Annex B") == ["5.3.1", "7.4.1.5.3", "B"]


def test_pack_extract_and_cover(tmp_path):
    doc = tmp_path / "38211_CR0123_(Rel-16)_ R1-2001234.docx"
    _cr_docx(doc)
    pack = tmp_path / "RP-200186.zip"
    with zipfile.ZipFile(pack, "w") as z:
        z.write(doc, doc.name)
        z.writestr("38213_CR0099_(Rel-16)_ R1-2009999.docx", b"other spec")
        z.writestr("RP-200186_cover.doc", b"cover")
    got = crdocs.find_cr_doc(pack, "38.211", "0123", tmp_path / "out")
    cover = crdocs.parse_cover(got)
    assert (cover.spec, cover.cr, cover.rev, cover.current_version) == ("38.211", "0123", "1", "16.0.0")
    assert cover.title == "Corrections to NR-U" and cover.category == "F"
    assert cover.wi_code == "NR_unlic-Core" and cover.date == "2020-02-28"
    assert cover.reason == "Misalignment with RAN2."
    assert cover.clauses == ["4.3.1", "7.4.1.5.3"]
    assert cover.wg_tdoc == "R1-2001234"
