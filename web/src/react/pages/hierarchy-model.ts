/* =====================================================================
   The owner hierarchy: Goal bullet -> sub-goal record -> scope item -> task.
   Two sources, one shape:

   - the SOURCED path renders the additive ui_hierarchy read (a server-side
     projection over the understanding read + the authored business-chain
     links): it carries the authored sub-goal title, status, revision and
     semantic digest, the scope priority/proof/plan status and the reason a
     record is outside the chain. The client derives nothing here.
   - the DERIVED path is the honest fallback when that read does not answer:
     it rebuilds the same tree from the snapshot's authored links and the
     atlas read, keeping the old framing. It says which source it used.

   Either way the rules are the same: the grouping key is the Goal's own
   bullet, never phase/milestone/sprint membership; an authored missing link
   is shown as unlinked with its reason, never guessed; historical phase
   links stay visible as labels.
   ===================================================================== */
import { object, rows, text, type Data, type Snapshot } from '../contracts';
import type { Model } from './atlas/model';

export interface OwnerTask {
  id: string;
  title: string;
  status: string;
  phases: string[];
}

export interface OwnerPlan {
  known: boolean;
  status: string | null;
  reconciled: boolean;
  source: string | null;
}

export interface OwnerScope {
  id: string;
  title: string;
  titleTruncated?: boolean;
  priority: string;
  proven: number | null;
  provenKnown?: boolean;
  phases: string[];
  subgoal: string;
  tasks: OwnerTask[];
  plan?: OwnerPlan | null;
  reason?: string;
  code?: string;
}

export interface OwnerSubgoal {
  id: string;
  title?: string;
  titleTruncated?: boolean;
  status?: string;
  retired?: boolean;
  revision?: number | null;
  revisionBound?: boolean;
  staleBinding?: boolean;
  goalBullet?: string;
  scopes: OwnerScope[];
}

export interface OwnerBullet {
  id: string;
  text: string;
  subgoals: OwnerSubgoal[];
  unbound: OwnerScope[];
}

export interface UnlinkedScope {
  scope: OwnerScope;
  reason: string;
}

export interface UnlinkedTask {
  id: string;
  title: string;
  status: string;
  phases: string[];
  reason: string;
}

export interface OwnerGap {
  code: string;
  id: string;
  reason: string;
}

export interface OwnerHierarchy {
  goal: { id: string; title: string };
  bullets: OwnerBullet[];
  unlinked: { scopes: UnlinkedScope[]; tasks: UnlinkedTask[] };
  gaps: OwnerGap[];
  counts: {
    bullets: number;
    subgoals: number;
    scopes: number;
    tasks: number;
    unlinkedScopes: number;
    unlinkedTasks: number;
  };
  focus: { item: string; task: string };
  empty: boolean;
  /* Which source this shape came from: the sourced read or the page fallback. */
  source: 'read' | 'derived';
}

/** Every wiki-link target in a value: strings, arrays, or objects with an id. */
export function linkIds(value: unknown): string[] {
  const list = Array.isArray(value) ? value : value === undefined || value === null || value === '' ? [] : [value];
  const out: string[] = [];
  const add = (raw: string): void => {
    const bare = raw.split('|')[0].split('#')[0].trim();
    if (bare && !out.includes(bare)) out.push(bare);
  };
  for (const entry of list) {
    const candidate = typeof entry === 'string' ? entry
      : entry !== null && typeof entry === 'object' && !Array.isArray(entry) ? text(object(entry).id) : '';
    if (!candidate) continue;
    const links = candidate.match(/\[\[[^\]]+\]\]/g);
    if (links) for (const link of links) add(link.slice(2, -2));
    else add(candidate);
  }
  return out;
}

/** The historical phase links a record still carries (scope.phase, phase). */
export function phasesOf(frontmatter: Data): string[] {
  const out: string[] = [];
  const add = (value: unknown): void => {
    for (const id of linkIds(value)) if (!out.includes(id)) out.push(id);
  };
  const scope = frontmatter.scope;
  if (scope !== null && typeof scope === 'object' && !Array.isArray(scope)) add(object(scope).phase);
  else add(scope);
  add(frontmatter.phase);
  return out;
}

const asNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;
const asBool = (value: unknown): boolean => value === true;

function planOf(raw: unknown): OwnerPlan | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const row = object(raw);
  if (row.known !== true && row.status === undefined) return null;
  return {
    known: asBool(row.known),
    status: text(row.status) || null,
    reconciled: asBool(row.reconciled),
    source: text(row.source) || null,
  };
}

