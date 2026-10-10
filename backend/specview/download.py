"""Fetch specification versions from the 3GPP archive.

All published versions of a spec live under
``https://www.3gpp.org/ftp/Specs/archive/<series>_series/<spec>/`` as
``<digits>-<version code>.zip``, each containing the .docx (older ones a .doc).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests

from .versions import Version, parse_archive_name, series_of

ARCHIVE = "https://www.3gpp.org/ftp/Specs/archive"
_HREF = re.compile(r'href="([^"]+)"', re.I)


@dataclass
class RemoteVersion:
    spec: str
    version: Version
    url: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def archive_url(spec: str) -> str:
    return f"{ARCHIVE}/{series_of(spec)}_series/{spec}/"


def list_remote(spec: str, session: requests.Session | None = None) -> list[RemoteVersion]:
    s = session or requests.Session()
    r = s.get(archive_url(spec), timeout=60)
    r.raise_for_status()
    found: dict[Version, RemoteVersion] = {}
    for href in _HREF.findall(r.text):
        name = href.rstrip("/").rsplit("/", 1)[-1]
        parsed = parse_archive_name(name)
        if parsed and parsed[0] == spec:
            url = href if href.startswith("http") else archive_url(spec) + name
            found[parsed[1]] = RemoteVersion(spec, parsed[1], url)
    return [found[v] for v in sorted(found)]


def select(versions: Iterable[RemoteVersion], releases: set[int] | None = None,
           latest_per_release: bool = False) -> list[RemoteVersion]:
    vs = [v for v in versions if not releases or v.version.release in releases]
    if latest_per_release:
        best: dict[int, RemoteVersion] = {}
        for v in vs:
            if v.version.release not in best or best[v.version.release].version < v.version:
                best[v.version.release] = v
        vs = sorted(best.values(), key=lambda v: v.version)
    return vs


def _extract_docx(zip_path: Path, out_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith((".docx", ".doc"))]
        if not names:
            raise RuntimeError(f"no Word document in {zip_path.name}")
        name = max(names, key=lambda n: z.getinfo(n).file_size)  # main body, not cover sheets
        target = out_dir / Path(name).name
        with z.open(name) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    if target.suffix.lower() == ".doc":
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise RuntimeError(f"{target.name} is a legacy .doc; LibreOffice is needed to convert it")
        subprocess.run([soffice, "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(target)],
                       capture_output=True, check=True, timeout=600)
        target = target.with_suffix(".docx")
    return target


def fetch(remote: RemoteVersion, cache_dir: Path, session: requests.Session | None = None,
          progress: Callable[[int, int], None] | None = None) -> Path:
    """Download (cached) and return the path of the extracted .docx."""
    s = session or requests.Session()
    d = Path(cache_dir) / remote.spec / str(remote.version)
    d.mkdir(parents=True, exist_ok=True)
    docs = list(d.glob("*.docx"))
    if docs:
        return docs[0]
    zip_path = d / remote.filename
    if not zip_path.exists():
        with s.get(remote.url, stream=True, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            tmp = zip_path.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            tmp.rename(zip_path)
    return _extract_docx(zip_path, d)
