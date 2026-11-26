/* The project map renders the engine's `map` read: the same resolver behind
   `rail read map`, so the screen and an agent see one tree. Placement, markers
   (focus, path, next, blocked) and the completion rollup all come from that
   read; the page only folds, filters and selects. The container hierarchy and
   the progress rule stay the pack's, through the compiled model. The canvas
   and the outline are two drawings of the same read. */
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowRight, ChevronDown, GitBranch, History, Layers, ListChecks, MapIcon, RotateCcw, Search, Target, X } from '../icons';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { cn } from '../ui';
import { CATEGORY_LABEL } from '../capabilities';
import { hierarchyLevels, progressOf, titleCase, unitLabel } from './operating';

const MapCanvas = lazy(() => import('./map-canvas'));

export interface MapNode {
  slug: string;
  title: string;
  preset: string;
  label: string;
  status: string;
  category: string;
  parent: string | null;
  also: string[];
  depth: number;
  below: number;
  rollup: { complete: number; total: number } | null;
  markers: string[];
  attachments: number;
  updated: string;
  etag: string;
}

interface MapRead {
  nodes: MapNode[];
  roots: string[];
  where: Data;
  needs: Map<string, string[]>;
  neededBy: Map<string, string[]>;
}

export const MARKER_LABEL: Record<string, string> = {
  focus: 'Focus',
  path: 'Path',
  next: 'Next',
  blocked: 'Blocked',
  changed: 'Changed',
};

const VISIT_KEY = 'rail.map.visit.';
const VIEW_KEY = 'rail.map.view';

type View = 'canvas' | 'outline';

interface Proposal {
  src: string;
  dst: string;
}

function parseMap(value: unknown): MapRead {
  const data = object(value);
  const nodes = rows(data.nodes).map((row): MapNode => {
    const rollup = object(row.rollup);
    return {
      slug: text(row.slug),
      title: text(row.title, text(row.slug)),
      preset: text(row.preset),
      label: text(row.label, titleCase(text(row.preset))),
      status: text(row.status),
      category: text(row.category, 'unknown'),
      parent: text(row.parent) || null,
      also: Array.isArray(row.also) ? row.also.map((slug) => text(slug)).filter(Boolean) : [],
      depth: Number(row.depth) || 0,
      below: Number(row.below) || 0,
      rollup: row.rollup ? { complete: Number(rollup.complete) || 0, total: Number(rollup.total) || 0 } : null,
      markers: Array.isArray(row.markers) ? row.markers.map((marker) => text(marker)) : [],
      attachments: Number(row.attachments) || 0,
      updated: text(row.updated),
      etag: text(row.etag),
    };
  });
  const needs = new Map<string, string[]>();
  const neededBy = new Map<string, string[]>();
  for (const pair of Array.isArray(data.depends_on) ? data.depends_on : []) {
    if (!Array.isArray(pair) || pair.length !== 2) continue;
    const [src, dst] = [text(pair[0]), text(pair[1])];
    needs.set(src, [...(needs.get(src) ?? []), dst]);
    neededBy.set(dst, [...(neededBy.get(dst) ?? []), src]);
  }
  const roots = Array.isArray(data.roots) ? data.roots.map((slug) => text(slug)) : [];
  return { nodes, roots, where: object(data.where), needs, neededBy };
}

function lastVisit(project: string): string {
  try {
    return window.localStorage.getItem(VISIT_KEY + project) ?? '';
  } catch {
    return '';
  }
}

function savedView(): View {
  try {
    return window.localStorage.getItem(VIEW_KEY) === 'outline' ? 'outline' : 'canvas';
  } catch {
    return 'canvas';
  }
}

function rememberView(view: View): void {
  try {
    window.localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* without storage the canvas is simply the default every visit */
  }
}

function rememberVisit(project: string, stamp: string): void {
  try {
    window.localStorage.setItem(VISIT_KEY + project, stamp);
  } catch {
    /* storage is a convenience: without it the toggle simply has no baseline */
  }
}

