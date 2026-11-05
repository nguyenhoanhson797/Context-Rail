/* Flow is the daily screen: the pack's pinned working population, grouped by
   the screen's declared axis. The category bands are the engine's fixed
   semantics; the columns are the pack's own states. Nothing here branches on
   a pack name or a state name. */
import { useCallback, useMemo, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  defaultAnnouncements,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import {
  ArrowRight,
  CircleDashed,
  GripVertical,
  Layers,
  LayoutDashboard,
  Lock,
  MapIcon,
  SquareKanban,
  Target,
  TriangleAlert,
} from '../icons';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { cn } from '../ui';
import { StatusGlyph } from '../overlays';
import {
  CATEGORY_LABEL,
  CATALOG_KINDS,
  allItems,
  allStatusColumns,
  attentionOf,
  bandedStatuses,
  catalogScreen,
  categoryOf,
  fieldValue,
  flowScreen,
  gateFor,
  groupByOf,
  hasExecution,
  hasPreviewField,
  hasRelations,
  itemById,
  itemsNeedingGate,
  populationItems,
  populationPresets,
  previewFieldsOf,
  screenOf,
  screenWorkflow,
  workflowFact,
  workflowFacts,
  workflowForPreset,
  workflowStatuses,
  type CategoryBand,
  type StatusColumn,
} from '../capabilities';
import { cardFieldsOf, focusName, hierarchyLabels, hierarchyLevels, labelOf, screenTitle, titleCase, unitLabel } from './operating';

const COLUMN_PREFIX = 'status:';
type Lens = 'board' | 'map' | 'grid' | 'canvas';
const LENS_LABEL: Record<Lens, string> = { board: 'Board', map: 'Map', grid: 'Grid', canvas: 'Canvas' };

const presetOf = (doc: Data): string => text(doc.preset, text(doc.type));

function FlowCard({
  item,
  workflow,
  snapshot,
  busy,
  openDoc,
  onPeek,
  selected,
}: {
  item: Data;
  workflow: string;
  snapshot: PageProps['snapshot'];
  busy: boolean;
  openDoc: (id: string) => void;
  onPeek: (item: Data | null) => void;
  selected: boolean;
}) {
  const id = text(item.id);
  const status = text(item.status);
  const category = text(item.category, categoryOf(snapshot, status));
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id,
    disabled: busy,
    data: { status },
  });
  const gate = gateFor(snapshot, workflow, status);
  const owed = itemsNeedingGate(snapshot, presetOf(item), status);
  const priority = text(item.priority);
  const fields = cardFieldsOf(snapshot).filter(
    (field) => !['status', 'workflow', 'priority', 'title', 'id', 'slug'].includes(field),
  );
  const claim = rows(object(snapshot).claims).find((entry) => text(entry.task) === id);
  const who = claim ? text(claim.actor, text(claim.holder, 'claimed')) : '';
  const done = category === 'done';
  return (
    <div
      ref={setNodeRef}
      className={cn('rf-card', done && 'done', category === 'blocked' && 'blocked', category === 'active' && 'active', selected && 'sel', isDragging && 'dragging')}
      onMouseEnter={() => onPeek(item)}
      onMouseLeave={() => onPeek(null)}
      onFocus={() => onPeek(item)}
      onBlur={() => onPeek(null)}
    >
      <div className="rf-card-top">
        <StatusGlyph status={status} statuses={workflowStatuses(snapshot, workflow)} category={category} size={13} />
        <button type="button" className="rf-card-title" onClick={() => openDoc(id)}>
          {text(item.title, id)}
        </button>
        <button
          type="button"
          className="rf-grip"
          aria-label={`Move ${text(item.title, id)} with the keyboard`}
          title="Press space, use the arrow keys, then space to drop"
          disabled={busy}
          {...listeners}
          {...attributes}
        >
          <GripVertical size={13} strokeWidth={1.8} />
        </button>
      </div>
      {text(item.reason, text(item.blocked_why)) && category === 'blocked' ? (
        <p className="rf-card-why">{text(item.reason, text(item.blocked_why))}</p>
      ) : null}
      <div className="rf-card-meta">
        <span className="rf-chip rf-chip-status">{titleCase(status)}</span>
        {gate ? <span className="rf-chip rf-chip-gate" title={gate.checks.length ? `Checks: ${gate.checks.join(', ')}` : 'No checks declared'}>{gate.gate}</span> : null}
        {!gate && owed.length ? <span className="rf-chip rf-chip-gate">gate at {owed.join(', ')}</span> : null}
        {priority ? <span className="rf-chip">{titleCase(priority)}</span> : null}
        {fields.map((field) => {
          const value = fieldValue(item, field);
          return value ? (
            <span key={field} className="rf-chip rf-chip-muted" title={`${field}: ${value}`}>
              {value}
            </span>
          ) : null;
        })}
        {who ? (
          <span className="rf-chip rf-chip-owner">
            <Lock size={10} strokeWidth={1.8} /> {who}
          </span>
        ) : null}
      </div>
      {owed.length ? (
        <div className="rf-card-foot">
          <span>next move needs {owed.join(', ')}</span>
          <button type="button" className="rf-link" onClick={() => openDoc(id)}>
            Open gate <ArrowRight size={11} />
          </button>
        </div>
      ) : null}
    </div>
  );
}

