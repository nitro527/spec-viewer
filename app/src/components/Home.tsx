// Library home: specs already converted, and the 3GPP downloader.
import { useEffect, useState } from "react";
import { api, type Job, type SpecInfo } from "../api";

const RELEASES = [15, 16, 17, 18, 19, 20];

export function Home({ prefill, onOpen }: { prefill?: string; onOpen: (spec: string) => void }) {
  const [specs, setSpecs] = useState<SpecInfo[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [input, setInput] = useState(prefill ?? "");
  const [rels, setRels] = useState<number[]>([]);
  const [latestOnly, setLatestOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => api.specs().then(setSpecs, (e) => setError(String(e)));
  useEffect(() => { refresh(); }, []);

  const running = jobs.some((j) => j.state === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => api.jobs().then((js) => {
      setJobs(js);
      if (!js.some((j) => j.state === "running")) refresh();
    }), 1000);
    return () => clearInterval(t);
  }, [running]);

  const start = async () => {
    const list = input.split(/[\s,;]+/).map((s) => s.trim()).filter((s) => /^\d{2}\.\d{3}$/.test(s));
    if (!list.length) return setError("스펙 번호를 38.211 형식으로 입력하세요");
    setError(null);
    try {
      const job = await api.fetchSpecs(list, rels.length ? rels : null, latestOnly);
      setJobs((js) => [...js, job]);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="home">
      <section>
        <h2>라이브러리</h2>
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
      </section>

      <section>
        <h2>스펙 다운로드</h2>
        <p className="muted small">3GPP archive 에서 선택한 스펙의 모든 버전을 받아 변환하고 버전 이력을 계산합니다.</p>
        <div className="dl-form">
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="38.211, 38.212, 38.213, 38.214" />
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
          <button className="primary" onClick={start} disabled={running}>다운로드</button>
        </div>
        {error && <p className="error small">{error}</p>}
        {jobs.map((j) => (
          <div key={j.id} className={`job job-${j.state}`}>
            <b>{j.specs.join(", ")}</b> <span className="small">{j.message}</span>
            {j.total > 0 && <progress value={j.done} max={j.total} />}
            <span className="small muted">{j.done}/{j.total || "?"}</span>
          </div>
        ))}
      </section>
    </div>
  );
}
