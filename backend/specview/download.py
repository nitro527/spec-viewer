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
import tempfile
import threading
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
    """A session for 3gpp.org that retries what is usually momentary there: timeouts, dropped connections,
    429 and 5xx (with back-off, honouring Retry-After)."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=4, connect=4, read=4, status=4, backoff_factor=2, allowed_methods={"GET", "HEAD"},
                  status_forcelist=(429, 500, 502, 503, 504), respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


DOWNLOAD_RETRY_WAIT = 5  # seconds, times the attempt number, between tries of a download cut off midway


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


_REPORT_ROW = re.compile(r"<td[^>]*>\s*(TS|TR)\s*<a[^>]*>([\d.\-]+)</a>.*?</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
                         re.S)


def catalog(series: str, session: requests.Session | None = None) -> list[dict]:
    """Every TS/TR of a series with its title, from 3GPP's series report (e.g. dynareport/38-series.htm)."""
    import html as _html

    s = session or new_session()
    r = s.get(f"https://www.3gpp.org/dynareport/{series}-series.htm", timeout=60)
    r.raise_for_status()
    rows = [{"number": num, "type": typ, "title": " ".join(_html.unescape(re.sub(r"<[^>]+>", "", title)).split()),
             "withdrawn": "WITHDRAWN" in flag.upper()}
            for typ, num, title, flag in _REPORT_ROW.findall(r.text)]
    parents = {n["number"].rsplit("-", 1)[0] for n in rows if "-" in n["number"]}
    return [x for x in rows if x["number"] not in parents]  # 38.101 itself only exists as 38.101-1, -2 ...


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


# Large specs (e.g. TS 36.211 since Rel-14) ship as several documents: 36211-ef0_cover.docx, _s00-s05, _s06-s08 ...
# s00-s05, s09-sxx, sA0606-sA0611, sB01-sXX, s00-05, s07-XX, sAnnexes
# "_s00-s05", "_1_s0-s7", "_sA0407-sA05", dotted "_sA.407-A.5" (38.133 v20.0.0), "-sB.1-XX" (v16.0.0),
# "_s07_01_01-s07_01_04" (36.523-1), "_s00-s06_noTOC_wrongfilenames!" (36.521-3), "_1" "_2" (36.777)
_PART = re.compile(r"[_-](cover\b.*|s[0-9A-Za-z][^/]*|\d{1,2})\.docx?$", re.I)
_NOT_PART = re.compile(r"rev_?marks|track|with_?changes", re.I)  # "36133-ba0_sA.7-A.8_with_rev_marks.doc" copies


