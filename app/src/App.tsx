// Shell: a fixed main screen (downloads, settings) plus viewer tabs. A new viewer tab shows every downloaded
// spec at a glance; cross-references open more viewer tabs.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ChatContext } from "./api";
import { ASK_EVENT } from "./ask";
import { ChatView } from "./components/ChatView";
import { Home } from "./components/Home";
import { SpecPicker } from "./components/SpecPicker";
import { SpecTab, type TabState } from "./components/SpecTab";

const HOME = "home";
const CHAT = "chat";

let nextId = 1;
const newTab = (p: Partial<TabState> = {}): TabState => ({ id: String(nextId++), spec: null, ...p });

export function App() {
  const [tabs, setTabs] = useState<TabState[]>([]);
  const [activeId, setActiveId] = useState<string>(HOME);
  const [homePrefill, setHomePrefill] = useState<string | undefined>();
  const [findSignal, setFindSignal] = useState(0);
  const [paletteSignal, setPaletteSignal] = useState(0);
  const [askContext, setAskContext] = useState<ChatContext | null>(null);
  const [chatOpened, setChatOpened] = useState(false);  // mount the chat on first use only

  const update = useCallback((id: string, patch: Partial<TabState>) =>
    setTabs((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t))), []);

  const open = useCallback((t: TabState) => {
    setTabs((ts) => [...ts, t]);
    setActiveId(t.id);
  }, []);

  const close = useCallback((id: string) => {
    if (id === HOME || id === CHAT) return;
    setTabs((ts) => {
      const i = ts.findIndex((t) => t.id === id);
      const rest = ts.filter((t) => t.id !== id);
      if (id === activeId) setActiveId(rest.length ? rest[Math.max(0, i - 1)].id : HOME);
      return rest;
    });
  }, [activeId]);

  const goHome = useCallback((prefill?: string) => {
    if (prefill) setHomePrefill(prefill);
    setActiveId(HOME);
  }, []);

  const opening = useRef(new Set<string>());
  const openSpec = useCallback(async (spec: string, clause?: string, version?: string, baseline?: string) => {
    const existing = tabs.find((t) => t.spec === spec);
    if (existing) {
      update(existing.id, { jumpTo: clause, jumpVersion: version, jumpBaseline: baseline });
      setActiveId(existing.id);
      return;
    }
    if (opening.current.has(spec)) return;  // a double click: the first one is opening it
    opening.current.add(spec);
    try {
      const known = (await api.specs()).some((s) => s.number === spec);
      if (known) open(newTab({ spec, jumpTo: clause, version, label: spec, jumpBaseline: baseline }));
      else goHome(spec); // not downloaded yet: suggest it on the main screen
    } catch {
      goHome(spec);
    } finally {
      opening.current.delete(spec);
    }
  }, [tabs, open, update, goHome]);

  useEffect(() => {
    const h = (e: Event) => {
      setAskContext((e as CustomEvent<ChatContext>).detail);
      setChatOpened(true);
      setActiveId(CHAT);
    };
    window.addEventListener(ASK_EVENT, h);
    return () => window.removeEventListener(ASK_EVENT, h);
  }, []);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      const viewer = tabs.some((t) => t.id === activeId && t.spec);  // a tab still picking a spec has no find bar
      if (k === "f" && viewer) { e.preventDefault(); setFindSignal((n) => n + 1); }
      if (k === "p" && viewer) { e.preventDefault(); setPaletteSignal((n) => n + 1); }
      if (k === "t") { e.preventDefault(); open(newTab()); }
      if (k === "w" && activeId !== HOME && activeId !== CHAT) { e.preventDefault(); close(activeId); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [activeId, open, close, tabs]);

  return (
    <div className="app">
      <div className="tabbar">
        <div className={`tab tab-home ${activeId === HOME ? "on" : ""}`} onClick={() => setActiveId(HOME)}
             title="다운로드 · 설정">메인</div>
        <div className={`tab tab-home tab-chat ${activeId === CHAT ? "on" : ""}`}
             onClick={() => { setChatOpened(true); setActiveId(CHAT); }}
             title="받은 스펙·CR·AI 분석을 근거로 질문에 답합니다">AI 채팅</div>
        {tabs.map((t) => (
          <div key={t.id} className={`tab ${t.id === activeId ? "on" : ""}`} onClick={() => setActiveId(t.id)}
               onAuxClick={(e) => e.button === 1 && close(t.id)}>
            <span>{t.spec ? t.label ?? t.spec : "스펙 열기"}</span>
            <button className="x" onClick={(e) => { e.stopPropagation(); close(t.id); }}>×</button>
          </div>
        ))}
        <button className="tab-add" onClick={() => open(newTab())} title="새 뷰어 탭 (Ctrl+T)">+</button>
      </div>
      <div className="tabpane" style={{ display: activeId === HOME ? "flex" : "none" }}>
        <Home prefill={homePrefill} active={activeId === HOME} onOpen={openSpec} />
      </div>
      {chatOpened && (
        <div className="tabpane" style={{ display: activeId === CHAT ? "flex" : "none" }}>
          <ChatView active={activeId === CHAT} pending={askContext} onPendingTaken={() => setAskContext(null)}
                    onOpenSpec={openSpec} />
        </div>
      )}
      {tabs.map((t) => (
        <div key={t.id} className="tabpane" style={{ display: t.id === activeId ? "flex" : "none" }}>
          {t.spec ? (
            <SpecTab tab={t} active={t.id === activeId} findSignal={findSignal}
                     paletteSignal={paletteSignal}
                     onUpdate={(p) => update(t.id, p)} onOpenSpec={openSpec} />
          ) : (
            <SpecPicker active={t.id === activeId} onOpen={(spec) => update(t.id, { spec, label: spec })}
                        onOpenClause={openSpec} onGoHome={() => goHome()} />
          )}
        </div>
      ))}
    </div>
  );
}
