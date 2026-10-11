"""Build an annotation pack for one spec version from a specview library.

A pack lists every block that changed in that version (keyed by the block's
content hash, so any client with its own conversion can attach it) with:

* ``crs``       - CRs of the version whose "Clauses affected" cover the clause
* ``features``  - TR 38.822 features of those CRs' work items
* ``intent``    - optional LLM summary of why the change was made
* ``agreements``- optional WG agreements (chairman's notes) behind it

Used by the annotation server (offline, whole specs) and by the desktop app
(the user's own LLM API, a version or a single clause at a time).
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from typing import Callable, Protocol

from .diff import strip_tags
from .intent import QuotaExceeded
from .store import Library

PACK_FORMAT = "specview-annotations/1"


class IntentModel(Protocol):
    name: str

    def intent(self, change: dict) -> dict | None: ...


def _touches(cl: str, number: str) -> bool:
    return cl == number or number.startswith(cl + ".") or cl.startswith(number + ".")


def _text(html: str) -> str:
    return " ".join(strip_tags(html).split())


def changed_blocks(lib: Library, spec: str, version: str, clause_key: str | None = None) -> list[dict]:
    """Blocks whose last change happened in ``version``, with the previous text when it was modified."""
    vers = [v["version"] for v in lib.versions(spec)]
    i = vers.index(version)
    prev = vers[i - 1] if i else None
    old_text: dict[tuple[str, int], str] = {}
    if prev:
        cmp = lib.compare(spec, prev, version)
        for c in cmp["clauses"]:
            for j, b in enumerate(c["blocks"]):
                if b["old"]:
                    old_text[(c["key"], j)] = _text(b["old"])
    out = []
    raw = {(r["clause_key"], r["ord"]): r["raw_hash"] for r in lib.db.execute(
        "SELECT clause_key, ord, raw_hash FROM block_ver WHERE version_id=?", (lib.version_id(spec, version),))}
    for c in lib.clauses(spec, version, 0, 100000):
        if clause_key and c["key"] != clause_key:
            continue
        for j, b in enumerate(c["blocks"]):
            if b["changed"] != version:
                continue
            kind = b["history"][-1][1] if b["history"] else "added"
            out.append({"clause": c["number"] or c["key"], "clause_key": c["key"], "title": c["title"],
                        "raw_hash": raw[(c["key"], j)], "change": kind, "text": _text(b["html"]),
                        "old_text": old_text.get((c["key"], j))})
    return out


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "not", "shall", "which", "when", "where", "used",
         "given", "according", "clause", "otherwise", "than", "each", "also", "into", "only", "both", "then", "case"}


def word_changes(old: str | None, new: str, limit: int = 12) -> list[str]:
    """Removed (-) and added (+) word runs between two texts, with a little context."""
    if not old:
        return []
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        ctx = " ".join(a[max(0, i1 - 4):i1])
        if i2 > i1:
            out.append(f"- …{ctx} [{' '.join(a[i1:i2])}]")
        if j2 > j1:
            out.append(f"+ …{ctx} [{' '.join(b[j1:j2])}]")
        if len(out) >= limit:
            break
    return out


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)} - _STOP


def relevant_lines(covers: list[dict], change: dict, top: int = 6) -> list[str]:
    """Cover-page lines (combined CRs list one correction per line) ranked by overlap with the change."""
    focus = _terms(" ".join(change.get("changes") or [])) or _terms(change["text"])
    focus |= _terms(change.get("title") or "")
    scored = []
    for c in covers:
        for field in ("summary", "reason", "consequences"):
            for line in re.split(r"\n|(?<=[.;])\s+(?=[A-Z])", c.get(field) or ""):
                line = line.strip()
                n = len(focus & _terms(line))
                if n >= 2 and len(line) > 15:
                    scored.append((n, f"[CR{c.get('cr', '')} {field}] {line}"))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for _, line in scored:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out[:top]


def draft_crs(hit: list[dict], clause: str, cache_dir, session=None, max_per_cr: int = 40) -> list[dict]:
    """Individual draft CRs listed in combined CRs that changed ``clause`` (their own covers say what and why)."""
    from . import crdocs

    out = []
    for c in hit:
        d = c["detail"]
        for tdoc in crdocs.listed_tdocs(d)[:max_per_cr]:
            try:
                sub = crdocs.fetch_draft_cr(tdoc, d, cache_dir, session)
            except Exception:  # a missing or broken Tdoc must not stop the summary
                sub = None
            if sub and any(_touches(x, clause) for x in sub.get("clauses") or []):
                out.append({**sub, "parent_cr": c["cr"]})
    return out


class AgreementFinder:
    """WG meeting reports for the CRs of a pack, fetched once per meeting (see minutes.py)."""

    def __init__(self, cache_dir, max_meetings: int = 4):
        from . import minutes

        self.m = minutes
        self.cache_dir = cache_dir
        self.max_meetings = max_meetings
        self.reports: dict[str, dict | None] = {}
        self.index = None

    def _meetings(self, hit: list[dict], drafts: list[dict]) -> list[str]:
        from . import crdocs

        out: list[str] = []
        for d in drafts:
            if d.get("meeting_folder"):
                out.append(d["meeting_folder"])
        for c in hit:
            cov = c["detail"]
            out += crdocs.wg_meetings(" ".join(cov.get(k) or "" for k in ("title", "reason", "summary", "consequences")))
            if cov.get("wg_tdoc"):
                if self.index is None:
                    self.index = self.m.TdocIndex(self.cache_dir)
                try:
                    f = self.index.meeting_of(cov["wg_tdoc"])
                except Exception:
                    f = None
                if f:
                    out.append(f)
        seen = []
        for f in out:
            if f not in seen:
                seen.append(f)
        return seen[:self.max_meetings]

    def find(self, change: dict, spec: str, hit: list[dict], drafts: list[dict]) -> list[dict]:
        reports = []
        for f in self._meetings(hit, drafts):
            if f not in self.reports:
                try:
                    self.reports[f] = self.m.fetch_report(f, self.cache_dir)
                except Exception:
                    self.reports[f] = None
            if self.reports[f]:
                reports.append(self.reports[f])
        tdocs = {c["detail"]["wg_tdoc"] for c in hit if c["detail"].get("wg_tdoc")} | {d["wg_tdoc"] for d in drafts}
        return self.m.match_agreements(change, reports, tdocs, spec) if reports else []

    def summaries(self, change: dict, agreements: list[dict]) -> list[dict]:
        try:
            return self.m.summaries_for(change, agreements, {f: r for f, r in self.reports.items() if r}, self.cache_dir)
        except Exception:  # a missing summary must not cost the whole change
            return []


def build_pack(lib: Library, spec: str, version: str, model: IntentModel | None = None,
               progress: Callable[[int, int], None] | None = None, limit: int | None = None,
               clause_key: str | None = None, skip_uncovered: bool = False,
               skip_hashes: set[str] | frozenset = frozenset(), only_hashes: set[str] | None = None,
               resolve_drafts: bool = True, find_agreements: bool = True,
               cr_analyzer: Callable[[list[dict]], list[dict]] | None = None) -> dict:
    """``limit`` caps the LLM requests; ``skip_uncovered`` leaves changes no CR explains (front matter,
    copyright year ...) to the rules; ``skip_hashes`` are blocks that already have a summary;
    ``only_hashes`` restricts the pack to those blocks (e.g. the texts a clause shows today);
    ``cr_analyzer`` returns the (stored or new) analyses of the CRs behind a change, given their rows."""
    crs = [c for c in lib.crs(spec, version) if c["detail"]]
    changes = changed_blocks(lib, spec, version, clause_key)
    if only_hashes is not None:
        changes = [ch for ch in changes if ch["raw_hash"] in only_hashes]
    if model is not None:  # only the blocks the model will see, so progress and limit count requests
        changes = [ch for ch in changes if ch["raw_hash"] not in skip_hashes]
    stopped = None
    asked = uncovered = capped = 0
    finder = AgreementFinder(lib.path.parent / "cache") if model is not None and find_agreements else None
    for n, ch in enumerate(changes, 1):
        hit = [c for c in crs if any(_touches(x, ch["clause"]) for x in c["detail"]["clauses"])]
        ch["crs"] = [{"cr": c["cr"], "rev": c["rev"], "tdoc": c["tdoc"], "wg_tdoc": c["detail"]["wg_tdoc"],
                      "wi_code": c["detail"]["wi_code"], "category": c["cat"], "subject": c["subject"]} for c in hit]
        wis = {w.strip() for c in hit for w in c["detail"]["wi_code"].split(",")}
        feats = lib.clause_features(spec, version, ch["clause_key"])
        ch["features"] = sorted({f"{s['section']}:{f['index']}" for w in feats.get("work_items", [])
                                 if w["wi_code"] in wis
                                 for s in w["sections"] for f in s["features"] if f.get("shared")})
        ch["agreements"] = []  # TODO: chairman's notes (see annotation_server/chairnotes.py)
        ch["intent"] = None
        if model is not None and not hit and skip_uncovered:
            uncovered += 1
        elif model is not None and not stopped and limit is not None and asked >= limit:
            capped += 1
        elif model is not None and not stopped:
            asked += 1
            ctx = {**ch, "cr_covers": [{**c["detail"], "cr": c["cr"]} for c in hit],
                   "changes": word_changes(ch["old_text"], ch["text"])}
            ctx["relevant_lines"] = relevant_lines(ctx["cr_covers"], ctx)
            if resolve_drafts and hit:
                ctx["draft_covers"] = draft_crs(hit, ch["clause"], lib.path.parent / "cache")
                ch["draft_crs"] = [{"tdoc": d["wg_tdoc"], "title": d["title"], "parent_cr": d["parent_cr"],
                                    "meeting": d.get("meeting_folder", "").rsplit("/", 1)[-1],
                                    "folder": d.get("meeting_folder", "")} for d in ctx["draft_covers"]]
            if finder and hit:
                ch["agreements"] = finder.find(ctx, spec, hit, ctx.get("draft_covers", []))
                ctx["agreements"] = ch["agreements"]
                ctx["fl_summaries"] = finder.summaries(ctx, ch["agreements"])
                ch["fl_summaries"] = [{k: f[k] for k in ("tdoc", "title", "source", "meeting", "agenda", "url")}
                                      | {"excerpt": f["excerpt"][:1200]} for f in ctx["fl_summaries"]]
            try:
                if cr_analyzer and hit:  # the CRs first: analysed once, reused by every paragraph they touch
                    ctx["cr_analyses"] = cr_analyzer(hit)
                    ch["cr_analyses"] = [{k: a[k] for k in ("cr", "rev", "tdoc")} for a in ctx["cr_analyses"]]
                ch["intent"] = model.intent(ctx)
            except QuotaExceeded as e:  # every later request would fail the same way
                stopped = str(e)
                ch["intent_error"] = stopped
            except Exception as e:  # one bad response must not lose the whole pack
                ch["intent_error"] = str(e)[:300]
        if progress:
            progress(n, len(changes))
    for ch in changes:
        ch.pop("clause_key")
        ch.pop("text")
        ch.pop("old_text")
    gen = {"intent_model": getattr(model, "name", None)}
    if model is not None:
        gen.update(requested=asked, skipped_uncovered=uncovered, over_limit=capped)
    if stopped:
        gen["stopped"] = stopped
    return {
        "format": PACK_FORMAT,
        "spec": spec,
        "version": version,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "generator": gen,
        "changes": changes,
    }


def dumps(pack: dict) -> str:
    return json.dumps(pack, ensure_ascii=False, indent=1)