function scopeOf(raw: unknown, fallbackPhases: string[], tasks: OwnerTask[]): OwnerScope {
  const row = object(raw);
  const proven = asNumber(row.proven);
  const plan = planOf(row.plan);
  const scope: OwnerScope = {
    id: text(row.id),
    title: text(row.title, text(row.id)),
    titleTruncated: asBool(row.title_truncated) || undefined,
    priority: text(row.priority, 'other'),
    proven,
    provenKnown: row.proven_known === undefined ? undefined : asBool(row.proven_known),
    phases: rows(row.phases).length ? rows(row.phases).map((p) => text(p)).filter(Boolean) : fallbackPhases,
    subgoal: text(row.subgoal),
    tasks: row.tasks === undefined ? tasks : rows(row.tasks).map((task) => ({
      id: text(task.id),
      title: text(task.title, text(task.id)),
      status: text(task.status, 'unknown'),
      phases: [],
    })).filter((task) => task.id !== ''),
  };
  if (plan) scope.plan = plan;
  const reason = text(row.reason);
  if (reason) scope.reason = reason;
  const code = text(row.code);
  if (code) scope.code = code;
  return scope;
}

/** The sourced read's payload as the page shape, or null when it is not one. */
export function sourcedHierarchyOf(raw: unknown): OwnerHierarchy | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const envelope = object(raw);
  const data = envelope.data !== undefined && typeof envelope.data === 'object'
    ? object(envelope.data) : envelope;
  if (!Array.isArray(data.bullets)) return null;
  const bullets: OwnerBullet[] = [];
  for (const rawBullet of data.bullets) {
    const row = object(rawBullet);
    const subgoals: OwnerSubgoal[] = rows(row.subgoals).map((rawSubgoal) => {
      const subgoal = object(rawSubgoal);
      const revision = asNumber(subgoal.revision);
      return {
        id: text(subgoal.id),
        title: text(subgoal.title) || undefined,
        titleTruncated: asBool(subgoal.title_truncated) || undefined,
        status: text(subgoal.status) || undefined,
        retired: asBool(subgoal.retired) || undefined,
        revision,
        revisionBound: asBool(subgoal.revision_bound),
        staleBinding: asBool(subgoal.stale_binding) || undefined,
        goalBullet: text(subgoal.goal_bullet) || undefined,
        scopes: rows(subgoal.scopes).map((scope) => scopeOf(scope, [], [])).filter((s) => s.id !== ''),
      };
    }).filter((subgoal) => subgoal.id !== '');
    bullets.push({
      id: text(row.id),
      text: text(row.text, text(row.id)),
      subgoals,
      unbound: rows(row.unbound_scopes).map((scope) => scopeOf(scope, [], [])).filter((s) => s.id !== ''),
    });
  }
  const unlinkedScopes: UnlinkedScope[] = rows(data.unlinked_scopes).map((rawScope) => {
    const scope = scopeOf(rawScope, [], []);
    return { scope, reason: text(object(rawScope).reason, 'not named by any Goal bullet') };
  }).filter((entry) => entry.scope.id !== '');
  const unlinkedTasks: UnlinkedTask[] = rows(data.unlinked_tasks).map((rawTask) => {
    const task = object(rawTask);
    return {
      id: text(task.id),
      title: text(task.title, text(task.id)),
      status: text(task.status, 'unknown'),
      phases: rows(task.phases).map((phase) => text(phase)).filter(Boolean),
      reason: text(task.reason, 'outside the authored chain'),
    };
  }).filter((task) => task.id !== '');
  const countsRow = object(data.counts);
  const gaps: OwnerGap[] = rows(data.gaps).map((rawGap) => ({
    code: text(object(rawGap).code),
    id: text(object(rawGap).id),
    reason: text(object(rawGap).reason),
  })).filter((gap) => gap.code !== '');
  return {
    goal: { id: 'Goal', title: 'Goal' },
    bullets,
    unlinked: { scopes: unlinkedScopes, tasks: unlinkedTasks },
    gaps,
    counts: {
      bullets: asNumber(countsRow.bullets) ?? bullets.length,
      subgoals: asNumber(countsRow.subgoals) ?? 0,
      scopes: asNumber(countsRow.scopes) ?? 0,
      tasks: asNumber(countsRow.tasks) ?? 0,
      unlinkedScopes: asNumber(countsRow.unlinked_scopes) ?? unlinkedScopes.length,
      unlinkedTasks: asNumber(countsRow.unlinked_tasks) ?? unlinkedTasks.length,
    },
    focus: { item: '', task: '' },
    empty: asBool(data.empty),
    source: 'read',
  };
}

interface RawTask extends OwnerTask {
  primaryScope: string[];
  implementsIds: string[];
}

interface RawScope {
  id: string;
  title: string;
  priority: string;
  proven: number | null;
  phases: string[];
  subgoal: string;
}

function taskRowsOf(snapshot: Snapshot): RawTask[] {
  return rows(snapshot.tasks).map((row) => ({
    id: text(row.id),
    title: text(row.title, text(row.id)),
    status: text(row.status, 'unknown'),
    phases: phasesOf(row),
    primaryScope: linkIds(row.primary_scope),
    implementsIds: linkIds(row.implements),
  })).filter((task) => task.id !== '');
}

