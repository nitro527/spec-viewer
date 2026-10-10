// Change intent and agreements from the annotation server (optional; the viewer works without it).
import { useEffect, useState } from "react";
import { api, type Annotation } from "../api";

export function AnnotationPanel({ spec, version, clauseKey, rawHash }: {
  spec: string; version: string; clauseKey: string; rawHash?: string;
}) {
  const [items, setItems] = useState<Annotation[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    api.annotations(spec, version, clauseKey).then(setItems, () => setItems([]));
  }, [spec, version, clauseKey, reload]);

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

  if (items === null) return null;
  const shown = rawHash ? items.filter((a) => a.raw_hash === rawHash) : items;
  return (
    <section className="annotations">
      <h4>변경 의도 <span className="muted small">annotation 서버</span></h4>
      {shown.length === 0 ? (
        <p className="muted small">
          {items.length ? "선택한 문단에 대한 annotation 이 없습니다." : "이 절에 대한 annotation 이 없습니다."}{" "}
          <button className="link" onClick={sync}>서버에서 동기화</button>
          {msg && <span> · {msg}</span>}
        </p>
      ) : (
        shown.map((a, i) => (
          <div key={i} className="intent">
            <div className="small muted">v{a.version} · {a.change}{a.crs.length ? ` · ${a.crs.map((c) => `CR${c.cr}`).join(", ")}` : ""}</div>
            {a.intent ? (
              <>
                <p><b>무엇이</b> {a.intent.summary}</p>
                <p><b>왜</b> {a.intent.motivation}</p>
                <p><b>구현 영향</b> {a.intent.impact}</p>
                <p className="muted tiny">신뢰도 {a.intent.confidence}{a.intent.model ? ` · ${a.intent.model}` : ""} · AI 요약은 CR 원문으로 확인하세요</p>
              </>
            ) : <p className="muted small">의도 요약 없음 (CR 매핑만 포함된 pack)</p>}
            {a.agreements.map((g, j) => <blockquote key={j} className="small">{g.meeting && <b>{g.meeting} </b>}{g.text}</blockquote>)}
          </div>
        ))
      )}
    </section>
  );
}
