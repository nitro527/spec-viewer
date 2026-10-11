// Every CR of the spec's Change history in one list: filter by text, release, category, work item and AI
// analysis; open the CR document, jump to the clauses it changed, and analyse the filtered CRs in one go.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, crKey, tdocUrl, type CR, type CRAnalysis, type TocEntry } from "../api";
import type { VersionColor } from "../colors";
import { announceAnalysesChanged, useCRAnalyses, useLlmReady } from "./CRAnalysis";
import { openCRDoc } from "./DocWindow";
import { CAT } from "./Panels";
import { WISummaryPanel } from "./WISummary";

const wiCodes = (c: CR) => (c.detail?.wi_code ?? "").split(/[,;\s]+/).filter(Boolean);
const release = (c: CR) => c.detail?.release?.replace(/^Rel-?/i, "") || c.new_version.split(".")[0];

export function CRBrowser({ spec, crs, toc, colors, onJump, onOpenSpec, onFetchCrs, fetching }: {
  spec: string; crs: CR[]; toc: TocEntry[]; colors: Map<string, VersionColor>;
  onJump: (ord: number) => void; onOpenSpec: (spec: string, clause?: string) => void;
  onFetchCrs?: () => void; fetching?: string | null;
}) {
  const analyses = useCRAnalyses(spec);
  const [q, setQ] = useState("");
  const [rel, setRel] = useState("");
  const [cat, setCat] = useState("");
  const [wi, setWi] = useState("");
  const [only, setOnly] = useState<"" | "analysed" | "not">("");
  const [open, setOpen] = useState<string | null>(null);
  const llmReady = useLlmReady();

  const rows = useMemo(() => [...crs].filter((c) => c.cr)
    .sort((a, b) => b.new_version.localeCompare(a.new_version, undefined, { numeric: true }) || b.cr.localeCompare(a.cr)), [crs]);
  const releases = useMemo(() => [...new Set(rows.map(release))].sort((a, b) => Number(b) - Number(a)), [rows]);
  const wis = useMemo(() => {
    const n = new Map<string, number>();
    rows.forEach((c) => wiCodes(c).forEach((w) => n.set(w, (n.get(w) ?? 0) + 1)));
    return [...n].sort((a, b) => b[1] - a[1]);
  }, [rows]);
  const byNumber = useMemo(() => new Map(toc.map((t) => [t.number, t])), [toc]);

  const shown = useMemo(() => {
    const low = q.trim().toLowerCase();
    return rows.filter((c) => {
      const a = analyses.get(crKey(c.cr, c.tdoc));
      if (rel && release(c) !== rel) return false;
      if (cat && c.cat !== cat) return false;
      if (wi && !wiCodes(c).includes(wi)) return false;
      if (only === "analysed" && !a) return false;
      if (only === "not" && a) return false;
      if (!low) return true;
      return [c.cr, c.tdoc, c.subject, c.meeting, c.detail?.wi_code, c.detail?.clauses_raw, c.detail?.wg_tdoc,
        a?.summary].some((x) => x?.toLowerCase().includes(low));
    });
  }, [rows, analyses, q, rel, cat, wi, only]);
  const todo = shown.filter((c) => c.detail && !analyses.has(crKey(c.cr, c.tdoc)));
  const covers = rows.filter((c) => c.detail).length;

  // analyse many CRs one after another (one job at a time: the LLM server is shared)
  const [batch, setBatch] = useState<{ done: number; total: number; current: string; failed: number } | null>(null);
  const stop = useRef(false);
  useEffect(() => () => { stop.current = true; }, []);  // leaving CR mode ends the batch after the current CR
  const runBatch = async (list: CR[]) => {
    stop.current = false;
    let failed = 0;
    for (let i = 0; i < list.length && !stop.current; i++) {
      const c = list[i];
      setBatch({ done: i, total: list.length, current: `CR${c.cr}`, failed });
      try {
        const job = await api.analyzeCR(spec, c.cr, c.tdoc);
        for (;;) {
          await new Promise((r) => setTimeout(r, 1500));
          const j = (await api.jobs()).find((x) => x.id === job.id);
          if (!j || j.state !== "running") {
            if (j?.state !== "done") failed++;
            break;
          }
        }
      } catch {
        failed++;
      }
      announceAnalysesChanged();
    }
    setBatch(null);
    if (failed) alert(`${failed}개 CR 분석에 실패했습니다 (LLM 설정·네트워크를 확인하세요)`);
  };

  if (!rows.length) {
    return (
      <div className="cr-browser">
        <p className="empty">이 스펙의 Change history 에서 CR 을 찾지 못했습니다.</p>
      </div>
    );
  }

  return (
    <div className="cr-browser">
      <div className="cr-filters">
        <input type="search" placeholder="CR 번호, 제목, Tdoc, WI, 절, 분석 내용…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={rel} onChange={(e) => setRel(e.target.value)}>
          <option value="">모든 릴리즈</option>
          {releases.map((r) => <option key={r} value={r}>Rel-{r}</option>)}
        </select>
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          <option value="">모든 카테고리</option>
          {Object.entries(CAT).map(([k, v]) => <option key={k} value={k}>{k} · {v}</option>)}
        </select>
        <select value={wi} onChange={(e) => setWi(e.target.value)}
                title="Work item code (CR 표지). 고르면 받은 모든 스펙에 걸친 이 WI 의 요약이 위에 나옵니다">
          <option value="">모든 WI</option>
          {wis.map(([w, n]) => <option key={w} value={w}>{w} ({n})</option>)}
        </select>
        <select value={only} onChange={(e) => setOnly(e.target.value as typeof only)}>
          <option value="">AI 분석 전체</option>
          <option value="analysed">분석 있음 ({analyses.size})</option>
          <option value="not">분석 없음</option>
        </select>
      </div>
      {wi && <WISummaryPanel wi={wi} spec={spec} byNumber={byNumber} llmReady={llmReady} onJump={onJump}
                             onOpenSpec={onOpenSpec} />}
      <p className="small muted cr-browser-status">
        {shown.length} / {rows.length}개 CR · 표지 {covers}개
        {covers < rows.length && onFetchCrs && (
          <> · <button className="link" disabled={!!fetching} onClick={onFetchCrs}>
            {fetching ? "표지 받는 중…" : "CR 표지 받기"}</button></>
        )}
        {llmReady && !batch && todo.length > 0 && (
          <> · <button className="link" title="지금 목록에서 표지가 있고 분석이 없는 CR 을 하나씩 분석합니다"
                       onClick={() => confirm(`CR ${todo.length}개를 차례로 AI 분석합니다. 진행할까요?`) && runBatch(todo)}>
            분석 없는 {todo.length}개 AI 분석</button></>
        )}
        {batch && (
          <> · <span className="job">AI 분석 {batch.done + 1}/{batch.total} ({batch.current})</span>{" "}
            <button className="link" onClick={() => { stop.current = true; }}>중지</button></>
        )}
      </p>
      <table className="cr-table">
        <colgroup>
          <col style={{ width: 84 }} /><col style={{ width: 64 }} /><col style={{ width: 36 }} /><col />
          <col style={{ width: 190 }} /><col style={{ width: 92 }} /><col style={{ width: 30 }} />
        </colgroup>
        <thead>
          <tr><th>버전</th><th>CR</th><th>Cat</th><th>제목</th><th>WI</th><th>회의</th><th /></tr>
        </thead>
        <tbody>
          {shown.map((c) => {
            const k = `${c.new_version}/${c.tdoc}/${c.cr}/${c.rev}`;  // the same CR can be listed twice
            const a = analyses.get(crKey(c.cr, c.tdoc));
            const isOpen = open === k;
            return [
              <tr key={k} className={isOpen ? "open" : ""} onClick={() => setOpen(isOpen ? null : k)}>
                <td><span className="vchip" style={{ background: colors.get(c.lib_version ?? "")?.solid }} />{c.new_version}</td>
                <td className="num">{c.cr}{c.rev && c.rev !== "-" ? ` r${c.rev}` : ""}</td>
                <td><span className={`cat cat-${c.cat}`} title={CAT[c.cat] ?? ""}>{c.cat}</span></td>
                <td>{c.subject}{a && <div className="small muted cr-sum">{a.summary}</div>}</td>
                <td className="small">{wiCodes(c).join(", ")}</td>
                <td className="small muted nowrap">{c.meeting}</td>
                <td>{a ? <span className="analysed tiny" title="AI 분석 있음">AI</span> : null}</td>
              </tr>,
              isOpen && (
                <tr key={`${k}-d`} className="cr-row-detail">
                  <td colSpan={7}>
                    <CRRowDetail cr={c} a={a} byNumber={byNumber} onJump={onJump} />
                  </td>
                </tr>
              ),
            ];
          })}
        </tbody>
      </table>
    </div>
  );
}

