"""Evidence for analysing one CR as a whole, and its storage.

A paragraph summary explains one changed paragraph; a CR analysis explains the
change request behind it (what, why, what was decided, alternatives) once, from
the CR's own revision marks, the draft CRs it merges, the meeting agreements and
the moderator summaries. Paragraph summaries then reuse it.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import html as lhtml

_HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}
_CELL = {"p", "li", "td", "th"}


def _marked(el) -> str:
    """Text of an element with Word's revision marks as [+added+] and [-removed-]."""
    out = [el.text or ""]
    for ch in el:
        if ch.tag == "ins":
            out.append(f"[+{ch.text_content()}+]")
        elif ch.tag == "del":
            out.append(f"[-{ch.text_content()}-]")
        elif isinstance(ch.tag, str):
            out.append(_marked(ch))
        out.append(ch.tail or "")
    return "".join(out)


def _short(line: str, limit: int) -> str:
    """Long table rows and paragraphs: keep the marked parts with a little context."""
    line = " ".join(line.split())
    if len(line) <= limit:
        return line
    keep = []
    for m in re.finditer(r"\[[+-].*?[+-]\]", line):
        keep.append(line[max(0, m.start() - 60):m.end() + 60])
        if sum(map(len, keep)) > limit:
            break
    return " … ".join(keep)[:limit] or line[:limit]


def cr_changes(html: str, max_chars: int = 9000, line_chars: int = 500) -> list[dict]:
    """The changed paragraphs of a rendered CR (see crdocs.cr_document_html), grouped by the heading above."""
    root = lhtml.fragment_fromstring(html, create_parent="div")
    groups: list[dict] = []
    current = {"clause": "(before the first heading)", "lines": []}
    used = 0
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag in _HEAD:
            if current["lines"]:
                groups.append(current)
            current = {"clause": " ".join(el.text_content().split())[:80], "lines": []}
        if el.tag not in _CELL and el.tag not in _HEAD:
            continue
        if el.tag in ("td", "th") and el.xpath(".//p|.//li"):
            continue  # its paragraphs come on their own
        if not el.xpath(".//ins|.//del"):
            continue
        line = _short(_marked(el), line_chars)
        if line.strip("[]+- "):
            current["lines"].append(line)
            used += len(line)
        if used > max_chars:
            break
    if current["lines"]:
        groups.append(current)
    return groups


def evidence(lib, spec: str, row: dict, cache_dir: Path, finder=None, max_drafts: int = 20) -> dict:
    """Everything the model sees for one CR row of ``lib.crs(spec)`` (which must have a parsed cover)."""
    from . import crdocs
    from .pack import AgreementFinder

    cover = {k: v for k, v in row["detail"].items() if k not in ("clauses",)}
    ev = {"spec": spec, "cr": row["cr"], "rev": row["rev"], "tdoc": row["tdoc"], "meeting": row["meeting"],
          "cover": cover, "changes": [], "drafts": [], "agreements": [], "fl_summaries": []}
    try:
        doc = crdocs.cr_document_html(spec, row["cr"], row["tdoc"], row["meeting"], cache_dir)
        ev["changes"] = cr_changes(doc["html"])
    except Exception:  # no CR document: the cover still says a lot
        pass
    drafts = []
    for tdoc in crdocs.listed_tdocs(row["detail"])[:max_drafts]:
        try:
            d = crdocs.fetch_draft_cr(tdoc, row["detail"], cache_dir)
        except Exception:
            d = None
        if d:
            drafts.append(d)
    ev["drafts"] = [{k: d.get(k) for k in ("wg_tdoc", "title", "reason", "summary", "clauses_raw")} for d in drafts]
    lines = [ln for g in ev["changes"] for ln in g["lines"]]
    added = " ".join(re.findall(r"\[\+(.*?)\+\]", " ".join(lines)))
    probe = {"clause": (row["detail"].get("clauses") or [""])[0], "title": cover.get("title", ""),
             "text": (added or " ".join(lines) or cover.get("summary", ""))[:4000],
             "changes": [f"+ {ln}" for ln in lines[:12]]}
    finder = finder or AgreementFinder(cache_dir)
    try:
        ags = finder.find(probe, spec, [row], drafts)
        ev["agreements"] = ags
        ev["fl_summaries"] = finder.summaries(probe, ags)
    except Exception:  # meeting reports unreachable: analyse with what there is
        pass
    return ev


def brief(ev: dict) -> dict:
    """What is kept next to the analysis, to show where it came from."""
    return {"changes": ev["changes"][:40],
            "drafts": [{"tdoc": d["wg_tdoc"], "title": d["title"]} for d in ev["drafts"]],
            "agreements": [{k: a.get(k) for k in ("meeting", "agenda", "kind", "text", "folder")}
                           for a in ev["agreements"]],
            "fl_summaries": [{k: f.get(k) for k in ("tdoc", "title", "meeting", "url")}
                             | {"excerpt": (f.get("excerpt") or "")[:1200]} for f in ev["fl_summaries"]]}