function FlowColumn({
  column,
  items,
  workflow,
  snapshot,
  busy,
  openDoc,
  onPeek,
  selectedDoc,
}: {
  column: StatusColumn;
  items: Data[];
  workflow: string;
  snapshot: PageProps['snapshot'];
  busy: boolean;
  openDoc: (id: string) => void;
  onPeek: (item: Data | null) => void;
  selectedDoc: string;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: COLUMN_PREFIX + column.status, data: { status: column.status } });
  const inColumn = items.filter((item) => text(item.status) === column.status);
  const unit = unitLabel(snapshot).toLowerCase();
  const gate = gateFor(snapshot, workflow, column.status);
  return (
    <section
      ref={setNodeRef}
      className={cn('rf-col', isOver && 'over', column.category === 'blocked' && 'blocked', column.category === 'done' && 'done')}
      data-column={column.status}
      aria-label={`${column.label} column`}
    >
      <header className="rf-col-h">
        <span className="rf-col-name">{column.label}</span>
        <span className="rf-col-count">{inColumn.length}</span>
      </header>
      {gate ? <div className="rf-col-gate" title={gate.checks.length ? `Checks: ${gate.checks.join(', ')}` : 'No checks declared'}>{gate.gate}</div> : null}
      <div className="rf-col-body">
        {inColumn.length ? (
          inColumn.map((item) => (
            <FlowCard
              key={text(item.id)}
              item={item}
              workflow={workflow}
              snapshot={snapshot}
              busy={busy}
              openDoc={openDoc}
              onPeek={onPeek}
              selected={text(item.id) === selectedDoc}
            />
          ))
        ) : (
          <div className="rf-empty-col">{`no ${unit}`}</div>
        )}
      </div>
    </section>
  );
}

