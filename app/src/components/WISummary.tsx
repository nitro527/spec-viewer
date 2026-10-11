// One work item (feature) across every downloaded spec: its CRs per spec and an AI summary made from their covers
// and stored CR analyses - what the feature is, why, what each spec got, how it evolved, implementation impact.
import { useCallback, useEffect, useState } from "react";
import { api, type TocEntry, type WorkItem } from "../api";
import { useAiJob } from "./CRAnalysis";

const NUM = /\d+(?:\.\d+)+[A-Za-z]?|\b\d+\b/g;

export function WISummaryPanel({ wi, spec, byNumber, llmReady, onJump, onOpenSpec }: {
  wi: string; spec: string; byNumber: Map<string, TocEntry>; llmReady: boolean;
  onJump: (ord: number) => void; onOpenSpec: (spec: string, clause?: string) => void;
}) {
  const [data, setData] = useState<WorkItem | null>(null);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((n) => n + 1), []);
  const job = useAiJob(refresh);
  useEffect(() => {
    let live = true;
    api.workItem(wi).then((x) => live && setData(x), () => live && setData(null));
    return () => { live = false; };
  }, [wi, reload]);

  if (!data) return <section className="wi-summary"><p className="muted small">{wi} 불러오는 중…</p></section>;
  const perSpec = new Map<string, number>();
  data.crs.forEach((c) => perSpec.set(c.spec, (perSpec.get(c.spec) ?? 0) + 1));
  const analysed = data.crs.filter((c) => c.analysis).length;
  const sm = data.summary;
  const run = (force: boolean) => job.start(() => api.summarizeWI(wi, force), `${wi} AI 요약 요청…`);

  const clauseLinks = (sp: string, text: string) => {
    const nums = text.match(NUM) ?? [];
    if (!nums.length) return text;
    return nums.map((n, i) => {
      const here = sp === spec ? byNumber.get(n) : undefined;
      return (
        <span key={i}>
          {i > 0 && ", "}
          {sp === spec
            ? here ? <button className="link" title={here.title} onClick={() => onJump(here.ord)}>{n}</button> : n
            : <button className="link" title={`TS ${sp} §${n} (새 뷰어 탭)`} onClick={() => onOpenSpec(sp, n)}>{n}</button>}
        </span>
      );
    });
  };

  return (
    <section className="wi-summary">
      <h3>
        {wi} <span className="muted small">
          · CR {data.crs.length}개 ({[...perSpec].map(([sp, n]) => `${sp} ${n}`).join(", ")}) · CR 분석 {analysed}/{data.crs.length}
        </span>
      </h3>
      {job.msg && <p className="small job">{job.msg}</p>}
      {sm ? (
        <div className="wi-body">
          <p className="lead">{sm.overview}</p>
          {sm.motivation && <><h4>도입 배경</h4><p>{sm.motivation}</p></>}
          {sm.spec_changes.length > 0 && (
            <><h4>스펙별 변경</h4>
              <ul>{sm.spec_changes.map((c, i) => (
                <li key={i}><b>TS {c.spec}</b> <span className="small">§{clauseLinks(c.spec, c.clauses)}</span> — {c.what}</li>
              ))}</ul></>
          )}
          {sm.timeline.length > 0 && (
            <><h4>진행 과정</h4>
              <ol className="wi-timeline">{sm.timeline.map((t, i) => <li key={i}><b>{t.when}</b> {t.what}</li>)}</ol></>
          )}
          {sm.impact && <><h4>구현 영향</h4><p>{sm.impact}</p></>}
          {sm.open_points && <><h4>이후 수정·주의점</h4><p>{sm.open_points}</p></>}
          <p className="muted tiny">
            신뢰도 {sm.confidence}{sm.model ? ` · ${sm.model}` : ""}
            {sm.generated ? ` · ${sm.generated.replace("T", " ").replace("+00:00", " UTC")}` : ""}
            {` · CR ${sm.crs}개(분석 ${sm.analysed}개) 기준`}
            {sm.crs !== data.crs.length && <b> · 그 뒤 CR 이 {data.crs.length}개로 바뀜</b>}
            {llmReady && <>{" · "}<button className="link tiny" disabled={job.busy} onClick={() => run(true)}>다시 요약</button></>}
          </p>
        </div>
      ) : llmReady ? (
        <p className="small">
          <button className="link" disabled={job.busy} onClick={() => run(false)}>
            AI 로 이 WI 요약 (받은 모든 스펙의 CR {data.crs.length}개)</button>
          {analysed < data.crs.length && (
            <span className="muted"> · CR 분석이 있으면 더 정확합니다 (아래 "분석 없는 N개 AI 분석" 먼저, 스펙마다)</span>
          )}
        </p>
      ) : <p className="muted small">AI 요약은 메인 탭에서 LLM 을 설정하면 쓸 수 있습니다.</p>}
    </section>
  );
}
