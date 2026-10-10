"""TR 38.822 UE feature list: parsing and linking to spec clauses.

Two deterministic links, no inference:

* **parameter names** - feature rows carry the TS 38.331 field name and their
  components quote higher-layer parameters; spec text names the same
  parameters (in italics). A shared parameter links a clause to a feature.
* **work item** - 38.822 sections are titled by work item (``NR_IAB``,
  ``NR_IAB-Core``); a CR's "Work item code" (``NR_IAB-Core``) points to them.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from lxml import html

from .parse_docx import Clause, norm_text

_INDEX = re.compile(r"^\d+-\d+[a-z]?(?:-\d+[a-z]?)*$")
# higher-layer parameter / ASN.1 field style: foo-Bar, fooBar-r16, msgA-PUSCH-Config
_PARAM = re.compile(r"\b[a-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b|\b[a-z]+[A-Z][A-Za-z0-9]+\b")
_GENERIC = {"n/a", "na", "none", "fr1", "fr2", "fdd", "tdd"}


@dataclass
class Feature:
    index: str
    group: str
    components: str
    prerequisite: str
    field_name: str
    parent_ie: str
    mandatory: str
    note: str
    section: str  # 38.822 clause number, e.g. "5.1.12"
    section_title: str  # work item, e.g. "NR_IAB"
    layer: str  # "Layer-1" | "Layer-2/3" | "RF/RRM"
    release: str  # "Rel-16"
    terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def norm_term(t: str) -> str:
    """``seperateRACH-IAB-Support-r16`` -> ``seperaterach-iab-support``."""
    t = re.sub(r"-r\d+[a-z]?$", "", t.strip().strip(".,;:()[]'\""))
    return t.lower()


def norm_wi(t: str) -> str:
    t = re.sub(r"-(core|perf|demod|rf|rrm)\b", "", t.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", t.lower())


def wi_codes(raw: str) -> list[str]:
    """``"NR_MBS-Core, NR_feMIMO-Core"`` -> both codes."""
    return [w.strip() for w in re.split(r"[,;/]+", raw or "") if w.strip()]


def match_sections(wi_code: str, sections: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """38.822 sections (number, title, layer, release) for one work item code.

    Exact name matches win; otherwise the longest prefix relation (NR_pos_enh2 -> NR_pos_enh
    rather than NR_pos). ``NR_newRAT`` is the Rel-15 basic feature set.
    """
    a = norm_wi(wi_code)
    if len(a) < 4:
        return []
    if a == "nrnewrat":
        return [s for s in sections if s[3] == "Rel-15"]
    out = []
    for layer in sorted({s[2] for s in sections}):  # decide per layer: L1 and L2/3 sections are named differently
        cands = [s for s in sections if s[2] == layer]
        exact = [s for s in cands if norm_wi(s[1]) == a]
        if exact:
            out += exact
            continue
        best, picked = 0, []
        for s in cands:
            b = norm_wi(s[1])
            if len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
                score = min(len(a), len(b))
                if score > best:
                    best, picked = score, [s]
                elif score == best:
                    picked.append(s)
        out += picked
    return sorted(out)


def wi_matches(wi_code: str, section_title: str) -> bool:
    return bool(match_sections(wi_code, [("", section_title, "", "")]))


_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9-]{2,}[A-Z0-9]\b")
_COMMON = {"NOTE", "TABLE", "UE", "NR", "TS", "THE", "FR1", "FR2", "TDD", "FDD", "RRC"}


def acronyms(text: str) -> set[str]:
    """Distinctive physical-layer terms (PRACH, PUSCH, DM-RS, SSB ...) for relevance ranking."""
    return {a.replace("-", "") for a in _ACRONYM.findall(text)} - _COMMON


def _is_param(tok: str) -> bool:
    # 3GPP parameter names always carry an inner capital (prach-ConfigurationIndex, msgA-PUSCH);
    # this rejects ordinary hyphenated words such as "single-layer".
    return any(ch.isupper() for ch in tok[1:])


def params_in(text: str) -> set[str]:
    return {norm_term(m) for m in _PARAM.findall(text) if _is_param(m)} - _GENERIC


def _layer(title: str) -> str:
    t = title.lower()
    if "layer-1" in t or "layer 1" in t:
        return "Layer-1"
    if "layer-2" in t or "layer 2" in t:
        return "Layer-2/3"
    if "rf" in t or "rrm" in t:
        return "RF/RRM"
    return ""


def parse_feature_list(clauses: list[Clause]) -> list[Feature]:
    out: list[Feature] = []
    release, layer = "", ""
    for c in clauses:
        m = re.match(r"Release\s+(\d+)", c.title)
        if m and c.level == 1:
            release, layer = f"Rel-{m.group(1)}", ""
            continue
        lay = _layer(c.title)
        if lay and c.level == 2:
            layer = lay
        for b in c.blocks:
            if b.kind != "table":
                continue
            rows = html.fragment_fromstring(b.html).xpath(".//tr")
            if not rows:
                continue
            header = [norm_text(x.text_content()).lower() for x in rows[0].xpath("./th|./td")]
            if "index" not in header or not any("feature group" in h for h in header):
                continue
            col = {name: next((i for i, h in enumerate(header) if h.startswith(name)), None) for name in
                   ("index", "feature group", "components", "prerequisite", "field name", "parent ie", "note",
                    "mandatory")}
            for tr in rows[1:]:
                cells = [norm_text(x.text_content()) for x in tr.xpath("./th|./td")]
                # The first "Features" column is row-spanned: realign on the index cell.
                at = next((i for i, v in enumerate(cells) if _INDEX.match(v)), None)
                if at is None:
                    continue
                shift = at - col["index"]
                get = lambda k: (cells[col[k] + shift] if col[k] is not None and 0 <= col[k] + shift < len(cells) else "")  # noqa: E731
                f = Feature(get("index"), get("feature group"), get("components"), get("prerequisite"),
                            get("field name"), get("parent ie"), get("mandatory"), get("note"),
                            c.number, c.title, layer or lay, release)
                terms = {norm_term(x) for x in re.split(r"[\s,;/]+", f.field_name)
                         if _PARAM.fullmatch(x.strip(".,;")) and _is_param(x.strip(".,;"))}
                terms |= params_in(f.components)
                f.terms = sorted(t for t in terms if t and t not in _GENERIC)
                out.append(f)
    return out
