// Tabbed shell: each tab is a spec (or the library home). Cross-references open new tabs.
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Home } from "./components/Home";
import { SpecTab, type TabState } from "./components/SpecTab";

let nextId = 1;
const newTab = (p: Partial<TabState> = {}): TabState => ({ id: String(nextId++), spec: null, ...p });

export function App() {
  const [tabs, setTabs] = useState<TabState[]>(() => [newTab()]);
  const [activeId, setActiveId] = useState(tabs[0].id);
  const [findSignal, setFindSignal] = useState(0);
  const [paletteSignal, setPaletteSignal] = useState(0);

  const update = useCallback((id: string, patch: Partial<TabState>) =>
    setTabs((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t))), []);

  const open = useCallback((t: TabState) => {
    setTabs((ts) => [...ts, t]);
    setActiveId(t.id);
  }, []);

  const close = useCallback((id: string) => {
    setTabs((ts) => {
      const i = ts.findIndex((t) => t.id === id);
      const rest = ts.filter((t) => t.id !== id);
      if (!rest.length) rest.push(newTab());
      if (id === activeId) setActiveId(rest[Math.max(0, i - 1)].id);
      return rest;
    });
  }, [activeId]);

  const openSpec = useCallback(async (spec: string, clause?: string) => {
    const existing = tabs.find((t) => t.spec === spec);
    if (existing) {
      update(existing.id, { jumpTo: clause });
      setActiveId(existing.id);
      return;
    }
    const known = (await api.specs()).some((s) => s.number === spec);
    open(known ? newTab({ spec, jumpTo: clause, label: spec }) : newTab({ prefill: spec }));
  }, [tabs, open, update]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); setFindSignal((n) => n + 1); }
      if (mod && e.key.toLowerCase() === "p") { e.preventDefault(); setPaletteSignal((n) => n + 1); }
      if (mod && e.key.toLowerCase() === "t") { e.preventDefault(); open(newTab()); }
      if (mod && e.key.toLowerCase() === "w") { e.preventDefault(); close(activeId); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [activeId, open, close]);

  return (
    <div className="app">
      <div className="tabbar">
        {tabs.map((t) => (
          <div key={t.id} className={`tab ${t.id === activeId ? "on" : ""}`} onClick={() => setActiveId(t.id)}
               onAuxClick={(e) => e.button === 1 && close(t.id)}>
            <span>{t.spec ? t.label ?? t.spec : "Library"}</span>
            <button className="x" onClick={(e) => { e.stopPropagation(); close(t.id); }}>×</button>
          </div>
        ))}
        <button className="tab-add" onClick={() => open(newTab())} title="새 탭 (Ctrl+T)">+</button>
      </div>
      {tabs.map((t) => (
        <div key={t.id} className="tabpane" style={{ display: t.id === activeId ? "flex" : "none" }}>
          {t.spec ? (
            <SpecTab tab={t} active={t.id === activeId} findSignal={t.id === activeId ? findSignal : 0}
                     paletteSignal={t.id === activeId ? paletteSignal : 0}
                     onUpdate={(p) => update(t.id, p)} onOpenSpec={openSpec} />
          ) : (
            <Home prefill={t.prefill} onOpen={(spec) => update(t.id, { spec, label: spec })} />
          )}
        </div>
      ))}
    </div>
  );
}