def _natural(n: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", n)]


def _word_parts(z: zipfile.ZipFile) -> list[str]:
    names = [n for n in z.namelist() if n.lower().endswith((".docx", ".doc"))]
    # TRs with annexes in their own files: 38857-h00.docx + "38857-h00 annex C1.1.1-7.docx" ...
    main = [n for n in names if Path(n).stem.lower() == Path(z.filename or "").stem.lower()]
    annexes = [n for n in names if re.search(r"(?<![a-z])annex", Path(n).stem, re.I)]  # "_AnnexB1", " annex C1.2"
    if main and annexes:
        return main + sorted(annexes, key=_natural)
    # natural order: 38133-k10_2_s8 before 38133-k10_10_sA06; "_cover" before "_s00". Names without an index
    # ("_sA.406" = A.4.6 but "_sA.10" = A.10) are put in clause order by parse_docx once their headings are known.
    parts = sorted((n for n in names if _PART.search(Path(n).name) and not _NOT_PART.search(n)), key=_natural)
    if len(parts) > 1:
        return parts
    return [max(names, key=lambda n: z.getinfo(n).file_size)] if names else []  # main body, not cover sheets


def _extract_docx(zip_path: Path, out_dir: Path) -> list[Path]:
    with zipfile.ZipFile(zip_path) as z:
        names = _word_parts(z)
        if not names:
            raise RuntimeError(f"no Word document in {zip_path.name}")
        targets = []
        for name in names:
            target = out_dir / Path(name).name
            # a half-written file must not count as cached; private name: two jobs may extract the same version
            part = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.part")
            with z.open(name) as src, open(part, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(part, target)
            targets.append(target)
    return [_convert_doc(t) if t.suffix.lower() == ".doc" else t for t in targets]


def _cached_parts(zip_path: Path, out_dir: Path) -> list[Path] | None:
    """The extracted documents of an earlier run, if all of them are there (older runs kept only the largest part)."""
    if not zip_path.exists():
        return None
    try:
        with zipfile.ZipFile(zip_path) as z:
            want = [out_dir / Path(n).with_suffix(".docx").name for n in _word_parts(z)]
    except zipfile.BadZipFile:
        return None
    return want if want and all(p.exists() for p in want) else None


# Word (COM) as a fallback on Windows PCs without LibreOffice. 16 = wdFormatXMLDocument (.docx).
_WORD_PS = r"""
$ErrorActionPreference = 'Stop'
$w = New-Object -ComObject Word.Application
try {
    $w.Visible = $false; $w.DisplayAlerts = 0
    $w.AutomationSecurity = 3  # msoAutomationSecurityForceDisable: never run a document's macros
    $d = $w.Documents.Open($env:SPECVIEW_DOC_SRC, $false, $true, $false)
    try { $d.SaveAs2($env:SPECVIEW_DOC_DST, 16) } finally { $d.Close($false) }
} finally { $w.Quit() }
"""


_WORD_LOCK = __import__("threading").Lock()


def _convert_doc(src: Path) -> Path:
    """.doc -> .docx with LibreOffice, else Microsoft Word; raises LegacyDocError if neither works."""
    dst = src.with_suffix(".docx")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        # own profile per call: a second LibreOffice on the same profile fails (a download and a CR lookup at once)
        with tempfile.TemporaryDirectory(prefix="specview-lo-") as profile:
            try:
                subprocess.run([soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless",
                                "--convert-to", "docx", "--outdir", str(src.parent), str(src)],
                               capture_output=True, check=True, timeout=600)
            except (OSError, subprocess.SubprocessError):
                pass
        if dst.exists():
            return dst
    if os.name == "nt":
        env = {**os.environ, "SPECVIEW_DOC_SRC": str(src.resolve()), "SPECVIEW_DOC_DST": str(dst.resolve())}
        try:
            with _WORD_LOCK:  # versions convert in parallel; Word automation does not like that
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WORD_PS],
                               capture_output=True, check=True, timeout=600, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            try:
                dst.unlink(missing_ok=True)  # a half-written file would be picked up as cached
            except OSError:  # still locked by a Word that timed out
                pass
            return _raise_legacy(src)
        if dst.exists():
            return dst
    return _raise_legacy(src)


def _raise_legacy(src: Path):
    raise LegacyDocError(f"{src.name} is a legacy .doc; install LibreOffice or Microsoft Word to convert it")


def fetch(remote: RemoteVersion, cache_dir: Path, session: requests.Session | None = None,
          progress: Callable[[int, int], None] | None = None) -> list[Path]:
    """Download (cached) and return the extracted .docx files of one version (usually one, in order if split)."""
    s = session or new_session()
    d = Path(cache_dir) / remote.spec / str(remote.version)
    d.mkdir(parents=True, exist_ok=True)
    zip_path = d / remote.filename
    if cached := _cached_parts(zip_path, d):
        return cached
    if zip_path.exists() and not zipfile.is_zipfile(zip_path):
        zip_path.unlink()  # damaged cache (disk full, an antivirus cutting it): download again
    if not zip_path.exists():
        for attempt in range(1, 4):  # the session retries the request; this also covers a body cut off midway
            try:
                _download(s, remote.url, zip_path, progress)
                break
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                if attempt == 3:
                    raise
                import time
                time.sleep(DOWNLOAD_RETRY_WAIT * attempt)
    return _extract_docx(zip_path, d)


def _download(s: requests.Session, url: str, dest: Path, progress: Callable[[int, int], None] | None) -> None:
    with s.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part")  # private per download
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done < total:  # the server closed early without an error
            raise requests.exceptions.ChunkedEncodingError(f"{url}: {done}/{total} bytes")
        tmp.replace(dest)
