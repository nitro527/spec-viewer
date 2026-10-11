"""Question answering over the downloaded material: the specs (every version), their change history, the CRs
(covers and AI analyses), meeting agreements and work item summaries.

The model gets tools to look things up (search, read a clause, its history, compare versions, CRs, work items)
and answers with source tags such as [[38.211 §6.3.3.2 v19.5.0]] that the viewer turns into links. Servers
without function calling get the same material in one prompt: keyword queries from the model, then search.
"""
from __future__ import annotations

import json
import re
from typing import Callable

import html as htmllib

from .diff import align, render_diff, strip_tags
from .versions import Version

SYSTEM = (
    "You answer questions about 3GPP specifications for physical-layer modem engineers. Use ONLY the material "
    "the tools return: the downloaded specs (every version), their change history, change requests (cover pages "
    "and earlier AI analyses), meeting agreements and work item summaries - not your own memory of the specs. "
    "Search with English 3GPP terms (PUSCH, msgA, DM-RS, parameter names) even when the question is in another "
    "language; read the clauses you rely on; for 'when/why did this change' use clause_history, compare_clause, "
    "get_cr and work_item; for 'what changed between versions / in this release' use crs_between. Cite every fact with the source tag exactly as a tool gave it, e.g. "
    "[[38.211 §6.3.3.2 v19.5.0]], [[CR 38.211 0023]], [[WI NR_2step_RACH-Core]] - one source per tag "
    "([[CR 38.211 0042]] [[CR 38.211 0045]], not [[CR 38.211 0042, 0045]]). When the material does not "
    "answer the question, say so plainly. Answer in {language}; keep 3GPP terms and parameter names in English; "
    "use short markdown (headings, bullet lists)."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_specs", "description": "The downloaded specifications with their titles and versions.",
        "parameters": {"type": "object", "properties": {  # some servers reject an empty properties object
            "series": {"type": "string", "description": "optional filter, e.g. 38"}}}}},
    {"type": "function", "function": {
        "name": "search", "description": "Full-text search in the specs (latest version of each, or the given "
        "spec/version). Use English 3GPP terms. Returns clauses with a snippet and a source tag.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "English keywords, e.g. 'msgA PUSCH DM-RS'"},
            "spec": {"type": "string", "description": "e.g. 38.211 (optional)"},
            "version": {"type": "string", "description": "e.g. 16.2.0 (optional; default latest)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_clause", "description": "The text of one clause (default: latest version) and its sub-clauses.",
        "parameters": {"type": "object", "properties": {
            "spec": {"type": "string"}, "clause": {"type": "string", "description": "clause number, e.g. 6.3.3.2"},
            "version": {"type": "string"}}, "required": ["spec", "clause"]}}},
    {"type": "function", "function": {
        "name": "clause_history", "description": "In which versions a clause changed, the CRs that name it "
        "(title, meeting, work item) and stored AI summaries of those changes.",
        "parameters": {"type": "object", "properties": {
            "spec": {"type": "string"}, "clause": {"type": "string"}}, "required": ["spec", "clause"]}}},
    {"type": "function", "function": {
        "name": "compare_clause", "description": "What changed in a clause between two versions, as "
        "[-removed-] and [+added+] text.",
        "parameters": {"type": "object", "properties": {
            "spec": {"type": "string"}, "clause": {"type": "string"},
            "old": {"type": "string", "description": "older version, e.g. 15.9.0"},
            "new": {"type": "string", "description": "newer version (default latest)"}},
            "required": ["spec", "clause", "old"]}}},
    {"type": "function", "function": {
        "name": "crs_between", "description": "What changed between two versions of a spec: the CRs in between "
        "(title, category, work item, clauses, stored AI analysis), the changed clauses and stored release notes.",
        "parameters": {"type": "object", "properties": {
            "spec": {"type": "string"}, "old": {"type": "string", "description": "older version, e.g. 16.1.0"},
            "new": {"type": "string", "description": "newer version (default latest)"}},
            "required": ["spec", "old"]}}},
    {"type": "function", "function": {
        "name": "referenced_by", "description": "Paragraphs that cite a clause, in the same spec and in the other "
        "downloaded specs (latest versions): what else is affected when that clause changes.",
        "parameters": {"type": "object", "properties": {
            "spec": {"type": "string"}, "clause": {"type": "string", "description": "clause number, e.g. 4.5"}},
            "required": ["spec", "clause"]}}},
    {"type": "function", "function": {
        "name": "search_crs", "description": "Find change requests by words in their title, work item code, "
        "clauses or AI analysis.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "spec": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_cr", "description": "One change request: cover page (reason, summary, clauses, work item) "
        "and the stored AI analysis (background, meeting decision, impact).",
        "parameters": {"type": "object", "properties": {
            "spec": {"type": "string"}, "cr": {"type": "string", "description": "CR number, e.g. 0023"}},
            "required": ["spec", "cr"]}}},
    {"type": "function", "function": {
        "name": "work_item", "description": "A work item (feature) across all downloaded specs: its CRs and the "
        "stored AI summary.",
        "parameters": {"type": "object", "properties": {
            "wi": {"type": "string", "description": "work item code, e.g. NR_2step_RACH-Core"}},
            "required": ["wi"]}}},
]

