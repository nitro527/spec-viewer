"""Local HTTP API consumed by the viewer UI (served on 127.0.0.1 only)."""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import download
from .parse_docx import parse_docx
from .store import Library
from .versions import Version


def default_home() -> Path:
    return Path(os.environ.get("SPECVIEW_HOME", Path.home() / ".specview"))


class FetchRequest(BaseModel):
    specs: list[str]
    releases: list[int] | None = None
    latest_per_release: bool = False


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

    @app.get("/api/specs/{spec}/compare")
    def compare(spec: str, old: str, new: str, changed_only: bool = True):
        return _get(lib.compare, spec, old, new, changed_only)

    @app.get("/api/asset/{h}")
    def asset(h: str):
        a = _get(lib.asset, h)
        if not a:
            raise HTTPException(404)
        return Response(a[1], media_type=a[0], headers={"Cache-Control": "max-age=31536000, immutable"})

    # ---- downloads (background jobs) ----

    @app.get("/api/remote/{spec}")
    def remote(spec: str):
        try:
            return [{"version": str(r.version), "release": r.version.release, "url": r.url}
                    for r in download.list_remote(spec)]
        except Exception as e:  # network / policy errors surface to the UI
            raise HTTPException(502, f"cannot reach 3GPP archive: {e}")

    def _run_fetch(job_id: str, req: FetchRequest):
        job = jobs[job_id]
        try:
            for spec in req.specs:
                job["message"] = f"{spec}: listing versions"
                remote_versions = download.select(download.list_remote(spec),
                                                  set(req.releases) if req.releases else None,
                                                  req.latest_per_release)
                job["total"] += len(remote_versions)
                for rv in remote_versions:
                    job["message"] = f"{spec} v{rv.version}: downloading"
                    path = download.fetch(rv, home / "cache")
                    job["message"] = f"{spec} v{rv.version}: converting"
                    parsed = parse_docx(path)
                    with lock:
                        lib.add_version(spec, rv.version, parsed, source=rv.url, recompute=False)
                    job["done"] += 1
                job["message"] = f"{spec}: computing history"
                with lock:
                    lib.recompute_history(spec)
            job["state"], job["message"] = "done", "finished"
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
