// LLM API settings and change-intent generation (main screen). Uses the user's own key; optional.
import { useEffect, useState } from "react";
import { api, type Job, type LlmForm, type LlmSettings, type SpecInfo, type VersionInfo } from "../api";

const PROVIDERS = [
  { id: "gemini", label: "Google Gemini" },
  { id: "openai", label: "사내 LLM 서버 (OpenAI 호환)" },
];

type Status = { kind: "ok" | "error" | "info"; text: string } | null;

const errText = (e: unknown) => String(e).replace(/^Error: /, "");

export function LlmPanel({ specs, onJob }: { specs: SpecInfo[]; onJob: (j: Job) => void }) {
  const [saved, setSaved] = useState<LlmSettings | null>(null);
  const [provider, setProvider] = useState("gemini");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>(null);
  const [busy, setBusy] = useState(false);

  const load = (s: LlmSettings) => {
    setSaved(s); setProvider(s.provider); setBaseUrl(s.base_url); setModel(s.model); setKey("");
  };
  useEffect(() => { api.llm().then(load).catch(() => {}); }, []);

  const form = (): LlmForm => ({ provider, base_url: baseUrl, model, api_key: key || undefined });
  const dirty = !!saved && (provider !== saved.provider || baseUrl !== saved.base_url || model !== saved.model || !!key);
  const isGemini = provider === "gemini";

  const pickProvider = (p: string) => {  // each provider keeps its own address, model and key
    setProvider(p);
    const ps = saved?.providers[p];
    setBaseUrl(ps?.base_url ?? "");
    setModel(ps?.model ?? "");
    setKey("");
    setModels([]);
    setStatus(null);
  };
  const cur = saved?.providers[provider];

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(true);
    setStatus({ kind: "info", text: label });
    try { await fn(); } catch (e) { setStatus({ kind: "error", text: errText(e) }); } finally { setBusy(false); }
  };

  const save = () => run("저장 중…", async () => {
    load(await api.saveLlm(form()));
    setStatus({ kind: "ok", text: "저장했습니다" });
  });
  const test = () => run("연결 확인 중… (요청 1회 사용)", async () => {
    const r = await api.testLlm(form());
    setStatus({ kind: "ok", text: `연결됨 · ${r.model} · ${r.seconds}초 · 응답 "${r.reply}"` });
  });
  const fetchModels = () => run("모델 목록 불러오는 중…", async () => {
    const r = await api.llmModels(form());
    setModels(r.models);
    setStatus({ kind: "ok", text: `모델 ${r.models.length}개 — 모델 칸을 눌러 고르세요` });
  });
  const clearKey = () => run("키 삭제 중…", async () => {
    load(await api.saveLlm({ provider, clear_key: true }));
    setStatus({ kind: "ok", text: "저장된 키를 지웠습니다" });
  });

  const keyPlaceholder = cur?.key_source === "saved" ? `저장됨 ${cur.key_hint} (바꾸려면 새 키 입력)`
    : cur?.key_source === "env" ? `환경 변수의 키 사용 중 ${cur.key_hint}`
    : isGemini ? "AIza… 로 시작하는 키" : "키가 필요 없는 서버면 비워두세요";

  return (
    <section className="llm">
      <h2>AI 변경 의도 요약 <span className="muted small">(LLM API · 선택)</span></h2>
      <p className="muted small">
        바뀐 문단마다 “무엇이 · 왜 · 구현 영향”을 CR 표지를 근거로 AI 가 요약합니다.
        ① 연결 설정 → ② 연결 테스트 → ③ 요약 만들기 순서로 진행하세요. 없어도 뷰어는 그대로 동작합니다.
      </p>

      <div className="step">
        <h3>① 연결 설정</h3>
        <div className="seg">
          {PROVIDERS.map((p) => (
            <button key={p.id} className={provider === p.id ? "on" : ""} onClick={() => pickProvider(p.id)}>{p.label}</button>
          ))}
        </div>
        <p className="muted small">
          {isGemini ? (
            <>API 키 발급: <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">aistudio.google.com/apikey</a>
              {" "}· 무료 등급은 모델마다 하루 요청 수가 적습니다(약 20회). 많이 쓰려면 결제를 켜세요.</>
          ) : (
            <>vLLM · Ollama · LM Studio 처럼 <code>/v1/chat/completions</code> 를 제공하는 서버.
              예: <code>http://llm.corp:8000/v1</code>, Ollama 는 <code>http://localhost:11434/v1</code></>
          )}
        </p>
        <div className="llm-grid">
          <label htmlFor="llm-url">서버 주소</label>
          <input id="llm-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                 placeholder="http://llm-server:8000/v1" />
          <label htmlFor="llm-model">모델</label>
          <div className="row">
            <input id="llm-model" list="llm-models" value={model} onChange={(e) => setModel(e.target.value)}
                   placeholder={isGemini ? "gemini-3.8-flash" : "glm-5.3"} />
            <datalist id="llm-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
            <button onClick={fetchModels} disabled={busy}>목록 불러오기</button>
          </div>
          <label htmlFor="llm-key">API 키</label>
          <div className="row">
            <input id="llm-key" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
                   placeholder={keyPlaceholder} />
            {cur?.key_source === "saved" && <button onClick={clearKey} disabled={busy}>키 삭제</button>}
          </div>
        </div>
        <div className="row actions">
          <button className="primary" onClick={save} disabled={busy || !dirty}>저장</button>
          <button onClick={test} disabled={busy}>② 연결 테스트</button>
          {dirty && <span className="small muted">저장 안 됨 (테스트는 입력한 값으로 합니다)</span>}
        </div>
        {status && <p className={`small status-${status.kind}`}>{status.text}</p>}
        <p className="muted tiny">키는 이 PC 의 라이브러리 파일(~/.specview)에만 저장되고, 요청은 이 PC 에서 LLM 서버로 바로 갑니다.</p>
      </div>

      <IntentRunner specs={specs} ready={!!saved && (saved.has_key || !isGemini) && !dirty} onJob={onJob} />
      <FullRunner specs={specs} ready={!!saved && (saved.has_key || !isGemini) && !dirty} />
    </section>
  );
}

