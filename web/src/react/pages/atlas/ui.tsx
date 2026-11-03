
/* =====================================================================
   ATLAS UI (React): the small shapes every altitude draws with.
   Every value from the server is already a plain string or number by the time
   it gets here (model.ts vets it), so React escapes it: no innerHTML anywhere.
   Class names stay 'at-*' so the shared stylesheet reaches only the shapes it owns.
   ===================================================================== */
import { createContext, useContext, type ReactNode } from 'react';
import { PROOF, cnt, fmtDay, fmtRange, str, type Program, type ScopeItem } from './model';
import { PROOF_LABELS, PROOF_NAMES, PROOF_SHORTS } from '../evidence';

/* Where a shape leads: the Atlas owns navigation, the page passes the handlers in. */
export interface AtlasHandlers {
  open: (kind: string, id: string) => void;
  openDoc: (id: string) => void;
  lens: (id: string) => void;
  page: (route: string) => void;
  go: (alt: string, anchor: string) => void;
  copy: (text: string) => void;
}
export const AtlasContext = createContext<AtlasHandlers>({
  open: () => {},
  openDoc: () => {},
  lens: () => {},
  page: () => {},
  go: () => {},
  copy: () => {},
});
export const useAtlas = (): AtlasHandlers => useContext(AtlasContext);

export const own = <T extends object>(map: T, key: string): string =>
  Object.hasOwn(map, key) ? String((map as Record<string, unknown>)[key] ?? '') : '';

/* The words come from the one vocabulary in ../evidence - this module only
   keeps the Atlas's capitalised chip forms of them. */
export const PROOF_NAME = PROOF_NAMES;
export const PROOF_LABEL = PROOF_LABELS;
export const PROOF_SHORT = PROOF_SHORTS;
export const PRIORITY_NAME: Record<string, string> = { must: 'Must', should: 'Should', could: 'Could', wont: "Won't", other: 'Unranked' };
export const STATUS_TONE: Record<string, string> = { done: 'g', 'in-progress': 'b', blocked: 'r', todo: '', active: 'b', planned: '' };
export const VIS_LABEL: Record<string, string> = { done: 'Done', built: 'Built', blocked: 'Blocked', now: 'In motion', next: 'Not yet', later: 'Later' };
export const VIS_KIND: Record<string, string> = {
  done: 'done',
  built: 'built, not shipped',
  blocked: 'blocked',
  now: 'in motion',
  next: 'not yet started in earnest',
  later: 'later',
};

/* ---------- work (blue) and proof (green family): two truths, never merged ---------- */
export const workParts = (c: Record<string, number>): [number, string, string][] => [
  [cnt(c.done), 'at-w-done', 'Tasks done'],
  [cnt(c['in-progress']), 'at-w-prog', 'In progress'],
  [cnt(c.blocked), 'at-w-block', 'Blocked'],
  [cnt(c.todo), 'at-w-todo', 'To do'],
];
export const proofParts = (c: Record<string, number>): [number, string, string][] =>
  PROOF.map((k) => [cnt(c[k]), 'at-p-' + k, PROOF_NAME[k]] as [number, string, string]);

/* One whole decomposed into its parts. The bar is a picture, so it carries its numbers as text. */
export function Stack({ parts, cls = '' }: { parts: [number, string, string][]; cls?: string }) {
  const live = parts.filter((p) => p[0] > 0);
  const label = live.length ? live.map((p) => p[2] + ': ' + p[0]).join(', ') : 'nothing recorded';
  if (!live.length) return <span className={'at-stack at-empty ' + cls} role="img" aria-label={label} />;
  return (
    <span className={'at-stack ' + cls} role="img" aria-label={label}>
      {live.map((p, i) => (
        <i key={i} className={p[1]} style={{ flex: p[0] }} data-atip={p[2] + ': ' + p[0]} />
      ))}
    </span>
  );
}
export const LegendItem = ({ cls, text }: { cls: string; text: string }) => (
  <span>
    <i className={'at-sw ' + cls} />
    {text}
  </span>
);

/* ---------- chips ---------- */
export const Chip = ({ text, tone = '', extra }: { text: string; tone?: string; extra?: Record<string, unknown> }) => (
  <span className={'at-chip' + (tone ? ' at-t-' + tone : '')} {...extra}>
    {text}
  </span>
);

