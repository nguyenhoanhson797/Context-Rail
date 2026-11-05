import type { ReactNode } from 'react';
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Data = Record<string, unknown>;
export const object = (v: unknown): Data => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Data : {};
export const text = (v: unknown, fallback = ''): string => typeof v === 'string' ? v : typeof v === 'number' ? String(v) : fallback;
export const rows = (v: unknown): Data[] => Array.isArray(v) ? v.filter(x => x !== null && typeof x === 'object' && !Array.isArray(x)) as Data[] : [];
export interface RailDoc extends Data { id: string; title: string; type: string; path: string; }
export interface Snapshot extends Data {
  project: Data; tasks: RailDoc[]; phases: RailDoc[]; scope_items: RailDoc[];
  decisions: RailDoc[]; risks: RailDoc[]; questions: RailDoc[]; core_docs: RailDoc[]; statuses: string[];
  records?: RailDoc[]; operating?: Data; digests?: Data;
}
/* The diagnostics document (/api/health): explicit loading/unknown/error - a
   clean board is ``ok`` with a dated summary, never a missing value read as 0. */
export interface Health extends Data {
  schema: string;
  state: 'ok' | 'unknown' | 'error';
  as_of: string | null;
  doctor: Data | null;
  cache: Data | null;
  error: string | null;
  error_code: string | null;
  reasons: string[];
}
export type Route = 'summary' | 'overview' | 'atlas' | 'outcomes' | 'traceability' | 'impact' | 'now' | 'board' | 'lens' | 'graph' | 'roadmap' | 'scope' | 'catalog' | 'decisions' | 'rules' | 'doctor' | 'pack' | 'activity' | 'portfolio' | 'settings' | 'timeline' | 'workflow' | 'execution';
/* The statuses that mean work is happening right now. A reader looking for live
   work puts these first, in the palette and in every picker. */
export const ACTIVE_STATUSES = new Set(['in-progress', 'blocked']);
export interface PageProps {
  snapshot: Snapshot;
  project: string;
  params: URLSearchParams;
  navigate: (route: Route, params?: Record<string, string>) => void;
  openDoc: (id: string) => void;
  read: (tool: string, args?: Record<string, string>, signal?: AbortSignal) => Promise<Data>;
  mutate: (tool: string, args: Record<string, unknown>, confirm?: boolean) => Promise<Data>;
  busy: boolean;
  /* The one refresh (web/src/react/refresh.ts): the whole project, not only
     the read this screen owns. A screen that renders it without a refresh
     falls back to refetching its own read. */
  refresh?: () => Promise<void>;
  request: (path: '/api/portfolio' | '/api/projects' | '/api/tools', init?: RequestInit) => Promise<Data>;
  theme: 'light' | 'dark' | 'system';
  setTheme: (theme: 'light' | 'dark' | 'system') => void;
}
/* --- UI-02: source-backed intelligence read contracts (GET /api/read/<view>) ---
   One versioned envelope for every view. Missing data is null with a reason and
   an explicit state - never 0, "ok" or a failure. The browser renders these
   documents; it never reads event/receipt files or JSON receipt internals. */
export const INTEL_CONTRACT = 'ui-intel-v1';
export type IntelStatus = 'ok' | 'unknown' | 'error' | 'refused';
export type IntelView = 'ui_rules' | 'ui_corrections' | 'ui_correction_detail'
  | 'ui_correction_history' | 'ui_task_episode' | 'ui_execution_metrics'
  | 'ui_adapter_diagnostics';