function IntentRunner({ specs, ready, onJob }: { specs: SpecInfo[]; ready: boolean; onJob: (j: Job) => void }) {
  const [spec, setSpec] = useState("");
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [version, setVersion] = useState("");
  const [limit, setLimit] = useState(20);
  const [skipUncovered, setSkipUncovered] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (!spec && specs.length) setSpec(specs[0].number); }, [specs, spec]);
  useEffect(() => {
    if (!spec) return;
    api.versions(spec).then((vs) => { setVersions(vs); setVersion(vs[vs.length - 1]?.version ?? ""); }, () => setVersions([]));
  }, [spec]);

  const start = async () => {
    setError(null);
    try {
      onJob(await api.makeIntents({ spec, version, limit: limit > 0 ? limit : null, skip_uncovered: skipUncovered }));
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <div className="step">
      <h3>③ 요약 만들기</h3>
      {!specs.length ? <p className="muted small">먼저 위에서 스펙을 받으세요.</p> : (
        <>
          <div className="row">
            <select value={spec} onChange={(e) => setSpec(e.target.value)}>
              {specs.map((s) => <option key={s.number} value={s.number}>TS {s.number}</option>)}
            </select>
            <select value={version} onChange={(e) => setVersion(e.target.value)}>
              {versions.slice(1).map((v) => <option key={v.version} value={v.version}>v{v.version} 에서 바뀐 문단</option>)}
            </select>
            <label className="small">최대 요청
              <input type="number" min={0} value={limit} className="num" onChange={(e) => setLimit(Number(e.target.value))} />개
            </label>
          </div>
          <label className="small">
            <input type="checkbox" checked={skipUncovered} onChange={(e) => setSkipUncovered(e.target.checked)} />
            {" "}CR 이 연결되지 않은 변경 건너뛰기 (표지·저작권 연도 등, 요청 절약)
          </label>
          <div className="row actions">
            <button className="primary" onClick={start} disabled={!ready || !version}>요약 시작</button>
            {!ready && <span className="small muted">①에서 설정을 저장하면 시작할 수 있습니다</span>}
          </div>
          {error && <p className="small status-error">{error}</p>}
          <p className="muted tiny">
            이미 요약된 문단은 다시 요청하지 않습니다(0 = 제한 없음). 절 하나만 하려면 문서에서 문단을 선택하고
            오른쪽 “변경 의도”의 <b>AI 로 요약</b>을 누르세요. 진행 상황은 바로 아래에 나옵니다.
          </p>
        </>
      )}
    </div>
  );
}

/** "④ 전체 분석": every changed paragraph of every version of every spec, then the CRs left, then every work item.
 *  Meant to run unattended (overnight); it saves as it goes, skips what is done and can be stopped any time. */
