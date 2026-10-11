// One open specification: toolbar, TOC, reading/compare view, info panel and timeline.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CR, type Note, type SearchHit, type TocEntry, type VersionInfo } from "../api";
import { highlightCss, highlighted, versionColors } from "../colors";
import { CommandPalette } from "./CommandPalette";
import { CompareView, VersionSelect } from "./CompareView";
import { CRBrowser } from "./CRBrowser";
import { FindBar } from "./FindBar";
import { HistoryView, type HistoryViewHandle, type Selection } from "./HistoryView";
import { ChangeRuler, InfoPanel, Toc } from "./Panels";
import { StatsView } from "./StatsView";
import { Timeline } from "./Timeline";

export type Mode = "history" | "compare" | "clean" | "stats" | "crs";

export interface TabState {
  id: string;
  spec: string | null;
  version?: string;
  label?: string;
  jumpTo?: string; // clause number to open at
}

interface Props {
  tab: TabState;
  active: boolean;
  findSignal: number;
  paletteSignal: number;
  onUpdate: (patch: Partial<TabState>) => void;
  onOpenSpec: (spec: string, clause?: string) => void;
}

export function SpecTab({ tab, active, findSignal, paletteSignal, onUpdate, onOpenSpec }: Props) {
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
  const [crJob, setCrJob] = useState<string | null>(null);
  const [notes, setNotes] = useState<Map<string, Note>>(new Map());
  const [bookmarkedOnly, setBookmarkedOnly] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
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
    api.notes(spec).then((ns) => setNotes(new Map(ns.map((n) => [n.clause_key, n]))));
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
    if (tab.jumpTo) onUpdate({ jumpTo: undefined });  // consumed; the same clause can be requested again
  }, [tab.jumpTo]);
  useEffect(() => {  // runs once the TOC is there, or right away when the tab is already open
    const target = pendingJump.current;
    if (!target || !toc.length) return;
    const e = toc.find((t) => t.number === target || t.key === target);
    if (e) setTimeout(() => view.current?.scrollToOrd(e.ord), 50);
    pendingJump.current = undefined;
  }, [toc, tab.jumpTo]);

  useEffect(() => {
    if (active && findSignal) setFindOpen(true);
  }, [findSignal, active]);
  useEffect(() => {
    if (active && paletteSignal) setPaletteOpen(true);
  }, [paletteSignal, active]);

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
    // Group by the first downloaded version containing the CR, so skipped versions are not lost.
    for (const c of crs) {
      const v = c.lib_version ?? c.new_version;
      m.set(v, [...(m.get(v) ?? []), c]);
    }
    return m;
  }, [crs]);

  const jump = useCallback((ord: number, block: number | null = null) => {
    const away = mode === "compare" || mode === "stats" || mode === "crs";
    if (away) setMode("history");
    setTimeout(() => view.current?.scrollToOrd(ord, block), away ? 80 : 0);
  }, [mode]);

  const onXref = useCallback((clause: string | undefined, ref: string | undefined) => {
    if (ref && ref !== spec) return onOpenSpec(ref, clause);
    if (!clause) return;
    const e = toc.find((t) => t.number === clause || t.key === clause);
    if (e) jump(e.ord);
  }, [spec, toc, jump, onOpenSpec]);

  const fetchCrs = useCallback(async () => {
    const job = await api.fetchCrs(spec);
    setCrJob("CR 표지 받는 중…");
    const t = setInterval(async () => {
      const j = (await api.jobs()).find((x) => x.id === job.id);
      if (!j) return;
      setCrJob(j.state === "running" ? `CR 표지 받는 중 ${j.done}/${j.total || "?"}` : j.state === "error" ? `실패: ${j.message}` : null);
      if (j.state !== "running") { clearInterval(t); api.crs(spec).then(setCrs); }
    }, 1000);
  }, [spec]);

  const bookmarks = useMemo(() => new Set([...notes.values()].filter((n) => n.bookmark).map((n) => n.clause_key)), [notes]);
  const updateNote = useCallback(async (key: string, body: { bookmark?: boolean; text?: string }) => {
    const saved = await api.saveNote(spec, key, { ...body, version });
    setNotes((m) => {
      const next = new Map(m);
      if (saved) next.set(key, saved); else next.delete(key);
      return next;
    });
  }, [spec, version]);
  const toggleBookmark = useCallback((key: string) => updateNote(key, { bookmark: !bookmarks.has(key) }),
    [updateNote, bookmarks]);

  const onFind = useCallback((h: SearchHit) => jump(h.ord, h.block), [jump]);
  const openAt = useCallback((v: string, key: string) => {  // a clause of another (or this) version
    if (mode !== "history" && mode !== "clean") setMode("history");
    if (v === version) {
      const e = toc.find((t) => t.key === key);
      if (e) setTimeout(() => view.current?.scrollToOrd(e.ord), 80);
      return;
    }
    pendingJump.current = key;  // done once that version's TOC is there
    setVersion(v);
  }, [mode, version, toc]);

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
          {(["history", "compare", "clean", "stats", "crs"] as Mode[]).map((m) => (
            <button key={m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
              {{ history: "History", compare: "Compare", clean: "Clean", stats: "Statistics", crs: "CR" }[m]}
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
        {mode !== "history" && <div className="spacer" />}
        <button onClick={() => setFindOpen(true)} title="검색 (Ctrl+F)">🔍</button>
        <button onClick={() => setInfoOpen(!infoOpen)} title="변경 정보 패널">{infoOpen ? "⇥" : "⇤"}</button>
      </header>

      <div className="body">
        <aside className="left">
          <div className="small toc-filter">
            <label><input type="checkbox" checked={tocChangedOnly} onChange={(e) => setTocChangedOnly(e.target.checked)} /> 변경된 절만</label>
            <label><input type="checkbox" checked={bookmarkedOnly} onChange={(e) => setBookmarkedOnly(e.target.checked)} /> ★ 북마크만 ({bookmarks.size})</label>
          </div>
          <Toc toc={toc} active={visible} baseline={baseline} highlight={mode === "history"}
               changedOnly={tocChangedOnly && mode === "history"} bookmarks={bookmarks} bookmarkedOnly={bookmarkedOnly}
               onJump={(o) => jump(o)} />
        </aside>

        <main className="center">
          {findOpen && <FindBar spec={spec} version={version} colors={colors} onGo={onFind} onOpenAt={openAt}
                               onClose={() => setFindOpen(false)} />}
          {mode === "crs" ? (
            <CRBrowser spec={spec} crs={crs} toc={toc} colors={colors} onJump={(o) => jump(o)} onOpenSpec={onOpenSpec}
                       onFetchCrs={fetchCrs} fetching={crJob} />
          ) : mode === "stats" ? (
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
                           onVisible={setVisible} onXref={onXref} bookmarks={bookmarks} onBookmark={toggleBookmark} />
              {mode === "history" && <ChangeRuler toc={toc} baseline={baseline} onJump={(o) => jump(o)} />}
            </div>
          )}
        </main>

        {infoOpen && (mode === "history" || mode === "clean") && (
          <InfoPanel spec={spec} selection={selection} version={version} baseline={baseline} crsByVersion={crsByVersion}
                     colors={colors} onClose={() => setSelection(null)} onFetchCrs={fetchCrs} fetching={crJob}
                     note={selection ? notes.get(selection.clause.key) : undefined}
                     onSaveNote={(key, text) => updateNote(key, { text })} />
        )}
      </div>

      {paletteOpen && (
        <CommandPalette spec={spec} toc={toc} versions={versions} onJump={(o) => jump(o)} onVersion={setVersion}
                        onMode={setMode} onOpenSpec={onOpenSpec} onClose={() => setPaletteOpen(false)} />
      )}
      <Timeline versions={versions} version={version} baseline={baseline} colors={colors}
                onVersion={setVersion} onBaseline={setBaseline} />
    </div>
  );
}
