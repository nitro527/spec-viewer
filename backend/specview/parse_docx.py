"""Convert a 3GPP .docx into a list of clauses made of blocks.

pandoc does the heavy lifting (OMML equations -> LaTeX, tables, lists, custom
paragraph styles via ``+styles``); this module splits its HTML output into
clauses at every heading and into blocks at every top-level element, which are
the units the history engine tracks across versions.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree, html

from .wmf import wmf_to_svg

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# 7.4.1, A.2, AA.1, 6.2A.1 (RF specs), 5.2.1.5.1a (a clause added between two others)
# "8.4.1.4." with a stray dot (38.211 v16) is 8.4.1.4
_NUM_RE = re.compile(r"^((?:[A-Z]{1,2}\.)?\d+(?:\.\d+(?:[A-Z]{1,2}|[a-z])?)*)\.?\s+(.*)$", re.S)
_ANNEX_RE = re.compile(r"^Annex\s+([A-Z]{1,2})\b\s*(.*)$", re.S)
_WS = re.compile(r"\s+")
_IMAGE_CONVERT = {".emf"}  # WMF is handled by specview.wmf


@dataclass
class Block:
    kind: str  # p | table | list | eq | figure | other
    style: str  # 3GPP paragraph style (B1, NO, EQ, TH ...) or ""
    html: str
    text: str

    @property
    def hash(self) -> str:
        return content_hash(self.html)


@dataclass
class Clause:
    key: str  # stable identifier: clause number, annex letter or slug
    number: str  # as printed ("7.4.1.1", "A.2", "" for unnumbered)
    title: str
    level: int
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Asset:
    hash: str
    mime: str
    data: bytes


@dataclass
class ParsedSpec:
    title: str | None
    clauses: list[Clause]
    assets: dict[str, Asset]


def content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def norm_text(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _normalize(el) -> None:
    """Drop presentation-only attributes that vary between versions without any text change.

    pandoc emits table widths and odd/even row classes depending on Word layout
    details; keeping them would flag unchanged tables as modified.
    """
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        if "style" in node.attrib and not (node.tag == "img" and "eq-img" in (node.get("class") or "")):
            del node.attrib["style"]
        if node.tag == "tr":
            node.attrib.pop("class", None)
        if node.tag in ("td", "th"):
            for a in ("width", "align", "valign"):
                node.attrib.pop(a, None)


def _serialize(el) -> str:
    _normalize(el)
    s = etree.tostring(el, encoding="unicode", method="html", with_tail=False)
    return _WS.sub(" ", s).strip()


def _split_heading(text: str) -> tuple[str, str, str]:
    """Return (key, number, title) for a heading's plain text."""
    text = norm_text(text)
    m = _ANNEX_RE.match(text)
    if m:
        title = m.group(2).lstrip(":").strip()
        return m.group(1), f"Annex {m.group(1)}", title
    m = _NUM_RE.match(text)
    if m:
        return m.group(1), m.group(1), m.group(2).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"
    return slug, "", text


def _level_for(tag: str, number: str) -> int:
    if number.startswith("Annex"):
        return 1
    if number:
        return number.count(".") + 1
    return int(tag[1]) if tag in HEADING_TAGS else 1


