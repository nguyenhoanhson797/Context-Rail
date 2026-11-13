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
  /* The diagnostics read is separate from the snapshot: the dashboard shows
     its dated state without blocking the rest of the page on the doctor. */
  health?: Health;
  healthError?: string;
}
export interface PanelProps { title: string; description?: string; children: ReactNode; action?: ReactNode; }