export function MapPage({ snapshot, project, params, navigate, openDoc, read, mutate, busy, refresh, theme }: PageProps) {
  const [data, setData] = useState<MapRead | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<Set<string> | null>(null);
  const [depth, setDepth] = useState(2);
  const [since] = useState(() => lastVisit(project));
  const [showChanged, setShowChanged] = useState(false);
  const [view, setView] = useState<View>(savedView);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [reason, setReason] = useState('');
  const [moveError, setMoveError] = useState('');
  const [moving, setMoving] = useState(false);
  const [moveTo, setMoveTo] = useState('');

  const load = useCallback(async () => {
    try {
      const value = await read('map');
      if (text(object(value).status) === 'error') throw new Error(text(object(value).error, 'the map read failed'));
      setData(parseMap(value));
      setError('');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }, [read]);

  useEffect(() => {
    void load();
  }, [load, snapshot]);

  useEffect(() => {
    if (data) rememberVisit(project, new Date().toISOString().replace(/\.\d+Z$/, 'Z'));
  }, [data, project]);

  const progress = progressOf(snapshot);
  const levels = hierarchyLevels(snapshot);
  const query = params.get('q') ?? '';
  const selected = params.get('item') ?? '';
  const needle = query.trim().toLowerCase();

  const bySlug = useMemo(() => new Map((data?.nodes ?? []).map((node) => [node.slug, node])), [data]);
  const kids = useMemo(() => {
    const map = new Map<string, MapNode[]>();
    for (const node of data?.nodes ?? []) {
      if (node.parent) map.set(node.parent, [...(map.get(node.parent) ?? []), node]);
    }
    return map;
  }, [data]);

  const changed = useMemo(() => {
    const found = new Set<string>();
    if (!since) return found;
    for (const node of data?.nodes ?? []) if (node.updated && node.updated >= since) found.add(node.slug);
    return found;
  }, [data, since]);

  const trail = useCallback(
    (slug: string): MapNode[] => {
      const chain: MapNode[] = [];
      const seen = new Set<string>();
      let current = bySlug.get(slug)?.parent ?? null;
      while (current && !seen.has(current)) {
        seen.add(current);
        const node = bySlug.get(current);
        if (!node) break;
        chain.unshift(node);
        current = node.parent;
      }
      return chain;
    },
    [bySlug],
  );

  /* A find or the changed filter keeps every hit with its ancestors, so a hit
     never arrives without the branch that explains it. */
  const visible = useMemo(() => {
    if (!needle && !showChanged) return null;
    const words = needle.split(/\s+/).filter(Boolean);
    const keep = new Set<string>();
    for (const node of data?.nodes ?? []) {
      const hay = `${node.title} ${node.slug}`.toLowerCase();
      const hit = words.every((word) => hay.includes(word)) && (!showChanged || changed.has(node.slug));
      if (!hit) continue;
      keep.add(node.slug);
      for (const parent of trail(node.slug)) keep.add(parent.slug);
    }
    return keep;
  }, [data, needle, showChanged, changed, trail]);

  const hits = useMemo(() => {
    const found = new Set<string>();
    if (!needle) return found;
    const words = needle.split(/\s+/).filter(Boolean);
    for (const node of data?.nodes ?? []) {
      if (words.every((word) => `${node.title} ${node.slug}`.toLowerCase().includes(word))) found.add(node.slug);
    }
    return found;
  }, [data, needle]);

  /* A branch never moves under itself: the engine refuses the cycle, and the
     choice list never offers one. */
  const descendants = useCallback(
    (slug: string): Set<string> => {
      const found = new Set<string>([slug]);
      const stack = [slug];
      while (stack.length) {
        for (const child of kids.get(stack.pop()!) ?? []) {
          if (found.has(child.slug)) continue;
          found.add(child.slug);
          stack.push(child.slug);
        }
      }
      return found;
    },
    [kids],
  );

  /* The default fold opens the requested depth and always the focus branch. */
  const defaultOpen = useMemo(() => {
    const next = new Set<string>();
    for (const node of data?.nodes ?? []) {
      if (node.depth < depth - 1 || node.markers.includes('path')) next.add(node.slug);
    }
    if (selected) for (const parent of trail(selected)) next.add(parent.slug);
    return next;
  }, [data, depth, selected, trail]);
  const opened = open ?? defaultOpen;
  const toggle = useCallback(
    (slug: string) => {
      setOpen((current) => {
        const next = new Set(current ?? defaultOpen);
        if (next.has(slug)) next.delete(slug);
        else next.add(slug);
        return next;
      });
    },
    [defaultOpen],
  );

  const where = object(data?.where);
  const whereProgress = object(where.progress);
  const total = Number(whereProgress.total ?? progress.total) || 0;
  const complete = Number(whereProgress.complete ?? progress.complete) || 0;
  const pct = total ? Math.round((complete / total) * 100) : 0;
  const focusTrail = [...rows(where.path), ...(where.focus ? [object(where.focus)] : [])];
  const nextUp = rows(where.next);
  const active = selected ? bySlug.get(selected) : undefined;

  const renderNode = (node: MapNode) => {
    if (visible && !visible.has(node.slug)) return null;
    const children = kids.get(node.slug) ?? [];
    const isOpen = !!visible || opened.has(node.slug);
    const markers = changed.has(node.slug) ? [...node.markers, 'changed'] : node.markers;
    const share = node.rollup?.total ? Math.round((node.rollup.complete / node.rollup.total) * 100) : 0;
    const container = children.length > 0 || node.rollup !== null;
    return (
      <li
        className={cn('rm-node', container && 'branch', markers.includes('focus') && 'focused', node.slug === selected && 'selected')}
        key={node.slug}
      >
        <div className={cn('rm-row', `cat-${node.category}`)}>
          {children.length ? (
            <button
              type="button"
              className={cn('rm-twist', isOpen && 'on')}
              aria-expanded={isOpen}
              aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${node.title}`}
              onClick={() => toggle(node.slug)}
            >
              <ChevronDown size={13} aria-hidden="true" />
            </button>
          ) : (
            <span className="rm-dot" aria-hidden="true" />
          )}
          <button type="button" className="rm-title" onClick={() => select(node.slug)}>
            <span className="trunc">{node.title}</span>
            {!isOpen && node.below ? <span className="rm-below">{`+${node.below}`}</span> : null}
          </button>
          <span className="rm-preset">{node.label}</span>
          <span className="rm-marks">
            {markers.filter((marker) => marker !== 'path').map((marker) => (
              <span className={cn('rm-mark', `mark-${marker}`)} key={marker}>
                {marker === 'focus' ? <Target size={10} aria-hidden="true" /> : null}
                {MARKER_LABEL[marker] ?? marker}
              </span>
            ))}
          </span>
          {node.status ? <span className={cn('rm-status', `cat-${node.category}`)}>{node.status}</span> : <span />}
          {node.rollup ? (
            <span className="rm-tally">
              <b>{node.rollup.complete}</b>/{node.rollup.total}
            </span>
          ) : (
            <span />
          )}
          {node.rollup?.total ? (
            <span className="rm-meter" role="img" aria-label={`${share}% complete`}>
              <i style={{ width: `${share}%` }} />
            </span>
          ) : (
            <span className="rm-meter empty" aria-hidden="true" />
          )}
        </div>
        {children.length && isOpen ? <ul className="rm-kids">{children.map(renderNode)}</ul> : null}
      </li>
    );
  };

  const roots = (data?.roots ?? []).map((slug) => bySlug.get(slug)).filter((node): node is MapNode => !!node);
  const shown = visible ? visible.size : data?.nodes.length ?? 0;

  return (
    <div className="rm-page">
      <header className="rm-head">
        <div>
          <div className="rf-kicker">{levels.map((level) => text(level.label, titleCase(text(level.preset)))).join(' > ') || 'Project'}</div>
          <h1>Project map</h1>
          <nav className="rm-where" aria-label="Where we are">
            <span>Where we are</span>
            {focusTrail.length ? (
              focusTrail.map((step, index) => (
                <button type="button" key={text(step.slug)} className={cn('rm-crumb', index === focusTrail.length - 1 && 'on')} onClick={() => select(text(step.slug))}>
                  {text(step.title, text(step.slug))}
                </button>
              ))
            ) : (
              <em>{text(where.focus_state) === 'ambiguous' ? 'the focus is ambiguous' : 'no focus container: the whole project is in scope'}</em>
            )}
          </nav>
          {nextUp.length ? (
            <p className="rm-next">
              Next:{' '}
              {nextUp.map((step) => (
                <button type="button" key={text(step.slug)} className="rm-crumb" onClick={() => select(text(step.slug))}>
                  {text(step.title, text(step.slug))}
                </button>
              ))}
            </p>
          ) : null}
        </div>
        <div className="rm-head-side">
          <div className="rm-odometer">
            <b>{pct}%</b>
            <span>{`${complete} of ${total} ${text(whereProgress.label) || unitLabel(snapshot, true)} complete`}</span>
            <span className="rm-odometer-bar" role="img" aria-label={`${pct}% complete`}><i style={{ width: `${pct}%` }} /></span>
          </div>
          <button type="button" className="rf-chip" disabled={busy} onClick={() => void (refresh ? refresh() : load())}>
            <RotateCcw size={13} aria-hidden="true" /> {busy ? 'Reading...' : 'Refresh'}
          </button>
        </div>
      </header>

      <div className="rm-legend">
        {Object.entries(object(progress.by_category)).map(([name, value]) => (
          <span className={cn('rm-leg', `cat-${name}`)} key={name}>
            <i aria-hidden="true" /> {text(CATEGORY_LABEL[name], titleCase(name))} <b>{Number(value) || 0}</b>
          </span>
        ))}
      </div>

      <div className="rm-toolbar">
        <div className="rm-depth" role="group" aria-label="Map view">
          <button
            type="button"
            className={cn('rm-depth-btn rm-view-btn', view === 'canvas' && 'on')}
            aria-pressed={view === 'canvas'}
            onClick={() => changeView('canvas')}
          >
            <MapIcon size={13} aria-hidden="true" /> Canvas
          </button>
          <button
            type="button"
            className={cn('rm-depth-btn rm-view-btn', view === 'outline' && 'on')}
            aria-pressed={view === 'outline'}
            onClick={() => changeView('outline')}
          >
            <ListChecks size={13} aria-hidden="true" /> Outline
          </button>
        </div>
        <label className="rm-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            placeholder="Find in the map: every word must match a title or slug"
            aria-label="Find in the project map"
            onChange={(event) => setParam('q', event.target.value)}
          />
          {query ? (
            <button type="button" className="rm-clear" aria-label="Clear the find" onClick={() => setParam('q', '')}>
              <X size={12} aria-hidden="true" />
            </button>
          ) : null}
        </label>
        <div className="rm-depth" role="group" aria-label="Unfold depth">
          <span><Layers size={12} aria-hidden="true" /> Depth</span>
          {[1, 2, 3, 4].map((level) => (
            <button
              type="button"
              key={level}
              className={cn('rm-depth-btn', depth === level && !open && 'on')}
              aria-pressed={depth === level && !open}
              onClick={() => { setDepth(level); setOpen(null); }}
            >
              {level}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={cn('rf-chip', showChanged && 'on')}
          aria-pressed={showChanged}
          disabled={!since}
          title={since ? `Changed since ${since}` : 'The first visit has no earlier baseline'}
          onClick={() => setShowChanged((value) => !value)}
        >
          <History size={13} aria-hidden="true" /> {`Since last visit${since ? ` (${changed.size})` : ''}`}
        </button>
        <button type="button" className="rf-chip" onClick={() => setOpen(new Set((data?.nodes ?? []).map((node) => node.slug)))}>Expand all</button>
        <button type="button" className="rf-chip" onClick={() => setOpen(new Set())}>Collapse all</button>
        <span className="ra-spacer" />
        <span className="rm-count">{`${shown} of ${data?.nodes.length ?? 0} items`}</span>
      </div>

      {proposal ? (
        <MoveBar
          proposal={proposal}
          nodes={bySlug}
          below={descendants(proposal.src).size - 1}
          reason={reason}
          setReason={setReason}
          error={moveError}
          moving={moving}
          confirm={() => void moveBranch()}
          cancel={() => { setProposal(null); setMoveError(''); }}
        />
      ) : null}

      <div className={cn('rm-layout', active && 'with-panel', view === 'canvas' && 'on-canvas')}>
        <main className="rm-body">
          {error ? (
            <div className="rf-emptystate">
              <b>The map could not be read</b>
              <span>{error}</span>
              <button type="button" className="rf-chip" onClick={() => void load()}>Try again</button>
            </div>
          ) : !data ? (
            <div className="rf-emptystate"><b>Reading the map...</b></div>
          ) : !data.nodes.length ? (
            <div className="rf-emptystate">
              <b>Nothing to map yet</b>
              <span>The tree appears once the store holds items placed with part_of under a container.</span>
            </div>
          ) : visible && !visible.size ? (
            <div className="rf-emptystate">
              <b>Nothing matches</b>
              <span>{`${data.nodes.length} items are mapped. Clear the find or the since filter to see them.`}</span>
              <button type="button" className="rf-chip" onClick={() => { setShowChanged(false); setParam('q', ''); }}>Clear filters</button>
            </div>
          ) : view === 'canvas' ? (
            <Suspense fallback={<div className="rf-emptystate"><b>Drawing the canvas...</b></div>}>
              <MapCanvas
                nodes={data.nodes}
                roots={data.roots}
                kids={kids}
                opened={opened}
                visible={visible}
                hits={hits}
                changed={changed}
                needs={data.needs}
                selected={selected}
                theme={theme}
                canMove={!busy && !moving}
                select={select}
                toggle={toggle}
                propose={propose}
              />
            </Suspense>
          ) : (
            <ul className="rm-tree">{roots.map(renderNode)}</ul>
          )}
        </main>
        {active ? (
          <aside className="rm-panel" aria-label={`Selected: ${active.title}`}>
            <div className="rm-panel-head">
              <nav className="rm-trail" aria-label="Breadcrumb">
                {trail(active.slug).map((step) => (
                  <button type="button" key={step.slug} className="rm-crumb" onClick={() => select(step.slug)}>{step.title}</button>
                ))}
              </nav>
              <button type="button" className="rm-clear" aria-label="Close the panel" onClick={() => setParam('item', '')}>
                <X size={12} aria-hidden="true" />
              </button>
            </div>
            <h2>{active.title}</h2>
            <p className="rm-panel-meta">
              <span className="rm-preset">{active.label}</span>
              {active.status ? <span className={cn('rm-status', `cat-${active.category}`)}>{active.status}</span> : null}
              {active.rollup ? <span className="rm-tally"><b>{active.rollup.complete}</b>/{active.rollup.total} done</span> : null}
            </p>
            {active.markers.length || changed.has(active.slug) ? (
              <p className="rm-marks">
                {[...active.markers, ...(changed.has(active.slug) ? ['changed'] : [])].map((marker) => (
                  <span className={cn('rm-mark', `mark-${marker}`)} key={marker}>{MARKER_LABEL[marker] ?? marker}</span>
                ))}
              </p>
            ) : null}
            <PanelList title="Children" slugs={(kids.get(active.slug) ?? []).map((node) => node.slug)} nodes={bySlug} select={select} />
            <PanelList title="Also under" slugs={active.also} nodes={bySlug} select={select} />
            <PanelList title="Needs" slugs={data?.needs.get(active.slug) ?? []} nodes={bySlug} select={select} />
            <PanelList title="Needed by" slugs={data?.neededBy.get(active.slug) ?? []} nodes={bySlug} select={select} />
            <p className="rm-panel-note">{`${active.attachments} attached note(s), check(s) and record(s).`}</p>
            <div className="rm-panel-actions">
              <button type="button" className="rf-chip" onClick={() => openDoc(active.slug)}>
                Open document <ArrowRight size={11} aria-hidden="true" />
              </button>
            </div>
            <section className="rm-panel-list">
              <h3>Move this branch</h3>
              <div className="rm-move">
                <select
                  value={moveTo}
                  aria-label={`New parent for ${active.title}`}
                  onChange={(event) => setMoveTo(event.target.value)}
                >
                  <option value="">Choose a new parent...</option>
                  {parentChoices(active.slug).map((node) => (
                    <option key={node.slug} value={node.slug}>{`${'  '.repeat(node.depth)}${node.title} (${node.label})`}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="rf-chip"
                  disabled={!moveTo || busy || moving}
                  onClick={() => propose(active.slug, moveTo)}
                >
                  <GitBranch size={12} aria-hidden="true" /> Move...
                </button>
              </div>
            </section>
            <p className="rm-panel-note">
              Agents read this branch with <code>{`rail read map ${active.slug}`}</code> and move it with{' '}
              <code>{`rail change link ${active.slug} part_of <parent> --replace --expect ${active.etag || '<etag>'}`}</code>.
            </p>
          </aside>
        ) : null}
      </div>
    </div>
  );

  function select(slug: string) {
    setParam('item', slug === selected ? '' : slug);
    setMoveTo('');
  }

  function changeView(next: View) {
    setView(next);
    rememberView(next);
  }

  function parentChoices(slug: string): MapNode[] {
    const own = descendants(slug);
    const current = bySlug.get(slug)?.parent;
    return (data?.nodes ?? []).filter((node) => !own.has(node.slug) && node.slug !== current);
  }

  function propose(src: string, dst: string) {
    setMoveError('');
    setProposal({ src, dst });
  }

  /* The engine decides: placement rules, cycles, reach and the etag all refuse
     here exactly as they refuse an agent's `link --replace`. */
  async function moveBranch() {
    if (!proposal) return;
    const node = bySlug.get(proposal.src);
    if (!node) return;
    setMoving(true);
    setMoveError('');
    try {
      await mutate('link', {
        src: proposal.src,
        rel: 'part_of',
        dst: proposal.dst,
        expect: node.etag,
        replace: true,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      });
      setProposal(null);
      setReason('');
      setMoveTo('');
    } catch (failure) {
      setMoveError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setMoving(false);
    }
  }

  function setParam(name: string, next: string) {
    const values: Record<string, string> = {};
    for (const [key, value] of params) values[key] = value;
    if (!next) delete values[name];
    else values[name] = next;
    navigate('lens', values);
  }

}

function MoveBar({
  proposal,
  nodes,
  below,
  reason,
  setReason,
  error,
  moving,
  confirm,
  cancel,
}: {
  proposal: Proposal;
  nodes: Map<string, MapNode>;
  below: number;
  reason: string;
  setReason: (value: string) => void;
  error: string;
  moving: boolean;
  confirm: () => void;
  cancel: () => void;
}) {
  const src = nodes.get(proposal.src);
  const dst = nodes.get(proposal.dst);
  const from = src?.parent ? nodes.get(src.parent) : undefined;
  return (
    <div className="rm-movebar" role="dialog" aria-label="Confirm the branch move">
      <p>
        Move <b>{src?.title ?? proposal.src}</b>
        {below ? ` and ${below} item(s) below it` : ''}
        {from ? <> from <b>{from.title}</b></> : null} under <b>{dst?.title ?? proposal.dst}</b>?
        <span className="rm-movebar-note">Its files move with it; one relocation record is written.</span>
      </p>
      <input
        type="text"
        value={reason}
        placeholder="Reason (recorded with the change)"
        aria-label="Reason for the move"
        onChange={(event) => setReason(event.target.value)}
      />
      <button type="button" className="rf-chip on" disabled={moving} onClick={confirm}>
        {moving ? 'Moving...' : 'Move branch'}
      </button>
      <button type="button" className="rf-chip" disabled={moving} onClick={cancel}>Cancel</button>
      {error ? <p className="rm-movebar-error" role="alert">{error}</p> : null}
    </div>
  );
}

function PanelList({
  title,
  slugs,
  nodes,
  select,
}: {
  title: string;
  slugs: string[];
  nodes: Map<string, MapNode>;
  select: (slug: string) => void;
}) {
  const known = slugs.map((slug) => nodes.get(slug)).filter((node): node is MapNode => !!node);
  if (!known.length) return null;
  return (
    <section className="rm-panel-list">
      <h3>{`${title} (${known.length})`}</h3>
      <ul>
        {known.map((node) => (
          <li key={node.slug}>
            <button type="button" className="rm-crumb" onClick={() => select(node.slug)}>{node.title}</button>
            <span className={cn('rm-status', `cat-${node.category}`)}>{node.status}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
