// Typed client for the local specview backend.

export interface SpecInfo { number: string; title: string | null; versions: number; latest: string | null }
export interface VersionInfo { id: number; version: string; release: number }
export type HistoryEntry = [version: string, change: "added" | "modified" | "moved", movedFrom?: string];

export interface TocEntry {
  ord: number; key: string; number: string; title: string; level: number;
  status: "base" | "added" | "modified" | "unchanged"; introduced: string; last_changed: string | null;
}

export interface Block {
  kind: string; style: string; html: string;
  introduced: string; changed: string | null; history: HistoryEntry[];
}

export interface Clause extends TocEntry {
  history: HistoryEntry[];
  deleted: { after: number; html: string; version: string; moved_to?: string }[];
  blocks: Block[];
}

export interface CR {
  spec: string; new_version: string; date: string; meeting: string; tdoc: string;
  cr: string; rev: string; cat: string; subject: string;
}

export interface DiffBlock {
  op: "equal" | "replace" | "insert" | "delete" | "move-in" | "move-out";
  kind: string; style: string; old: string | null; new: string | null; diff: string;
  moved_from?: string; moved_to?: string;
}
export interface DiffClause { key: string; number: string; title: string; old_title: string | null; level: number; status: "added" | "modified" | "removed"; blocks: DiffBlock[] }
export interface CompareResult { spec: string; old: string; new: string; clauses: DiffClause[]; removed: DiffClause[]; crs: CR[] }

export interface VersionStats {
  version: string; release: number; added: number; modified: number; removed: number; clauses: number;
  crs: Record<string, number>;
}
export interface ClauseStats { ord: number; key: string; number: string; title: string; level: number; introduced: string; changes: string[] }
export interface Stats { spec: string; latest: string | null; versions: VersionStats[]; clauses: ClauseStats[] }

export interface SearchHit { ord: number; key: string; number: string; title: string; block: number | null; snippet: string }
export interface Job { id: string; state: "running" | "done" | "error"; done: number; total: number; message: string; specs: string[] }

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const s = encodeURIComponent;

export const api = {
  specs: () => get<SpecInfo[]>("/api/specs"),
  versions: (spec: string) => get<VersionInfo[]>(`/api/specs/${s(spec)}/versions`),
  toc: (spec: string, v: string) => get<TocEntry[]>(`/api/specs/${s(spec)}/${s(v)}/toc`),
  clauses: (spec: string, v: string, start: number, count: number) =>
    get<Clause[]>(`/api/specs/${s(spec)}/${s(v)}/clauses?start=${start}&count=${count}`),
  search: (spec: string, v: string, q: string) => get<SearchHit[]>(`/api/specs/${s(spec)}/${s(v)}/search?q=${s(q)}`),
  crs: (spec: string) => get<CR[]>(`/api/specs/${s(spec)}/crs`),
  compare: (spec: string, oldV: string, newV: string, changedOnly = true) =>
    get<CompareResult>(`/api/specs/${s(spec)}/compare?old=${s(oldV)}&new=${s(newV)}&changed_only=${changedOnly}`),
  stats: (spec: string) => get<Stats>(`/api/specs/${s(spec)}/stats`),
  jobs: () => get<Job[]>("/api/jobs"),
  fetchSpecs: async (specs: string[], releases: number[] | null, latestPerRelease: boolean): Promise<Job> => {
    const r = await fetch("/api/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ specs, releases, latest_per_release: latestPerRelease }),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
};

export function cmpVersion(a: string, b: string): number {
  const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) if (pa[i] !== pb[i]) return pa[i] - pb[i];
  return 0;
}

export function tdocUrl(tdoc: string): string {
  return `https://portal.3gpp.org/ngppapp/CreateTdoc.aspx?mode=view&contributionUid=${s(tdoc)}`;
}
