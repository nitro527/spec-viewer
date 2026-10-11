// Side panels: table of contents (left) and change information (right).
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, cmpVersion, crKey, crTouches, tdocUrl, type CR, type CRAnalysis, type Note, type RefHit, type TocEntry } from "../api";
import { askAboutParagraph } from "../ask";
import { openCRDoc } from "./DocWindow";
import { useCRAnalyses } from "./CRAnalysis";
import type { VersionColor } from "../colors";
import { AnnotationPanel } from "./AnnotationPanel";
import { ParagraphHistory } from "./ParagraphHistory";
import { FeaturePanel } from "./FeaturePanel";
import type { Selection } from "./HistoryView";

const newer = (v: string | null | undefined, baseline: string) => !!v && cmpVersion(v, baseline) > 0;

export function Toc({ toc, active, baseline, highlight, changedOnly, bookmarks, bookmarkedOnly, onJump }: {
  toc: TocEntry[]; active: number; baseline: string; highlight: boolean; changedOnly: boolean;
  bookmarks: Set<string>; bookmarkedOnly: boolean; onJump: (ord: number) => void;
}) {
  const navRef = useRef<HTMLElement>(null);
  const shownActive = useRef(-1);
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const onJumpRef = useRef(onJump);
  onJumpRef.current = onJump;
  const jump = useCallback((ord: number) => onJumpRef.current(ord), []);
  // scrolling the document changes `active` many times a second: mark the row in the DOM instead of
  // rendering thousands of rows again (a 36.523-1 has ~9000 clauses)
  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    nav.querySelector(".toc-row.active")?.classList.remove("active");
    const row = nav.querySelector<HTMLElement>(`.toc-row[data-ord="${active}"]`);
    row?.classList.add("active");
    if (shownActive.current !== active) row?.scrollIntoView({ block: "nearest" });
    shownActive.current = active;
  });

  const hasChildren = useMemo(() => {
    const s = new Set<string>();
    toc.forEach((t, i) => { if (toc[i + 1] && toc[i + 1].level > t.level) s.add(t.key); });
    return s;
  }, [toc]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out: TocEntry[] = [];
    let hideBelow = Infinity;
    for (const t of toc) {
      if (q) {
        if (`${t.number} ${t.title}`.toLowerCase().includes(q)) out.push(t);
        continue;
      }
      if (t.level > hideBelow) continue;
      hideBelow = collapsed.has(t.key) ? t.level : Infinity;
      out.push(t);
    }
    return out;
  }, [toc, query, collapsed]);

  const toggle = useCallback((key: string) => setCollapsed((c) => {
    const n = new Set(c);
    if (n.has(key)) n.delete(key); else n.add(key);
    return n;
  }), []);

  return (
    <>
      <div className="toc-tools">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="목차 필터 (번호/제목)" />
        <button title="모두 접기" onClick={() => setCollapsed(new Set(toc.filter((t) => t.level === 1 && hasChildren.has(t.key)).map((t) => t.key)))}>−</button>
        <button title="모두 펼치기" onClick={() => setCollapsed(new Set())}>+</button>
      </div>
      <TocRows visible={visible} highlight={highlight} baseline={baseline} changedOnly={changedOnly}
               bookmarks={bookmarks} bookmarkedOnly={bookmarkedOnly} hasChildren={hasChildren} collapsed={collapsed}
               query={query} toggle={toggle} jump={jump} navRef={navRef} />
    </>
  );
}

const TocRows = memo(function TocRows({ visible, highlight, baseline, changedOnly, bookmarks, bookmarkedOnly,
  hasChildren, collapsed, query, toggle, jump, navRef }: {
  visible: TocEntry[]; highlight: boolean; baseline: string; changedOnly: boolean; bookmarks: Set<string>;
  bookmarkedOnly: boolean; hasChildren: Set<string>; collapsed: Set<string>; query: string;
  toggle: (key: string) => void; jump: (ord: number) => void; navRef: React.RefObject<HTMLElement>;
}) {
  return (
    <nav className="toc" ref={navRef}>
      {visible.map((t) => {
        const changed = highlight && newer(t.last_changed, baseline);
        if (changedOnly && !changed) return null;
        if (bookmarkedOnly && !bookmarks.has(t.key)) return null;
        return (
          <div key={t.ord} className="toc-row" data-ord={t.ord} style={{ paddingLeft: (Math.max(t.level, 1) - 1) * 12 }}>
            {hasChildren.has(t.key) && !query
              ? <button className="twisty" onClick={() => toggle(t.key)}>{collapsed.has(t.key) ? "▸" : "▾"}</button>
              : <span className="twisty" />}
            <button className="toc-item" onClick={() => jump(t.ord)} title={`${t.number} ${t.title}`}>
              {changed && <span className="dot" data-bv={t.last_changed!} />}
              <span className="num">{t.number}</span> {t.title}
              {bookmarks.has(t.key) && <span className="toc-star">★</span>}
            </button>
          </div>
        );
      })}
    </nav>
  );
});

