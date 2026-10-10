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
        if not data or data.get("v") == 2:
            return data or None  # older cache entries lack the Tdoc list: fetch again
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
                xl = [n for n in z.namelist() if n.lower().endswith(".xlsx") and "tdoc" in n.lower()]
                tdoc_list = parse_tdoc_list(z.read(xl[0])) if xl else []
                result = {"v": 2, "meeting": meeting_label(folder), "folder": folder, "url": url,
                          "items": parse_report(doc), "tdocs": tdoc_list}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result or {}, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------- Tdoc list and feature lead summaries

_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_SUMMARY = re.compile(r"\b(summary|summaries)\b", re.I)


def _xlsx_rows(data: bytes) -> list[dict]:
    import io

    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in etree.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_X}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_X}t")))
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    rows = []
    for r in etree.fromstring(z.read(sheet)).iter(f"{_X}row"):
        row = {}
        for c in r.iter(f"{_X}c"):
            col = re.match(r"[A-Z]+", c.get("r") or "A").group(0)
            v, t = c.find(f"{_X}v"), c.get("t")
            if t == "s" and v is not None:
                row[col] = shared[int(v.text)]
            elif t == "inlineStr":
                row[col] = "".join(x.text or "" for x in c.iter(f"{_X}t"))
            else:
                row[col] = v.text if v is not None and v.text else ""
        rows.append(row)
    return rows


def parse_tdoc_list(data: bytes) -> list[dict]:
    """The meeting's Tdoc list (TDoc_List_Meeting_RAN1#95.xlsx): title, source, agenda item, status, revisions."""
    rows = _xlsx_rows(data)
    if not rows:
        return []
    col = {v.strip(): k for k, v in rows[0].items()}
    want = {"tdoc": "TDoc", "title": "Title", "source": "Source", "type": "Type", "agenda": "Agenda item",
            "agenda_desc": "Agenda item description", "status": "TDoc Status", "revised_to": "Revised to",
            "spec": "Spec", "cr": "CR"}
    out = []
    for r in rows[1:]:
        d = {k: (r.get(col.get(v, ""), "") or "").strip() for k, v in want.items()}
        if d["tdoc"]:
            out.append(d)
    return out


def _norm_agenda(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"^[\d.]+\s*", "", text.lower())).strip()


def feature_lead_summaries(report: dict, agenda: str, limit: int = 12) -> list[dict]:
    """FL / moderator summaries of an agenda item, every revision (an issue settled in v1 is gone from v3)."""
    want = _norm_agenda(agenda)
    if not want:
        return []
    rows = [t for t in report.get("tdocs", [])
            if _SUMMARY.search(t["title"]) and t["type"].lower() in ("discussion", "other", "")
            and (_norm_agenda(t["agenda_desc"]) == want or _norm_agenda(t["agenda"]) == want)]
    rows.sort(key=lambda t: tdoc_value(t["tdoc"]))
    return rows[:limit]


def fetch_tdoc_text(folder: str, tdoc: str, cache_dir: Path, session=None) -> list[str] | None:
    """Paragraph texts of a meeting Tdoc (e.g. a feature lead summary), cached."""
    cache = Path(cache_dir) / "minutes" / folder_name(folder) / f"{tdoc}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")) or None
    s = session or new_session()
    r = _get(s, f"{folder}/Docs/{tdoc}.zip")
    paras = None
    if r is not None and r.content[:2] == b"PK":
        import io

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            docs = [n for n in z.namelist() if n.lower().endswith((".docx", ".doc")) and not n.startswith("__MACOSX")]
            if docs:
                cache.parent.mkdir(parents=True, exist_ok=True)
                doc = cache.parent / Path(docs[0]).name
                doc.write_bytes(z.read(docs[0]))
                if doc.suffix.lower() == ".doc":
                    from .download import LegacyDocError, _convert_doc

                    try:
                        doc = _convert_doc(doc)
                    except LegacyDocError:
                        return None
                paras = [t for _, t in _paragraphs(doc) if t]
                doc.unlink(missing_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(paras or [], ensure_ascii=False), encoding="utf-8")
    return paras


_DISCUSSION = re.compile(r"^\s*\[[^\]]{2,40}\]|\b(proposal|issue|observation|concern|views?|FL\s*(?:proposal|comment))\b", re.I)


def excerpt(paras: list[str], focus: set[str], must: set[str], budget: int = 2500,
            context: set[str] = frozenset()) -> tuple[str, int]:
    """The parts of a long summary about this change and how strongly the summary is about it. Paragraphs
    sharing the changed words count double, parameter names triple; company views and proposals (the "why")
    get a bonus over quoted spec text."""
    scores = []
    for i, p in enumerate(paras):
        words = _terms(p)
        sc = 2 * len(focus & words) + len(context & words) + 3 * len(must & words)
        if sc >= 4 and _DISCUSSION.search(p):
            sc += 4
        if sc >= 4:
            scores.append((sc, i))
    scores.sort(reverse=True)
    # a summary about this change has paragraphs where several changed words or a parameter come together
    if not any(len(focus & _terms(paras[i])) >= 3 or must & _terms(paras[i]) for _, i in scores[:8]):
        return "", 0
    total = sum(sc for sc, _ in scores[:8])
    picked: set[int] = set()
    for _, i in scores:
        if sum(len(paras[j]) for j in picked) > budget:
            break
        picked |= {j for j in range(max(0, i - 1), min(len(paras), i + 3))}
    out, last = [], -2
    for j in sorted(picked):
        if j != last + 1:
            out.append("…")
        out.append(paras[j][:600])
        last = j
    return "\n".join(out).strip()[:budget + 600], total


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
             "source": rep["url"], "folder": rep.get("folder"), "via": via} for _, rep, it, via in scored[:top]]


def summaries_for(change: dict, agreements: list[dict], reports: dict[str, dict], cache_dir: Path,
                  max_items: int = 2) -> list[dict]:
    """The feature lead summaries (any revision) of the agreements' agenda items that are most about the change,
    cut down to the paragraphs that concern it."""
    focus = _terms(" ".join(change.get("changes") or [])) | _terms(change["text"])
    must = {p.lower() for p in _PARAM.findall(change["text"] + " " + (change.get("old_text") or ""))}
    found, seen = [], set()
    for a in agreements:
        rep = reports.get(a.get("folder") or "")
        if not rep or (a["folder"], a["agenda"]) in seen:
            continue
        seen.add((a["folder"], a["agenda"]))
        agreed = _terms(a["text"]) - focus
        for t in feature_lead_summaries(rep, a["agenda"]):
            paras = fetch_tdoc_text(a["folder"], t["tdoc"], cache_dir)
            text, score = excerpt(paras or [], focus, must, context=agreed)
            if text and score >= 20:
                found.append((score, {"tdoc": t["tdoc"], "title": t["title"], "source": t["source"],
                                      "meeting": rep["meeting"], "agenda": a["agenda"], "excerpt": text,
                                      "url": f"{a['folder']}/Docs/{t['tdoc']}.zip"}))
    found.sort(key=lambda x: -x[0])
    found = [x for x in found if x[0] >= found[0][0] * 0.6] if found else []  # drop summaries mostly about something else
    out, titles = [], set()
    for _, f in found:  # one revision per summary family
        key = re.sub(r"\W+|v\d+|#\d+", "", f["title"].lower())
        if key not in titles:
            titles.add(key)
            out.append(f)
        if len(out) >= max_items:
            break
    return out
