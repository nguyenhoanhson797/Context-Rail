/* =====================================================================
   ATLAS GRAPH (React): the phase dependency graph and its blast radius.
   Pure (no DOM): the model's phases and edges in, a layout out.
   Source-backed links only: 'authored' is a dependency a phase file states;
   'ordered' is roadmap order only; no edge means unknown, not absent.
   ===================================================================== */
import type { Model, Phase } from './model';

export interface GEdge { src: string; dst: string; kind: string; why: string }
export interface GNode { id: string; ph: Phase | null; program: string; col: number; lane: number; sub: number; x: number; y: number }
export interface GLane { id: string; label: string; y: number; rows: number }
export interface Graph {
  nodes: GNode[];
  edges: GEdge[];
  hidden: number;
  lanes: GLane[];
  W: number;
  H: number;
  out: Map<string, GEdge[]>;
  inn: Map<string, GEdge[]>;
  byId: Map<string, GNode>;
}

export const NW = 148;
export const NH = 70;
export const GX = 24;
export const ROW = NH + 28;
const X0 = 18;
const LANE_H = 24;

export const isFinished = (ph: Phase | null | undefined): boolean =>
  !!ph && (ph.derived === 'done' || (ph.derived === 'no tasks' && ph.recorded === 'done'));
const cache = new WeakMap<Model, { open?: Graph; all?: Graph }>();

