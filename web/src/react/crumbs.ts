/* =====================================================================
   THE PARENT TRAIL (breadcrumbs): where the reader is in the drill-down.

   The Overview tells the project top down - the vision, the outcome, the
   scope item, its acceptance and proof, the task - and every level of that
   drill-down has to keep its parent visible. The top bar named the current
   page and nothing else, so a reader two levels down in the matrix or the
   lens could only get back up with the browser's Back button.

   The trail is derived from the address plus the read model, never from a
   navigation stack: a deep link, a reload and Back/Forward all show the
   same trail. A parent is named only when the store states it - a scope
   item that two outcomes share has no single parent, so the trail names the
   item and stops there instead of guessing which outcome it belongs to.

   Pure: no DOM, no fetch, no store. React escapes every value it renders.
   ===================================================================== */
import { object, rows, text, type Route } from './contracts';

/** The routes the drill-down chain covers. Every other route keeps the single
    crumb it always had: the page's own name. */
export const TRAIL_ROUTES = ['overview', 'outcomes', 'traceability', 'lens'] as const;

export interface Crumb {
  /** Stable identity for the row; never shown. */
  key: string;
  label: string;
  /** The parent's own state and source, kept in the title so the trail carries
      the parent context without widening the top bar. */
  hint?: string;
  route: Route;
  params: Record<string, string>;
  /** The current page: named, never a link. */
  current: boolean;
}

export interface CrumbGoal {
  id: string;
  text: string;
  /** The scope items this outcome names. */
  items: string[];
  /** The tasks this outcome names. */
  tasks: string[];
  /** Proof and work, as the model states them. */
  hint: string;
}

export interface CrumbModel {
  goals: CrumbGoal[];
  /** Scope item id -> title. */
  items: Record<string, string>;
  /** Task id -> title. */
  tasks: Record<string, string>;
}

/* ---------- the numbers, with the same honesty as the screens ---------- */
const ratio = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
/* A fraction below 1 never reads as 100%: 0.999 keeps its decimal (99.9%),
   because 100% is a claim that nothing is left. */
const pct = (value: unknown): number | null => {
  const r = ratio(value);
  if (r === null) return null;
  if (r >= 1) return 100;
  const n = Math.round(r * 1000) / 10;
  return n >= 100 ? 99.9 : n;
};
/* A long outcome text is clipped for the top bar; the full text stays in the
   title, so nothing is lost, only shortened. */
export const clip = (value: string, max = 48): string =>
  value.length <= max ? value : value.slice(0, max).replace(/\s+\S*$/, '') + '…';

/**
 * The trail's model: the outcomes, the scope items and the tasks, read from
 * the understanding read model and the snapshot. Nothing is invented here -
 * a name the store does not carry is simply absent.
 */
export function crumbModel(understanding: unknown, snapshot: unknown): CrumbModel {
  const data = object(understanding);
  const snap = object(snapshot);
  const goals: CrumbGoal[] = [];
  for (const goal of rows(data.goals)) {
    const id = text(goal.id);
    if (!id) continue;
    const proof = object(goal.proof);
    const exec = object(goal.execution);
    const proven = pct(proof.proven);
    const total = typeof exec.total === 'number' && Number.isFinite(exec.total) ? exec.total : null;
    const done = typeof exec.done === 'number' && Number.isFinite(exec.done) ? exec.done : null;
    const state = proven === null ? 'proof not assessed' : `proven ${proven}%`;
    const work = total === null || done === null ? 'work not assessed' : `work ${done}/${total} done`;
    goals.push({
      id,
      text: text(goal.text, id),
      items: rows(goal.items).map((item) => text(item.id)).filter(Boolean),
      tasks: rows(goal.tasks).map((task) => text(task.id)).filter(Boolean),
      hint: `${state} · ${work}`,
    });
  }
  const items: Record<string, string> = {};
  for (const item of rows(snap.scope_items)) {
    const id = text(item.id);
    if (id) items[id] = text(item.title, id);
  }
  for (const item of rows(object(data.unmapped).items)) {
    const id = text(item.id);
    if (id && !items[id]) items[id] = text(item.title, id);
  }
  for (const goal of rows(data.goals)) {
    for (const item of rows(goal.items)) {
      const id = text(item.id);
      if (id && !items[id]) items[id] = text(item.title, id);
    }
  }
  const tasks: Record<string, string> = {};
  for (const task of rows(snap.tasks)) {
    const id = text(task.id);
    if (id) tasks[id] = text(task.title, id);
  }
  for (const goal of rows(data.goals)) {
    for (const task of rows(goal.tasks)) {
      const id = text(task.id);
      if (id && !tasks[id]) tasks[id] = text(task.title, id);
    }
  }
  return { goals, items, tasks };
}

