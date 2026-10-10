// LLM API settings and change-intent generation (Library page). Uses the user's own key; optional.
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

  const pickProvider = (p: string) => {
    setProvider(p);
    const preset = saved?.presets[p];
    setBaseUrl(preset?.base_url ?? "");
    setModel(preset?.model ?? "");
    setModels([]);
    setStatus(null);
  };

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
    load(await api.saveLlm({ clear_key: true }));
    setStatus({ kind: "ok", text: "저장된 키를 지웠습니다" });
  });

  const keyPlaceholder = saved?.key_source === "saved" ? `저장됨 ${saved.key_hint} (바꾸려면 새 키 입력)`
    : saved?.key_source === "env" ? `환경 변수의 키 사용 중 ${saved.key_hint}`
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
            {saved?.key_source === "saved" && <button onClick={clearKey} disabled={busy}>키 삭제</button>}
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