def convert_workers() -> int:
    """How many pandoc conversions run at once: SPECVIEW_CONVERT_WORKERS, else half the CPU cores up to 4 (each
    pandoc on a big spec can take a gigabyte or more of memory; 4 at once is ~3x faster than one)."""
    default = max(1, min(4, (os.cpu_count() or 2) // 2))
    try:
        return max(1, min(8, int(os.environ.get("SPECVIEW_CONVERT_WORKERS", default))))
    except ValueError:
        return default


_SLOTS: threading.BoundedSemaphore | None = None
_SLOTS_LOCK = threading.Lock()


def _pandoc_slot() -> threading.BoundedSemaphore:
    """One limit for every pandoc in the process: versions converted in parallel, and the parts of a split
    spec converted in parallel within each, never run more than convert_workers() at a time."""
    global _SLOTS
    with _SLOTS_LOCK:
        if _SLOTS is None:
            _SLOTS = threading.BoundedSemaphore(convert_workers())
        return _SLOTS


def _run_pandoc(docx: Path, media_dir: Path, track_changes: bool = False) -> str:
    if not shutil.which("pandoc"):
        raise RuntimeError("pandoc is required to convert .docx files (https://pandoc.org)")
    timeout = float(os.environ.get("SPECVIEW_PANDOC_TIMEOUT") or 1800)  # the biggest specs take a few minutes
    try:
        with _pandoc_slot():
            proc = subprocess.run(
                [
                    "pandoc", "-f", "docx+styles", "-t", "html", "--katex",
                    f"--extract-media={media_dir}", "--wrap=none", *(["--track-changes=all"] if track_changes else []),
                    str(docx),
                ],
                capture_output=True, check=True, timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"pandoc took more than {timeout:.0f} s on {Path(docx).name} (SPECVIEW_PANDOC_TIMEOUT)")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"pandoc failed on {Path(docx).name}: {err[-1] if err else e.returncode}")
    return proc.stdout.decode("utf-8")


_SAFE_URL = re.compile(r"^\s*(https?:|mailto:|#|/api/asset/|data:image/(png|jpe?g|gif|webp|bmp);)", re.I)
_UNSAFE_TAGS = ("script", "iframe", "frame", "object", "embed", "form", "base", "meta", "link", "style")


def _make_safe(root) -> None:
    """Documents come from 3gpp.org but CRs are written by anyone: a Word hyperlink to ``javascript:...`` comes
    through pandoc as is. Keep only plain links and images; no scripts, frames or event handlers."""
    for el in root.xpath("|".join(f"//{t}" for t in _UNSAFE_TAGS)):
        el.drop_tree()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for a in list(el.attrib):
            v = el.attrib[a]
            if a.lower().startswith("on") or a.lower() in ("srcdoc", "formaction", "xlink:href"):
                del el.attrib[a]
            elif a.lower() in ("href", "src", "action", "poster", "background") and v and not _SAFE_URL.match(v):
                del el.attrib[a]


def docx_view_html(docx: Path, track_changes: bool = False) -> str:
    """A whole document as one HTML page for the viewer window: images inlined, equations as KaTeX spans.
    With ``track_changes`` (CRs), Word's revision marks become <ins>/<del> like the app's own diffs."""
    import base64

    with tempfile.TemporaryDirectory() as tmp:
        media = Path(tmp) / "media"
        root = html.fragment_fromstring(_run_pandoc(Path(docx), media, track_changes), create_parent="div")
        assets = _rewrite_images(root, media)
        _make_safe(root)  # after the images got their /api/asset/ addresses
        _fix_multiline_math(root)
        for img in root.xpath("//img[starts-with(@src, '/api/asset/')]"):
            a = assets.get(img.get("src").rsplit("/", 1)[-1])
            if a:
                img.set("src", f"data:{a.mime};base64,{base64.b64encode(a.data).decode()}")
        for span in root.xpath("//span[@class]"):
            kind = span.get("class")
            if kind in ("insertion", "deletion"):
                span.tag = "ins" if kind == "insertion" else "del"
                for k in list(span.attrib):
                    del span.attrib[k]
            elif kind in ("paragraph-insertion", "paragraph-deletion"):
                span.drop_tag()
        return "".join(_serialize(el) for el in root)


def _convert_vector_images(paths: list[Path]) -> dict[Path, Path]:
    """Convert WMF/EMF (legacy equation objects) to PNG with LibreOffice, if present."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice or not paths:
        return {}
    out: dict[Path, Path] = {}
    by_dir: dict[Path, list[Path]] = {}
    for p in paths:
        by_dir.setdefault(p.parent, []).append(p)
    for d, files in by_dir.items():
        # its own profile per run: versions convert in parallel, and a second LibreOffice on a shared profile
        # quietly does nothing (images left as EMF in one version, PNG in the next: false changes)
        with tempfile.TemporaryDirectory(prefix="specview-lo-") as profile:
            try:
                subprocess.run(
                    [soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "png",
                     "--outdir", str(d), *map(str, files)],
                    capture_output=True, timeout=600,
                )
            except (OSError, subprocess.SubprocessError):
                continue
        for f in files:
            png = f.with_suffix(".png")
            if png.exists():
                out[f] = png
    return out


_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
         ".svg": "image/svg+xml", ".wmf": "image/wmf", ".emf": "image/emf"}


def _rewrite_images(root, media_dir: Path) -> dict[str, Asset]:
    assets: dict[str, Asset] = {}
    imgs = root.xpath("//img[@src]")
    srcs = [Path(i.get("src")) for i in imgs]
    converted = _convert_vector_images(sorted({s for s in srcs if s.suffix.lower() in _IMAGE_CONVERT and s.exists()}))
    wmf_cache: dict[Path, tuple] = {}
    for img, src in zip(imgs, srcs):
        path = converted.get(src, src)
        if not path.exists():
            continue
        for attr in ("style", "width", "height"):
            img.attrib.pop(attr, None)
        if path.suffix.lower() == ".wmf":
            if path not in wmf_cache:
                try:
                    wmf_cache[path] = (wmf_to_svg(path.read_bytes()),)
                except Exception:
                    wmf_cache[path] = (None,)
            res = wmf_cache[path][0]
            if res is not None:
                data = res.svg.encode("utf-8")
                h = hashlib.sha1(data).hexdigest()[:20]
                assets[h] = Asset(h, "image/svg+xml", data)
                img.set("src", f"/api/asset/{h}")
                img.set("class", "eq-img")
                img.set("alt", res.text)
                # Word body text is 10 pt: sizing in em keeps equations proportional to the text.
                img.set("style", f"height:{res.height_pt / 10:.3f}em")
                continue
        data = path.read_bytes()
        h = hashlib.sha1(data).hexdigest()[:20]
        assets[h] = Asset(h, _MIME.get(path.suffix.lower(), "application/octet-stream"), data)
        img.set("src", f"/api/asset/{h}")
    return assets


def _top_groups(latex: str) -> list[str] | None:
    """``"{a}{b}{c}"`` -> ``["a", "b", "c"]`` when the whole string is >= 2 top-level brace groups."""
    t = latex.strip()
    groups, depth, start, i = [], 0, None, 0
    while i < len(t):
        ch = t[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                groups.append(t[start:i])
        elif depth == 0 and not ch.isspace():
            return None
        i += 1
    return groups if depth == 0 and len(groups) >= 2 else None


def _fix_multiline_math(root) -> None:
    """pandoc renders a Word equation array (several lines in one oMathPara) as ``{l1}{l2}{l3}``,
    which typesets as one run-on line; turn it into a centred multi-line block."""
    for span in root.xpath('//span[contains(concat(" ", @class, " "), " math ")]'):
        groups = _top_groups(span.text or "")
        if groups:
            span.text = "\\begin{gathered}" + " \\\\ ".join(g.strip() for g in groups) + "\\end{gathered}"
            span.set("class", "math display")


def _blocks_from(el) -> list[Block]:
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag == "div" and el.get("data-custom-style"):
        style = el.get("data-custom-style")
        out = []
        for child in el:
            for b in _blocks_from(child):
                b.style = b.style or style
                if style == "EQ":
                    b.kind = "eq"
                out.append(b)
        return out
    if tag == "div":
        return [b for child in el for b in _blocks_from(child)]
    if tag == "colgroup":
        return []
    kind = {"p": "p", "table": "table", "ul": "list", "ol": "list", "figure": "figure"}.get(tag, "other")
    if tag == "table":
        for cg in el.xpath(".//colgroup"):
            cg.getparent().remove(cg)
    text = norm_text(" ".join([el.text_content(), *el.xpath(".//img/@alt")]))
    if kind == "p" and not text and not el.xpath(".//img"):
        return []
    return [Block(kind, "", _serialize(el), text)]


def html_to_clauses(doc_html: str) -> list[Clause]:
    root = html.fragment_fromstring(doc_html, create_parent="div")
    clauses: list[Clause] = [Clause("front", "", "Front matter", 0)]
    seen: dict[str, int] = {}
    numbered = ""  # last numbered clause: unnumbered sub-headings ("UE:", "Preamble:") are keyed under it
    path: list[tuple[int, str]] = []  # enclosing unnumbered headings (level, slug) below that clause
    for el in root:
        tag = el.tag if isinstance(el.tag, str) else ""
        is_heading = tag in HEADING_TAGS or (tag == "p" and "heading" in (el.get("class") or "").split())
        is_heading = is_heading and bool(el.text_content().strip())  # an empty paragraph in a heading style
        if is_heading:
            key, number, title = _split_heading(el.text_content())
            level = _level_for(tag, number)
            if number:
                numbered, path = key, []
            elif numbered:  # stable across versions: "6.2.3/ue", not the 7th "ue" of the document
                # and under their unnumbered parents: 38.508-1 repeats "NZP-CSI-RS-Resource" below
                # "CSI-RS for CSI acquisition" and "CSI-RS for beam refinement"
                path = [(lv, k) for lv, k in path if lv < level]
                slug = key
                key = "/".join([numbered, *(k for _, k in path), slug])
                path.append((level, slug))
            n = seen.get(key, 0)
            seen[key] = n + 1
            if n:
                key = f"{key}~{n + 1}"
            clauses.append(Clause(key, number, title, level))
            continue
        clauses[-1].blocks.extend(_blocks_from(el))
    if not clauses[0].blocks:
        clauses.pop(0)
    # The "Contents" clause is Word's generated TOC: page-number links, not spec text.
    return [c for c in clauses if c.title.strip().lower() not in ("contents", "table of contents")]


def assign_keys(heads: list[tuple[str, str, int]]) -> list[str]:
    """Keys for (number, title, level) headings in document order, as html_to_clauses gives them: a numbered
    clause is its number ("Annex B" -> "B"), an unnumbered heading goes under the last numbered clause and
    its enclosing unnumbered headings ("6.2.3/ue"), repeats get "~2", "~3"."""
    keys: list[str] = []
    seen: dict[str, int] = {}
    numbered = ""
    path: list[tuple[int, str]] = []
    for number, title, level in heads:
        if number:
            key = number[6:] if number.startswith("Annex ") else number
            numbered, path = key, []
        else:
            key = _split_heading(title)[0]
            if numbered:
                path = [(lv, k) for lv, k in path if lv < level]
                slug = key
                key = "/".join([numbered, *(k for _, k in path), slug])
                path.append((level, slug))
        n = seen.get(key, 0)
        seen[key] = n + 1
        keys.append(f"{key}~{n + 1}" if n else key)
    return keys


def guess_title(clauses: list[Clause]) -> str | None:
    # Cover page: ZT-styled lines "3rd Generation Partnership Project;" ... "NR;" "Physical channels ..."
    front = clauses[0].blocks if clauses else []
    zt = [b.text for b in front[:30] if b.style == "ZT" and b.text]
    parts = [t for t in zt if not re.match(r"(3rd Generation|Technical Specification Group|\(Release)", t)]
    if parts:
        return " ".join(parts)
    for c in clauses[:3]:
        for b in c.blocks[:40]:
            if re.match(r"^(NR|Evolved|E-UTRA|5G|LTE|Study|Technical Specification Group)\b.*;", b.text):
                return b.text
    return None


def parse_docx(docx: Path | list[Path]) -> ParsedSpec:
    """One spec from a .docx, or from the parts a large spec is split into (cover, s00-s05, s06-s08 ...)."""
    if isinstance(docx, (list, tuple)):
        if len(docx) > 1:  # each part is its own pandoc run: convert them side by side (38.101-1: 5 parts)
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(len(docx)) as ex:
                parts = list(ex.map(lambda p: _parse_one(Path(p)), docx))
        else:
            parts = [_parse_one(Path(p)) for p in docx]
        return merge_parts(sorted(parts, key=_part_order)) if len(parts) > 1 else parts[0]
    return _parse_one(Path(docx))


def _parse_one(docx: Path) -> ParsedSpec:
    with tempfile.TemporaryDirectory() as tmp:
        media = Path(tmp) / "media"
        doc_html = _run_pandoc(Path(docx), media)
        root = html.fragment_fromstring(doc_html, create_parent="div")
        assets = _rewrite_images(root, media)
        _make_safe(root)  # after the images got their /api/asset/ addresses
        _fix_multiline_math(root)
        doc_html = "".join(_serialize(el) for el in root)
    clauses = html_to_clauses(doc_html)
    return ParsedSpec(guess_title(clauses), clauses, assets)


def _clause_order(number: str) -> tuple:
    """"6.2A.1" < "6.10" < "Annex A" < "A.4.6" < "A.10" < "B.1": numbered clauses, then annexes by letter."""
    number = re.sub(r"^Annex\s+", "", number)
    comps = [[(0, int(t)) if t.isdigit() else (1, t.lower()) for t in re.split(r"(\d+)", c) if t]
             for c in number.split(".")]
    return (1 if number[:1].isalpha() else 0, comps)


def _part_order(part: ParsedSpec) -> tuple:
    """Split parts cover disjoint clause ranges: order them by their first numbered heading (cover sheets first).
    Stable, so parts without numbered headings keep their file order."""
    first = next((c.number for c in part.clauses if c.number), None)
    return (0, ()) if first is None else (1, _clause_order(first))


def merge_parts(parts: list[ParsedSpec]) -> ParsedSpec:
    """Concatenate split documents in order; text before a part's first heading continues the previous clause."""
    clauses: list[Clause] = []
    assets: dict[str, Asset] = {}
    for part in parts:
        assets.update(part.assets)
        for i, c in enumerate(part.clauses):
            if i == 0 and c.key == "front" and clauses:
                clauses[-1].blocks.extend(c.blocks)
                continue
            clauses.append(Clause(c.key, c.number, c.title, c.level, list(c.blocks)))
    # keys as one document would give them: a part that starts with unnumbered headings continues the last
    # numbered clause of the part before ("6.2.3/gnb", not "gnb"), and repeats are numbered across parts
    body = [c for c in clauses if c.key != "front"]
    for c, k in zip(body, assign_keys([(c.number, c.title, c.level) for c in body])):
        c.key = k
    title = next((p.title for p in parts if p.title), None)
    return ParsedSpec(title, clauses, assets)
