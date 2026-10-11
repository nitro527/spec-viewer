"""3GPP version code handling.

Archive file names encode the version as ``<spec digits>-<code>.zip``, e.g.
``38211-g10.zip`` -> 16.1.0. Each field is one character in ``0-9a-z``
(a=10 ... z=35). Versions with a field >= 36 use two decimal digits per field
(``38211-361000.zip`` -> 36.10.0).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache, total_ordering

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    editorial: int

    @classmethod
    def parse(cls, text: str) -> "Version":
        return _parse(cls, text)

    @classmethod
    def from_code(cls, code: str) -> "Version":
        code = code.lower()
        if len(code) == 3 and all(c in _DIGITS for c in code):
            return cls(*(_DIGITS.index(c) for c in code))
        if len(code) == 6 and code.isdigit():
            return cls(int(code[0:2]), int(code[2:4]), int(code[4:6]))
        raise ValueError(f"not a 3GPP version code: {code!r}")

    @property
    def code(self) -> str:
        if max(self.major, self.minor, self.editorial) < 36:
            return "".join(_DIGITS[x] for x in (self.major, self.minor, self.editorial))
        return f"{self.major:02d}{self.minor:02d}{self.editorial:02d}"

    @property
    def release(self) -> int:
        # For the 38 (and 36) series the major version equals the release.
        return self.major

    @property
    def sort_key(self) -> int:
        return self.major * 1_000_000 + self.minor * 1_000 + self.editorial

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.editorial}"

    def __lt__(self, other: "Version") -> bool:
        return self.sort_key < other.sort_key


# Multi-part specs carry the part number: 38101-1-k10.zip is TS 38.101-1 v20.1.0.
_FILE_RE = re.compile(r"^(\d{4,6}(?:-\d{1,2}){0,2})-([0-9a-z]{3}|\d{6})\.(zip|docx?)$", re.I)  # 38716-01-01-...


def parse_archive_name(name: str) -> tuple[str, Version] | None:
    """``38211-g10.zip`` -> (``"38.211"``, Version(16,1,0)), ``38101-1-k10.zip`` -> ``"38.101-1"``; None if no match."""
    m = _FILE_RE.match(name)
    if not m:
        return None
    digits, code = m.group(1), m.group(2)
    return f"{digits[:2]}.{digits[2:]}", Version.from_code(code)


def spec_digits(spec: str) -> str:
    """``"38.211"`` -> ``"38211"``."""
    return spec.replace(".", "")


def series_of(spec: str) -> str:
    return spec.split(".")[0]


@lru_cache(maxsize=8192)
def _parse(cls, text: str) -> Version:
    """Versions are immutable and the same few hundred strings are parsed over and over (CR lists, sorting)."""
    m = _VERSION.fullmatch(text.strip())
    if not m:
        raise ValueError(f"not a version: {text!r}")
    return cls(*(int(g) for g in m.groups()))
