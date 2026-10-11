"""SQLite library: parsed versions, deduplicated content, history results and FTS index.

Block content is stored once per distinct hash, so dozens of versions of a spec
cost little more than one: most clauses do not change between versions.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterator

from . import changehistory, history
from .diff import render_diff
from .parse_docx import ParsedSpec, content_hash
from .versions import Version

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS spec (
  number TEXT PRIMARY KEY,
  title  TEXT
);
CREATE TABLE IF NOT EXISTS version (
  id       INTEGER PRIMARY KEY,
  spec     TEXT NOT NULL REFERENCES spec(number),
  version  TEXT NOT NULL,
  release  INTEGER NOT NULL,
  sort_key INTEGER NOT NULL,
  source   TEXT,
  UNIQUE(spec, version)
);
CREATE TABLE IF NOT EXISTS blob (
  hash TEXT PRIMARY KEY,
  html TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset (
  hash TEXT PRIMARY KEY,
  mime TEXT NOT NULL,
  data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS clause_ver (
  version_id   INTEGER NOT NULL REFERENCES version(id) ON DELETE CASCADE,
  ord          INTEGER NOT NULL,
  key          TEXT NOT NULL,
  number       TEXT NOT NULL,
  title        TEXT NOT NULL,
  level        INTEGER NOT NULL,
  status       TEXT,
  introduced   TEXT,
  last_changed TEXT,
  history      TEXT,
  deleted      TEXT,
  PRIMARY KEY (version_id, ord)
);
CREATE INDEX IF NOT EXISTS clause_ver_key ON clause_ver(version_id, key);
CREATE TABLE IF NOT EXISTS block_ver (
  version_id INTEGER NOT NULL REFERENCES version(id) ON DELETE CASCADE,
  clause_key TEXT NOT NULL,
  ord        INTEGER NOT NULL,
  kind       TEXT NOT NULL,
  style      TEXT NOT NULL,
  raw_hash   TEXT NOT NULL REFERENCES blob(hash),
  annot_hash TEXT REFERENCES annot(hash),
  PRIMARY KEY (version_id, clause_key, ord)
);
CREATE INDEX IF NOT EXISTS block_ver_raw ON block_ver(raw_hash);
CREATE TABLE IF NOT EXISTS annot (
  hash       TEXT PRIMARY KEY,
  html       TEXT NOT NULL,
  introduced TEXT NOT NULL,
  changed    TEXT,
  history    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS removed_clause (
  version_id INTEGER NOT NULL REFERENCES version(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  title      TEXT
);
CREATE TABLE IF NOT EXISTS cr (
  spec        TEXT NOT NULL,
  new_version TEXT NOT NULL,
  date TEXT, meeting TEXT, tdoc TEXT, cr TEXT, rev TEXT, cat TEXT, subject TEXT,
  UNIQUE(spec, new_version, tdoc, cr, rev)
);
CREATE TABLE IF NOT EXISTS cr_detail (
  spec TEXT NOT NULL, cr TEXT NOT NULL, rev TEXT NOT NULL, tdoc TEXT NOT NULL,
  status TEXT NOT NULL,            -- ok | missing | error
  cover TEXT,                      -- JSON of crdocs.CRCover
  error TEXT,
  PRIMARY KEY (spec, cr, rev, tdoc)
);
CREATE TABLE IF NOT EXISTS ue_feature (
  source TEXT NOT NULL,            -- 38.822 version the list was read from
  section TEXT NOT NULL, section_title TEXT, layer TEXT, release TEXT,
  idx TEXT NOT NULL, grp TEXT, components TEXT, prerequisite TEXT,
  field_name TEXT, parent_ie TEXT, mandatory TEXT, note TEXT, terms TEXT
);
CREATE TABLE IF NOT EXISTS annotation (
  spec TEXT NOT NULL, version TEXT NOT NULL, raw_hash TEXT NOT NULL, clause TEXT, data TEXT NOT NULL,
  PRIMARY KEY (spec, version, raw_hash)
);
CREATE TABLE IF NOT EXISTS cr_analysis (     -- LLM analysis of one CR as a whole (cranalysis.py)
  spec TEXT NOT NULL, cr TEXT NOT NULL, rev TEXT NOT NULL, tdoc TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (spec, cr, rev, tdoc)
);
CREATE TABLE IF NOT EXISTS wi_summary (      -- LLM summary of one work item across every downloaded spec
  wi TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (            -- AI chat conversations (chat.py)
  id TEXT PRIMARY KEY, title TEXT NOT NULL, updated TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS note (          -- user bookmarks/notes, keyed by clause so they survive versions
  spec TEXT NOT NULL, clause_key TEXT NOT NULL, bookmark INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',
  version TEXT, updated TEXT NOT NULL,
  PRIMARY KEY (spec, clause_key)
);
CREATE VIRTUAL TABLE IF NOT EXISTS blob_fts USING fts5(hash UNINDEXED, text, tokenize = 'unicode61');
"""


