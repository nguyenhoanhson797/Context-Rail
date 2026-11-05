/* The compiled operating model, read straight from the snapshot.
   Labels, the unit, the hierarchy and the screens come from the pack's
   presentation; no screen may branch on a pack name. */
import { object, rows, text, type Data, type Snapshot } from '../contracts';

export const operatingOf = (snapshot: Snapshot | Data): Data => object(object(snapshot).operating);
export const unitOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).unit);
export const labelsOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).labels);
export const focusOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).focus);
export const progressOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).progress);
export const queueOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).queue);
export const queuePolicyOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).queue_policy);
export const screensOf = (snapshot: Snapshot | Data): Data[] => rows(operatingOf(snapshot).screens);
export const fieldsOf = (snapshot: Snapshot | Data): Record<string, string[]> => {
  const fields = object(operatingOf(snapshot).fields);
  const out: Record<string, string[]> = {};
  for (const [key, value] of Object.entries(fields)) {
    out[key] = Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
  }
  return out;
};
export const cardFieldsOf = (snapshot: Snapshot | Data): string[] => fieldsOf(snapshot).card ?? [];
export const filterFieldsOf = (snapshot: Snapshot | Data): string[] => fieldsOf(snapshot).filters ?? [];
export const columnFieldsOf = (snapshot: Snapshot | Data): string[] => fieldsOf(snapshot).columns ?? [];

export interface ScreenDef {
  id: string;
  kind: string;
  title: string;
  preset: string | null;
  presets: string[];
  groupBy: string | null;
}

export function screenDef(snapshot: Snapshot | Data, id: string): ScreenDef | null {
  const screen = screensOf(snapshot).find((entry) => text(entry.id) === id);
  if (!screen) return null;
  const presets = Array.isArray(screen.presets)
    ? screen.presets.filter((entry): entry is string => typeof entry === 'string')
    : [];
  return {
    id: text(screen.id),
    kind: text(screen.kind),
    title: text(screen.title),
    preset: text(screen.preset) || null,
    presets,
    groupBy: text(screen.group_by) || null,
  };
}

/* Kind -> route. The shell's routes keep their legacy names; a screen is
   addressed by its id, and the kind decides which renderer it gets. */
export const KIND_ROUTE: Record<string, string> = {
  summary: 'summary',
  board: 'board',
  plan: 'roadmap',
  timeline: 'roadmap',
  catalog: 'catalog',
  records: 'decisions',
  workflow: 'workflow',
  execution: 'execution',
  pack: 'pack',
};

export function routeForScreen(screen: ScreenDef | null, legacy: string): string {
  if (!screen) return legacy;
  return KIND_ROUTE[screen.kind] ?? legacy;
}

export function screenFor(snapshot: Snapshot | Data, route: string): ScreenDef | null {
  const found = screensOf(snapshot).find(
    (entry) => text(entry.id) === route || KIND_ROUTE[text(entry.kind)] === route,
  );
  return found ? screenDef(snapshot, text(found.id)) : null;
}

/* One title for one route, everywhere: nav, breadcrumb, document.title and
   "Go to" in the palette. A pack that renames its Plan screen governs every
   surface. */
export function screenTitleForRoute(
  snapshot: Snapshot | Data | undefined,
  route: string,
  fallback: string,
): string {
  const screen = snapshot && screenFor(snapshot, route);
  return screen ? screen.title : fallback;
}

export const titleCase = (id: string): string =>
  id.replace(/-/g, ' ').replace(/(^|\s)\w/g, (c) => c.toUpperCase());

export function labelOf(snapshot: Snapshot | Data, preset: string, many = false): string {
  const label = object(labelsOf(snapshot)[preset]);
  return text(many ? label.many : label.one, titleCase(preset));
}

export function unitLabel(snapshot: Snapshot | Data, many = false): string {
  const unit = text(unitOf(snapshot).preset);
  return unit ? labelOf(snapshot, unit, many) : many ? 'items' : 'item';
}

