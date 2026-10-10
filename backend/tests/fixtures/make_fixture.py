"""Generate small synthetic 3GPP-style .docx files for tests.

These are NOT real specifications. They mimic the structural conventions of
3GPP TS documents (numbered Heading N styles with a tab after the clause
number, B1/NO/EQ/TH styles, OMML equations, a Change history annex) so the
parser and history engine can be exercised offline.
"""
from __future__ import annotations

import copy
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import parse_xml

OMML_NS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def _omml(latexish_runs: list[str]) -> str:
    # Minimal OMML: a sequence of text runs plus one subscript, enough for pandoc.
    base, sub, *rest = latexish_runs + ["", ""]
    tail = "".join(f"<m:r><m:t>{t}</m:t></m:r>" for t in rest if t)
    return (
        f'<m:oMath {OMML_NS}>'
        f"<m:sSub><m:e><m:r><m:t>{base}</m:t></m:r></m:e>"
        f"<m:sub><m:r><m:t>{sub}</m:t></m:r></m:sub></m:sSub>{tail}"
        "</m:oMath>"
    )


def _style(doc, name):
    if name not in [s.name for s in doc.styles]:
        doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    return name


def _heading(doc, number, title, level):
    doc.add_paragraph(f"{number}\t{title}", style=f"Heading {level}")


def _eq(doc, runs):
    p = doc.add_paragraph(style=_style(doc, "EQ"))
    p._p.append(parse_xml(_omml(runs)))


def _table(doc, rows):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            t.cell(r, c).text = val


def _change_history(doc, rows):
    doc.add_paragraph("Annex A (informative):\nChange history", style="Heading 8")
    header = ["Date", "Meeting", "TDoc", "CR", "Rev", "Cat", "Subject/Comment", "New version"]
    _table(doc, [header] + rows)


def build(version: str) -> Document:
    doc = Document()
    _style(doc, "B1")
    _style(doc, "NO")
    doc.add_paragraph("Foreword", style="Heading 1")
    doc.add_paragraph("This Technical Specification has been produced by the 3rd Generation Partnership Project (3GPP).")

    _heading(doc, "1", "Scope", 1)
    doc.add_paragraph("The present document describes the physical channels and signals for NR.")

    _heading(doc, "4", "Frame structure and physical resources", 1)
    _heading(doc, "4.2", "Numerologies", 2)
    doc.add_paragraph("Multiple OFDM numerologies are supported as given by Table 4.2-1.")
    rows = [["μ", "Δf = 2^μ·15 [kHz]", "Cyclic prefix"], ["0", "15", "Normal"], ["1", "30", "Normal"]]
    if version >= "16.0.0":
        rows.append(["2", "60", "Normal, Extended"])
    _table(doc, rows)

    _heading(doc, "4.3", "Frame structure", 2)
    _heading(doc, "4.3.1", "Frames and subframes", 3)
    doc.add_paragraph("Downlink, uplink, and sidelink transmissions are organized into frames.")
    _eq(doc, ["T", "f", "=(Δfmax Nf/100)·Tc=10 ms"])
    if version >= "16.1.0":
        doc.add_paragraph("For operation with shared spectrum channel access, the frame structure is unchanged.", style="NO")

    _heading(doc, "7", "Downlink", 1)
    _heading(doc, "7.4", "Physical signals", 2)
    _heading(doc, "7.4.1", "Reference signals", 3)
    _heading(doc, "7.4.1.1", "Demodulation reference signals for PDSCH", 4)
    _heading(doc, "7.4.1.1.1", "Sequence generation", 5)
    if version >= "16.0.0":
        doc.add_paragraph("The UE shall assume the sequence r(n) is defined by the pseudo-random sequence c(i) defined in clause 5.2.1 of TS 38.211.")
    else:
        doc.add_paragraph("The UE shall assume the sequence r(n) is defined by the pseudo-random sequence c(i) defined in clause 5.2.1.")
    p = doc.add_paragraph("where the pseudo-random sequence generator shall be initialized with ", style="B1")
    p._p.append(parse_xml(_omml(["c", "init", "=(2^17)mod 2^31"])))
    if version < "16.1.0":
        doc.add_paragraph("- legacy bullet removed in a later version.", style="B1")
    if version >= "16.2.0":
        _heading(doc, "7.4.1.1.3", "Mapping for positioning", 5)
        doc.add_paragraph("A new clause added in a late version for positioning, see TS 38.214 clause 5.1.6.5.")

    hist = [["2017-12", "RAN#78", "RP-172674", "", "", "", "Approved by plenary", "15.0.0"]]
    if version >= "16.0.0":
        hist.append(["2019-12", "RAN#86", "RP-192620", "0100", "1", "B", "Introduction of NR-U", "16.0.0"])
    if version >= "16.1.0":
        hist.append(["2020-03", "RAN#87-e", "RP-200186", "0123", "", "F", "Corrections for NR-U frame structure", "16.1.0"])
    if version >= "16.2.0":
        hist.append(["2020-07", "RAN#88-e", "RP-201176", "0140", "2", "B", "Introduction of positioning enhancements", "16.2.0"])
    _change_history(doc, hist)
    return doc


VERSIONS = ["15.0.0", "16.0.0", "16.1.0", "16.2.0"]


def write_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    codes = {"15.0.0": "f00", "16.0.0": "g00", "16.1.0": "g10", "16.2.0": "g20"}
    paths = []
    for v in VERSIONS:
        p = out_dir / f"99211-{codes[v]}.docx"
        build(v).save(p)
        paths.append(p)
    return paths


if __name__ == "__main__":
    import sys
    for p in write_all(Path(sys.argv[1] if len(sys.argv) > 1 else ".")):
        print(p)
