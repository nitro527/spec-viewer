// Feature-first view: pick a TR 38.822 UE feature, see the clauses of every downloaded spec tied to it.
import { useEffect, useRef, useState } from "react";
import { api, followJob, type FeatureClause, type FeatureLinks, type Job, type UEFeature } from "../api";

export function FeatureFinder({ onOpen }: { onOpen: (spec: string, clause?: string) => void }) {
  const [status, setStatus] = useState<{ count: number; source: string | null } | null>(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<UEFeature[]>([]);
  const [picked, setPicked] = useState<UEFeature | null>(null);
  const [job, setJob] = useState<string | null>(null);
  const seq = useRef(0);

  const loadStatus = () => api.featuresStatus().then(setStatus, () => setStatus({ count: 0, source: null }));
  useEffect(() => { loadStatus(); }, []);

  useEffect(() => {
    const n = ++seq.current;
    if (q.trim().length < 2) return setResults([]);
    const t = setTimeout(() => api.searchFeatures(q).then((r) => n === seq.current && setResults(r), () => {}), 200);
    return () => clearTimeout(t);
  }, [q]);

  const stopJob = useRef<() => void>();
  useEffect(() => () => stopJob.current?.(), []);
  const fetchFeatures = async () => {
    let j: Job;
    try {
      j = await api.fetchFeatures();
    } catch (e) {
      return setJob(`실패: ${String(e).replace(/^Error: /, "")}`);
    }
    setJob("38.822 받는 중…");
    stopJob.current?.();
    stopJob.current = followJob(j.id, (cur) => {
      if (cur.state === "running") return setJob(`38.822: ${cur.message ?? ""}`);
      setJob(cur.state === "error" ? `실패: ${cur.message}` : null);
      loadStatus();
    });
  };

  if (!status) return <p className="muted">불러오는 중…</p>;
  if (!status.count) return (
    <div className="ff-empty">
      <p>TR 38.822 UE feature 목록이 아직 없습니다.</p>
      <button className="primary" onClick={fetchFeatures} disabled={!!job}>38.822 받기</button>
      {job && <p className="muted small">{job}</p>}
    </div>
  );
  if (picked) return <FeatureDetail feature={picked} onBack={() => setPicked(null)} onOpen={onOpen} />;
  return (
    <div className="ff">
      <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} className="ff-search"
             placeholder="feature 번호·이름·work item 으로 찾기 (예: 2-1, PDSCH, NR_IAB)" />
      <p className="muted tiny">TR 38.822 v{status.source} · feature {status.count}개</p>
      <div className="ff-results">
        {results.map((f) => (
          <button key={`${f.section}:${f.index}`} className="ff-row" onClick={() => setPicked(f)}>
            <span className="ff-idx">{f.index}</span>
            <span className="ff-name">{f.group}</span>
            <span className="small muted">{f.release} · {f.layer} · {f.section_title}</span>
          </button>
        ))}
        {q.trim().length >= 2 && !results.length && <p className="muted small">“{q}”에 맞는 feature 가 없습니다.</p>}
      </div>
    </div>
  );
}

function FeatureDetail({ feature, onBack, onOpen }: {
  feature: UEFeature; onBack: () => void; onOpen: (spec: string, clause?: string) => void;
}) {
  const [data, setData] = useState<FeatureLinks | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.featureLinks(feature.section, feature.index).then(setData, (e) => setError(String(e)));
  }, [feature]);
  const f = data?.feature ?? feature;

  return (
    <div className="ff">
      <button className="link" onClick={onBack}>← feature 목록</button>
      <div className="ff-head">
        <h3><span className="ff-idx">{f.index}</span> {f.group}</h3>
        <div className="small muted">TR 38.822 §{f.section} {f.section_title} · {f.release} · {f.layer}
          {f.mandatory && ` · ${f.mandatory}`}</div>
        {f.components && <p className="small ff-comp">{f.components}</p>}
        {f.field_name && f.field_name.toLowerCase() !== "n/a" && <p className="small">RRC 필드: <code>{f.field_name}</code></p>}
        {f.prerequisite && f.prerequisite.toLowerCase() !== "n/a" && <p className="small muted">선행 feature: {f.prerequisite}</p>}
      </div>
      {error && <p className="error small">{error}</p>}
      {!data && !error && <p className="muted">관련 절을 찾는 중…</p>}
      {data && !data.specs.length && (
        <p className="muted small">
          받은 스펙에서 연결된 절이 없습니다. 이 feature 의 work item CR 이 들어 있는 스펙과 그 CR 표지를 받았는지 확인하세요
          (메인 탭 다운로드는 CR 표지를 함께 받습니다).
        </p>
      )}
      {data?.specs.map((sp) => <SpecClauses key={sp.spec} {...sp} onOpen={onOpen} />)}
      <p className="muted tiny">
        연결 근거: <b>CR</b> = 이 feature 의 work item CR 이 “Clauses affected”에 적은 절, <b>파라미터</b> = 본문에 RRC 필드 이름이 나오는 절.
        feature 와 겹치는 용어가 있는 절을 먼저 보여 줍니다.
      </p>
    </div>
  );
}

function SpecClauses({ spec, title, version, clauses, onOpen }: {
  spec: string; title: string | null; version: string; clauses: FeatureClause[];
  onOpen: (spec: string, clause?: string) => void;
}) {
  const strong = clauses.filter((c) => c.shared.length || c.via.some((v) => v.kind === "param"));
  const rest = clauses.filter((c) => !strong.includes(c));
  const [showRest, setShowRest] = useState(!strong.length);
  return (
    <section className="ff-spec">
      <h4>TS {spec} <span className="muted small">{title} · v{version} 기준 · {clauses.length}개 절</span></h4>
      {strong.map((c) => <ClauseRow key={c.number} c={c} onOpen={() => onOpen(spec, c.key ? c.number : undefined)} />)}
      {rest.length > 0 && (
        <>
          <button className="link small" onClick={() => setShowRest(!showRest)}>
            {showRest ? "▾" : "▸"} 같은 work item CR 이 고친 다른 절 {rest.length}개 (feature 와 겹치는 용어 없음)
          </button>
          {showRest && rest.map((c) => <ClauseRow key={c.number} c={c} onOpen={() => onOpen(spec, c.key ? c.number : undefined)} />)}
        </>
      )}
    </section>
  );
}

function ClauseRow({ c, onOpen }: { c: FeatureClause; onOpen: () => void }) {
  const crs = c.via.filter((v) => v.kind === "cr") as Extract<FeatureClause["via"][number], { kind: "cr" }>[];
  const params = c.via.filter((v) => v.kind === "param") as Extract<FeatureClause["via"][number], { kind: "param" }>[];
  return (
    <button className={`ff-clause ${c.key ? "" : "gone"}`} onClick={onOpen} disabled={!c.key}>
      <span className="ff-num">§{c.number}</span>
      <span className="ff-ctitle">{c.title}</span>
      <span className="ff-via small">
        {crs.length > 0 && <span title={crs.map((v) => `CR${v.cr} (v${v.version}) ${v.subject}`).join("\n")}>
          CR {crs.map((v) => v.cr).filter((x, i, a) => a.indexOf(x) === i).slice(0, 4).join(", ")}{crs.length > 4 ? " …" : ""}
        </span>}
        {params.map((v) => <code key={v.name}>{v.name}</code>)}
        {c.shared.map((t) => <span key={t} className="ff-term">{t}</span>)}
      </span>
    </button>
  );
}
