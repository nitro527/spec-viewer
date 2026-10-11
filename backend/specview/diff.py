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


_CHUNK_END = ("</tr", "</p", "</li", "</h", "<br")
_BIG = 4000  # tokens left after trimming above which a word-level diff gets slow (it is close to quadratic)


def _chunks(keys: list[str], lo: int, hi: int) -> list[tuple[int, int]]:
    """Split keys[lo:hi] after row / paragraph ends: a huge table becomes a few hundred comparable rows."""
    out, start = [], lo
    for i in range(lo, hi):
        if keys[i].startswith(_CHUNK_END):
            out.append((start, i + 1))
            start = i + 1
    if start < hi:
        out.append((start, hi))
    return out


def _patience(a: list[str], b: list[str]) -> list[tuple[int, int, int]]:
    """Matching blocks (i, j, size) of two long row lists, like difflib's but near-linear: rows that occur once
    on each side anchor the match (patience diff) and only the short gaps between anchors go to difflib. Big
    tables repeat rows a lot, which is what makes difflib quadratic on them."""
    import bisect
    from collections import Counter

    blocks: list[tuple[int, int, int]] = []
    # work items in document order, last first: ("gap", alo, ahi, blo, bhi) or ("match", i, j, size); a stack
    # instead of recursion, since anchors can nest deeper than Python's recursion limit
    todo: list[tuple] = [("gap", 0, len(a), 0, len(b))]
    while todo:
        kind, alo, ahi, blo, bhi = todo.pop()
        if kind == "match":
            blocks.append((alo, ahi, blo))
            continue
        while alo < ahi and blo < bhi and a[alo] == b[blo]:
            blocks.append((alo, blo, 1))
            alo, blo = alo + 1, blo + 1
        tail = 0
        while ahi - tail > alo and bhi - tail > blo and a[ahi - tail - 1] == b[bhi - tail - 1]:
            tail += 1
        later: list[tuple] = []  # in document order
        ahi, bhi = ahi - tail, bhi - tail
        if alo < ahi and blo < bhi:
            ca, cb = Counter(a[alo:ahi]), Counter(b[blo:bhi])
            at = {b[j]: j for j in range(blo, bhi) if cb[b[j]] == 1 and ca[b[j]] == 1}
            pairs = [(i, at[a[i]]) for i in range(alo, ahi) if a[i] in at]
            if pairs:
                # longest run of anchors in order on both sides (patience sorting)
                tops: list[int] = []
                top_at: list[int] = []
                prev = [-1] * len(pairs)
                for n, (_, j) in enumerate(pairs):
                    k = bisect.bisect_left(tops, j)
                    if k:
                        prev[n] = top_at[k - 1]
                    if k == len(tops):
                        tops.append(j)
                        top_at.append(n)
                    else:
                        tops[k], top_at[k] = j, n
                chain, n = [], top_at[-1]
                while n >= 0:
                    chain.append(pairs[n])
                    n = prev[n]
                i0, j0 = alo, blo
                for i, j in reversed(chain):
                    later += [("gap", i0, i, j0, j), ("match", i, j, 1, 0)]
                    i0, j0 = i + 1, j + 1
                later.append(("gap", i0, ahi, j0, bhi))
            elif (ahi - alo) * (bhi - blo) <= 250_000:
                sm = SequenceMatcher(None, a[alo:ahi], b[blo:bhi], autojunk=False)
                later += [("match", i + alo, j + blo, k, 0) for i, j, k in sm.get_matching_blocks() if k]
        if tail:
            later.append(("match", ahi, bhi, tail, 0))
        todo.extend(reversed(later))

    merged: list[tuple[int, int, int]] = []
    for i, j, k in blocks:
        if merged and merged[-1][0] + merged[-1][2] == i and merged[-1][1] + merged[-1][2] == j:
            merged[-1] = (merged[-1][0], merged[-1][1], merged[-1][2] + k)
        else:
            merged.append((i, j, k))
    return merged


