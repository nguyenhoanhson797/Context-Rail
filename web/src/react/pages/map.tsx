/* The project map is the declared hierarchy, drawn as a tree with the engine's
   own completion numbers. Containers come from the store's `container` fact and
   nest by `phase`; a container the engine counted uses its counted totals, and
   anything deeper is counted here with the same declared done rule so the two
   never disagree about what "complete" means. */
import { useMemo, useState } from 'react';
import { ArrowRight, ChevronDown, Layers, RotateCcw, Search, Target, X } from '../icons';
import { object, rows, text, type PageProps } from '../contracts';
import { cn } from '../ui';
import {
  CATEGORY_LABEL,
  allItems,
  completionOf,
  hierarchyTreeOf,
  isCompleteItem,
  type CompletionRule,
  type TreeNode,
} from '../capabilities';
import { focusName, hierarchyLevels, labelOf, progressOf, titleCase, unitLabel } from './operating';

const countLeaves = (node: TreeNode, rule: CompletionRule): { total: number; done: number } => {
  let total = 0;
  let done = 0;
  for (const kid of node.kids) {
    if (kid.container) {
      const nested = countLeaves(kid, rule);
      total += nested.total;
      done += nested.done;
    } else if (!rule.of.length || rule.of.includes(kid.preset)) {
      total += 1;
      if (isCompleteItem(kid.doc, rule)) done += 1;
    }
  }
  return { total, done };
};

/* A search keeps the ancestors of every hit, so a match never arrives orphaned
   from the container that explains it. */
function prune(node: TreeNode, needle: string): TreeNode | null {
  const kids = node.kids.map((kid) => prune(kid, needle)).filter((kid): kid is TreeNode => kid !== null);
  const self = !needle || `${node.title} ${node.id} ${node.preset} ${node.status}`.toLowerCase().includes(needle);
  if (!self && !kids.length) return null;
  return { ...node, kids };
}

/* A kind filter keeps the containers that hold a match, so a bug still arrives
   with the sprint that explains it, but never with its unrelated siblings. */
function pruneKind(node: TreeNode, kind: string): TreeNode | null {
  if (kind === 'all') return node;
  const kids = node.kids.map((kid) => pruneKind(kid, kind)).filter((kid): kid is TreeNode => kid !== null);
  if (node.preset === kind) return { ...node, kids };
  if (node.container && kids.length) return { ...node, kids };
  return null;
}

const countNodes = (nodes: TreeNode[]): number =>
  nodes.reduce((sum, node) => sum + 1 + countNodes(node.kids), 0);

