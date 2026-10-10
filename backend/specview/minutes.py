"""Working-group meeting reports (RAN1/2/3/4): the agreements behind a spec change.

Each WG meeting folder holds a final report, e.g.
``.../WG1_RL1/TSGR1_95/Report/Final_Minutes_report_RAN1#95_v100.zip``. It is split into
agenda items and their "Agreement:", "Decision:", "Conclusion:" and "Working assumption:" blocks,
with the Tdocs each block mentions. A change is then matched to blocks that name its CR's Tdocs
or share its specific terms (parameter names, the words that changed).

Which meetings to look in: the ones a CR cover names ("from RAN1#94bis and RAN1#95"), the ones its
draft CRs were found in, and the one its own WG Tdoc belongs to (found by binary search over the
meetings' Tdoc number ranges).
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

import requests
from lxml import etree

from .download import new_session

FTP = "https://www.3gpp.org/ftp/tsg_ran"
WG_PATH = {"1": "WG1_RL1", "2": "WG2_RL2", "3": "WG3_Iu", "4": "WG4_Radio"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MARK = re.compile(r"^\s*(Agreements?|Decision|Conclusions?|Working assumptions?)\s*:?\s*(.*)$", re.I)
_TDOC = re.compile(r"\bR([1-4])-(\d{6,7})\b")
_FOLDER = re.compile(r"^TSGR([1-4])_(\d+)(b|bis)?(_e)?$", re.I)


def folder_name(folder_url: str) -> str:
    return folder_url.rstrip("/").rsplit("/", 1)[-1]


def meeting_label(folder_url: str) -> str:
    """``.../TSGR1_100b_e`` -> ``RAN1#100b-e``."""
    m = _FOLDER.match(folder_name(folder_url))
    if not m:
        return folder_name(folder_url)
    return f"RAN{m.group(1)}#{m.group(2)}{'b' if m.group(3) else ''}{'-e' if m.group(4) else ''}"


def tdoc_value(tdoc: str) -> int:
    """Comparable number: R1-1813585 -> 18_13585, R1-101711 -> 10_01711 (6-digit Tdocs use a 4-digit counter)."""
    m = _TDOC.match(tdoc)
    if not m:
        return -1
    d = m.group(2)
    return int(d[:2]) * 100000 + int(d[2:])


# ---------------------------------------------------------------- meetings and Tdoc ranges

def _get(session, url: str) -> requests.Response | None:
    for _ in range(3):
        try:
            r = session.get(url, timeout=120)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404):
                return None
        except requests.RequestException:
            pass
    return None


def list_meetings(wg: str, session=None) -> list[str]:
    """Regular meeting folders of a WG in order (ad hoc meetings with other names are left out)."""
    s = session or new_session()
    r = _get(s, f"{FTP}/{WG_PATH[wg]}/")
    if r is None:
        return []
    names = {n for n in re.findall(r"/(TSGR[1-4]_[0-9A-Za-z_]+)/?\"", r.text) if _FOLDER.match(n)}

    def key(n):
        m = _FOLDER.match(n)
        return int(m.group(2)), bool(m.group(3))

    return [f"{FTP}/{WG_PATH[wg]}/{n}" for n in sorted(names, key=key)]


class TdocIndex:
    """Tdoc number range of each meeting, filled lazily from the Docs listings and cached on disk."""

    def __init__(self, cache_dir: Path, session=None):
        self.file = Path(cache_dir) / "minutes" / "tdoc_ranges.json"
        self.ranges: dict[str, list[int]] = json.loads(self.file.read_text()) if self.file.exists() else {}
        self.s = session or new_session()

    def _range(self, folder: str) -> list[int] | None:
        if folder not in self.ranges:
            r = _get(self.s, f"{folder}/Docs/")
            vals = [tdoc_value(f"R{m[0]}-{m[1]}") for m in _TDOC.findall(r.text)] if r is not None else []
            self.ranges[folder] = [min(vals), max(vals)] if vals else []
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.ranges))
        return self.ranges[folder] or None

    def meeting_of(self, tdoc: str) -> str | None:
        m = _TDOC.match(tdoc)
        if not m:
            return None
        v = tdoc_value(tdoc)
        meetings = list_meetings(m.group(1), self.s)
        lo, hi = 0, len(meetings) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            rg = self._range(meetings[mid])
            if rg is None:  # empty folder: look at a neighbour instead
                meetings.pop(mid)
                hi -= 1
                continue
            if v < rg[0]:
                hi = mid - 1
            elif v > rg[1]:
                lo = mid + 1
            else:
                return meetings[mid]
        return None


# ---------------------------------------------------------------- reports

def report_url(folder: str, session=None) -> str | None:
    """The final report of a meeting (else its latest draft)."""
    s = session or new_session()
    r = _get(s, f"{folder}/Report/")
    if r is None:
        return None
    files = sorted(set(re.findall(r"Report/([^/\"<>]+\.zip)", r.text)))
    files = [f for f in files if re.search(r"minutes|report", unquote(f), re.I)]
    if not files:
        return None

    def rank(f):
        v = re.search(r"v(\d+)", f)
        return ("final" in f.lower(), int(v.group(1)) if v else 0)

    return f"{folder}/Report/{max(files, key=rank)}"


