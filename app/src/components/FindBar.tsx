// Ctrl+F search backed by the library's FTS5 index (works on unloaded clauses too).
import { useEffect, useRef, useState } from "react";
import { api, type SearchHit } from "../api";

export function FindBar({ spec, version, onGo, onClose }: {
  spec: string; version: string; onGo: (hit: SearchHit) => void; onClose: () => void;
}) {
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
        <button onClick={onClose} title="닫기">✕</button>
      </div>
      {hits && hits.length > 0 && (
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

function escape(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