/** Paragraphs that cite this clause, here and in the other downloaded specs: what else to re-check when it
 *  changes. Looked up when opened. */
function ReferencedBy({ spec, clause, onXref }: {
  spec: string; clause: string; onXref: (clause: string | undefined, spec: string | undefined) => void;
}) {
  const [refs, setRefs] = useState<RefHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => { if (!refs) api.referencedBy(spec, clause).then(setRefs, (e) => setError(String(e))); };
  const bySpec = useMemo(() => {
    const m = new Map<string, RefHit[]>();
    (refs ?? []).forEach((r) => m.set(r.spec, [...(m.get(r.spec) ?? []), r]));
    return [...m].sort(([a], [b]) => (a === spec ? -1 : b === spec ? 1 : a.localeCompare(b)));
  }, [refs, spec]);
  return (
    <details className="refby" onToggle={(e) => (e.target as HTMLDetailsElement).open && load()}>
      <summary className="small">이 절(§{clause})을 인용하는 곳{refs ? ` ${refs.length}` : ""}
        <span className="muted"> · 받은 스펙의 최신 버전</span></summary>
      {error && <p className="small status-error">{error}</p>}
      {refs && !refs.length && <p className="small muted">인용하는 문단을 찾지 못했습니다.</p>}
      {bySpec.map(([sp, hits]) => (
        <div key={sp}>
          <p className="small"><b>TS {sp}</b>{sp === spec ? " (이 스펙)" : ""} · {hits.length}곳 <span className="muted">v{hits[0].version}</span></p>
          <ul>
            {hits.map((h, i) => (
              <li key={i} className="small">
                <button className="link" onClick={() => onXref(h.number || h.key, sp === spec ? undefined : sp)}>
                  §{h.number || h.title}</button>{" "}
                <span className="muted" dangerouslySetInnerHTML={{
                  __html: escapeHtml(h.snippet).replace(/\[\[/g, "<mark>").replace(/\]\]/g, "</mark>") }} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}

const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Scrollbar-side overview: one tick per changed clause, like an editor's minimap markers. */
export function ChangeRuler({ toc, baseline, onJump }: { toc: TocEntry[]; baseline: string; onJump: (ord: number) => void }) {
  if (!toc.length) return null;
  return (
    <div className="ruler" aria-label="change overview">
      {toc.filter((t) => newer(t.last_changed, baseline)).map((t) => (
        <button key={t.ord} className="tick" data-bv={t.last_changed!} title={`${t.number} ${t.title} (${t.last_changed})`}
                style={{ top: `${(t.ord / toc.length) * 100}%` }} onClick={() => onJump(t.ord)} />
      ))}
    </div>
  );
}

function CRItemView({ cr, hit, analysis }: { cr: CR; hit?: boolean; analysis?: CRAnalysis }) {
  const d = cr.detail;
  return (
    <li className={hit ? "hit" : ""}>
      <div className="cr-head">
        {cr.cr ? <b>CR{cr.cr}{cr.rev && cr.rev !== "-" ? ` r${cr.rev}` : ""}</b> : <b>—</b>}
        {cr.cat && <span className={`cat cat-${cr.cat}`} title={CAT[cr.cat] ?? ""}>{cr.cat}</span>}
        {cr.tdoc && <a href={tdocUrl(cr.tdoc)} target="_blank" rel="noreferrer">{cr.tdoc}</a>}
        {d?.wg_tdoc && <a href={tdocUrl(d.wg_tdoc)} target="_blank" rel="noreferrer" className="muted">{d.wg_tdoc}</a>}
        <span className="muted">{cr.meeting} {cr.date}</span>
        {cr.cr && cr.tdoc && (
          <button className="link tiny" title="CR 문서(표지와 바뀐 절, 수정 표시 포함)를 별도 창으로 엽니다"
                  onClick={() => openCRDoc(cr.spec, cr.cr, cr.tdoc)}>문서 보기</button>
        )}
        {analysis && <button className="link tiny analysed" title={analysis.summary}
                             onClick={() => openCRDoc(cr.spec, cr.cr, cr.tdoc)}>AI 분석 있음</button>}
      </div>
      <div>{cr.subject}</div>
      {d && (
        <div className="cr-detail">
          {d.wi_code && <span className="wi" title="Work item code">{d.wi_code}</span>}
          {d.source_wg && <span className="muted small"> · {d.source_wg}</span>}
          {d.clauses.length > 0 && <div className="small"><span className="muted">Clauses affected:</span> {d.clauses_raw}</div>}
          <details>
            <summary className="small">변경 이유 / 요약</summary>
            {d.reason && <p><b>Reason for change</b><br />{d.reason}</p>}
            {d.summary && <p><b>Summary of change</b><br />{d.summary}</p>}
            {d.consequences && <p className="muted"><b>Consequences if not approved</b><br />{d.consequences}</p>}
          </details>
        </div>
      )}
    </li>
  );
}

function CRList({ crs, clause }: { crs: CR[]; clause?: string }) {
  const analyses = useCRAnalyses(crs[0]?.spec ?? "");
  if (!crs.length) return <p className="muted small">Change history 에 CR 기록 없음</p>;
  // a plain function, not a component made during render (that remounted every item and closed its details)
  const CRItem = (p: { cr: CR; hit?: boolean; key: number }) =>
    <CRItemView key={p.key} cr={p.cr} hit={p.hit} analysis={analyses.get(crKey(p.cr.cr, p.cr.tdoc))} />;
  const haveDetail = crs.some((c) => c.detail);
  if (!clause || !haveDetail) return <ul className="cr-list">{crs.map((cr, i) => CRItem({ key: i, cr }))}</ul>;
  const hits = crs.filter((c) => crTouches(c, clause));
  const rest = crs.filter((c) => !crTouches(c, clause));
  return (
    <>
      {hits.length > 0
        ? <ul className="cr-list">{hits.map((cr, i) => CRItem({ key: i, cr, hit: true }))}</ul>
        : <p className="muted small">이 절을 “Clauses affected”에 적은 CR이 없습니다.</p>}
      {rest.length > 0 && (
        <details className="cr-rest">
          <summary className="muted small">이 버전의 다른 CR ({rest.length})</summary>
          <ul className="cr-list">{rest.map((cr, i) => CRItem({ key: i, cr }))}</ul>
        </details>
      )}
    </>
  );
}

function NoteEditor({ initial, meta, onSave }: { initial: string; meta?: Note; onSave: (text: string) => void }) {
  const [text, setText] = useState(initial);
  const dirty = text !== initial;
  const latest = useRef({ text, dirty, onSave });
  latest.current = { text, dirty, onSave };
  // closing the panel or picking another paragraph unmounts the editor without a blur: save what was typed
  useEffect(() => () => { const l = latest.current; if (l.dirty) l.onSave(l.text); }, []);
  return (
    <section className="note-editor">
      <h4>내 메모 <span className="muted small">버전이 바뀌어도 이 절에 유지</span></h4>
      <textarea value={text} rows={3} placeholder="구현 메모, 확인할 점…" onChange={(e) => setText(e.target.value)}
                onBlur={() => { if (dirty) { latest.current.dirty = false; onSave(text); } }} />
      <div className="tiny muted">{dirty ? "포커스를 옮기면 저장" : meta ? `저장됨 · ${meta.updated.replace("T", " ")}${meta.version ? ` (v${meta.version} 보던 중)` : ""}` : ""}</div>
    </section>
  );
}

export const CAT: Record<string, string> = {
  F: "Correction", A: "Mirror of a correction in an earlier release", B: "Addition of feature",
  C: "Functional modification of feature", D: "Editorial modification",
};

export function InfoPanel({ spec, selection, version, baseline, crsByVersion, colors, onClose, onFetchCrs, fetching,
  note, onSaveNote, onXref }: {
  spec: string; selection: Selection | null; version: string; baseline: string; crsByVersion: Map<string, CR[]>;
  colors: Map<string, VersionColor>; onClose: () => void; onFetchCrs?: () => void; fetching?: string | null;
  note?: Note; onSaveNote: (key: string, text: string) => void;
  onXref: (clause: string | undefined, spec: string | undefined) => void;
}) {
  const hasDetail = [...crsByVersion.values()].some((l) => l.some((c) => c.detail_status));
  const block = selection && selection.block !== null ? selection.clause.blocks[selection.block] : null;
  const history = block ? block.history : selection?.clause.history ?? [];
  const introduced = block ? block.introduced : selection?.clause.introduced;
  return (
    <aside className="info">
      {fetching && <p className="small job">{fetching}</p>}
      <div className="info-head">
        <b>{selection ? `§${selection.clause.number || selection.clause.title}` : `v${version}`}</b>
        {selection && (
          <button className="link small ask-ai" title="이 문단(또는 절)을 맥락으로 AI 채팅에서 질문합니다"
                  onClick={() => askAboutParagraph({
                    spec, version, clause: selection.clause.number || selection.clause.key,
                    text: (block ? block.html : selection.clause.blocks.map((b) => b.html).join("\n"))
                      .replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
                      .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/[ \t]+/g, " ").trim().slice(0, 3000),
                  })}>💬 AI 에게 질문</button>
        )}
        {selection && <button className="link" onClick={onClose}>✕</button>}
      </div>
      {selection ? (
        <>
          {block && (
            <ParagraphHistory spec={spec} version={version} clauseKey={selection.clause.key} rawHash={block.raw_hash}
                              colors={colors} />
          )}
          <p className="muted small">{block ? "이 문단이 바뀐 버전에 반영된 CR" : "절 전체의 변경 이력"}</p>
          <ol className="timeline">
            <li>
              <span className="chip" style={{ background: colors.get(introduced ?? "")?.solid }} />
              <b>v{introduced}</b> {block ? "에서 처음 등장" : "에서 절 생성"}
            </li>
            {history.filter(([, t]) => t !== "added").map(([v, t, from]) => (
              <li key={`${v}-${t}`} className={newer(v, baseline) ? "" : "dim"}>
                <span className="chip" style={{ background: colors.get(v)?.solid }} />
                <b>v{v}</b> {t === "moved" ? `에서 §${from} 로부터 이동` : "에서 변경"}
                <details open={newer(v, baseline)}>
                  <summary className="muted small">이 버전에 반영된 CR ({crsByVersion.get(v)?.length ?? 0})</summary>
                  <CRList crs={crsByVersion.get(v) ?? []} clause={selection.clause.number} />
                </details>
              </li>
            ))}
          </ol>
          {!hasDetail && onFetchCrs && (
            <p className="small note">CR 표지(변경 이유·Clauses affected)를 아직 받지 않았습니다.{" "}
              <button className="link" onClick={onFetchCrs}>CR 표지 받기</button></p>
          )}
          {selection.clause.number && (
            <ReferencedBy key={selection.clause.key} spec={spec} clause={selection.clause.number} onXref={onXref} />
          )}
          <NoteEditor key={selection.clause.key} initial={note?.text ?? ""} meta={note}
                      onSave={(t) => onSaveNote(selection.clause.key, t)} />
          <AnnotationPanel spec={spec} version={version} clauseKey={selection.clause.key} rawHash={block?.raw_hash} />
          <FeaturePanel spec={spec} version={version} clauseKey={selection.clause.key} />
        </>
      ) : (
        <>
          <p className="muted small">v{version} 에 반영된 CR</p>
          <CRList crs={crsByVersion.get(version) ?? []} />
          {!hasDetail && onFetchCrs && (
            <p className="small note">CR 표지(변경 이유·Clauses affected)를 받으면 절별로 어떤 CR이 수정했는지 표시됩니다.{" "}
              <button className="link" onClick={onFetchCrs}>CR 표지 받기</button></p>
          )}
          <p className="muted small note">본문의 문단이나 절 제목을 클릭하면 해당 부분의 버전 이력이 표시됩니다.</p>
        </>
      )}
    </aside>
  );
}
