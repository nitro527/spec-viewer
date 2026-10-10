// Change intent: AI summaries made with the user's own LLM API, or synced from the annotation server
// (both optional; the viewer works without them).
import { useEffect, useState } from "react";
import { api, tdocUrl, type Annotation } from "../api";

export function AnnotationPanel({ spec, version, clauseKey, rawHash }: {
  spec: string; version: string; clauseKey: string; rawHash?: string;
}) {
  const [items, setItems] = useState<Annotation[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [llmReady, setLlmReady] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  useEffect(() => {
    api.annotations(spec, version, clauseKey).then(setItems, () => setItems([]));
  }, [spec, version, clauseKey, reload]);
  useEffect(() => {
    api.llm().then((s) => setLlmReady(s.has_key || s.provider !== "gemini"), () => setLlmReady(false));
  }, []);
  useEffect(() => { setMsg(null); setJobId(null); }, [clauseKey]);

  useEffect(() => {
    if (!jobId) return;
    const t = setInterval(async () => {
      const j = (await api.jobs()).find((x) => x.id === jobId);
      if (!j) return;
      if (j.state === "running") return setMsg(j.total ? `AI 요약 중 ${j.done}/${j.total}…` : "AI 요약 준비 중…");
      setMsg(j.message);
      setJobId(null);
      setReload((n) => n + 1);
    }, 1500);
    return () => clearInterval(t);
  }, [jobId]);

  const sync = async () => {
    setMsg("동기화 중…");
    try {
      const r = await api.syncAnnotations(spec);
      setMsg(`${r.packs}개 pack 받음`);
      setReload((n) => n + 1);
    } catch (e) {
      setMsg(String(e).replace(/^Error: /, ""));
    }
  };

  const summarize = async (force = false) => {
    setMsg(force ? "AI 다시 요약 요청…" : "AI 요약 요청…");
    try {
      const j = await api.makeIntents({ spec, version, clause_key: clauseKey, limit: 10, skip_uncovered: false, force });
      setJobId(j.id);
    } catch (e) {
      setMsg(String(e).replace(/^Error: /, ""));
    }
  };

  if (items === null) return null;
  const shown = rawHash ? items.filter((a) => a.raw_hash === rawHash) : items;
  const actions = (
    <p className="muted small">
      {llmReady
        ? <>
            <button className="link" onClick={() => summarize()} disabled={!!jobId}>AI 로 이 절 요약</button>
            {items.some((a) => a.intent) && <>{" · "}<button className="link" onClick={() => summarize(true)} disabled={!!jobId}
              title="이미 있는 요약도 최신 근거(세부 draft CR 등)로 다시 만듭니다">다시 요약</button></>}
          </>
        : <span title="메인 탭 → AI 변경 의도 요약에서 LLM API 를 설정하세요">AI 요약: 메인 탭에서 LLM 설정 필요</span>}
      {" · "}<button className="link" onClick={sync}>서버에서 동기화</button>
      {msg && <span> · {msg}</span>}
    </p>
  );
  return (
    <section className="annotations">
      <h4>변경 의도 <span className="muted small">AI 요약</span></h4>
      {shown.length === 0 && (
        <p className="muted small">
          {items.length ? "선택한 문단에 대한 요약이 없습니다." : "이 절에 대한 요약이 없습니다."}
        </p>
      )}
      {shown.map((a, i) => (
        <div key={i} className="intent">
          <div className="small muted">v{a.version} · {a.change}{a.crs.length ? ` · ${a.crs.map((c) => `CR${c.cr}`).join(", ")}` : ""}</div>
          {a.intent ? (
            <>
              <p><b>무엇이</b> {a.intent.summary}</p>
              <p><b>왜</b> {a.intent.motivation}</p>
              <p><b>구현 영향</b> {a.intent.impact}</p>
              {!!a.draft_crs?.length && (
                <p className="small">세부 CR{" "}
                  {a.draft_crs.map((d) => (
                    <a key={d.tdoc} href={tdocUrl(d.tdoc)} target="_blank" rel="noreferrer" title={`${d.title} (CR${d.parent_cr} 에 포함, ${d.meeting})`}>
                      {d.tdoc}</a>
                  )).reduce<React.ReactNode[]>((acc, x, i) => (i ? [...acc, ", ", x] : [x]), [])}
                </p>
              )}
              <p className="muted tiny">신뢰도 {a.intent.confidence}{a.intent.model ? ` · ${a.intent.model}` : ""} · AI 요약은 CR 원문으로 확인하세요</p>
            </>
          ) : <p className="muted small">의도 요약 없음{a.intent_error ? ` (${a.intent_error})` : " (CR 매핑만 있음)"}</p>}
          {a.agreements.map((g, j) => <blockquote key={j} className="small">{g.meeting && <b>{g.meeting} </b>}{g.text}</blockquote>)}
        </div>
      ))}
      {actions}
    </section>
  );
}
