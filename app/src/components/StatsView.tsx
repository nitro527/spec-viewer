// Statistics: how much each version changed, and which clauses change most often.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Stats, type VersionStats } from "../api";
import type { VersionColor } from "../colors";

export function StatsView({ spec, colors, onJump, onCompare }: {
  spec: string; colors: Map<string, VersionColor>;
  onJump: (key: string) => void; onCompare: (version: string) => void;
}) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [hover, setHover] = useState<{ v: VersionStats; x: number; y: number } | null>(null);
  const tip = useRef<HTMLDivElement>(null);
  const [limit, setLimit] = useState(25);

  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setFailed(null);
    setStats(null);
    api.stats(spec).then((x) => live && setStats(x), (e) => live && setFailed(String(e).replace(/^Error: /, "")));
    return () => { live = false; };
  }, [spec]);

  const versions = useMemo(() => stats?.versions.slice(1) ?? [], [stats]); // first version is the baseline
  const changed = (v: VersionStats) => v.added + v.modified + v.removed;
  const crCount = (v: VersionStats) => Object.values(v.crs).reduce((a, b) => a + b, 0);
  const maxChanged = Math.max(1, ...versions.map(changed));
  const maxCr = Math.max(1, ...versions.map(crCount));
  // Cover page and Change history annex change with every version: not interesting here.
  const ranked = useMemo(() => (stats?.clauses ?? [])
    .filter((c) => c.changes.length && c.key !== "front" && !/change history/i.test(c.title))
    .sort((a, b) => b.changes.length - a.changes.length || a.ord - b.ord), [stats]);

  if (!stats) return <p className="empty">{failed ? `통계를 계산하지 못했습니다: ${failed}` : "통계 계산 중…"}</p>;

  const bars = (value: (v: VersionStats) => number, max: number, colored: boolean) => (
    <div className="bars">
      {versions.map((v) => (
        <button key={v.version} className="bar-col" onClick={() => onCompare(v.version)}
                onMouseMove={(e) => {
                  // a new bar re-renders; moving within one only moves the tooltip (the table can be long)
                  if (hover?.v !== v) return setHover({ v, x: e.clientX, y: e.clientY });
                  const t = tip.current;
                  if (t) { t.style.left = `${e.clientX + 12}px`; t.style.top = `${e.clientY + 12}px`; }
                }} onMouseLeave={() => setHover(null)}>
          <span className="bar-val">{value(v) || ""}</span>
          <span className="bar" style={{ height: `${(value(v) / max) * 100}%`,
            background: colored ? colors.get(v.version)?.solid : "var(--muted)" }} />
          <span className="bar-label">{v.version}</span>
        </button>
      ))}
    </div>
  );

  return (
    <div className="stats">
      <section>
        <h3>버전별 변경된 절 수 <span className="muted small">(이전 버전 대비 · 막대 클릭 시 Compare)</span></h3>
        {bars(changed, maxChanged, true)}
      </section>
      <section>
        <h3>버전별 반영 CR 수</h3>
        {bars(crCount, maxCr, false)}
      </section>
      <section>
        <h3>가장 자주 바뀐 절 <span className="muted small">(v{stats.latest} 기준 · {ranked.length}개 절이 1회 이상 변경)</span></h3>
        <table className="stats-table">
          <thead><tr><th>절</th><th>제목</th><th>변경</th><th>변경된 버전</th></tr></thead>
          <tbody>
            {ranked.slice(0, limit).map((c) => (
              <tr key={c.key} onClick={() => onJump(c.key)}>
                <td className="num">{c.number}</td>
                <td>{c.title}</td>
                <td className="count">{c.changes.length}</td>
                <td className="dots">
                  {c.changes.map((v) => <span key={v} className="vchip" title={`v${v}`} style={{ background: colors.get(v)?.solid }} />)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {ranked.length > limit && <button className="link" onClick={() => setLimit(limit + 50)}>더 보기</button>}
      </section>
      {hover && (
        <div ref={tip} className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          <b>v{hover.v.version}</b>
          <div>추가 {hover.v.added} · 변경 {hover.v.modified} · 삭제 {hover.v.removed}</div>
          <div>CR {crCount(hover.v)}{Object.keys(hover.v.crs).length ? ` (${Object.entries(hover.v.crs).map(([k, n]) => `${k}:${n}`).join(", ")})` : ""}</div>
        </div>
      )}
    </div>
  );
}
