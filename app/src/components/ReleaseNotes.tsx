// AI release notes between two versions (Compare view): the CRs in between, grouped by work item, the
// corrections that matter and what an implementer has to check. Stored, exportable as HTML or markdown.
import { useCallback, useEffect, useState } from "react";
import { api, type ReleaseNote } from "../api";
import { download, releaseNoteHtml, releaseNoteMarkdown } from "../export";
import { useAiJob, useLlmReady } from "./CRAnalysis";

export function ReleaseNotes({ spec, oldV, newV }: { spec: string; oldV: string; newV: string }) {
  const llmReady = useLlmReady();
  const [info, setInfo] = useState<{ note: ReleaseNote | null; crs: number; analysed: number; changed_clauses: number } | null>(null);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((n) => n + 1), []);
  const job = useAiJob(refresh);
  useEffect(() => {
    let live = true;
    setInfo(null);
    api.releaseNotes(spec, oldV, newV).then((x) => live && setInfo(x), () => live && setInfo(null));
    return () => { live = false; };
  }, [spec, oldV, newV, reload]);

  if (!info) return <section className="release-notes"><p className="muted small">릴리즈 노트 불러오는 중…</p></section>;
  const n = info.note;
  const run = (force: boolean) => job.start(() => api.makeReleaseNotes(spec, oldV, newV, force), "릴리즈 노트 AI 요청…");
  return (
    <section className="release-notes">
      <h3>AI 릴리즈 노트 <span className="muted small">v{oldV} → v{newV} · CR {info.crs}개 (AI 분석 {info.analysed}개) · 바뀐 절 {info.changed_clauses}개</span>
        {n && (
          <span className="rn-actions">
            <button className="link small" onClick={() => navigator.clipboard?.writeText(releaseNoteMarkdown(spec, oldV, newV, n))}>⧉ Markdown 복사</button>
            <button className="link small" onClick={() => download(`TS${spec}_v${oldV}_to_v${newV}_release_notes.html`, releaseNoteHtml(spec, oldV, newV, n))}>⬇ HTML</button>
          </span>
        )}
      </h3>
      {job.msg && <p className="small job">{job.msg}</p>}
      {n ? (
        <>
          <p className="lead">{n.overview}</p>
          {n.features.length > 0 && (
            <><h4>기능 추가·변경 (WI 별)</h4>
              <ul>{n.features.map((f, i) => <li key={i}><b>{f.wi}</b> {f.what} <span className="muted small">(CR {f.crs} · §{f.clauses})</span></li>)}</ul></>
          )}
          {n.corrections.length > 0 && (
            <><h4>주요 수정</h4>
              <ul>{n.corrections.map((c, i) => <li key={i}>{c.what} <span className="muted small">(CR {c.crs} · §{c.clauses})</span></li>)}</ul></>
          )}
          {n.impact && <><h4>구현 영향</h4><p>{n.impact}</p></>}
          <p className="muted tiny">신뢰도 {n.confidence}{n.model ? ` · ${n.model}` : ""}
            {n.generated ? ` · ${n.generated.replace("T", " ").replace("+00:00", " UTC")}` : ""}
            {n.crs !== info.crs && <b> · 그 뒤 CR 이 {info.crs}개로 바뀜</b>}
            {llmReady && <>{" · "}<button className="link tiny" disabled={job.busy} onClick={() => run(true)}>다시 만들기</button></>}</p>
        </>
      ) : !info.crs ? (
        <p className="muted small">이 두 버전 사이에 Change history 의 CR 이 없습니다.</p>
      ) : llmReady ? (
        <p className="small">
          <button className="link" disabled={job.busy} onClick={() => run(false)}>AI 로 릴리즈 노트 만들기 (CR {info.crs}개)</button>
          {info.analysed < info.crs && <span className="muted"> · CR 분석이 있으면 더 정확합니다 (CR 모드의 "분석 없는 N개 AI 분석" 또는 전체 분석)</span>}
        </p>
      ) : <p className="muted small">AI 릴리즈 노트는 메인 탭에서 LLM 을 설정하면 쓸 수 있습니다.</p>}
    </section>
  );
}
