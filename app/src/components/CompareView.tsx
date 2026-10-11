// Code-review style comparison of two arbitrary versions (inline or side by side).
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { api, cmpVersion, tdocUrl, type CompareResult, type DiffBlock, type DiffClause, type VersionInfo } from "../api";
import { compareReport, download } from "../export";
import { ReleaseNotes } from "./ReleaseNotes";
import { openCRDoc } from "./DocWindow";
import { Html } from "./Html";

interface Props {
  spec: string;
  versions: VersionInfo[];
  oldV: string;
  newV: string;
  onChange: (oldV: string, newV: string) => void;
  onXref: (clause: string | undefined, spec: string | undefined) => void;
}

export function CompareView({ spec, versions, oldV, newV, onChange, onXref }: Props) {
  const [data, setData] = useState<CompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sideBySide, setSideBySide] = useState(false);
  const [notes, setNotes] = useState(false);

  useEffect(() => {
    setData(null);
    setError(null);
    if (cmpVersion(oldV, newV) >= 0) return;
    let live = true;  // a big compare takes seconds: the reply for versions picked before must not land here
    api.compare(spec, oldV, newV).then((x) => live && setData(x), (e) => live && setError(String(e)));
    return () => { live = false; };
  }, [spec, oldV, newV]);

  const all = useMemo(() => (data ? [...data.clauses, ...data.removed] : []), [data]);
  // A comparison of distant versions has thousands of changed clauses (38.211 15.0.0 -> 16.10.0: 170 000 DOM
  // nodes, 18 s to show): only the clauses in view are rendered.
  const listRef = useRef<HTMLDivElement>(null);
  const virt = useVirtualizer({
    count: all.length,
    getScrollElement: () => listRef.current,
    estimateSize: (i) => 90 + 60 * Math.min(all[i]?.blocks.length ?? 1, 6),
    overscan: 4,
  });
  useEffect(() => { listRef.current?.scrollTo(0, 0); setExpanded(new Set()); }, [data]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());  // kept here: a clause scrolled out unmounts

  const onClick = (e: React.MouseEvent) => {
    const x = (e.target as HTMLElement).closest<HTMLElement>(".xref");
    if (x) onXref(x.dataset.clause, x.dataset.spec);
  };

  return (
    <div className="compare" onClick={onClick}>
      <div className="compare-bar">
        <VersionSelect versions={versions} value={oldV} onChange={(v) => onChange(v, newV)} />
        <span className="arrow">→</span>
        <VersionSelect versions={versions} value={newV} onChange={(v) => onChange(oldV, v)} />
        <div className="seg">
          <button className={!sideBySide ? "on" : ""} onClick={() => setSideBySide(false)}>Inline</button>
          <button className={sideBySide ? "on" : ""} onClick={() => setSideBySide(true)}>Side by side</button>
        </div>
        {data && <span className="muted">{data.clauses.length} changed · {data.removed.length} removed · {data.crs.length} CRs</span>}
        {data && (
          <button onClick={async () => download(`TS${spec}_v${oldV}_to_v${newV}.html`, await compareReport(data))}
                  title="비교 결과를 하나의 HTML 파일로 저장 (메일·보고서용)">⤓ 보고서 내보내기</button>
        )}
        {cmpVersion(oldV, newV) < 0 && (
          <button className={notes ? "on" : ""} onClick={() => setNotes(!notes)}
                  title="두 버전 사이 CR 을 WI 별로 묶어 AI 가 정리한 릴리즈 노트">✦ AI 릴리즈 노트</button>
        )}
      </div>
      {cmpVersion(oldV, newV) >= 0 && <p className="empty">왼쪽 버전이 오른쪽보다 이전이어야 합니다.</p>}
      {notes && cmpVersion(oldV, newV) < 0 && <ReleaseNotes spec={spec} oldV={oldV} newV={newV} />}
      {error && <p className="empty error">{error}</p>}
      {!data && !error && cmpVersion(oldV, newV) < 0 && <p className="empty">비교 중…</p>}
      {data && (
        <div className="compare-body">
          <nav className="compare-nav">
            {all.map((c, i) => (
              <a key={c.key} href={`#cmp-${c.key}`} className={`st-${c.status}`}
                 onClick={(e) => { e.preventDefault(); virt.scrollToIndex(i, { align: "start" }); }}>
                <span className="num">{c.number || "·"}</span> {c.title}
              </a>
            ))}
            {data.crs.length > 0 && <h4>CRs in range</h4>}
            {data.crs.map((cr, i) => (
              <a key={i} href={tdocUrl(cr.tdoc)} target="_blank" rel="noreferrer" className="cr-mini" title={cr.subject}
                 onClick={(e) => { if (cr.cr && cr.tdoc) { e.preventDefault(); openCRDoc(cr.spec, cr.cr, cr.tdoc); } }}>
                <b>{cr.new_version}</b> CR{cr.cr || "—"} {cr.subject}
              </a>
            ))}
          </nav>
          <div ref={listRef} className={`compare-list ${sideBySide ? "sbs" : "inline"}`}>
            {all.length === 0 && <p className="empty">두 버전 사이에 변경 사항이 없습니다.</p>}
            <div style={{ height: virt.getTotalSize(), position: "relative" }}>
              {virt.getVirtualItems().map((it) => (
                <div key={all[it.index].key} data-index={it.index} ref={virt.measureElement}
                     style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${it.start}px)` }}>
                  <DiffClauseView c={all[it.index]} sideBySide={sideBySide} showAll={expanded.has(all[it.index].key)}
                                  onShowAll={(k) => setExpanded((x) => new Set(x).add(k))} />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const DiffClauseView = memo(function DiffClauseView({ c, sideBySide, showAll, onShowAll }: {
  c: DiffClause; sideBySide: boolean; showAll: boolean; onShowAll: (key: string) => void;
}) {
  const Tag = (`h${Math.min(Math.max(c.level, 1), 6)}`) as "h1";
  // Collapse runs of unchanged blocks, keeping one block of context around changes.
  const changed = (i: number) => !!c.blocks[i] && c.blocks[i].op !== "equal";
  const visible = c.blocks.map((_, i) => showAll || changed(i) || changed(i - 1) || changed(i + 1));
  const hidden = visible.filter((v) => !v).length;
  return (
    <section id={`cmp-${c.key}`} className={`diff-clause st-${c.status}`}>
      <Tag className="clause-title">
        {c.number && <span className="num">{c.number}</span>}
        {c.old_title && c.old_title !== c.title ? <span><del>{c.old_title}</del> <ins>{c.title}</ins></span> : <span>{c.title}</span>}
        <span className={`status status-${c.status}`}>{c.status}</span>
      </Tag>
      {c.blocks.map((b, i) => visible[i] && (
        <div key={i} className={`diff-blk op-${b.op} kind-${b.kind} ${b.style && !sideBySide ? `st-${b.style}` : ""}`}>
          {b.op === "move-out" ? (
            <MovedOut b={b} />
          ) : (
            <>
              {b.op === "move-in" && <div className="move-tag">↘ §{b.moved_from} 에서 이동{b.diff.includes("<ins>") || b.diff.includes("<del>") ? " (수정 포함)" : ""}</div>}
              {sideBySide ? (
                <>
                  <Html html={b.op === "insert" || b.op === "move-in" ? "" : b.diff} className={`side old ${b.style ? `st-${b.style}` : ""}`} />
                  <Html html={b.op === "delete" ? "" : b.diff} className={`side new ${b.style ? `st-${b.style}` : ""}`} />
                </>
              ) : (
                <Html html={b.diff} />
              )}
            </>
          )}
        </div>
      ))}
      {hidden > 0 && (
        <button className="link more" onClick={() => onShowAll(c.key)}>… 변경 없는 문단 {hidden}개 펼치기</button>
      )}
    </section>
  );
});

function MovedOut({ b }: { b: DiffBlock }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="move-out">
      <button className="link" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} §{b.moved_to} 로 이동된 문단</button>
      {open && <Html html={b.old ?? ""} className="moved-body" />}
    </div>
  );
}

export function VersionSelect({ versions, value, onChange, label }: {
  versions: VersionInfo[]; value: string; onChange: (v: string) => void; label?: string;
}) {
  return (
    <label className="vsel">
      {label && <span className="muted">{label}</span>}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {[...new Set(versions.map((v) => v.release))].map((rel) => (
          <optgroup key={rel} label={`Rel-${rel}`}>
            {versions.filter((v) => v.release === rel).map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
          </optgroup>
        ))}
      </select>
    </label>
  );
}