/* ---------- the trail ---------- */
/** The outcomes that name this entity. More than one means there is no single
    parent, and the trail says so instead of picking one. */
const ownersOf = (model: CrumbModel, names: (goal: CrumbGoal) => boolean): CrumbGoal[] => model.goals.filter(names);

const goalCrumb = (goal: CrumbGoal): Crumb => ({
  key: `goal:${goal.id}`,
  label: clip(goal.text),
  hint: goal.hint,
  route: 'outcomes',
  params: { goal: goal.id },
  current: false,
});

/** The parent's own place in the trail: one outcome, or none - and the count
    when several name the same entity, so the reader knows the parent is
    shared rather than missing. */
function parentCrumb(owners: CrumbGoal[]): Crumb[] {
  return owners.length === 1 ? [goalCrumb(owners[0])] : [];
}

const sharedHint = (title: string | undefined, owners: number): string =>
  owners > 1 ? `${title ?? ''}${title ? ' · ' : ''}named by ${owners} outcomes` : title ?? '';

/**
 * The trail of the current address.
 *
 * An empty trail means "this route is not part of the chain": the caller
 * then keeps the single crumb the route always had.
 */
export function trailOf(route: string, params: URLSearchParams, model: CrumbModel): Crumb[] {
  const root = (): Crumb => ({
    key: 'overview',
    label: 'Overview',
    route: 'overview',
    params: {},
    current: route === 'overview',
  });
  if (!(TRAIL_ROUTES as readonly string[]).includes(route)) return [];
  if (route === 'overview') return [root()];
  if (route === 'outcomes') {
    const id = params.get('goal') || '';
    if (!id) {
      return [root(), { key: 'outcomes', label: 'Outcomes', route: 'outcomes', params: {}, current: true }];
    }
    const goal = model.goals.find((entry) => entry.id === id);
    return [
      root(),
      {
        key: `goal:${id}`,
        label: goal ? clip(goal.text) : id,
        hint: goal?.hint,
        route: 'outcomes',
        params: { goal: id },
        current: true,
      },
    ];
  }
  if (route === 'traceability') {
    const item = params.get('item') || '';
    if (!item) {
      return [root(), { key: 'traceability', label: 'Traceability', route: 'traceability', params: {}, current: true }];
    }
    const owners = ownersOf(model, (entry) => entry.items.includes(item));
    const trail: Crumb[] = [root(), ...parentCrumb(owners)];
    trail.push({
      key: `item:${item}`,
      label: item,
      hint: sharedHint(model.items[item], owners.length),
      route: 'traceability',
      params: { item },
      current: true,
    });
    return trail;
  }
  /* the task lens: the outcome the task serves is its parent */
  const task = params.get('task') || '';
  if (!task) {
    return [root(), { key: 'lens', label: 'Lens', route: 'lens', params: {}, current: true }];
  }
  const owners = ownersOf(model, (entry) => entry.tasks.includes(task));
  const trail: Crumb[] = [root(), ...parentCrumb(owners)];
  trail.push({
    key: `task:${task}`,
    label: clip(model.tasks[task] || task),
    hint: sharedHint(model.tasks[task], owners.length),
    route: 'lens',
    params: { task },
    current: true,
  });
  return trail;
}