export function MapPage({ snapshot, params, navigate, openDoc, busy, refresh }: PageProps) {
  const snap = object(snapshot);
  const progress = progressOf(snapshot);
  const rule = useMemo(() => completionOf(snapshot), [snapshot]);
  const counted = useMemo(() => {
    const map = new Map<string, { total: number; complete: number }>();
    for (const child of rows(progress.children)) {
      map.set(text(child.slug), { total: Number(child.total) || 0, complete: Number(child.complete) || 0 });
    }
    return map;
  }, [progress]);

  const work = useMemo(() => allItems(snap), [snap]);
  const tree = useMemo(() => hierarchyTreeOf(snap), [snap]);
  const levels = hierarchyLevels(snapshot);
  const focus = focusName(snapshot);
  const groupLabel = text(progress.group) ? labelOf(snapshot, text(progress.group), true) : 'Groups';

  const query = params.get('q') ?? '';
  const preset = params.get('kind') ?? 'all';
  const [open, setOpen] = useState<Set<string>>(() => new Set(tree.roots.map((root) => root.id)));
  const [depth, setDepth] = useState(2);

  const needle = query.trim().toLowerCase();
  const roots = useMemo(
    () =>
      tree.roots
        .map((root) => prune(root, needle))
        .filter((root): root is TreeNode => root !== null)
        .map((root) => pruneKind(root, preset))
        .filter((root): root is TreeNode => root !== null),
    [tree, needle, preset],
  );

  /* Unfiled work is filtered by the same needle and kind as the tree, so a
     search never leaves an unrelated row sitting under the results. */
  const loose = useMemo(
    () =>
      tree.loose.filter((node) => {
        if (preset !== 'all' && node.preset !== preset) return false;
        if (!needle) return true;
        return `${node.title} ${node.id} ${node.preset} ${node.status}`.toLowerCase().includes(needle);
      }),
    [tree, needle, preset],
  );

  const presets = useMemo(() => {
    const counts = new Map<string, number>();
    for (const doc of work) {
      const name = text(doc.preset, text(doc.type));
      if (name) counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [work]);

  const total = Number(progress.total) || 0;
  const complete = Number(progress.complete) || 0;
  const pct = total ? Math.round((complete / total) * 100) : 0;
  const byCategory = object(progress.by_category);

  const expandTo = (limit: number) => {
    const next = new Set<string>();
    const walk = (node: TreeNode) => {
      if (node.depth < limit - 1 && node.kids.length) {
        next.add(node.id);
        for (const kid of node.kids) walk(kid);
      }
    };
    for (const root of tree.roots) walk(root);
    setOpen(next);
    setDepth(limit);
  };

  const renderNode = (node: TreeNode) => {
    const isOpen = open.has(node.id) || !!needle;
    const engine = counted.get(node.id);
    const local = countLeaves(node, rule);
    const sum = engine ?? { total: local.total, complete: local.done };
    const share = sum.total ? Math.round((sum.complete / sum.total) * 100) : 0;
    const isFocus = !!focus && (node.title === focus || node.id === focus);
    const hasKids = node.kids.length > 0;
    return (
      <li className={cn('rm-node', node.container && 'branch', isFocus && 'focused')} key={node.id}>
        <div className={cn('rm-row', `cat-${node.category}`)}>
          {node.container ? (
            <button
              type="button"
              className={cn('rm-twist', isOpen && 'on')}
              aria-expanded={isOpen}
              aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${node.title}`}
              disabled={!hasKids}
              onClick={() => toggle(node.id)}
            >
              <ChevronDown size={13} aria-hidden="true" />
            </button>
          ) : (
            <span className="rm-dot" aria-hidden="true" />
          )}
          <button type="button" className="rm-title" onClick={() => openDoc(node.id)}>
            <span className="trunc">{node.title}</span>
            <ArrowRight size={11} aria-hidden="true" />
          </button>
          <span className="rm-preset">{labelOf(snapshot, node.preset)}</span>
          {isFocus ? <span className="rm-focus"><Target size={10} aria-hidden="true" /> focus</span> : null}
          {node.status ? <span className={cn('rm-status', `cat-${node.category}`)}>{node.status}</span> : null}
          {node.container ? (
            <span className="rm-tally">
              <b>{sum.complete}</b>/{sum.total}
            </span>
          ) : null}
          {node.container && sum.total ? (
            <span className="rm-meter" role="img" aria-label={`${share}% complete`}>
              <i style={{ width: `${share}%` }} />
            </span>
          ) : (
            <span className="rm-meter empty" aria-hidden="true" />
          )}
        </div>
        {hasKids && isOpen ? <ul className="rm-kids">{node.kids.map(renderNode)}</ul> : null}
        {!hasKids && node.container && isOpen ? (
          <p className="rm-void">Nothing is filed under this {labelOf(snapshot, node.preset)} yet.</p>
        ) : null}
      </li>
    );
  };

  return (
    <div className="rm-page">
      <header className="rm-head">
        <div>
          <div className="rf-kicker">{`${groupLabel} · ${levels.map((level) => text(level.label, titleCase(text(level.preset)))).join(' → ') || 'flat'}`}</div>
          <h1>Project map</h1>
          <p>{`The whole hierarchy with the engine's completion rule: ${text(progress.group) ? labelOf(snapshot, text(progress.group), true) : 'work'} counted in ${unitLabel(snapshot, true)}.`}</p>
        </div>
        <div className="rm-head-side">
          <div className="rm-odometer">
            <b>{pct}%</b>
            <span>{`${complete} of ${total} ${unitLabel(snapshot, true)} complete`}</span>
            <span className="rm-odometer-bar" role="img" aria-label={`${pct}% complete`}><i style={{ width: `${pct}%` }} /></span>
          </div>
          <button type="button" className="rf-chip" disabled={busy || !refresh} onClick={() => void refresh?.()}>
            <RotateCcw size={13} aria-hidden="true" /> {busy ? 'Reading…' : 'Refresh'}
          </button>
        </div>
      </header>

      <div className="rm-legend">
        {Object.entries(byCategory).map(([name, value]) => (
          <span className={cn('rm-leg', `cat-${name}`)} key={name}>
            <i aria-hidden="true" /> {text(CATEGORY_LABEL[name], titleCase(name))} <b>{Number(value) || 0}</b>
          </span>
        ))}
      </div>

      <div className="rm-toolbar">
        <label className="rm-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            placeholder="Find a container or a work item…"
            aria-label="Search the project map"
            onChange={(event) => setParam('q', event.target.value)}
          />
          {query ? (
            <button type="button" className="rm-clear" aria-label="Clear search" onClick={() => setParam('q', '')}>
              <X size={12} aria-hidden="true" />
            </button>
          ) : null}
        </label>
        <div className="rm-depth" role="group" aria-label="Expansion depth">
          <span><Layers size={12} aria-hidden="true" /> Depth</span>
          {[1, 2, 3, 4].map((level) => (
            <button
              type="button"
              key={level}
              className={cn('rm-depth-btn', depth === level && 'on')}
              aria-pressed={depth === level}
              onClick={() => expandTo(level)}
            >
              {level}
            </button>
          ))}
        </div>
        <button type="button" className="rf-chip" onClick={() => expandTo(4)}>Expand all</button>
        <button type="button" className="rf-chip" onClick={() => { setOpen(new Set()); setDepth(1); }}>Collapse all</button>
        <span className="ra-spacer" />
        <span className="rm-count">{`${roots.length} ${roots.length === 1 ? 'root' : 'roots'} · ${countNodes(roots) + loose.length} of ${work.length} items`}</span>
      </div>

      <div className="rm-presets" role="group" aria-label="Filter by kind">
        <button type="button" className={cn('ra-kindchip', preset === 'all' && 'on')} aria-pressed={preset === 'all'} onClick={() => setParam('kind', '')}>
          All kinds <b>{work.length}</b>
        </button>
        {presets.map(([name, count]) => (
          <button
            type="button"
            key={name}
            className={cn('ra-kindchip', preset === name && 'on')}
            aria-pressed={preset === name}
            onClick={() => setParam('kind', name)}
          >
            {labelOf(snapshot, name, true)} <b>{count}</b>
          </button>
        ))}
      </div>

      <main className="rm-body">
        {!work.length ? (
          <div className="rf-emptystate">
            <b>Nothing to map yet</b>
            <span>The hierarchy appears once the store holds containers and work under them.</span>
          </div>
        ) : !roots.length && !loose.length ? (
          <div className="rf-emptystate">
            <b>Nothing matches this view</b>
            <span>{`${work.length} items are recorded. Clear the search or kind to see them.`}</span>
            <button type="button" className="rf-chip" onClick={() => clearFilters()}>Clear filters</button>
          </div>
        ) : roots.length ? (
          <ul className="rm-tree">{roots.map(renderNode)}</ul>
        ) : null}
        {loose.length ? (
          <section className="rm-loose">
            <div className="rm-loose-head">
              <h2>Not filed under a {text(progress.group) ? labelOf(snapshot, text(progress.group)) : 'container'}</h2>
              <span>{`${loose.length} items`}</span>
            </div>
            <ul className="rm-tree">{loose.map(renderNode)}</ul>
            <p className="rf-note">These items carry no container relation, so the engine counts them outside the hierarchy.</p>
          </section>
        ) : null}
      </main>
    </div>
  );

  function setParam(name: string, next: string) {
    const values: Record<string, string> = {};
    for (const [key, value] of params) values[key] = value;
    if (!next) delete values[name];
    else values[name] = next;
    navigate('lens', values);
  }

  function clearFilters() {
    const values: Record<string, string> = {};
    for (const [key, value] of params) {
      if (key !== 'kind' && key !== 'q') values[key] = value;
    }
    navigate('lens', values);
  }

  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
}
