// Ctrl+P: jump to a clause ("6.4.1.2"), another spec's clause ("38.213 10.1"), a version ("v16.2") or a mode.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type SpecInfo, type TocEntry, type VersionInfo } from "../api";
import type { Mode } from "./SpecTab";

export interface PaletteItem { label: string; hint: string; run: () => void }

export function CommandPalette({ spec, toc, versions, onJump, onVersion, onMode, onOpenSpec, onClose }: {
  spec: string; toc: TocEntry[]; versions: VersionInfo[];
  onJump: (ord: number) => void; onVersion: (v: string) => void; onMode: (m: Mode) => void;
  onOpenSpec: (spec: string, clause?: string) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [specs, setSpecs] = useState<SpecInfo[]>([]);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); api.specs().then(setSpecs); }, []);

  const items = useMemo<PaletteItem[]>(() => {
    const query = q.trim();
    const low = query.toLowerCase();
    const out: PaletteItem[] = [];
    const other = query.match(/^(?:ts\s*)?(\d{2}\.\d{3}(?:-\d{1,2}){0,2})\s*(.*)$/i);
    if (other && other[1] !== spec) {
      const known = specs.some((s) => s.number === other[1]);
      out.push({ label: `TS ${other[1]}${other[2] ? ` §${other[2]}` : ""}`, hint: known ? "새 탭에서 열기" : "라이브러리에 없음 · 다운로드 화면",
                 run: () => onOpenSpec(other[1], other[2] || undefined) });
    }
    const vm = low.match(/^v?(\d+(?:\.\d+){0,2})$/);
    if (vm) {
      versions.filter((v) => v.version.startsWith(vm[1])).slice(0, 6)
        .forEach((v) => out.push({ label: `v${v.version}`, hint: `Rel-${v.release} 버전 보기`, run: () => onVersion(v.version) }));
    }
    const modes: [Mode, string][] = [["history", "History"], ["compare", "Compare"], ["clean", "Clean"], ["stats", "Statistics"], ["crs", "CR 목록"]];
    modes.filter(([, n]) => low && n.toLowerCase().startsWith(low)).forEach(([m, n]) => out.push({ label: n, hint: "모드 전환", run: () => onMode(m) }));
    const clauseQ = other ? "" : low;
    const byNum = toc.filter((t) => clauseQ && t.number.toLowerCase().startsWith(clauseQ));
    const byTitle = toc.filter((t) => clauseQ.length >= 2 && !byNum.includes(t) && t.title.toLowerCase().includes(clauseQ));
    [...byNum, ...byTitle].slice(0, 40).forEach((t) => out.push({ label: `${t.number} ${t.title}`, hint: "절로 이동", run: () => onJump(t.ord) }));
    if (!query) specs.filter((s) => s.number !== spec).forEach((s) => out.push({ label: `TS ${s.number}`, hint: s.title ?? "", run: () => onOpenSpec(s.number) }));
    return out;
  }, [q, toc, versions, specs, spec, onJump, onVersion, onMode, onOpenSpec]);

  useEffect(() => setSel(0), [q]);
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => { list.current?.children[sel]?.scrollIntoView({ block: "nearest" }); }, [sel]);
  const pick = (i: number) => { const it = items[i]; if (it) { it.run(); onClose(); } };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}
           onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="절 번호·제목, 38.213 10.1, v16.2, compare…"
               onKeyDown={(e) => {
                 if (e.nativeEvent.isComposing) return;  // Enter that ends a Hangul syllable is not a pick
                 if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)); }
                 if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
                 if (e.key === "Enter") pick(sel);
               }} />
        <ul ref={list}>
          {items.map((it, i) => (
            <li key={i} className={i === sel ? "on" : ""} onMouseEnter={() => setSel(i)} onClick={() => pick(i)}>
              <span>{it.label}</span><span className="muted small">{it.hint}</span>
            </li>
          ))}
          {!items.length && <li className="muted small">결과 없음</li>}
        </ul>
      </div>
    </div>
  );
}
