/* =====================================================================
   THE OUTCOME A PIECE OF WORK OR SCOPE SERVES

   A goal names its tasks and its scope items, so the mapping is the read
   model's, not the screen's: a surface shows which outcome the work serves
   without copying that outcome's status - the outcome page stays the one
   place that reports it.

   Pure: no DOM, no fetch, no store. React escapes every value it renders.
   ===================================================================== */
import { object, rows, text, type Data } from './contracts';

export type Outcome = { id: string; text: string };

/** The goals that name a task, or the scope items a goal names. */
function mapGoals(understanding: unknown, of: (goal: Data) => Data[]): Map<string, Outcome[]> {
  const map = new Map<string, Outcome[]>();
  for (const goal of rows(object(understanding).goals)) {
    const id = text(goal.id);
    if (!id) continue;
    const outcome: Outcome = { id, text: text(goal.text, id) };
    for (const entry of of(goal)) {
      const key = text(entry.id);
      if (!key) continue;
      const list = map.get(key) ?? [];
      /* One goal names an entity once, however many of its items carry it. */
      if (!list.some((entry2) => entry2.id === id)) list.push(outcome);
      map.set(key, list);
    }
  }
  return map;
}

/** The outcomes a task serves. */
export const outcomeByTask = (understanding: unknown): Map<string, Outcome[]> =>
  mapGoals(understanding, (goal) => rows(goal.tasks));

/** The outcomes that name a scope item. */
export const outcomeByItem = (understanding: unknown): Map<string, Outcome[]> =>
  mapGoals(understanding, (goal) => rows(goal.items));

/** The short label: the lead of the goal's own words, clipped. */
export function outcomeLabel(outcome: Outcome): string {
  const body = outcome.text.split(':')[0].trim() || outcome.text;
  return body.length > 32 ? body.slice(0, 31) + '…' : body;
}
