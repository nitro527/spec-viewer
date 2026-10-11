// Separate window for WG meeting documents (feature lead summaries, meeting reports, draft CRs) and the CRs of a
// spec's Change history. Opened as /#/doc?folder=...&tdoc=...&find=... (scrolls to the passage the panel quoted)
// or /#/doc?spec=...&cr=...&tdoc=... (a CR, with its revision marks).
import { useEffect, useRef, useState } from "react";
import { api, crKey, tdocUrl, type MeetingDoc } from "../api";
import { CRAnalysisFull, useAiJob, useCRAnalyses } from "./CRAnalysis";
import { Html } from "./Html";

const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();

export function openMeetingDoc(folder: string, tdoc?: string, find?: string) {
  const q = new URLSearchParams({ folder });
  if (tdoc) q.set("tdoc", tdoc);
  if (find) q.set("find", find.slice(0, 120));
  window.open(`${location.origin}${location.pathname}#/doc?${q}`, "_blank");
}

export function openCRDoc(spec: string, cr: string, tdoc: string) {
  const q = new URLSearchParams({ spec, cr, tdoc });
  window.open(`${location.origin}${location.pathname}#/doc?${q}`, "_blank");
}

/** First words of a quoted passage, to find it again in the document. */
export function findKey(text: string): string {
  // the most distinctive of the first lines: "Text proposal for TS38.211" occurs many times in a report,
  // "6.4.1.1.1.2 Sequence generation when transform precoding is enabled" once
  // Equations are missing from the quoted text ("if  is configured"), so only use a run without gaps.
  const runs = text.split("\n").slice(0, 8)
    .map((l) => l.replace(/^\[[^\]]*\]\s*/, "").replace(/^[-–•]\s*/, ""))
    .flatMap((l) => l.split(/\s{2,}|\t/))
    .map((r) => r.trim())
    .filter((r) => r.length > 12 && r !== "…" && !/^[-–—=*>.\s]+$/.test(r) && !/start of tp/i.test(r));
  return runs.reduce((a, b) => (b.length > a.length ? b : a), "").slice(0, 80);
}

export function DocWindow() {
  const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const folder = params.get("folder") ?? "", tdoc = params.get("tdoc") ?? undefined, find = params.get("find") ?? "";
  const spec = params.get("spec"), cr = params.get("cr");
  const [doc, setDoc] = useState<MeetingDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.title = `${cr ? `CR${cr}` : tdoc ?? "회의록"} — Spec Viewer`;
    const load = spec && cr && tdoc ? api.crDoc(spec, cr, tdoc) : api.meetingDoc(folder, tdoc);
    load.then(setDoc, (e) => setError(String(e).replace(/^Error: /, "")));
  }, [folder, tdoc, spec, cr]);

  useEffect(() => {  // jump to the quoted passage
    if (!doc || !find || !body.current) return;
    const want = norm(find).slice(0, 60);
    // the smallest element holding the passage (summaries often sit inside one big table cell)
    const el = [...body.current.querySelectorAll<HTMLElement>("p, li, td, h1, h2, h3, h4, h5, h6")]
      .filter((e) => norm(e.textContent ?? "").includes(want))
      .sort((a, b) => (a.textContent ?? "").length - (b.textContent ?? "").length)[0];
    if (el) {
      el.classList.add("doc-hit");
      setTimeout(() => el.scrollIntoView({ block: "center" }), 50);
    }
  }, [doc, find]);

  const search = (backwards = false) => {
    // Chromium's find-in-page: selects and scrolls to the next match
    if (q) (window as unknown as { find: (s: string, c: boolean, b: boolean, w: boolean) => boolean }).find(q, false, backwards, true);
  };

  return (
    <div className="docwin">
      <header className="docwin-head">
        <div>
          <b>{doc?.meeting ?? ""}</b> {tdoc && <span className="docwin-tdoc">{tdoc}</span>} <span>{doc?.title}</span>
        </div>
        <div className="row">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="문서 안에서 찾기"
                 onKeyDown={(e) => e.key === "Enter" && search(e.shiftKey)} />
          <button onClick={() => search()}>다음</button>
          {doc?.url && <a href={doc.url} target="_blank" rel="noreferrer" className="small">원문 받기</a>}
          {cr && tdoc && <a href={tdocUrl(tdoc)} target="_blank" rel="noreferrer" className="small">3GPP 포털</a>}
        </div>
      </header>
      {error && <p className="error docwin-body">{error}</p>}
      {!doc && !error && <p className="muted docwin-body">문서를 받아서 변환하는 중… (처음 여는 문서는 몇 초 걸립니다)</p>}
      {spec && cr && tdoc && <CRAnalysisSection spec={spec} cr={cr} tdoc={tdoc} />}
      {doc && (
        <div ref={body} className="docwin-body" onClick={(e) => {
          // a Tdoc of the same meeting (R1-1812056) opens in another viewer window instead of the browser
          const a = (e.target as HTMLElement).closest("a");
          const id = a?.textContent?.trim() ?? "";
          if (a && folder && /^R[1-4]-\d{6,7}$/.test(id)) {
            e.preventDefault();
            openMeetingDoc(folder, id);
          }
        }}>
          {cr && <p className="muted small">CR 의 수정 내용: <ins>추가</ins> · <del>삭제</del></p>}
          <Html html={doc.html} className="doc-html" />
        </div>
      )}
    </div>
  );
}

function CRAnalysisSection({ spec, cr, tdoc }: { spec: string; cr: string; tdoc: string }) {
  const analyses = useCRAnalyses(spec);
  const job = useAiJob();
  const [llmReady, setLlmReady] = useState(false);
  useEffect(() => {
    api.llm().then((s) => setLlmReady(s.has_key || s.provider !== "gemini"), () => setLlmReady(false));
  }, []);
  const a = analyses.get(crKey(cr, tdoc));
  const run = (force: boolean) => job.start(() => api.analyzeCR(spec, cr, tdoc, force),
                                            force ? "CR 다시 분석 요청…" : "CR 분석 요청…");
  return (
    <details className="docwin-analysis" open>
      <summary><b>AI 분석</b> {a ? <span className="muted small">CR 문서·세부 CR·회의 합의·FL 요약을 근거로</span>
                                     : <span className="muted small">아직 없음</span>}</summary>
      {a && <CRAnalysisFull a={a} />}
      <p className="small">
        {llmReady
          ? <button className="link" disabled={job.busy} onClick={() => run(!!a)}>{a ? "다시 분석" : "AI 로 이 CR 분석"}</button>
          : <span className="muted">AI 분석: 메인 탭에서 LLM 설정 필요</span>}
        {!a && <span className="muted tiny"> · 문단을 AI 요약하면 관련 CR 분석도 함께 만들어집니다</span>}
        {job.msg && <span> · {job.msg}</span>}
      </p>
    </details>
  );
}
