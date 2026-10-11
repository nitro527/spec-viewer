// Ctrl+F search backed by the library's FTS5 index (works on unloaded clauses too).
import { useEffect, useRef, useState } from "react";
import { api, type PhraseHistory, type SearchHit } from "../api";
import type { VersionColor } from "../colors";

export function FindBar({ spec, version, colors, onGo, onOpenAt, onClose }: {
  spec: string; version: string; colors: Map<string, VersionColor>; onGo: (hit: SearchHit) => void;
  onOpenAt: (version: string, key: string) => void; onClose: () => void;
}) {
  const [byVersion, setByVersion] = useState(false);
  const [phrase, setPhrase] = useState<PhraseHistory | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [idx, setIdx] = useState(0);
  const jumped = useRef(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => input.current?.focus(), []);
  useEffect(() => {
    if (q.trim().length < 2) return setHits(null);
    let live = true;  // a slow reply for an earlier query must not replace the hits of this one
    const t = setTimeout(() => api.search(spec, version, q).then((h) => {
      if (!live) return;
      setHits(h); setIdx(0); jumped.current = false;
    }), 200);
    return () => { live = false; clearTimeout(t); };
  }, [q, spec, version]);
  useEffect(() => {
    setPhrase(null);
    if (!byVersion || q.trim().length < 2) return;
    let live = true;
    const t = setTimeout(() => api.phraseHistory(spec, q).then((h) => live && setPhrase(h), () => {}), 250);
    return () => { live = false; clearTimeout(t); };
  }, [q, spec, byVersion]);

  const go = (i: number) => {
    if (!hits?.length) return;
    jumped.current = true;
    const n = (i + hits.length) % hits.length;
    setIdx(n);
    onGo(hits[n]);
  };

  return (
    <div className="findbar">
      <div className="find-row">
        <input ref={input} value={q} placeholder="스펙 전체 검색 (Enter: 다음, Shift+Enter: 이전)"
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key === "Enter") go(!jumped.current ? idx : e.shiftKey ? idx - 1 : idx + 1);
                 if (e.key === "Escape") onClose();
               }} />
        <span className="muted small">{hits ? `${hits.length ? idx + 1 : 0}/${hits.length}` : ""}</span>
        <button onClick={() => go(idx - 1)} title="이전">↑</button>
        <button onClick={() => go(idx + 1)} title="다음">↓</button>
        <button className={byVersion ? "on" : ""} onClick={() => setByVersion(!byVersion)}
                title="이 문구(정확히 일치)가 버전마다 어느 절에 있었는지: 처음 나온 버전, 생기고 사라진 절">버전별</button>
        <button onClick={onClose} title="닫기">✕</button>
      </div>
      {byVersion && q.trim().length >= 2 && (
        phrase ? <PhraseTimeline h={phrase} colors={colors} onOpenAt={onOpenAt} />
          : <p className="muted small phrase-loading">버전별로 찾는 중…</p>
      )}
      {!byVersion && hits && hits.length > 0 && (
        <ul className="find-results">
          {hits.slice(0, 50).map((h, i) => (
            <li key={i} className={i === idx ? "on" : ""} onClick={() => { setIdx(i); onGo(h); }}>
              <span className="num">{h.number}</span>{" "}
              <span dangerouslySetInnerHTML={{ __html: escape(h.snippet).replace(/\[\[/g, "<mark>").replace(/\]\]/g, "</mark>") }} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );

}

function PhraseTimeline({ h, colors, onOpenAt }: {
  h: PhraseHistory; colors: Map<string, VersionColor>; onOpenAt: (version: string, key: string) => void;
}) {
  if (!h.first) return <p className="muted small phrase-loading">어느 버전에도 이 문구가 없습니다 (단어 순서 그대로 찾습니다).</p>;
  const max = Math.max(1, ...h.versions.map((v) => v.clauses));
  const latest = h.versions[h.versions.length - 1];
  return (
    <div className="phrase">
      <p className="small">
        처음 나온 버전 <b>v{h.first}</b> · 최신 v{latest.version} 에는 {h.latest_clauses
          ? `${latest.clauses}개 절, ${latest.paragraphs}개 문단` : "없음 (삭제됨)"}
      </p>
      <div className="phrase-bars" title="버전별로 이 문구가 있는 절 수">
        {h.versions.map((v) => (
          <span key={v.version} title={`v${v.version}: ${v.clauses}개 절, ${v.paragraphs}개 문단`}
                style={{ height: `${Math.max(v.clauses ? 8 : 0, (v.clauses / max) * 100)}%`, background: colors.get(v.version)?.solid }} />
        ))}
      </div>
      <ul className="phrase-events">
        {h.events.map((e) => (
          <li key={e.version}>
            <span className="vchip" style={{ background: colors.get(e.version)?.solid }} /><b>v{e.version}</b>
            {e.added.map((c) => (
              <div key={`+${c.key}`} className="phrase-item add" onClick={() => onOpenAt(e.version, c.key)}
                   title="이 버전으로 열기">
                + <span className="num">{c.number || c.title}</span>{" "}
                <span dangerouslySetInnerHTML={{ __html: escape(c.snippet ?? "").replace(/\[\[/g, "<mark>").replace(/\]\]/g, "</mark>") }} />
              </div>
            ))}
            {e.removed.map((c) => (
              <div key={`-${c.key}`} className="phrase-item del" title="이 버전에서 이 절에서 사라짐 (이전 버전으로 열기)"
                   onClick={() => {
                     const i = h.versions.findIndex((v) => v.version === e.version);
                     onOpenAt(h.versions[Math.max(0, i - 1)].version, c.key);
                   }}>
                − <span className="num">{c.number || c.title}</span> <span className="muted">{c.title}</span>
              </div>
            ))}
            {e.more > 0 && <div className="muted tiny">… 외 {e.more}개</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function escape(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
