"""Local HTTP API consumed by the viewer UI (served on 127.0.0.1 only)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import download, intent
from .parse_docx import parse_docx
from .store import Library
from .versions import Version


log = logging.getLogger("specview")


def default_home() -> Path:
    return Path(os.environ.get("SPECVIEW_HOME", Path.home() / ".specview"))


class LLMConfig(BaseModel):
    provider: str | None = None  # gemini | openai (any OpenAI-compatible server: vLLM, Ollama ...)
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    clear_key: bool = False


class IntentRequest(BaseModel):
    spec: str
    version: str
    clause_key: str | None = None  # one clause (blocks shown in this version), else the whole version
    limit: int | None = 20
    skip_uncovered: bool = True
    force: bool = False  # summarize again even where a summary exists (e.g. after better evidence is available)
    # single paragraph texts, e.g. the steps of a paragraph's history: {"version", "clause_key", "raw_hash"}
    targets: list[dict] | None = None


class CRAnalysisRequest(BaseModel):
    spec: str
    cr: str
    tdoc: str
    force: bool = False  # analyse again (e.g. after a better model is set up)


class WISummaryRequest(BaseModel):
    wi: str
    force: bool = False


class FullAnalysisRequest(BaseModel):
    specs: list[str] | None = None  # None: every downloaded spec
    skip_uncovered: bool = True  # changes no CR explains (front matter, years) are left out
    cr_analyses: bool = True  # then the CRs no summarised paragraph pulled in
    wi_summaries: bool = True  # then every work item


class ChatRequest(BaseModel):
    question: str
    chat_id: str | None = None  # None: a new conversation
    context: dict | None = None  # {"spec", "version", "clause", "text"} when asked from a paragraph


class ReleaseNotesRequest(BaseModel):
    spec: str
    old: str
    new: str
    force: bool = False


class FetchRequest(BaseModel):
    specs: list[str]
    releases: list[int] | None = None
    latest_per_release: bool = False
    with_crs: bool = True
    crs_only: bool = False
    only_newer: bool = False  # just versions newer than the library's latest (the "새 버전 받기" button)


FULL_RETRY_WAIT = 60  # seconds before the n-th retry waits n times this, when the LLM server fails every request


class LLMErrorText(Exception):
    """A failure that only left its message behind (build_pack records errors as text)."""


def _llm_timeout() -> float:
    """Seconds to wait for one LLM reply: SPECVIEW_LLM_TIMEOUT, else 300 (a local model on a long prompt can
    take minutes; the connection itself is given up after 20 s)."""
    try:
        return max(30.0, float(os.environ.get("SPECVIEW_LLM_TIMEOUT", "300")))
    except ValueError:
        return 300.0


def _convert_workers() -> int:
    from .parse_docx import convert_workers

    return convert_workers()


def _short_error(e: Exception) -> str:
    """'Read timed out' instead of the whole HTTPSConnectionPool(host=..., port=443): ... chain."""
    text = str(e)
    for key in ("Read timed out", "Connection reset", "Max retries exceeded", "Name or service not known",
                "timed out", "404", "403", "500", "502", "503"):
        if key in text:
            return key
    return text[:80]


def _full_counts(c: dict) -> str:
    out = f"문단 요약 {c['intents']}개 · CR 분석 {c['cr_analyses']}개 · WI 요약 {c['wi_summaries']}개"
    return out + (f" · 실패 {c['failed']}개 (다음 실행 때 다시 시도)" if c["failed"] else "")


def _keep_awake():
    """Keep Windows from sleeping while the full analysis runs; returns the function that lets it sleep again."""
    import sys

    if sys.platform != "win32":
        return lambda: None
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
    except Exception:
        return lambda: None
    return lambda: ctypes.windll.kernel32.SetThreadExecutionState(es_continuous)


def create_app(home: Path | None = None, static_dir: Path | None = None) -> FastAPI:
    home = Path(home or default_home())
    lib = Library(home / "library.sqlite")
    try:
        lib.protect_secrets()
    except Exception:  # never keep the app from starting over this
        pass
    lock = threading.RLock()  # one SQLite connection shared by the request threads; re-entrant for nested helpers
    jobs: dict[str, dict] = {}
    jobs_lock = threading.Lock()

    def _jobs() -> list[dict]:
        with jobs_lock:
            return list(jobs.values())

    def _put(job: dict) -> None:
        """Register a job; AI jobs one at a time (the check above is repeated here so two clicks can't both pass)."""
        with jobs_lock:
            if job.get("kind") == "intent" and any(j.get("kind") == "intent" and j["state"] == "running"
                                                   for j in jobs.values()):
                raise HTTPException(409, "AI 작업이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
            if job.get("chat_id") and any(j.get("chat_id") == job["chat_id"] and j["state"] == "running"
                                          for j in jobs.values()):
                raise HTTPException(409, "이 대화의 답을 아직 만드는 중입니다")
            finished = [k for k, j in jobs.items() if j["state"] != "running"]
            for k in finished[:-50]:  # keep the last few finished jobs for the UI, not every one since start
                del jobs[k]
            jobs[job["id"]] = job

    def _repair():  # the app was closed while a download was between storing versions and computing history
        try:
            reader = Library(home / "library.sqlite")  # the scans read every block: not under the shared lock
            try:
                todo = set(reader.specs_missing_history())
                rekey = reader.needs_rekey()
            finally:
                reader.close()
            if rekey:
                with lock:
                    todo |= set(lib.normalize_keys())
            for spec in sorted(todo):
                lib.recompute_history(spec, lock=lock)
        except Exception:
            log.exception("startup repair")

    threading.Thread(target=_repair, daemon=True).start()
    app = FastAPI(title="specview")
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    # Local API: only for this machine. A web page that re-binds its DNS name to 127.0.0.1 must not reach it.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    # Pages served here may show text from documents anyone can write (CRs): no inline script, no javascript:
    # links, nothing loaded from elsewhere. KaTeX needs inline styles and its bundled fonts only.
    CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
           "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
           "frame-ancestors 'none'")

    @app.middleware("http")
    async def guard(request, call_next):
        # Another web site open in a browser can send requests to this port. Reading answers is already blocked
        # (no CORS); changes are refused unless they come from this app's own pages.
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:  # "null": a sandboxed frame, refused too
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Slow read-only queries (a compare of distant versions, the citation map, statistics) run on their own
    # connection: under the shared lock they held up every other request for up to a second. WAL lets them
    # read while the main connection writes; their caches check PRAGMA data_version / version ids.
    heavy = Library(home / "library.sqlite")
    heavy_lock = threading.Lock()

    def _read(fn_name: str, *a):
        try:
            with heavy_lock:
                return getattr(heavy, fn_name)(*a)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    def _get(fn, *a):
        try:
            with lock:
                return fn(*a)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:  # a malformed version or clause number in the URL
            raise HTTPException(400, str(e))

    @app.get("/api/specs")
    def specs():
        return _get(lib.specs)

    @app.get("/api/specs/{spec}/versions")
    def versions(spec: str):
        return _get(lib.versions, spec)

    @app.get("/api/specs/{spec}/{version}/toc")
    def toc(spec: str, version: str):
        return _get(lib.toc, spec, version)

    @app.get("/api/specs/{spec}/{version}/clauses")
    def clauses(spec: str, version: str, start: int = 0, count: int = 20):
        return _get(lib.clauses, spec, version, start, min(count, 200))

    @app.get("/api/specs/{spec}/phrase-history")
    def phrase_history(spec: str, q: str):
        return _read("phrase_history", spec, q)

    @app.get("/api/specs/{spec}/{version}/search")
    def search(spec: str, version: str, q: str):
        return _get(lib.search, spec, version, q)

    @app.get("/api/specs/{spec}/crs")
    def crs(spec: str, version: str | None = None):
        return _get(lib.crs, spec, version)

    @app.get("/api/specs/{spec}/stats")
    def stats(spec: str):
        return _read("stats", spec)

    @app.get("/api/specs/{spec}/{version}/clause/{key}/features")
    def clause_features(spec: str, version: str, key: str):
        return _get(lib.clause_features, spec, version, key)

    @app.get("/api/features")
    def features_status():
        return _get(lib.features_status)

    @app.get("/api/features/search")
    def search_features(q: str):
        return _get(lib.search_features, q)

    @app.get("/api/features/{section}/{index}/links")
    def feature_links(section: str, index: str):
        return _get(lib.feature_links, section, index)

    def _run_features(job_id: str):
        job = jobs[job_id]
        try:
            job["message"] = "38.822: 최신 버전 확인"
            rv = download.select(download.list_remote("38.822"))[-1]
            job["message"] = f"38.822 v{rv.version}: 다운로드"
            path = download.fetch(rv, home / "cache")
            job["message"] = f"38.822 v{rv.version}: 변환"
            parsed = parse_docx(path)
            with lock:
                n = lib.import_features(parsed, str(rv.version))
            job["state"], job["message"], job["done"], job["total"] = "done", f"{n} features", 1, 1
        except Exception as e:
            job["state"], job["message"] = "error", str(e)
            log.exception("job %s (%s) failed", job.get("id"), job.get("kind") or job.get("specs"))

    @app.post("/api/fetch-features")
    def fetch_features():
        job_id = uuid.uuid4().hex[:8]
        _put({"id": job_id, "state": "running", "done": 0, "total": 1, "message": "starting",
                        "specs": ["38.822"]})
        threading.Thread(target=_run_features, args=(job_id,), daemon=True).start()
        return jobs[job_id]

    # ---- keeping up: new versions on 3gpp.org, and bookmarked clauses that changed ----

    REMOTE_TTL = 12 * 3600  # 3GPP publishes new versions after each plenary, a few times a year

    @app.get("/api/specs/{spec}/referenced-by")
    def referenced_by(spec: str, clause: str):
        """Paragraphs that cite a clause, in this spec and the other downloaded ones (latest versions)."""
        return _read("referenced_by", spec, clause)

    @app.get("/api/citations")
    def citations():
        """Citation map of the downloaded specs (latest versions)."""
        return _read("citation_map")

    @app.get("/api/citations/pairs")
    def citation_pairs(frm: str, to: str):
        return _read("citation_pairs", frm, to)

    @app.get("/api/updates")
    def updates(refresh: bool = False):
        """Versions on 3gpp.org newer than the newest one downloaded, per downloaded spec. The archive listings
        are cached for half a day (``refresh`` asks again)."""
        import time
        from concurrent.futures import ThreadPoolExecutor

        with lock:
            mine = {x["number"]: [v["version"] for v in lib.versions(x["number"])] for x in lib.specs()}
            cache = json.loads(lib.get_setting("remote_versions") or "{}")
        now = time.time()
        stale = [sp for sp in mine if refresh or now - cache.get(sp, {}).get("t", 0) > REMOTE_TTL]
        errors = []

        def listing(sp):
            try:
                return [str(rv.version) for rv in download.select(download.list_remote(sp))]
            except requests.RequestException as e:
                return e

        if stale:
            with ThreadPoolExecutor(4) as ex:
                for sp, res in zip(stale, ex.map(listing, stale)):
                    if isinstance(res, Exception):
                        errors.append(f"{sp}: {_short_error(res)}")
                    else:
                        cache[sp] = {"t": now, "v": res}
            with lock:
                lib.set_setting("remote_versions", json.dumps(cache))
        out = []
        for sp, vs in mine.items():
            if sp not in cache or not vs:
                continue
            latest = max(download.Version.parse(v) for v in vs)
            new = [v for v in cache[sp]["v"] if download.Version.parse(v) > latest]
            if new:
                out.append({"spec": sp, "latest": str(latest), "new": new})
        checked = min((cache[sp]["t"] for sp in mine if sp in cache), default=None)
        return {"specs": out, "errors": errors,
                "checked": dt.datetime.fromtimestamp(checked).isoformat(timespec="minutes") if checked else None}

    @app.get("/api/watch")
    def watch():
        """Bookmarked or annotated clauses changed since the version they were marked in."""
        return _get(lib.watch_report)

    @app.post("/api/watch/seen")
    def watch_seen(body: dict):
        with lock:
            lib.mark_watch_seen(str(body.get("spec", "")), body.get("clause_key") or None)
        return _get(lib.watch_report)

    @app.get("/api/specs/{spec}/notes")
    def notes(spec: str):
        return _get(lib.notes, spec)

    @app.put("/api/specs/{spec}/notes/{key}")
    def save_note(spec: str, key: str, body: dict):
        with lock:
            return lib.save_note(spec, key, body.get("bookmark"), body.get("text"), body.get("version"))

    @app.get("/api/settings")
    def get_settings():
        with lock:
            return {"annotation_url": lib.get_setting("annotation_url", os.environ.get("SPECVIEW_ANNOTATION_URL", ""))}

    @app.put("/api/settings")
    def put_settings(body: dict):
        if "annotation_url" in body:
            with lock:
                lib.set_setting("annotation_url", str(body["annotation_url"]).rstrip("/"))
        return get_settings()

    @app.post("/api/annotations/sync/{spec}")
    def sync_annotations(spec: str):
        base = get_settings()["annotation_url"]
        if not base:
            raise HTTPException(400, "annotation server URL is not set")
        s = download.new_session()
        try:
            index = s.get(f"{base}/v1/packs", timeout=30).json()
            with lock:
                have = {v["version"] for v in lib.versions(spec)}
            n = 0
            for item in index:
                if item["spec"] == spec and item["version"] in have:
                    pack = s.get(base + item["url"], timeout=120).json()
                    with lock:
                        lib.import_pack(pack)
                    n += 1
        except Exception as e:
            raise HTTPException(502, f"annotation server: {e}")
        return {"packs": n}

    @app.get("/api/specs/{spec}/{version}/clause/{key}/lineage")
    def clause_lineage(spec: str, version: str, key: str, hash: str):
        """Every text one paragraph had, with diffs, CRs and stored summaries per step."""
        return _get(lib.block_lineage, spec, version, key, hash)

    @app.get("/api/specs/{spec}/{version}/clause/{key}/annotations")
    def clause_annotations(spec: str, version: str, key: str):
        return _get(lib.clause_annotations, spec, version, key)

    @app.get("/api/specs/{spec}/compare")
    def compare(spec: str, old: str, new: str, changed_only: bool = True):
        return _read("compare", spec, old, new, changed_only)

    @app.get("/api/asset/{h}")
    def asset(h: str):
        a = _get(lib.asset, h)
        if not a:
            raise HTTPException(404)
        return Response(a[1], media_type=a[0], headers={"Cache-Control": "max-age=31536000, immutable"})

    # ---- LLM change-intent summaries (the user's own API key) ----

    def _provider_settings(p: str) -> dict:
        """Address, model and key are kept per provider so a key never goes to another provider's server."""
        url, model, key_env = intent.PRESETS.get(p, ("", "", None))
        with lock:
            legacy = lib.get_setting("llm_provider") == p  # settings saved before they were per provider
            vals = {k: lib.get_setting(f"llm_{p}_{k}") or (lib.get_setting(f"llm_{k}") if legacy else None)
                    for k in ("base_url", "model", "api_key")}
        get = vals.get
        saved_key = get("api_key") or ""
        env_key = os.environ.get(key_env, "") if key_env else os.environ.get("LLM_API_KEY", "")
        return {"base_url": get("base_url") or url, "model": get("model") or model, "api_key": saved_key or env_key,
                "key_source": "saved" if saved_key else "env" if env_key else None}

    def _current_provider() -> str:
        with lock:
            return lib.get_setting("llm_provider") or "gemini"

    def _public(st: dict) -> dict:
        st = dict(st)
        key = st.pop("api_key")
        return {**st, "has_key": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else ""}

    def _llm(cfg: LLMConfig | None = None) -> intent.OpenAICompatIntentModel:
        st = _provider_settings((cfg and cfg.provider) or _current_provider())
        if cfg:  # unsaved values from the form, so "test" works before "save"
            new_url = (cfg.base_url or "").strip().rstrip("/")
            if new_url and new_url != st["base_url"].rstrip("/") and not (cfg.api_key or "").strip():
                st["api_key"] = ""  # the saved key belongs to the saved server; never send it to a typed-in one
            st.update({k: v.strip() for k, v in (("base_url", cfg.base_url), ("model", cfg.model),
                                                 ("api_key", cfg.api_key)) if v and v.strip()})
        if not st["base_url"]:
            raise HTTPException(400, "LLM 서버 주소를 입력하세요")
        if not st["model"]:
            raise HTTPException(400, "모델 이름을 입력하세요")
        return intent.OpenAICompatIntentModel(st["base_url"], st["model"], st["api_key"] or None, timeout=_llm_timeout())

    @app.get("/api/llm")
    def llm_settings():
        p = _current_provider()
        return {"provider": p, **_public(_provider_settings(p)),
                "providers": {q: _public(_provider_settings(q)) for q in ("gemini", "openai")},
                "presets": {k: {"base_url": u, "model": m} for k, (u, m, _) in intent.PRESETS.items()}}

    @app.put("/api/llm")
    def save_llm(cfg: LLMConfig):
        p = cfg.provider or _current_provider()
        with lock:
            legacy = lib.get_setting("llm_provider")
            for k in ("base_url", "model", "api_key"):  # settings saved before they were per provider
                v = lib.get_setting(f"llm_{k}")
                if v and legacy and not lib.get_setting(f"llm_{legacy}_{k}"):
                    lib.set_setting(f"llm_{legacy}_{k}", v)
                if v:
                    lib.set_setting(f"llm_{k}", "")
            lib.set_setting("llm_provider", p)
            for k in ("base_url", "model"):
                v = getattr(cfg, k)
                if v is not None:
                    lib.set_setting(f"llm_{p}_{k}", v.strip().rstrip("/") if k == "base_url" else v.strip())
            if cfg.api_key and cfg.api_key.strip():
                lib.set_setting(f"llm_{p}_api_key", cfg.api_key.strip())
            elif cfg.clear_key:
                lib.set_setting(f"llm_{p}_api_key", "")
        return llm_settings()

    @app.post("/api/llm/test")
    def test_llm(cfg: LLMConfig):
        try:
            return _llm(cfg).ping()
        except intent.LLMError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/llm/models")
    def llm_models(cfg: LLMConfig):
        try:
            return {"models": _llm(cfg).list_models()}
        except intent.LLMError as e:
            raise HTTPException(400, str(e))

    def _cr_analyzer(reader: Library, spec: str, model: intent.OpenAICompatIntentModel, job: dict,
                     force: set[tuple] = frozenset()):
        """Analyses of CR rows, stored ones as they are, missing ones made now (one LLM request each)."""
        from . import cranalysis
        from .pack import AgreementFinder

        finder = AgreementFinder(home / "cache")

        failed: set[tuple] = set()  # CRs that failed in this job: not asked again for every paragraph

        def run(rows: list[dict], strict: bool = False) -> list[dict]:
            """``strict`` raises a failure; otherwise a CR that fails is left out (and remembered)."""
            out = []
            for row in rows:
                key = (row["cr"], row["rev"], row["tdoc"])
                have = None if key in force else reader.cr_analysis(spec, *key)
                if have is None and key in failed and not strict:
                    continue
                if have is None:
                    try:
                        have = make(row, key)
                    except intent.QuotaExceeded:
                        raise
                    except Exception:
                        failed.add(key)
                        if strict:
                            raise
                        continue
                if have:
                    out.append(have)
            return out

        def make(row: dict, key: tuple) -> dict:
            job["message"] = f"{spec} CR{row['cr']} 분석 중 (CR 문서·회의 자료 모으는 중)"
            ev = cranalysis.evidence(reader, spec, row, home / "cache", finder)
            job["message"] = f"{spec} CR{row['cr']} 분석 중 (AI 요청)"
            res = model.analyze_cr(ev)
            have = {**res, "spec": spec, "cr": row["cr"], "rev": row["rev"], "tdoc": row["tdoc"],
                    "meeting": row["meeting"], "title": row["detail"].get("title") or row["subject"],
                    "version": row["lib_version"], "evidence": cranalysis.brief(ev),
                    "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
            with lock:
                lib.save_cr_analysis(spec, *key, have)
            job["cr_made"] = job.get("cr_made", 0) + 1
            return have

        return run

    def _run_intent(job_id: str, req: IntentRequest, model: intent.OpenAICompatIntentModel):
        from .pack import build_pack

        job = jobs[job_id]
        reader = Library(home / "library.sqlite")  # own connection: LLM calls are slow, don't hold the lock
        try:
            analyzer = _cr_analyzer(reader, req.spec, model, job)
            if req.targets:
                plan = [(t["version"], t["clause_key"], {t["raw_hash"]}) for t in req.targets]
            elif req.clause_key:  # blocks of the clause as shown, each under the version that introduced it
                clause = next((c for c in reader.clauses(req.spec, req.version, 0, 100000)
                               if c["key"] == req.clause_key), None)
                blocks = clause["blocks"] if clause else []
                versions = sorted({b["changed"] for b in blocks if b["changed"]}, key=lambda v: Version.parse(v).sort_key)
                current = {b["raw_hash"] for b in blocks}  # not texts the clause replaced again later
                plan = [(v, req.clause_key, current) for v in versions]
            else:
                plan = [(req.version, None, None)]
            made, errors, stopped, uncovered, capped, asked = 0, [], None, 0, 0, 0
            for v, clause_key, only in plan:
                def prog(i, n):
                    job["done"], job["total"] = i, n
                    job["message"] = f"{req.spec} v{v}: AI 요약 {i}/{n}"
                left = None if req.limit is None else max(req.limit - asked, 0)  # the limit is per job
                pack = build_pack(reader, req.spec, v, model, progress=prog, limit=left,
                                  clause_key=clause_key, skip_uncovered=req.skip_uncovered,
                                  skip_hashes=set() if req.force else reader.intent_hashes(req.spec, v),
                                  only_hashes=only, cr_analyzer=analyzer)
                with lock:
                    made += lib.merge_pack(pack)
                errors += [c["intent_error"] for c in pack["changes"] if c.get("intent_error")]
                asked += pack["generator"]["requested"]
                uncovered += pack["generator"]["skipped_uncovered"]
                capped += pack["generator"]["over_limit"]
                if stopped := pack["generator"].get("stopped"):
                    break
            msg = f"AI 요약 {made}개 만듦 ({model.name})"
            if job.get("cr_made"):
                msg += f" · CR 분석 {job['cr_made']}개 새로 만듦"
            if stopped:
                msg += f" · 중단: {stopped}"
            elif errors:
                msg += f" · 실패 {len(errors)}개: {errors[0]}"
            elif not plan:
                msg = "이 절에는 변경된 문단이 없습니다"
            if capped:
                msg += f" · 최대 요청 수에 걸려 {capped}개 남음 (다시 시작하면 이어서 함)"
            if uncovered:
                msg += f" · CR 이 연결되지 않은 변경 {uncovered}개 건너뜀"
                if not made and not errors and not stopped:
                    msg += " ('CR 이 연결되지 않은 변경 건너뛰기'를 끄면 요청합니다)"
            job["state"] = "error" if (stopped or errors) and not made else "done"
            job["message"] = msg
        except Exception as e:
            job["state"], job["message"] = "error", str(e)
            log.exception("job %s (%s) failed", job.get("id"), job.get("kind") or job.get("specs"))
        finally:
            reader.close()

    @app.post("/api/intent")
    def make_intents(req: IntentRequest):
        model = _llm()
        if not model.api_key and "googleapis.com" in model.base_url:
            raise HTTPException(400, "Gemini API 키를 먼저 저장하세요")
        if any(j.get("kind") == "intent" and j["state"] == "running" for j in _jobs()):
            raise HTTPException(409, "AI 요약이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
        job_id = uuid.uuid4().hex[:8]
        _put({"id": job_id, "state": "running", "done": 0, "total": 0, "message": "AI 요약 준비",
                        "specs": [req.spec], "kind": "intent"})
        threading.Thread(target=_run_intent, args=(job_id, req, model), daemon=True).start()
        return jobs[job_id]

    @app.get("/api/specs/{spec}/cr-analyses")
    def cr_analyses(spec: str):
        """Stored CR analyses of a spec (the panel shows them for every paragraph their CR touched)."""
        with lock:
            return lib.cr_analyses(spec)

    def _run_cr_analysis(job_id: str, req: CRAnalysisRequest, row: dict, model: intent.OpenAICompatIntentModel):
        job = jobs[job_id]
        reader = Library(home / "library.sqlite")
        try:
            got = _cr_analyzer(reader, req.spec, model, job, force={(row["cr"], row["rev"], row["tdoc"])})([row], strict=True)
            job["state"] = "done" if got else "error"
            job["message"] = f"{req.spec} CR{req.cr} 분석 완료 ({model.name})" if got else "AI 응답이 비어 있습니다"
        except intent.LLMError as e:
            job["state"], job["message"] = "error", str(e)
        except Exception as e:
            job["state"], job["message"] = "error", f"CR 분석 실패: {e}"
            log.exception("job %s (%s) failed", job.get("id"), job.get("kind") or job.get("specs"))
        finally:
            reader.close()

    @app.post("/api/cr-analysis")
    def make_cr_analysis(req: CRAnalysisRequest):
        """Analyse one CR of a downloaded spec's Change history (cover page needed) in a background job."""
        with lock:
            row = next((c for c in lib.crs(req.spec) if c["cr"] == req.cr and c["tdoc"] == req.tdoc), None)
        if row is None or not row["detail"]:
            raise HTTPException(404, f"CR{req.cr} ({req.tdoc}) 의 표지가 없습니다. 스펙을 CR 정보와 함께 받으세요")
        model = _llm()
        if not model.api_key and "googleapis.com" in model.base_url:
            raise HTTPException(400, "Gemini API 키를 먼저 저장하세요")
        if any(j.get("kind") == "intent" and j["state"] == "running" for j in _jobs()):
            raise HTTPException(409, "AI 작업이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
        job_id = uuid.uuid4().hex[:8]
        _put({"id": job_id, "state": "running", "done": 0, "total": 1, "message": f"CR{req.cr} 분석 준비",
                        "specs": [req.spec], "kind": "intent"})
        threading.Thread(target=_run_cr_analysis, args=(job_id, req, row, model), daemon=True).start()
        return jobs[job_id]

    def _summarize_wi(model: intent.OpenAICompatIntentModel, wi: dict) -> bool:
        res = model.summarize_wi(wi)
        if not res:
            return False
        data = {**res, "wi": wi["wi"], "crs": len(wi["crs"]), "analysed": sum(1 for c in wi["crs"] if c["analysis"]),
                "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        with lock:
            lib.save_wi_summary(wi["wi"], data)
        return True

    @app.get("/api/work-items")
    def work_items():
        """Work item codes of the CR covers of every downloaded spec."""
        return _get(lib.work_items)

    @app.get("/api/work-items/{wi}")
    def work_item(wi: str):
        """Every CR of one work item across the downloaded specs, with the stored WI summary."""
        return _get(lib.work_item, wi)

    @app.post("/api/wi-summary")
    def make_wi_summary(req: WISummaryRequest):
        """Summarise a work item from its CRs (covers and stored CR analyses) in a background job."""
        with lock:
            wi = lib.work_item(req.wi)
        if not wi["crs"]:
            raise HTTPException(404, f"{req.wi} 의 CR 표지가 없습니다")
        model = _llm()
        if not model.api_key and "googleapis.com" in model.base_url:
            raise HTTPException(400, "Gemini API 키를 먼저 저장하세요")
        if any(j.get("kind") == "intent" and j["state"] == "running" for j in _jobs()):
            raise HTTPException(409, "AI 작업이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
        job_id = uuid.uuid4().hex[:8]
        _put({"id": job_id, "state": "running", "done": 0, "total": 1,
                        "message": f"{req.wi} 요약 중 (CR {len(wi['crs'])}개, AI 요청)",
                        "specs": sorted({c["spec"] for c in wi["crs"]}), "kind": "intent"})

        def run():
            job = jobs[job_id]
            try:
                if not _summarize_wi(model, wi):
                    job["state"], job["message"] = "error", "AI 응답이 비어 있습니다"
                    return
                job["state"], job["message"] = "done", f"{req.wi} 요약 완료 ({model.name})"
            except intent.LLMError as e:
                job["state"], job["message"] = "error", str(e)
            except Exception as e:
                job["state"], job["message"] = "error", f"WI 요약 실패: {e}"
                log.exception("job %s (%s) failed", job.get("id"), job.get("kind") or job.get("specs"))

        threading.Thread(target=run, daemon=True).start()
        return jobs[job_id]

    # ---- downloads (background jobs) ----

    @app.get("/api/meeting-doc")
    def meeting_doc(folder: str, tdoc: str | None = None):
        """A WG meeting report (no tdoc) or meeting Tdoc rendered for the document viewer window."""
        from . import minutes

        try:
            return minutes.document_html(folder, tdoc, home / "cache")
        except minutes.Unreachable as e:
            raise HTTPException(503, f"3gpp.org 에 연결할 수 없습니다. 잠시 후 다시 열어 보세요 ({e})")
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/cr-doc")
    def cr_doc(spec: str, cr: str, tdoc: str):
        """A CR of the spec's Change history rendered for the document viewer window."""
        from . import crdocs

        with lock:
            row = next((c for c in lib.crs(spec) if c["cr"] == cr and c["tdoc"] == tdoc), None)
        if row is None:
            raise HTTPException(404, f"CR{cr} ({tdoc}) is not in the Change history of {spec}")
        try:
            return crdocs.cr_document_html(spec, cr, tdoc, row["meeting"], home / "cache")
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/catalog/{series}")
    def spec_catalog(series: str, refresh: bool = False):
        """Specs of a series with titles for the download picker. Fetched once, then kept on disk until the
        user asks for a refresh."""
        import json as _json

        if series not in ("36", "37", "38"):
            raise HTTPException(404, "unknown series")
        f = home / "catalog" / f"{series}.json"
        if f.exists() and not refresh:
            return _json.loads(f.read_text(encoding="utf-8"))
        try:
            rows = download.catalog(series)
        except Exception as e:
            raise HTTPException(502, f"3GPP 스펙 목록을 받을 수 없습니다: {e}")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows

    @app.get("/api/remote/{spec}")
    def remote(spec: str):
        try:
            return [{"version": str(r.version), "release": r.version.release, "url": r.url}
                    for r in download.list_remote(spec)]
        except Exception as e:  # network / policy errors surface to the UI
            raise HTTPException(502, f"cannot reach 3GPP archive: {e}")

    def _official_title(spec: str) -> str | None:
        """Title from 3GPP's series list (saved for the download picker; fetched once if missing)."""
        import json as _json

        f = home / "catalog" / f"{spec.split('.')[0]}.json"
        try:
            if not f.exists():
                rows = download.catalog(spec.split(".")[0])
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(_json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            rows = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
        return next((r["title"] for r in rows if r["number"] == spec), None)

    def _fetch_and_parse(todo: list, spec: str, job: dict):
        """Download and convert versions a few at a time (pandoc takes ~10 s per version and runs in its own
        process); yields (version, parsed, error) as each finishes. Only a small window is in flight, since a
        parsed version holds the whole document."""
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        def work(rv):
            return parse_docx(download.fetch(rv, home / "cache"))

        workers = _convert_workers()
        rest = iter(todo)
        with ThreadPoolExecutor(workers) as ex:
            pending = {}

            def submit_next():
                rv = next(rest, None)
                if rv is not None:
                    pending[ex.submit(work, rv)] = rv

            for _ in range(workers):
                submit_next()
            while pending:
                job["message"] = (f"{spec}: 받는 중·변환 중 v{', v'.join(str(rv.version) for rv in pending.values())}"
                                  + (f" (동시 {workers}개)" if workers > 1 else ""))
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for f in done:
                    rv = pending.pop(f)
                    try:
                        yield rv, f.result(), None
                    except Exception as e:  # noqa: BLE001 - reported per version, the others go on
                        log.warning("%s v%s: %s", spec, rv.version, e, exc_info=not isinstance(e, requests.RequestException))
                        yield rv, None, e
                    submit_next()

    def _run_fetch(job_id: str, req: FetchRequest):
        import requests

        job = jobs[job_id]
        skipped = []  # .doc-only versions with no converter on this PC
        drafts_only = []  # specs with no approved (3.x and later) version yet
        failed = []  # versions (or whole specs) 3gpp.org did not deliver even after retries
        have_count = 0
        pending: set[str] = set()  # specs with new versions whose history is not computed yet
        try:
            for spec in req.specs:
                if req.crs_only:
                    def prog(i, n, c):
                        job["done"], job["total"] = i, n
                        job["message"] = f"{spec}: CR{c['cr']} ({c['tdoc']})"
                    lib.fetch_cr_details(spec, cache_dir=home / "cache", progress=prog, refetch_missing=True, lock=lock)
                    continue
                job["message"] = f"{spec}: listing versions"
                try:
                    remote_versions = download.select(download.list_remote(spec),
                                                      set(req.releases) if req.releases else None,
                                                      req.latest_per_release)
                except requests.RequestException as e:  # one spec's archive page: the others can still come
                    failed.append(f"{spec} (버전 목록: {_short_error(e)})")
                    continue
                if req.only_newer:
                    with lock:
                        mine = [download.Version.parse(v["version"]) for v in lib.versions(spec)]
                    if mine:
                        remote_versions = [rv for rv in remote_versions if rv.version > max(mine)]
                        if not remote_versions:
                            continue
                if not remote_versions:
                    drafts_only.append(spec)
                    continue
                with lock:
                    have = {v["version"] for v in lib.versions(spec)}
                job["total"] += len(remote_versions)
                todo = [rv for rv in remote_versions if str(rv.version) not in have]  # an interrupted download
                have_count += len(remote_versions) - len(todo)                       # goes on from here
                job["done"] += len(remote_versions) - len(todo)
                for rv, parsed, err in _fetch_and_parse(todo, spec, job):
                    if isinstance(err, download.LegacyDocError):
                        skipped.append(f"{spec} v{rv.version}")
                    elif err is not None:  # 3gpp.org timed out even after retries, or the conversion failed
                        failed.append(f"{spec} v{rv.version} ({_short_error(err)})")
                    else:
                        with lock:
                            lib.add_version(spec, rv.version, parsed, source=rv.url, recompute=False)
                        pending.add(spec)
                    job["done"] += 1
                with lock:
                    if not lib.versions(spec):
                        continue
                title = _official_title(spec)  # cover pages of recent versions often defeat title detection
                if title:
                    with lock:
                        lib.set_spec_title(spec, title)
                job["message"] = f"{spec}: computing history"
                lib.recompute_history(spec, lock=lock)
                pending.discard(spec)
                if req.with_crs:
                    def prog(i, n, c):
                        job["message"] = f"{spec}: CR 표지 {i}/{n} ({c['tdoc']})"
                    try:
                        lib.fetch_cr_details(spec, cache_dir=home / "cache", progress=prog, lock=lock)
                    except requests.RequestException as e:
                        failed.append(f"{spec} CR 표지 ({_short_error(e)})")
            job["state"], job["message"] = "done", "finished"
            notes = []
            if have_count:
                notes.append(f"이미 받은 {have_count}개 버전은 건너뜀")
            if failed:
                notes.append(f"받지 못함 {len(failed)}개: {', '.join(failed[:8])}{' …' if len(failed) > 8 else ''}"
                             " (3gpp.org 응답 없음 — 다시 다운로드하면 받은 것은 건너뛰고 이것만 다시 받습니다)")
            if skipped:
                notes.append(f"건너뜀: {', '.join(skipped)} (옛 .doc 형식, LibreOffice 또는 Word 가 있어야 변환됨)")
            if drafts_only:
                notes.append(f"받을 버전 없음: {', '.join(drafts_only)} (승인 전 초안만 있거나 고른 릴리즈에 버전이 없음)")
            if notes:
                job["message"] = "finished — " + " · ".join(notes)
        except Exception as e:
            job["state"], job["message"] = "error", str(e)
            log.exception("job %s (%s) failed", job.get("id"), job.get("kind") or job.get("specs"))
        finally:  # versions stored before an error still get their history
            for spec in pending:
                try:
                    lib.recompute_history(spec, lock=lock)
                except Exception:
                    pass

    @app.post("/api/fetch")
    def fetch(req: FetchRequest):
        job_id = uuid.uuid4().hex[:8]
        _put({"id": job_id, "state": "running", "done": 0, "total": 0, "message": "starting",
                        "specs": req.specs})
        threading.Thread(target=_run_fetch, args=(job_id, req), daemon=True).start()
        return jobs[job_id]

    @app.get("/api/jobs")
    def list_jobs():
        with jobs_lock:  # workers add keys to their job while it runs: copy while nothing else changes the dicts
            return [_public_job(j) for j in list(jobs.values())]

    def _public_job(j: dict) -> dict:
        for _ in range(5):
            try:
                return {k: v for k, v in dict(j).items() if k != "cancel"}
            except RuntimeError:  # a worker thread added a key mid-copy: try again
                continue
        return {k: v for k, v in dict(j).items() if k != "cancel"}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        with jobs_lock:
            j = jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "no such job")
        return _public_job(j)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        """Ask a long job (the full AI analysis) to stop after the request in flight; what is done stays saved."""
        if job_id not in jobs:
            raise HTTPException(404, "no such job")
        jobs[job_id]["cancel"] = True
        if jobs[job_id]["state"] == "running":
            jobs[job_id]["message"] += " · 중지 요청됨 (지금 요청이 끝나면 멈춤)"
        return {k: v for k, v in jobs[job_id].items() if k != "cancel"}

    @app.get("/api/analysis-status")
    def analysis_status():
        """How far the AI analysis got, per spec and version (the board under ④)."""
        return _read("analysis_status")

    # ---- release notes between two versions ----

    @app.get("/api/specs/{spec}/release-notes")
    def release_notes(spec: str, old: str, new: str):
        with lock:
            data = lib.release_input(spec, old, new)
            note = lib.release_note(spec, old, new)
        return {"note": note, "crs": len(data["crs"]), "analysed": sum(1 for c in data["crs"] if c["analysis"]),
                "changed_clauses": len(data["changed_clauses"])}

    @app.post("/api/release-notes")
    def make_release_notes(req: ReleaseNotesRequest):
        """AI release notes of the CRs between two versions, in a background job."""
        with lock:
            data = lib.release_input(req.spec, req.old, req.new)
        if not data["crs"]:
            raise HTTPException(404, f"v{req.old} → v{req.new} 사이에 Change history 의 CR 이 없습니다")
        model = _llm()
        if not model.api_key and "googleapis.com" in model.base_url:
            raise HTTPException(400, "Gemini API 키를 먼저 저장하세요")
        if any(j.get("kind") == "intent" and j["state"] == "running" for j in _jobs()):
            raise HTTPException(409, "AI 작업이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
        job_id = uuid.uuid4().hex[:8]
        _put({"id": job_id, "state": "running", "done": 0, "total": 1, "kind": "intent", "specs": [req.spec],
                        "message": f"{req.spec} v{req.old} → v{req.new} 릴리즈 노트 (CR {len(data['crs'])}개, AI 요청)"})

        def run():
            job = jobs[job_id]
            try:
                res = model.release_notes(data)
                if not res:
                    job["state"], job["message"] = "error", "AI 응답이 비어 있습니다"
                    return
                note = {**res, "crs": len(data["crs"]), "analysed": sum(1 for c in data["crs"] if c["analysis"]),
                        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
                with lock:
                    lib.save_release_note(req.spec, req.old, req.new, note)
                job["state"], job["message"] = "done", f"릴리즈 노트 완료 ({model.name})"
            except intent.LLMError as e:
                job["state"], job["message"] = "error", str(e)
            except Exception as e:
                job["state"], job["message"] = "error", f"릴리즈 노트 실패: {e}"
                log.exception("job %s (%s) failed", job.get("id"), job.get("kind") or job.get("specs"))

        threading.Thread(target=run, daemon=True).start()
        return jobs[job_id]

    # ---- AI chat over the downloaded material ----

    @app.get("/api/chats")
    def chats():
        return _get(lib.chats)

    @app.get("/api/chats/{chat_id}")
    def chat(chat_id: str):
        return _get(lib.chat, chat_id)

    @app.delete("/api/chats/{chat_id}")
    def delete_chat(chat_id: str):
        with lock:
            lib.delete_chat(chat_id)
        return {"ok": True}

    @app.post("/api/chat")
    def ask(req: ChatRequest):
        """Answer a question in a background job (the model looks things up first, which takes a while)."""
        if not req.question.strip():
            raise HTTPException(400, "질문을 입력하세요")
        model = _llm()
        if not model.api_key and "googleapis.com" in model.base_url:
            raise HTTPException(400, "Gemini API 키를 먼저 저장하세요")
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with lock:
            if req.chat_id:
                try:
                    conv = lib.chat(req.chat_id)
                except KeyError:
                    raise HTTPException(404, "대화를 찾을 수 없습니다 (지워졌을 수 있음)")
            else:
                conv = {"id": uuid.uuid4().hex[:10], "title": req.question.strip()[:60], "messages": []}
            job_id = uuid.uuid4().hex[:8]
            _put({"id": job_id, "state": "running", "done": 0, "total": 0, "message": "질문 이해하는 중",
                  "specs": [], "kind": "chat", "chat_id": conv["id"], "steps": []})  # 409 if this chat is busy
            try:
                conv["messages"].append({"role": "user", "content": req.question.strip(), "context": req.context,
                                         "time": now})
                lib.save_chat(conv["id"], conv["title"], conv["messages"], now)
            except Exception:
                jobs[job_id]["state"] = "error"
                raise
        threading.Thread(target=_run_chat, args=(job_id, conv, req, model), daemon=True).start()
        return {"job": _public_job(jobs[job_id]), "chat_id": conv["id"]}  # a copy: the worker is changing it

    def _run_chat(job_id: str, conv: dict, req: ChatRequest, model: intent.OpenAICompatIntentModel):
        from . import chat as chatmod

        job = jobs[job_id]
        reader = Library(home / "library.sqlite")

        def step(s: str):
            job["steps"].append(s)
            job["message"] = s

        def partial(text: str, sources: dict):  # the answer as it streams in; the chat window polls it
            job["partial"] = text
            if text:
                job["sources"] = sources

        try:
            history, known = [], {}
            msgs = conv["messages"][:-1]  # the last one is the question being answered
            for q, a in zip(msgs, msgs[1:]):  # a question and the answer right after it; a question left
                if q.get("role") != "user" or a.get("role") != "assistant":  # unanswered (app closed) is skipped
                    continue
                if a.get("error") or not a.get("content"):  # a failed turn is not something the model said
                    continue
                history += [{"role": "user", "content": q["content"]}, {"role": "assistant", "content": a["content"]}]
                known.update(a.get("sources") or {})
            res = chatmod.answer(model, reader, req.question.strip(), history, req.context, step=step,
                                 known_sources=known, should_stop=lambda: bool(job.get("cancel")),
                                 on_partial=partial)
            reply = {"role": "assistant", "content": res["answer"] or "(빈 답변)", "sources": res["sources"],
                     "steps": res["steps"], "mode": res["mode"], "model": res.get("model") or model.name}
            final = ("done", "답변 완료")
        except chatmod.Stopped:  # kept in the conversation, left out of the next question's history like errors
            reply = {"role": "assistant", "content": "(답변 중지됨)", "error": True, "stopped": True,
                     "steps": job["steps"]}
            final = ("done", "중지됨")
        except intent.LLMError as e:
            reply = {"role": "assistant", "content": f"AI 요청 실패: {e}", "error": True, "steps": job["steps"]}
            log.warning("chat %s: %s", conv["id"], e, exc_info=not isinstance(e, intent.LLMError))
            final = ("error", str(e))
        except Exception as e:
            reply = {"role": "assistant", "content": f"답변 중 오류: {e}", "error": True, "steps": job["steps"]}
            log.warning("chat %s: %s", conv["id"], e, exc_info=not isinstance(e, intent.LLMError))
            final = ("error", str(e))
        finally:
            reader.close()
        reply["time"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        try:
            with lock:  # saved before the job reads as finished: the window loads the chat right then
                cur = lib.chat(conv["id"])
                cur["messages"].append(reply)
                lib.save_chat(conv["id"], cur["title"], cur["messages"], reply["time"])
        except KeyError:
            pass  # the conversation was deleted while it was being answered
        finally:
            job["state"], job["message"] = final

    # ---- full AI analysis (left running overnight) ----

    @app.post("/api/full-analysis")
    def full_analysis(req: FullAnalysisRequest):
        """Summarise every changed paragraph of every version of every downloaded spec (newest versions first),
        then analyse the CRs left, then summarise every work item. Work is saved as it goes and what is already
        done is skipped, so it can be stopped and started again at any time."""
        model = _llm()
        if not model.api_key and "googleapis.com" in model.base_url:
            raise HTTPException(400, "Gemini API 키를 먼저 저장하세요")
        if any(j.get("kind") == "intent" and j["state"] == "running" for j in _jobs()):
            raise HTTPException(409, "AI 작업이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
        with lock:
            specs = req.specs or [s["number"] for s in lib.specs()]
        job_id = uuid.uuid4().hex[:8]
        _put({"id": job_id, "state": "running", "done": 0, "total": 0, "message": "전체 AI 분석 준비",
                        "specs": specs, "kind": "intent", "full": True,
                        "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                        "counts": {"intents": 0, "cr_analyses": 0, "wi_summaries": 0, "failed": 0}})
        threading.Thread(target=_run_full, args=(job_id, req, specs, model), daemon=True).start()
        return jobs[job_id]

    def _run_full(job_id: str, req: FullAnalysisRequest, specs: list[str], model: intent.OpenAICompatIntentModel):
        import time

        from .pack import build_pack

        job = jobs[job_id]
        counts = job["counts"]
        reader = Library(home / "library.sqlite")
        awake = _keep_awake()
        max_strikes = 8  # waits 1+2+...+7 minutes (about half an hour) before giving up

        class Stop(Exception):
            pass

        def check():
            if job.get("cancel"):
                raise Stop("사용자가 중지함")

        def wait_after_failure(strikes: int, what: str):
            """The LLM server failing every request (restart, network): wait and try again a few times."""
            if strikes >= max_strikes:
                raise Stop(f"LLM 요청이 계속 실패해 멈춤: {what}")
            end = time.monotonic() + FULL_RETRY_WAIT * strikes
            while time.monotonic() < end:
                check()
                time.sleep(min(1, FULL_RETRY_WAIT))

        def failed_item(err: Exception) -> bool:
            """After a failed request: True when the server answers (the item fails on its own: count it and
            go on), False after waiting when it does not (try the item again). Quota stops the run."""
            nonlocal strikes
            log.warning("full analysis: %s", err, exc_info=not isinstance(err, intent.LLMError))
            if isinstance(err, intent.QuotaExceeded):
                raise Stop(str(err))
            try:
                model.ping()
            except intent.QuotaExceeded as e:
                raise Stop(str(e))
            except Exception:
                strikes += 1
                wait_after_failure(strikes, str(err))
                return False
            strikes = 0
            counts["failed"] += 1
            return True

        strikes = 0  # failures in a row while the server did not answer at all
        try:
            # 1) paragraph summaries, newest versions first; CR analyses are made along the way. One pass per
            # version: every summary is saved as it is made (closing the app loses at most the one in flight)
            plan = []
            with lock:
                for spec in specs:
                    plan += [(spec, v["version"]) for v in reversed(lib.versions(spec)[1:])]
            for i, (spec, v) in enumerate(plan, 1):
                check()
                analyzer = _cr_analyzer(reader, spec, model, job)

                def prog(n, total, spec=spec, v=v, i=i):
                    job["done"], job["total"] = n, total
                    job["message"] = f"[1/3 문단 요약] {spec} v{v} ({i}/{len(plan)}번째 버전) {n}/{total}"

                def saved(ch: dict, spec=spec, v=v) -> None:
                    nonlocal strikes
                    if ch.get("intent"):
                        keep = {k: x for k, x in ch.items() if k not in ("clause_key", "text", "old_text")}
                        with lock:
                            counts["intents"] += lib.merge_pack({"spec": spec, "version": v, "changes": [keep],
                                                                 "generated": dt.datetime.now(dt.timezone.utc)
                                                                 .isoformat(timespec="seconds")})
                        strikes = 0
                    elif ch.get("intent_error"):  # left for the next run either way
                        failed_item(LLMErrorText(ch["intent_error"]))
                    counts["cr_analyses"] = job.get("cr_made", 0)

                pack = build_pack(reader, spec, v, model, progress=prog, skip_uncovered=req.skip_uncovered,
                                  skip_hashes=reader.intent_hashes(spec, v), cr_analyzer=analyzer,
                                  on_change=saved, should_stop=lambda: "사용자가 중지함" if job.get("cancel") else None)
                if stopped := pack["generator"].get("stopped"):
                    raise Stop(stopped)
            # 2) CRs no paragraph summary needed (e.g. clauses not downloaded), when they have a cover
            if req.cr_analyses:
                for spec in specs:
                    check()
                    with lock:
                        rows = [c for c in lib.crs(spec) if c["detail"] and c["cr"]
                                and not lib.cr_analysis(spec, c["cr"], c["rev"], c["tdoc"])]
                    analyzer = _cr_analyzer(reader, spec, model, job)
                    for n, row in enumerate(rows, 1):
                        job["done"], job["total"] = n, len(rows)
                        job["message"] = f"[2/3 CR 분석] {spec} CR{row['cr']} {n}/{len(rows)}"
                        while True:
                            check()
                            try:
                                analyzer([row], strict=True)
                                break
                            except Exception as e:  # noqa: BLE001
                                if failed_item(e):
                                    break
                        counts["cr_analyses"] = job.get("cr_made", 0)
            # 3) work item summaries (after the CR analyses they are built from)
            if req.wi_summaries:
                with lock:
                    wis = [w["wi"] for w in lib.work_items() if not w["summary"]]
                for n, w in enumerate(wis, 1):
                    job["done"], job["total"] = n, len(wis)
                    job["message"] = f"[3/3 WI 요약] {w} {n}/{len(wis)}"
                    with lock:
                        wi = lib.work_item(w)
                    while True:
                        check()
                        try:
                            counts["wi_summaries"] += 1 if _summarize_wi(model, wi) else 0
                            break
                        except Exception as e:  # noqa: BLE001
                            if failed_item(e):
                                break
            job["state"] = "done"
            job["message"] = "전체 AI 분석 완료 · " + _full_counts(counts)
        except Stop as e:
            job["state"], job["stopped"] = ("done" if job.get("cancel") else "error"), True
            job["message"] = f"전체 AI 분석 멈춤 ({e}) · " + _full_counts(counts) + " · 다시 시작하면 이어서 합니다"
        except Exception as e:
            job["state"], job["message"] = "error", f"전체 AI 분석 실패: {e} · " + _full_counts(counts)
            log.exception("job %s (%s) failed", job.get("id"), job.get("kind") or job.get("specs"))
        finally:
            job["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            awake()
            reader.close()
            with lock:  # kept for the status board: the job list is gone once the app restarts
                lib.set_setting("last_full_analysis", json.dumps(
                    {k: job.get(k) for k in ("started", "finished", "state", "message", "counts", "specs", "stopped")},
                    ensure_ascii=False))

    @app.get("/api/version-of/{code}")
    def version_of(code: str):
        return {"version": str(Version.from_code(code))}

    if static_dir and Path(static_dir).is_dir():
        static_dir = Path(static_dir)
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        root = static_dir.resolve()

        @app.get("/{path:path}")
        def spa(path: str):
            f = (root / path).resolve()
            ok = path and f.is_file() and f.is_relative_to(root)  # "..%2f" must not leave the UI folder
            return FileResponse(f if ok else root / "index.html")

    return app
