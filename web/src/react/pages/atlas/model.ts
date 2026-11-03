
/* =====================================================================
   ATLAS MODEL (React): the 'atlas' read tool's payload, normalised once.
   Pure: no DOM, no store, no fetch. Ported from pages/atlas-model.js so the
   React Atlas and the contract stay identical: a missing field is handled here,
   in exactly one place, and never becomes NaN or a false claim.
   ===================================================================== */
export type Json = Record<string, unknown>;

export const PROOF = ['proven', 'stale', 'failed', 'unrun', 'attention'] as const;
export const WORK = ['done', 'in-progress', 'blocked', 'todo'] as const;
export type ProofClass = (typeof PROOF)[number];
export type WorkKey = (typeof WORK)[number];
const PROGRAM_STATUS = ['done', 'active', 'blocked', 'planned', 'later'];
const PRIORITY = ['must', 'should', 'could', 'wont'];

/* ---------- value guards ---------- */
export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v.filter((x) => x != null) : []);
export const rec = (v: unknown): Json => (v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Json) : {});
export const str = (v: unknown): string =>
  typeof v === 'string' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : '';
export const strs = (v: unknown): string[] => arr(v).map(str).filter(Boolean);
export const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
export const cnt = (v: unknown): number => {
  const n = num(v);
  return n != null && n > 0 ? Math.round(n) : 0;
};
export const frac = (v: unknown): number | null => {
  const n = num(v);
  return n != null && n >= 0 && n <= 1 ? n : null;
};
export const pc = (v: unknown): number | null => {
  const f = frac(v);
  return f == null ? null : Math.round(f * 100);
};
export const sumv = (o: unknown): number => Object.values(rec(o)).reduce<number>((a, b) => a + cnt(b), 0);
const counts = (o: unknown, keys: readonly string[]): Record<string, number> =>
  Object.fromEntries(keys.map((k) => [k, cnt(rec(o)[k])]));
const has = (list: readonly string[], v: unknown): boolean => typeof v === 'string' && list.includes(v);

/* ---------- dates: observed days, 'YYYY-MM-DD' ---------- */
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function dayOk(v: unknown): string {
  if (typeof v !== 'string') return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (!m) return '';
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return '';
  return Number.isFinite(Date.parse(m[0] + 'T00:00:00Z')) ? m[0] : '';
}
export const dayNum = (iso: string): number => Date.parse(iso + 'T00:00:00Z') / 864e5;
export function fmtDay(v: unknown): string {
  const iso = dayOk(v);
  if (!iso) return '';
  const [, m, d] = iso.split('-').map(Number);
  return d + ' ' + MON[m - 1];
}
export function fmtRange(a: unknown, b: unknown): string {
  const x = dayOk(a);
  const y = dayOk(b);
  if (!x) return '';
  if (!y || x === y) return fmtDay(x);
  const [, m1, d1] = x.split('-').map(Number);
  const [, m2, d2] = y.split('-').map(Number);
  return m1 === m2 ? d1 + '–' + d2 + ' ' + MON[m1 - 1] : fmtDay(x) + ' – ' + fmtDay(y);
}
export function daysBetween(from: unknown, to: unknown): number | null {
  const a = dayOk(from);
  const b = dayOk(to);
  return a && b ? Math.max(0, Math.round(dayNum(b) - dayNum(a))) : null;
}
export const pcLabel = (v: unknown, unknown = 'unknown'): string => {
  const p = pc(v);
  return p == null ? unknown : p + '%';
};
export function shortName(label: unknown, max = 16): string {
  const s = str(label).trim();
  if (s.length <= max) return s;
  const cut = s.slice(0, max).replace(/\s+\S*$/, '');
  return (cut.length >= 4 ? cut : s.slice(0, max)) + '…';
}
export const shortId = (id: unknown): string => str(id).replace(/^SC-/i, '').slice(0, 4);

