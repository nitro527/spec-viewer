// Code-review style comparison of two arbitrary versions (inline or side by side).
import { useEffect, useMemo, useState } from "react";
import { api, cmpVersion, tdocUrl, type CompareResult, type DiffBlock, type DiffClause, type VersionInfo } from "../api";
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

  useEffect(() => {
    setData(null);
    setError(null);
    if (cmpVersion(oldV, newV) >= 0) return;
    api.compare(spec, oldV, newV).then(setData, (e) => setError(String(e)));
  }, [spec, oldV, newV]);

  const all = useMemo(() => (data ? [...data.clauses, ...data.removed] : []), [data]);

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
      </div>
      {cmpVersion(oldV, newV) >= 0 && <p className="empty">왼쪽 버전이 오른쪽보다 이전이어야 합니다.</p>}
      {error && <p className="empty error">{error}</p>}
      {!data && !error && cmpVersion(oldV, newV) < 0 && <p className="empty">비교 중…</p>}
      {data && (
        <div className="compare-body">
          <nav className="compare-nav">
            {all.map((c) => (
              <a key={c.key} href={`#cmp-${c.key}`} className={`st-${c.status}`}>
                <span className="num">{c.number || "·"}</span> {c.title}
              </a>
            ))}
            {data.crs.length > 0 && <h4>CRs in range</h4>}
            {data.crs.map((cr, i) => (
              <a key={i} href={tdocUrl(cr.tdoc)} target="_blank" rel="noreferrer" className="cr-mini" title={cr.subject}>
                <b>{cr.new_version}</b> CR{cr.cr || "—"} {cr.subject}
              </a>
            ))}
          </nav>
          <div className={`compare-list ${sideBySide ? "sbs" : "inline"}`}>
            {all.length === 0 && <p className="empty">두 버전 사이에 변경 사항이 없습니다.</p>}
            {all.map((c) => <DiffClauseView key={c.key} c={c} sideBySide={sideBySide} />)}
          </div>
        </div>
      )}
    </div>
  );
}

function DiffClauseView({ c, sideBySide }: { c: DiffClause; sideBySide: boolean }) {
  const [showAll, setShowAll] = useState(false);
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
        <button className="link more" onClick={() => setShowAll(true)}>… 변경 없는 문단 {hidden}개 펼치기</button>
      )}
    </section>
  );
}

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
