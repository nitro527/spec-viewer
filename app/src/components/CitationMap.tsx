// Which downloaded spec cites which (latest versions), as a table rather than a graph: rows cite, columns are
// cited, a cell counts the citing clauses. A cell lists those clauses and what they cite; the most cited clauses
// are what a change ripples out from.
import { Fragment, useState } from "react";
import { api, type CitationMap as Map_, type CitationPair } from "../api";

export function CitationMap({ onOpen }: { onOpen: (spec: string, clause?: string) => void }) {
  const [data, setData] = useState<Map_ | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cell, setCell] = useState<{ from: string; to: string; pairs: CitationPair[] | null } | null>(null);
  const load = () => { if (!data) api.citations().then(setData, (e) => setError(String(e))); };
  const count = (f: string, t: string) => data?.matrix.find((m) => m.from === f && m.to === t)?.clauses ?? 0;
  const max = Math.max(1, ...(data?.matrix.filter((m) => m.from !== m.to).map((m) => m.clauses) ?? [1]));
  const pick = (from: string, to: string) => {
    if (cell?.from === from && cell.to === to) return setCell(null);
    setCell({ from, to, pairs: null });
    api.citationPairs(from, to).then((pairs) => setCell((c) => c && c.from === from && c.to === to ? { ...c, pairs } : c),
      (e) => setError(String(e)));
  };

  return (
    <details className="cite-map" onToggle={(e) => (e.target as HTMLDetailsElement).open && load()}>
      <summary><b>스펙 간 인용 관계</b> <span className="small muted">받은 스펙의 최신 버전 · 어느 스펙의 몇 개 절이 어느 스펙을 인용하는지</span></summary>
      {error && <p className="small status-error">{error}</p>}
      {!data && !error && <p className="small muted">계산 중…</p>}
      {data && (
        <>
          <div className="cite-wrap">
            <table className="cite-table">
              <thead>
                <tr><th className="small muted">인용하는 ↓ / 인용되는 →</th>
                  {data.specs.map((t) => <th key={t}>{t}</th>)}</tr>
              </thead>
              <tbody>
                {data.specs.map((f) => (
                  <tr key={f}>
                    <th>{f}</th>
                    {data.specs.map((t) => {
                      const n = count(f, t);
                      const self = f === t;
                      return (
                        <td key={t} className={`${self ? "self" : ""} ${cell?.from === f && cell.to === t ? "on" : ""}`}
                            style={!self && n ? { background: `rgba(52, 120, 246, ${0.08 + 0.5 * n / max})` } : undefined}
                            title={self ? `${f} 안에서 다른 절을 인용하는 절 ${n}개` : `${f} 의 ${n}개 절이 ${t} 를 인용`}
                            onClick={() => n && pick(f, t)}>
                          {n || ""}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {cell && (
            <div className="cite-pairs">
              <p className="small"><b>{cell.from}</b> → <b>{cell.to}</b>
                {cell.pairs && ` · ${cell.pairs.length}개 절`} <button className="link tiny" onClick={() => setCell(null)}>닫기</button></p>
              {!cell.pairs && <p className="small muted">불러오는 중…</p>}
              <ul>
                {cell.pairs?.map((p) => (
                  <li key={p.key} className="small">
                    <button className="link" onClick={() => onOpen(cell.from, p.number || p.key)}>
                      §{p.number || p.title}</button>{p.number && <span className="muted"> {p.title}</span>}
                    {" → "}
                    {p.cites.length ? p.cites.map((c, i) => (
                      <Fragment key={c}>{i > 0 && ", "}
                        <button className="link" onClick={() => onOpen(cell.to, c)}>{cell.to} §{c}</button></Fragment>
                    )) : <span className="muted">{cell.to} (절 번호 없이)</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="cite-lists">
            <div>
              <h4>가장 많이 인용되는 절 <span className="small muted">(바뀌면 영향이 넓은 곳)</span></h4>
              <ol className="small">
                {data.top_cited.map((c) => (
                  <li key={`${c.spec}/${c.clause}`}>
                    <button className="link" onClick={() => onOpen(c.spec, c.clause)}>{c.spec} §{c.clause}</button>{" "}
                    <span className="muted">{c.title}</span> · <b>{c.count}</b>
                    <span className="muted"> ({c.from_specs.join(", ")})</span>
                  </li>
                ))}
              </ol>
            </div>
            {data.external.length > 0 && (
              <div>
                <h4>인용되지만 받지 않은 스펙</h4>
                <ul className="small">
                  {data.external.map((e) => (
                    <li key={e.spec}><button className="link" onClick={() => onOpen(e.spec)}>{e.spec}</button>
                      <span className="muted"> · 인용하는 절 {e.count}개</span></li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
    </details>
  );
}