/* The graph opens on the frontier: finished phases are hidden unless asked for. */
export function graphOf(M: Model, finishedShown: boolean): Graph {
  const key = finishedShown ? 'all' : 'open';
  const hit = cache.get(M);
  const cached = hit ? hit[key] : undefined;
  if (cached) return cached;
  let ids: string[] = [];
  const seen = new Set<string>();
  const add = (id: string) => {
    if (id && !seen.has(id)) {
      seen.add(id);
      ids.push(id);
    }
  };
  for (const e of M.edges) {
    add(e.src);
    add(e.dst);
  }
  for (const p of M.programs)
    if (p.vis === 'blocked')
      for (const id of p.phases) {
        const ph = M.PH.get(id);
        if (ph && ph.derived !== 'done') add(id);
      }
  let hidden = 0;
  if (!finishedShown) {
    const live = new Set(ids.filter((id) => !isFinished(M.PH.get(id))));
    const keep = new Set(live);
    for (const e of M.edges) if (e.kind === 'authored' && live.has(e.dst)) keep.add(e.src);
    hidden = ids.length - ids.filter((id) => keep.has(id)).length;
    ids = ids.filter((id) => keep.has(id));
  }  const shown = new Set(ids);
  const edges = M.edges.filter((e) => shown.has(e.src) && shown.has(e.dst));
  const out = new Map<string, GEdge[]>(ids.map((id) => [id, []]));
  const inn = new Map<string, GEdge[]>(ids.map((id) => [id, []]));
  for (const e of edges) {
    out.get(e.src)?.push(e);
    inn.get(e.dst)?.push(e);
  }
  const col = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    const known = col.get(id);
    if (known != null) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let d = 0;
    for (const e of inn.get(id) || []) d = Math.max(d, depth(e.src) + 1);
    visiting.delete(id);
    col.set(id, d);
    return d;
  };
  ids.forEach(depth);
  [...ids]
    .sort((a, b) => (col.get(b) || 0) - (col.get(a) || 0))
    .forEach((id) => {
      const next = (out.get(id) || []).map((e) => col.get(e.dst) || 0).filter((c) => c > (col.get(id) || 0));
      if (next.length) col.set(id, Math.min(...next) - 1);
    });  const programOf = (id: string): string => {
    const ph = M.PH.get(id);
    return ph && M.P.get(ph.program) ? ph.program : '';
  };
  const laneIds = [...new Set(ids.map(programOf))].sort(
    (a, b) => (a ? M.P.get(a)?.index ?? 0 : 1e6) - (b ? M.P.get(b)?.index ?? 0 : 1e6),
  );
  const nodes: GNode[] = ids.map((id) => {
    const ph = M.PH.get(id) || null;
    return {
      id,
      ph,
      program: programOf(id),
      col: col.get(id) || 0,
      lane: laneIds.indexOf(programOf(id)),
      sub: 0,
      x: 0,
      y: 0,
    };
  });
  const laneRows = laneIds.map((_, li) => {
    const per = new Map<number, number>();
    for (const n of nodes.filter((q) => q.lane === li)) {
      n.sub = per.get(n.col) || 0;
      per.set(n.col, n.sub + 1);
    }
    return Math.max(1, ...[...per.values()]);
  });
  let y = 0;
  const lanes: GLane[] = laneIds.map((pid, li) => {
    const p = pid ? M.P.get(pid) : undefined;
    const lane = { id: pid, label: p ? p.label : 'Not placed in a program', y, rows: laneRows[li] };
    y += LANE_H + laneRows[li] * ROW;
    return lane;
  });
  const maxCol = Math.max(0, ...nodes.map((n) => n.col));
  for (const n of nodes) {
    n.x = X0 + n.col * (NW + GX);
    n.y = lanes[n.lane].y + LANE_H + n.sub * ROW;
  }
  const G: Graph = {
    nodes,
    edges,
    hidden,
    lanes,
    W: X0 * 2 + (maxCol + 1) * NW + maxCol * GX,
    H: Math.max(y, ROW),
    out,
    inn,
    byId: new Map(nodes.map((n) => [n.id, n])),
  };
  cache.set(M, { ...(hit || {}), [key]: G });
  return G;
}
/* Everything reachable from a step, following the dependency forward (down) or backward (up). */
export function reach(G: Graph, id: string, up: boolean): Set<string> {
  const seen = new Set<string>();
  const queue = [id];
  while (queue.length) {
    const cur = queue.shift();
    if (cur == null) break;
    for (const e of (up ? G.inn : G.out).get(cur) || []) {
      const next = up ? e.src : e.dst;
      if (next !== id && !seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return seen;
}

export const nameOf = (M: Model, id: string): string => {
  const ph = M.PH.get(id);
  return ph ? ph.title : id;
};
export const plural = (n: number, one: string, many = one + 's'): string => (n === 1 ? one : many);

/* What a slip of one step costs: the parts a reader needs, already counted. */
export interface Blast {
  name: string;
  down: string[];
  openTasks: number;
  scopeItems: number;
  ordered: number;
  up: string[];
}
export function blast(M: Model, G: Graph, id: string): Blast | null {
  if (!G.byId.has(id)) return null;
  const down = reach(G, id, false);
  const up = reach(G, id, true);
  const dph = [...down].filter((i) => M.PH.has(i));
  const open = M.tasks.filter((t) => dph.includes(t.phase) && t.status !== 'done');
  const items = new Set<string>();
  for (const t of open) for (const x of t.implements) items.add(x);
  const ordered = G.edges.filter((e) => down.has(e.dst) && (e.src === id || down.has(e.src)) && e.kind === 'ordered').length;
  return {
    name: nameOf(M, id),
    down: [...down].map((i) => nameOf(M, i)),
    openTasks: open.length,
    scopeItems: items.size,
    ordered,
    up: [...up].map((i) => nameOf(M, i)),
  };
}

/* A title cut into at most lines lines of width characters, the last one ellipsised. */
export function wrap(text: string, width = 19, lines = 2): string[] {
  const words = String(text || '')
    .split(/\s+/)
    .filter(Boolean);
  const out: string[] = [];
  let cur = '';
  for (const w of words) {
    if ((cur + ' ' + w).trim().length <= width) cur = (cur + ' ' + w).trim();
    else {
      if (cur) out.push(cur);
      cur = w;
    }
  }
  if (cur) out.push(cur);
  const cut = out.slice(0, lines).map((l) => (l.length > width ? l.slice(0, width - 1) + ELL : l));
  if (out.length > lines) cut[lines - 1] = cut[lines - 1].replace(new RegExp(ELL + '?$'), ELL);
  return cut.length ? cut : [''];
}
const ELL = '…';