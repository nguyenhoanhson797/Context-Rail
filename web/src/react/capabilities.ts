/* The compiled-surface registry: every decision a screen makes is asked here,
   and every answer is read from the snapshot the compiler produced. A pack
   name, a preset name, a state name or a tag name never selects a branch.
   A capability is present only when its compiled fact is present. */
import { object, rows, text, type Data, type Snapshot } from './contracts';
import {
  focusOf,
  hierarchyLevels,
  labelOf,
  operatingOf,
  progressOf,
  screenDef,
  screensOf,
  unitOf,
  type ScreenDef,
} from './pages/operating';

export type Slot = 'flow' | 'plan' | 'ledger';
export const FLOW_KINDS = ['board'];
export const PLAN_KINDS = ['plan', 'timeline'];
export const LEDGER_KINDS = ['records'];
export const CATALOG_KINDS = ['catalog'];
export const WORKFLOW_KINDS = ['workflow'];
export const PROGRESS_KINDS = ['execution'];
export const PACK_KINDS = ['pack'];
export const SUMMARY_KINDS = ['summary'];

export const CATEGORY_ORDER = ['not_started', 'active', 'blocked', 'done', 'dropped'] as const;
export const CATEGORY_LABEL: Record<string, string> = {
  not_started: 'Not started',
  active: 'Active',
  blocked: 'Blocked',
  done: 'Done',
  dropped: 'Dropped',
};

/** A media field the profile may declare; no built-in pack declares one yet. */
const PREVIEW_FIELDS = ['preview', 'media', 'poster', 'thumbnail', 'image', 'still', 'cover'];

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];

const presetOf = (doc: Data): string => text(doc.preset, text(doc.type));

/** Every item the snapshot carries, once. Containers, leaves and milestones. */
export function allItems(snapshot: Snapshot | Data): Data[] {
  const seen = new Set<string>();
  const items: Data[] = [];
  for (const key of ['tasks', 'phases', 'milestones']) {
    for (const doc of rows(object(snapshot)[key])) {
      const id = text(doc.id);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      items.push(doc);
    }
  }
  return items;
}

export function itemById(snapshot: Snapshot | Data): Map<string, Data> {
  return new Map(allItems(snapshot).map((doc) => [text(doc.id), doc]));
}

export function screenOf(snapshot: Snapshot | Data, kinds: string[]): ScreenDef | null {
  const declared = screensOf(snapshot).find((screen) => kinds.includes(text(screen.kind)));
  if (!declared) return null;
  return screenDef(snapshot, text(declared.id));
}

export const flowScreen = (snapshot: Snapshot | Data): ScreenDef | null => screenOf(snapshot, FLOW_KINDS);
export const planScreen = (snapshot: Snapshot | Data): ScreenDef | null => screenOf(snapshot, PLAN_KINDS);
export const ledgerScreen = (snapshot: Snapshot | Data): ScreenDef | null => screenOf(snapshot, LEDGER_KINDS);
export const catalogScreen = (snapshot: Snapshot | Data): ScreenDef | null => screenOf(snapshot, CATALOG_KINDS);

/** The preset population a screen pins. A screen with no pin uses the unit. */
export function populationPresets(snapshot: Snapshot | Data, screen: ScreenDef | null): string[] {
  if (screen && screen.presets.length) return screen.presets;
  if (screen && screen.preset) return [screen.preset];
  const unit = text(unitOf(snapshot).preset);
  return unit ? [unit] : [];
}

export function populationItems(snapshot: Snapshot | Data, presets: string[]): Data[] {
  if (!presets.length) return [];
  const wanted = new Set(presets);
  return allItems(snapshot).filter((doc) => wanted.has(presetOf(doc)));
}

/** A view's declared grouping. The board honours it; categories are the fallback. */
export const groupByOf = (screen: ScreenDef | null): string => (screen ? screen.groupBy ?? '' : '');

export function workflowFacts(snapshot: Snapshot | Data): Data[] {
  return rows(object(snapshot).workflows);
}

export function workflowFact(snapshot: Snapshot | Data, name: string): Data | null {
  return workflowFacts(snapshot).find((entry) => text(entry.name) === name) ?? null;
}

export function workflowForPreset(snapshot: Snapshot | Data, preset: string): string {
  const declared = rows(object(snapshot).presets).find((entry) => text(entry.name) === preset);
  return text(object(declared).workflow);
}

