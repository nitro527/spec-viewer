// Renders a spec HTML fragment: KaTeX for equations and clickable cross-references.
import katex from "katex";
import { memo, useLayoutEffect, useRef } from "react";

const XREF = new RegExp(
  String.raw`\b([Cc]lauses?|[Ss]ubclauses?|[Aa]nnex)\s+((?:[A-Z]\.)?\d+(?:\.\d+)*|[A-Z])` +
    String.raw`(?:\s+(?:of|in)\s+(?:\[\d+,\s*)?(?:3GPP\s+)?(?:TS|TR)\s+(\d{2}\.\d{3}(?:-\d{1,2})?))?` +
    String.raw`|\b(?:3GPP\s+)?(TS|TR)\s+(\d{2}\.\d{3}(?:-\d{1,2})?)\b`,
  "g",
);

function linkify(root: HTMLElement) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) =>
      (n.parentElement?.closest(".math, a, .xref") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes: Text[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);
  for (const node of nodes) {
    const text = node.data;
    XREF.lastIndex = 0;
    let m: RegExpExecArray | null, last = 0;
    const frag = document.createDocumentFragment();
    while ((m = XREF.exec(text))) {
      frag.append(text.slice(last, m.index));
      const a = document.createElement("span");
      a.className = "xref";
      a.textContent = m[0];
      if (m[2]) {
        a.dataset.clause = m[2];
        if (m[3]) a.dataset.spec = m[3];
      } else {
        a.dataset.spec = m[5];
      }
      frag.append(a);
      last = m.index + m[0].length;
    }
    if (last === 0) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

function renderMath(root: HTMLElement) {
  root.querySelectorAll<HTMLElement>("span.math").forEach((el) => {
    if (el.dataset.rendered) return;
    try {
      katex.render(el.textContent ?? "", el, { throwOnError: false, displayMode: el.classList.contains("display") });
    } catch {
      /* leave raw LaTeX */
    }
    el.dataset.rendered = "1";
  });
}

export const Html = memo(function Html({ html, className }: { html: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!ref.current) return;
    linkify(ref.current);
    renderMath(ref.current);
  }, [html]);
  return <div ref={ref} className={className} dangerouslySetInnerHTML={{ __html: html }} />;
});
