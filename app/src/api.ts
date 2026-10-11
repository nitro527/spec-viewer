// Typed client for the local specview backend.

export interface SpecInfo {
  number: string; title: string | null; versions: number; latest: string | null;
  first: string | null; releases: number[]; crs: number;
}
export interface CatalogSpec { number: string; type: "TS" | "TR"; title: string; withdrawn: boolean }
export interface VersionInfo { id: number; version: string; release: number }
export type HistoryEntry = [version: string, change: "added" | "modified" | "moved", movedFrom?: string];

export interface TocEntry {
  ord: number; key: string; number: string; title: string; level: number;
  status: "base" | "added" | "modified" | "unchanged"; introduced: string; last_changed: string | null;
}

export interface Block {
  kind: string; style: string; html: string;
  introduced: string; changed: string | null; history: HistoryEntry[]; raw_hash: string;
}

export interface Clause extends TocEntry {
  history: HistoryEntry[];
  deleted: { after: number; html: string; version: string; moved_to?: string }[];
  blocks: Block[];
}

export interface CRDetail {
  title: string; source_wg: string; wi_code: string; category: string; release: string;
  reason: string; summary: string; consequences: string; clauses_raw: string; clauses: string[];
  wg_tdoc: string; current_version: string;
}

export interface CR {
  spec: string; new_version: string; date: string; meeting: string; tdoc: string;
  cr: string; rev: string; cat: string; subject: string;
  detail_status: "ok" | "missing" | "error" | null; detail: CRDetail | null;
  lib_version: string | null; // first downloaded version that contains this CR
}