export function screenWorkflow(snapshot: Snapshot | Data, screen: ScreenDef | null): string {
  const presets = populationPresets(snapshot, screen);
  for (const preset of presets) {
    const workflow = workflowForPreset(snapshot, preset);
    if (workflow) return workflow;
  }
  const unit = text(unitOf(snapshot).workflow);
  if (unit) return unit;
  return text(workflowFacts(snapshot)[0]?.name);
}

export function workflowStatuses(snapshot: Snapshot | Data, workflow: string): string[] {
  const fact = workflowFact(snapshot, workflow);
  const states = strings(fact?.states);
  if (states.length) return states;
  return strings(object(snapshot).statuses);
}

export function statusCategories(snapshot: Snapshot | Data): Data {
  return object(object(snapshot).status_categories);
}

export function categoryOf(snapshot: Snapshot | Data, status: string): string {
  return text(statusCategories(snapshot)[status], 'not_started');
}

export interface StatusColumn {
  status: string;
  category: string;
  label: string;
  order: number;
}

export interface CategoryBand {
  category: string;
  label: string;
  statuses: StatusColumn[];
}

/** `group_by: status` rendered as fixed category bands over the profile's states. */
export function bandedStatuses(snapshot: Snapshot | Data, workflow: string): CategoryBand[] {
  const statuses = workflowStatuses(snapshot, workflow);
  const columns: StatusColumn[] = statuses.map((status, index) => ({
    status,
    category: categoryOf(snapshot, status),
    label: labelOf(snapshot, status).replace(/-/g, ' '),
    order: index,
  }));
  const extra = [...new Set(columns.map((column) => column.category))].filter(
    (category) => !(CATEGORY_ORDER as readonly string[]).includes(category),
  );
  const order = [...CATEGORY_ORDER, ...extra];
  return order
    .map((category) => ({
      category,
      label: CATEGORY_LABEL[category] ?? category.replace(/_/g, ' '),
      statuses: columns.filter((column) => column.category === category),
    }))
    .filter((band) => band.statuses.length > 0);
}

export const allStatusColumns = (bands: CategoryBand[]): StatusColumn[] =>
  bands.flatMap((band) => band.statuses);

export interface GateFact {
  gate: string;
  checks: string[];
}

export function gateFor(snapshot: Snapshot | Data, workflow: string, status: string): GateFact | null {
  const fact = workflowFact(snapshot, workflow);
  const rule = object(object(fact?.quality)[status]);
  const gate = text(rule.gate);
  if (!gate) return null;
  return { gate, checks: strings(rule.checks) };
}

export function gateStatuses(snapshot: Snapshot | Data, workflow: string): string[] {
  const fact = workflowFact(snapshot, workflow);
  return Object.entries(object(fact?.quality))
    .filter(([, rule]) => Boolean(text(object(rule).gate)))
    .map(([status]) => status);
}

export function transitionsOf(snapshot: Snapshot | Data, workflow: string, status: string): string[] {
  const fact = workflowFact(snapshot, workflow);
  return strings(object(fact?.transitions)[status]);
}

export function hasRelations(items: Data[]): boolean {
  return items.some((item) => {
    const relations = object(item.relations);
    return ['part_of', 'depends_on', 'verifies'].some((rel) => strings(relations[rel]).length > 0);
  });
}

export function itemsNeedingGate(snapshot: Snapshot | Data, preset: string, status: string): string[] {
  const workflow = workflowForPreset(snapshot, preset);
  if (!workflow) return [];
  return transitionsOf(snapshot, workflow, status).filter((target) => Boolean(gateFor(snapshot, workflow, target)));
}

/* ---------- conditional capabilities ---------- */

export const hasWorkflowSelector = (snapshot: Snapshot | Data): boolean => workflowFacts(snapshot).length > 1;

export const hasGates = (snapshot: Snapshot | Data): boolean =>
  workflowFacts(snapshot).some((workflow) => Object.keys(object(workflow.quality)).length > 0);

export const executionActions = (snapshot: Snapshot | Data): string[] =>
  strings(object(operatingOf(snapshot).execution).actions);

export const hasExecution = (snapshot: Snapshot | Data): boolean => executionActions(snapshot).length > 0;

