// Small markdown renderer for AI answers (chat, release notes): headings, lists, bold/italics, code, LaTeX
// (KaTeX) and source tags [[...]] turned into chips the views make clickable.
import katex from "katex";
import type { ChatSource } from "./api";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function tex(src: string, display: boolean) {
  try {
    return katex.renderToString(src, { displayMode: display, throwOnError: false });
  } catch {
    return esc(src);
  }
}

/** "CR 38.211 0042, 0045" or "CR 38.211 0040-0046" (several CRs in one tag) → one tag per CR the answer's
 *  sources know; anything else stays as it is. */
export function expandTag(tag: string, sources: Record<string, ChatSource>): string[] {
  const m = tag.match(/^CR\s+(\S+)\s+(.+)$/);
  if (sources[tag] || !m) return [tag];
  const nums: string[] = [];
  for (const part of m[2].split(/[,;\s]+/).filter(Boolean)) {
    const r = part.match(/^(\d+)-(\d+)$/);
    if (r && Number(r[2]) - Number(r[1]) <= 30) {
      for (let n = Number(r[1]); n <= Number(r[2]); n++) nums.push(String(n).padStart(r[1].length, "0"));
    } else nums.push(part.replace(/^CR/i, ""));
  }
  const tags = nums.map((n) => `CR ${m[1]} ${n}`).filter((t) => sources[t]);
  return tags.length ? tags : [tag];
}

/** Inline markdown: code, math, bold, italics and source tags. */
function inline(text: string, sources: Record<string, ChatSource>): string {
  const keep: string[] = [];
  const stash = (html: string) => `\u0000${keep.push(html) - 1}\u0000`;
  let t = text
    .replace(/`([^`]+)`/g, (_, c) => stash(`<code>${esc(c)}</code>`))
    .replace(/\$\$([^$]+)\$\$/g, (_, m) => stash(tex(m, true)))
    .replace(/\$([^$\n]+)\$/g, (_, m) => stash(tex(m, false)))
    .replace(/\[\[([^\]]+)\]\]/g, (_, raw) => expandTag(raw.trim(), sources).map((tag) => {
      const s = sources[tag];
      const kind = s ? s.type : "none";
      const title = !s ? "답변이 근거로 든 자료 (목록에 없음)" : s.type === "clause" ? `${s.title} — 뷰어에서 열기`
        : s.type === "cr" ? `${s.title} — CR 문서 열기` : "CR 모드에서 이 WI 를 고르면 요약을 볼 수 있습니다";
      return stash(`<span class="src src-${kind}" data-src="${esc(tag)}" title="${esc(title)}">${esc(tag)}</span>`);
    }).join(" "));
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

