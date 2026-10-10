"""Chairman's notes (WG agreements) - skeleton.

RAN1 chair notes live in each meeting's folder, e.g.
``https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_100b_e/Inbox/Chair_notes/``.
The plan:

1. map a CR's WG Tdoc (``R1-2005128``) to its meeting via the Tdoc list
   (``.../TSGR1_xxx/Docs/`` or the meeting's Tdoc_List.xlsx);
2. download that meeting's final chair notes;
3. split them into agreement blocks ("Agreement:", "Conclusion:", "Working assumption:");
4. pick the agreements that mention the CR's parameters/clauses, or let the LLM
   rank candidates against the diff.

Only step-agnostic helpers exist so far.
"""
from __future__ import annotations

import re

RAN1 = "https://www.3gpp.org/ftp/tsg_ran/WG1_RL1"
_AGREEMENT = re.compile(r"^\s*(Agreements?|Conclusions?|Working assumptions?)\s*:?\s*$", re.I | re.M)


def meeting_folder(meeting: str) -> str | None:
    """``"RAN1#100b-e"`` -> ``.../WG1_RL1/TSGR1_100b_e``."""
    m = re.search(r"RAN1\s*#\s*(\d+)(bis|b)?(-e)?", meeting, re.I)
    if not m:
        return None
    return f"{RAN1}/TSGR1_{m.group(1)}{'b' if m.group(2) else ''}{'_e' if m.group(3) else ''}"


def split_agreements(text: str) -> list[str]:
    """Split chair-notes text into agreement blocks."""
    starts = [m.start() for m in _AGREEMENT.finditer(text)]
    return [text[a:b].strip() for a, b in zip(starts, starts[1:] + [len(text)])]