function scopeRowsOf(snapshot: Snapshot, model: Model | null): RawScope[] {
  return rows(snapshot.scope_items).map((row) => {
    const id = text(row.id);
    const subgoal = linkIds(row.primary_subgoal)[0] ?? '';
    return {
      id,
      title: text(row.title, id),
      priority: text(row.priority, 'other'),
      proven: model?.SC.get(id)?.proven ?? null,
      phases: phasesOf(row),
      subgoal,
    };
  }).filter((scope) => scope.id !== '');
}

/** The derived fallback (see the file header): the page's own authored links. */
function derivedHierarchy(
  snapshot: Snapshot,
  model: Model | null,
  focus: { item?: string; task?: string },
): OwnerHierarchy {
  const rawScopes = scopeRowsOf(snapshot, model);
  const byId = new Map(rawScopes.map((scope) => [scope.id, scope]));
  const scopes = new Map<string, OwnerScope>();
  for (const scope of rawScopes) {
    scopes.set(scope.id, {
      id: scope.id, title: scope.title, priority: scope.priority,
      proven: scope.proven, phases: scope.phases, subgoal: scope.subgoal, tasks: [],
    });
  }

  const rawTasks = taskRowsOf(snapshot);
  const unlinkedTasks: UnlinkedTask[] = [];
  for (const task of rawTasks) {
    const owner = task.primaryScope.length === 1 ? scopes.get(task.primaryScope[0]) : undefined;
    if (owner) {
      owner.tasks.push({ id: task.id, title: task.title, status: task.status, phases: task.phases });
      continue;
    }
    let reason: string;
    if (task.primaryScope.length > 1) {
      reason = 'more than one authored scope: ' + task.primaryScope.join(', ');
    } else if (task.primaryScope.length === 1 && !byId.has(task.primaryScope[0])) {
      reason = 'the authored scope ' + task.primaryScope[0] + ' is not a scope item in this store';
    } else if (task.implementsIds.length) {
      reason = 'no authored scope; it only implements ' + task.implementsIds.join(', ') + ' (not the chain)';
    } else if (task.phases.length) {
      reason = 'no authored scope; historical phase ' + task.phases.join(', ') + ' (not the chain)';
    } else {
      reason = 'no authored scope; standalone (no invented ancestry)';
    }
    unlinkedTasks.push({ id: task.id, title: task.title, status: task.status, phases: task.phases, reason });
  }

  const used = new Set<string>();
  const bullets: OwnerBullet[] = [];
  for (const goal of model?.goals ?? []) {
    const groups = new Map<string, OwnerSubgoal>();
    const unbound: OwnerScope[] = [];
    for (const id of goal.items) {
      const scope = scopes.get(id);
      if (!scope || used.has(id)) continue;
      used.add(id);
      if (scope.subgoal) {
        const group = groups.get(scope.subgoal) ?? { id: scope.subgoal, scopes: [] };
        group.scopes.push(scope);
        groups.set(scope.subgoal, group);
      } else {
        unbound.push(scope);
      }
    }
    bullets.push({ id: goal.id, text: goal.text || goal.label || goal.id, subgoals: [...groups.values()], unbound });
  }

  const unlinkedScopes: UnlinkedScope[] = rawScopes.filter((scope) => !used.has(scope.id)).map((scope) => ({
    scope: scopes.get(scope.id) as OwnerScope,
    reason: model
      ? 'not named by any Goal bullet'
      : 'the atlas read did not answer, so no Goal bullet can be resolved',
  }));

  const subgoalIds = new Set<string>();
  for (const scope of rawScopes) if (scope.subgoal) subgoalIds.add(scope.subgoal);

  return {
    goal: { id: 'Goal', title: 'Goal' },
    bullets,
    unlinked: { scopes: unlinkedScopes, tasks: unlinkedTasks },
    gaps: [],
    counts: {
      bullets: bullets.length,
      subgoals: subgoalIds.size,
      scopes: rawScopes.length,
      tasks: rawTasks.length,
      unlinkedScopes: unlinkedScopes.length,
      unlinkedTasks: unlinkedTasks.length,
    },
    focus: { item: focus.item ?? '', task: focus.task ?? '' },
    empty: bullets.length === 0 && rawScopes.length === 0 && rawTasks.length === 0,
    source: 'derived',
  };
}

/** The one shape the owner view renders: the sourced read when it answered, else the derived fallback. */
export function hierarchyOf(
  snapshot: Snapshot,
  model: Model | null,
  focus: { item?: string; task?: string } = {},
  sourced: unknown = null,
): OwnerHierarchy {
  const read = sourcedHierarchyOf(sourced);
  if (read) return { ...read, focus: { item: focus.item ?? '', task: focus.task ?? '' } };
  return derivedHierarchy(snapshot, model, focus);
}
