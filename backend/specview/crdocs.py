"""Fetch CR documents and parse their cover pages.

The Change history annex names, for each CR, the TSG Tdoc (e.g. ``RP-200186``)
and meeting (``RAN#87-e``). That Tdoc is a zip on the 3GPP FTP containing one
.docx per CR (``38211_CR0033_(Rel-16)_ R1-2001466.docx``); its first tables are
the CR cover page with "Reason for change", "Summary of change", "Work item
code" and, crucially, "Clauses affected".
"""
from __future__ import annotations

import io
import json
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
    m = re.search(r"(\d+)\s*(-?e)?\s*$", meeting.strip(), re.I)  # "RAN#87-e", "RAN_46" (older Change histories)
    if not m:
        return None
    return f"{FTP}/{path}/{folder}{m.group(1)}{'e' if m.group(2) else ''}"


def parse_clauses(raw: str) -> list[str]:
    """``"5.3.1, Table 7.4.1.5.3-1, Annex B"`` -> ``["5.3.1", "7.4.1.5.3", "B"]``."""
    out: list[str] = []
    for m in re.finditer(r"Annex\s+([A-Z])\b|((?:[A-Z]\.)?\d+(?:\.\d+[A-Z]{0,2})*)(?:-\d+)?", raw):
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
    m = re.search(r"\b([A-Z]\d-\d{6,7}|R5s\d{6})\b", docx.name)
    cover.wg_tdoc = m.group(1) if m else ""
    return cover


def fetch_pack(tdoc: str, meeting: str, cache_dir: Path, session: requests.Session | None = None) -> Path | None:
    """Download (cached) the TSG Tdoc zip; None if it cannot be located."""
    target = Path(cache_dir) / "tdocs" / f"{tdoc}.zip"
    if target.exists():
        return target
    s = session or new_session()
    folder = meeting_folder(tdoc, meeting)
    if not folder and re.match(r"R[1-5P]-\d{6,7}$", tdoc):
        # conformance specs (38.5xx) list the RAN5 Tdoc itself, and some Change histories leave the meeting
        # empty (36.323): find the meeting by its Tdoc number
        from .minutes import TdocIndex

        folder = TdocIndex(cache_dir, s).meeting_of(tdoc)
    if not folder and (m := re.match(r"R5s(\d\d)\d{4}$", tdoc)):
        # TTCN test suite CRs (38.523-3, 36.523-3 ...) come from RAN5's TTCN e-mail meetings, filed by year
        folder = f"{FTP}/tsg_ran/WG5_Test_ex-T1/TTCN/TTCN_CRs/20{m.group(1)}"
    if not folder:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    # "RP-88" in a Change history is the e-meeting folder TSGR_88e
    folders = [folder]
    if re.search(r"/TSG[RSC]_\d+e?$", folder):
        folders.append(folder[:-1] if folder.endswith("e") else f"{folder}e")
    urls = [f"{f}/Docs/{t}.zip" for f in folders for t in (tdoc, tdoc.lower())]
    for url in urls:
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


def _is_doc(name: str) -> bool:
    """A Word file, not a pack cover sheet nor macOS zip junk ("__MACOSX/._R5-264584 ....docx")."""
    base = Path(name).name.lower()
    # pack cover sheets end in "cover" ("RP-261383_cover.docx", "CR cover sheet.doc"); "Spherical_Coverage" and
    # "SL CR to TR 38.786 ... cover RAN4#110bis.docx" are CRs
    cover = re.search(r"(?<![a-z])cover(?:[ _-]?sheet)?\.docx?$", base)
    return (base.endswith((".docx", ".doc")) and not cover and not base.startswith("._")
            and "__macosx" not in name.lower())


def _pack_docs(z: zipfile.ZipFile) -> list[tuple[str, str, zipfile.ZipFile]]:
    """(name, enclosing zip name, zip) of every Word file in a pack, one level of nested zips included:
    RAN4 packs hold one zip per CR (the CR plus its band-combination JSON files)."""
    out = []
    for n in z.namelist():
        if _is_doc(n):
            out.append((n, "", z))
        elif n.lower().endswith(".zip") and "__macosx" not in n.lower():
            try:
                inner = zipfile.ZipFile(io.BytesIO(z.read(n)))
            except zipfile.BadZipFile:
                continue
            out += [(m, n, inner) for m in inner.namelist() if _is_doc(m)]
    return out


def _cover_matches(path: Path, spec: str, cr: str) -> bool:
    try:
        cover = parse_cover(path)
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError):
        return False
    return cover.cr.lstrip("0") == cr.lstrip("0") and spec_digits(cover.spec or "") == spec_digits(spec)