/* ---------- records ---------- */
export interface Program {
  id: string; label: string; blurb: string; status: string; phases: string[];
  tasks: Record<string, number>; n: number; phases_n: number; phases_done: number;
  proof: Record<string, number>; items: string[]; start: string; end: string;
  vis: string; lastTouch: string; index: number;
}
function program(p: unknown, i: number): Program {
  const q = rec(p);
  const id = str(q.id) || 'program-' + (i + 1);
  const tasks = counts(q.tasks, WORK);
  const phases = strs(q.phases);
  return {
    id,
    label: str(q.label) || id,
    blurb: str(q.blurb),
    status: has(PROGRAM_STATUS, q.status) ? String(q.status) : 'planned',
    phases,
    tasks,
    n: cnt(q.n) || sumv(tasks),
    phases_n: cnt(q.phases_n) || phases.length,
    phases_done: cnt(q.phases_done),
    proof: counts(q.proof, PROOF),
    items: strs(q.items),
    start: dayOk(q.start),
    end: dayOk(q.end),
    vis: 'next',
    lastTouch: '',
    index: i,
  };
}
export interface Phase {
  id: string; title: string; recorded: string; recorded_progress: number | null;
  derived: string; derived_pct: number | null; tasks: Record<string, number>; n: number;
  program: string; created: string; order: number; mismatch: boolean;
}
function phase(p: unknown): Phase {
  const q = rec(p);
  return {
    id: str(q.id),
    title: str(q.title) || str(q.id),
    recorded: str(q.recorded) || 'unknown',
    recorded_progress: num(q.recorded_progress),
    derived: str(q.derived) || 'unknown',
    derived_pct: num(q.derived_pct),
    tasks: counts(q.tasks, WORK),
    n: cnt(q.n),
    program: str(q.program),
    created: dayOk(q.created),
    order: num(q.order) ?? 0,
    mismatch: q.mismatch === true,
  };
}
export interface Task {
  id: string; title: string; status: string; phase: string; program: string;
  implements: string[]; created: string; updated: string; claim: string;
}
function task(t: unknown): Task {
  const q = rec(t);
  return {
    id: str(q.id),
    title: str(q.title) || str(q.id),
    status: str(q.status) || 'unknown',
    phase: str(q.phase),
    program: str(q.program),
    implements: strs(q.implements),
    created: dayOk(q.created),
    updated: dayOk(q.updated),
    claim: str(q.claim),
  };
}
export interface Check { text: string; mode: string; cls: ProofClass; detail: string; checked: boolean }
function check(c: unknown): Check {
  const q = rec(c);
  return {
    text: str(q.text) || 'check',
    mode: str(q.mode),
    cls: has(PROOF, q.cls) ? (q.cls as ProofClass) : 'unrun',
    detail: str(q.detail),
    checked: q.checked === true,
  };
}
export interface ScopeItem {
  id: string; title: string; priority: string; claim: string; proven: number | null;
  score: number | null; goals: string[]; program: string; programs: string[];
  checks: Check[]; acceptance: string; phases: string[]; tasks: Record<string, number>;
}
function scopeItem(s: unknown): ScopeItem {
  const q = rec(s);
  return {
    id: str(q.id),
    title: str(q.title) || str(q.id),
    priority: has(PRIORITY, q.priority) ? String(q.priority) : 'other',
    claim: str(q.claim),
    proven: frac(q.proven),
    score: frac(q.score),
    goals: strs(q.goals),
    program: str(q.program),
    programs: strs(q.programs),
    checks: arr(q.checks).map(check),
    acceptance: str(q.acceptance),
    phases: strs(q.phases),
    tasks: Object.fromEntries(Object.entries(rec(q.tasks)).map(([k, v]) => [k, cnt(v)])),
  };
}
export interface Goal {
  id: string; label: string; text: string; items: string[];
  exec: { total: number; done: number; in_progress: number; blocked: number; todo: number };
  proof: { proven: number | null; checked: number | null; available: boolean };
  gaps: string[];
}
function goal(g: unknown): Goal {
  const q = rec(g);
  const e = rec(q.exec);
  const p = rec(q.proof);
  return {
    id: str(q.id),
    label: str(q.label) || str(q.text) || str(q.id),
    text: str(q.text),
    items: strs(q.items),
    exec: { total: cnt(e.total), done: cnt(e.done), in_progress: cnt(e.in_progress), blocked: cnt(e.blocked), todo: cnt(e.todo) },
    proof: { proven: frac(p.proven), checked: frac(p.checked), available: p.available !== false },
    gaps: arr(q.gaps)
      .map((x) => (typeof x === 'string' ? x : str(rec(x).why) || str(rec(x).kind) || str(rec(x).id)))
      .filter(Boolean),
  };
}
export interface Entry { id: string; title: string; status: string; severity: string; created: string }
const entry = (x: unknown): Entry => {
  const q = rec(x);
  return { id: str(q.id), title: str(q.title) || str(q.id), status: str(q.status), severity: str(q.severity), created: dayOk(q.created) };
};
export interface Loops {
  proposed: Entry[]; risks: Entry[]; questions: Entry[]; blocked: Entry[];
  escalate: { kind: string; id: string; title: string; command: string }[];
  claims: { task: string; actor: string; expired: boolean }[];
}
function loops(l: unknown): Loops {
  const q = rec(l);
  return {
    proposed: arr(q.proposed).map(entry),
    risks: arr(q.risks).map(entry),
    questions: arr(q.questions).map(entry),
    blocked: arr(q.blocked).map(entry),
    escalate: arr(q.escalate).map((e) => ({
      kind: str(rec(e).kind),
      id: str(rec(e).id),
      title: str(rec(e).title) || str(rec(e).id),
      command: str(rec(e).command),
    })),
    claims: arr(q.claims).map((c) => ({ task: str(rec(c).task), actor: str(rec(c).actor), expired: rec(c).expired === true })),
  };
}
export interface Proof {
  classes: Record<string, number>;
  stale: { files: number; since: string; example: string; composition: [string, number][] };
  must_total: number; must_proven_now: number; must_if_stale_repass: number;
  official: { final_score: number | null; checked_score: number | null; gap: number | null; wont_held: number | null; wont_total: number | null };
}
function proof(pr: unknown, scope: ScopeItem[]): Proof {
  const q = rec(pr);
  const st = rec(q.stale);
  const o = rec(q.official);
  const musts = scope.filter((s) => s.priority === 'must');
  const proven = (s: ScopeItem) => s.checks.length > 0 && s.checks.every((c) => c.cls === 'proven');
  const ceil = (s: ScopeItem) => s.checks.length > 0 && s.checks.every((c) => c.cls === 'proven' || c.cls === 'stale');
  return {
    classes: counts(q.classes, PROOF),
    stale: {
      files: cnt(st.files),
      since: str(st.since),
      example: str(st.example),
      composition: Object.entries(rec(st.composition))
        .map(([k, v]) => [k, cnt(v)] as [string, number])
        .filter(([, v]) => v > 0)
        .sort((a, b) => b[1] - a[1]),
    },
    must_total: num(q.must_total) != null ? cnt(q.must_total) : musts.length,
    must_proven_now: num(q.must_proven_now) != null ? cnt(q.must_proven_now) : musts.filter(proven).length,
    must_if_stale_repass: num(q.must_if_stale_repass) != null ? cnt(q.must_if_stale_repass) : musts.filter(ceil).length,
    official: {
      final_score: frac(o.final_score),
      checked_score: frac(o.checked_score),
      gap: frac(o.gap),
      wont_held: num(o.wont_held) != null ? cnt(o.wont_held) : null,
      wont_total: num(o.wont_total) != null ? cnt(o.wont_total) : null,
    },
  };
}

