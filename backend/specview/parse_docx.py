"""Convert a 3GPP .docx into a list of clauses made of blocks.

pandoc does the heavy lifting (OMML equations -> LaTeX, tables, lists, custom
paragraph styles via ``+styles``); this module splits its HTML output into
clauses at every heading and into blocks at every top-level element, which are
the units the history engine tracks across versions.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree, html

from .wmf import wmf_to_svg

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_NUM_RE = re.compile(r"^((?:[A-Z]\.)?\d+(?:\.\d+)*)\s+(.*)$", re.S)
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


def _run_pandoc(docx: Path, media_dir: Path) -> str:
    if not shutil.which("pandoc"):
        raise RuntimeError("pandoc is required to convert .docx files (https://pandoc.org)")
    proc = subprocess.run(
        [
            "pandoc", "-f", "docx+styles", "-t", "html", "--katex",
            f"--extract-media={media_dir}", "--wrap=none", str(docx),
        ],
        capture_output=True, check=True,
    )
    return proc.stdout.decode("utf-8")


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
        subprocess.run(
            [soffice, "--headless", "--convert-to", "png", "--outdir", str(d), *map(str, files)],
            capture_output=True, timeout=600,
        )
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
    for el in root:
        tag = el.tag if isinstance(el.tag, str) else ""
        is_heading = tag in HEADING_TAGS or (tag == "p" and "heading" in (el.get("class") or "").split())
        if is_heading:
            key, number, title = _split_heading(el.text_content())
            n = seen.get(key, 0)
            seen[key] = n + 1
            if n:
                key = f"{key}~{n + 1}"
            clauses.append(Clause(key, number, title, _level_for(tag, number)))
            continue
        clauses[-1].blocks.extend(_blocks_from(el))
    if not clauses[0].blocks:
        clauses.pop(0)
    # The "Contents" clause is Word's generated TOC: page-number links, not spec text.
    return [c for c in clauses if c.title.strip().lower() not in ("contents", "table of contents")]


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


def parse_docx(docx: Path) -> ParsedSpec:
    with tempfile.TemporaryDirectory() as tmp:
        media = Path(tmp) / "media"
        doc_html = _run_pandoc(Path(docx), media)
        root = html.fragment_fromstring(doc_html, create_parent="div")
        assets = _rewrite_images(root, media)
        doc_html = "".join(_serialize(el) for el in root)
    clauses = html_to_clauses(doc_html)
    return ParsedSpec(guess_title(clauses), clauses, assets)