export function hierarchyLabels(snapshot: Snapshot | Data): string[] {
  return rows(operatingOf(snapshot).hierarchy).map((level) =>
    text(level.label, titleCase(text(level.preset))),
  );
}

export function screenTitle(snapshot: Snapshot | Data | undefined, id: string, fallback: string): string {
  const find = (predicate: (entry: Data) => boolean) =>
    rows(operatingOf(snapshot ?? {}).screens).find(predicate);
  const byId = find((entry) => text(entry.id) === id);
  if (byId) return text(byId.title, fallback);
  /* Custom screen ids (e.g. {id: sprints, kind: timeline}) match by kind. */
  const byKind = find((entry) => text(entry.kind) === id);
  if (byKind) return text(byKind.title, fallback);
  /* Legacy route ids: `roadmap` is the plan screen (plan or timeline kind),
     `decisions` the records screen. */
  const legacyKind: Record<string, string[]> = { roadmap: ['plan', 'timeline'], decisions: ['records'] };  for (const kind of legacyKind[id] ?? []) {
    const legacy = find((entry) => text(entry.kind) === kind);
    if (legacy) return text(legacy.title, fallback);
  }
  return fallback;
}

export function focusName(snapshot: Snapshot | Data): string {
  const focus = focusOf(snapshot);
  if (text(focus.state) !== 'resolved') return '';
  const chosen = object(focus.focus);
  return text(chosen.title, text(chosen.slug));
}

export function catalogItems(snapshot: Snapshot | Data, presetFilter?: string[]): Data[] {
  const unit = text(unitOf(snapshot).preset);
  const presetSet = presetFilter && presetFilter.length ? new Set(presetFilter) : null;
  if (presetSet) {
    return rows(object(snapshot).tasks).filter((task) => presetSet.has(text(task.preset)));
  }
  return rows(object(snapshot).tasks).filter(
    (task) =>
      !['done', 'dropped'].includes(text(task.category)) &&
      text(task.preset) !== unit &&
      !text(task.priority),
  );
}

export function priorityValues(snapshot: Snapshot | Data): string[] {
  const presets = rows(object(snapshot).presets);
  for (const preset of presets) {
    const fields = object(preset.fields);
    const priority = fields.priority;
    if (Array.isArray(priority) && priority.length) {
      return priority.filter((entry): entry is string => typeof entry === 'string');
    }
  }
  return ['must', 'should', 'could', 'wont'];
}

export function hierarchyLevels(snapshot: Snapshot | Data): Data[] {
  return rows(operatingOf(snapshot).hierarchy);
}

export function allWorkItems(snapshot: Snapshot | Data): Data[] {
  const snap = object(snapshot);
  const seen = new Set<string>();
  const all: Data[] = [];
  for (const key of ['tasks', 'phases', 'scope_items', 'records', 'decisions', 'risks', 'questions', 'core_docs', 'milestones']) {
    for (const doc of rows(snap[key])) {
      const id = text(doc.id);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      all.push(doc);
    }
  }
  return all;
}

export function screenHasData(snapshot: Snapshot | Data, route: string): boolean {
  switch (route) {
    case 'board':
    case 'workflow':
    case 'execution':
      return true;
    case 'roadmap':
      return (
        rows(object(snapshot).phases).length > 0 ||
        rows(object(snapshot).tasks).length > 0
      );
    case 'scope':
      return rows(object(snapshot).scope_items).length > 0;
    case 'catalog': {
      const def = screenFor(snapshot, 'catalog');
      return catalogItems(snapshot, def?.presets).length > 0;
    }
    case 'decisions':
      return (
        rows(object(snapshot).decisions).length +
          rows(object(snapshot).risks).length +
          rows(object(snapshot).questions).length +
          rows(object(snapshot).records).length >
        0
      );
    default:
      return true;
  }
}
