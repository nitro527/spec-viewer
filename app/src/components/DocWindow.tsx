// Separate window for WG meeting documents (feature lead summaries, meeting reports, draft CRs).
// Opened as /#/doc?folder=...&tdoc=...&find=... ; scrolls to the passage the panel quoted.
import { useEffect, useRef, useState } from "react";
import { api, type MeetingDoc } from "../api";
import { Html } from "./Html";

const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();

export function openMeetingDoc(folder: string, tdoc?: string, find?: string) {
  const q = new URLSearchParams({ folder });
  if (tdoc) q.set("tdoc", tdoc);
  if (find) q.set("find", find.slice(0, 120));
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
  const [doc, setDoc] = useState<MeetingDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.title = `${tdoc ?? "회의록"} — Spec Viewer`;
    api.meetingDoc(folder, tdoc).then(setDoc, (e) => setError(String(e).replace(/^Error: /, "")));
  }, [folder, tdoc]);

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
        </div>
      </header>
      {error && <p className="error docwin-body">{error}</p>}
      {!doc && !error && <p className="muted docwin-body">문서를 받아서 변환하는 중… (처음 여는 문서는 몇 초 걸립니다)</p>}
      {doc && (
        <div ref={body} className="docwin-body" onClick={(e) => {
          // a Tdoc of the same meeting (R1-1812056) opens in another viewer window instead of the browser
          const a = (e.target as HTMLElement).closest("a");
          const id = a?.textContent?.trim() ?? "";
          if (a && /^R[1-4]-\d{6,7}$/.test(id)) {
            e.preventDefault();
            openMeetingDoc(folder, id);
          }
        }}>
          <Html html={doc.html} className="doc-html" />
        </div>
      )}
    </div>
  );
}
