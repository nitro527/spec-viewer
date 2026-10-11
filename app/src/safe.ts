// Document HTML from the backend is cleaned when a spec is imported; libraries imported before that may
// still hold a CR's javascript: hyperlink. The app's CSP already stops it from running; this also cleans
// what goes into exported files, which open without that policy.
const RISKY = /javascript:|vbscript:|<\s*(script|iframe|frame|object|embed|form|base|meta|link)\b|\son[a-z]+\s*=|srcdoc/i;
const SAFE_URL = /^\s*(https?:|mailto:|#|\/api\/asset\/|data:image\/(png|jpe?g|gif|webp|bmp);)/i;
const DROP = "script,iframe,frame,object,embed,form,base,meta,link,style";

export function safeHtml(html: string): string {
  if (!RISKY.test(html)) return html;  // almost every block: no parsing needed
  const t = document.createElement("template");
  t.innerHTML = html;
  t.content.querySelectorAll(DROP).forEach((e) => e.remove());
  t.content.querySelectorAll("*").forEach((e) => {
    for (const a of [...e.attributes]) {
      const n = a.name.toLowerCase();
      if (n.startsWith("on") || n === "srcdoc" || n === "formaction" || n === "xlink:href") e.removeAttribute(a.name);
      else if (["href", "src", "action", "poster", "background"].includes(n) && !SAFE_URL.test(a.value)) e.removeAttribute(a.name);
    }
  });
  return t.innerHTML;
}
