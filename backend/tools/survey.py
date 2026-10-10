"""Survey a whole 3GPP series against specview's ingestion: python tools/survey.py 38 --out survey-38

Phase A (every version, cheap): read each archive zip's directory over HTTP range requests and classify it
  (single .docx / legacy .doc / split parts / no Word document / unreadable).
Phase B (latest two approved versions per spec): download, convert, store, compute history; record clause and
  block counts, title detection, unconverted images, and how much changed between the two versions.
Phase C (TS only): parse the cover pages of the two newest CRs in the Change history.

Results go to <out>/zips.jsonl, <out>/specs.jsonl and a summary in <out>/summary.md. Re-running skips specs
already in specs.jsonl, so an interrupted run can continue.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from specview import download  # noqa: E402
from specview.versions import Version  # noqa: E402


class HttpFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests, enough for zipfile to read the central directory."""

    def __init__(self, session, url: str):
        self.s, self.url, self.pos = session, url, 0
        r = self._get({"Range": "bytes=0-0"})
        self.size = int(r.headers["Content-Range"].split("/")[1])

    def _get(self, headers):
        for attempt in range(4):
            try:
                r = self.s.get(self.url, headers=headers, timeout=60)
                if r.status_code in (200, 206):
                    return r
            except Exception:
                pass
            time.sleep(2 ** attempt)
        raise OSError(f"range request failed: {self.url}")

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def read(self, n=-1):
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        if end <= self.pos:
            return b""
        data = self._get({"Range": f"bytes={self.pos}-{end - 1}"}).content
        self.pos += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


def list_specs(series: str) -> list[str]:
    s = download.new_session()
    r = s.get(f"{download.ARCHIVE}/{series}_series/", timeout=60)
    r.raise_for_status()
    return sorted(set(re.findall(rf"{series}_series/({series}\.\d{{3}}(?:-\d+)?)\b", r.text)))


def classify_zip(session, rv) -> dict:
    out = {"spec": rv.spec, "version": str(rv.version), "file": rv.filename}
    try:
        with zipfile.ZipFile(HttpFile(session, rv.url)) as z:
            names = z.namelist()
            word = [n for n in names if n.lower().endswith((".docx", ".doc"))]
            parts = download._word_parts(z)
    except Exception as e:
        return {**out, "kind": "unreadable", "error": str(e)[:200]}
    if not word:
        kind = "no-word"
    elif len(parts) > 1:
        kind = "split"
    elif parts[0].lower().endswith(".doc"):
        kind = "doc"
    else:
        kind = "docx"
    return {**out, "kind": kind, "word": word, "parts": parts, "others": [n for n in names if n not in word][:10]}


