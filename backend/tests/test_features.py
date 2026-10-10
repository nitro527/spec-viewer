from specview.features import acronyms, match_sections, norm_term, parse_feature_list, wi_codes
from specview.parse_docx import html_to_clauses
from tests.conftest import needs_pandoc

SECTIONS = [
    ("4.1", "Layer-1 UE features", "Layer-1", "Rel-15"),
    ("5.1.2", "NR-unlicensed", "Layer-1", "Rel-16"),
    ("5.1.5", "NR positioning", "Layer-1", "Rel-16"),
    ("5.2.2", "NR_unlic-Core", "Layer-2/3", "Rel-16"),
    ("6.1.5", "NR_pos_enh", "Layer-1", "Rel-17"),
]


def test_wi_matching_per_layer_and_newrat():
    assert [s[0] for s in match_sections("NR_unlic-Core", SECTIONS)] == ["5.1.2", "5.2.2"]
    assert [s[0] for s in match_sections("NR_pos_enh-Core", SECTIONS)] == ["6.1.5"]
    assert [s[0] for s in match_sections("NR_newRAT-Core", SECTIONS)] == ["4.1"]
    assert wi_codes("NR_MBS-Core, NR_feMIMO-Core") == ["NR_MBS-Core", "NR_feMIMO-Core"]


def test_terms():
    assert norm_term("seperateRACH-IAB-Support-r16") == "seperaterach-iab-support"
    assert acronyms("PRACH and DM-RS for the UE") == {"PRACH", "DMRS"}


def test_parse_feature_table_with_rowspan():
    doc = """
    <h1>5 Release 16 UE feature list</h1>
    <h2>5.1 Layer-1 UE features</h2>
    <h3>5.1.12 NR_IAB</h3>
    <table><tr><th>Features</th><th>Index</th><th>Feature group</th><th>Components</th>
      <th>Prerequisite feature groups</th><th>Field name in TS 38.331 [2]</th><th>Parent IE in TS 38.331 [2]</th>
      <th>Note</th><th>Mandatory/Optional</th></tr>
      <tr><td rowspan="2">20. NR_IAB</td><td>20-2</td><td>SSB reception</td><td>Support up to 4 SMTCs</td><td></td>
        <td>seperateSMTC-InterIAB-Support-r16</td><td>Phy-ParametersCommon</td><td></td><td>Mandatory</td></tr>
      <tr><td>20-3</td><td>RACH extension</td><td>Separate RACH configuration</td><td></td>
        <td>seperateRACH-IAB-Support-r16</td><td>Phy-ParametersCommon</td><td></td><td>Optional</td></tr>
    </table>"""
    feats = parse_feature_list(html_to_clauses(doc))
    assert [(f.index, f.group, f.release, f.layer, f.section_title) for f in feats] == [
        ("20-2", "SSB reception", "Rel-16", "Layer-1", "NR_IAB"),
        ("20-3", "RACH extension", "Rel-16", "Layer-1", "NR_IAB"),
    ]
    assert feats[1].field_name == "seperateRACH-IAB-Support-r16"
    assert feats[1].mandatory == "Optional"


@needs_pandoc
def test_feature_links_across_specs(library, tmp_path):
    import json
    import sqlite3

    from specview.store import Library

    dst = sqlite3.connect(tmp_path / "l.sqlite")  # own copy: this test adds features and a CR
    library.db.backup(dst)
    dst.close()
    lib = Library(tmp_path / "l.sqlite")
    with lib.db:
        lib.db.execute("INSERT INTO ue_feature VALUES ('19.1.0','5.1.2','NR-unlicensed','Layer-1','Rel-16','10-1',"
                       "'Shared spectrum channel access','NR-U frame structure','','','','Optional','','[]')")
        lib.db.execute("INSERT INTO cr VALUES ('99.211','16.1.0','2020-03','RAN#87','RP-9','0999','','B','NR-U frames')")
        cover = {"wi_code": "NR_unlic-Core", "release": "Rel-16", "clauses": ["4.3.1"], "clauses_raw": "4.3.1"}
        lib.db.execute("INSERT INTO cr_detail VALUES ('99.211','0999','','RP-9','ok',?,NULL)", (json.dumps(cover),))
    assert lib.search_features("10-1")[0]["index"] == "10-1"
    assert [f["index"] for f in lib.search_features("unlicensed")] == ["10-1"]
    links = lib.feature_links("5.1.2", "10-1")
    sp = links["specs"][0]
    assert sp["spec"] == "99.211" and sp["version"] == "16.2.0"
    c = next(c for c in sp["clauses"] if c["number"] == "4.3.1")
    assert c["key"] and c["via"][0]["kind"] == "cr" and c["via"][0]["cr"] == "0999"