_MAX_RESULT = 9000  # characters of one tool result given back to the model


def _norm_spec(spec: str) -> str:
    s = (spec or "").strip().upper().removeprefix("TS").removeprefix("TR").strip()
    return s if "." in s or not re.fullmatch(r"\d{5}", s) else f"{s[:2]}.{s[2:]}"


def _touches(x: str, num: str) -> bool:
    return x == num or num.startswith(x + ".") or x.startswith(num + ".")


class SpecTools:
    """The lookups the model can call. ``sources`` collects every tag a result handed out, so the answer's
    tags can be turned into links (clause, CR document or work item)."""

    def __init__(self, lib):
        self.lib = lib
        self.sources: dict[str, dict] = {}

    # -- helpers

    def _versions(self, spec: str) -> list[str]:
        return [v["version"] for v in self.lib.versions(spec)]

    def _version(self, spec: str, version: str | None) -> str:
        vs = self._versions(spec)
        if not vs:
            raise KeyError(f"{spec} 는 받은 스펙이 아닙니다 (list_specs 참고)")
        if version and version.lstrip("v") in vs:
            return version.lstrip("v")
        return vs[-1]

    def _clause(self, spec: str, clause: str, version: str):
        c = clause.strip().lstrip("§").strip()
        toc = self.lib.toc(spec, version)
        e = next((t for t in toc if t["number"] == c or t["key"] == c), None) \
            or next((t for t in toc if t["title"].lower() == c.lower()), None)
        if e is None:
            raise KeyError(f"{spec} v{version} 에 {clause} 절이 없습니다")
        return e, toc

    def _clause_tag(self, spec: str, e: dict, version: str) -> str:
        tag = f"{spec} §{e['number'] or e['title']} v{version}"
        self.sources[tag] = {"type": "clause", "spec": spec, "clause": e["number"] or e["key"], "key": e["key"],
                             "version": version, "title": e["title"]}
        return tag

    def _cr_tag(self, spec: str, row: dict) -> str:
        tag = f"CR {spec} {row['cr']}"
        self.sources[tag] = {"type": "cr", "spec": spec, "cr": row["cr"], "tdoc": row["tdoc"],
                             "title": (row.get("detail") or {}).get("title") or row.get("subject", "")}
        return tag

    def _wi_tag(self, wi: str) -> str:
        tag = f"WI {wi}"
        self.sources[tag] = {"type": "wi", "wi": wi}
        return tag

    def _paras(self, spec: str, version: str, key: str) -> list[str]:
        vid = self.lib.version_id(spec, version)
        rows = self.lib.db.execute(
            "SELECT x.text FROM block_ver b JOIN blob x ON x.hash=b.raw_hash WHERE b.version_id=? AND b.clause_key=? "
            "ORDER BY b.ord", (vid, key)).fetchall()
        return [" ".join(r["text"].split()) for r in rows if r["text"].strip()]

    def _text(self, spec: str, version: str, key: str) -> str:
        return "\n".join(self._paras(spec, version, key))

    # -- tools

    def call(self, name: str, args: dict) -> str:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:  # small models pass numbers (clause 6.3, version 16.2): every argument is text here
            out = fn(**{k: str(v) for k, v in args.items() if v not in (None, "")})
        except KeyError as e:
            out = {"error": str(e).strip("'\"")}
        except TypeError as e:
            out = {"error": f"bad arguments: {e}"}
        except Exception as e:  # noqa: BLE001 - the model gets the error and can try again
            out = {"error": f"{type(e).__name__}: {e}"}
        text = json.dumps(out, ensure_ascii=False)
        return text if len(text) <= _MAX_RESULT else text[:_MAX_RESULT] + "…(truncated)"

    def t_list_specs(self, series: str | None = None) -> list[dict]:
        out = []
        for s in self.lib.specs():
            if series and not s["number"].startswith(series.strip().rstrip(".") + "."):
                continue
            vs = self._versions(s["number"])
            out.append({"spec": s["number"], "title": s.get("title"), "versions": len(vs),
                        "first": vs[0] if vs else None, "latest": vs[-1] if vs else None})
        return out

    def t_search(self, query: str, spec: str | None = None, version: str | None = None, limit: int = 10) -> dict:
        words = [w for w in re.findall(r"[\w\-.]+", query) if len(w) > 1]
        if not words:
            return {"hits": []}
        specs = [_norm_spec(spec)] if spec else [s["number"] for s in self.lib.specs()]
        vids = {}
        for sp in specs:
            try:
                v = self._version(sp, version)
            except KeyError:
                continue
            vids[self.lib.version_id(sp, v)] = (sp, v)
        if not vids:
            return {"hits": [], "note": "no such spec downloaded"}
        hits = []
        for joiner in (" ", " OR "):  # every word first, then any word
            fts = joiner.join('"' + w.replace('"', "") + '"' for w in words)
            q = ("SELECT b.version_id, c.key, c.number, c.title, snippet(blob_fts, 1, '', '', '…', 16) AS snip, "
                 "bm25(blob_fts) AS score FROM blob_fts f JOIN block_ver b ON b.raw_hash = f.hash "
                 f"AND b.version_id IN ({','.join('?' * len(vids))}) "
                 "JOIN clause_ver c ON c.version_id = b.version_id AND c.key = b.clause_key "
                 "WHERE blob_fts MATCH ? ORDER BY score LIMIT 200")
            rows = self.lib.db.execute(q, (*vids, fts)).fetchall()
            seen = set()
            for r in rows:
                k = (r["version_id"], r["key"])
                if k in seen:
                    continue
                seen.add(k)
                if re.search(r"change history", r["title"], re.I):  # CR lists: search_crs covers them
                    continue
                sp, v = vids[r["version_id"]]
                e = {"key": r["key"], "number": r["number"], "title": r["title"]}
                hits.append({"source": self._clause_tag(sp, e, v), "title": r["title"], "snippet": r["snip"]})
                if len(hits) >= limit:
                    break
            if len(hits) >= 3:
                break
        return {"hits": hits[:limit]}

    def t_read_clause(self, spec: str, clause: str, version: str | None = None) -> dict:
        spec = _norm_spec(spec)
        version = self._version(spec, version)
        e, toc = self._clause(spec, clause, version)
        text = self._text(spec, version, e["key"])
        subs = [f"{t['number']} {t['title']}" for t in toc
                if t["number"] and e["number"] and t["number"].startswith(e["number"] + ".")][:40]
        return {"source": self._clause_tag(spec, e, version), "title": e["title"], "text": text[:7000],
                "truncated": len(text) > 7000, "sub_clauses": subs,
                "versions_downloaded": self._versions(spec)}

    def t_clause_history(self, spec: str, clause: str) -> dict:
        spec = _norm_spec(spec)
        latest = self._version(spec, None)
        e, _ = self._clause(spec, clause, latest)
        row = self.lib.db.execute("SELECT introduced, history FROM clause_ver WHERE version_id=? AND key=?",
                                  (self.lib.version_id(spec, latest), e["key"])).fetchone()
        num = e["number"]
        crs = [{"source": self._cr_tag(spec, c), "version": c["lib_version"] or c["new_version"],
                "meeting": c["meeting"], "category": c["cat"], "title": c["detail"].get("title") or c["subject"],
                "work_item": c["detail"].get("wi_code", "")}
               for c in self.lib.crs(spec) if c["detail"] and c["cr"] and num
               and any(_touches(x, num) for x in c["detail"].get("clauses", []))]
        summaries = []
        for r in self.lib.db.execute("SELECT version, data FROM annotation WHERE spec=? AND clause=?", (spec, num)):
            d = json.loads(r["data"])
            if (it := d.get("intent")) and it.get("summary"):
                summaries.append({"version": r["version"], "what": it["summary"], "why": it.get("motivation", "")})
        summaries.sort(key=lambda s: Version.parse(s["version"]).sort_key)
        return {"source": self._clause_tag(spec, e, latest), "introduced": row["introduced"] if row else None,
                "changed_in": [h[0] for h in json.loads(row["history"] or "[]")] if row else [],
                "crs": crs[-30:], "change_summaries": summaries[-20:]}

    def t_compare_clause(self, spec: str, clause: str, old: str, new: str | None = None) -> dict:
        spec = _norm_spec(spec)
        vs = self._versions(spec)
        old = old.lstrip("v")
        if old not in vs:
            raise KeyError(f"{spec} v{old} 는 받은 버전이 아닙니다: {', '.join(vs)}")
        new = self._version(spec, new)
        e_new, _ = self._clause(spec, clause, new)
        try:
            e_old, _ = self._clause(spec, clause, old)
            before = self._paras(spec, old, e_old["key"])
        except KeyError:
            before = []
        after = self._paras(spec, new, e_new["key"])
        changed = []
        for oi, ni in align(before, after, key=lambda x: x, text=lambda x: x):
            o = before[oi] if oi is not None else None
            n = after[ni] if ni is not None else None
            if o == n:
                continue
            if o and n:
                d = render_diff(htmllib.escape(o), htmllib.escape(n))
                d = d.replace("<ins>", "[+").replace("</ins>", "+]").replace("<del>", "[-").replace("</del>", "-]")
                changed.append(strip_tags(d))
            else:
                changed.append(f"[+{n}+]" if n else f"[-{o}-]")
        return {"source": self._clause_tag(spec, e_new, new), "old": old, "new": new,
                "changes": [c[:1500] for c in changed[:40]] or ["(no change)"]}

    def t_crs_between(self, spec: str, old: str, new: str | None = None) -> dict:
        spec = _norm_spec(spec)
        vs = self._versions(spec)
        old, new = old.lstrip("v"), self._version(spec, new)
        if old not in vs:
            raise KeyError(f"{spec} v{old} 는 받은 버전이 아닙니다: {', '.join(vs)}")
        data = self.lib.release_input(spec, old, new)
        rows = {(c["cr"], c["tdoc"]): c for c in self.lib.crs(spec)}
        note = self.lib.release_note(spec, old, new)
        return {"old": old, "new": new, "changed_clauses": data["changed_clauses"][:80],
                "release_notes": {k: note.get(k) for k in ("overview", "features", "corrections", "impact")}
                if note else None,
                "crs": [{"source": self._cr_tag(spec, rows[(c["cr"], c["tdoc"])]), "version": c["version"],
                         "category": c["cat"], "title": c["title"], "work_item": c["wi"], "clauses": c["clauses"],
                         "analysis": (c["analysis"] or {}).get("summary")} for c in data["crs"][:60]]}

    def t_referenced_by(self, spec: str, clause: str) -> dict:
        spec = _norm_spec(spec)
        hits = self.lib.referenced_by(spec, clause.lstrip("§ "))
        return {"clause": clause, "count": len(hits), "citing": [
            {"source": self._clause_tag(h["spec"], h, h["version"]), "text": h["snippet"].replace("[[", "").replace("]]", "")}
            for h in hits[:40]]}

    def t_search_crs(self, query: str, spec: str | None = None) -> dict:
        words = [w.lower() for w in re.findall(r"[\w\-.]+", query) if len(w) > 1]
        specs = [_norm_spec(spec)] if spec else [s["number"] for s in self.lib.specs()]
        scored = []
        for sp in specs:
            analyses = {(a["cr"], a["tdoc"]): a for a in self.lib.cr_analyses(sp)}
            for c in self.lib.crs(sp):
                if not c["cr"]:
                    continue
                d = c["detail"] or {}
                a = analyses.get((c["cr"], c["tdoc"]))
                hay = " ".join([c["subject"] or "", d.get("title", ""), d.get("wi_code", ""), d.get("clauses_raw", ""),
                                d.get("summary", ""), (a or {}).get("summary", "")]).lower()
                score = sum(w in hay for w in words)
                if score:
                    scored.append((score, sp, c, a))
        scored.sort(key=lambda x: (-x[0], x[2]["new_version"]))
        return {"crs": [{"source": self._cr_tag(sp, c), "version": c["lib_version"] or c["new_version"],
                         "meeting": c["meeting"], "title": (c["detail"] or {}).get("title") or c["subject"],
                         "work_item": (c["detail"] or {}).get("wi_code", ""),
                         "clauses": (c["detail"] or {}).get("clauses_raw", ""),
                         "analysis": (a or {}).get("summary")} for _, sp, c, a in scored[:15]]}

    def t_get_cr(self, spec: str, cr: str) -> dict:
        spec = _norm_spec(spec)
        cr = cr.upper().removeprefix("CR").strip().zfill(4)
        rows = [c for c in self.lib.crs(spec) if c["cr"] == cr]
        if not rows:
            raise KeyError(f"{spec} 의 Change history 에 CR{cr} 이 없습니다")
        c = rows[-1]
        d = c["detail"] or {}
        a = self.lib.cr_analysis(spec, c["cr"], c["rev"], c["tdoc"]) if c["detail"] else None
        return {"source": self._cr_tag(spec, c), "tdoc": c["tdoc"], "meeting": c["meeting"], "category": c["cat"],
                "in_version": c["lib_version"] or c["new_version"], "title": d.get("title") or c["subject"],
                "work_item": d.get("wi_code", ""), "clauses": d.get("clauses_raw", ""),
                "reason": d.get("reason", "")[:1500], "summary": d.get("summary", "")[:2000],
                "analysis": {k: a.get(k) for k in ("summary", "background", "changes", "decision", "impact")} if a else
                "not analysed yet"}

    def t_work_item(self, wi: str) -> dict:
        w = self.lib.work_item(wi.strip())
        if not w["crs"]:
            raise KeyError(f"{wi} 의 CR 이 받은 스펙에 없습니다")
        sm = w["summary"]
        return {"source": self._wi_tag(w["wi"]),
                "summary": {k: sm.get(k) for k in ("overview", "motivation", "spec_changes", "timeline", "impact")}
                if sm else "not summarised yet",
                "crs": [{"source": self._cr_tag(c["spec"], {**c, "detail": {"title": c["title"]}}),
                         "meeting": c["meeting"], "version": c["new_version"], "category": c["cat"],
                         "title": c["title"], "clauses": c["clauses_raw"]} for c in w["crs"][:40]]}


