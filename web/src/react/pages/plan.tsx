/* Plan is the weekly screen: the plan view's pinned containers, the work graph
   under them, and the sequence the pack declares. A missing parent is shown as
   a gap, never repaired by inference. */
import { useMemo, useState, type CSSProperties } from 'react';
import { ArrowRight, CircleDashed, GitBranch, Layers, Target, TriangleAlert } from '../icons';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { cn } from '../ui';
import { StatusGlyph } from '../overlays';
import {
  allItems,
  focusCandidates,
  focusFact,
  focusState,
  hasFocus,
  hasOptionalHierarchy,
  hasOrderedHierarchy,
  hierarchyLevels,
  inCompiledOrder,
  itemById,
  itemsNeedingGate,
  orphansOf,
  planScreen,
  populationPresets,
  treeOf,
  workflowForPreset,
  workflowStatuses,
  type TreeRow,
} from '../capabilities';
import { focusName, labelOf, screenTitle, titleCase } from './operating';

const presetOf = (doc: Data): string => text(doc.preset, text(doc.type));
const whyText = (why: string): string =>
  why === 'preset' ? 'matches the declared queue presets' : why === 'transition' ? 'a transition is available' : why;

function PlanRow({
  row,
  snapshot,
  openDoc,
  selected,
}: {
  row: TreeRow;
  snapshot: PageProps['snapshot'];
  openDoc: (id: string) => void;
  selected: string;
}) {
  const item = row.doc;
  const id = text(item.id);
  const preset = presetOf(item);
  const workflow = workflowForPreset(snapshot, preset);
  const owed = itemsNeedingGate(snapshot, preset, text(item.status));
  const container = item.container === true;
  const category = text(item.category);
  const isSelected = selected === id;
  const title = text(item.title, id);
  return (
    <button
      type="button"
      className={cn('rp-row', isSelected && 'sel', row.depth > 0 && 'child')}
      style={{ paddingLeft: 8 + row.depth * 18 }}
      onClick={() => openDoc(id)}
      aria-current={isSelected ? 'true' : undefined}
    >
      <span className="rp-rail" aria-hidden="true">
        <span className={cn('rp-dot', category === 'done' && 'done', category === 'active' && 'active', category === 'blocked' && 'blocked')} />
      </span>
      <StatusGlyph status={text(item.status)} statuses={workflow ? workflowStatuses(snapshot, workflow) : []} category={category} size={12} />
      <span className="rp-title trunc" title={title}>
        {container ? <b>{title}</b> : title}
      </span>
      {owed.length ? <span className="rf-chip rf-chip-gate">gate at {owed.join(', ')}</span> : null}
      <span className="rp-meta">{labelOf(snapshot, preset)}</span>
      <span className={cn('rp-status', `rp-status-${category}`)}>{titleCase(text(item.status))}</span>
    </button>
  );
}

function SequenceLens({ roots, snapshot, openDoc, selected }: { roots: Data[]; snapshot: PageProps['snapshot']; openDoc: (id: string) => void; selected: string }) {
  const ordered = inCompiledOrder(roots);
  return (
    <div className="rp-sequence">
      {ordered.map((root) => {
        const subtree = treeOf(snapshot, [root]);
        const children = subtree.slice(1);
        const rows = children.length ? children : subtree;
        return (
          <div className="rp-lane" key={text(root.id)}>
            <button type="button" className="rp-lane-name" onClick={() => openDoc(text(root.id))}>
              <span className="trunc">{text(root.title, text(root.id))}</span>
              <span className="faint">{rows.length}</span>
            </button>
            <div className="rp-lane-bars">
              {rows.map((entry, index) => {
                const share = 100 / Math.max(rows.length, 1);
                const category = text(entry.doc.category);
                return (
                  <button
                    type="button"
                    key={`${text(entry.doc.id)}-${index}`}
                    className={cn('rp-bar', category === 'done' && 'done', category === 'blocked' && 'blocked', selected === text(entry.doc.id) && 'sel')}
                    style={{ '--bar-left': `${index * share}%`, '--bar-width': `${Math.max(share - 1.2, 4)}%` } as CSSProperties}
                    title={`${text(entry.doc.title, text(entry.doc.id))} · ${titleCase(text(entry.doc.status))}`}
                    onClick={() => openDoc(text(entry.doc.id))}
                  >
                    <span>{text(entry.doc.title, text(entry.doc.id))}</span>
                  </button>
                );
              })}
              {!rows.length ? <span className="faint">no work under this container</span> : null}
            </div>
          </div>
        );
      })}
      <p className="rf-note">
        {hasOrderedHierarchy(snapshot)
          ? 'Lanes follow the declared order. A date axis appears only when a temporal field is declared.'
          : 'Lanes are a sequence, not a dated schedule: this pack declares no order or temporal field.'}
      </p>
    </div>
  );
}

