"""Minimal WMF -> SVG converter for legacy equation objects.

Older 3GPP specs embed equations as Equation Editor 3.0 / MathType OLE objects
whose only renderable form is a WMF preview. Those metafiles use a small set of
records (fonts, text out, lines, polygons), so translating them directly gives
crisp, correctly cropped vector output plus the equation's text, without
LibreOffice (which renders each metafile onto a full A4 page).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

PLACEABLE_KEY = 0x9AC6CDD7

# Adobe Symbol font code points -> Unicode (only the printable range that differs from ASCII).
_SYMBOL = {
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−", 0x40: "≅",
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε", 0x46: "Φ", 0x47: "Γ", 0x48: "Η", 0x49: "Ι",
    0x4A: "ϑ", 0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ", 0x52: "Ρ",
    0x53: "Σ", 0x54: "Τ", 0x55: "Υ", 0x56: "ς", 0x57: "Ω", 0x58: "Ξ", 0x59: "Ψ", 0x5A: "Ζ", 0x5C: "∴",
    0x5E: "⊥", 0x60: "‾",
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι",
    0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ",
    0x73: "σ", 0x74: "τ", 0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ", 0x7E: "∼",
    0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞", 0xA6: "ƒ", 0xA7: "♣", 0xA8: "♦", 0xA9: "♥",
    0xAA: "♠", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°", 0xB1: "±", 0xB2: "″",
    0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈",
    0xBC: "…", 0xBD: "⏐", 0xBE: "⎯", 0xBF: "↵", 0xC0: "ℵ", 0xC1: "ℑ", 0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗",
    0xC5: "⊕", 0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇", 0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊆",
    0xCE: "∈", 0xCF: "∉", 0xD0: "∠", 0xD1: "∇", 0xD2: "®", 0xD3: "©", 0xD4: "™", 0xD5: "∏", 0xD6: "√",
    0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔", 0xDC: "⇐", 0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓",
    0xE0: "◊", 0xE1: "〈", 0xE5: "∑", 0xE6: "⎛", 0xE7: "⎜", 0xE8: "⎝", 0xE9: "⎡", 0xEA: "⎢", 0xEB: "⎣",
    0xEC: "⎧", 0xED: "⎨", 0xEE: "⎩", 0xEF: "⎪", 0xF1: "〉", 0xF2: "∫", 0xF3: "⌠", 0xF4: "⎮", 0xF5: "⌡",
    0xF6: "⎞", 0xF7: "⎟", 0xF8: "⎠", 0xF9: "⎤", 0xFA: "⎥", 0xFB: "⎦", 0xFC: "⎫", 0xFD: "⎬", 0xFE: "⎭",
}
# MT Extra (MathType helper font): the handful of glyphs that show up in equations.
_MTEXTRA = {0x22: "∘", 0x26: "→", 0x4B: "ℏ", 0x4C: "…", 0x4D: "⋯", 0x4E: "⋮", 0x4F: "⋰", 0x50: "⋱",
            0x60: "ℓ", 0x6A: "ȷ", 0x6B: "ħ", 0x6C: "ℓ", 0x7B: "⏞", 0x7D: "⏟"}


@dataclass
class _Font:
    face: str
    size: float
    weight: int
    italic: bool
    escapement: int = 0


@dataclass
class _Pen:
    width: float
    color: str
    visible: bool


@dataclass
class _Brush:
    color: str
    visible: bool


@dataclass
class _State:
    font: _Font | None = None
    pen: _Pen = field(default_factory=lambda: _Pen(1, "#000", True))
    brush: _Brush = field(default_factory=lambda: _Brush("#fff", True))
    text_color: str = "#000"
    align: int = 0
    pos: tuple[float, float] = (0, 0)


def _color(ref: int) -> str:
    r, g, b = ref & 0xFF, (ref >> 8) & 0xFF, (ref >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def _decode(raw: bytes, face: str) -> str:
    f = face.lower()
    if f == "symbol":
        return "".join(_SYMBOL.get(b, chr(b)) for b in raw)
    if f == "mt extra":
        return "".join(_MTEXTRA.get(b, chr(b)) for b in raw)
    return raw.decode("cp1252", errors="replace")


@dataclass
class WmfResult:
    svg: str
    text: str
    width_pt: float
    height_pt: float


def wmf_to_svg(data: bytes) -> WmfResult:
    off = 0
    bbox = None
    inch = 1440
    if len(data) >= 22 and struct.unpack_from("<I", data, 0)[0] == PLACEABLE_KEY:
        left, top, right, bottom, inch = struct.unpack_from("<hhhhH", data, 6)
        bbox = (left, top, right, bottom)
        off = 22
    header_words = struct.unpack_from("<H", data, off + 2)[0]
    off += header_words * 2

    objects: list[object | None] = []
    st = _State()
    win_org, win_ext = (0, 0), None
    out: list[str] = []
    texts: list[tuple[float, float, str]] = []

    def add_obj(o):
        for i, slot in enumerate(objects):
            if slot is None:
                objects[i] = o
                return
        objects.append(o)

    while off + 6 <= len(data):
        size, fn = struct.unpack_from("<IH", data, off)
        if size < 3:
            break
        p = off + 6
        rec_end = off + size * 2
        off = rec_end
        if fn == 0x0000:
            break
        if fn == 0x020B:  # SetWindowOrg
            y, x = struct.unpack_from("<hh", data, p)
            win_org = (x, y)
        elif fn == 0x020C:  # SetWindowExt
            y, x = struct.unpack_from("<hh", data, p)
            win_ext = (x, y)
        elif fn == 0x02FB:  # CreateFontIndirect
            h, _w, esc, _ori, weight, italic = struct.unpack_from("<hhhhhB", data, p)
            face = data[p + 18:min(p + 50, rec_end)].split(b"\0")[0].decode("latin-1")
            add_obj(_Font(face, abs(h), weight, bool(italic), esc))
        elif fn == 0x02FA:  # CreatePenIndirect
            style, w = struct.unpack_from("<Hh", data, p)
            color = struct.unpack_from("<I", data, p + 6)[0]
            add_obj(_Pen(max(w, 1), _color(color), (style & 0xF) != 5))
        elif fn == 0x02FC:  # CreateBrushIndirect
            style, color = struct.unpack_from("<HI", data, p)
            add_obj(_Brush(_color(color), style != 1))
        elif fn == 0x01F0:  # DeleteObject
            i = struct.unpack_from("<H", data, p)[0]
            if i < len(objects):
                objects[i] = None
        elif fn == 0x012D:  # SelectObject
            i = struct.unpack_from("<H", data, p)[0]
            o = objects[i] if i < len(objects) else None
            if isinstance(o, _Font):
                st.font = o
            elif isinstance(o, _Pen):
                st.pen = o
            elif isinstance(o, _Brush):
                st.brush = o
        elif fn == 0x0209:  # SetTextColor
            st.text_color = _color(struct.unpack_from("<I", data, p)[0])
        elif fn == 0x012E:  # SetTextAlign
            st.align = struct.unpack_from("<H", data, p)[0]
        elif fn == 0x0214:  # MoveTo
            y, x = struct.unpack_from("<hh", data, p)
            st.pos = (x, y)
        elif fn == 0x0213:  # LineTo
            y, x = struct.unpack_from("<hh", data, p)
            if st.pen.visible:
                out.append(f'<line x1="{st.pos[0]}" y1="{st.pos[1]}" x2="{x}" y2="{y}" '
                           f'stroke="{st.pen.color}" stroke-width="{st.pen.width}"/>')
            st.pos = (x, y)
        elif fn in (0x0324, 0x0325):  # Polygon / Polyline
            n = struct.unpack_from("<h", data, p)[0]
            pts = struct.unpack_from(f"<{2 * n}h", data, p + 2)
            pts_s = " ".join(f"{pts[i]},{pts[i + 1]}" for i in range(0, 2 * n, 2))
            if fn == 0x0324:
                fill = st.brush.color if st.brush.visible else "none"
                stroke = f'stroke="{st.pen.color}" stroke-width="{st.pen.width}"' if st.pen.visible else 'stroke="none"'
                out.append(f'<polygon points="{pts_s}" fill="{fill}" {stroke}/>')
            elif st.pen.visible:
                out.append(f'<polyline points="{pts_s}" fill="none" stroke="{st.pen.color}" stroke-width="{st.pen.width}"/>')
        elif fn in (0x0A32, 0x0521):  # ExtTextOut / TextOut
            if fn == 0x0A32:
                y, x, n, opts = struct.unpack_from("<hhhH", data, p)
                q = p + 8 + (8 if opts & 0x0006 else 0)
                raw = data[q:q + n]
                q += n + (n & 1)
                dx = list(struct.unpack_from(f"<{n}h", data, q)) if q + 2 * n <= rec_end else []
            else:
                n = struct.unpack_from("<h", data, p)[0]
                raw = data[p + 2:p + 2 + n]
                q = p + 2 + n + (n & 1)
                y, x = struct.unpack_from("<hh", data, q)
                dx = []
            font = st.font or _Font("Times New Roman", 12, 400, False)
            s = _decode(raw, font.face)
            if st.align & 0x01:  # TA_UPDATECP: draw at the current position, then advance it
                x, y = st.pos
                advance = sum(dx) if dx else round(len(raw) * font.size * 0.5)
                st.pos = (x + advance, y)
            if not s.strip():
                continue
            texts.append((x, y, s))
            xs = [x]
            for d in dx[:-1]:
                xs.append(xs[-1] + d)
            va = st.align & 0x18
            baseline = {0x18: "alphabetic", 0x08: "text-after-edge"}.get(va, "text-before-edge")
            ha = st.align & 0x06
            anchor = {0x06: "middle", 0x02: "end"}.get(ha, "start")
            family = {"symbol": "'Symbol','Times New Roman',serif", "mt extra": "'Times New Roman',serif"}.get(
                font.face.lower(), f"'{font.face}','Times New Roman',serif")
            attrs = [f'font-family="{escape(family)}"', f'font-size="{font.size}"', f'fill="{st.text_color}"',
                     f'dominant-baseline="{baseline}"']
            if font.italic:
                attrs.append('font-style="italic"')
            if font.weight >= 600:
                attrs.append('font-weight="bold"')
            if anchor != "start":
                attrs.append(f'text-anchor="{anchor}"')
            # Per-glyph x positions keep MathType's exact spacing.
            xattr = " ".join(str(v) for v in xs[:len(s)]) if len(xs) >= len(s) and dx else str(x)
            out.append(f'<text x="{xattr}" y="{y}" {" ".join(attrs)} xml:space="preserve">{escape(s)}</text>')

    if win_ext:
        vx, vy, vw, vh = win_org[0], win_org[1], win_ext[0], win_ext[1]
    elif bbox:
        vx, vy, vw, vh = bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]
    else:
        vx, vy, vw, vh = 0, 0, 1000, 300
    if bbox:
        w_pt = abs(bbox[2] - bbox[0]) / inch * 72
        h_pt = abs(bbox[3] - bbox[1]) / inch * 72
    else:
        w_pt, h_pt = abs(vw) / 20, abs(vh) / 20
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx} {vy} {vw} {vh}" '
           f'width="{w_pt:.2f}pt" height="{h_pt:.2f}pt">{"".join(out)}</svg>')
    texts.sort(key=lambda t: (t[0], t[1]))
    text = " ".join(t[2] for t in texts)
    return WmfResult(svg, text, w_pt, h_pt)