function BoardLens({
  items,
  bands,
  workflow,
  snapshot,
  busy,
  openDoc,
  onMove,
  onPeek,
  groupBy,
  selectedDoc,
}: {
  items: Data[];
  bands: CategoryBand[];
  workflow: string;
  snapshot: PageProps['snapshot'];
  busy: boolean;
  openDoc: (id: string) => void;
  onMove: (item: Data, status: string) => void;
  onPeek: (item: Data | null) => void;
  groupBy: string;
  selectedDoc: string;
}) {
  const columns = allStatusColumns(bands);
  const [active, setActive] = useState<Data | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));
  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActive(null);
      const over = event.over ? String(event.over.id) : '';
      if (!over.startsWith(COLUMN_PREFIX)) return;
      const item = items.find((candidate) => text(candidate.id) === String(event.active.id));
      if (item) onMove(item, over.slice(COLUMN_PREFIX.length));
    },
    [items, onMove],
  );
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={(event: DragStartEvent) => setActive(items.find((item) => text(item.id) === String(event.active.id)) ?? null)}
      onDragEnd={onDragEnd}
      onDragCancel={() => setActive(null)}
      accessibility={{
        announcements: {
          ...defaultAnnouncements,
          onDragStart: ({ active: dragged }) => `Picked up ${String(dragged.id)}.`,
          onDragOver: ({ active: dragged, over }) => (over ? `${String(dragged.id)} is over ${String(over.id).replace(COLUMN_PREFIX, '')}.` : `${String(dragged.id)} left the board.`),
          onDragEnd: ({ active: dragged, over }) =>
            over ? `${String(dragged.id)} dropped on ${String(over.id).replace(COLUMN_PREFIX, '')}; the store decides whether it moves.` : `${String(dragged.id)} did not move.`,
        },
        screenReaderInstructions: {
          draggable: 'Focus a card grip, press space, use the arrow keys, then space to drop. Escape cancels.',
        },
      }}
    >
      <div className="rf-bandgrid" style={{ gridTemplateColumns: `repeat(${Math.max(columns.length, 1)}, minmax(144px, 1fr))` }}>
        {bands.map((band) => (
          <div key={band.category} className={cn('rf-band', `rf-band-${band.category}`)} style={{ gridColumn: `span ${band.statuses.length}` }}>
            <span>{band.label}</span>
            <span>{band.statuses.reduce((sum, status) => sum + items.filter((item) => text(item.status) === status.status).length, 0)}</span>
          </div>
        ))}
        {columns.map((column) => (
          <FlowColumn
            key={column.status}
            column={column}
            items={items}
            workflow={workflow}
            snapshot={snapshot}
            busy={busy}
            openDoc={openDoc}
            onPeek={onPeek}
            selectedDoc={selectedDoc}
          />
        ))}
      </div>
      {groupBy && groupBy !== 'status' ? (
        <p className="rf-note">
          This screen groups by <b>{groupBy}</b>; the category bands remain the fixed fallback.
        </p>
      ) : null}
      <DragOverlay dropAnimation={null}>
        {active ? (
          <div className="rf-card rf-card-drag">
            <div className="rf-card-top">
              <StatusGlyph status={text(active.status)} statuses={workflowStatuses(snapshot, workflow)} category={text(active.category)} size={13} />
              <span className="rf-card-title">{text(active.title, text(active.id))}</span>
            </div>
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}

function FieldGroupLens({
  items,
  groupBy,
  snapshot,
  openDoc,
}: {
  items: Data[];
  groupBy: string;
  snapshot: PageProps['snapshot'];
  openDoc: (id: string) => void;
}) {
  const groups = new Map<string, Data[]>();
  for (const item of items) {
    const key = fieldValue(item, groupBy) || 'unassigned';
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return (
    <div className="rf-fieldgroups">
      {[...groups.entries()].map(([key, list]) => (
        <section className="rf-fieldgroup" key={key}>
          <header>
            <b>{key}</b>
            <span>{list.length}</span>
          </header>
          {list.map((item) => (
            <button type="button" key={text(item.id)} className="rf-gridrow" onClick={() => openDoc(text(item.id))}>
              <StatusGlyph status={text(item.status)} statuses={workflowStatuses(snapshot, screenWorkflow(snapshot, flowScreen(snapshot)))} category={text(item.category)} size={12} />
              <span className="trunc">{text(item.title, text(item.id))}</span>
              <span className="faint">{titleCase(text(item.status))}</span>
            </button>
          ))}
        </section>
      ))}
    </div>
  );
}

function MapLens({
  items,
  snapshot,
  openDoc,
  selectedDoc,
}: {
  items: Data[];
  snapshot: PageProps['snapshot'];
  openDoc: (id: string) => void;
  selectedDoc: string;
}) {
  const universe = new Map(allItems(snapshot).map((item) => [text(item.id), item]));
  const nodeMap = new Map(items.map((item) => [text(item.id), item]));
  for (const item of items) {
    for (const rel of ['part_of', 'depends_on', 'verifies']) {
      const values = object(item.relations)[rel];
      for (const target of Array.isArray(values) ? values : []) {
        if (typeof target !== 'string' || nodeMap.has(target)) continue;
        const context = universe.get(target);
        if (context) nodeMap.set(target, context);
      }
    }
  }
  const nodes = [...nodeMap.values()].slice(0, 30);
  const byId = new Map(nodes.map((item) => [text(item.id), item]));
  const population = new Set(items.map((item) => text(item.id)));
  const depthOf = (item: Data, seen = new Set<string>()): number => {
    const id = text(item.id);
    if (seen.has(id)) return 0;
    seen.add(id);
    const parents = object(item.relations).part_of;
    const parent = (Array.isArray(parents) ? parents : []).find((value): value is string => typeof value === 'string');
    const parentItem = parent ? byId.get(parent) : undefined;
    return parentItem ? depthOf(parentItem, seen) + 1 : 0;
  };
  const columns = new Map<number, Data[]>();
  for (const item of nodes) {
    const depth = depthOf(item);
    columns.set(depth, [...(columns.get(depth) ?? []), item]);
  }
  const positions = new Map<string, { x: number; y: number }>();
  for (const [depth, list] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    list.forEach((item, index) => positions.set(text(item.id), { x: 24 + depth * 220, y: 42 + index * 62 }));
  }
  const edges: { from: string; to: string; rel: string }[] = [];
  for (const item of nodes) {
    for (const rel of ['depends_on', 'part_of', 'verifies']) {
      const targets = Array.isArray(object(item.relations)[rel]) ? (object(item.relations)[rel] as unknown[]) : [];
      for (const target of targets) {
        if (typeof target === 'string' && positions.has(target)) edges.push({ from: text(item.id), to: target, rel });
      }
    }
  }
  const tone = (item: Data): string => {
    const category = text(item.category, categoryOf(snapshot, text(item.status)));
    return category === 'done' ? '#1c7a52' : category === 'blocked' ? '#c43d34' : category === 'active' ? '#2f5bea' : '#8a93a0';
  };
  return (
    <div className="rf-mapwrap">
      <svg className="rf-map" viewBox="0 0 840 420" role="img" aria-label="Dependency map of the current population">
        <defs>
          <marker id="rf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
          </marker>
        </defs>
        {edges.map((edge, index) => {
          const from = positions.get(edge.from);
          const to = positions.get(edge.to);
          if (!from || !to) return null;
          return (
            <g key={`${edge.from}-${edge.to}-${edge.rel}-${index}`} className={cn('rf-edge', `rf-edge-${edge.rel}`)}>
              <line x1={from.x + 170} y1={from.y + 18} x2={to.x} y2={to.y + 18} markerEnd="url(#rf-arrow)" />
              <text x={(from.x + 170 + to.x) / 2} y={(from.y + to.y) / 2 + 12}>{edge.rel}</text>
            </g>
          );
        })}
        {nodes.map((item) => {
          const pos = positions.get(text(item.id));
          if (!pos) return null;
          const context = !population.has(text(item.id));
          const selected = text(item.id) === selectedDoc;
          return (
            <g
              key={text(item.id)}
              className={cn('rf-node', context && 'context', selected && 'sel')}
              transform={`translate(${pos.x},${pos.y})`}
              onClick={() => openDoc(text(item.id))}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  openDoc(text(item.id));
                }
              }}
              role="button"
              tabIndex={0}
            >
              <rect width="170" height="36" rx="7" fill="var(--surface)" stroke={selected ? 'var(--accent)' : context ? 'var(--border-strong)' : tone(item)} strokeWidth={selected ? 2.4 : 1.4} strokeDasharray={context ? '4 3' : undefined} />
              <circle cx="12" cy="18" r="4" fill={context ? 'var(--text-4)' : tone(item)} />
              <text x="24" y="22" className="rf-node-label">{text(item.title, text(item.id)).slice(0, 24)}</text>
            </g>
          );
        })}
      </svg>
      <div className="rf-maplegend">
        {['not_started', 'active', 'blocked', 'done'].map((category) => (
          <span key={category}><i style={{ background: category === 'done' ? '#1c7a52' : category === 'blocked' ? '#c43d34' : category === 'active' ? '#2f5bea' : '#8a93a0' }} />{CATEGORY_LABEL[category]}</span>
        ))}
        <span className="faint">{nodes.length < items.length ? `${items.length - nodes.length} more not drawn` : 'depends_on, part_of and verifies edges'}</span>
      </div>
    </div>
  );
}