function FullRunner({ specs, ready }: { specs: SpecInfo[]; ready: boolean }) {
  const [job, setJob] = useState<Job | null>(null);
  const [chosen, setChosen] = useState<Set<string> | null>(null);  // null: every spec
  const [skipUncovered, setSkipUncovered] = useState(true);
  const [crs, setCrs] = useState(true);
  const [wis, setWis] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {  // pick up a run started earlier (the app tab was reopened)
    const load = () => api.jobs().then((js) => {
      const full = js.filter((j) => j.full);
      setJob(full.find((j) => j.state === "running") ?? full[full.length - 1] ?? null);
    }, () => undefined);
    load();
    const t = setInterval(() => { load(); setNow(Date.now()); }, 3000);
    return () => clearInterval(t);
  }, []);

  const running = job?.state === "running";
  const picked = chosen ?? new Set(specs.map((x) => x.number));
  const start = async () => {
    setError(null);
    const list = specs.map((x) => x.number).filter((n) => picked.has(n));
    if (!confirm(`스펙 ${list.length}개의 모든 버전을 AI 로 분석합니다. 요청 수가 많아 몇 시간 걸릴 수 있습니다.\n`
      + "이미 분석한 것은 건너뛰고, 중간에 멈추거나 앱을 꺼도 다시 시작하면 이어서 합니다. 시작할까요?")) return;
    try {
      setJob(await api.fullAnalysis({ specs: chosen ? list : null, skip_uncovered: skipUncovered, cr_analyses: crs, wi_summaries: wis }));
    } catch (e) {
      setError(errText(e));
    }
  };
  const elapsed = (j: Job) => {
    if (!j.started) return "";
    const ms = (j.finished ? Date.parse(j.finished) : now) - Date.parse(j.started);
    const m = Math.max(0, Math.round(ms / 60000));
    return m >= 60 ? `${Math.floor(m / 60)}시간 ${m % 60}분` : `${m}분`;
  };

  return (
    <div className="step full-run">
      <h3>④ 전체 분석 <span className="muted small">(퇴근할 때 걸어 두기)</span></h3>
      <p className="small muted">
        받은 스펙의 <b>모든 버전</b>에서 바뀐 문단을 최신 버전부터 요약하고(관련 CR 분석 포함), 이어서 남은 CR 분석과
        모든 WI 요약을 만듭니다. 조금씩 바로 저장하므로 중간에 멈추거나 앱을 꺼도 다시 시작하면 이어서 합니다.
        LLM 서버가 잠시 끊기면 기다렸다가 다시 시도하고, 도는 동안 PC 절전을 막습니다(Windows).
      </p>
      {!running && (
        <>
          <div className="row full-specs">
            {specs.map((x) => (
              <label key={x.number} className="small">
                <input type="checkbox" checked={picked.has(x.number)} onChange={(e) => {
                  const n = new Set(picked);
                  if (e.target.checked) n.add(x.number); else n.delete(x.number);
                  setChosen(n.size === specs.length ? null : n);
                }} /> {x.number}
              </label>
            ))}
          </div>
          <label className="small"><input type="checkbox" checked={skipUncovered} onChange={(e) => setSkipUncovered(e.target.checked)} />
            {" "}CR 이 연결되지 않은 변경 건너뛰기 (표지·저작권 연도 등)</label>
          <label className="small"><input type="checkbox" checked={crs} onChange={(e) => setCrs(e.target.checked)} />
            {" "}남은 CR 도 모두 분석 (표지가 있는 CR)</label>
          <label className="small"><input type="checkbox" checked={wis} onChange={(e) => setWis(e.target.checked)} />
            {" "}마지막에 모든 WI 요약</label>
          <div className="row actions">
            <button className="primary" onClick={start} disabled={!ready || !picked.size}>전체 분석 시작</button>
            {!ready && <span className="small muted">①에서 설정을 저장하면 시작할 수 있습니다</span>}
          </div>
        </>
      )}
      {error && <p className="small status-error">{error}</p>}
      {job && (
        <div className={`full-status ${job.state}`}>
          <p className="small"><b>{running ? "진행 중" : job.state === "done" && !job.stopped ? "끝남" : "멈춤"}</b>
            {" · "}{elapsed(job)}{running && job.total > 0 && ` · 이 버전 ${job.done}/${job.total}`}</p>
          <p className="small">{job.message}</p>
          {job.counts && (
            <p className="small muted">새로 만든 것: 문단 요약 {job.counts.intents} · CR 분석 {job.counts.cr_analyses}
              {" · "}WI 요약 {job.counts.wi_summaries}{job.counts.failed ? ` · 실패 ${job.counts.failed}` : ""}</p>
          )}
          {running && <button onClick={() => api.cancelJob(job.id).then(setJob, () => undefined)}>중지</button>}
        </div>
      )}
    </div>
  );
}