export interface IntelBudget extends Data { max_bytes: number; used_bytes: number; }
export interface IntelEnvelope<T extends Data = Data> extends Data {
  contract: string; view: IntelView; status: IntelStatus; as_of: string | null;
  schema: number | null; reasons: string[]; budget: IntelBudget; data: T;
}
export interface IntelConvention extends Data {
  key: string; title: string; present: boolean; authority: 'governing';
  text: string | null; truncated: boolean; source: string | null;
}
export interface IntelRail extends Data {
  id: string; source: string | null; state: string; command: string | null;
  error: string | null; declared: boolean;
}
export interface IntelRulesData extends Data {
  conventions: IntelConvention[]; rails: IntelRail[]; rails_enforced: boolean;
  rail_revision: string; missing_conventions: string[]; authority: 'governing'; note: string;
}
export interface IntelIdentity extends Data {
  id: string; digest: string | null; actor: string | null; via: string | null;
  at: string | null; source: string | null; event: string | null; order: [number, string];
}
export interface IntelCorrection extends Data {
  identity: IntelIdentity; kind: string; title: string; text: string; status: string;
  task: string | null; files: string[]; convention: string | null; proposes: string | null;
  rail: Data | null; ambiguous: boolean; in_prompt_window: boolean; adoption: Data;
}
export interface IntelWindow extends Data {
  limit: number; undecided: number; in_window: number; omitted: number; note: string;
}
export interface IntelCorrectionsData extends Data {
  authority: 'proposal'; entries: IntelCorrection[]; count: number; total: number;
  matched: number; truncated: boolean; next_cursor: string | null; filters: Data;
  counts: Data; window: IntelWindow; ambiguous: Data[]; note: string;
}
export interface IntelCorrectionDetailData extends IntelCorrection {
  requested: string; history: IntelHistoryData;
}
export interface IntelTransition extends Data {
  correction: string; event: string; kind: string; from: string | null; to: string;
  actor: string | null; at: string | null; via: string | null; source: 'log';
}
export interface IntelHistoryData extends Data {
  known: boolean; entries: IntelTransition[]; count: number; total: number;
  truncated: boolean; filter: Data; current: Data[]; current_total: number;
  ambiguous: Data[]; reason: string | null;
}
export interface IntelCapture extends Data {
  event?: string; at?: string; actor?: string | null; captured: boolean | null;
  attribution: string | null; reason_code: string | null; late?: boolean;
  observed_files: number | null; recorded_files: number | null; recorded_commits: number | null;
  ambiguous: number | null; unverifiable: number | null; merged_in: number | null;
  overlap: string[] | null; base: string | null; head: string | null; baseline_at: string | null;
}
export interface IntelEpisode extends Data {
  id: string; actor: string | null; worktree: string | null; acquired: string | null;
  released: string | null; release_event: string | null; forced_release: boolean;
  captures: IntelCapture[]; status: Data[];
}
export interface IntelCloseout extends Data {
  state: 'unknown' | 'recorded'; complete: boolean | null;
  required_follow_ups: string[] | null; optional_follow_ups: string[] | null;
  capture_record: Data | null; event?: string | null; at?: string | null;
  actor?: string | null; reason: string | null; producer?: string;
}
export interface IntelPointers extends Data {
  declared: { files: string[]; commits: string[] }; observed: Data; provenance: string;
}
export interface IntelEpisodeData extends Data {
  task: string; title: string; path: string; status: string; updated: string | null;
  done: boolean; episode_identity: Data; episodes: IntelEpisode[]; episodes_total: number;
  episodes_truncated: boolean; status_timeline: Data[]; reopened: boolean;
  live: Data | null; pointers: IntelPointers; closeout: IntelCloseout;
  capture_unrecorded: boolean; note: string;
}
export interface IntelMetricsData extends Data {
  measured: boolean; metrics: Data | null; provenance: Data; not_measured: Data;
  reason?: string; per_task: Data | null;
}
export interface IntelGitAdapter extends Data {
  state: string; reason: string | null; receipt_only: boolean; repository: string | null;
  hook_path: string | null; hook_present: boolean | null; hook_size: number | null;
  marker_present: boolean | null; hooks_authorized: boolean | null;
  core_hooks_path: string | null; note: string;
}
export interface IntelClaudeAdapter extends Data {
  state: string; reason: string | null; config_path: string; requires_operator: boolean;
  command: string | null; hooks: string[] | null; note: string;
}
export interface IntelAdaptersData extends Data {
  git: IntelGitAdapter; claude: IntelClaudeAdapter; observations: Data; caveats: string[];
}
export type IntelRules = IntelEnvelope<IntelRulesData>;
export type IntelCorrections = IntelEnvelope<IntelCorrectionsData>;
export type IntelCorrectionDetail = IntelEnvelope<IntelCorrectionDetailData>;
export type IntelCorrectionHistory = IntelEnvelope<IntelHistoryData>;
export type IntelTaskEpisode = IntelEnvelope<IntelEpisodeData>;
export type IntelExecutionMetrics = IntelEnvelope<IntelMetricsData>;
export type IntelAdapterDiagnostics = IntelEnvelope<IntelAdaptersData>;
export interface IntelFilters { status?: string; kind?: string; task?: string; convention?: string; q?: string; limit?: number; cursor?: string; }

export interface PanelProps { title: string; description?: string; children: ReactNode; action?: ReactNode; }