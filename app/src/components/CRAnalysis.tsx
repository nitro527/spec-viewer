// CR-level analysis: made once per CR (with the paragraph summaries, or on its own) and reused by every paragraph
// the CR touched. Compact card for the side panel, full view for the CR document window.
import { useCallback, useEffect, useState } from "react";
import { api, crKey, tdocUrl, type CRAnalysis } from "../api";
import { findKey, openCRDoc, openMeetingDoc } from "./DocWindow";

const CHANGED = "cr-analyses-changed";

/** Stored analyses of a spec, keyed by crKey(cr, tdoc); refreshed whenever any view makes a new one. */
export function useCRAnalyses(spec: string): Map<string, CRAnalysis> {
  const [map, setMap] = useState<Map<string, CRAnalysis>>(new Map());
  useEffect(() => {
    let live = true;
    const load = () => api.crAnalyses(spec).then(
      (rows) => live && setMap(new Map(rows.map((a) => [crKey(a.cr, a.tdoc), a]))), () => undefined);
    load();
    window.addEventListener(CHANGED, load);
    // the CR window is another browser window: it tells this one through storage events
    const onStorage = (e: StorageEvent) => e.key === CHANGED && load();
    window.addEventListener("storage", onStorage);
    return () => { live = false; window.removeEventListener(CHANGED, load); window.removeEventListener("storage", onStorage); };
  }, [spec]);
  return map;
}

export function announceAnalysesChanged() {
  window.dispatchEvent(new Event(CHANGED));
  try { localStorage.setItem(CHANGED, String(Date.now())); } catch { /* storage may be unavailable */ }
}

/** Whether AI requests can be made: Gemini needs a key, an OpenAI-compatible (in-house) server may not. */
export function useLlmReady(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let live = true;
    api.llm().then((s) => live && setReady(s.has_key || s.provider !== "gemini"), () => live && setReady(false));
    return () => { live = false; };
  }, []);
  return ready;
}

/** Run an AI job and follow it until it ends; `done` gets the final message. */
export function useAiJob(done?: () => void) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (!jobId) return;
    const t = setInterval(async () => {
      const j = (await api.jobs()).find((x) => x.id === jobId);
      if (!j) return;
      if (j.state === "running") return setMsg(j.message || "AI 작업 중…");
      setMsg(j.message);
      setJobId(null);
      announceAnalysesChanged();
      done?.();
    }, 1500);
    return () => clearInterval(t);
  }, [jobId, done]);
  const start = useCallback(async (run: () => Promise<{ id: string }>, label: string) => {
    setMsg(label);
    try {
      setJobId((await run()).id);
    } catch (e) {
      setMsg(String(e).replace(/^Error: /, ""));
    }
  }, []);
  return { busy: !!jobId, msg, start, setMsg };
}

export function CRAnalysisCard({ a, spec }: { a: CRAnalysis; spec: string }) {
  return (
    <details className="cr-analysis small">
      <summary><b>CR{a.cr}</b> 분석 · {a.title}</summary>
      <p>{a.summary}</p>
      {a.background && <p><b>배경</b> {a.background}</p>}
      {a.decision && <p><b>회의 결정</b> {a.decision}</p>}
      <p className="muted tiny">신뢰도 {a.confidence}{a.model ? ` · ${a.model}` : ""}{" · "}
        <button className="link tiny" onClick={() => openCRDoc(spec, a.cr, a.tdoc)}>CR 문서와 전체 분석 (새 창)</button></p>
    </details>
  );
}

export function CRAnalysisFull({ a }: { a: CRAnalysis }) {
  const ev = a.evidence;
  return (
    <section className="cr-analysis-full">
      <p className="lead">{a.summary}</p>
      {a.background && <><h4>배경</h4><p>{a.background}</p></>}
      {a.changes.length > 0 && (
        <><h4>절별 변경</h4>
          <ul>{a.changes.map((c, i) => <li key={i}><b>{c.clause}</b> {c.what}</li>)}</ul></>
      )}
      {a.decision && <><h4>회의 결정과 대안</h4><p>{a.decision}</p></>}
      {a.impact && <><h4>구현 영향</h4><p>{a.impact}</p></>}
      <p className="muted tiny">신뢰도 {a.confidence}{a.model ? ` · ${a.model}` : ""}
        {a.generated ? ` · ${a.generated.replace("T", " ").replace("+00:00", " UTC")}` : ""} · AI 분석은 아래 CR 원문으로 확인하세요</p>
      {ev && (ev.drafts.length > 0 || ev.agreements.length > 0 || ev.fl_summaries.length > 0) && (
        <details className="small">
          <summary>분석에 쓴 근거 (세부 CR {ev.drafts.length} · 회의 합의 {ev.agreements.length} · FL 요약 {ev.fl_summaries.length})</summary>
          {ev.drafts.length > 0 && <p>세부 CR {ev.drafts.map((d) => (
            <a key={d.tdoc} href={tdocUrl(d.tdoc)} target="_blank" rel="noreferrer" title={d.title}>{d.tdoc}</a>
          )).reduce<React.ReactNode[]>((acc, x, i) => (i ? [...acc, ", ", x] : [x]), [])}</p>}
          {ev.agreements.map((g, i) => (
            <div key={i} className="agreement">
              <b>{g.meeting}</b> · {g.agenda || "(안건 없음)"} · {g.kind ?? "Agreement"}
              <blockquote>{g.text}</blockquote>
              {g.folder && <button className="link tiny" onClick={() => openMeetingDoc(g.folder!, undefined, findKey(g.text))}>회의록에서 보기</button>}
            </div>
          ))}
          {ev.fl_summaries.map((f) => (
            <div key={f.tdoc} className="agreement">
              <b>{f.meeting}</b> · {f.tdoc} {f.title}
              <blockquote>{f.excerpt}</blockquote>
              <button className="link tiny" onClick={() => openMeetingDoc(f.url.split("/Docs/")[0], f.tdoc, findKey(f.excerpt))}>문서 보기</button>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
