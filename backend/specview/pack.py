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
import json
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


def build_pack(lib: Library, spec: str, version: str, model: IntentModel | None = None,
               progress: Callable[[int, int], None] | None = None, limit: int | None = None,
               clause_key: str | None = None, skip_uncovered: bool = True,
               skip_hashes: set[str] | frozenset = frozenset(), only_hashes: set[str] | None = None) -> dict:
    """``limit`` caps the LLM requests; ``skip_uncovered`` leaves changes no CR explains (front matter,
    copyright year ...) to the rules; ``skip_hashes`` are blocks that already have a summary;
    ``only_hashes`` restricts the pack to those blocks (e.g. the texts a clause shows today)."""
    crs = [c for c in lib.crs(spec, version) if c["detail"]]
    changes = changed_blocks(lib, spec, version, clause_key)
    if only_hashes is not None:
        changes = [ch for ch in changes if ch["raw_hash"] in only_hashes]
    if model is not None:  # only the blocks the model will see, so progress and limit count requests
        changes = [ch for ch in changes if ch["raw_hash"] not in skip_hashes]
    stopped = None
    asked = uncovered = capped = 0
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
            ctx = {**ch, "cr_covers": [c["detail"] for c in hit]}
            try:
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