export const fullyProven = (s: ScopeItem): boolean => s.checks.length > 0 && s.checks.every((c) => c.cls === 'proven');
export const provableAfterRerun = (s: ScopeItem): boolean =>
  s.checks.length > 0 && s.checks.every((c) => c.cls === 'proven' || c.cls === 'stale');
export const provenOf = (s: ScopeItem): number => s.checks.filter((c) => c.cls === 'proven').length;

export interface Model {
  asOf: string; asOfDay: string; schema: string;
  project: { name: string; state: string; activePhase: string; lastStatus: string; vision: string; nonGoals: string };
  programs: Program[]; P: Map<string, Program>;
  phases: Phase[]; PH: Map<string, Phase>;
  edges: { src: string; dst: string; kind: string; why: string }[];
  tasks: Task[]; TASK: Map<string, Task>;
  scope: ScopeItem[]; SC: Map<string, ScopeItem>;
  goals: Goal[]; unlinked: string[]; proof: Proof;
  pace: { date: string; events: number; done: number }[];
  milestones: { date: string; id: string; title: string }[];
  loops: Loops;
  inflight: Task[]; landed: Task[];
  queue: { id: string; title: string; why: string; kind: string; command: string }[];
  queueMore: number; nextState: string;
  doctor: { total: number | null; error: number; warning: number; info: number };
  plans: Record<string, number>;
  eventKinds: Record<string, number>;
  decisionStates: Record<string, number>;
  counts: {
    tasks: number; phases: number; scope: number; decisions: number | null; milestones: number;
    risks: number | null; questions: number | null; events: number | null; taskStatus: Record<string, number>;
  };
  empty: boolean;
}

const cache = new WeakMap<object, Model>();

export function modelOf(raw: unknown): Model {
  if (raw && typeof raw === 'object') {
    const key = raw as object;
    if (!cache.has(key)) cache.set(key, buildModel(raw));
    return cache.get(key) as Model;
  }
  return buildModel(raw);
}

