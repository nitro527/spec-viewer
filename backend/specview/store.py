"""SQLite library: parsed versions, deduplicated content, history results and FTS index.

Block content is stored once per distinct hash, so dozens of versions of a spec
cost little more than one: most clauses do not change between versions.
"""
from __future__ import annotations

import json
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
CREATE VIRTUAL TABLE IF NOT EXISTS blob_fts USING fts5(hash UNINDEXED, text, tokenize = 'unicode61');
"""


class Library:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ import

    def add_version(self, spec: str, version: Version, parsed: ParsedSpec, source: str = "",
                    recompute: bool = True) -> int:
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
        return [dict(r) for r in self.db.execute(
            "SELECT s.number, s.title, COUNT(v.id) AS versions, MAX(v.version) AS latest FROM spec s "
            "LEFT JOIN version v ON v.spec = s.number GROUP BY s.number ORDER BY s.number")]

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
                "changed": b["changed"], "history": json.loads(b["history"]),
            } for b in self.db.execute(
                "SELECT b.kind, b.style, a.html, a.introduced, a.changed, a.history FROM block_ver b "
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
        q, args = "SELECT * FROM cr WHERE spec=?", [spec]
        if version:
            q += " AND new_version=?"
            args.append(version)
        rows = [dict(r) for r in self.db.execute(q, args)]
        rows.sort(key=lambda r: Version.parse(r["new_version"]).sort_key)
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
                if c["new_version"] == v["version"]:
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

    # ------------------------------------------------------------ compare

    def _raw(self, vid: int) -> Iterator[tuple[dict, list[dict]]]:
        for c in self.db.execute("SELECT * FROM clause_ver WHERE version_id=? ORDER BY ord", (vid,)).fetchall():
            blocks = [dict(b) for b in self.db.execute(
                "SELECT b.kind, b.style, b.raw_hash, x.html, x.text FROM block_ver b JOIN blob x ON x.hash=b.raw_hash "
                "WHERE b.version_id=? AND b.clause_key=? ORDER BY b.ord", (vid, c["key"]))]
            yield dict(c), blocks

    def compare(self, spec: str, old: str, new: str, changed_only: bool = True) -> dict:
        """Clause-by-clause diff between two arbitrary versions."""
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
        return {"spec": spec, "old": old, "new": new, "clauses": out, "removed": removed,
                "crs": [r for r in self.crs(spec) if Version.parse(old) < Version.parse(r["new_version"]) <= Version.parse(new)]}
