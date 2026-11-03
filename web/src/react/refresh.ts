/* =====================================================================
   THE REFRESH CONTRACT

   Every screen reads the store through TanStack Query, so "refresh" has
   two halves: the keys that decide what a read invalidates, and the rule
   that decides whether an answer may still be written into the view.

   Both used to live in App.tsx and in each page's own Reload button,
   which is how a screen could show a fresh page body over a stale task
   list, a stale cursor and a stale doctor count while the shell's "as
   of" label claimed the whole view was current - and how the slowest
   answer, not the newest one, could end up on screen.

   This module is the one place that knows:

   - PROJECT_READS and readKey: the key of every cached read of a project,
     so a read and the thing that invalidates it cannot drift apart.
   - refreshProject: the one refresh - cancel what is in flight, take one
     snapshot, seed it, invalidate the project's reads, refetch the ones
     that are on screen.
   - RefreshGeneration: the request generation guard - an answer that
     belongs to a superseded refresh describes a moment the user has
     already left, so it is dropped instead of overwriting the newer data.
   ===================================================================== */
import type { Query, QueryClient, QueryKey } from '@tanstack/react-query';
import type { Snapshot } from './contracts';

/** The root of every query that belongs to one project. */
export const RAIL_KEY = 'rail';
/** The root of the queries that are not about one project. */
export const RAIL_GLOBAL_KEY = 'rail-global';

/**
 * The cached reads of one project and the key segment each one uses.
 *
 * A screen and the thing that invalidates it have to agree on the segment,
 * so the segments are named once here. A read a screen adds without a
 * segment here is a read no refresh can reach.
 */
export const PROJECT_READS = {
  snapshot: 'snapshot',
  health: 'health',
  legacySnapshot: 'legacy-snapshot',
  doc: 'doc',
  next: 'next',
  scopeScore: 'scope-score',
  lens: 'lens',
  graph: 'graph',
  doctor: 'doctor',
  understanding: 'understanding',
  activity: 'activity',
  impact: 'impact',
  atlas: 'atlas',
  catchup: 'catchup',
  hierarchy: 'hierarchy',
} as const;

export type ProjectRead = keyof typeof PROJECT_READS;

/** The key of one read of one project. */
export function readKey(project: string, read: ProjectRead, ...rest: (string | number | boolean)[]): QueryKey {
  return [RAIL_KEY, project, PROJECT_READS[read], ...rest];
}

/** The key prefix that owns every read of one project. */
export function projectKey(project: string): QueryKey {
  return [RAIL_KEY, project];
}

/** True when a query is a read of `project`. */
function ownsProject(project: string) {
  return (query: Query): boolean => {
    const key = query.queryKey;
    return Array.isArray(key) && key[0] === RAIL_KEY && key[1] === project;
  };
}

/** True when a query is the project's snapshot. */
function isSnapshot(query: Query): boolean {
  const key = query.queryKey;
  return Array.isArray(key) && key[0] === RAIL_KEY && key[2] === PROJECT_READS.snapshot;
}

/**
 * The generation of the refreshes one project is showing.
 *
 * Two refreshes can be in flight at once: the user asks for a reload
 * twice, or a write finishes while a reload is running. Their answers
 * arrive in whatever order the server happens to finish them, and the last
 * one to arrive is not the newest one. The generation says which refresh is
 * the current one, so an older answer is dropped instead of overwriting
 * the newer data.
 */
export class RefreshGeneration {
  private current = 0;

  /** Begin a refresh; every refresh started earlier is now superseded. */
  begin(): number {
    this.current += 1;
    return this.current;
  }

  /** The generation of the refresh in force right now. */
  get value(): number {
    return this.current;
  }

  /** True when the refresh that started in `generation` may still land. */
  allows(generation: number): boolean {
    return generation === this.current;
  }
}

export interface RefreshOptions {
  /** The project whose view is refreshed. */
  project: string;
  /** Ask the server for a snapshot that ignores what it remembers. */
  fresh?: boolean;
  /** The guard that decides whether this refresh is still the current one. */
  guard?: RefreshGeneration;
  /** Refetch the reads that are on screen. Defaults to true. */
  refetch?: boolean;
}

export interface SnapshotReader {
  snapshot(fresh?: boolean): Promise<Snapshot>;
}

/**
 * The one refresh.
 *
 * Cancel the reads that are in flight, take one snapshot, seed it, then
 * invalidate the project's reads and refetch the ones that are on screen.
 *
 * The snapshot that was just taken is left alone: refetching it would
 * replace the answer we asked for with a second, later one, and the view
 * would describe two moments at once. A refresh that a newer one has
 * superseded seeds nothing at all and reports that it did not land.
 */
export async function refreshProject(
  queryClient: QueryClient,
  client: SnapshotReader,
  options: RefreshOptions,
): Promise<boolean> {
  const { project, fresh = true, guard, refetch = true } = options;
  const generation = guard ? guard.begin() : undefined;
  const owns = ownsProject(project);
  await queryClient.cancelQueries({ queryKey: projectKey(project) });
  const snapshot = await client.snapshot(fresh);
  if (generation !== undefined && !guard!.allows(generation)) return false;
  queryClient.setQueryData(readKey(project, 'snapshot'), snapshot);
  await queryClient.invalidateQueries({
    predicate: (query) => owns(query) && !isSnapshot(query),
    refetchType: 'none',
  });
  if (refetch) {
    await queryClient.refetchQueries({
      predicate: (query) => owns(query) && !isSnapshot(query),
      type: 'active',
    });
  }
  return true;
}
