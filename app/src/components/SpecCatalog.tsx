// Download picker: every TS/TR of a series as a button, grouped by area, with the common ones up front.
import { useEffect, useMemo, useState } from "react";
import { api, type CatalogSpec } from "../api";

const SERIES = [
  { id: "38", label: "NR (38)" },
  { id: "36", label: "LTE (36)" },
  { id: "37", label: "공통 (37)" },
];

const COMMON: Record<string, string[]> = {
  "38": ["38.211", "38.212", "38.213", "38.214", "38.215", "38.300", "38.321", "38.331", "38.306", "38.133", "38.101-1"],
  "36": ["36.211", "36.212", "36.213", "36.214", "36.300", "36.321", "36.331", "36.306", "36.133", "36.101"],
  "37": ["37.213", "37.340", "37.355"],
};

// By the hundreds digit after the dot; TRs go to their own group whatever their number.
const GROUPS: Record<string, string> = {
  "0": "일반", "1": "RF · RRM 성능", "2": "물리계층 (L1)", "3": "L2 / L3 프로토콜", "4": "RAN 구조 · 인터페이스",
  "5": "UE 적합성 시험", "6": "기타", "7": "기타", "8": "기타", "9": "기타",
};

/** "NR; Physical channels and modulation" -> "Physical channels and modulation" */
function shortTitle(t: string): string {
  const parts = t.split(";").map((x) => x.trim()).filter(Boolean);
  return parts.length > 1 ? parts.slice(1).join("; ") : t;
}

export function SpecCatalog({ selected, downloaded, focus, onToggle }: {
  selected: string[]; downloaded: Set<string>; focus?: string; onToggle: (spec: string) => void;
}) {
  const [series, setSeries] = useState("38");
  const [cat, setCat] = useState<Record<string, CatalogSpec[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [showWithdrawn, setShowWithdrawn] = useState(false);

  useEffect(() => { if (focus) setSeries(focus.slice(0, 2)); }, [focus]);
  useEffect(() => {
    if (cat[series]) return;
    setError(null);
    api.catalog(series).then((rows) => setCat((c) => ({ ...c, [series]: rows })), (e) => setError(String(e).replace(/^Error: /, "")));
  }, [series, cat]);

  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {  // the list is kept locally; only this goes back to 3GPP
    setRefreshing(true);
    setError(null);
    try {
      const rows = await api.catalog(series, true);
      setCat((c) => ({ ...c, [series]: rows }));
    } catch (e) {
      setError(String(e).replace(/^Error: /, "") + " (저장된 목록을 그대로 씁니다)");
    } finally {
      setRefreshing(false);
    }
  };

  const rows = cat[series];
  const byNumber = useMemo(() => new Map((rows ?? []).map((r) => [r.number, r])), [rows]);
  const groups = useMemo(() => {
    const t = q.trim().toLowerCase();
    const out = new Map<string, CatalogSpec[]>();
    for (const r of rows ?? []) {
      if (r.withdrawn && !showWithdrawn) continue;
      if (t && !r.number.includes(t) && !r.title.toLowerCase().includes(t)) continue;
      const g = r.type === "TR" ? "TR · 연구 보고서" : GROUPS[r.number.split(".")[1]?.[0] ?? "9"] ?? "기타";
      out.set(g, [...(out.get(g) ?? []), r]);
    }
    const order = [...Object.values(GROUPS), "TR · 연구 보고서"];
    return [...out.entries()].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  }, [rows, q, showWithdrawn]);

  const chip = (r: CatalogSpec) => {
    const on = selected.includes(r.number), have = downloaded.has(r.number);
    return (
      <button key={r.number} className={`chip ${on ? "on" : ""} ${r.withdrawn ? "withdrawn" : ""}`}
              title={`${r.type} ${r.number}\n${r.title}${have ? "\n(받음 · 다시 받으면 새 버전만 추가)" : ""}${r.withdrawn ? "\n(폐기된 스펙)" : ""}`}
              onClick={() => onToggle(r.number)}>
        <b>{r.number}</b>{have && <span className="chip-have">✓</span>}
        <span className="chip-title">{shortTitle(r.title)}</span>
      </button>
    );
  };

  return (
    <div className="catalog">
      <div className="row">
        <div className="seg">
          {SERIES.map((s) => (
            <button key={s.id} className={series === s.id ? "on" : ""} onClick={() => setSeries(s.id)}>{s.label}</button>
          ))}
        </div>
        <input className="catalog-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="번호·제목으로 찾기 (예: 331, PDCP)" />
        <label className="small"><input type="checkbox" checked={showWithdrawn} onChange={(e) => setShowWithdrawn(e.target.checked)} /> 폐기된 스펙도</label>
        <button className="small" onClick={refresh} disabled={refreshing}
                title="3GPP 에서 스펙 목록을 다시 받습니다 (새 스펙·폐기 반영). 평소에는 저장된 목록을 씁니다.">
          {refreshing ? "받는 중…" : "목록 새로고침"}
        </button>
      </div>
      {error && <p className="small status-error">{error}{!rows && " — 아래 “직접 입력”으로 받을 수 있습니다."}</p>}
      {!rows && !error && <p className="muted small">3GPP 스펙 목록을 불러오는 중…</p>}
      {rows && !q && (
        <div className="catalog-group">
          <h4>자주 쓰는 스펙</h4>
          <div className="chips">{COMMON[series].map((n) => byNumber.get(n)).filter((r): r is CatalogSpec => !!r).map(chip)}</div>
        </div>
      )}
      {groups.map(([g, list]) => (
        <details key={g} className="catalog-group" open={!!q}>
          <summary><h4>{g} <span className="muted small">{list.length}</span></h4></summary>
          <div className="chips">{list.map(chip)}</div>
        </details>
      ))}
      {rows && q && !groups.length && <p className="muted small">“{q}”에 맞는 스펙이 없습니다.</p>}
    </div>
  );
}