def _mark_moves(clauses: list[dict]) -> None:
    """Pair deletions and insertions of (nearly) the same paragraph in different places as a move.

    Identical text is paired through a dict first; only what is left is compared word by word, and only
    with deletions of a length that could still reach the similarity floor (a reorganised conformance
    spec has thousands of each)."""
    import bisect

    from .diff import strip_tags, word_similarity

    dels = [(c, b) for c in clauses for b in c["blocks"] if b["op"] == "delete"]
    ins = [(c, b) for c in clauses for b in c["blocks"] if b["op"] == "insert"]
    text = {id(b): " ".join(strip_tags(b["old"] or b["new"] or "").split()) for _, b in dels + ins}
    used: set[int] = set()

    def pair(ic, ib, dc, db) -> None:
        used.add(id(db))
        db["op"], db["moved_to"] = "move-out", ic["number"] or ic["title"]
        ib["op"], ib["moved_from"] = "move-in", dc["number"] or dc["title"]
        ib["diff"] = render_diff(db["old"], ib["new"])

    same: dict[str, list] = {}
    for dc, db in dels:
        same.setdefault(text[id(db)], []).append((dc, db))
    fuzzy = []
    for ic, ib in ins:
        t = text[id(ib)]
        if len(t) < 40:
            continue
        hit = next(((dc, db) for dc, db in same.get(t, ()) if id(db) not in used and dc is not ic), None)
        if hit:
            pair(ic, ib, *hit)
        else:
            fuzzy.append((ic, ib))
    floor = 0.8
    words = {id(b): text[id(b)].split() for _, b in dels}
    by_len = sorted((len(words[id(b)]), i) for i, (_, b) in enumerate(dels) if id(b) not in used)
    lens = [n for n, _ in by_len]
    for ic, ib in fuzzy:
        w = text[id(ib)].split()
        lo, hi = len(w) * floor / (2 - floor), len(w) * (2 - floor) / floor  # 2·min/(a+b) ≥ floor
        best, best_s = None, floor
        for _, i in by_len[bisect.bisect_left(lens, lo):bisect.bisect_right(lens, hi)]:
            dc, db = dels[i]
            if id(db) in used or dc is ic:
                continue
            sc = word_similarity(words[id(db)], w, best_s)
            if sc >= best_s:
                best, best_s = (dc, db), sc
        if best is not None:
            pair(ic, ib, *best)


def wi_codes(detail: dict | None) -> list[str]:
    """Work item codes of a CR cover ("NR_unlic-Core, NR_IAB-Core" lists several)."""
    return [w for w in re.split(r"[,;\s]+", (detail or {}).get("wi_code") or "") if w]


def _feature_out(f: dict) -> dict:
    return {"section": f["section"], "section_title": f["section_title"], "release": f["release"], "layer": f["layer"],
            "index": f["idx"], "group": f["grp"], "components": f["components"], "prerequisite": f["prerequisite"],
            "field_name": f["field_name"], "mandatory": f["mandatory"], "note": f["note"]}


