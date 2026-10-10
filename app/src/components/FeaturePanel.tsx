// TR 38.822 UE features related to the selected clause (via CR work items and parameter names).
import { useEffect, useState } from "react";
import { api, type ClauseFeatures, type FeatureSection, type UEFeature } from "../api";

export function FeaturePanel({ spec, version, clauseKey }: { spec: string; version: string; clauseKey: string }) {
  const [data, setData] = useState<ClauseFeatures | null>(null);
  const [job, setJob] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setData(null);
    api.clauseFeatures(spec, version, clauseKey).then(setData, () => setData(null));
  }, [spec, version, clauseKey, reload]);

  const fetchFeatures = async () => {
    const j = await api.fetchFeatures();
    setJob("38.822 받는 중…");
    const t = setInterval(async () => {
      const cur = (await api.jobs()).find((x) => x.id === j.id);
      if (!cur || cur.state === "running") return setJob(`38.822: ${cur?.message ?? ""}`);
      clearInterval(t);
      setJob(cur.state === "error" ? `실패: ${cur.message}` : null);
      setReload((n) => n + 1);
    }, 1000);
  };

  if (!data) return null;
  return (
    <section className="features">
      <h4>관련 UE feature <span className="muted small">TR 38.822</span></h4>
      {!data.available ? (
        <p className="small">
          38.822 feature list 가 없습니다. <button className="link" onClick={fetchFeatures} disabled={!!job}>받기</button>
          {job && <span className="muted"> {job}</span>}
        </p>
      ) : data.work_items.length === 0 && data.parameters.length === 0 ? (
        <p className="muted small">이 절을 수정한 CR의 work item 이 없거나 CR 표지를 아직 받지 않았습니다.</p>
      ) : (
        <>
          {data.work_items.map((w) => (
            <details key={w.wi_code} className="wi-group" open={data.work_items.length <= 3}>
              <summary>
                <span className="wi">{w.wi_code}</span>{" "}
                <span className="muted small">{w.crs.map((c) => `CR${c.cr}`).filter((x, i, a) => a.indexOf(x) === i).join(", ")}</span>
              </summary>
              {w.sections.map((s) => <Section key={s.section} s={s} />)}
            </details>
          ))}
          {data.parameters.length > 0 && (
            <details className="wi-group">
              <summary className="small">본문 파라미터와 field name 이 일치하는 feature ({data.parameters.length})</summary>
              <ul className="feat-list">{data.parameters.map((f, i) => <Feat key={i} f={f} />)}</ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}

function Section({ s }: { s: FeatureSection }) {
  const [all, setAll] = useState(false);
  const shown = all ? s.features : s.features.filter((f) => f.shared?.length).slice(0, 6);
  return (
    <div className="feat-sec">
      <div className="small"><b>{s.section}</b> {s.title} <span className="muted">· {s.layer} · {s.release}</span></div>
      <ul className="feat-list">{shown.map((f) => <Feat key={f.index} f={f} />)}</ul>
      {shown.length < s.features.length && (
        <button className="link small" onClick={() => setAll(true)}>
          {shown.length ? "" : "본문과 겹치는 용어가 있는 feature 없음 · "}전체 {s.features.length}개 보기
        </button>
      )}
    </div>
  );
}

function Feat({ f }: { f: UEFeature }) {
  const mo = /^mandatory/i.test(f.mandatory) ? "M" : /^optional/i.test(f.mandatory) ? "O" : "";
  return (
    <li title={`${f.components}${f.prerequisite ? `\nPrerequisite: ${f.prerequisite}` : ""}\n${f.mandatory}`}>
      <span className="fidx">{f.index}</span> {f.group}
      {mo && <span className={`mo mo-${mo}`}>{mo}</span>}
      {f.field_name && f.field_name !== "n/a" && <div className="fname">{f.field_name}</div>}
      {(f.shared?.length || f.matched?.length) ? <div className="muted tiny">↔ {(f.shared ?? f.matched)!.join(", ")}</div> : null}
    </li>
  );
}