export function focusPolicyOf(snapshot: Snapshot | Data): Data {
  const policy = operatingOf(snapshot).focus_policy;
  return policy && typeof policy === 'object' && !Array.isArray(policy) ? (policy as Data) : {};
}

export const hasFocus = (snapshot: Snapshot | Data): boolean => Object.keys(focusPolicyOf(snapshot)).length > 0;

export function focusState(snapshot: Snapshot | Data): string {
  return text(focusOf(snapshot).state, 'none');
}

export function focusCandidates(snapshot: Snapshot | Data): Data[] {
  return rows(focusOf(snapshot).candidates);
}

export function focusFact(snapshot: Snapshot | Data): Data {
  return object(focusOf(snapshot).focus);
}

export const hasOrderedHierarchy = (snapshot: Snapshot | Data): boolean =>
  hierarchyLevels(snapshot).some((level) => Boolean(text(level.order))) || Boolean(text(focusPolicyOf(snapshot).order));

export const hasOptionalHierarchy = (snapshot: Snapshot | Data): boolean => {
  const levels = hierarchyLevels(snapshot);
  return levels.length === 0 || levels.some((level) => level.required !== true);
};

export function previewFieldsOf(snapshot: Snapshot | Data): string[] {
  const found = new Set<string>();
  for (const preset of rows(object(snapshot).presets)) {
    for (const field of Object.keys(object(preset.fields))) {
      if (PREVIEW_FIELDS.includes(field)) found.add(field);
    }
  }
  return [...found];
}

export const hasPreviewField = (snapshot: Snapshot | Data): boolean => previewFieldsOf(snapshot).length > 0;

export const hasPerPresetCompletion = (snapshot: Snapshot | Data): boolean =>
  Object.keys(object(object(object(progressOf(snapshot)).rule).presets)).length > 0;

export { hierarchyLevels } from './pages/operating';

export const declaredActions = (snapshot: Snapshot | Data): Data[] => rows(operatingOf(snapshot).actions);

export const hasActionPreconditions = (snapshot: Snapshot | Data): boolean =>
  declaredActions(snapshot).some((action) => {
    const requires = object(action.requires);
    return requires.children === true || Boolean(requires.check) || requires.review === true || Boolean(requires.field);
  });

export function actionsFor(snapshot: Snapshot | Data, preset: string, status: string): Data[] {
  return declaredActions(snapshot).filter((action) => {
    const on = text(action.on);
    if (on !== '*' && on !== preset) return false;
    const from = strings(action.from);
    return from.length === 0 || from.includes(status);
  });
}

/* ---------- attention ---------- */

export interface Attention {
  key: string;
  kind: 'blocked' | 'gate' | 'focus';
  id: string;
  title: string;
  reason: string;
}

export function attentionOf(snapshot: Snapshot | Data): Attention[] {
  const out: Attention[] = [];
  const seen = new Set<string>();
  for (const item of allItems(snapshot)) {
    const id = text(item.id);
    if (!id) continue;
    const category = text(item.category);
    if (category === 'blocked') {
      const key = 'blocked:' + id;
      if (!seen.has(key)) {
        seen.add(key);
        out.push({ key, kind: 'blocked', id, title: text(item.title, id), reason: text(item.reason, text(item.blocked_why, 'blocked')) });
      }
    }
    const preset = presetOf(item);
    const gates = itemsNeedingGate(snapshot, preset, text(item.status));
    if (gates.length) {
      const key = 'gate:' + id;
      if (!seen.has(key)) {
        seen.add(key);
        out.push({ key, kind: 'gate', id, title: text(item.title, id), reason: 'proof owed before ' + gates.join(', ') });
      }
    }
  }
  if (focusState(snapshot) === 'ambiguous') {
    for (const candidate of focusCandidates(snapshot)) {
      const id = text(candidate.slug);
      const key = 'focus:' + id;
      if (!id || seen.has(key)) continue;
      seen.add(key);
      out.push({ key, kind: 'focus', id, title: text(candidate.title, id), reason: 'focus is ambiguous; choose a candidate' });
    }
  }
  return out;
}

/* ---------- completion ---------- */

export interface CompletionRule {
  of: string[];
  states: string[];
  category: string;
  presets: Record<string, string[]>;
  group: string;
}

