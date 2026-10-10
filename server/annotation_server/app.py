"""HTTP API serving annotation packs.

    GET /v1/packs                        index: [{spec, version, generated, url}]
    GET /v1/packs/{spec}/{version}.json  one pack
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def create_app(pack_dir: Path) -> FastAPI:
    pack_dir = Path(pack_dir)
    app = FastAPI(title="specview annotation server")

    @app.get("/v1/packs")
    def index():
        out = []
        for f in sorted(pack_dir.glob("*/*.json")):
            meta = json.loads(f.read_text(encoding="utf-8"))
            out.append({"spec": meta["spec"], "version": meta["version"], "generated": meta["generated"],
                        "changes": len(meta["changes"]), "url": f"/v1/packs/{meta['spec']}/{meta['version']}.json"})
        return out

    @app.get("/v1/packs/{spec}/{version}.json")
    def pack(spec: str, version: str):
        f = pack_dir / spec / f"{version}.json"
        if not f.is_file():
            raise HTTPException(404, "no pack")
        return FileResponse(f, media_type="application/json")

    return app