/** Does a CR (by its "Clauses affected") touch this clause? Parent/child clauses count. */
export function crTouches(cr: CR, clause: string): boolean {
  if (!cr.detail || !clause) return false;
  return cr.detail.clauses.some((c) => c === clause || clause.startsWith(c + ".") || c.startsWith(clause + "."));
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

export interface UEFeature {
  section: string; section_title: string; release: string; layer: string; index: string; group: string;
  components: string; prerequisite: string; field_name: string; mandatory: string; note: string;
  shared?: string[]; matched?: string[];
}
export type FeatureVia = { kind: "cr"; cr: string; version: string; wi_code: string; subject: string } | { kind: "param"; name: string };
export interface FeatureClause { key: string | null; number: string; title: string; via: FeatureVia[]; shared: string[] }
export interface FeatureLinks { feature: UEFeature; specs: { spec: string; title: string | null; version: string; clauses: FeatureClause[] }[] }
export interface FeatureSection { section: string; title: string; layer: string; release: string; relevant: number; features: UEFeature[] }
export interface WorkItemFeatures { wi_code: string; releases: string[]; crs: { cr: string; version: string; subject: string }[]; sections: FeatureSection[] }
export interface ClauseFeatures { available: boolean; work_items: WorkItemFeatures[]; parameters: UEFeature[] }

export interface Intent { summary: string; motivation: string; impact: string; cr: string; confidence: string; model?: string }
export interface Annotation {
  clause: string; raw_hash: string; change: string; version: string; generated?: string;
  crs: { cr: string; tdoc: string; subject: string }[]; features: string[];
  agreements: { meeting?: string; agenda?: string; kind?: string; text: string; source?: string; folder?: string; via?: "tdoc" | "clause" | "terms" }[]; intent: Intent | null; intent_error?: string;
  draft_crs?: { tdoc: string; title: string; parent_cr: string; meeting: string; folder?: string }[];
  cr_analyses?: { cr: string; rev: string; tdoc: string }[];
  fl_summaries?: { tdoc: string; title: string; source: string; meeting: string; agenda: string; url: string; excerpt: string }[];
}

export interface CRAnalysis {
  spec: string; cr: string; rev: string; tdoc: string; meeting: string; title: string; version: string | null;
  summary: string; background: string; changes: { clause: string; what: string }[]; decision: string; impact: string;
  confidence: string; model?: string; generated?: string;
  evidence?: {
    changes: { clause: string; lines: string[] }[]; drafts: { tdoc: string; title: string }[];
    agreements: { meeting?: string; agenda?: string; kind?: string; text: string; folder?: string }[];
    fl_summaries: { tdoc: string; title: string; meeting: string; url: string; excerpt: string }[];
  };
}

/** One text a paragraph had (Library.block_lineage), oldest first. */
export interface LineageStep {
  version: string; change: string; clause_key: string; clause: string; raw_hash: string; html: string; diff: string;
  text: string; crs: { cr: string; rev: string; tdoc: string; subject: string; cat: string; meeting: string }[];
  annotation: Annotation | null;
}

export const crKey = (cr: string, tdoc: string) => `${cr}|${tdoc}`;

export interface Note { clause_key: string; bookmark: number; text: string; version: string | null; updated: string }

export interface PhraseClause { key: string; number: string; title: string; ord?: number; block?: number; snippet?: string; count?: number }
export interface PhraseHistory {
  query: string; first: string | null; latest_clauses: number; truncated: boolean;
  versions: { version: string; paragraphs: number; clauses: number }[];
  events: { version: string; added: PhraseClause[]; removed: PhraseClause[]; more: number }[];
}
export interface WICR {
  spec: string; cr: string; rev: string; tdoc: string; meeting: string; date: string; new_version: string;
  lib_version: string | null; cat: string; title: string; release: string; clauses: string[]; clauses_raw: string;
  reason: string; summary: string; analysis: { summary: string; background: string; decision: string; impact: string } | null;
}
export interface WISummary {
  overview: string; motivation: string; spec_changes: { spec: string; clauses: string; what: string }[];
  timeline: { when: string; what: string }[]; impact: string; open_points: string; confidence: string;
  model?: string; generated?: string; crs: number; analysed: number;
}
export interface WorkItem { wi: string; crs: WICR[]; summary: WISummary | null }
export interface SearchHit { ord: number; key: string; number: string; title: string; block: number | null; snippet: string }
export interface LlmProviderSettings {
  base_url: string; model: string; has_key: boolean; key_hint: string; key_source: "saved" | "env" | null;
}
export interface LlmSettings extends LlmProviderSettings {
  provider: string; providers: Record<string, LlmProviderSettings>;
  presets: Record<string, { base_url: string; model: string }>;
}
export interface LlmForm { provider?: string; base_url?: string; model?: string; api_key?: string; clear_key?: boolean }
export interface IntentRequest {
  spec: string; version: string; clause_key?: string; limit?: number | null; skip_uncovered?: boolean; force?: boolean;
  targets?: { version: string; clause_key: string; raw_hash: string }[];
}

export interface MeetingDoc { meeting: string; tdoc: string | null; title: string; html: string; url: string | null }

export interface Job { id: string; state: "running" | "done" | "error"; done: number; total: number; message: string; specs: string[]; kind?: "intent" }

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

/** JSON request whose error is the backend's human-readable `detail`. */
async function send<T>(method: string, path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    const text = await r.text();
    let detail = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
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
  phraseHistory: (spec: string, q: string) => get<PhraseHistory>(`/api/specs/${s(spec)}/phrase-history?q=${s(q)}`),
  crs: (spec: string) => get<CR[]>(`/api/specs/${s(spec)}/crs`),
  workItem: (wi: string) => get<WorkItem>(`/api/work-items/${s(wi)}`),
  summarizeWI: (wi: string, force = false) => send<Job>("POST", "/api/wi-summary", { wi, force }),
  compare: (spec: string, oldV: string, newV: string, changedOnly = true) =>
    get<CompareResult>(`/api/specs/${s(spec)}/compare?old=${s(oldV)}&new=${s(newV)}&changed_only=${changedOnly}`),
  stats: (spec: string) => get<Stats>(`/api/specs/${s(spec)}/stats`),
  jobs: () => get<Job[]>("/api/jobs"),
  catalog: (series: string, refresh = false) => get<CatalogSpec[]>(`/api/catalog/${s(series)}${refresh ? "?refresh=true" : ""}`),
  notes: (spec: string) => get<Note[]>(`/api/specs/${s(spec)}/notes`),
  saveNote: async (spec: string, key: string, body: { bookmark?: boolean; text?: string; version?: string }) => {
    const r = await fetch(`/api/specs/${s(spec)}/notes/${s(key)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    return r.json() as Promise<Note | null>;
  },
  annotations: (spec: string, v: string, key: string) =>
    get<Annotation[]>(`/api/specs/${s(spec)}/${s(v)}/clause/${s(key)}/annotations`),
  settings: () => get<{ annotation_url: string }>("/api/settings"),
  saveSettings: async (body: { annotation_url: string }) => {
    const r = await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return r.json() as Promise<{ annotation_url: string }>;
  },
  syncAnnotations: async (spec: string): Promise<{ packs: number }> => {
    const r = await fetch(`/api/annotations/sync/${s(spec)}`, { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
    return r.json();
  },
  llm: () => get<LlmSettings>("/api/llm"),
  saveLlm: (form: LlmForm) => send<LlmSettings>("PUT", "/api/llm", form),
  testLlm: (form: LlmForm) => send<{ model: string; reply: string; seconds: number }>("POST", "/api/llm/test", form),
  llmModels: (form: LlmForm) => send<{ models: string[] }>("POST", "/api/llm/models", form),
  makeIntents: (req: IntentRequest) => send<Job>("POST", "/api/intent", req),
  lineage: (spec: string, v: string, key: string, hash: string) =>
    get<LineageStep[]>(`/api/specs/${s(spec)}/${s(v)}/clause/${s(key)}/lineage?hash=${s(hash)}`),
  crAnalyses: (spec: string) => get<CRAnalysis[]>(`/api/specs/${s(spec)}/cr-analyses`),
  analyzeCR: (spec: string, cr: string, tdoc: string, force = false) =>
    send<Job>("POST", "/api/cr-analysis", { spec, cr, tdoc, force }),
  meetingDoc: (folder: string, tdoc?: string) =>
    get<MeetingDoc>(`/api/meeting-doc?folder=${s(folder)}${tdoc ? `&tdoc=${s(tdoc)}` : ""}`),
  crDoc: (spec: string, cr: string, tdoc: string) =>
    get<MeetingDoc>(`/api/cr-doc?spec=${s(spec)}&cr=${s(cr)}&tdoc=${s(tdoc)}`),
  featuresStatus: () => get<{ count: number; source: string | null }>("/api/features"),
  searchFeatures: (q: string) => get<UEFeature[]>(`/api/features/search?q=${s(q)}`),
  featureLinks: (section: string, index: string) => get<FeatureLinks>(`/api/features/${s(section)}/${s(index)}/links`),
  clauseFeatures: (spec: string, v: string, key: string) =>
    get<ClauseFeatures>(`/api/specs/${s(spec)}/${s(v)}/clause/${s(key)}/features`),
  fetchFeatures: async (): Promise<Job> => {
    const r = await fetch("/api/fetch-features", { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  fetchCrs: async (spec: string): Promise<Job> => {
    const r = await fetch("/api/fetch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ specs: [spec], crs_only: true }),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
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
