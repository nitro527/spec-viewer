"""Version-history ("blame") engine.

Walks the versions of one specification in order and tracks, for every token
of every block, the version that introduced it. The result for each version is
the list of clauses with annotated block HTML plus change metadata, which the
viewer renders as colour-coded highlights.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .diff import Token, align, best_similar, opcodes, render_annotated, tokenize
from .parse_docx import norm_text


@dataclass
class InBlock:
    kind: str
    style: str
    raw_hash: str
    html: str
    text: str


@dataclass
class InClause:
    key: str
    number: str
    title: str
    level: int
    blocks: list[InBlock]


@dataclass
class _BlockState:
    src: InBlock
    tokens: list[Token]
    origins: list[int]
    introduced: int
    changed: int | None
    history: list[tuple]  # (version index, change[, moved-from clause])
    html: str | None = None  # rendered once: an unchanged block is the same state in every later version


@dataclass
class _ClauseState:
    title: str
    blocks: list[_BlockState]
    introduced: int
    last_changed: int | None
    history: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class OutBlock:
    kind: str
    style: str
    raw_hash: str
    html: str  # annotated
    introduced: str
    changed: str | None
    history: list[tuple[str, str]]


@dataclass
class OutClause:
    key: str
    number: str
    title: str
    level: int
    status: str  # base | added | modified | unchanged
    introduced: str
    last_changed: str | None
    history: list[tuple[str, str]]
    blocks: list[OutBlock]
    deleted: list[dict]  # [{"after": ord, "html": str, "version": label}] removed in this version


@dataclass
class VersionResult:
    index: int
    label: str
    clauses: list[OutClause]
    removed_clauses: list[dict]  # [{"key","number","title"}] removed in this version


def _fresh(b: InBlock, vi: int) -> _BlockState:
    toks = tokenize(b.html)
    return _BlockState(b, toks, [vi] * len(toks), vi, vi if vi else None, [(vi, "added")] if vi else [])


def _evolve(prev: _BlockState, b: InBlock, vi: int) -> _BlockState:
    toks = tokenize(b.html)
    origins = [vi] * len(toks)
    for tag, i1, i2, j1, j2 in opcodes(prev.tokens, toks):
        if tag == "equal":
            origins[j1:j2] = prev.origins[i1:i2]
    return _BlockState(b, toks, origins, prev.introduced, vi, prev.history + [(vi, "modified")])


def _html(b: _BlockState, labels: Sequence[str]) -> str:
    if b.html is None:  # states are never changed after they are made, so the rendering stays valid
        b.html = render_annotated(b.tokens, b.origins, labels)
    return b.html


_MOVE_MIN_CHARS = 40  # short blocks ("where", "-") match by accident
_MOVE_SIMILARITY = 0.8


def _resolve_moves(added: list, gone: list[dict], vi: int) -> None:
    """Turn delete-here + insert-there pairs into moves that keep the text's origin versions."""
    by_hash: dict[str, list[dict]] = {}
    for g in gone:
        by_hash.setdefault(g["state"].src.raw_hash, []).append(g)
    long_gone = [g for g in gone if len(g["state"].src.text) >= _MOVE_MIN_CHARS]
    words = {id(g): g["state"].src.text.split() for g in long_gone}
    for cs, idx, b, number in added:
        if len(b.text) < _MOVE_MIN_CHARS:
            continue
        cand = next((g for g in by_hash.get(b.raw_hash, []) if not g.get("used")), None)
        if cand is None:
            cand, _ = best_similar(((g, words[id(g)]) for g in long_gone if not g.get("used")), b.text.split(),
                                   _MOVE_SIMILARITY, strict="first")
        if cand is None:
            continue
        cand["used"] = True
        src = cand["state"]
        moved = src if src.src.raw_hash == b.raw_hash else _evolve(src, b, vi)
        cs.blocks[idx] = _BlockState(b, moved.tokens, moved.origins, src.introduced, vi,
                                     src.history + [(vi, "moved", cand["number"])])
        if cand["entry"] is not None:
            cand["entry"]["moved_to"] = number