def find_cr_doc(pack: Path, spec: str, cr: str, out_dir: Path) -> Path | None:
    """Extract the .docx for ``spec`` CR ``cr`` from a CR pack (or a single-CR Tdoc); older packs hold .doc files."""
    digits = spec_digits(spec)
    want = re.compile(rf"{digits}[_-]?CR0*{int(cr)}\D", re.I) if cr.isdigit() else None
    with zipfile.ZipFile(pack) as z:
        docs = _pack_docs(z)
        pick = next((d for d in docs if want and want.search(d[0])), None) or \
            next((d for d in docs if want and want.search(d[1])), None)
        if pick is None and len(docs) == 1:
            pick = docs[0]
        if pick is None and cr.isdigit():
            # names without the CR number ("R4-2607773 CR 38.101-1 removing ...docx", "Big CR on DC_R20_...docx"):
            # read the cover pages, those naming the spec first
            mention = re.compile(rf"{re.escape(spec)}|{digits}", re.I)
            for name, outer, src in sorted(docs, key=lambda d: not mention.search(f"{d[0]} {d[1]}")):
                if not name.lower().endswith(".docx"):
                    continue
                out = Path(out_dir) / Path(name).name
                out.parent.mkdir(parents=True, exist_ok=True)
                if not out.exists():
                    out.write_bytes(src.read(name))
                if _cover_matches(out, spec, cr):
                    return out
        if pick is None:
            return None
        name, _, src = pick
        out = Path(out_dir) / Path(name).name
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".doc" and out.with_suffix(".docx").exists():
            return out.with_suffix(".docx")
        out.write_bytes(src.read(name))
    if out.suffix.lower() == ".doc":
        from .download import _convert_doc

        return _convert_doc(out)
    return out


# ---------------------------------------------------------------- individual draft CRs behind a combined CR

_WG = {  # Tdoc prefix -> (FTP path, meeting folder prefix)
    "R1": ("tsg_ran/WG1_RL1", "TSGR1_"),
    "R2": ("tsg_ran/WG2_RL2", "TSGR2_"),
    "R3": ("tsg_ran/WG3_Iu", "TSGR3_"),
    "R4": ("tsg_ran/WG4_Radio", "TSGR4_"),
    "R5": ("tsg_ran/WG5_Test_ex-T1", "TSGR5_"),
}
_WG_MEETING = re.compile(r"RAN\s*([1-4])\s*#\s*(\d+)\s*(bis|b)?(?:\s*-?\s*(e))?", re.I)
_WG_TDOC = re.compile(r"\b(R[1-5]-\d{6,7})\b")


def wg_meetings(text: str) -> list[str]:
    """Working-group meeting folders named in a cover: "RAN1#94bis and RAN1#95" -> [.../TSGR1_94b, .../TSGR1_95]."""
    out = []
    for wg, num, bis, e in _WG_MEETING.findall(text):
        path, prefix = _WG[f"R{wg}"]
        folder = f"{FTP}/{path}/{prefix}{num}{'b' if bis else ''}{'_e' if e else ''}"
        if folder not in out:
            out.append(folder)
    return out


def listed_tdocs(cover: dict) -> list[str]:
    """WG Tdocs a combined CR lists as its individual draft CRs (its own WG Tdoc excluded)."""
    text = " ".join(cover.get(k) or "" for k in ("reason", "summary", "consequences", "other_comments"))
    found = []
    for t in _WG_TDOC.findall(text):
        if t != cover.get("wg_tdoc") and t not in found:
            found.append(t)
    return found


def fetch_draft_cr(tdoc: str, cover: dict, cache_dir: Path, session: requests.Session | None = None) -> dict | None:
    """Cover of an individual draft CR (e.g. R1-1813585) listed in ``cover``; cached as JSON, None if not found."""
    cache = Path(cache_dir) / "tdocs" / f"{tdoc}.cover.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data or None
    text = " ".join(cover.get(k) or "" for k in ("title", "reason", "summary", "consequences"))
    folders = [f for f in wg_meetings(text) if f"/{_WG.get(tdoc[:2], ('', ''))[1]}" in f]
    s = session or new_session()
    result = None
    for folder in folders:
        for url in (f"{folder}/Docs/{tdoc}.zip",):
            try:
                r = s.get(url, timeout=120)
            except requests.RequestException:
                continue
            if r.status_code != 200 or r.content[:2] != b"PK":
                continue
            out_dir = Path(cache_dir) / "tdocs" / tdoc
            out_dir.mkdir(parents=True, exist_ok=True)
            pack = out_dir / f"{tdoc}.zip"
            pack.write_bytes(r.content)
            with zipfile.ZipFile(pack) as z:
                docs = [n for n in z.namelist() if n.lower().endswith((".docx", ".doc"))]
                if not docs:
                    break
                doc = out_dir / Path(docs[0]).name
                doc.write_bytes(z.read(docs[0]))
            if doc.suffix.lower() == ".doc":
                from .download import LegacyDocError, _convert_doc

                try:
                    doc = _convert_doc(doc)
                except LegacyDocError:
                    return None  # not cached: a later run with LibreOffice/Word may succeed
            c = parse_cover(doc)
            if c.title or c.reason:
                result = {**c.to_dict(), "wg_tdoc": tdoc, "meeting_folder": folder}
            break
        if result:
            break
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result or {}, ensure_ascii=False), encoding="utf-8")
    return result
