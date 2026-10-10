from specview.features import acronyms, match_sections, norm_term, parse_feature_list, wi_codes
from specview.parse_docx import html_to_clauses

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