/* ---------- buttons: a shape is a button, never a bare div ---------- */
export function OpenBtn({ k, id, cls = '', label, tipText = '', children }: { k: string; id: string; cls?: string; label: string; tipText?: string; children?: ReactNode }) {
  const { open } = useAtlas();
  return (
    <button type="button" className={cls} aria-label={label} data-atip={tipText || undefined} onClick={() => open(k, id)}>
      {children}
    </button>
  );
}
export const idOk = (id: unknown): boolean =>
  typeof id === 'string' && id.length > 0 && id.length < 200 && !/[\p{Cc}"'<>&\x60\\]/u.test(id);
/* A link to one record: the app's doc drawer when the id is a safe internal id, plain text otherwise. */
export function DocLink({ id, label, cls = 'at-link' }: { id: string; label?: string; cls?: string }) {
  const { openDoc } = useAtlas();
  const text = label == null || label === '' ? id : label;
  return idOk(id) ? (
    <button type="button" className={cls} onClick={() => openDoc(id)}>
      {text}
    </button>
  ) : (
    <>{text}</>
  );
}
/* A jump to a place on the Atlas itself (an altitude, optionally a section of it). */
export function GoBtn({ alt, anchor = '', label, cls = 'at-go' }: { alt: string; anchor?: string; label: string; cls?: string }) {
  const { go } = useAtlas();
  return (
    <button type="button" className={cls} onClick={() => go(alt, anchor)}>
      {label}
    </button>
  );
}
export const shellSafe = (id: unknown): boolean => /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/.test(str(id));
export function CopyBtn({ text, label = 'copy' }: { text: string; label?: string }) {
  const { copy } = useAtlas();
  return (
    <button type="button" className="at-copy" aria-label={'Copy: ' + text} onClick={() => copy(text)}>
      {label}
    </button>
  );
}

/* ---------- dates ---------- */
export const dayOr = (v: unknown, unknown = 'date unknown'): string => fmtDay(v) || unknown;
/* When a program happened, in observed days; the roadmap commits to none. */
export function when(p: Program): string {
  if (p.vis === 'later') return 'not dated';
  if (!p.start) return p.vis === 'done' ? 'dates unknown' : 'not dated';
  if (p.vis === 'done') return fmtRange(p.start, p.end) || 'since ' + fmtDay(p.start);
  if (p.vis === 'built') return fmtRange(p.start, p.end || p.lastTouch);
  return 'since ' + fmtDay(p.start);
}
export const workText = (p: Program): string => (p.n ? p.tasks.done + '/' + p.n : p.phases_n ? p.phases_done + '/' + p.phases_n + ' ph' : '—');

/* ---------- the program marker: ring = share of tasks done, centre = state ---------- */
export function Marker({ p }: { p: Program }) {
  const R = 17;
  const C = 2 * Math.PI * R;
  const frac = p.vis === 'done' ? 1 : p.n ? p.tasks.done / p.n : 0;
  const col = p.vis === 'blocked' ? 'var(--red)' : 'var(--work)';
  return (
    <svg width="48" height="48" viewBox="-24 -24 48 48" aria-hidden="true" focusable="false">
      <circle r="22" fill="var(--surface)" />
      {p.vis === 'done' ? (
        <>
          <circle r="18" fill="var(--work)" />
          <path d="M-7 0.5l4.8 4.8L7.5-4.5" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        </>
      ) : p.vis === 'built' ? (
        <>
          <circle r={R} fill="none" stroke="var(--work)" strokeWidth="5" />
          <path d="M-6.5 0.5l4.3 4.3L6.5-4" fill="none" stroke="var(--work)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="14" cy="-14" r="5.5" fill="var(--amber)" stroke="var(--surface)" strokeWidth="2" />
        </>
      ) : p.vis === 'blocked' ? (
        <>
          <circle r={R} fill="color-mix(in srgb,var(--red) 10%,var(--surface))" stroke="var(--red)" strokeWidth="5" strokeDasharray="5 3.5" />
          <rect x="-8" y="-2.8" width="16" height="5.6" rx="2.4" fill="var(--red)" />
        </>
      ) : p.vis === 'later' ? (
        <circle r={R} fill="none" stroke="var(--border-strong)" strokeWidth="3" strokeDasharray="2 5" strokeLinecap="round" />
      ) : (
        <>
          <circle r={R} fill="none" stroke="var(--surface-3)" strokeWidth="5" />
          {frac > 0 && (
            <circle
              r={R}
              fill="none"
              stroke={col}
              strokeWidth="5"
              strokeLinecap="round"
              strokeDasharray={(C * frac).toFixed(1) + ' ' + C.toFixed(1)}
              transform="rotate(-90)"
            />
          )}
          {p.vis === 'now' && (
            <>
              <circle className="at-pulse" r="12" fill="var(--accent)" />
              <circle r="8" fill="var(--accent)" />
            </>
          )}
        </>
      )}
    </svg>
  );
}

/* ---------- a scope item as a vessel filled by its checks, proven at the bottom ---------- */
export function Tank({ s }: { s: ScopeItem }) {
  const tot = s.checks.length;
  if (!tot) return null;
  return (
    <>
      {PROOF.map((k) => ({ k, n: s.checks.filter((c) => c.cls === k).length }))
        .filter((x) => x.n)
        .map((x) => <i key={x.k} className={'at-p-' + x.k} style={{ height: ((100 * x.n) / tot).toFixed(1) + '%' }} />)}
    </>
  );
}
export const itemTip = (s: ScopeItem): string =>
  s.id + ' · ' + s.title + ' — ' + (s.checks.length ? s.checks.filter((c) => c.cls === 'proven').length + '/' + s.checks.length + ' checks proven' : 'no checks');
/* The one sentence that says whether a Must item is proven, would be after a re-run, or needs a fix. */
export function mustState(s: ScopeItem): { k: string; text: string } {
  const ok = s.checks.length > 0 && s.checks.every((c) => c.cls === 'proven');
  const ceil = s.checks.length > 0 && s.checks.every((c) => c.cls === 'proven' || c.cls === 'stale');
  return ok
    ? { k: 'ok', text: 'fully proven' }
    : ceil
      ? { k: 'ceil', text: 'would be proven if its stale checks re-pass' }
      : { k: 'no', text: 'needs a fix or a first run' };
}
export const programName = (P: Map<string, Program>, id: string): string => {
  const p = id ? P.get(id) : undefined;
  return p ? p.label : '';
};