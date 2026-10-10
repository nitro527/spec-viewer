"""Fetch specification versions from the 3GPP archive.

All published versions of a spec live under
``https://www.3gpp.org/ftp/Specs/archive/<series>_series/<spec>/`` as
``<digits>-<version code>.zip``, each containing the .docx (older ones a .doc).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests

from .versions import Version, parse_archive_name, series_of


class LegacyDocError(RuntimeError):
    """A version only published as .doc that neither LibreOffice nor Word could convert."""

ARCHIVE = "https://www.3gpp.org/ftp/Specs/archive"
_HREF = re.compile(r'href="([^"]+)"', re.I)
# 3gpp.org sits behind a WAF that rejects the default python-requests/curl User-Agent.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


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
    s = session or new_session()
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


def is_draft(v: Version) -> bool:
    """0.x/1.x/2.x are pre-approval drafts (major 3+ only after TSG approval)."""
    return v.major < 3


def select(versions: Iterable[RemoteVersion], releases: set[int] | None = None,
           latest_per_release: bool = False, include_drafts: bool = False) -> list[RemoteVersion]:
    vs = [v for v in versions
          if (include_drafts or not is_draft(v.version)) and (not releases or v.version.release in releases)]
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
        target = _convert_doc(target)
    return target


# Word (COM) as a fallback on Windows PCs without LibreOffice. 16 = wdFormatXMLDocument (.docx).
_WORD_PS = r"""
$ErrorActionPreference = 'Stop'
$w = New-Object -ComObject Word.Application
try {
    $w.Visible = $false; $w.DisplayAlerts = 0
    $d = $w.Documents.Open($env:SPECVIEW_DOC_SRC, $false, $true, $false)
    try { $d.SaveAs2($env:SPECVIEW_DOC_DST, 16) } finally { $d.Close($false) }
} finally { $w.Quit() }
"""


def _convert_doc(src: Path) -> Path:
    """.doc -> .docx with LibreOffice, else Microsoft Word; raises LegacyDocError if neither works."""
    dst = src.with_suffix(".docx")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        subprocess.run([soffice, "--headless", "--convert-to", "docx", "--outdir", str(src.parent), str(src)],
                       capture_output=True, check=True, timeout=600)
        return dst
    if os.name == "nt":
        env = {**os.environ, "SPECVIEW_DOC_SRC": str(src.resolve()), "SPECVIEW_DOC_DST": str(dst.resolve())}
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WORD_PS],
                           capture_output=True, check=True, timeout=600, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            dst.unlink(missing_ok=True)  # a half-written file would be picked up as cached
        if dst.exists():
            return dst
    raise LegacyDocError(f"{src.name} is a legacy .doc; install LibreOffice or Microsoft Word to convert it")


def fetch(remote: RemoteVersion, cache_dir: Path, session: requests.Session | None = None,
          progress: Callable[[int, int], None] | None = None) -> Path:
    """Download (cached) and return the path of the extracted .docx."""
    s = session or new_session()
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