def _step_label(name: str, args: dict) -> str:
    a = {k: str(v) for k, v in args.items() if v}
    return {
        "search": lambda: f"검색: {a.get('query', '')}" + (f" ({a['spec']})" if a.get("spec") else ""),
        "read_clause": lambda: f"읽기: {a.get('spec', '')} §{a.get('clause', '')}",
        "clause_history": lambda: f"변경 이력: {a.get('spec', '')} §{a.get('clause', '')}",
        "compare_clause": lambda: f"비교: {a.get('spec', '')} §{a.get('clause', '')} v{a.get('old', '')}→{a.get('new', '최신')}",
        "search_crs": lambda: f"CR 찾기: {a.get('query', '')}",
        "referenced_by": lambda: f"인용하는 곳: {a.get('spec', '')} §{a.get('clause', '')}",
        "crs_between": lambda: f"버전 사이 CR: {a.get('spec', '')} v{a.get('old', '')}→{a.get('new', '최신')}",
        "get_cr": lambda: f"CR: {a.get('spec', '')} CR{a.get('cr', '')}",
        "work_item": lambda: f"WI: {a.get('wi', '')}",
        "list_specs": lambda: "받은 스펙 목록",
    }.get(name, lambda: name)()


def _context_tag(tools: SpecTools, context: dict | None) -> str | None:
    """The clause the question was asked from, as a source the answer can cite."""
    if not context or not context.get("spec") or not context.get("clause"):
        return None
    try:
        spec = _norm_spec(context["spec"])
        version = tools._version(spec, context.get("version"))
        e, _ = tools._clause(spec, context["clause"], version)
        return tools._clause_tag(spec, e, version)
    except KeyError:
        return None


