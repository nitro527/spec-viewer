"""Fetch CR documents and parse their cover pages.

The Change history annex names, for each CR, the TSG Tdoc (e.g. ``RP-200186``)
and meeting (``RAN#87-e``). That Tdoc is a zip on the 3GPP FTP containing one
.docx per CR (``38211_CR0033_(Rel-16)_ R1-2001466.docx``); its first tables are
the CR cover page with "Reason for change", "Summary of change", "Work item
code" and, crucially, "Clauses affected".
"""
from __future__ import annotations

import re
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests
from lxml import etree

from .download import new_session
from .versions import spec_digits

FTP = "https://www.3gpp.org/ftp"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Tdoc prefix -> (FTP path of the TSG, meeting folder prefix, meeting name prefix in Change history)
_TSG = {
    "RP": ("tsg_ran/TSG_RAN", "TSGR_", "RAN"),
    "SP": ("tsg_sa/TSG_SA", "TSGS_", "SA"),
    "CP": ("tsg_ct/TSG_CT", "TSGC_", "CT"),
}

_FIELDS = {
    "title": "title",
    "source to wg": "source_wg",
    "source to tsg": "source_tsg",
    "work item code": "wi_code",
    "date": "date",
    "category": "category",
    "release": "release",
    "reason for change": "reason",
    "summary of change": "summary",
    "consequences if not approved": "consequences",
    "clauses affected": "clauses_raw",
    "other comments": "other_comments",
}


@dataclass
class CRCover:
    spec: str = ""
    cr: str = ""
    rev: str = ""
    current_version: str = ""
    title: str = ""
    source_wg: str = ""
    source_tsg: str = ""
    wi_code: str = ""
    date: str = ""
    category: str = ""
    release: str = ""
    reason: str = ""
    summary: str = ""
    consequences: str = ""
    clauses_raw: str = ""
    other_comments: str = ""
    clauses: list[str] = field(default_factory=list)
    wg_tdoc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def meeting_folder(tdoc: str, meeting: str) -> str | None:
    """``("RP-200186", "RAN#87-e")`` -> ``".../TSG_RAN/TSGR_87e"``."""
    prefix = tdoc.split("-")[0].upper()
    if prefix not in _TSG:
        return None
    path, folder, _ = _TSG[prefix]
    m = re.search(r"#\s*(\d+)\s*(-?e)?", meeting, re.I)
    if not m:
        return None
    return f"{FTP}/{path}/{folder}{m.group(1)}{'e' if m.group(2) else ''}"


def parse_clauses(raw: str) -> list[str]:
    """``"5.3.1, Table 7.4.1.5.3-1, Annex B"`` -> ``["5.3.1", "7.4.1.5.3", "B"]``."""
    out: list[str] = []
    for m in re.finditer(r"Annex\s+([A-Z])\b|((?:[A-Z]\.)?\d+(?:\.\d+)*)(?:-\d+)?", raw):
        c = m.group(1) or m.group(2)
        if c and c not in out:
            out.append(c)
    return out


def _cell_text(tc) -> str:
    paras = ["".join(t.text or "" for t in p.iter(f"{_W}t")) for p in tc.iter(f"{_W}p")]
    return "\n".join(p.strip() for p in paras if p.strip())


def parse_cover(docx: Path) -> CRCover:
    with zipfile.ZipFile(docx) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    cover = CRCover()
    tables = root.iter(f"{_W}tbl")
    for _, tbl in zip(range(4), tables):
        for tr in tbl.iter(f"{_W}tr"):
            cells = []
            for tc in tr.iter(f"{_W}tc"):
                t = _cell_text(tc)
                if t and (not cells or cells[-1] != t):
                    cells.append(t)
            if not cells:
                continue
            flat = [" ".join(c.split()) for c in cells]
            if len(flat) >= 4 and flat[1] == "CR":  # "38.211 | CR | 0033 | rev | - | Current version: | 16.0.0"
                cover.spec, cover.cr = flat[0], flat[2]
                if "rev" in flat:
                    i = flat.index("rev")
                    cover.rev = flat[i + 1] if i + 1 < len(flat) else ""
                vm = re.search(r"\d+\.\d+\.\d+", " ".join(flat))
                cover.current_version = vm.group(0) if vm else ""
                continue
            # label/value pairs; one row may hold two pairs (Work item code | x | Date: | y)
            i = 0
            while i < len(cells) - 1:
                key = cells[i].rstrip(":").strip().lower()
                attr = _FIELDS.get(key)
                if attr and not getattr(cover, attr):
                    setattr(cover, attr, cells[i + 1].strip())
                    i += 2
                else:
                    i += 1
    cover.clauses = parse_clauses(cover.clauses_raw)
    m = re.search(r"\b([A-Z]\d-\d{6,7})\b", docx.name)
    cover.wg_tdoc = m.group(1) if m else ""
    return cover


def fetch_pack(tdoc: str, meeting: str, cache_dir: Path, session: requests.Session | None = None) -> Path | None:
    """Download (cached) the TSG Tdoc zip; None if it cannot be located."""
    target = Path(cache_dir) / "tdocs" / f"{tdoc}.zip"
    if target.exists():
        return target
    folder = meeting_folder(tdoc, meeting)
    if not folder:
        return None
    s = session or new_session()
    target.parent.mkdir(parents=True, exist_ok=True)
    for url in (f"{folder}/Docs/{tdoc}.zip", f"{folder}/Docs/{tdoc.lower()}.zip"):
        for attempt in range(3):  # the 3GPP server times out now and then
            try:
                r = s.get(url, timeout=120)
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        if r.status_code == 200 and r.content[:2] == b"PK":
            target.write_bytes(r.content)
            return target
    return None


def find_cr_doc(pack: Path, spec: str, cr: str, out_dir: Path) -> Path | None:
    """Extract the .docx for ``spec`` CR ``cr`` from a CR pack (or a single-CR Tdoc)."""
    digits = spec_digits(spec)
    want = re.compile(rf"{digits}[_-]?CR0*{int(cr)}\D", re.I) if cr.isdigit() else None
    with zipfile.ZipFile(pack) as z:
        docs = [n for n in z.namelist() if n.lower().endswith(".docx") and "cover" not in n.lower()]
        pick = next((n for n in docs if want and want.search(n)), None)
        if pick is None and len(docs) == 1:
            pick = docs[0]
        if pick is None:
            return None
        out = Path(out_dir) / Path(pick).name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(z.read(pick))
        return out
