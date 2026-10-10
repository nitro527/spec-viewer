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
    return [[norm_text(td.text_content()) for td in tr.xpath("./th|./td")] for tr in table.xpath(".//tr")]


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
