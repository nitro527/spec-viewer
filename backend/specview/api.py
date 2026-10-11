"""Local HTTP API consumed by the viewer UI (served on 127.0.0.1 only)."""
from __future__ import annotations

import datetime as dt
import os
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import download, intent
from .parse_docx import parse_docx
from .store import Library
from .versions import Version


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


class FetchRequest(BaseModel):
    specs: list[str]
    releases: list[int] | None = None
    latest_per_release: bool = False
    with_crs: bool = True
    crs_only: bool = False


def create_app(home: Path | None = None, static_dir: Path | None = None) -> FastAPI:
    home = Path(home or default_home())
    lib = Library(home / "library.sqlite")
    lock = threading.Lock()
    jobs: dict[str, dict] = {}
    app = FastAPI(title="specview")

    def _get(fn, *a):
        try:
            with lock:
                return fn(*a)
        except KeyError as e:
            raise HTTPException(404, str(e))

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

    @app.get("/api/specs/{spec}/{version}/search")
    def search(spec: str, version: str, q: str):
        return _get(lib.search, spec, version, q)

    @app.get("/api/specs/{spec}/crs")
    def crs(spec: str, version: str | None = None):
        return _get(lib.crs, spec, version)

    @app.get("/api/specs/{spec}/stats")
    def stats(spec: str):
        return _get(lib.stats, spec)

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

    @app.post("/api/fetch-features")
    def fetch_features():
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"id": job_id, "state": "running", "done": 0, "total": 1, "message": "starting",
                        "specs": ["38.822"]}
        threading.Thread(target=_run_features, args=(job_id,), daemon=True).start()
        return jobs[job_id]

    @app.get("/api/specs/{spec}/notes")
    def notes(spec: str):
        return _get(lib.notes, spec)

    @app.put("/api/specs/{spec}/notes/{key}")
    def save_note(spec: str, key: str, body: dict):
        with lock:
            return lib.save_note(spec, key, body.get("bookmark"), body.get("text"), body.get("version"))

    @app.get("/api/settings")
    def get_settings():
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
        return _get(lib.compare, spec, old, new, changed_only)

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
        legacy = lib.get_setting("llm_provider") == p  # settings saved before they were per provider
        get = lambda k: lib.get_setting(f"llm_{p}_{k}") or (lib.get_setting(f"llm_{k}") if legacy else None)
        saved_key = get("api_key") or ""
        env_key = os.environ.get(key_env, "") if key_env else os.environ.get("LLM_API_KEY", "")
        return {"base_url": get("base_url") or url, "model": get("model") or model, "api_key": saved_key or env_key,
                "key_source": "saved" if saved_key else "env" if env_key else None}

    def _current_provider() -> str:
        return lib.get_setting("llm_provider") or "gemini"

    def _public(st: dict) -> dict:
        st = dict(st)
        key = st.pop("api_key")
        return {**st, "has_key": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else ""}

    def _llm(cfg: LLMConfig | None = None) -> intent.OpenAICompatIntentModel:
        st = _provider_settings((cfg and cfg.provider) or _current_provider())
        if cfg:  # unsaved values from the form, so "test" works before "save"
            st.update({k: v.strip() for k, v in (("base_url", cfg.base_url), ("model", cfg.model),
                                                 ("api_key", cfg.api_key)) if v and v.strip()})
        if not st["base_url"]:
            raise HTTPException(400, "LLM 서버 주소를 입력하세요")
        if not st["model"]:
            raise HTTPException(400, "모델 이름을 입력하세요")
        return intent.OpenAICompatIntentModel(st["base_url"], st["model"], st["api_key"] or None, timeout=120)

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

        def run(rows: list[dict]) -> list[dict]:
            out = []
            for row in rows:
                key = (row["cr"], row["rev"], row["tdoc"])
                have = None if key in force else reader.cr_analysis(spec, *key)
                if have is None:
                    job["message"] = f"{spec} CR{row['cr']} 분석 중 (CR 문서·회의 자료 모으는 중)"
                    ev = cranalysis.evidence(reader, spec, row, home / "cache", finder)
                    job["message"] = f"{spec} CR{row['cr']} 분석 중 (AI 요청)"
                    res = model.analyze_cr(ev)
                    if not res:
                        continue
                    have = {**res, "spec": spec, "cr": row["cr"], "rev": row["rev"], "tdoc": row["tdoc"],
                            "meeting": row["meeting"], "title": row["detail"].get("title") or row["subject"],
                            "version": row["lib_version"], "evidence": cranalysis.brief(ev),
                            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
                    with lock:
                        lib.save_cr_analysis(spec, *key, have)
                    job["cr_made"] = job.get("cr_made", 0) + 1
                out.append(have)
            return out
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
        finally:
            reader.close()

    @app.post("/api/intent")
    def make_intents(req: IntentRequest):
        model = _llm()
        if not model.api_key and "googleapis.com" in model.base_url:
            raise HTTPException(400, "Gemini API 키를 먼저 저장하세요")
        if any(j.get("kind") == "intent" and j["state"] == "running" for j in jobs.values()):
            raise HTTPException(409, "AI 요약이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"id": job_id, "state": "running", "done": 0, "total": 0, "message": "AI 요약 준비",
                        "specs": [req.spec], "kind": "intent"}
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
            got = _cr_analyzer(reader, req.spec, model, job, force={(row["cr"], row["rev"], row["tdoc"])})([row])
            job["state"] = "done" if got else "error"
            job["message"] = f"{req.spec} CR{req.cr} 분석 완료 ({model.name})" if got else "AI 응답이 비어 있습니다"
        except intent.LLMError as e:
            job["state"], job["message"] = "error", str(e)
        except Exception as e:
            job["state"], job["message"] = "error", f"CR 분석 실패: {e}"
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
        if any(j.get("kind") == "intent" and j["state"] == "running" for j in jobs.values()):
            raise HTTPException(409, "AI 작업이 이미 진행 중입니다. 끝난 뒤에 다시 시도하세요")
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"id": job_id, "state": "running", "done": 0, "total": 1, "message": f"CR{req.cr} 분석 준비",
                        "specs": [req.spec], "kind": "intent"}
        threading.Thread(target=_run_cr_analysis, args=(job_id, req, row, model), daemon=True).start()
        return jobs[job_id]

    # ---- downloads (background jobs) ----

    @app.get("/api/meeting-doc")
    def meeting_doc(folder: str, tdoc: str | None = None):
        """A WG meeting report (no tdoc) or meeting Tdoc rendered for the document viewer window."""
        from . import minutes

        try:
            return minutes.document_html(folder, tdoc, home / "cache")
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

    def _run_fetch(job_id: str, req: FetchRequest):
        job = jobs[job_id]
        skipped = []  # .doc-only versions with no converter on this PC
        drafts_only = []  # specs with no approved (3.x and later) version yet
        try:
            for spec in req.specs:
                if req.crs_only:
                    def prog(i, n, c):
                        job["done"], job["total"] = i, n
                        job["message"] = f"{spec}: CR{c['cr']} ({c['tdoc']})"
                    lib.fetch_cr_details(spec, cache_dir=home / "cache", progress=prog, refetch_missing=True, lock=lock)
                    continue
                job["message"] = f"{spec}: listing versions"
                remote_versions = download.select(download.list_remote(spec),
                                                  set(req.releases) if req.releases else None,
                                                  req.latest_per_release)
                if not remote_versions:
                    drafts_only.append(spec)
                    continue
                job["total"] += len(remote_versions)
                for rv in remote_versions:
                    job["message"] = f"{spec} v{rv.version}: downloading"
                    try:
                        path = download.fetch(rv, home / "cache")
                    except download.LegacyDocError:
                        skipped.append(f"{spec} v{rv.version}")
                        job["done"] += 1
                        continue
                    job["message"] = f"{spec} v{rv.version}: converting"
                    parsed = parse_docx(path)
                    with lock:
                        lib.add_version(spec, rv.version, parsed, source=rv.url, recompute=False)
                    job["done"] += 1
                title = _official_title(spec)  # cover pages of recent versions often defeat title detection
                if title:
                    with lock:
                        lib.set_spec_title(spec, title)
                job["message"] = f"{spec}: computing history"
                with lock:
                    lib.recompute_history(spec)
                if req.with_crs:
                    def prog(i, n, c):
                        job["message"] = f"{spec}: CR 표지 {i}/{n} ({c['tdoc']})"
                    lib.fetch_cr_details(spec, cache_dir=home / "cache", progress=prog, lock=lock)
            job["state"], job["message"] = "done", "finished"
            notes = []
            if skipped:
                notes.append(f"건너뜀: {', '.join(skipped)} (옛 .doc 형식, LibreOffice 또는 Word 가 있어야 변환됨)")
            if drafts_only:
                notes.append(f"받을 버전 없음: {', '.join(drafts_only)} (승인 전 초안만 있거나 고른 릴리즈에 버전이 없음)")
            if notes:
                job["message"] = "finished — " + " · ".join(notes)
        except Exception as e:
            job["state"], job["message"] = "error", str(e)

    @app.post("/api/fetch")
    def fetch(req: FetchRequest):
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"id": job_id, "state": "running", "done": 0, "total": 0, "message": "starting",
                        "specs": req.specs}
        threading.Thread(target=_run_fetch, args=(job_id, req), daemon=True).start()
        return jobs[job_id]

    @app.get("/api/jobs")
    def list_jobs():
        return list(jobs.values())

    @app.get("/api/version-of/{code}")
    def version_of(code: str):
        return {"version": str(Version.from_code(code))}

    if static_dir and Path(static_dir).is_dir():
        static_dir = Path(static_dir)
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = static_dir / path
            return FileResponse(f if path and f.is_file() else static_dir / "index.html")

    return app
