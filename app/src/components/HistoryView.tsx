// The main reading view: the whole spec as one virtualised stream of clauses,
// loaded page by page while scrolling, with per-version change highlights.
import { useVirtualizer } from "@tanstack/react-virtual";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api, cmpVersion, type Clause, type TocEntry } from "../api";
import { Html } from "./Html";

const PAGE = 25;

export interface Selection { clause: Clause; block: number | null }

export interface HistoryViewHandle { scrollToOrd: (ord: number, block?: number | null) => void }

interface Props {
  spec: string;
  citeLabel: string;
  version: string;
  baseline: string;
  toc: TocEntry[];
  highlight: boolean;
  selected: Selection | null;
  onSelect: (s: Selection) => void;
  onVisible: (ord: number) => void;
  onXref: (clause: string | undefined, spec: string | undefined) => void;
  bookmarks: Set<string>;
  onBookmark: (key: string) => void;
}

const isNewer = (v: string | null | undefined, baseline: string) => !!v && cmpVersion(v, baseline) > 0;

const KEEP = 1500;  // clauses kept in memory

export const HistoryView = forwardRef<HistoryViewHandle, Props>(function HistoryView(
  { spec, version, citeLabel, baseline, toc, highlight, selected, onSelect, onVisible, onXref, bookmarks, onBookmark }, ref,
) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [cache, setCache] = useState<Map<number, Clause>>(new Map());
  const loading = useRef(new Set<number>());
  const [flash, setFlash] = useState<{ ord: number; block: number | null } | null>(null);
  const current = useRef("");

  useEffect(() => {
    current.current = `${spec}@${version}`;
    setCache(new Map());
    loading.current.clear();
  }, [spec, version]);

  const virt = useVirtualizer({
    count: toc.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => (toc[i]?.level === 1 ? 140 : 110),
    overscan: 6,
  });
  const items = virt.getVirtualItems();

  // Load whichever pages the visible window needs.
  useEffect(() => {
    const pages = new Set(items.map((it) => Math.floor(it.index / PAGE)));
    for (const p of pages) {
      if (loading.current.has(p) || cache.has(p * PAGE)) continue;
      loading.current.add(p);
      const key = `${spec}@${version}`;
      const lo = items[0].index, hi = items[items.length - 1].index;
      api.clauses(spec, version, p * PAGE, PAGE).then((cs) => {
        if (key !== current.current) return;
        setCache((old) => {
          const next = new Map(old);
          for (const c of cs) next.set(c.ord, c);
          if (next.size > KEEP) {  // a big conformance spec scrolled end to end: drop what is far from view
            for (const ord of next.keys()) {
              if (ord < lo - KEEP / 2 || ord > hi + KEEP / 2) {
                next.delete(ord);
                loading.current.delete(Math.floor(ord / PAGE));
              }
            }
          }
          return next;
        });
      }, () => {
        // the backend was busy or restarting: allow another try instead of a skeleton for good
        setTimeout(() => { if (key === current.current) { loading.current.delete(p); setCache((c) => new Map(c)); } }, 2000);
      });
    }
    // the clause at the top of the view (the virtual items also include a few rendered above it)
    const top = virt.scrollOffset ?? 0;
    const first = items.find((it) => it.end > top + 8);
    if (first) onVisible(first.index);
  }, [items, spec, version, cache, onVisible]);

  useImperativeHandle(ref, () => ({
    scrollToOrd: (ord, block = null) => {
      virt.scrollToIndex(ord, { align: "start" });
      setFlash({ ord, block });
      // Re-align once the target page is loaded and measured.
      setTimeout(() => virt.scrollToIndex(ord, { align: "start" }), 250);
      setTimeout(() => setFlash(null), 2200);
    },
  }), [virt]);

  const onClick = useCallback((e: React.MouseEvent) => {
    const x = (e.target as HTMLElement).closest<HTMLElement>(".xref");
    if (x) {
      e.stopPropagation();
      onXref(x.dataset.clause, x.dataset.spec);
    }
  }, [onXref]);

  return (
    <div ref={parentRef} className={`doc ${highlight ? "hl" : "clean"}`} onClick={onClick}>
      <div style={{ height: virt.getTotalSize(), position: "relative" }}>
        {items.map((it) => {
          const c = cache.get(it.index);
          const t = toc[it.index];
          return (
            <div key={it.key} data-index={it.index} ref={virt.measureElement} className="clause-wrap"
                 style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${it.start}px)` }}>
              {c ? (
                <ClauseView clause={c} starred={bookmarks.has(c.key)} onBookmark={onBookmark} cite={`${citeLabel} ${c.number ? `§${c.number}` : c.title} (v${version})`} baseline={baseline} highlight={highlight}
                            selectedBlock={selected?.clause.key === c.key ? selected.block : undefined}
                            flashBlock={flash?.ord === c.ord ? flash.block ?? -1 : undefined}
                            onSelect={onSelect} />
              ) : (
                <div className="clause placeholder"><Heading entry={t} /><div className="skeleton" /></div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
});

function Heading({ entry, badge, cite, starred, onBookmark }: {
  entry: TocEntry; badge?: React.ReactNode; cite?: string; starred?: boolean; onBookmark?: (key: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const Tag = (`h${Math.min(Math.max(entry.level, 1), 6)}`) as "h1";
  return (
    <Tag className="clause-title" id={`c-${entry.key}`}>
      {onBookmark && (
        <button className={`star ${starred ? "on" : ""}`} title={starred ? "북마크 해제" : "북마크"}
                onClick={(e) => { e.stopPropagation(); onBookmark(entry.key); }}>{starred ? "★" : "☆"}</button>
      )}
      {entry.number && <span className="num">{entry.number}</span>}
      <span>{entry.title}</span>
      {badge}
      {cite && (
        <button className="cite" title={`인용 복사: ${cite}`} onClick={(e) => {
          e.stopPropagation();
          navigator.clipboard?.writeText(cite).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); });
        }}>{copied ? "✓ 복사됨" : "⧉"}</button>
      )}
    </Tag>
  );
}

function ClauseView({ clause, cite, starred, onBookmark, baseline, highlight, selectedBlock, flashBlock, onSelect }: {
  clause: Clause; cite: string; starred: boolean; onBookmark: (key: string) => void; baseline: string; highlight: boolean; selectedBlock?: number | null; flashBlock?: number;
  onSelect: (s: Selection) => void;
}) {
  const added = highlight && clause.status !== "base" && isNewer(clause.introduced, baseline);
  const changes = useMemo(() => clause.history.filter(([v]) => isNewer(v, baseline)), [clause, baseline]);
  const badge = highlight && changes.length > 0 && (
    <span className="badges">
      {added
        ? <span className="badge" data-v={clause.introduced}>new in {clause.introduced}</span>
        : changes.map(([v]) => <span key={v} className="badge" data-v={v}>{v}</span>)}
    </span>
  );
  const deletedAfter = (i: number) => (highlight ? clause.deleted.filter((d) => d.after === i && isNewer(d.version, baseline)) : []);

  return (
    <section className={`clause ${added ? "clause-added" : ""} ${flashBlock === -1 ? "flash" : ""}`}
             data-bv={added ? clause.introduced : undefined}>
      <div onClick={() => onSelect({ clause, block: null })}><Heading entry={clause} badge={badge} cite={cite} starred={starred} onBookmark={onBookmark} /></div>
      {deletedAfter(-1).map((d, k) => <Deleted key={`d-${k}`} {...d} />)}
      {clause.blocks.map((b, i) => (
        <div key={i}>
          <div className={`blk kind-${b.kind} ${b.style ? `st-${b.style}` : ""} ${selectedBlock === i ? "sel" : ""} ${flashBlock === i ? "flash" : ""}`}
               data-bv={highlight && isNewer(b.changed, baseline) ? b.changed! : undefined}
               onClick={() => onSelect({ clause, block: i })}>
            <Html html={b.html} />
          </div>
          {deletedAfter(i).map((d, k) => <Deleted key={`d-${i}-${k}`} {...d} />)}
        </div>
      ))}
    </section>
  );
}

function Deleted({ html, version, moved_to }: { html: string; version: string; moved_to?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="deleted" data-bv={version}>
      <button className="link" onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>
        {open ? "▾" : "▸"} {moved_to ? `${version} 에서 §${moved_to} 로 이동된 내용` : `${version} 에서 삭제된 내용`}
      </button>
      {open && <Html html={html} className="deleted-body" />}
    </div>
  );
}
