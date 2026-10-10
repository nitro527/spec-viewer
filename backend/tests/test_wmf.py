import struct

from specview.wmf import PLACEABLE_KEY, wmf_to_svg


def _rec(fn: int, payload: bytes) -> bytes:
    if len(payload) % 2:
        payload += b"\0"
    return struct.pack("<IH", 3 + len(payload) // 2, fn) + payload


def _font(face: str, height: int, italic: bool = False) -> bytes:
    name = face.encode("latin-1").ljust(32, b"\0")
    return _rec(0x02FB, struct.pack("<hhhhhBBBBBBBB", -height, 0, 0, 0, 400, int(italic), 0, 0, 0, 0, 0, 0, 0) + name)


def _text(x: int, y: int, s: bytes) -> bytes:
    return _rec(0x0A32, struct.pack("<hhhH", y, x, len(s), 0) + s + (b"\0" if len(s) % 2 else b""))


def _wmf(records: bytes, bbox=(0, 0, 1440, 360)) -> bytes:
    placeable = struct.pack("<IHhhhhHI", PLACEABLE_KEY, 0, *bbox, 1440, 0)
    placeable += struct.pack("<H", 0)  # checksum (unused by the converter)
    header = struct.pack("<HHHIHIH", 1, 9, 0x300, 0, 0, 0, 0)
    return placeable + header + records + _rec(0x0000, b"")


def test_text_fonts_and_symbol_mapping():
    recs = (
        _rec(0x020B, struct.pack("<hh", 0, 0)) + _rec(0x020C, struct.pack("<hh", 360, 1440))
        + _font("Times New Roman", 240, italic=True) + _rec(0x012D, struct.pack("<H", 0))
        + _text(10, 200, b"n")
        + _font("Symbol", 240) + _rec(0x012D, struct.pack("<H", 1))
        + _text(200, 200, b"\xa3a")  # "≤α" in the Symbol font
        + _rec(0x0214, struct.pack("<hh", 300, 0)) + _rec(0x0213, struct.pack("<hh", 300, 1000))
    )
    r = wmf_to_svg(_wmf(recs))
    assert r.text == "n ≤α"
    assert 'font-style="italic"' in r.svg and "<line" in r.svg
    assert 'viewBox="0 0 1440 360"' in r.svg
    assert abs(r.width_pt - 72) < 0.01 and abs(r.height_pt - 18) < 0.01


def test_update_current_position_alignment():
    recs = (
        _rec(0x020C, struct.pack("<hh", 360, 1440))
        + _font("Times New Roman", 240) + _rec(0x012D, struct.pack("<H", 0))
        + _rec(0x012E, struct.pack("<H", 0x18 | 0x01))  # baseline + TA_UPDATECP
        + _rec(0x0214, struct.pack("<hh", 250, 100))  # MoveTo(100, 250)
        + _text(0, 0, b"a")
    )
    svg = wmf_to_svg(_wmf(recs)).svg
    assert '<text x="100" y="250"' in svg
