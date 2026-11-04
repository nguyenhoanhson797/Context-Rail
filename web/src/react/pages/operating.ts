/* The compiled operating model, read straight from the snapshot.
   Labels, the unit, the hierarchy and the screens come from the pack's
   presentation; no screen may branch on a pack name. */
import { object, rows, text, type Data, type Snapshot } from '../contracts';

export const operatingOf = (snapshot: Snapshot | Data): Data => object(object(snapshot).operating);
export const unitOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).unit);
export const labelsOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).labels);
export const focusOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).focus);
export const progressOf = (snapshot: Snapshot | Data): Data => object(operatingOf(snapshot).progress);

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
  const screen = rows(operatingOf(snapshot ?? {}).screens).find((entry) => text(entry.id) === id);
  return text(screen?.title, fallback);
}

export function focusName(snapshot: Snapshot | Data): string {
  const focus = focusOf(snapshot);
  if (text(focus.state) !== 'resolved') return '';
  const chosen = object(focus.focus);
  return text(chosen.title, text(chosen.slug));
}