function CRRowDetail({ cr, a, byNumber, onJump }: {
  cr: CR; a?: CRAnalysis; byNumber: Map<string, TocEntry>; onJump: (ord: number) => void;
}) {
  const d = cr.detail;
  return (
    <div className="small">
      <p>
        <button className="link" onClick={() => openCRDoc(cr.spec, cr.cr, cr.tdoc)}>
          CR 문서{a ? "와 전체 AI 분석" : ""} (새 창)</button>
        {" · "}<a href={tdocUrl(cr.tdoc)} target="_blank" rel="noreferrer">{cr.tdoc}</a>
        {d?.wg_tdoc && <> · <a href={tdocUrl(d.wg_tdoc)} target="_blank" rel="noreferrer">{d.wg_tdoc}</a></>}
        {cr.lib_version && <span className="muted"> · 받은 버전 중 v{cr.lib_version} 에 반영</span>}
      </p>
      {d ? (
        <>
          {d.clauses.length > 0 && (
            <p><span className="muted">바뀐 절</span>{" "}
              {d.clauses.map((n) => {
                const t = byNumber.get(n);
                return t
                  ? <button key={n} className="link cr-clause" title={t.title} onClick={() => onJump(t.ord)}>§{n}</button>
                  : <span key={n} className="muted cr-clause">§{n}</span>;
              })}
            </p>
          )}
          {a && (
            <div className="intent compact">
              <p><b>AI 요약</b> {a.summary}</p>
              {a.decision && <p><b>회의 결정</b> {a.decision}</p>}
              {a.impact && <p><b>구현 영향</b> {a.impact}</p>}
            </div>
          )}
          {d.reason && <p><b>Reason for change</b><br />{d.reason}</p>}
          {d.summary && <p><b>Summary of change</b><br />{d.summary}</p>}
        </>
      ) : <p className="muted">CR 표지를 아직 받지 않았습니다.</p>}
    </div>
  );
}
