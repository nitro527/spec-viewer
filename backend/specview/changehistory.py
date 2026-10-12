"""Parse the "Change history" annex of a 3GPP specification.

Every TS ends with a table listing, for each new version, the CRs that were
implemented (date, meeting, TDoc, CR number, revision, category, subject).
This links a version to the CRs, and so to the reason for a change, without
needing any inference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from lxml import html

from .parse_docx import Clause, norm_text

_COLS = {
    "date": ("date",),
    "meeting": ("meeting", "tsg #", "tsg#"),
    "tdoc": ("tdoc", "tsg doc", "wg doc"),
    "cr": ("cr",),
    "rev": ("rev",),
    "cat": ("cat",),
    "subject": ("subject", "comment", "subject/comment"),
    "new_version": ("new version", "new vers", "new"),
}


@dataclass
class CREntry:
    date: str
    meeting: str
    tdoc: str
    cr: str
    rev: str
    cat: str
    subject: str
    new_version: str

    def to_dict(self) -> dict:
        return asdict(self)


def _column_map(cells: list[str]) -> dict[str, int] | None:
    low = [c.lower().strip() for c in cells]
    m: dict[str, int] = {}
    for field, names in _COLS.items():
        for i, c in enumerate(low):
            if i in m.values():
                continue
            if any(c == n or c.startswith(n) for n in names):
                m[field] = i
                break
    return m if {"cr", "new_version", "subject"} <= m.keys() else None


def _rows(table) -> list[list[str]]:
    """The table as a full grid: Word's merged cells come through pandoc as rowspan/colspan, so later rows have
    fewer cells and every column after the merge shifted (a CR whose date, meeting and version were merged
    with the row above was dropped). A spanned value is repeated in each row and column it covers."""
    def span(td, attr):
        try:
            return max(1, min(int(td.get(attr) or 1), 1000))
        except ValueError:
            return 1

    grid: list[list[str]] = []
    carry: dict[int, tuple[int, str]] = {}  # column -> (rows still covered, value) from a rowspan above
    for tr in table.xpath(".//tr"):
        row: list[str] = []
        cells = iter(tr.xpath("./th|./td"))
        col = 0
        while True:
            if col in carry:
                left, val = carry[col]
                row.append(val)
                if left > 1:
                    carry[col] = (left - 1, val)
                else:
                    del carry[col]
                col += 1
                continue
            td = next(cells, None)
            if td is None:
                break
            val = norm_text(td.text_content())
            rs, cs = span(td, "rowspan"), span(td, "colspan")
            for k in range(cs):
                row.append(val)
                if rs > 1:
                    carry[col + k] = (rs - 1, val)
            col += cs
        while col in carry:  # rowspans to the right of the last cell
            left, val = carry.pop(col)
            row.append(val)
            if left > 1:
                carry[col] = (left - 1, val)
            col += 1
        grid.append(row)
    return grid


def parse_change_history(clauses: list[Clause]) -> list[CREntry]:
    entries: list[CREntry] = []
    for c in clauses:
        if "change history" not in c.title.lower():
            continue
        for b in c.blocks:
            if b.kind != "table":
                continue
            rows = _rows(html.fragment_fromstring(b.html))
            colmap = None
            for cells in rows:
                if colmap is None:
                    colmap = _column_map(cells)
                    continue
                get = lambda f: cells[colmap[f]] if f in colmap and colmap[f] < len(cells) else ""  # noqa: E731
                version = get("new_version")
                if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                    continue
                entries.append(CREntry(get("date"), get("meeting"), get("tdoc"), get("cr"), get("rev"),
                                       get("cat"), get("subject"), version))
    return entries
