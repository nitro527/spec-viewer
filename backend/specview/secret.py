"""API keys at rest: encrypted with Windows DPAPI for the signed-in Windows user.

Another user on the PC, or someone who copies library.sqlite to another machine, cannot read them. On other
systems (development on Linux/macOS) they are stored as they are, as before."""
from __future__ import annotations

import base64
import os

PREFIX = "dpapi:"
_ENTROPY = b"specview api key"  # ties the blob to this app: other DPAPI users of the account can't just read it


def available() -> bool:
    return os.name == "nt"


def protect(plain: str) -> str:
    if not plain or not available():
        return plain
    return PREFIX + base64.b64encode(_crypt(plain.encode("utf-8"), protect=True)).decode("ascii")


def unprotect(stored: str | None) -> str | None:
    """The key, or "" when it can't be decrypted (the library was copied from another user or PC)."""
    if not stored or not stored.startswith(PREFIX):
        return stored
    if not available():
        return ""
    try:
        return _crypt(base64.b64decode(stored[len(PREFIX):]), protect=False).decode("utf-8")
    except (OSError, ValueError):
        return ""


def _crypt(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> tuple[Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    src, _keep = blob(data)
    ent, _keep2 = blob(_ENTROPY)
    out = Blob()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