def survey_spec(spec: str, work: str, crs: bool) -> dict:
    """Phase B and C for one spec, in its own library (run in a worker process)."""
    from specview.parse_docx import parse_docx
    from specview.store import Library

    res: dict = {"spec": spec, "errors": []}
    home = Path(tempfile.mkdtemp(dir=work, prefix=spec + "-"))
    t0 = time.time()
    try:
        remote = download.select(download.list_remote(spec))  # approved versions
        res["approved_versions"] = len(remote)
        pick = remote[-2:]
        res["versions"] = [str(r.version) for r in pick]
        if not pick:
            res["errors"].append("no approved version")
            return res
        lib = Library(home / "library.sqlite")
        for rv in pick:
            try:
                paths = download.fetch(rv, home / "cache")
                parsed = parse_docx(paths)
                lib.add_version(spec, rv.version, parsed, source=rv.url, recompute=False)
                res.setdefault("parts", {})[str(rv.version)] = len(paths)
            except download.LegacyDocError as e:
                res["errors"].append(f"v{rv.version}: legacy .doc ({e})")
            except Exception as e:
                res["errors"].append(f"v{rv.version}: {type(e).__name__}: {str(e)[:300]}")
        vers = [v["version"] for v in lib.versions(spec)]
        if not vers:
            return res
        lib.recompute_history(spec)
        latest = vers[-1]
        toc = lib.toc(spec, latest)
        clauses = lib.clauses(spec, latest, 0, 100000)
        blocks = [b for c in clauses for b in c["blocks"]]
        numbered = [t for t in toc if t["number"]]
        res.update({
            "title": lib.specs()[0]["title"],
            "clauses": len(toc), "numbered": len(numbered),
            "first_numbered": numbered[0]["number"] if numbered else None,
            "has_scope": any(t["number"] == "1" for t in toc),
            "empty_clauses": sum(1 for c in clauses if not c["blocks"]),
            "blocks": len(blocks),
            "tables": sum(1 for b in blocks if b["kind"] == "table"),
            "equations": sum(1 for b in blocks if b["kind"] == "eq" or 'class="math' in b["html"]),
            "images": sum(b["html"].count("<img") for b in blocks),
            "raw_images": sum(len(re.findall(r'<img[^>]+src="[^"]*\.(?:emf|wmf)', b["html"], re.I)) for b in blocks),
            "missing_images": sum(len(re.findall(r'<img(?![^>]*src="/api/asset/)', b["html"])) for b in blocks),
            "dup_keys": sum(1 for t in toc if "~" in t["key"]),
        })
        if len(vers) == 2:
            cmp = lib.compare(spec, vers[0], vers[1])
            changed = len(cmp["clauses"]) + len(cmp["removed"])
            res["changed_clauses"] = changed
            res["changed_ratio"] = round(changed / max(len(toc), 1), 3)
        res["cr_rows"] = len(lib.crs(spec))
        if crs and spec.split(".")[1][0] != "8" and spec.split(".")[1][0] != "9":  # TS only
            rows = [c for c in lib.crs(spec) if c["cr"] and c["tdoc"] and Version.parse(c["new_version"]).major >= 3][-2:]
            if rows:
                st = lib.fetch_cr_details(spec, versions={c["new_version"] for c in rows}, cache_dir=home / "cache")
                details = [c for c in lib.crs(spec) if c["detail"]]
                res["cr_covers"] = {**st, "with_clauses": sum(1 for c in details if c["detail"]["clauses"]),
                                    "with_wi": sum(1 for c in details if c["detail"]["wi_code"])}
    except Exception as e:
        res["errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
        res["trace"] = traceback.format_exc()[-1500:]
    finally:
        res["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(home, ignore_errors=True)
    return res


def summarize(out: Path, series: str) -> str:
    zips = [json.loads(line) for line in (out / "zips.jsonl").read_text().splitlines()] if (out / "zips.jsonl").exists() else []
    specs = [json.loads(line) for line in (out / "specs.jsonl").read_text().splitlines()] if (out / "specs.jsonl").exists() else []
    lines = [f"# Series {series} survey", ""]
    if zips:
        from collections import Counter
        approved = [z for z in zips if z["kind"] == "unlistable" or Version.parse(z["version"]).major >= 3]
        kinds = Counter(z["kind"] for z in approved)
        lines += [f"## Archive zips (approved versions): {len(approved)}", ""]
        lines += [f"- {k}: {n}" for k, n in kinds.most_common()]
        for k in ("split", "no-word", "unreadable", "unlistable"):
            ex = [z for z in approved if z["kind"] == k]
            if ex:
                specs_k = sorted({z["spec"] for z in ex})
                lines.append(f"- {k} specs ({len(specs_k)}): {', '.join(specs_k[:40])}{' …' if len(specs_k) > 40 else ''}")
                lines.append(f"  e.g. {ex[0]['file']}: {ex[0].get('word') or ex[0].get('others') or ex[0].get('error')}")
        doc_specs = sorted({z["spec"] for z in approved if z["kind"] == "doc"})
        lines.append(f"- specs with legacy .doc versions ({len(doc_specs)}): {', '.join(doc_specs[:40])}")
        lines.append("")
    if specs:
        bad = [s for s in specs if s["errors"]]
        lines += [f"## Conversion (latest two versions): {len(specs)} specs, {len(bad)} with errors", ""]
        for s in bad:
            lines.append(f"- **{s['spec']}**: {'; '.join(s['errors'])[:400]}")
        checks = {
            "no clauses parsed": [s for s in specs if s.get("clauses") == 0],
            "few numbered clauses (<5)": [s for s in specs if "numbered" in s and s["numbered"] < 5],
            "no clause 1 (Scope)": [s for s in specs if s.get("has_scope") is False],
            "title not detected": [s for s in specs if "clauses" in s and not s.get("title")],
            "EMF/WMF images left unconverted": [s for s in specs if s.get("raw_images")],
            "images without asset": [s for s in specs if s.get("missing_images")],
            "duplicate clause keys": [s for s in specs if s.get("dup_keys")],
            "unstable diff (>60% of clauses changed)": [s for s in specs if s.get("changed_ratio", 0) > 0.6],
        }
        lines.append("")
        for name, ss in checks.items():
            lines.append(f"- {name}: {len(ss)}" + (f" — {', '.join(x['spec'] for x in ss[:30])}" if ss else ""))
        cov = [s["cr_covers"] for s in specs if s.get("cr_covers")]
        if cov:
            tot = {k: sum(c[k] for c in cov) for k in ("ok", "missing", "error", "total", "with_clauses", "with_wi")}
            lines += ["", f"## CR covers (sample): {tot}"]
            errs = [(s["spec"], s["cr_covers"]) for s in specs if s.get("cr_covers") and (s["cr_covers"]["missing"] or s["cr_covers"]["error"])]
            lines.append(f"- specs with missing/error covers: {', '.join(x[0] for x in errs[:40])}")
        slow = sorted(specs, key=lambda s: -s.get("seconds", 0))[:5]
        lines += ["", "Slowest: " + ", ".join(f"{s['spec']} {s.get('seconds')}s" for s in slow)]
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text, encoding="utf-8")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("series", help="e.g. 38 or 36")
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", choices=["a", "b", "all"], default="all")
    ap.add_argument("--specs", nargs="*", help="limit to these specs")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--no-crs", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    specs = args.specs or list_specs(args.series)
    print(f"{len(specs)} specs", flush=True)

    if args.phase in ("a", "all") and not (out / "zips.jsonl").exists():
        s = download.new_session()
        rows = []
        with ThreadPoolExecutor(6) as ex:
            remotes = []
            for spec in specs:
                try:
                    remotes += download.list_remote(spec, s)
                except Exception as e:  # keep going; the spec shows up as unlistable
                    rows.append({"spec": spec, "version": "0.0.0", "file": "", "kind": "unlistable", "error": str(e)[:200]})
            print(f"phase A: {len(remotes)} zips", flush=True)
            futs = [ex.submit(classify_zip, s, rv) for rv in remotes]
            for i, f in enumerate(as_completed(futs), 1):
                rows.append(f.result())
                if i % 200 == 0:
                    print(f"  {i}/{len(remotes)}", flush=True)
        (out / "zips.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    if args.phase in ("b", "all"):
        done = set()
        if (out / "specs.jsonl").exists():
            done = {json.loads(line)["spec"] for line in (out / "specs.jsonl").read_text().splitlines()}
        todo = [s for s in specs if s not in done]
        print(f"phase B/C: {len(todo)} specs", flush=True)
        work = out / "work"
        work.mkdir(exist_ok=True)
        with ProcessPoolExecutor(args.workers) as ex, open(out / "specs.jsonl", "a") as fh:
            futs = {ex.submit(survey_spec, s, str(work), not args.no_crs): s for s in todo}
            for i, f in enumerate(as_completed(futs), 1):
                r = f.result()
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"  [{i}/{len(todo)}] {r['spec']} {r.get('seconds')}s {'ERR ' + r['errors'][0][:80] if r['errors'] else ''}", flush=True)
    print(summarize(out, args.series))


if __name__ == "__main__":
    main()
