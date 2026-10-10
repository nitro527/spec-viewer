from docx import Document

from specview import minutes
from specview.pack import word_changes


def _report(path):
    d = Document()
    d.add_heading("Maintenance for Reference signals and QCL", level=4)
    d.add_paragraph("Agreement")
    d.add_paragraph("Text proposal for TS38.211")
    d.add_paragraph("6.4.1.1.1.2\tSequence generation when transform precoding is enabled")
    d.add_paragraph("- if sequenceGroupHopping equals 'enabled' and sequenceHopping equals 'disabled', "
                    "group hopping but not sequence hopping shall be used")
    d.add_heading("Maintenance for physical downlink control channel", level=4)
    d.add_paragraph("Agreement:")
    d.add_paragraph("The draft CR in R1-1814118 is endorsed (38.213)")
    d.add_paragraph("Offline to prepare a draft CR, see also R1-1814055")
    d.add_paragraph("Decision: The draft CR in R1-1814055 is endorsed (38.211)")
    d.save(path)


def test_parse_and_match(tmp_path):
    _report(tmp_path / "r.docx")
    items = minutes.parse_report(tmp_path / "r.docx")
    assert [(i["agenda"][:11], i["kind"]) for i in items] == [
        ("Maintenance", "Agreement"), ("Maintenance", "Agreement"), ("Maintenance", "Decision")]
    rep = {"meeting": "RAN1#94b", "url": "u", "items": items}
    old, new = "- if group hopping but not sequence hopping shall be used", "- if group hopping is enabled and sequence hopping is disabled"
    got = minutes.match_agreements({"text": new, "old_text": old, "changes": word_changes(old, new), "clause": "6.4.1.1.1.2"},
                                   [rep], set(), "38.211")
    assert got[0]["via"] == "clause" and "Reference signals" in got[0]["agenda"]
    got = minutes.match_agreements({"text": "x", "changes": [], "clause": "7.3.2.2"}, [rep], {"R1-1814055"}, "38.211")
    assert got[0]["text"].startswith("The draft CR in R1-1814055 is endorsed (38.211)")  # the decision about this Tdoc first


def test_labels_and_tdoc_order():
    assert minutes.meeting_label("https://x/WG1_RL1/TSGR1_100b_e") == "RAN1#100b-e"
    assert minutes.tdoc_value("R1-101711") < minutes.tdoc_value("R1-1813585") < minutes.tdoc_value("R1-2005128")
