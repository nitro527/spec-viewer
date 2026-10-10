"""HTML-aware tokenising, alignment and diff rendering.

Blocks are HTML fragments. They are tokenised into tags, words, whitespace and
atomic tokens (equations, images, entities) so that diffs never split markup
and equations are compared as a whole.
"""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Sequence, TypeVar

_TOKEN_RE = re.compile(
    r'(?P<atom><span class="math (?:inline|display)">.*?</span>|<img\b[^>]*>)'
    r"|(?P<tag></?[a-zA-Z][^>]*>)"
    r"|(?P<ent>&#?\w+;)"
    r"|(?P<ws>\s+)"
    r"|(?P<word>\w+)"
    r"|(?P<punct>.)",
    re.S,
)


@dataclass(frozen=True)
class Token:
    kind: str  # atom | tag | ent | ws | word | punct
    text: str

    @property
    def is_content(self) -> bool:
        return self.kind != "tag"


def tokenize(fragment: str) -> list[Token]:
    return [Token(m.lastgroup, m.group(0)) for m in _TOKEN_RE.finditer(fragment)]


def _key(t: Token) -> str:
    return " " if t.kind == "ws" else t.text


def opcodes(a: Sequence[Token], b: Sequence[Token]):
    return SequenceMatcher(None, [_key(t) for t in a], [_key(t) for t in b], autojunk=False).get_opcodes()


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    sm = SequenceMatcher(None, a.split(), b.split(), autojunk=False)
    return sm.ratio()


T = TypeVar("T")


def align(old: Sequence[T], new: Sequence[T], key: Callable[[T], str], text: Callable[[T], str],
          threshold: float = 0.45) -> list[tuple[int | None, int | None]]:
    """Align two block sequences.

    Returns (old_index, new_index) pairs in document order; ``None`` on one side
    means a pure insertion or deletion. Equal blocks are matched by key; inside
    changed regions blocks are paired by text similarity so an edited paragraph
    shows up as a modification rather than delete+insert.
    """
    pairs: list[tuple[int | None, int | None]] = []
    sm = SequenceMatcher(None, [key(x) for x in old], [key(x) for x in new], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs.extend(zip(range(i1, i2), range(j1, j2)))
            continue
        olds, news = list(range(i1, i2)), list(range(j1, j2))
        # Greedy monotone pairing by similarity.
        oi = 0
        for j in news:
            best, best_s = None, threshold
            for k in range(oi, len(olds)):
                s = similarity(text(old[olds[k]]), text(new[j]))
                if s > best_s:
                    best, best_s = k, s
            if best is None:
                pairs.append((None, j))
                continue
            for k in range(oi, best):
                pairs.append((olds[k], None))
            pairs.append((olds[best], j))
            oi = best + 1
        for k in range(oi, len(olds)):
            pairs.append((olds[k], None))
    return pairs


# ---------------------------------------------------------------- rendering


def _wrap(tag: str, attrs: str, toks: list[Token]) -> str:
    """Wrap content tokens, closing/reopening around markup so nesting stays valid."""
    out, run = [], []

    def flush():
        if run:
            body = "".join(t.text for t in run)
            out.append(f"<{tag}{attrs}>{body}</{tag}>" if body.strip() else body)
            run.clear()

    for t in toks:
        if t.kind == "tag":
            flush()
            out.append(t.text)
        else:
            run.append(t)
    flush()
    return "".join(out)


def render_annotated(tokens: Sequence[Token], origins: Sequence[int], labels: Sequence[str], base: int = 0) -> str:
    """Render tokens with runs of non-base origin wrapped in ``<span class="vc" data-v>``."""
    out: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        o = origins[i]
        if tokens[i].kind == "tag" or o <= base:
            out.append(tokens[i].text)
            i += 1
            continue
        j = i
        while j < n and tokens[j].kind != "tag" and (origins[j] == o or (tokens[j].kind == "ws" and j + 1 < n and origins[j + 1] == o)):
            j += 1
        out.append(_wrap("span", f' class="vc" data-v="{labels[o]}"', list(tokens[i:j])))
        i = j
    return "".join(out)


def render_diff(old_html: str, new_html: str) -> str:
    """Inline word diff of two fragments using <del>/<ins>; markup follows the new side."""
    a, b = tokenize(old_html), tokenize(new_html)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes(a, b):
        if tag == "equal":
            out.extend(t.text for t in b[j1:j2])
            continue
        if tag in ("delete", "replace"):
            dels = [t for t in a[i1:i2] if t.is_content]
            if dels:
                out.append(_wrap("del", "", dels))
        if tag in ("insert", "replace"):
            out.append(_wrap("ins", "", list(b[j1:j2])))
    return "".join(out)


def strip_tags(fragment: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))