def _match_renumbered(new: Sequence[InClause], state: dict[str, _ClauseState]) -> dict[str, str]:
    """Map new clause keys that disappeared/appeared to an old key with the same title."""
    new_keys = {c.key for c in new}
    orphans = {norm_text(s.title).lower(): k for k, s in state.items() if k not in new_keys}
    out = {}
    for c in new:
        if c.key not in state:
            old = orphans.pop(norm_text(c.title).lower(), None)
            if old and c.title:
                out[c.key] = old
    return out


def compute(versions: Sequence[str], clauses_of: Iterable[Sequence[InClause]]) -> list[VersionResult]:
    """``versions`` are labels in ascending order; ``clauses_of`` yields each version's clauses."""
    labels = list(versions)
    lbl = lambda i: labels[i] if i is not None else None  # noqa: E731
    state: dict[str, _ClauseState] = {}
    results: list[VersionResult] = []

    for vi, clauses in enumerate(clauses_of):
        renum = _match_renumbered(clauses, state) if vi else {}
        new_state: dict[str, _ClauseState] = {}
        pending: list[tuple[InClause, _ClauseState, str, list[dict]]] = []
        used_old: set[str] = set()
        # Candidates for move detection within this version.
        added: list[tuple[_ClauseState, int, InBlock, str]] = []  # (clause state, block index, block, clause number)
        gone: list[dict] = []  # {"state", "number", "entry"} for blocks deleted anywhere in this version

        for c in clauses:
            old_key = c.key if c.key in state else renum.get(c.key)
            prev = state.get(old_key) if old_key else None
            deleted: list[dict] = []
            if prev is None:
                blocks = [_fresh(b, vi) for b in c.blocks]
                cs = _ClauseState(c.title, blocks, vi, vi if vi else None, [(vi, "added")] if vi else [])
                status = "added" if vi else "base"
                if vi:
                    added.extend((cs, i, b, c.number) for i, b in enumerate(c.blocks))
            else:
                used_old.add(old_key)
                pairs = align(prev.blocks, c.blocks, key=lambda x: x.src.raw_hash if isinstance(x, _BlockState) else x.raw_hash,
                              text=lambda x: x.src.text if isinstance(x, _BlockState) else x.text)
                blocks, changed = [], False
                cs = _ClauseState(c.title, blocks, prev.introduced, None, [])
                for oi, ni in pairs:
                    if ni is None:
                        entry = {"after": len(blocks) - 1, "html": prev.blocks[oi].src.html, "version": labels[vi]}
                        deleted.append(entry)
                        gone.append({"state": prev.blocks[oi], "number": c.number, "entry": entry})
                        changed = True
                    elif oi is None:
                        added.append((cs, len(blocks), c.blocks[ni], c.number))
                        blocks.append(_fresh(c.blocks[ni], vi))
                        changed = True
                    elif prev.blocks[oi].src.raw_hash == c.blocks[ni].raw_hash:
                        blocks.append(prev.blocks[oi])
                    else:
                        blocks.append(_evolve(prev.blocks[oi], c.blocks[ni], vi))
                        changed = True
                if norm_text(prev.title) != norm_text(c.title):
                    changed = True
                cs.history = prev.history + ([(vi, "modified")] if changed else [])
                cs.last_changed = vi if changed else prev.last_changed
                status = "modified" if changed else "unchanged"
            new_state[c.key] = cs
            pending.append((c, cs, status, deleted))

        removed = [{"key": k, "title": s.title} for k, s in state.items() if k not in used_old and k not in new_state]
        for k in (r["key"] for r in removed):
            gone.extend({"state": b, "number": k, "entry": None} for b in state[k].blocks)
        if vi and added and gone:
            _resolve_moves(added, gone, vi)

        out_clauses = [OutClause(
            key=c.key, number=c.number, title=c.title, level=c.level, status=status,
            introduced=labels[cs.introduced], last_changed=lbl(cs.last_changed),
            history=[(labels[i], t) for i, t in cs.history],
            blocks=[OutBlock(b.src.kind, b.src.style, b.src.raw_hash, _html(b, labels),
                             labels[b.introduced], lbl(b.changed),
                             [(labels[h[0]], *h[1:]) for h in b.history]) for b in cs.blocks],
            deleted=deleted,
        ) for c, cs, status, deleted in pending]
        results.append(VersionResult(vi, labels[vi], out_clauses, removed))
        state = new_state
    return results
