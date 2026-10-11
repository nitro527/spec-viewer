// AI chat over the downloaded material. The model looks things up (search, clauses, history, CRs, work items)
// and answers with source tags [[38.211 §6.3.3.2 v19.5.0]] / [[CR 38.211 0023]] that become links.
import katex from "katex";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Chat, type ChatContext, type ChatMessage, type ChatSource, type ChatSummary } from "../api";
import { useLlmReady } from "./CRAnalysis";
import { openCRDoc } from "./DocWindow";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function tex(src: string, display: boolean) {
  try {
    return katex.renderToString(src, { displayMode: display, throwOnError: false });
  } catch {
    return esc(src);
  }
}

/** Inline markdown: code, math, bold, italics and source tags. */
function inline(text: string, sources: Record<string, ChatSource>): string {
  const keep: string[] = [];
  const stash = (html: string) => `\u0000${keep.push(html) - 1}\u0000`;
  let t = text
    .replace(/`([^`]+)`/g, (_, c) => stash(`<code>${esc(c)}</code>`))
    .replace(/\$\$([^$]+)\$\$/g, (_, m) => stash(tex(m, true)))
    .replace(/\$([^$\n]+)\$/g, (_, m) => stash(tex(m, false)))
    .replace(/\[\[([^\]]+)\]\]/g, (_, tag) => {
      const s = sources[tag.trim()];
      const kind = s ? s.type : "none";
      const title = !s ? "답변이 근거로 든 자료 (목록에 없음)" : s.type === "clause" ? `${s.title} — 뷰어에서 열기`
        : s.type === "cr" ? `${s.title} — CR 문서 열기` : "CR 모드에서 이 WI 를 고르면 요약을 볼 수 있습니다";
      return stash(`<span class="src src-${kind}" data-src="${esc(tag.trim())}" title="${esc(title)}">${esc(tag.trim())}</span>`);
    });
  t = esc(t)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
  return t.replace(/\u0000(\d+)\u0000/g, (_, i) => keep[Number(i)]);
}

/** Small markdown: headings, bullet/numbered lists, paragraphs, display math. */
export function renderAnswer(md: string, sources: Record<string, ChatSource>): string {
  const out: string[] = [];
  let list: "ul" | "ol" | null = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of md.replace(/\r/g, "").split("\n")) {
    const line = raw.trimEnd();
    let m: RegExpMatchArray | null;
    if (!line.trim()) { close(); continue; }
    if ((m = line.match(/^\s*(#{1,4})\s+(.*)$/))) {
      close();
      out.push(`<h${Math.min(m[1].length + 2, 6)}>${inline(m[2], sources)}</h${Math.min(m[1].length + 2, 6)}>`);
    } else if ((m = line.match(/^(\s*)[-*•]\s+(.*)$/)) || (m = line.match(/^(\s*)\d+[.)]\s+(.*)$/))) {
      const kind = /^\s*\d/.test(line) ? "ol" : "ul";
      if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
      out.push(`<li class="${m[1].length >= 2 ? "sub" : ""}">${inline(m[2], sources)}</li>`);
    } else if ((m = line.match(/^\s*\$\$(.+)\$\$\s*$/))) {
      close();
      out.push(tex(m[1], true));
    } else {
      close();
      out.push(`<p>${inline(line, sources)}</p>`);
    }
  }
  close();
  return out.join("");
}

export function ChatView({ active, pending, onPendingTaken, onOpenSpec }: {
  active: boolean; pending: ChatContext | null; onPendingTaken: () => void;
  onOpenSpec: (spec: string, clause?: string, version?: string) => void;
}) {
  const llmReady = useLlmReady();
  const [list, setList] = useState<ChatSummary[]>([]);
  const [chat, setChat] = useState<Chat | null>(null);
  const [question, setQuestion] = useState("");
  const [context, setContext] = useState<ChatContext | null>(null);
  const [job, setJob] = useState<{ id: string; chatId: string; steps: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const loadList = useCallback(() => api.chats().then(setList, () => undefined), []);
  useEffect(() => { if (active) loadList(); }, [active, loadList]);
  useEffect(() => {  // "ask about this paragraph" from a viewer tab: a new conversation with that context
    if (!pending) return;
    setChat(null);
    setContext(pending);
    onPendingTaken();
    setTimeout(() => input.current?.focus(), 50);
  }, [pending, onPendingTaken]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [chat, job?.steps.length]);

  useEffect(() => {
    if (!job) return;
    const t = setInterval(async () => {
      const j = (await api.jobs()).find((x) => x.id === job.id);
      if (!j) return;
      if (j.state === "running") return setJob((cur) => cur && { ...cur, steps: j.steps ?? [] });
      setJob(null);
      const done = await api.chat(job.chatId);
      setChat((cur) => (!cur || cur.id === done.id ? done : cur));  // the user may have opened another one
      loadList();
    }, 1000);
    return () => clearInterval(t);
  }, [job, loadList]);

  const open = async (id: string) => {
    setError(null);
    setContext(null);
    setChat(await api.chat(id));
  };
  const send = async () => {
    const q = question.trim();
    if (!q || job) return;
    setError(null);
    try {
      const r = await api.ask(q, chat?.id ?? null, chat ? null : context);
      setQuestion("");
      setContext(null);
      setJob({ id: r.job.id, chatId: r.chat_id, steps: [] });
      setChat(await api.chat(r.chat_id));
      loadList();
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    }
  };
  const onAnswerClick = (e: React.MouseEvent, m: ChatMessage) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-src]");
    const s = el && m.sources?.[el.dataset.src!];
    if (!s) return;
    if (s.type === "clause") onOpenSpec(s.spec, s.clause, s.version);
    else if (s.type === "cr") openCRDoc(s.spec, s.cr, s.tdoc);
  };

  return (
    <div className="chat">
      <aside className="chat-list">
        <button className="primary" onClick={() => { setChat(null); setContext(null); setError(null); input.current?.focus(); }}>
          + 새 대화</button>
        <ul>
          {list.map((c) => (
            <li key={c.id} className={chat?.id === c.id ? "on" : ""} onClick={() => open(c.id)} title={c.updated}>
              <span>{c.title}</span>
              <button className="x" title="대화 삭제" onClick={async (e) => {
                e.stopPropagation();
                if (!confirm(`"${c.title}" 대화를 지울까요?`)) return;
                await api.deleteChat(c.id);
                if (chat?.id === c.id) setChat(null);
                loadList();
              }}>×</button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="chat-main">
        <div className="chat-msgs">
          {!chat && !job && (
            <div className="chat-empty">
              <h2>받은 스펙에 대해 물어보세요</h2>
              <p className="muted">
                AI 가 받아 둔 스펙 본문(모든 버전)·변경 이력·CR·회의 합의·AI 분석을 찾아 읽고, 출처를 달아 답합니다.
                출처를 누르면 그 절이나 CR 문서가 열립니다.
              </p>
              <ul className="chat-examples">
                {["38.211 에서 msgA PUSCH 의 DM-RS 는 어떻게 설정돼?",
                  "38.211 4.5절 carrier aggregation slot offset 은 Rel-16 에서 왜 바뀌었어?",
                  "NR_2step_RACH-Core 로 38.211 과 38.213 에 뭐가 추가됐는지 정리해줘",
                  "CR0023 이 바꾼 절과 이유를 알려줘"].map((q) => (
                  <li key={q}><button className="link" onClick={() => { setQuestion(q); input.current?.focus(); }}>{q}</button></li>
                ))}
              </ul>
            </div>
          )}
          {chat?.messages.map((m, i) => (
            <div key={i} className={`msg msg-${m.role} ${m.error ? "msg-error" : ""}`}>
              {m.role === "user" ? (
                <>
                  {m.context && <div className="ctx">TS {m.context.spec} §{m.context.clause}{m.context.version ? ` v${m.context.version}` : ""} 에 대해</div>}
                  <div className="bubble">{m.content}</div>
                </>
              ) : (
                <>
                  {m.steps && m.steps.length > 0 && (
                    <details className="steps"><summary>찾아본 자료 {m.steps.length}건</summary>
                      <ol>{m.steps.map((s, k) => <li key={k}>{s}</li>)}</ol></details>
                  )}
                  <div className="answer" onClick={(e) => onAnswerClick(e, m)}
                       dangerouslySetInnerHTML={{ __html: renderAnswer(m.content, m.sources ?? {}) }} />
                  {m.model && <p className="muted tiny">{m.model}{m.mode === "retrieval" ? " · 검색 결과 기반(도구 호출 미지원 서버)" : ""}
                    {" · AI 답변은 출처의 원문으로 확인하세요"}</p>}
                </>
              )}
            </div>
          ))}
          {job && (
            <div className="msg msg-assistant">
              <div className="thinking">
                <span className="spinner" /> 자료를 찾아보는 중…
                <ol>{job.steps.map((s, k) => <li key={k}>{s}</li>)}</ol>
              </div>
            </div>
          )}
          <div ref={bottom} />
        </div>
        <div className="chat-input">
          {error && <p className="small status-error">{error}</p>}
          {context && !chat && (
            <div className="ctx">
              TS {context.spec} §{context.clause}{context.version ? ` v${context.version}` : ""} 문단에 대해 질문
              <button className="link tiny" onClick={() => setContext(null)}>✕</button>
              {context.text && <div className="ctx-text">{context.text.slice(0, 300)}{context.text.length > 300 ? "…" : ""}</div>}
            </div>
          )}
          <div className="row">
            <textarea ref={input} rows={2} value={question} disabled={!llmReady}
                      placeholder={llmReady ? "질문을 입력하세요 (Enter 보내기, Shift+Enter 줄바꿈)" : "메인 탭에서 LLM 을 먼저 설정하세요"}
                      onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
            <button className="primary" onClick={send} disabled={!llmReady || !!job || !question.trim()}>보내기</button>
          </div>
        </div>
      </main>
    </div>
  );
}