class Library:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self._compared: dict[tuple, dict] = {}  # last few compare results; a big conformance spec takes seconds

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ import

    def add_version(self, spec: str, version: Version, parsed: ParsedSpec, source: str = "",
                    recompute: bool = True) -> int:
        self._compared.clear()
        db = self.db
        with db:
            db.execute("INSERT INTO spec(number, title) VALUES (?, ?) ON CONFLICT(number) DO UPDATE SET "
                       "title = COALESCE(excluded.title, spec.title)", (spec, parsed.title))
            old = db.execute("SELECT id FROM version WHERE spec=? AND version=?", (spec, str(version))).fetchone()
            if old:
                db.execute("DELETE FROM version WHERE id=?", (old["id"],))
            vid = db.execute(
                "INSERT INTO version(spec, version, release, sort_key, source) VALUES (?,?,?,?,?)",
                (spec, str(version), version.release, version.sort_key, source),
            ).lastrowid
            for a in parsed.assets.values():
                db.execute("INSERT OR IGNORE INTO asset VALUES (?,?,?)", (a.hash, a.mime, a.data))
            for ord_, c in enumerate(parsed.clauses):
                db.execute("INSERT INTO clause_ver(version_id, ord, key, number, title, level) VALUES (?,?,?,?,?,?)",
                           (vid, ord_, c.key, c.number, c.title, c.level))
                for bo, b in enumerate(c.blocks):
                    h = b.hash
                    if db.execute("INSERT OR IGNORE INTO blob VALUES (?,?,?)", (h, b.html, b.text)).rowcount:
                        db.execute("INSERT INTO blob_fts(hash, text) VALUES (?,?)", (h, b.text))
                    db.execute("INSERT INTO block_ver(version_id, clause_key, ord, kind, style, raw_hash) "
                               "VALUES (?,?,?,?,?,?)", (vid, c.key, bo, b.kind, b.style, h))
            for e in changehistory.parse_change_history(parsed.clauses):
                db.execute("INSERT OR IGNORE INTO cr VALUES (?,?,?,?,?,?,?,?,?)",
                           (spec, e.new_version, e.date, e.meeting, e.tdoc, e.cr, e.rev, e.cat, e.subject))
        if recompute:
            self.recompute_history(spec)
        return vid

    def _load_clauses(self, vid: int) -> list[history.InClause]:
        blocks: dict[str, list[history.InBlock]] = {}
        for r in self.db.execute(
            "SELECT b.clause_key, b.kind, b.style, b.raw_hash, x.html, x.text FROM block_ver b "
            "JOIN blob x ON x.hash = b.raw_hash WHERE b.version_id=? ORDER BY b.clause_key, b.ord", (vid,)
        ):
            blocks.setdefault(r["clause_key"], []).append(
                history.InBlock(r["kind"], r["style"], r["raw_hash"], r["html"], r["text"]))
        return [history.InClause(r["key"], r["number"], r["title"], r["level"], blocks.get(r["key"], []))
                for r in self.db.execute("SELECT * FROM clause_ver WHERE version_id=? ORDER BY ord", (vid,))]

    def recompute_history(self, spec: str) -> None:
        self._compared.clear()
        vers = self.versions(spec)
        if not vers:
            return
        results = history.compute([v["version"] for v in vers], (self._load_clauses(v["id"]) for v in vers))
        db = self.db
        with db:
            for v, res in zip(vers, results):
                vid = v["id"]
                db.execute("DELETE FROM removed_clause WHERE version_id=?", (vid,))
                for rc in res.removed_clauses:
                    db.execute("INSERT INTO removed_clause VALUES (?,?,?)", (vid, rc["key"], rc["title"]))
                for c in res.clauses:
                    db.execute("UPDATE clause_ver SET status=?, introduced=?, last_changed=?, history=?, deleted=? "
                               "WHERE version_id=? AND key=?",
                               (c.status, c.introduced, c.last_changed, json.dumps(c.history),
                                json.dumps(c.deleted), vid, c.key))
                    for bo, b in enumerate(c.blocks):
                        hist = json.dumps(b.history)
                        ah = content_hash(f"{b.html}\x00{b.introduced}\x00{b.changed}\x00{hist}")
                        db.execute("INSERT OR IGNORE INTO annot VALUES (?,?,?,?,?)",
                                   (ah, b.html, b.introduced, b.changed, hist))
                        db.execute("UPDATE block_ver SET annot_hash=? WHERE version_id=? AND clause_key=? AND ord=?",
                                   (ah, vid, c.key, bo))

    # ------------------------------------------------------------ queries

    def specs(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT number, title FROM spec ORDER BY number"):
            vs = self.db.execute("SELECT version, release FROM version WHERE spec=? ORDER BY sort_key",
                                 (r["number"],)).fetchall()
            out.append({"number": r["number"], "title": r["title"], "versions": len(vs),
                        "latest": vs[-1]["version"] if vs else None, "first": vs[0]["version"] if vs else None,
                        "releases": sorted({v["release"] for v in vs}),
                        "crs": self.db.execute("SELECT COUNT(*) FROM cr WHERE spec=?", (r["number"],)).fetchone()[0]})
        return out

    def set_spec_title(self, spec: str, title: str) -> None:
        with self.db:
            self.db.execute("UPDATE spec SET title=? WHERE number=?", (title, spec))

    def versions(self, spec: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT id, version, release FROM version WHERE spec=? ORDER BY sort_key", (spec,))]

    def version_id(self, spec: str, version: str) -> int:
        r = self.db.execute("SELECT id FROM version WHERE spec=? AND version=?", (spec, version)).fetchone()
        if not r:
            raise KeyError(f"{spec} v{version} not in library")
        return r["id"]

    def toc(self, spec: str, version: str) -> list[dict]:
        vid = self.version_id(spec, version)
        return [dict(r) for r in self.db.execute(
            "SELECT ord, key, number, title, level, status, introduced, last_changed FROM clause_ver "
            "WHERE version_id=? ORDER BY ord", (vid,))]

    def clauses(self, spec: str, version: str, start: int, count: int) -> list[dict]:
        """History-annotated clauses ``start .. start+count`` (by document order)."""
        vid = self.version_id(spec, version)
        rows = self.db.execute("SELECT * FROM clause_ver WHERE version_id=? AND ord>=? AND ord<? ORDER BY ord",
                               (vid, start, start + count)).fetchall()
        out = []
        for r in rows:
            blocks = [{
                "kind": b["kind"], "style": b["style"], "html": b["html"], "introduced": b["introduced"],
                "changed": b["changed"], "history": json.loads(b["history"]), "raw_hash": b["raw_hash"],
            } for b in self.db.execute(
                "SELECT b.kind, b.style, b.raw_hash, a.html, a.introduced, a.changed, a.history FROM block_ver b "
                "JOIN annot a ON a.hash=b.annot_hash WHERE b.version_id=? AND b.clause_key=? ORDER BY b.ord",
                (vid, r["key"]))]
            out.append({
                "ord": r["ord"], "key": r["key"], "number": r["number"], "title": r["title"], "level": r["level"],
                "status": r["status"], "introduced": r["introduced"], "last_changed": r["last_changed"],
                "history": json.loads(r["history"] or "[]"), "deleted": json.loads(r["deleted"] or "[]"),
                "blocks": blocks,
            })
        return out

    def crs(self, spec: str, version: str | None = None) -> list[dict]:
        q = ("SELECT c.*, d.status AS detail_status, d.cover FROM cr c LEFT JOIN cr_detail d "
             "ON d.spec=c.spec AND d.cr=c.cr AND d.rev=c.rev AND d.tdoc=c.tdoc WHERE c.spec=?")
        args = [spec]
        rows = []
        for r in self.db.execute(q, args):
            d = dict(r)
            cover = json.loads(d.pop("cover") or "null")
            d["detail"] = cover
            rows.append(d)
        rows.sort(key=lambda r: Version.parse(r["new_version"]).sort_key)
        lib_versions = [Version.parse(v["version"]) for v in self.versions(spec)]
        for r in rows:
            # Library version in which this CR's change first appears (versions may be skipped).
            v = Version.parse(r["new_version"])
            r["lib_version"] = next((str(x) for x in lib_versions if x >= v), None)
        if version:
            rows = [r for r in rows if r["lib_version"] == version]
        return rows

    def stats(self, spec: str) -> dict:
        """Per-version change counts and the most frequently changed clauses."""
        vers = self.versions(spec)
        crs = self.crs(spec)
        per_version = []
        for v in vers:
            counts = {r["status"]: r["n"] for r in self.db.execute(
                "SELECT status, COUNT(*) AS n FROM clause_ver WHERE version_id=? GROUP BY status", (v["id"],))}
            removed = self.db.execute("SELECT COUNT(*) FROM removed_clause WHERE version_id=?", (v["id"],)).fetchone()[0]
            cats: dict[str, int] = {}
            for c in crs:
                if c["lib_version"] == v["version"]:
                    cats[c["cat"] or "-"] = cats.get(c["cat"] or "-", 0) + 1
            per_version.append({"version": v["version"], "release": v["release"],
                                "added": counts.get("added", 0) if v is not vers[0] else 0,
                                "modified": counts.get("modified", 0), "removed": removed,
                                "clauses": sum(counts.values()), "crs": cats})
        clauses = []
        if vers:
            for r in self.db.execute("SELECT ord, key, number, title, level, introduced, history FROM clause_ver "
                                     "WHERE version_id=? ORDER BY ord", (vers[-1]["id"],)):
                hist = json.loads(r["history"] or "[]")
                changes = [v for v, t in hist if t == "modified"]
                clauses.append({"ord": r["ord"], "key": r["key"], "number": r["number"], "title": r["title"],
                                "level": r["level"], "introduced": r["introduced"], "changes": changes})
        return {"spec": spec, "latest": vers[-1]["version"] if vers else None,
                "versions": per_version, "clauses": clauses}

    def fetch_cr_details(self, spec: str, versions: set[str] | None = None, cache_dir: Path | None = None,
                         progress=None, refetch_missing: bool = False, lock=None) -> dict:
        """Download CR packs named in the Change history and store parsed cover pages."""
        import contextlib

        from . import crdocs

        guard = lock or contextlib.nullcontext()
        cache_dir = Path(cache_dir or self.path.parent / "cache")
        with guard:
            rows = self.crs(spec)
        todo = [c for c in rows if c["cr"] and c["cr"].strip("-") and c["tdoc"]
                and (not versions or c["new_version"] in versions)
                and (c["detail_status"] is None or (refetch_missing and c["detail_status"] != "ok"))]
        session = crdocs.new_session()
        stats = {"ok": 0, "missing": 0, "error": 0, "total": len(todo)}
        for i, c in enumerate(todo):
            status, cover, err = "missing", None, None
            try:
                pack = crdocs.fetch_pack(c["tdoc"], c["meeting"], cache_dir, session)
                if pack:
                    doc = crdocs.find_cr_doc(pack, spec, c["cr"], cache_dir / "tdocs" / c["tdoc"])
                    if doc:
                        cover = crdocs.parse_cover(doc).to_dict()
                        status = "ok"
            except Exception as e:  # keep going: one broken Tdoc must not stop the batch
                status, err = "error", str(e)
            stats[status] += 1
            with guard, self.db:
                self.db.execute("INSERT OR REPLACE INTO cr_detail VALUES (?,?,?,?,?,?,?)",
                                (spec, c["cr"], c["rev"], c["tdoc"], status, json.dumps(cover) if cover else None, err))
            if progress:
                progress(i + 1, len(todo), c)
        return stats

    # ------------------------------------------------------------ 38.822 features

    def import_features(self, parsed: ParsedSpec, version: str) -> int:
        from .features import parse_feature_list

        feats = parse_feature_list(parsed.clauses)
        with self.db:
            self.db.execute("DELETE FROM ue_feature")
            self.db.executemany(
                "INSERT INTO ue_feature VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(version, f.section, f.section_title, f.layer, f.release, f.index, f.group, f.components,
                  f.prerequisite, f.field_name, f.parent_ie, f.mandatory, f.note, json.dumps(f.terms)) for f in feats])
        self._feature_cache = None
        return len(feats)

    def _features(self) -> list[dict]:
        if getattr(self, "_feature_cache", None) is None:
            self._feature_cache = [dict(r) for r in self.db.execute("SELECT * FROM ue_feature")]
        return self._feature_cache

    def features_status(self) -> dict:
        r = self.db.execute("SELECT COUNT(*) AS n, MAX(source) AS source FROM ue_feature").fetchone()
        return {"count": r["n"], "source": r["source"]}

    def clause_features(self, spec: str, version: str, key: str) -> dict:
        """38.822 features related to a clause: via work items of CRs that changed it, and via parameters."""
        from .features import acronyms, match_sections, norm_term, params_in, wi_codes

        feats = self._features()
        if not feats:
            return {"available": False, "work_items": [], "parameters": []}
        vid = self.version_id(spec, version)
        row = self.db.execute("SELECT number FROM clause_ver WHERE version_id=? AND key=?", (vid, key)).fetchone()
        number = row["number"] if row else key
        cur = Version.parse(version)

        def touches(c: str) -> bool:
            return c == number or number.startswith(c + ".") or c.startswith(number + ".")

        sections = sorted({(f["section"], f["section_title"], f["layer"], f["release"]) for f in feats})
        by_section: dict[str, list[dict]] = {}
        for f in feats:
            by_section.setdefault(f["section"], []).append(f)
        wis: dict[str, dict] = {}
        for cr in self.crs(spec):
            d = cr["detail"]
            if not d or Version.parse(cr["new_version"]) > cur or not any(touches(c) for c in d["clauses"]):
                continue
            for code in wi_codes(d["wi_code"]):
                w = wis.setdefault(code, {"wi_code": code, "crs": [], "sections": [], "releases": set()})
                w["crs"].append({"cr": cr["cr"], "version": cr["new_version"], "subject": cr["subject"]})
                if d.get("release"):
                    w["releases"].add(d["release"].replace(" ", ""))
        text = " ".join(r["text"] for r in self.db.execute(
            "SELECT x.text FROM block_ver b JOIN blob x ON x.hash=b.raw_hash WHERE b.version_id=? AND b.clause_key=?",
            (vid, key)))
        title = row_title = self.db.execute("SELECT title FROM clause_ver WHERE version_id=? AND key=?",
                                            (vid, key)).fetchone()
        terms = acronyms(text + " " + (row_title["title"] if title else ""))

        def scored(f: dict) -> dict:
            shared = sorted(terms & acronyms(f"{f['grp']} {f['components']}"))
            return {**_feature_out(f), "shared": shared}

        for w in wis.values():
            # The CR's release picks the right generation (NR_pos -> Rel-16 "NR positioning", not NR_pos_enh2).
            rel_secs = [x for x in sections if x[3] in w["releases"]]
            matched = match_sections(w["wi_code"], rel_secs) or match_sections(w["wi_code"], sections)
            w["releases"] = sorted(w["releases"])
            for sec in matched:
                fl = sorted((scored(f) for f in by_section.get(sec[0], [])), key=lambda f: -len(f["shared"]))
                w["sections"].append({"section": sec[0], "title": sec[1], "layer": sec[2], "release": sec[3],
                                      "relevant": sum(1 for f in fl if f["shared"]), "features": fl})
        # parameter mentions: only specific field names (short/generic terms give noise)
        mentioned = params_in(text)
        params = []
        for f in feats:
            fields = {norm_term(x) for x in re.split(r"[\s,;/]+", f["field_name"] or "") if len(x) >= 10}
            hit = sorted(fields & mentioned)
            if hit:
                params.append({**_feature_out(f), "matched": hit})
        return {"available": True, "work_items": sorted(wis.values(), key=lambda w: w["wi_code"]),
                "parameters": params[:50]}

    def search_features(self, q: str, limit: int = 80) -> list[dict]:
        """38.822 features by index (23-1), group name, field name, work item or component text."""
        t = q.strip().lower()
        if not t:
            return []
        out = []
        for f in self._features():
            idx, grp = f["idx"].lower(), (f["grp"] or "").lower()
            score = (100 if idx == t else 80 if idx.startswith(t) else 60 if t in grp
                     else 50 if t in (f["field_name"] or "").lower() else 40 if t in (f["section_title"] or "").lower()
                     else 20 if t in (f["components"] or "").lower() else 0)
            if score:
                out.append((-score, f["release"], f["section"], f["idx"], f))
        out.sort(key=lambda x: x[:4])
        return [_feature_out(x[4]) for x in out[:limit]]

    def feature_links(self, section: str, index: str) -> dict:
        """Clauses of every downloaded spec related to one 38.822 feature.

        * ``cr``    - a CR of that spec belongs to the feature's work item and lists the clause as affected
        * ``param`` - the clause text names the feature's RRC field
        Clauses are given in each spec's latest version, related ones (shared terms) first.
        """
        from .features import acronyms, match_sections, norm_term, wi_codes

        feats = self._features()
        f = next((x for x in feats if x["section"] == section and x["idx"] == index), None)
        if f is None:
            raise KeyError(f"38.822 feature {section}:{index} not found")
        sections = sorted({(x["section"], x["section_title"], x["layer"], x["release"]) for x in feats})
        mine = (f["section"], f["section_title"], f["layer"], f["release"])
        fields = {norm_term(x) for x in re.split(r"[\s,;/]+", f["field_name"] or "") if len(x) >= 10}
        f_terms = acronyms(f"{f['grp']} {f['components']}")
        cache: dict[tuple[str, str], bool] = {}

        def in_feature_wi(code: str, release: str) -> bool:
            if (code, release) not in cache:
                rel_secs = [x for x in sections if x[3] == release]
                cache[(code, release)] = mine in (match_sections(code, rel_secs) or match_sections(code, sections))
            return cache[(code, release)]

        out = []
        for sp in self.specs():
            if not sp["latest"]:
                continue
            spec, latest = sp["number"], sp["latest"]
            vid = self.version_id(spec, latest)
            toc = {r["number"] or r["key"]: dict(r) for r in self.db.execute(
                "SELECT ord, key, number, title FROM clause_ver WHERE version_id=?", (vid,))}
            text: dict[str, list[str]] = {}
            for r in self.db.execute("SELECT b.clause_key, x.text FROM block_ver b JOIN blob x ON x.hash=b.raw_hash "
                                     "WHERE b.version_id=? ORDER BY b.clause_key, b.ord", (vid,)):
                text.setdefault(r["clause_key"], []).append(r["text"])
            hits: dict[str, dict] = {}

            def add(number: str, via: dict) -> None:
                c = toc.get(number)
                h = hits.setdefault(number, {"key": c["key"] if c else None, "number": number,
                                             "title": c["title"] if c else "(최신 버전에 없는 절)",
                                             "ord": c["ord"] if c else 10 ** 9, "via": []})
                if via not in h["via"]:
                    h["via"].append(via)

            for cr in self.crs(spec):
                d = cr["detail"]
                if not d:
                    continue
                codes = [c for c in wi_codes(d["wi_code"]) if in_feature_wi(c, (d.get("release") or "").replace(" ", ""))]
                for number in d["clauses"] if codes else []:
                    add(number, {"kind": "cr", "cr": cr["cr"], "version": cr["new_version"], "wi_code": codes[0],
                                 "subject": cr["subject"]})
            if fields:
                for c in toc.values():
                    body = " ".join(text.get(c["key"], [])).lower()
                    for name in sorted(fields):
                        if name in body:
                            add(c["number"] or c["key"], {"kind": "param", "name": name})
            for h in hits.values():
                body = " ".join(text.get(h["key"], [])) + " " + h["title"] if h["key"] else ""
                h["shared"] = sorted(f_terms & acronyms(body))
            clauses = sorted(hits.values(), key=lambda h: (-len(h["shared"]) - 5 * any(v["kind"] == "param" for v in h["via"]),
                                                            h["ord"]))
            if clauses:
                out.append({"spec": spec, "title": sp["title"], "version": latest, "clauses": clauses})
        return {"feature": _feature_out(f), "specs": out}

    # ------------------------------------------------------------ settings / annotation packs

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO setting VALUES (?,?)", (key, value))

    def notes(self, spec: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT clause_key, bookmark, text, version, updated FROM note WHERE spec=? ORDER BY updated DESC", (spec,))]

    def save_note(self, spec: str, key: str, bookmark: bool | None = None, text: str | None = None,
                  version: str | None = None) -> dict | None:
        import datetime as dt

        cur = self.db.execute("SELECT bookmark, text FROM note WHERE spec=? AND clause_key=?", (spec, key)).fetchone()
        bm = int(bookmark) if bookmark is not None else (cur["bookmark"] if cur else 0)
        tx = text if text is not None else (cur["text"] if cur else "")
        with self.db:
            if not bm and not tx.strip():
                self.db.execute("DELETE FROM note WHERE spec=? AND clause_key=?", (spec, key))
                return None
            now = dt.datetime.now().isoformat(timespec="seconds")
            self.db.execute("INSERT OR REPLACE INTO note VALUES (?,?,?,?,?,?)", (spec, key, bm, tx, version, now))
        return {"clause_key": key, "bookmark": bm, "text": tx, "version": version, "updated": now}

    def _annotation_rows(self, spec: str, version: str) -> dict[str, dict]:
        return {r["raw_hash"]: json.loads(r["data"]) for r in self.db.execute(
            "SELECT raw_hash, data FROM annotation WHERE spec=? AND version=?", (spec, version))}

    def import_pack(self, pack: dict) -> int:
        """Replace a version's server annotations; summaries made locally survive where the server has none."""
        if not str(pack.get("format", "")).startswith("specview-annotations/"):
            raise ValueError("not a specview annotation pack")
        old = self._annotation_rows(pack["spec"], pack["version"])
        rows = {h: d for h, d in old.items() if d.get("local") and d.get("intent")}
        for ch in pack["changes"]:
            mine = rows.get(ch["raw_hash"])
            rows[ch["raw_hash"]] = {**ch, "generated": pack.get("generated"),
                                    **({"intent": mine["intent"], "local": True} if mine and not ch.get("intent") else {})}
        with self.db:
            self.db.execute("DELETE FROM annotation WHERE spec=? AND version=?", (pack["spec"], pack["version"]))
            self.db.executemany("INSERT INTO annotation VALUES (?,?,?,?,?)", [
                (pack["spec"], pack["version"], h, d.get("clause"), json.dumps(d, ensure_ascii=False))
                for h, d in rows.items()])
        return len(pack["changes"])

    def merge_pack(self, pack: dict) -> int:
        """Add the summaries of a locally built pack, keeping whatever else a row already has (agreements ...)."""
        old = self._annotation_rows(pack["spec"], pack["version"])
        n = 0
        with self.db:
            for ch in pack["changes"]:
                if not ch.get("intent"):  # not requested / failed: nothing worth storing
                    continue
                prev = old.get(ch["raw_hash"], {})
                data = {**prev, **ch, "agreements": ch.get("agreements") or prev.get("agreements", []),
                        "generated": pack.get("generated"), "local": True}
                data.pop("intent_error", None)
                self.db.execute("INSERT OR REPLACE INTO annotation VALUES (?,?,?,?,?)", (
                    pack["spec"], pack["version"], ch["raw_hash"], ch.get("clause"), json.dumps(data, ensure_ascii=False)))
                n += 1
        return n

    def intent_hashes(self, spec: str, version: str) -> set[str]:
        """Blocks of a version that already have an intent summary."""
        rows = self.db.execute("SELECT raw_hash, data FROM annotation WHERE spec=? AND version=?", (spec, version))
        return {r["raw_hash"] for r in rows if json.loads(r["data"]).get("intent")}

    def clause_annotations(self, spec: str, version: str, key: str) -> list[dict]:
        """Server annotations for the blocks of a clause (any version that introduced their current text)."""
        vid = self.version_id(spec, version)
        hashes = [r["raw_hash"] for r in self.db.execute(
            "SELECT raw_hash FROM block_ver WHERE version_id=? AND clause_key=? ORDER BY ord", (vid, key))]
        if not hashes:
            return []
        q = ",".join("?" * len(hashes))
        rows = self.db.execute(f"SELECT version, raw_hash, data FROM annotation WHERE spec=? AND raw_hash IN ({q})",
                               (spec, *hashes)).fetchall()
        out = [{**json.loads(r["data"]), "version": r["version"]} for r in rows]
        out.sort(key=lambda a: (hashes.index(a["raw_hash"]), Version.parse(a["version"]).sort_key))
        return out

    def cr_analysis(self, spec: str, cr: str, rev: str, tdoc: str) -> dict | None:
        r = self.db.execute("SELECT data FROM cr_analysis WHERE spec=? AND cr=? AND rev=? AND tdoc=?",
                            (spec, cr, rev, tdoc)).fetchone()
        return json.loads(r["data"]) if r else None

    def cr_analyses(self, spec: str) -> list[dict]:
        return [json.loads(r["data"]) for r in self.db.execute("SELECT data FROM cr_analysis WHERE spec=?", (spec,))]

    def save_cr_analysis(self, spec: str, cr: str, rev: str, tdoc: str, data: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO cr_analysis VALUES (?,?,?,?,?)",
                            (spec, cr, rev, tdoc, json.dumps(data, ensure_ascii=False)))

    # ------------------------------------------------------------ work items

    def _wi_rows(self) -> Iterator[tuple[str, dict]]:
        for sp in self.specs():
            for c in self.crs(sp["number"]):
                if c["detail"] and c["cr"]:
                    yield sp["number"], c

    def work_items(self) -> list[dict]:
        """Work item codes on the CR covers of every downloaded spec, with how many CRs in which specs."""
        out: dict[str, dict] = {}
        for spec, c in self._wi_rows():
            for w in wi_codes(c["detail"]):
                e = out.setdefault(w, {"wi": w, "crs": 0, "specs": {}})
                e["crs"] += 1
                e["specs"][spec] = e["specs"].get(spec, 0) + 1
        done = {r["wi"] for r in self.db.execute("SELECT wi FROM wi_summary")}
        return sorted(({**e, "summary": e["wi"] in done} for e in out.values()), key=lambda e: -e["crs"])

    def work_item(self, wi: str) -> dict:
        """Every CR of one work item across the downloaded specs, oldest first, with its stored CR analysis."""
        crs = []
        for spec, c in self._wi_rows():
            if wi not in wi_codes(c["detail"]):
                continue
            d, a = c["detail"], self.cr_analysis(spec, c["cr"], c["rev"], c["tdoc"])
            crs.append({"spec": spec, "cr": c["cr"], "rev": c["rev"], "tdoc": c["tdoc"], "meeting": c["meeting"],
                        "date": c["date"], "new_version": c["new_version"], "lib_version": c["lib_version"],
                        "cat": c["cat"], "title": d.get("title") or c["subject"], "release": d.get("release", ""),
                        "clauses": d.get("clauses", []), "clauses_raw": d.get("clauses_raw", ""),
                        "reason": d.get("reason", ""), "summary": d.get("summary", ""),
                        "analysis": {k: a.get(k) for k in ("summary", "background", "decision", "impact")} if a else None})
        crs.sort(key=lambda c: (c["date"] or "", Version.parse(c["new_version"]), c["spec"], c["cr"]))
        r = self.db.execute("SELECT data FROM wi_summary WHERE wi=?", (wi,)).fetchone()
        return {"wi": wi, "crs": crs, "summary": json.loads(r["data"]) if r else None}

    def save_wi_summary(self, wi: str, data: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO wi_summary VALUES (?,?)", (wi, json.dumps(data, ensure_ascii=False)))

    # ------------------------------------------------------------ chat

    def chats(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT id, title, updated FROM chat ORDER BY updated DESC")]

    def chat(self, chat_id: str) -> dict:
        r = self.db.execute("SELECT * FROM chat WHERE id=?", (chat_id,)).fetchone()
        if r is None:
            raise KeyError(f"no chat {chat_id}")
        return {"id": r["id"], "title": r["title"], "updated": r["updated"], "messages": json.loads(r["data"])}

    def save_chat(self, chat_id: str, title: str, messages: list[dict], updated: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO chat VALUES (?,?,?,?)",
                            (chat_id, title, updated, json.dumps(messages, ensure_ascii=False)))

    def delete_chat(self, chat_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM chat WHERE id=?", (chat_id,))

    def block_lineage(self, spec: str, version: str, key: str, raw_hash: str) -> list[dict]:
        """Every text a paragraph had, oldest first: the version that wrote it, the change ("base", "added",
        "modified", "moved"), the clause it sat in, its HTML and the diff from the step before, with the CRs of
        that version that name the clause and any summary stored for that step."""
        from .diff import strip_tags

        vid = self.version_id(spec, version)
        row = self.db.execute(
            "SELECT b.clause_key, b.raw_hash, a.introduced, a.history FROM block_ver b JOIN annot a "
            "ON a.hash=b.annot_hash WHERE b.version_id=? AND b.clause_key=? AND b.raw_hash=?",
            (vid, key, raw_hash)).fetchone()
        if row is None:
            raise KeyError(f"no such paragraph in {spec} v{version} {key}")
        hist = json.loads(row["history"])
        intro = row["introduced"]
        vids = {v["version"]: v["id"] for v in self.versions(spec)}
        # a step is the block of that version with the same origin and the history up to that step
        wanted = ([] if hist and hist[0][0] == intro and hist[0][1] == "added" else [(intro, "base", 0)])
        wanted += [(h[0], h[1], i + 1) for i, h in enumerate(hist)]
        steps, near = [], key
        for ver, change, n in wanted:
            cands = self.db.execute(
                "SELECT b.clause_key, b.raw_hash, x.html, x.text FROM block_ver b JOIN annot a ON a.hash=b.annot_hash "
                "JOIN blob x ON x.hash=b.raw_hash WHERE b.version_id=? AND a.introduced=? AND a.history=?",
                (vids[ver], intro, json.dumps(hist[:n]))).fetchall()
            if not cands:
                continue
            pick = next((c for c in cands if c["clause_key"] == near), cands[0])
            near = pick["clause_key"]
            steps.append({"version": ver, "change": change, "clause_key": pick["clause_key"],
                          "raw_hash": pick["raw_hash"], "html": pick["html"]})
        if not steps or steps[-1]["raw_hash"] != raw_hash:  # the shown text itself, whatever was matched before
            steps.append({"version": version, "change": hist[-1][1] if hist else "base", "clause_key": key,
                          "raw_hash": raw_hash, "html": self.db.execute(
                              "SELECT html FROM blob WHERE hash=?", (raw_hash,)).fetchone()["html"]})
        numbers = {}
        crs = self.crs(spec)
        for i, st in enumerate(steps):
            if st["clause_key"] not in numbers:
                r = self.db.execute("SELECT number FROM clause_ver WHERE version_id=? AND key=?",
                                    (vids[st["version"]], st["clause_key"])).fetchone()
                numbers[st["clause_key"]] = (r["number"] if r else "") or st["clause_key"]
            num = numbers[st["clause_key"]]
            st["clause"] = num
            prev = steps[i - 1]["html"] if i else None
            st["diff"] = render_diff(prev, st["html"]) if prev is not None else st["html"]
            st["text"] = " ".join(strip_tags(st["html"]).split())
            st["crs"] = [{"cr": c["cr"], "rev": c["rev"], "tdoc": c["tdoc"], "subject": c["subject"],
                          "cat": c["cat"], "meeting": c["meeting"]}
                         for c in crs if c["lib_version"] == st["version"] and c["detail"]
                         and any(x == num or num.startswith(x + ".") or x.startswith(num + ".")
                                 for x in c["detail"]["clauses"])]
            a = self.db.execute("SELECT data FROM annotation WHERE spec=? AND version=? AND raw_hash=?",
                                (spec, st["version"], st["raw_hash"])).fetchone()
            st["annotation"] = json.loads(a["data"]) if a else None
        return steps

    def asset(self, h: str) -> tuple[str, bytes] | None:
        r = self.db.execute("SELECT mime, data FROM asset WHERE hash=?", (h,)).fetchone()
        return (r["mime"], r["data"]) if r else None

    def search(self, spec: str, version: str, query: str, limit: int = 200) -> list[dict]:
        """Full-text search inside one version; returns matching clauses in document order."""
        vid = self.version_id(spec, version)
        fts_q = " ".join(f'"{w}"' for w in query.replace('"', " ").split())
        if not fts_q:
            return []
        rows = self.db.execute(
            "SELECT c.ord, c.key, c.number, c.title, b.ord AS block, snippet(blob_fts, 1, '[[', ']]', '…', 12) AS snip "
            "FROM blob_fts f JOIN block_ver b ON b.raw_hash = f.hash AND b.version_id = ? "
            "JOIN clause_ver c ON c.version_id = b.version_id AND c.key = b.clause_key "
            "WHERE blob_fts MATCH ? ORDER BY c.ord, b.ord LIMIT ?", (vid, fts_q, limit)).fetchall()
        title_rows = self.db.execute(
            "SELECT ord, key, number, title FROM clause_ver WHERE version_id=? AND (title LIKE ? OR number = ?) "
            "ORDER BY ord", (vid, f"%{query}%", query.strip())).fetchall()
        hits = [{"ord": r["ord"], "key": r["key"], "number": r["number"], "title": r["title"], "block": None,
                 "snippet": r["title"]} for r in title_rows]
        hits += [{"ord": r["ord"], "key": r["key"], "number": r["number"], "title": r["title"], "block": r["block"],
                  "snippet": r["snip"]} for r in rows]
        hits.sort(key=lambda h: (h["ord"], -1 if h["block"] is None else h["block"]))
        return hits

    def phrase_history(self, spec: str, query: str, per_event: int = 60, max_rows: int = 20000) -> dict:
        """Where an exact phrase occurs in every version: how many paragraphs and clauses, the first version that
        has it, and per version the clauses where it appeared or disappeared (with a snippet)."""
        words = query.replace('"', " ").split()
        vers = self.versions(spec)
        out = {"query": query, "versions": [], "events": [], "first": None, "latest_clauses": 0, "truncated": False}
        if not words or not vers:
            return out
        rows = self.db.execute(
            "SELECT b.version_id, b.clause_key, c.number, c.title, c.ord, b.ord AS block, "
            "snippet(blob_fts, 1, '[[', ']]', '…', 12) AS snip FROM blob_fts f "
            "JOIN block_ver b ON b.raw_hash = f.hash JOIN version v ON v.id = b.version_id AND v.spec = ? "
            "JOIN clause_ver c ON c.version_id = b.version_id AND c.key = b.clause_key "
            "WHERE blob_fts MATCH ? LIMIT ?", (spec, '"' + " ".join(words) + '"', max_rows + 1)).fetchall()
        if len(rows) > max_rows:  # a very common phrase: counts would be partial, so say so
            out["truncated"] = True
            rows = rows[:max_rows]
        rows.sort(key=lambda r: (r["ord"], r["block"]))
        by_vid: dict[int, dict[str, dict]] = {}
        for r in rows:
            cl = by_vid.setdefault(r["version_id"], {}).setdefault(r["clause_key"], {
                "key": r["clause_key"], "number": r["number"], "title": r["title"], "ord": r["ord"],
                "block": r["block"], "snippet": r["snip"], "count": 0})
            cl["count"] += 1
        prev: dict[str, dict] = {}
        for v in vers:
            cur = by_vid.get(v["id"], {})
            out["versions"].append({"version": v["version"], "paragraphs": sum(c["count"] for c in cur.values()),
                                    "clauses": len(cur)})
            added = [c for k, c in cur.items() if k not in prev]
            removed = [{k2: c[k2] for k2 in ("key", "number", "title")} for k, c in prev.items() if k not in cur]
            if added or removed:
                out["events"].append({"version": v["version"], "added": added[:per_event], "removed": removed[:per_event],
                                      "more": max(0, len(added) - per_event) + max(0, len(removed) - per_event)})
            if cur and out["first"] is None:
                out["first"] = v["version"]
            prev = cur
        out["latest_clauses"] = len(prev)
        return out

    # ------------------------------------------------------------ compare

    def _raw(self, vid: int) -> Iterator[tuple[dict, list[dict]]]:
        for c in self.db.execute("SELECT * FROM clause_ver WHERE version_id=? ORDER BY ord", (vid,)).fetchall():
            blocks = [dict(b) for b in self.db.execute(
                "SELECT b.kind, b.style, b.raw_hash, x.html, x.text FROM block_ver b JOIN blob x ON x.hash=b.raw_hash "
                "WHERE b.version_id=? AND b.clause_key=? ORDER BY b.ord", (vid, c["key"]))]
            yield dict(c), blocks

    def compare(self, spec: str, old: str, new: str, changed_only: bool = True) -> dict:
        """Clause-by-clause diff between two arbitrary versions (the CRs are read fresh, the diff is cached)."""
        # data_version changes when another connection (the CLI, an LLM job) commits: a re-import there can
        # reuse the version's rowid, so the ids alone do not prove the cached diff is current
        k = (spec, self.version_id(spec, old), self.version_id(spec, new), changed_only,
             self.db.execute("PRAGMA data_version").fetchone()[0])
        if k not in self._compared:
            if len(self._compared) >= 4:
                self._compared.pop(next(iter(self._compared)))
            self._compared[k] = self._compare(spec, old, new, changed_only)
        return self._compared[k] | {
            "crs": [r for r in self.crs(spec) if Version.parse(old) < Version.parse(r["new_version"]) <= Version.parse(new)]}

    def _compare(self, spec: str, old: str, new: str, changed_only: bool) -> dict:
        from .diff import align

        a = {c["key"]: (c, bl) for c, bl in self._raw(self.version_id(spec, old))}
        b = list(self._raw(self.version_id(spec, new)))
        out, seen = [], set()
        for c, blocks in b:
            prev = a.get(c["key"])
            seen.add(c["key"])
            rows, status = [], "unchanged"
            if prev is None:
                status = "added"
                rows = [{"op": "insert", "kind": x["kind"], "style": x["style"], "old": None, "new": x["html"],
                         "diff": f"<ins>{x['html']}</ins>"} for x in blocks]
            else:
                pc, pblocks = prev
                for oi, ni in align(pblocks, blocks, key=lambda x: x["raw_hash"], text=lambda x: x["text"]):
                    o = pblocks[oi] if oi is not None else None
                    n = blocks[ni] if ni is not None else None
                    if o and n and o["raw_hash"] == n["raw_hash"]:
                        rows.append({"op": "equal", "kind": n["kind"], "style": n["style"], "old": o["html"],
                                     "new": n["html"], "diff": n["html"]})
                        continue
                    status = "modified"
                    op = "replace" if o and n else ("insert" if n else "delete")
                    diff = render_diff(o["html"], n["html"]) if op == "replace" else (
                        f"<ins>{n['html']}</ins>" if n else f"<del>{o['html']}</del>")
                    ref = n or o
                    rows.append({"op": op, "kind": ref["kind"], "style": ref["style"], "old": o and o["html"],
                                 "new": n and n["html"], "diff": diff})
                if pc["title"] != c["title"]:
                    status = "modified"
            if changed_only and status == "unchanged":
                continue
            out.append({"key": c["key"], "number": c["number"], "title": c["title"], "level": c["level"],
                        "old_title": prev[0]["title"] if prev else None, "status": status, "blocks": rows})
        removed = [{"key": k, "number": c["number"], "title": c["title"], "level": c["level"], "status": "removed",
                    "old_title": c["title"],
                    "blocks": [{"op": "delete", "kind": x["kind"], "style": x["style"], "old": x["html"], "new": None,
                                "diff": f"<del>{x['html']}</del>"} for x in bl]}
                   for k, (c, bl) in a.items() if k not in seen]
        _mark_moves(out + removed)
        return {"spec": spec, "old": old, "new": new, "clauses": out, "removed": removed}
