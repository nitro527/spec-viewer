// Main screen: the 3GPP downloader, downloaded specs and settings (viewing happens in viewer tabs).
import { useEffect, useState } from "react";
import { api, type Job, type SpecInfo } from "../api";
import { CitationMap } from "./CitationMap";
import { KeepUp } from "./KeepUp";
import { LlmPanel } from "./LlmPanel";
import { SpecCatalog } from "./SpecCatalog";

const RELEASES = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20];  // LTE from Rel-8, NR from Rel-15

export function Home({ prefill, active, onOpen }: {
  prefill?: string; active: boolean; onOpen: (spec: string, clause?: string, version?: string, baseline?: string) => void;
}) {
  const [specs, setSpecs] = useState<SpecInfo[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [input, setInput] = useState("");
  const [selected, setSelected] = useState<string[]>(prefill ? [prefill] : []);
  const toggle = (n: string) => setSelected((xs) => (xs.includes(n) ? xs.filter((x) => x !== n) : [...xs, n]));
  const [rels, setRels] = useState<number[]>([]);
  const [latestOnly, setLatestOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [annUrl, setAnnUrl] = useState("");
  const [annSaved, setAnnSaved] = useState(false);
  const refresh = () => api.specs().then(setSpecs, (e) => setError(String(e)));
  useEffect(() => { refresh(); api.settings().then((s) => setAnnUrl(s.annotation_url)).catch(() => {}); }, []);
  useEffect(() => {  // e.g. a reference to a spec not downloaded
    if (prefill) setSelected((xs) => (xs.includes(prefill) ? xs : [...xs, prefill]));
  }, [prefill]);
  useEffect(() => { if (active) refresh(); }, [active]);

  const running = jobs.some((j) => j.state === "running");
  const downloading = jobs.some((j) => j.state === "running" && j.kind !== "intent");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => api.jobs().then((js) => {
      setJobs(js);
      if (!js.some((j) => j.state === "running")) refresh();
    }), 1000);
    return () => clearInterval(t);
  }, [running]);

  const start = async () => {
    const typed = input.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
    const bad = typed.filter((s) => !/^\d{2}\.\d{3}(-\d{1,2}){0,2}$/.test(s));
    if (bad.length) return setError(`스펙 번호 형식이 아닙니다: ${bad.join(", ")} (예: 38.211, 38.101-1)`);
    const list = [...new Set([...selected, ...typed])];
    if (!list.length) return setError("받을 스펙을 위에서 고르세요");
    setError(null);
    try {
      const job = await api.fetchSpecs(list, rels.length ? rels : null, latestOnly);
      setJobs((js) => [...js, job]);
      setSelected([]);
      setInput("");
    } catch (e) {
      setError(String(e));
    }
  };
  const downloaded = new Set((specs ?? []).map((x) => x.number));

  return (
    <div className="home">
      <KeepUp specs={specs} downloading={downloading} onJob={(j) => setJobs((js) => [...js, j])} onOpen={onOpen} />
      <section>
        <h2>받은 스펙 <span className="muted small">(누르면 뷰어 탭에서 열림 · 새 뷰어 탭은 위의 +)</span></h2>
        {specs === null && <p className="muted">불러오는 중…</p>}
        {specs?.length === 0 && <p className="muted">아직 받은 스펙이 없습니다. 아래에서 다운로드하세요.</p>}
        <div className="spec-grid">
          {specs?.map((s) => (
            <button key={s.number} className="spec-card" onClick={() => onOpen(s.number)}>
              <b>TS {s.number}</b>
              <span className="muted small">{s.title ?? ""}</span>
              <span className="small">{s.versions}개 버전 · 최신 v{s.latest}</span>
            </button>
          ))}
        </div>
              {(specs?.length ?? 0) > 1 && <CitationMap onOpen={(spec, clause) => onOpen(spec, clause)} />}
      </section>

      <section>
        <h2>스펙 다운로드</h2>
        <p className="muted small">받을 스펙을 눌러 고른 뒤 다운로드를 누르세요. 모든 버전과 CR 표지를 받아 버전 이력을 계산합니다. ✓ = 이미 받음.</p>
        <SpecCatalog selected={selected} downloaded={downloaded} focus={prefill} onToggle={toggle} />
        <div className="dl-form">
          <div className="picked">
            <b className="small">선택 {selected.length}개</b>
            {selected.map((n) => <button key={n} className="chip on small" onClick={() => toggle(n)} title="빼기">{n} ✕</button>)}
            {selected.length > 0 && <button className="link small" onClick={() => setSelected([])}>모두 해제</button>}
          </div>
          <details>
            <summary className="small muted">직접 입력</summary>
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="38.211, 36.211, 38.101-1" />
          </details>
          <div className="rels">
            {RELEASES.map((r) => (
              <label key={r}>
                <input type="checkbox" checked={rels.includes(r)}
                       onChange={(e) => setRels(e.target.checked ? [...rels, r] : rels.filter((x) => x !== r))} />
                Rel-{r}
              </label>
            ))}
            <span className="muted small">(선택 없음 = 전체)</span>
          </div>
          <label><input type="checkbox" checked={latestOnly} onChange={(e) => setLatestOnly(e.target.checked)} /> 릴리즈별 최신 버전만</label>
          <button className="primary" onClick={start} disabled={downloading || (!selected.length && !input.trim())}>
            다운로드{selected.length ? ` (${selected.length}개)` : ""}
          </button>
        </div>
        {error && <p className="error small">{error}</p>}
        <JobList jobs={jobs.filter((j) => j.kind !== "intent")} />
        <h2>Annotation 서버 <span className="muted small">(선택)</span></h2>
        <p className="muted small">변경 의도 요약·Chairman’s notes 연결 등 서버에서 만든 결과를 받아 함께 보여줍니다. 없어도 뷰어는 그대로 동작합니다.</p>
        <div className="dl-form">
          <input value={annUrl} placeholder="http://annotation-server:8800" onChange={(e) => { setAnnUrl(e.target.value); setAnnSaved(false); }} />
          <button onClick={async () => { await api.saveSettings({ annotation_url: annUrl }); setAnnSaved(true); }}>저장</button>
          {annSaved && <span className="small muted">저장됨</span>}
        </div>
      </section>

      <LlmPanel specs={specs ?? []} onJob={(job) => setJobs((js) => [...js, job])} />
      <JobList jobs={jobs.filter((j) => j.kind === "intent")} />
    </div>
  );
}

function JobList({ jobs }: { jobs: Job[] }) {
  return (
    <>
      {jobs.map((j) => (
        <div key={j.id} className={`job job-${j.state}`}>
          <b>{j.specs.join(", ")}</b> <span className="small">{j.message}</span>
          {j.total > 0 && <progress value={j.done} max={j.total} />}
          <span className="small muted">{j.done}/{j.total || "?"}</span>
        </div>
      ))}
    </>
  );
}