def _paragraphs(docx: Path) -> list[tuple[str, str]]:
    with zipfile.ZipFile(docx) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(f"{_W}p"):
        st = p.find(f"{_W}pPr/{_W}pStyle")
        style = st.get(f"{_W}val") if st is not None else ""
        text = "".join((t.text or "") if t.tag == f"{_W}t" else "\t" for t in p.iter(f"{_W}t", f"{_W}tab")).strip()
        out.append((style, text))
    return out


def parse_report(docx: Path) -> list[dict]:
    """Agreement-like blocks with their agenda item and the Tdocs they mention."""
    items: list[dict] = []
    agenda = ""
    cur: dict | None = None
    blanks = 0
    for style, text in _paragraphs(docx):
        if style.lower().startswith("heading") or style.upper().startswith("TOC"):
            if style.lower().startswith("heading") and text:
                agenda = text
            cur = None
            continue
        m = _MARK.match(text)
        if m:
            cur = {"agenda": agenda, "kind": m.group(1).capitalize().rstrip("s"), "lines": [m.group(2)] if m.group(2) else []}
            items.append(cur)
            blanks = 0
            continue
        if cur is None:
            continue
        if not text:
            blanks += 1
            if blanks >= 3:
                cur = None
            continue
        blanks = 0
        if len(cur["lines"]) < 40:
            cur["lines"].append(text)
    out = []
    for it in items:
        text = "\n".join(it.pop("lines")).strip()
        if text:
            out.append({**it, "text": text[:3000],
                        "tdocs": sorted({f"R{a}-{b}" for a, b in _TDOC.findall(text)})})
    return out


def fetch_report(folder: str, cache_dir: Path, session=None) -> dict | None:
    """Parsed report of one meeting, cached as JSON (an empty file marks "no report")."""
    cache = Path(cache_dir) / "minutes" / f"{folder_name(folder)}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data or None
    s = session or new_session()
    url = report_url(folder, s)
    result = None
    r = _get(s, url) if url else None
    if r is not None and r.content[:2] == b"PK":
        work = cache.parent / folder_name(folder)
        work.mkdir(parents=True, exist_ok=True)
        zp = work / "report.zip"
        zp.write_bytes(r.content)
        with zipfile.ZipFile(zp) as z:
            docs = [n for n in z.namelist() if n.lower().endswith((".docx", ".doc")) and not n.startswith("__MACOSX")]
            if docs:
                doc = work / Path(max(docs, key=lambda n: z.getinfo(n).file_size)).name
                doc.write_bytes(z.read(max(docs, key=lambda n: z.getinfo(n).file_size)))
                if doc.suffix.lower() == ".doc":
                    from .download import LegacyDocError, _convert_doc

                    try:
                        doc = _convert_doc(doc)
                    except LegacyDocError:
                        return None  # not cached: may work once LibreOffice/Word is there
                result = {"meeting": meeting_label(folder), "folder": folder, "url": url, "items": parse_report(doc)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result or {}, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------- matching

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_PARAM = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*(?:-r\d+)?\b|\b[a-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "not", "shall", "which", "when", "where", "used",
         "given", "according", "clause", "otherwise", "than", "each", "also", "into", "only", "both", "then", "case",
         "agreement", "agreements", "decision", "draft", "endorse", "endorsed", "agreed", "noted", "following", "support",
         "configured", "higher-layer", "parameter", "parameters", "ue"}


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)} - _STOP


def _names_clause(text: str, spec: str, clause: str) -> bool:
    """A text proposal / draft CR quoting this clause of this spec ("TP for TS38.211 ... 6.4.1.1.1.2")."""
    if not clause or not re.search(rf"(?<![\d.]){re.escape(clause)}(?![\d.]*\d)", text):
        return False
    return not spec or re.search(re.escape(spec).replace(r"\.", r"\.?"), text) is not None


def match_agreements(change: dict, reports: list[dict], tdocs: set[str], spec: str = "", top: int = 3) -> list[dict]:
    """Report blocks most related to a change: a text proposal for this very clause, a block naming one of its
    CR Tdocs, or one sharing its specific terms (parameter names, the words that changed)."""
    focus = _terms(" ".join(change.get("changes") or [])) or _terms(change["text"])
    params = {p.lower() for p in _PARAM.findall(change["text"] + " " + (change.get("old_text") or ""))}
    clause = change.get("clause", "")
    scored = []
    for rep in reports:
        for it in rep["items"]:
            words = _terms(it["text"])
            named = _names_clause(it["text"], spec, clause)
            hit_t, hit_p, hit_f = tdocs & set(it["tdocs"]), params & words, focus & words
            score = 10 * len(hit_t) + 8 * named + 3 * len(hit_p) + len(hit_f)
            head = it["text"].split("\n", 1)[0][:250]  # the decision line itself
            if any(t in head for t in hit_t):  # "The draft CR in R1-xxxx is endorsed" is about this very Tdoc
                score += 5
            specs = set(re.findall(r"\b(?:TS\s*)?(\d{2}\.\d{3})\b", head))
            if spec and specs and spec not in specs:  # the block's own subject is another spec
                score -= 6
            if hit_t or (named and len(hit_f) >= 2) or len(hit_p) >= 2 or (hit_p and len(hit_f) >= 3) or len(hit_f) >= 6:
                scored.append((score, rep, it, "tdoc" if hit_t else "clause" if named else "terms"))
    scored.sort(key=lambda x: -x[0])
    return [{"meeting": rep["meeting"], "agenda": it["agenda"], "kind": it["kind"], "text": it["text"][:1500],
             "source": rep["url"], "via": via} for _, rep, it, via in scored[:top]]