def _user_text(question: str, context: dict | None, tag: str | None = None) -> str:
    if not context:
        return question
    head = f"(The user is looking at TS {context.get('spec')} §{context.get('clause', '')}"
    if context.get("version"):
        head += f" v{context['version']}"
    head += f"; cite it as [[{tag}]].)" if tag else ".)"
    if context.get("text"):
        head += f"\nSelected paragraph:\n{context['text'][:2500]}"
    return f"{head}\n\nQuestion: {question}"


_BUDGET = 60000  # characters of tool results kept in full; older ones shrink to a stub (context window)


def _tool_calls(msg: dict, step: int) -> list[dict]:
    """Tool calls as every OpenAI-compatible server accepts them back: an id, type "function", arguments as a
    JSON string (some servers send a dict, leave out the id or the type); extra fields (e.g. Gemini's thought
    signature) are kept."""
    out = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = dict(tc.get("function") or {})
        args = fn.get("arguments")
        if not isinstance(args, str):
            fn["arguments"] = json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False)
        out.append({**tc, "id": tc.get("id") or f"call_{step}_{i}", "type": "function", "function": fn})
    return out


def _args(tc: dict) -> dict:
    try:
        args = json.loads(tc["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


def _shrink(messages: list[dict]) -> None:
    """Keep the newest tool results whole and shrink older ones once the total passes the budget."""
    total = 0
    for m in reversed(messages):
        if m["role"] != "tool":
            continue
        total += len(m["content"])
        if total > _BUDGET and not m["content"].startswith('{"omitted"'):
            m["content"] = json.dumps({"omitted": "earlier result shortened to save space; call the tool again "
                                                  "if you still need it"})


class Stopped(Exception):
    """The user pressed stop: raised between model requests (the one in flight is not cut off)."""


def answer(model, lib, question: str, history: list[dict] | None = None, context: dict | None = None,
           language: str = "Korean", max_steps: int = 10, step: Callable[[str], None] | None = None,
           known_sources: dict | None = None, should_stop: Callable[[], bool] | None = None) -> dict:
    """Answer one question; ``history`` is the earlier turns ({role, content}); ``known_sources`` the sources
    earlier answers cited (a follow-up may cite them again). Returns {"answer", "sources", "steps", "mode",
    "model"}."""
    from .intent import LLMError, ToolsUnsupported

    tools = SpecTools(lib)
    tools.sources.update(known_sources or {})
    steps: list[str] = []

    def check():
        if should_stop and should_stop():
            raise Stopped()

    def note(s: str):
        check()
        steps.append(s)
        if step:
            step(s)

    tag = _context_tag(tools, context)
    messages = [{"role": "system", "content": SYSTEM.format(language=language)}]
    for m in (history or [])[-8:]:
        messages.append({"role": m["role"], "content": m["content"][:4000]})
    messages.append({"role": "user", "content": _user_text(question, context, tag)})
    try:
        for n in range(max_steps):
            check()
            try:
                msg = model.complete(messages, TOOLS)
            except LLMError as e:  # context window: answer with what was found so far
                if n == 0 or not re.search(r"context|token|length|too long|maximum", str(e), re.I):
                    raise
                break
            calls = _tool_calls(msg, n)
            if not calls:
                return {"answer": msg["content"], "sources": tools.sources, "steps": steps, "mode": "tools",
                        "model": msg.get("model")}
            messages.append({k: v for k, v in msg.items() if k not in ("model",)} | {"role": "assistant",
                            "content": msg.get("content") or "", "tool_calls": calls})
            for tc in calls:
                name, args = tc["function"].get("name", ""), _args(tc)
                note(_step_label(name, args))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name,
                                 "content": tools.call(name, args)})
            _shrink(messages)
        messages.append({"role": "user", "content": f"Answer now with the material above, in {language}."})
        _shrink(messages)
        check()
        msg = model.complete(messages)
        return {"answer": msg["content"], "sources": tools.sources, "steps": steps, "mode": "tools",
                "model": msg.get("model")}
    except ToolsUnsupported:
        note("이 LLM 서버는 도구 호출을 지원하지 않아 검색 결과를 한 번에 넘겨 답합니다")
        return _answer_without_tools(model, tools, question, history, context, language, note, steps, tag)


