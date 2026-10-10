// One open specification: toolbar, TOC, reading/compare view, info panel and timeline.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CR, type SearchHit, type TocEntry, type VersionInfo } from "../api";
import { highlightCss, highlighted, versionColors } from "../colors";
import { CompareView, VersionSelect } from "./CompareView";
import { FindBar } from "./FindBar";
import { HistoryView, type HistoryViewHandle, type Selection } from "./HistoryView";
import { ChangeRuler, InfoPanel, Toc } from "./Panels";
import { StatsView } from "./StatsView";
import { Timeline } from "./Timeline";

export type Mode = "history" | "compare" | "clean" | "stats";

export interface TabState {
  id: string;
  spec: string | null;
  version?: string;
  label?: string;
  jumpTo?: string; // clause number to open at
  prefill?: string; // Home tab: spec to suggest for download
}

interface Props {
  tab: TabState;
  active: boolean;
  findSignal: number;
  onUpdate: (patch: Partial<TabState>) => void;
  onOpenSpec: (spec: string, clause?: string) => void;
}

export function SpecTab({ tab, active, findSignal, onUpdate, onOpenSpec }: Props) {
  const spec = tab.spec!;
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [version, setVersion] = useState<string | undefined>(tab.version);
  const [baseline, setBaseline] = useState<string>("");
  const [mode, setMode] = useState<Mode>("history");
  const [toc, setToc] = useState<TocEntry[]>([]);
  const [crs, setCrs] = useState<CR[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [visible, setVisible] = useState(0);
  const [findOpen, setFindOpen] = useState(false);
  const [tocChangedOnly, setTocChangedOnly] = useState(false);
  const [infoOpen, setInfoOpen] = useState(true);
  const [cmp, setCmp] = useState<[string, string] | null>(null);
  const view = useRef<HistoryViewHandle>(null);
  const pendingJump = useRef<string | undefined>(tab.jumpTo);

  useEffect(() => {
    api.versions(spec).then((vs) => {
      setVersions(vs);
      if (!vs.length) return;
      setVersion((v) => v ?? vs[vs.length - 1].version);
      setBaseline(vs[0].version);
    });
    api.crs(spec).then(setCrs);
  }, [spec]);

  useEffect(() => {
    if (!version) return;
    onUpdate({ version, label: `${spec} v${version}` });
    setSelection(null);
    api.toc(spec, version).then(setToc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec, version]);

  // Jump requested by a cross-reference from another tab.
  useEffect(() => {
    if (tab.jumpTo) pendingJump.current = tab.jumpTo;
  }, [tab.jumpTo]);
  useEffect(() => {
    const target = pendingJump.current;
    if (!target || !toc.length) return;
    const e = toc.find((t) => t.number === target || t.key === target);
    if (e) setTimeout(() => view.current?.scrollToOrd(e.ord), 50);
    pendingJump.current = undefined;
  }, [toc]);

  useEffect(() => {
    if (active && findSignal) setFindOpen(true);
  }, [findSignal, active]);

  // Alt+←/→ steps through versions.
  useEffect(() => {
    if (!active) return;
    const h = (e: KeyboardEvent) => {
      if (!e.altKey || !version || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      const i = versions.findIndex((v) => v.version === version);
      const n = versions[i + (e.key === "ArrowLeft" ? -1 : 1)];
      if (n) { e.preventDefault(); setVersion(n.version); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [active, version, versions]);

  const colors = useMemo(() => versionColors(versions), [versions]);
  const shown = useMemo(() => (version && baseline ? highlighted(versions, baseline, version) : []),
    [versions, baseline, version]);
  const css = useMemo(() => highlightCss(colors, shown, `#tab-${tab.id}`), [colors, shown, tab.id]);
  const crsByVersion = useMemo(() => {
    const m = new Map<string, CR[]>();
    for (const c of crs) m.set(c.new_version, [...(m.get(c.new_version) ?? []), c]);
    return m;
  }, [crs]);

  const jump = useCallback((ord: number, block: number | null = null) => {
    const away = mode === "compare" || mode === "stats";
    if (away) setMode("history");
    setTimeout(() => view.current?.scrollToOrd(ord, block), away ? 80 : 0);
  }, [mode]);

  const onXref = useCallback((clause: string | undefined, ref: string | undefined) => {
    if (ref && ref !== spec) return onOpenSpec(ref, clause);
    if (!clause) return;
    const e = toc.find((t) => t.number === clause || t.key === clause);
    if (e) jump(e.ord);
  }, [spec, toc, jump, onOpenSpec]);

  const onFind = useCallback((h: SearchHit) => jump(h.ord, h.block), [jump]);

  if (!versions.length || !version) {
    return <div className="empty">{versions.length === 0 ? `${spec} 를 불러오는 중…` : ""}</div>;
  }
  const [cmpOld, cmpNew] = cmp ?? [baseline === version ? versions[Math.max(0, versions.length - 2)].version : baseline, version];

  return (
    <div id={`tab-${tab.id}`} className="spec-tab">
      <style>{css}</style>
      <header className="toolbar">
        <b className="spec-name">TS {spec}</b>
        {(mode === "history" || mode === "clean") && <VersionSelect versions={versions} value={version} onChange={setVersion} label="버전" />}
        {mode === "history" && (
          <VersionSelect versions={versions} value={baseline}
                         onChange={setBaseline} label="이후 변경 표시" />
        )}
        <div className="seg">
          {(["history", "compare", "clean", "stats"] as Mode[]).map((m) => (
            <button key={m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
              {{ history: "History", compare: "Compare", clean: "Clean", stats: "Statistics" }[m]}
            </button>
          ))}
        </div>
        {mode === "history" && (
          <div className="legend">
            {shown.map((v) => (
              <span key={v.version} className="legend-item" data-v={v.version}
                    title={(crsByVersion.get(v.version) ?? []).map((c) => c.subject).join("\n")}>
                {v.version}
              </span>
            ))}
          </div>
        )}
        <div className="spacer" />
        <button onClick={() => setFindOpen(true)} title="검색 (Ctrl+F)">🔍</button>
        <button onClick={() => setInfoOpen(!infoOpen)} title="변경 정보 패널">{infoOpen ? "⇥" : "⇤"}</button>
      </header>

      <div className="body">
        <aside className="left">
          <label className="small toc-filter">
            <input type="checkbox" checked={tocChangedOnly} onChange={(e) => setTocChangedOnly(e.target.checked)} />
            변경된 절만
          </label>
          <Toc toc={toc} active={visible} baseline={baseline} highlight={mode === "history"}
               changedOnly={tocChangedOnly && mode === "history"} onJump={(o) => jump(o)} />
        </aside>

        <main className="center">
          {findOpen && <FindBar spec={spec} version={version} onGo={onFind} onClose={() => setFindOpen(false)} />}
          {mode === "stats" ? (
            <StatsView spec={spec} colors={colors}
                       onJump={(key) => { const e = toc.find((t) => t.key === key); if (e) jump(e.ord); }}
                       onCompare={(v) => {
                         const i = versions.findIndex((x) => x.version === v);
                         setCmp([versions[Math.max(0, i - 1)].version, v]);
                         setMode("compare");
                       }} />
          ) : mode === "compare" ? (
            <CompareView spec={spec} versions={versions} oldV={cmpOld} newV={cmpNew}
                         onChange={(o, n) => setCmp([o, n])} onXref={onXref} />
          ) : (
            <div className="doc-wrap">
              <HistoryView ref={view} spec={spec} version={version} citeLabel={`TS ${spec}`} baseline={baseline} toc={toc}
                           highlight={mode === "history"} selected={selection} onSelect={setSelection}
                           onVisible={setVisible} onXref={onXref} />
              {mode === "history" && <ChangeRuler toc={toc} baseline={baseline} onJump={(o) => jump(o)} />}
            </div>
          )}
        </main>

        {infoOpen && (mode === "history" || mode === "clean") && (
          <InfoPanel selection={selection} version={version} baseline={baseline} crsByVersion={crsByVersion}
                     colors={colors} onClose={() => setSelection(null)} />
        )}
      </div>

      <Timeline versions={versions} version={version} baseline={baseline} colors={colors}
                onVersion={setVersion} onBaseline={setBaseline} />
    </div>
  );
}