def _block_opcodes(blocks: list[tuple[int, int, int]], la: int, lb: int) -> list[tuple]:
    out, i, j = [], 0, 0
    for ai, bj, k in blocks + [(la, lb, 0)]:
        if i < ai or j < bj:
            out.append(("replace" if i < ai and j < bj else "delete" if i < ai else "insert", i, ai, j, bj))
        if k:
            out.append(("equal", ai, ai + k, bj, bj + k))
        i, j = ai + k, bj + k
    return out


def _diff(ka: list[str], kb: list[str], a0: int, a1: int, b0: int, b1: int) -> list[tuple]:
    """Opcodes for ka[a0:a1] vs kb[b0:b1] in absolute positions; rows first when the span is big."""
    if (a1 - a0) + (b1 - b0) <= _BIG:
        sm = SequenceMatcher(None, ka[a0:a1], kb[b0:b1], autojunk=(a1 - a0) + (b1 - b0) > 3000)
        return [(t, i1 + a0, i2 + a0, j1 + b0, j2 + b0) for t, i1, i2, j1, j2 in sm.get_opcodes()]
    ca, cb = _chunks(ka, a0, a1), _chunks(kb, b0, b1)
    # NUL between tokens: rows whose words split differently ("Rel 15" / "Rel15") must not look equal
    rows_a, rows_b = ["\0".join(ka[i:j]) for i, j in ca], ["\0".join(kb[i:j]) for i, j in cb]
    out = []
    for t, i1, i2, j1, j2 in _block_opcodes(_patience(rows_a, rows_b), len(rows_a), len(rows_b)):
        sa = (ca[i1][0], ca[i2 - 1][1]) if i2 > i1 else (ca[i1][0] if i1 < len(ca) else a1,) * 2
        sb = (cb[j1][0], cb[j2 - 1][1]) if j2 > j1 else (cb[j1][0] if j1 < len(cb) else b1,) * 2
        if t == "replace" and (sa[1] - sa[0]) + (sb[1] - sb[0]) <= _BIG:
            out += _diff(ka, kb, sa[0], sa[1], sb[0], sb[1])  # the changed rows, word by word
        else:
            out.append((t, sa[0], sa[1], sb[0], sb[1]))
    return out


def opcodes(a: Sequence[Token], b: Sequence[Token]):
    """difflib opcodes over token keys.

    Whitespace is left out of the comparison (it is half of all tokens and the most frequent one, which
    is what makes difflib slow); inside equal runs, whitespace present on one side only becomes its own
    small edit. Edits are usually local, so the common prefix/suffix is trimmed first. What is left is
    compared word by word when small; a big remainder (conformance-spec tables run to tens of thousands of
    tokens) is compared row by row first and only the changed rows word by word, since a word-level diff
    over the whole of it is close to quadratic.
    """
    ia = [i for i, t in enumerate(a) if t.kind != "ws"]
    ib = [i for i, t in enumerate(b) if t.kind != "ws"]
    if not ia or not ib:  # one side is only whitespace (or empty): nothing to align
        if not a and not b:
            return []
        if not ia and not ib and len(a) == len(b):
            return [("equal", 0, len(a), 0, len(b))]
        return [("replace" if a and b else "delete" if a else "insert", 0, len(a), 0, len(b))]
    ka, kb = [a[i].text for i in ia], [b[i].text for i in ib]
    lo, hi_a, hi_b = 0, len(ka), len(kb)
    while lo < hi_a and lo < hi_b and ka[lo] == kb[lo]:
        lo += 1
    while hi_a > lo and hi_b > lo and ka[hi_a - 1] == kb[hi_b - 1]:
        hi_a -= 1
        hi_b -= 1
    ops = [("equal", 0, lo, 0, lo)] if lo else []
    ops += _diff(ka, kb, lo, hi_a, lo, hi_b)
    if hi_a < len(ka):
        ops.append(("equal", hi_a, len(ka), hi_b, len(kb)))

    out: list[list] = []

    def emit(i1: int, i2: int, j1: int, j2: int, equal: bool) -> None:
        if i1 == i2 and j1 == j2:
            return
        if out and (out[-1][0] == "equal") == equal and out[-1][2] == i1 and out[-1][4] == j1:
            out[-1][2], out[-1][4] = i2, j2
        else:
            out.append(["equal" if equal else "", i1, i2, j1, j2])

    def pos(idx: list[int], n: int, k: int) -> int:  # token position of kept token k; leading whitespace from 0
        return 0 if k == 0 else (idx[k] if k < len(idx) else n)

    for t, k1, k2, l1, l2 in ops:
        a1, a2, b1, b2 = pos(ia, len(a), k1), pos(ia, len(a), k2), pos(ib, len(b), l1), pos(ib, len(b), l2)
        if t != "equal" or k2 - k1 != l2 - l1:
            emit(a1, a2, b1, b2, False)
            continue
        i, j = a1, b1
        for n in range(k2 - k1 + 1):  # each kept token, then the whitespace up to the next one
            ai = ia[k1 + n] if n < k2 - k1 else a2
            bj = ib[l1 + n] if n < l2 - l1 else b2
            emit(i, ai, j, bj, ai - i == bj - j)  # whitespace on both sides or on neither
            if n < k2 - k1:
                emit(ai, ai + 1, bj, bj + 1, True)
                i, j = ai + 1, bj + 1
    for op in out:
        if not op[0]:
            op[0] = "replace" if op[1] < op[2] and op[3] < op[4] else "delete" if op[1] < op[2] else "insert"
    return [tuple(op) for op in out]