_QUERY_PROMPT = (
    "Turn the question into search queries for 3GPP specifications. Reply with only JSON: "
    '{"queries": ["2-5 English keyword queries with 3GPP terms"], "spec": "spec number if the question names '
    'one, else empty", "cr": "CR number if named, else empty", "wi": "work item code if named, else empty"}'
)


def _answer_without_tools(model, tools: SpecTools, question, history, context, language, note, steps,
                          tag: str | None = None) -> dict:
    from .intent import _parse_json

    msg = model.complete([{"role": "system", "content": _QUERY_PROMPT},
                          {"role": "user", "content": _user_text(question, context)}])
    try:
        plan = _parse_json(msg["content"])
    except Exception:
        plan = {"queries": [question]}
    spec = _norm_spec(plan.get("spec") or (context or {}).get("spec") or "")
    material = []
    if context and context.get("spec") and context.get("clause"):
        note(f"읽기: {context['spec']} §{context['clause']}")
        material.append(tools.call("read_clause", {"spec": context["spec"], "clause": context["clause"],
                                                   "version": context.get("version")}))
        material.append(tools.call("clause_history", {"spec": context["spec"], "clause": context["clause"]}))
    read = set()
    for q in (plan.get("queries") or [question])[:4]:
        note(f"검색: {q}")
        res = json.loads(tools.call("search", {"query": q, "spec": spec or None}))
        material.append(json.dumps(res, ensure_ascii=False))
        for h in res.get("hits", [])[:2]:
            src = tools.sources.get(h["source"])
            if src and h["source"] not in read and len(read) < 5:
                read.add(h["source"])
                note(f"읽기: {src['spec']} §{src['clause']}")
                material.append(tools.call("read_clause", {"spec": src["spec"], "clause": src["clause"],
                                                           "version": src["version"]}))
    if plan.get("cr") and spec:
        note(f"CR: {spec} CR{plan['cr']}")
        material.append(tools.call("get_cr", {"spec": spec, "cr": plan["cr"]}))
    if plan.get("wi"):
        note(f"WI: {plan['wi']}")
        material.append(tools.call("work_item", {"wi": plan["wi"]}))
    note("CR 찾기")
    material.append(tools.call("search_crs", {"query": " ".join(plan.get("queries") or [question]), "spec": spec or None}))
    body = "\n\n".join(material)[:40000]
    messages = [{"role": "system", "content": SYSTEM.format(language=language)}]
    for m in (history or [])[-6:]:
        messages.append({"role": m["role"], "content": m["content"][:3000]})
    messages.append({"role": "user", "content": f"<material>\n{body}\n</material>\n\n{_user_text(question, context, tag)}"})
    out = model.complete(messages)
    return {"answer": out["content"], "sources": tools.sources, "steps": steps, "mode": "retrieval",
            "model": out.get("model")}
