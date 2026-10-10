import pytest

from specview.versions import Version, parse_archive_name


@pytest.mark.parametrize("code,text", [("f00", "15.0.0"), ("g10", "16.1.0"), ("hb0", "17.11.0"),
                                       ("i23", "18.2.3"), ("361000", "36.10.0")])
def test_code_roundtrip(code, text):
    v = Version.from_code(code)
    assert str(v) == text
    assert v.code == code


def test_archive_name():
    assert parse_archive_name("38211-g10.zip") == ("38.211", Version(16, 1, 0))
    assert parse_archive_name("index.html") is None


def test_ordering():
    assert Version.parse("16.10.0") > Version.parse("16.9.0") > Version.parse("15.12.0")


def test_archive_name_with_part_number():
    assert parse_archive_name("38101-1-k10.zip") == ("38.101-1", Version(20, 1, 0))
    assert parse_archive_name("36211-890.zip") == ("36.211", Version(8, 9, 0))
    assert parse_archive_name("38101-121.zip") == ("38.101", Version(1, 2, 1))


def test_heading_numbers_with_letters():
    from specview.crdocs import parse_clauses
    from specview.parse_docx import _split_heading

    assert _split_heading("6.2A.1 UE maximum output power for CA") == ("6.2A.1", "6.2A.1", "UE maximum output power for CA")
    assert _split_heading("5G NR overview")[1] == ""  # not a clause number
    assert parse_clauses("6.2A.1, 6.5D") == ["6.2A.1", "6.5D"]
    assert parse_archive_name("38716-01-01-h00.zip") == ("38.716-01-01", Version(17, 0, 0))


def test_unnumbered_subheadings_keyed_under_their_clause():
    from specview.parse_docx import html_to_clauses

    cl = html_to_clauses("<h1>Foreword</h1><p>f</p><h2>6.2.3 Test A</h2><h4>UE:</h4><p>a</p>"
                         "<h2>6.2.4 Test B</h2><h4>UE:</h4><p>b</p>")
    assert [c.key for c in cl] == ["foreword", "6.2.3", "6.2.3/ue", "6.2.4", "6.2.4/ue"]


def test_unnumbered_subheadings_keyed_under_their_parents():
    from specview.parse_docx import html_to_clauses

    cl = html_to_clauses("<h4>5.4.2 CSI-RS</h4><h5>CSI-RS for CSI acquisition</h5><h6>NZP-CSI-RS-Resource</h6><p>a</p>"
                         "<h5>CSI-RS for beam refinement</h5><h6>NZP-CSI-RS-Resource</h6><p>b</p><h4></h4><p>c</p>"
                         '<h4><img src="x.png"/></h4>')
    assert [c.key for c in cl] == ["5.4.2", "5.4.2/csi-rs-for-csi-acquisition",
                                   "5.4.2/csi-rs-for-csi-acquisition/nzp-csi-rs-resource",
                                   "5.4.2/csi-rs-for-beam-refinement", "5.4.2/csi-rs-for-beam-refinement/nzp-csi-rs-resource"]
    assert "c" in cl[-1].blocks[-2].text and "<img" in cl[-1].blocks[-1].html  # empty headings are not clauses