def word_similarity(wa: Sequence[str], wb: Sequence[str], floor: float = 0.0) -> float:
    """SequenceMatcher ratio over words, or 0.0 as soon as a cheap upper bound shows it stays below ``floor``
    (length first, then the multiset bound): ratio itself is close to quadratic and is asked a lot."""
    if not wa and not wb:
        return 1.0
    n = len(wa) + len(wb)
    if 2 * min(len(wa), len(wb)) < floor * n:
        return 0.0
    lo, hi_a, hi_b = 0, len(wa), len(wb)  # common ends match anyway: compare only the middle
    while lo < hi_a and lo < hi_b and wa[lo] == wb[lo]:
        lo += 1
    while hi_a > lo and hi_b > lo and wa[hi_a - 1] == wb[hi_b - 1]:
        hi_a -= 1
        hi_b -= 1
    same = lo + len(wa) - hi_a
    sm = SequenceMatcher(None, wa[lo:hi_a], wb[lo:hi_b], autojunk=False)
    if floor and 2 * (same + sm.quick_ratio() * (hi_a - lo + hi_b - lo) / 2) < floor * n:
        return 0.0
    if (hi_a - lo) + (hi_b - lo) > 4000:  # long tables: words matched in order, anchored on unique ones
        return (2 * same + 2 * sum(k for _, _, k in _patience(list(wa[lo:hi_a]), list(wb[lo:hi_b])))) / n
    return (2 * same + 2 * sum(m.size for m in sm.get_matching_blocks())) / n


def similarity(a: str, b: str, floor: float = 0.0) -> float:
    return word_similarity(a.split(), b.split(), floor)


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
        old_words = [text(old[i]).split() for i in olds]
        # Greedy monotone pairing by similarity.
        oi = 0
        for j in news:
            best, best_s = None, threshold
            words = text(new[j]).split()
            for k in range(oi, len(olds)):
                s = word_similarity(old_words[k], words, best_s)
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
            i, j = 0, len(run)  # whitespace at either end stays outside the mark
            while i < j and run[i].kind == "ws":
                i += 1
            while j > i and run[j - 1].kind == "ws":
                j -= 1
            body = "".join(t.text for t in run[i:j])
            out.append("".join(t.text for t in run[:i]) + (f"<{tag}{attrs}>{body}</{tag}>" if body.strip() else body)
                       + "".join(t.text for t in run[j:]))
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
