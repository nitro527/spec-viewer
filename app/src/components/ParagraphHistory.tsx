// Every text one paragraph had, oldest first: what changed at each step, the CRs behind it, their CR analyses and
// the AI summary of that step. Steps can be summarized one by one or all at once.
import { useCallback, useEffect, useState } from "react";
import { api, crKey, type LineageStep } from "../api";
import type { VersionColor } from "../colors";
import { CRAnalysisCard, useAiJob, useCRAnalyses } from "./CRAnalysis";
import { openCRDoc } from "./DocWindow";
import { Html } from "./Html";

const CHANGE: Record<string, string> = { base: "처음 버전", added: "추가", modified: "변경", moved: "이동" };

export function ParagraphHistory({ spec, version, clauseKey, rawHash, colors }: {
  spec: string; version: string; clauseKey: string; rawHash: string; colors: Map<string, VersionColor>;
}) {
  const [llmReady, setLlmReady] = useState(false);
  useEffect(() => {
    api.llm().then((s) => setLlmReady(s.has_key || s.provider !== "gemini"), () => setLlmReady(false));
  }, []);
  const [steps, setSteps] = useState<LineageStep[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const analyses = useCRAnalyses(spec);
  const refresh = useCallback(() => setReload((n) => n + 1), []);
  const job = useAiJob(refresh);

  useEffect(() => {
    setError(null);
    api.lineage(spec, version, clauseKey, rawHash).then(setSteps, (e) => { setSteps([]); setError(String(e)); });
  }, [spec, version, clauseKey, rawHash, reload]);

  if (steps === null) return <p className="muted small">변화 이력 불러오는 중…</p>;
  if (error) return <p className="muted small">변화 이력을 불러오지 못했습니다.</p>;
  const todo = steps.filter((s) => s.change !== "base" && !s.annotation?.intent && s.crs.length);

  const summarize = (targets: LineageStep[], force = false) => job.start(() => api.makeIntents({
    spec, version, limit: targets.length * 2, skip_uncovered: false, force,
    targets: targets.map((s) => ({ version: s.version, clause_key: s.clause_key, raw_hash: s.raw_hash })),
  }), targets.length > 1 ? `변화 ${targets.length}단계 AI 요약 요청…` : "이 단계 AI 요약 요청…");

  return (
    <section className="para-history">
      <h4>문단 변화 이력 <span className="muted small">{steps.length}개 버전 · 오래된 것부터</span></h4>
      {llmReady && todo.length > 1 && (
        <p className="small"><button className="link" disabled={job.busy} onClick={() => summarize(todo)}
          title="CR 이 연결된 단계 중 요약이 없는 것을 모두 요약합니다. 관련 CR 분석도 함께 만들어 저장합니다">
          요약 없는 {todo.length}단계 모두 AI 요약</button></p>
      )}
      {job.msg && <p className="small job">{job.msg}</p>}
      <ol className="lineage">
        {steps.map((s, i) => {
          const last = i === steps.length - 1;
          const a = s.annotation?.intent;
          return (
            <li key={`${s.version}-${i}`}>
              <div className="lineage-head">
                <span className="chip" style={{ background: colors.get(s.version)?.solid }} />
                <b>v{s.version}</b> {CHANGE[s.change] ?? s.change}
                {s.clause_key !== clauseKey && <span className="muted tiny"> (§{s.clause})</span>}
                {s.crs.map((c) => (
                  <button key={c.tdoc + c.cr} className="link tiny cr-tag" title={`${c.subject} — CR 문서 (새 창)`}
                          onClick={() => openCRDoc(spec, c.cr, c.tdoc)}>CR{c.cr}</button>
                ))}
              </div>
              {i > 0 && (
                <details open={last}>
                  <summary className="muted small">바뀐 부분</summary>
                  <div className="lineage-diff"><Html html={s.diff} /></div>
                </details>
              )}
              {a ? (
                <div className="intent compact small">
                  <p><b>무엇이</b> {a.summary}</p>
                  <p><b>왜</b> {a.motivation}</p>
                  {a.impact && <p><b>구현 영향</b> {a.impact}</p>}
                  <p className="muted tiny">신뢰도 {a.confidence}{a.model ? ` · ${a.model}` : ""}
                    {llmReady && <>{" · "}<button className="link tiny" disabled={job.busy} onClick={() => summarize([s], true)}>다시 요약</button></>}
                  </p>
                </div>
              ) : s.change !== "base" && llmReady && (
                <p className="small"><button className="link" disabled={job.busy} onClick={() => summarize([s])}>
                  이 단계 AI 요약</button>{!s.crs.length && <span className="muted tiny"> (연결된 CR 없음)</span>}</p>
              )}
              {s.crs.map((c) => analyses.get(crKey(c.cr, c.tdoc)))
                .filter((x) => !!x)
                .map((x) => <CRAnalysisCard key={x!.tdoc + x!.cr} a={x!} spec={spec} />)}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
