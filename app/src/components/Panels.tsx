// Side panels: table of contents (left) and change information (right).
import { useEffect, useRef } from "react";
import { cmpVersion, tdocUrl, type CR, type TocEntry } from "../api";
import type { VersionColor } from "../colors";
import type { Selection } from "./HistoryView";

const newer = (v: string | null | undefined, baseline: string) => !!v && cmpVersion(v, baseline) > 0;

export function Toc({ toc, active, baseline, highlight, changedOnly, onJump }: {
  toc: TocEntry[]; active: number; baseline: string; highlight: boolean; changedOnly: boolean;
  onJump: (ord: number) => void;
}) {
  const activeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => activeRef.current?.scrollIntoView({ block: "nearest" }), [active]);
  return (
    <nav className="toc">
      {toc.map((t) => {
        const changed = highlight && newer(t.last_changed, baseline);
        if (changedOnly && !changed) return null;
        return (
          <button key={t.ord} ref={t.ord === active ? activeRef : undefined}
                  className={`toc-item ${t.ord === active ? "active" : ""}`}
                  style={{ paddingLeft: 8 + (Math.max(t.level, 1) - 1) * 12 }} onClick={() => onJump(t.ord)}
                  title={`${t.number} ${t.title}`}>
            {changed && <span className="dot" data-bv={t.last_changed!} />}
            <span className="num">{t.number}</span> {t.title}
          </button>
        );
      })}
    </nav>
  );
}

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

function CRList({ crs }: { crs: CR[] }) {
  if (!crs.length) return <p className="muted small">Change history 에 CR 기록 없음</p>;
  return (
    <ul className="cr-list">
      {crs.map((cr, i) => (
        <li key={i}>
          <div className="cr-head">
            {cr.cr ? <b>CR{cr.cr}{cr.rev ? ` r${cr.rev}` : ""}</b> : <b>—</b>}
            {cr.cat && <span className={`cat cat-${cr.cat}`} title={CAT[cr.cat] ?? ""}>{cr.cat}</span>}
            {cr.tdoc && <a href={tdocUrl(cr.tdoc)} target="_blank" rel="noreferrer">{cr.tdoc}</a>}
            <span className="muted">{cr.meeting} {cr.date}</span>
          </div>
          <div>{cr.subject}</div>
        </li>
      ))}
    </ul>
  );
}

const CAT: Record<string, string> = {
  F: "Correction", A: "Mirror of a correction in an earlier release", B: "Addition of feature",
  C: "Functional modification of feature", D: "Editorial modification",
};

export function InfoPanel({ selection, version, baseline, crsByVersion, colors, onClose }: {
  selection: Selection | null; version: string; baseline: string; crsByVersion: Map<string, CR[]>;
  colors: Map<string, VersionColor>; onClose: () => void;
}) {
  const block = selection && selection.block !== null ? selection.clause.blocks[selection.block] : null;
  const history = block ? block.history : selection?.clause.history ?? [];
  const introduced = block ? block.introduced : selection?.clause.introduced;
  return (
    <aside className="info">
      <div className="info-head">
        <b>{selection ? `§${selection.clause.number || selection.clause.title}` : `v${version}`}</b>
        {selection && <button className="link" onClick={onClose}>✕</button>}
      </div>
      {selection ? (
        <>
          <p className="muted small">{block ? "선택한 문단" : "절 전체"}의 변경 이력</p>
          <ol className="timeline">
            <li>
              <span className="chip" style={{ background: colors.get(introduced ?? "")?.solid }} />
              <b>v{introduced}</b> {block ? "에서 처음 등장" : "에서 절 생성"}
            </li>
            {history.filter(([, t]) => t === "modified").map(([v]) => (
              <li key={v} className={newer(v, baseline) ? "" : "dim"}>
                <span className="chip" style={{ background: colors.get(v)?.solid }} />
                <b>v{v}</b> 에서 변경
                <details open={newer(v, baseline)}>
                  <summary className="muted small">이 버전에 반영된 CR ({crsByVersion.get(v)?.length ?? 0})</summary>
                  <CRList crs={crsByVersion.get(v) ?? []} />
                </details>
              </li>
            ))}
          </ol>
          <p className="muted small note">
            CR → 문단 단위의 정확한 매핑(CR 표지의 “Clauses affected”)과 Tdoc·Chairman’s notes 의 의도 요약은 annotation 서버 단계에서 추가됩니다.
          </p>
        </>
      ) : (
        <>
          <p className="muted small">v{version} 에 반영된 CR</p>
          <CRList crs={crsByVersion.get(version) ?? []} />
          <p className="muted small note">본문의 문단이나 절 제목을 클릭하면 해당 부분의 버전 이력이 표시됩니다.</p>
        </>
      )}
    </aside>
  );
}