export function completionOf(snapshot: Snapshot | Data): CompletionRule {
  const progress = progressOf(snapshot);
  const rule = object(progress.rule);
  const presets: Record<string, string[]> = {};
  for (const [key, value] of Object.entries(object(rule.presets))) presets[key] = strings(value);
  return {
    of: strings(progress.of),
    states: strings(rule.states),
    category: text(rule.category),
    presets,
    group: text(progress.group),
  };
}

export function completionSentence(snapshot: Snapshot | Data): string {
  const rule = completionOf(snapshot);
  const population = rule.of.length === 1 ? labelOf(snapshot, rule.of[0], true) : labelOf(snapshot, text(unitOf(snapshot).preset), true);
  const grouped = rule.group ? `, grouped by ${labelOf(snapshot, rule.group, true)}` : '';
  if (Object.keys(rule.presets).length) {
    const parts = Object.entries(rule.presets).map(([preset, states]) => `${labelOf(snapshot, preset, true)} when ${states.join(' or ')}`);
    return `Complete = ${parts.join('; ')}${grouped}.`;
  }
  const scope = rule.states.length
    ? `states ${rule.states.join(', ')}`
    : rule.category
      ? `category ${rule.category.replace(/_/g, ' ')}`
      : 'the done category';
  return `Complete = ${scope}, counted in ${population}${grouped}.`;
}

/* ---------- records ---------- */

export interface RecordFact {
  doc: Data;
  id: string;
  kind: string;
  title: string;
  status: string;
  about: string;
}

export function recordsOf(snapshot: Snapshot | Data, includeActivity = false): RecordFact[] {
  const seen = new Set<string>();
  const out: RecordFact[] = [];
  for (const key of ['decisions', 'risks', 'questions', 'records']) {
    for (const doc of rows(object(snapshot)[key])) {
      const id = text(doc.id);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const relations = object(doc.relations);
      const about = strings(relations.about)[0] ?? text(object(doc.fields).about, text(doc.about));
      /* The generic record stream is the activity/evidence log. The ledger
         opens on governance (decisions, risks, questions) and keeps the
         stream behind an explicit toggle, never as a default feed. */
      if (key === 'records' && !includeActivity) continue;
      out.push({
        doc,
        id,
        kind: presetOf(doc) || text(doc.primitive, 'record'),
        title: text(doc.title, id),
        status: text(doc.status, text(doc.category)),
        about,
      });
    }
  }
  return out.sort((a, b) => a.kind.localeCompare(b.kind) || a.title.localeCompare(b.title));
}

/* ---------- plan ---------- */

export interface TreeRow {
  doc: Data;
  depth: number;
}

export function treeOf(snapshot: Snapshot | Data, roots: Data[]): TreeRow[] {
  const children = new Map<string, Data[]>();
  for (const doc of allItems(snapshot)) {
    for (const parent of strings(object(doc.relations).part_of)) {
      children.set(parent, [...(children.get(parent) ?? []), doc]);
    }
  }
  const walk = (doc: Data, depth: number, seen: Set<string>): TreeRow[] => {
    const id = text(doc.id);
    if (!id || seen.has(id)) return [];
    const next = new Set(seen);
    next.add(id);
    return [
      { doc, depth },
      ...(children.get(id) ?? []).flatMap((child) => walk(child, depth + 1, next)),
    ];
  };
  return roots.flatMap((root) => walk(root, 0, new Set()));
}

export function orphansOf(snapshot: Snapshot | Data, candidates: string[]): Data[] {
  const wanted = new Set(candidates);
  return allItems(snapshot).filter((doc) => {
    if (!wanted.has(presetOf(doc))) return false;
    const parents = strings(object(doc.relations).part_of);
    return parents.length === 0;
  });
}

export function orderedValue(doc: Data): number | null {
  const raw = object(doc.fields).sequence;
  const value = typeof raw === 'number' ? raw : Number(text(raw));
  return Number.isFinite(value) ? value : null;
}

export function inCompiledOrder(items: Data[]): Data[] {
  return [...items].sort((a, b) => {
    const left = orderedValue(a);
    const right = orderedValue(b);
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;
    return left - right;
  });
}

/* ---------- fields ---------- */

export function fieldValue(doc: Data, field: string): string {
  const value = doc[field] ?? object(doc.fields)[field];
  if (Array.isArray(value)) return value.map((entry) => text(entry)).filter(Boolean).join(', ');
  return text(value);
}

export function searchableText(snapshot: Snapshot | Data): Data[] {
  return allItems(snapshot);
}