export function buildModel(raw: unknown): Model {
  const r0 = rec(raw);
  const d = !Array.isArray(r0.programs) && Array.isArray(rec(r0.data).programs) ? rec(r0.data) : r0;
  const programs = arr(d.programs).map(program);
  const phases = arr(d.phases).map(phase).filter((p) => p.id);
  const tasks = arr(d.tasks).map(task).filter((t) => t.id);
  const scope = arr(d.scope).map(scopeItem).filter((s) => s.id);
  const edges = arr(d.edges)
    .map((e) => ({ src: str(rec(e).src), dst: str(rec(e).dst), kind: rec(e).kind === 'authored' ? 'authored' : 'ordered', why: str(rec(e).why) }))
    .filter((e) => e.src && e.dst && e.src !== e.dst);
  const c = rec(d.counts);
  const taskStatus = rec(c.taskStatus);
  const tally: Record<string, number> = {};
  for (const t of tasks) tally[t.status] = (tally[t.status] || 0) + 1;
  const ts = Object.keys(taskStatus).length
    ? Object.fromEntries(Object.entries(taskStatus).map(([k, v]) => [k, cnt(v)]))
    : tally;
  const counts_: Model['counts'] = {
    tasks: cnt(c.tasks) || tasks.length,
    phases: cnt(c.phases) || phases.length,
    scope: cnt(c.scope) || scope.length,
    decisions: num(c.decisions) != null ? cnt(c.decisions) : null,
    milestones: cnt(c.milestones),
    risks: num(c.risks) != null ? cnt(c.risks) : null,
    questions: num(c.questions) != null ? cnt(c.questions) : null,
    events: num(c.events) != null ? cnt(c.events) : null,
    taskStatus: ts,
  };
  const P = new Map(programs.map((p) => [p.id, p]));
  const PH = new Map(phases.map((p) => [p.id, p]));
  const SC = new Map(scope.map((s) => [s.id, s]));
  const TASK = new Map(tasks.map((t) => [t.id, t]));

  let most: Program | null = null;
  for (const p of programs)
    if (p.status === 'active' && p.tasks['in-progress'] > 0 && (!most || p.tasks['in-progress'] > most.tasks['in-progress'])) most = p;
  for (const p of programs) {
    p.vis =
      p.status === 'done' ? 'done'
      : p.status === 'blocked' ? 'blocked'
      : p.status === 'later' ? 'later'
      : p.n && p.tasks.done / p.n >= 0.95 ? 'built'
      : most && p.id === most.id ? 'now'
      : 'next';
    p.lastTouch = tasks.filter((t) => t.program === p.id && t.updated).reduce((m, t) => (t.updated > m ? t.updated : m), '');
  }

  const M: Model = {
    asOf: str(d.asOf),
    asOfDay: dayOk(d.asOf),
    schema: str(d.schema),
    project: (() => {
      const p = rec(d.project);
      return {
        name: str(p.name),
        state: str(p.state),
        activePhase: str(p.activePhase),
        lastStatus: str(p.lastStatus),
        vision: str(p.vision),
        nonGoals: str(p.nonGoals),
      };
    })(),
    programs, P,
    phases, PH,
    edges,
    tasks, TASK,
    scope, SC,
    goals: arr(d.goals).map(goal),
    unlinked: strs(d.unlinked),
    proof: proof(d.proof, scope),
    pace: arr(d.pace)
      .map((p) => ({ date: dayOk(rec(p).date), events: cnt(rec(p).events), done: cnt(rec(p).done) }))
      .filter((p) => p.date)
      .sort((a, b) => (a.date < b.date ? -1 : 1)),
    milestones: arr(d.milestones)
      .map((m) => ({ date: dayOk(rec(m).date), id: str(rec(m).id), title: str(rec(m).title) || str(rec(m).id) }))
      .filter((m) => m.date),
    loops: loops(d.loops),
    inflight: arr(d.inflight).map(task).filter((t) => t.id),
    landed: arr(d.landed).map(task).filter((t) => t.id),
    queue: arr(d.queue).map((q) => ({
      id: str(rec(q).id),
      title: str(rec(q).title) || str(rec(q).id),
      why: str(rec(q).why),
      kind: str(rec(q).kind),
      command: str(rec(q).command),
    })),
    queueMore: cnt(d.queueMore),
    nextState: str(d.nextState),
    doctor: (() => {
      const q = rec(d.doctor);
      const b = rec(q.by_severity);
      return { total: num(q.total) != null ? cnt(q.total) : null, error: cnt(b.error), warning: cnt(b.warning), info: cnt(b.info) };
    })(),
    plans: counts(d.plans, ['reconciled', 'dropped', 'ready']),
    eventKinds: Object.fromEntries(Object.entries(rec(d.eventKinds)).map(([k, v]) => [k, cnt(v)])),
    decisionStates: counts(d.decisionStates, ['active', 'proposed', 'closed', 'superseded']),
    counts: counts_,
    empty: false,
  };
  M.empty = !programs.length && !phases.length && !scope.length && !tasks.length;
  return M;
}

export const programTone = (p: Program | null | undefined): number => (p ? p.index % 8 : 8);