function GridLens({
  snapshot,
  openDoc,
  selectedDoc,
}: {
  snapshot: PageProps['snapshot'];
  openDoc: (id: string) => void;
  selectedDoc: string;
}) {
  const catalog = catalogScreen(snapshot) ?? screenOf(snapshot, CATALOG_KINDS);
  const presets = catalog ? populationPresets(snapshot, catalog) : [];
  const items = populationItems(snapshot, presets);
  const declaredFields = [...new Set(['title', 'status', ...cardFieldsOf(snapshot)])].filter((field) => !['id', 'slug'].includes(field));
  /* A catalog column appears only when the pinned population actually carries
     the field; an empty column is not a field. */
  const fields = declaredFields.filter(
    (field) => field === 'title' || field === 'status' || items.some((item) => Boolean(fieldValue(item, field))),
  );
  const workflowOf = (item: Data) => workflowForPreset(snapshot, presetOf(item));
  return (
    <div className="rf-gridwrap">
      <table className="rf-gridtable">
        <thead>
          <tr>
            <th>{text(catalog?.title, 'Catalog')}</th>
            <th>Preset</th>
            {fields.filter((field) => field !== 'title').map((field) => <th key={field}>{field === 'status' ? 'Status' : titleCase(field)}</th>)}
            <th>Gate</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const workflow = workflowOf(item);
            const gate = workflow ? gateFor(snapshot, workflow, text(item.status)) : null;
            return (
              <tr key={text(item.id)} className={text(item.id) === selectedDoc ? 'sel' : undefined} onClick={() => openDoc(text(item.id))} tabIndex={0} onKeyDown={(event) => event.key === 'Enter' && openDoc(text(item.id))}>
                <td><b>{text(item.title, text(item.id))}</b><span className="rf-gridid">{text(item.id)}</span></td>
                <td>{labelOf(snapshot, presetOf(item))}</td>
                {fields.filter((field) => field !== 'title').map((field) => (
                  <td key={field} className={field === 'status' ? 'rf-gridstatus' : undefined}>
                    {field === 'status' ? titleCase(text(item.status)) : fieldValue(item, field) || '—'}
                  </td>
                ))}
                <td>{gate ? <span className="rf-chip rf-chip-gate">{gate.gate}</span> : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!items.length ? <div className="rf-empty-col">This pack declares no catalog population.</div> : null}
      <p className="rf-note">The catalog is its own population. The working board never mixes it with other presets.</p>
    </div>
  );
}

function CanvasLens({ snapshot }: { snapshot: PageProps['snapshot'] }) {
  const fields = previewFieldsOf(snapshot);
  const items = allItems(snapshot);
  return (
    <div className="rf-canvas">
      {items.slice(0, 12).map((item) => {
        const media = fields.map((field) => fieldValue(item, field)).find(Boolean);
        return (
          <figure key={text(item.id)} className="rf-tile">
            <div className="rf-tile-media">
              {media ? <span>{media}</span> : <span className="faint">no value</span>}
            </div>
            <figcaption>{text(item.title, text(item.id))}</figcaption>
          </figure>
        );
      })}
      {!items.length ? <div className="rf-empty-col">No items carry this preview field.</div> : null}
    </div>
  );
}

export function FlowPage({ snapshot, params, navigate, openDoc, mutate, busy }: PageProps) {
  const screen = flowScreen(snapshot);
  const population = populationPresets(snapshot, screen);
  const declaredGroup = groupByOf(screen);
  const unit = unitLabel(snapshot).toLowerCase();
  const workflowDefault = screenWorkflow(snapshot, screen);
  const requested = params.get('workflow') ?? '';
  const known = workflowFacts(snapshot).some((entry) => text(entry.name) === requested);
  const workflow = known ? requested : workflowDefault;
  const workflowFactRow = workflowFact(snapshot, workflow);
  const workflowLabel = text(workflowFactRow?.name, 'workflow');
  const applicableWorkflows = useMemo(
    () => workflowFacts(snapshot).filter((fact) => population.some((preset) => workflowForPreset(snapshot, preset) === text(fact.name))),
    [snapshot, population],
  );
  const bands = useMemo(() => bandedStatuses(snapshot, workflow), [snapshot, workflow]);
  const hierarchy = hierarchyLevels(snapshot);
  const containerPreset = text(hierarchy[0]?.preset);
  const containerLevel = hierarchyLabels(snapshot)[0] ?? '';
  const requestedPhase = params.get('phase') ?? '';
  const phase = containerPreset && allItems(snapshot).some((item) => text(item.id) === requestedPhase && text(item.preset) === containerPreset)
    ? requestedPhase
    : '';
  const [notice, setNotice] = useState('');
  const [peek, setPeek] = useState<Data | null>(null);
  const lensParam = params.get('lens') ?? 'board';

  const base = useMemo(() => populationItems(snapshot, population), [snapshot, population]);
  const byPresetWorkflow = useMemo(
    () => base.filter((item) => !workflow || !workflowForPreset(snapshot, presetOf(item)) || workflowForPreset(snapshot, presetOf(item)) === workflow),
    [base, snapshot, workflow],
  );
  const attention = useMemo(() => attentionOf(snapshot), [snapshot]);
  const attentionIds = useMemo(() => new Set(attention.map((entry) => entry.id)), [attention]);
  const filtered = useMemo(() => {
    if (!phase) return byPresetWorkflow;
    return byPresetWorkflow.filter((item) => {
      const parents = Array.isArray(object(item.relations).part_of) ? (object(item.relations).part_of as unknown[]) : [];
      return parents.includes(phase);
    });
  }, [byPresetWorkflow, phase]);
  const items = useMemo(
    () => (params.get('attention') === '1' ? filtered.filter((item) => attentionIds.has(text(item.id))) : filtered),
    [filtered, params, attentionIds],
  );

  const visibleBands = useMemo(
    () => bands.filter((band) => band.category !== 'dropped' || items.some((item) => text(item.category) === 'dropped')),
    [bands, items],
  );
  const byId = useMemo(() => itemById(snapshot), [snapshot]);
  const focus = focusName(snapshot);
  const selectedDoc = params.get('doc') ?? '';
  const hasCanvas = hasPreviewField(snapshot);
  const hasMap = useMemo(() => hasRelations(items), [items]);
  const hasCatalog = Boolean(catalogScreen(snapshot));
  const lensOptions: Lens[] = ['board', ...(hasMap ? (['map'] as Lens[]) : []), ...(hasCatalog ? (['grid'] as Lens[]) : []), ...(hasCanvas ? (['canvas'] as Lens[]) : [])];
  const lens: Lens = lensOptions.includes(lensParam as Lens) ? (lensParam as Lens) : 'board';
  const applicableWorkflowNames = applicableWorkflows.map((fact) => text(fact.name));
  const selectorWorkflows = applicableWorkflowNames.length > 1 ? applicableWorkflowNames : [];
  const workflowChipLabel = population.length === 1 ? `${labelOf(snapshot, population[0])} workflow` : workflowLabel;
  const groupBy = declaredGroup;

  const setParam = (key: string, value: string) => {
    const next: Record<string, string> = {};
    for (const [name, entry] of params) next[name] = entry;
    if (value) next[key] = value;
    else delete next[key];
    navigate('board', next);
  };

  const move = useCallback(
    async (item: Data, status: string) => {
      const id = text(item.id);
      if (!id || busy || text(item.status) === status) return;
      setNotice('');
      try {
        await mutate('change move', { slug: id, status, expect: text(item.etag) });
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error));
      }
    },
    [busy, mutate],
  );

  const peekRelations = peek
    ? ['part_of', 'depends_on', 'verifies', 'owned_by'].map((rel) => ({ rel, values: Array.isArray(object(peek.relations)[rel]) ? (object(peek.relations)[rel] as unknown[]).filter((value): value is string => typeof value === 'string') : [] })).filter((entry) => entry.values.length)
    : [];
  const peekWorkflow = peek ? workflowForPreset(snapshot, presetOf(peek)) || workflow : workflow;
  const peekOwed = peek ? itemsNeedingGate(snapshot, presetOf(peek), text(peek.status)) : [];
  const peekGate = peek
    ? gateFor(snapshot, peekWorkflow, text(peek.status)) ?? (peekOwed.length ? gateFor(snapshot, peekWorkflow, peekOwed[0]) : null)
    : null;

  return (
    <div className="rf-page">
      <header className="rf-head">
        <div className="rf-head-main">
          <div className="rf-kicker">{`${labelOf(snapshot, population[0] ?? '', true)} · ${groupBy === 'status' ? 'grouped by status' : groupBy ? `grouped by ${groupBy}` : 'category fallback'}`}</div>
          <h1>{screen?.title ?? screenTitle(snapshot, 'board', 'Flow')}</h1>
          <p>{`The pack's working population. Move a ${unit} through its own states; the store decides what is legal.`}</p>
        </div>
        <div className="rf-head-side">
          <span className="rf-popcount"><b>{items.length}</b> {labelOf(snapshot, population[0] ?? '', true)}</span>
          {focus ? <button type="button" className="rf-chip rf-chip-focus" onClick={() => navigate('roadmap', selectedDoc ? { doc: selectedDoc } : {})}><Target size={11} /> {focus}</button> : null}
          {attention.length ? <button type="button" className="rf-chip rf-chip-attn" onClick={() => setParam('attention', '1')}><TriangleAlert size={11} /> {attention.length} need you</button> : null}
        </div>
      </header>

      <div className="rf-toolbar" role="toolbar" aria-label="Flow controls">
        <div className="rf-segmented" role="group" aria-label="Lens">
          {lensOptions.map((entry) => (
            <button key={entry} type="button" className={cn('rf-seg', lens === entry && 'on')} aria-pressed={lens === entry} onClick={() => setParam('lens', entry === 'board' ? '' : entry)}>
              {entry === 'board' ? <SquareKanban size={13} /> : entry === 'map' ? <MapIcon size={13} /> : entry === 'grid' ? <LayoutDashboard size={13} /> : <Layers size={13} />}
              {LENS_LABEL[entry]}
            </button>
          ))}
        </div>
        <span className="rf-toolbar-sep" />
        {selectorWorkflows.length > 1 ? (
          <label className="rf-field">
            <span>Workflow</span>
            <select value={workflow} onChange={(event) => setParam('workflow', event.target.value)} disabled={busy}>
              {selectorWorkflows.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
        ) : (
          <span className="rf-chip rf-chip-muted"><CircleDashed size={11} /> {workflowChipLabel}</span>
        )}
        {declaredGroup ? <span className="rf-chip rf-chip-muted">Group by {declaredGroup}</span> : null}
        {containerLevel ? (
          <label className="rf-field">
            <span>{containerLevel}</span>
            <select value={phase} onChange={(event) => setParam('phase', event.target.value)} disabled={busy}>
              <option value="">All {containerLevel.toLowerCase()}s</option>
              {allItems(snapshot).filter((item) => text(item.preset) === containerPreset).map((item) => <option key={text(item.id)} value={text(item.id)}>{text(item.title, text(item.id))}</option>)}
            </select>
          </label>
        ) : null}
        <span className="rf-toolbar-spacer" />
        {hasExecution(snapshot) ? <span className="rf-chip rf-chip-muted">execution separate</span> : null}
      </div>

      {notice ? <div role="alert" className="rf-notice"><TriangleAlert size={14} /> {notice}</div> : null}

      {params.get('attention') === '1' && attention.length ? (
        <section className="rf-attention" aria-label="Needs you">
          <header><b>{attention.length} need you</b><span>blocked work, proof owed and an unresolved focus</span><button type="button" className="rf-link" onClick={() => setParam('attention', '')}>clear</button></header>
          <div className="rf-attention-list">
            {attention.slice(0, 8).map((entry) => (
              <button type="button" key={entry.key} className="rf-attention-row" onClick={() => openDoc(entry.id)}>
                <span className={cn('rf-dot', `rf-dot-${entry.kind}`)} />
                <span className="trunc"><b>{entry.title}</b><span>{entry.reason}</span></span>
                <ArrowRight size={12} />
              </button>
            ))}
            {attention.length > 8 ? <span className="rf-note">{attention.length - 8} more in the board and history.</span> : null}
          </div>
        </section>
      ) : null}

      <main className={cn('rf-content', `rf-content-${lens}`)}>
        {!base.length ? (
          <div className="rf-emptystate">
            <b>No {labelOf(snapshot, population[0] ?? '', true).toLowerCase()} yet</b>
            <span>This screen pins {population.length ? population.join(', ') : 'no preset'}. Create one with <code>rail change add {population[0] ?? '<preset>'} "..."</code>.</span>
          </div>
        ) : lens === 'board' ? (
          groupBy && groupBy !== 'status' ? (
            <FieldGroupLens items={items} groupBy={groupBy} snapshot={snapshot} openDoc={openDoc} />
          ) : (
            <BoardLens
              items={items}
              bands={visibleBands}
              workflow={workflow}
              snapshot={snapshot}
              busy={busy}
              openDoc={openDoc}
              onMove={(item, status) => void move(item, status)}
              onPeek={setPeek}
              groupBy={groupBy}
              selectedDoc={selectedDoc}
            />
          )
        ) : lens === 'map' ? (
          <MapLens items={items} snapshot={snapshot} openDoc={openDoc} selectedDoc={selectedDoc} />
        ) : lens === 'grid' ? (
          <GridLens snapshot={snapshot} openDoc={openDoc} selectedDoc={selectedDoc} />
        ) : lens === 'canvas' && hasCanvas ? (
          <CanvasLens snapshot={snapshot} />
        ) : null}
      </main>

      {peek ? (
        <aside className="rf-peek" aria-label={`${text(peek.title, 'Item')} details`}>
          <header>
            <b>{text(peek.title, text(peek.id))}</b>
            <span>{labelOf(snapshot, presetOf(peek))} · {titleCase(text(peek.status))}</span>
          </header>
          {peekGate ? (
            <div className="rf-peek-block">
              <div className="rf-k2">Gate · {peekGate.gate}</div>
              {peekGate.checks.length ? peekGate.checks.map((check) => <div key={check} className="rf-peek-check"><span>○</span>{check}</div>) : <div className="rf-peek-check faint">no checks declared</div>}
            </div>
          ) : null}
          {peekRelations.length ? (
            <div className="rf-peek-block">
              <div className="rf-k2">Relations</div>
              {peekRelations.map((entry) => (
                <div key={entry.rel} className="rf-peek-rel"><b>{entry.rel}</b><span>{entry.values.map((value) => text(byId.get(value)?.title, value)).join(', ')}</span></div>
              ))}
            </div>
          ) : null}
          <footer><span>Click a card to pin it</span><span className="faint">Esc closes</span></footer>
        </aside>
      ) : null}
    </div>
  );
}
