// New viewer tab: every downloaded spec at a glance; pick one to open it in this tab.
import { useEffect, useMemo, useState } from "react";
import { api, type SpecInfo } from "../api";
import { releaseColor } from "../colors";

export function SpecPicker({ active, onOpen, onGoHome }: {
  active: boolean; onOpen: (spec: string) => void; onGoHome: () => void;
}) {
  const [specs, setSpecs] = useState<SpecInfo[] | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => { if (active) api.specs().then(setSpecs, () => setSpecs([])); }, [active]);

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (specs ?? []).filter((s) => !t || s.number.includes(t) || (s.title ?? "").toLowerCase().includes(t));
  }, [specs, q]);

  if (specs === null) return <div className="picker"><p className="muted">불러오는 중…</p></div>;
  if (!specs.length) return (
    <div className="picker empty">
      <h2>볼 수 있는 스펙이 없습니다</h2>
      <p className="muted">메인 화면에서 스펙을 먼저 다운로드하세요.</p>
      <button className="primary" onClick={onGoHome}>메인 화면으로</button>
    </div>
  );
  return (
    <div className="picker">
      <div className="picker-head">
        <h2>스펙 열기 <span className="muted small">{specs.length}개</span></h2>
        {specs.length > 6 && (
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="번호나 제목으로 찾기 (예: 38.21, PDSCH)"
                 onKeyDown={(e) => e.key === "Enter" && shown.length === 1 && onOpen(shown[0].number)} />
        )}
      </div>
      <div className="picker-grid">
        {shown.map((s) => (
          <button key={s.number} className="picker-card" onClick={() => onOpen(s.number)}>
            <span className="picker-num">TS {s.number}</span>
            <span className="picker-title">{s.title ?? "(제목 없음)"}</span>
            <span className="picker-rels">
              {s.releases.map((r) => <span key={r} className="rel-badge" style={{ background: releaseColor(r) }}>Rel-{r}</span>)}
            </span>
            <span className="small muted">
              {s.first !== s.latest && <>v{s.first} → </>}<b>v{s.latest}</b> · {s.versions}개 버전{s.crs ? ` · CR ${s.crs}건` : ""}
            </span>
          </button>
        ))}
        {!shown.length && <p className="muted small">“{q}”에 맞는 스펙이 없습니다.</p>}
      </div>
      <p className="muted tiny">다운로드·설정은 왼쪽 위 <b>메인</b> 탭에서 합니다.</p>
    </div>
  );
}