export function PlanPage({ snapshot, params, navigate, openDoc }: PageProps) {
  const screen = planScreen(snapshot);
  const population = populationPresets(snapshot, screen);
  const hierarchy = hierarchyLevels(snapshot);
  const planPreset = population[0] ?? text(hierarchy[0]?.preset);
  const roots = useMemo(() => allItems(snapshot).filter((item) => population.includes(presetOf(item))), [snapshot, population]);
  const tree = useMemo(() => treeOf(snapshot, inCompiledOrder(roots)), [snapshot, roots]);
  const orphanRoots = useMemo(() => {
    /* A top-level container is a root, not an orphan. Only the working presets
       below the declared containers can be unfiled. */
    const containers = new Set([...population, ...hierarchy.map((level) => text(level.preset))]);
    const working = [...new Set(allItems(snapshot).map(presetOf))].filter((preset) => preset && !containers.has(preset));
    return orphansOf(snapshot, working);
  }, [snapshot, population, hierarchy]);
  const selected = params.get('doc') ?? '';
  const lensParam = params.get('lens') ?? 'tree';
  const lens = lensParam === 'sequence' ? 'sequence' : 'tree';
  const focus = focusName(snapshot);
  const focusIsAmbiguous = focusState(snapshot) === 'ambiguous';
  const candidates = focusCandidates(snapshot);
  const queue = rows(object(object(snapshot.operating).queue).items);
  const byId = useMemo(() => itemById(snapshot), [snapshot]);
  const planLabel = labelOf(snapshot, planPreset, true);

  const [notice] = useState('');
  const selectedItem = selected ? byId.get(selected) : undefined;

  return (
    <div className="rp-page">
      <header className="rp-head">
        <div>
          <div className="rf-kicker">{`${planLabel} · ${hasOptionalHierarchy(snapshot) ? 'optional containment' : 'required containment'}`}</div>
          <h1>{screen?.title ?? screenTitle(snapshot, 'roadmap', 'Plan')}</h1>
          <p>{`${titleCase(planPreset || 'container')} → work. Missing parents stay visible; the plan never re-parents by inference.`}</p>
        </div>
        <div className="rp-head-side">
          <span className="rf-popcount"><b>{roots.length}</b> {planLabel.toLowerCase()}</span>
          {hasFocus(snapshot) && focus ? (
            <span className="rf-chip rf-chip-focus"><Target size={11} /> {focus}</span>
          ) : null}
          {focusIsAmbiguous ? (
            <span className="rf-chip rf-chip-attn"><TriangleAlert size={11} /> focus ambiguous</span>
          ) : null}
        </div>
      </header>

      <div className="rf-toolbar" role="toolbar" aria-label="Plan controls">
        <div className="rf-segmented" role="group" aria-label="Plan lens">
          <button type="button" className={cn('rf-seg', lens === 'tree' && 'on')} aria-pressed={lens === 'tree'} onClick={() => navigateLens('tree')}>
            <Layers size={13} /> Tree
          </button>
          <button type="button" className={cn('rf-seg', lens === 'sequence' && 'on')} aria-pressed={lens === 'sequence'} onClick={() => navigateLens('sequence')}>
            <GitBranch size={13} /> {hasOrderedHierarchy(snapshot) ? 'Timeline' : 'Sequence'}
          </button>
        </div>
        <span className="rf-toolbar-sep" />
        <span className="rf-chip rf-chip-muted">{hasOrderedHierarchy(snapshot) ? 'ordered hierarchy' : 'no declared order'}</span>
        <span className="rf-chip rf-chip-muted">{`${tree.length} rows`}</span>
      </div>

      {notice ? <div role="alert" className="rf-notice"><TriangleAlert size={14} /> {notice}</div> : null}

      {orphanRoots.length ? (
        <section className="rp-orphans" aria-label="Work without a container">
          <span className="rf-chip rf-chip-gate">unfiled</span>
          <span>{orphanRoots.length === 1 ? `${text(orphanRoots[0].title, text(orphanRoots[0].id))} has no container` : `${orphanRoots.length} items have no container`}</span>
          <button type="button" className="rf-link" onClick={() => openDoc(text(orphanRoots[0].id))}>Open <ArrowRight size={11} /></button>
        </section>
      ) : null}

      <div className="rp-layout">
        <main className={cn('rp-main', lens === 'tree' ? 'tree' : 'sequence')}>
          {!roots.length ? (
            <div className="rf-emptystate">
              <b>No {planLabel.toLowerCase()} yet</b>
              <span>This screen pins {population.length ? population.join(', ') : 'the declared hierarchy'}. Add one with <code>rail change add {planPreset || '<preset>'} "..."</code>.</span>
            </div>
          ) : lens === 'tree' ? (
            <div className="rp-tree" role="tree" aria-label={`${planLabel} tree`}>
              {tree.map((row, index) => (
                <PlanRow key={`${text(row.doc.id)}-${index}`} row={row} snapshot={snapshot} openDoc={openDoc} selected={selected} />
              ))}
              {!tree.length ? <div className="rf-empty-col">No work is filed under these containers yet.</div> : null}
            </div>
          ) : (
            <SequenceLens roots={roots} snapshot={snapshot} openDoc={openDoc} selected={selected} />
          )}
        </main>

        <aside className="rp-side">
          {hasFocus(snapshot) ? (
            <section className="rp-box">
              <h5>Focus</h5>
              {focus ? (
                <button type="button" className="rp-focus" onClick={() => openDoc(text(focusFact(snapshot).slug))}>
                  <span className="rf-chip rf-chip-focus">focus</span>
                  <span className="trunc">{focus}</span>
                </button>
              ) : focusIsAmbiguous ? (
                <>
                  <p className="rp-box-note">Candidates are listed. The plan never auto-picks one.</p>
                  {candidates.slice(0, 5).map((candidate) => (
                    <button type="button" key={text(candidate.slug)} className="rp-candidate" onClick={() => openDoc(text(candidate.slug))}>
                      <CircleDashed size={12} />
                      <span className="trunc">{text(candidate.title, text(candidate.slug))}</span>
                    </button>
                  ))}
                </>
              ) : (
                <p className="rp-box-note">No active container matches the declared focus.</p>
              )}
            </section>
          ) : null}

          {queue.length ? (
            <section className="rp-box">
              <h5>Next eligible · why</h5>
              {queue.slice(0, 5).map((item) => {
                const id = text(item.slug);
                const doc = byId.get(id);
                return (
                  <button type="button" key={id} className="rp-next" onClick={() => openDoc(id)}>
                    <span className="rp-dot active" />
                    <span className="grow">
                      <b>{text(item.title, text(doc?.title, id))}</b>
                      <span className="rp-why">{whyText(text(item.why, 'transition available'))}</span>
                    </span>
                    <span className="faint">{text(item.status)}</span>
                  </button>
                );
              })}
            </section>
          ) : null}

          <section className="rp-box">
            <h5>Plan reading</h5>
            <div className="rp-stat"><span>Containers</span><b>{roots.length}</b></div>
            <div className="rp-stat"><span>Rows in tree</span><b>{tree.length}</b></div>
            <div className="rp-stat"><span>Optional hierarchy</span><b>{hasOptionalHierarchy(snapshot) ? 'yes' : 'no'}</b></div>
            <div className="rp-stat"><span>Order</span><b>{hasOrderedHierarchy(snapshot) ? 'declared' : 'not declared'}</b></div>
          </section>

          {selectedItem ? (
            <section className="rp-box">
              <h5>Selected</h5>
              <button type="button" className="rp-focus" onClick={() => openDoc(text(selectedItem.id))}>
                <StatusGlyph status={text(selectedItem.status)} statuses={workflowStatuses(snapshot, workflowForPreset(snapshot, presetOf(selectedItem)))} category={text(selectedItem.category)} size={12} />
                <span className="trunc">{text(selectedItem.title, text(selectedItem.id))}</span>
              </button>
              <p className="rp-box-note">The selection carries across Flow, Plan, Map and Grid.</p>
            </section>
          ) : null}
        </aside>
      </div>
    </div>
  );

  function navigateLens(next: 'tree' | 'sequence') {
    const values: Record<string, string> = {};
    for (const [key, value] of params) values[key] = value;
    if (next === 'tree') delete values.lens;
    else values.lens = next;
    navigate('roadmap', values);
  }
}
