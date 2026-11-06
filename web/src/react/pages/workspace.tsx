/* =====================================================================
   WORKSPACE SCREENS (React migration - task-19)

   Board, Now, Roadmap, Scope, Decisions, Lens, Graph, Doctor, Portfolio
   and Settings. Every screen takes PageProps from ../contracts and renders
   ../ui primitives plus Tailwind utilities; the readable surface classes
   are the semantic rail-* components the shell styles (Lead-owned CSS).

   Rules kept here, matching the legacy screens' semantics:
   - No raw HTML: store text is rendered as React text nodes only, never
     through dangerouslySetInnerHTML. Store prose goes through the reviewed
     safe Markdown component in ../ui (skipHtml, external http(s) links only,
     images omitted). Wiki links written in prose stay inert spans: following
     an internal [[link]] from prose is a reported navigation gap, not a
     silent rewrite. The work order is shown as exact preformatted text.
   - No mocked production data: every list comes from the snapshot, a real
     read tool or GET /api/portfolio through props.request.
   - No optimistic status: a board card renders from snapshot.tasks only; a
     drop calls start / done / task_update and the shell reconciles the
     snapshot. A refusal (including status:ok with started/applied false) is
     surfaced in the page and the card never moves on its own.
   - The task status becomes a tool call in exactly one place (moveTask
     below), mirroring web/src/actions/actions.js:
       in-progress -> start {task}
       done        -> done {task} with confirm:true (checks run first)
       otherwise   -> task_update {slug, status}
   - Doctor source links are exact and fail closed: a link appears only when
     the finding's file matches one document by the server's structured
     store_root/ambiguous_doc_ids metadata, never by basename guessing.
   - Graph filters keep their deep-link parameters (focus/depth/types/fields).
   ===================================================================== */
import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
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
  Ban,
  BookOpen,
  Check,
  Circle,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  ClipboardCheck,
  File as FileGlyph,
  FileText,
  Flag,
  Folders,
  GitBranch,
  GitMerge,
  GripVertical,
  Keyboard as KeyboardGlyph,
  Loader,
  History,
  ListChecks,
  RotateCcw,
  Lock,
  Monitor,
  Moon,
  Palette,
  Route as RouteGlyph,
  ShieldAlert,
  ShieldCheck,
  Sun,
  Target,
  Terminal,
  TriangleAlert,
} from '../icons';
import { ACTIVE_STATUSES, INTEL_CONTRACT, object, rows, text, type Data, type PageProps, type Snapshot } from '../contracts';
import { RAIL_KEY, readKey } from '../refresh';
import { ACCENTS, applyPrefs, readPrefs, savePrefs, type Prefs } from '../preferences';
import { Badge, Button, Empty, ErrorState, Loading, Markdown, PageHeading, Panel, cn } from '../ui';
import { useModalFocus, useOverlayEscape } from '../overlays';
import { AutomationSettings } from './settings';
import { ConfigSettings } from './config';
import { EVIDENCE, evidenceTitle, evidenceWord } from './evidence';
import { cardFieldsOf, focusName, hierarchyLabels, hierarchyLevels, labelOf, priorityValues, screenTitle, screenTitleForRoute, unitLabel, unitOf } from './operating';

type Tone = 'neutral' | 'good' | 'warning' | 'danger' | 'accent';

/* ---------- vocabulary ---------- */
const STATUS_FALLBACK = ['todo', 'in-progress', 'blocked', 'done'];
const PRIORITY_TONE: Record<string, Tone> = { must: 'accent', should: 'accent', could: 'neutral', wont: 'neutral' };
const SEVERITY_TONE: Record<string, Tone> = { error: 'danger', warning: 'warning', info: 'neutral' };
const CHECK_TONE: Record<string, Tone> = { pass: 'good', fail: 'danger', pending: 'neutral', asserted: 'warning' };
const MOSCOW: Record<string, string> = { must: 'Must', should: 'Should', could: 'Could', wont: "Won't" };
const priorityLabel = (value: string): string => MOSCOW[value] ?? titleCase(value);
const priorityOrderOf = (snapshot: Snapshot): string[] => priorityValues(snapshot);
const SEVERITIES = ['error', 'warning', 'info'];
const DONE_LIMIT = 5;
const COLUMN_PREFIX = 'column:';
const GRAPH_TYPES = ['task', 'scope_item', 'requirement', 'decision', 'phase', 'risk', 'question', 'note'];
/* The closed statuses of the legacy doc picker (ui/doc-picker.js CLOSED_STATUSES). */
const CLOSED_STATUSES = new Set(['done', 'superseded', 'resolved', 'closed', 'archived', 'wont', 'skipped', 'cancelled']);
/* The legacy freshness tone classes (pages/graph.js FRESH_TONE). */
const FRESH_CLASS: Record<string, string> = { fresh: 'green', recent: 'green', aging: 'amber', stale: 'red', ancient: 'red', unknown: 'gray' };
const MAX_NODES = 60;
const MAX_SEARCH = 200;
/* Node fill by freshness, from theme variables with the legacy colours as fallback. */
const FRESH_FILL: Record<string, string> = {
  fresh: 'var(--green, #2f9e44)',
  recent: 'var(--green, #2f9e44)',
  aging: 'var(--amber, #e8a317)',
  stale: 'var(--red, #d6453d)',
  ancient: 'var(--red, #d6453d)',
  unknown: 'var(--text-3, #888)',
};

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;
const titleCase = (id: string): string => id.replace(/-/g, ' ').replace(/(^|\s)\w/g, (c) => c.toUpperCase());
const idOf = (doc: Data, fallback = ''): string => text(doc.id, fallback);
const titleOf = (doc: Data): string => text(doc.title, text(doc.id));

/* ---------- the outcome a piece of work serves ---------- */
/* A goal names its tasks, so the mapping is the read model's, not the
   screen's: the work surfaces show which outcome the work serves without
   copying that outcome's status - the outcome page stays the one place
   that reports it. The mapping itself lives in ../outcomes, shared with
   the change-impact view, which names the outcome a scope item serves. */
import { outcomeByTask, outcomeLabel, type Outcome } from '../outcomes';
/* One read of the understanding model, shared with the drawer: the same
   query key AND the same fresh-forever policy the shell uses for it, so a page
   that mounts its outcome rows never turns the shared read into a second fetch.
   (With mismatched staleTimes TanStack treats the shared entry as stale for the
   newly mounted observer and refetches it, which is the "read twice" the shared
   key is supposed to prevent.) */
function useOutcomes(project: string, read: PageProps['read'], busy: boolean): Map<string, Outcome[]> {
  const query = useQuery({ queryKey: readKey(project, 'understanding'), queryFn: ({ signal }) => read('understanding', {}, signal), retry: false, enabled: !busy, staleTime: Infinity });
  return useMemo(() => outcomeByTask(query.data), [query.data]);
}
const statusesOf = (snapshot: Snapshot): string[] => (snapshot.statuses.length ? snapshot.statuses : STATUS_FALLBACK);
/* "[[SC-30]]" and "[[SC-30|label]]" both give the target name; a lone string is a list of one. */
const linkRefs = (value: unknown): string[] => {
  const list = Array.isArray(value) ? value : value === undefined || value === null || value === '' ? [] : [value];
  return list
    .map((entry) => text(entry).trim().replace(/^\[\[/, '').replace(/\]\]$/, '').split('|')[0].trim())
    .filter(Boolean);
};

/* `rows()` keeps object rows (the shared contract helper); a bare list of strings - fields, commands,
   changed files, trim actions - needs its own filter or it silently reads as empty. */
const stringList = (value: unknown): string[] => (Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []);

/* ---------- score units (ported from the legacy helpers so a tick never reads as proof) ---------- */
/* Only a real fraction in 0..1 is a percent. A fraction below 1 never rounds up to 100%. */
export function fractionPct(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) return null;
  if (value >= 1) return 100;
  const n = Math.round(value * 1000) / 10;
  return n >= 100 ? 99.9 : n;
}
export const pctLabel = (value: unknown, missing = 'Not assessed'): string => {
  const n = fractionPct(value);
  return n === null ? missing : `${n}%`;
};
/* An item with no rubric has no score at all (has_checklist false), so it reads as not assessed, never 0%. */
export function provenPct(item: Data): number | null {
  if (item.has_checklist === false) return null;
  return fractionPct(item.proven);
}
export function checkedPct(item: Data): number | null {
  return fractionPct(item.score);
}

/* ---------- small presentation pieces ---------- */
function DocLink({ id, label, openDoc }: { id: string; label?: string; openDoc: (id: string) => void }) {
  if (!id) return null;
  return (
    <button type="button" className="link rail-link" onClick={() => openDoc(id)}>
      {label || id}
    </button>
  );
}
/* Store prose marks commands with `backticks`; a reader sees the command as code, not the marks. */
function Inline({ value }: { value: string }) {
  const parts = value.split(/`([^`\n]+)`/);
  return <>{parts.map((part, index) => (index % 2 ? <code key={index} className="rail-code">{part}</code> : part))}</>;
}
function Sub({ value }: { value: unknown }) {
  const body = text(value).trim();
  return body ? <div className="faint rail-list-sub" style={{fontSize:12,overflowWrap:'anywhere'}}><Inline value={body} /></div> : null;
}
/* A refused lens read is the page's own state. The reader's raw refusal ("... - pass task_id") names a parameter the page
   picks for you, so it is rewritten into what to do. */
export function lensRefusal(message: string): string {
  const m = /^(.*?)\s*-\s*pass (?:the )?task(?:_id)?\.?$/i.exec(message.trim());
  if (!m) return message;
  const reason = m[1].charAt(0).toUpperCase() + m[1].slice(1);
  return `${reason}. Pick a task from the list above.`;
}

/* The legacy empty block (ui/helpers.js empty()): a centered 20-unit glyph, an es-h heading
   and one body line. The shell's Empty is a different structure, so the legacy one is drawn
   here for the routes whose legacy page used empty(). */
function LegacyEmptyBlock({ icon, title, text: body, level = 2 }: { icon: ReactNode; title: string; text: string; level?: 2 | 3 }) {
  return (
    <div className="empty-state">
      <div className="glyph">{icon}</div>
      {level === 3 ? <h3 className="es-h">{title}</h3> : <h2 className="es-h">{title}</h2>}
      <p>{body}</p>
    </div>
  );
}

/* The legacy clock glyph (ic('clock', n)): the shared ./icons module does not export it, so the
   same Lucide node data is inlined here with the same svg shape ic() emits. */
function ClockGlyph({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="i lucide lucide-clock">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

/* The legacy graph/lens row (pages/graph.js row()): a .mini.lens-row with a fixed top alignment. */
function GraphRow({ icon, main, extra }: { icon: ReactNode; main: ReactNode; extra?: ReactNode }) {
  return (
    <div className="mini lens-row" style={{ cursor: 'default', alignItems: 'flex-start' }}>
      {icon}
      <div className="grow" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{main}</div>
      {extra ?? null}
    </div>
  );
}

/* The legacy Scope headline (pages/core.js scoreLine): the numeric totals are authoritative -
   final_score is the proven total, and the server's summary is only ever appended after it. */
/* The server's score line says "Won't held 10/10"; a reader needs to know what held. */
const readableScore = (value: string): string => value.replace(/Won't held (\d+\/\d+)/g, "Won't items held $1");
function scoreLine(roll: Data): string {
  const totals = object(roll.totals);
  const proven = fractionPct(totals.final_score);
  if (proven === null) return evidenceTitle('proven') + ' ' + evidenceWord('not_assessed');
  const checked = fractionPct(totals.checked_score);
  const note = checked === null || checked === proven ? '' : ' (' + evidenceWord('checked') + ' ' + String(checked) + '%)';
  const summary = typeof roll.summary === 'string' && roll.summary.trim() ? ' · ' + readableScore(roll.summary) : '';
  return evidenceTitle('proven') + ' ' + String(proven) + '%' + note + summary;
}

/* The legacy tone classes for a decision status and a risk severity (pages/core.js). */
const DECISION_STATUS_CLASS: Record<string, string> = { active: 'accent', proposed: 'amber', closed: 'green', superseded: 'gray', rejected: 'gray' };
const RISK_SEVERITY_CLASS: Record<string, string> = { critical: 'red', high: 'red', medium: 'amber', low: 'gray' };

/* =====================================================================
   BOARD
   ===================================================================== */
/* The legacy status glyph (web/src/ui/helpers.js stIcon): a 14-unit ring with a
   slice set by the status's place in the enum, a bar for blocked and a filled tick for
   the last status. Kept as inline SVG so the board matches the legacy markup exactly. */
function StatusGlyph({ status, statuses = [], category }: { status: string; statuses?: string[]; category?: string }) {
  const key = category || status;
  const color = `var(--st-${key.replace(/[^a-z0-9-]/gi, '')}, var(--st-todo))`;
  const ring = <circle cx="7" cy="7" r={5.4} fill="none" stroke={color} strokeWidth={1.5} />;
  const idx = statuses.indexOf(status);
  const last = statuses.length - 1;
  let inner: ReactNode;
  const done = category ? category === 'done' : status === 'done' || (idx === last && idx > 0);
  const blocked = category ? category === 'blocked' : status === 'blocked';
  const active = category ? category === 'active' : idx * 2 >= last;
  if (done) {
    inner = (
      <>
        <circle cx="7" cy="7" r={6.2} fill={color} />
        <path d="M4.4 7.2 6.2 9 9.7 5.3" fill="none" stroke="var(--surface)" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
      </>
    );
  } else if (blocked) {
    inner = (
      <>
        {ring}
        <path d="M4.6 7h4.8" stroke={color} strokeWidth={1.6} strokeLinecap="round" />
      </>
    );
  } else if (idx <= 0 && !category) {
    inner = ring;
  } else if (active) {
    inner = (
      <>
        {ring}
        <path d="M7 3.4A3.6 3.6 0 1 1 3.4 7L7 7Z" fill={color} />
      </>
    );
  } else {
    inner = (
      <>
        {ring}
        <path d="M7 3.4A3.6 3.6 0 0 1 7 10.6Z" fill={color} />
      </>
    );
  }
  return (
    <svg width={14} height={14} viewBox="0 0 14 14" aria-hidden="true" style={{ flexShrink: 0 }}>
      {inner}
    </svg>
  );
}

/* The legacy badge helper: one span, one tone class, no extra wrappers. */
const chip = (tone: string, children: ReactNode, key?: string) => <span key={key} className={cn('badge', tone)}>{children}</span>;
const PHASE_STATE: Record<string, string> = { planned: 'gray', active: 'accent', done: 'green', skipped: 'amber' };
const BOARD_EMPTY: Record<string, string> = { todo: 'Nothing waiting.', 'in-progress': 'Drop a card here to start it.', blocked: 'Nothing is blocked.', done: 'Nothing finished yet.' };

/* The legacy card body: title row, blocked reason and the meta line. Store text is a React
   text node only; the reference chips keep the legacy "m" and "mono" wrappers. */
function BoardCardBody({ task, claim, phaseTitle, outcome, done, openDoc, snapshot }: { task: Data; claim?: Data; phaseTitle: string; outcome?: Outcome[]; done: boolean; openDoc: (id: string) => void; snapshot?: Snapshot }) {
  const refs = linkRefs(task.implements);
  const why = text(task.status) === 'blocked' ? text(task.blocked_why) : '';
  const priority = text(task.priority);
  const who = claim ? text(claim.actor, text(claim.holder, 'claimed')) : '';
  const claimTip = claim ? [`Claimed by ${who}${claim.mine ? ' (you)' : ''}`, claim.expired ? 'lease expired' : ''].filter(Boolean).join(' - ') : '';
  const cardFields = snapshot ? cardFieldsOf(snapshot) : [];
  const extra = cardFields.filter((field) => field && !['status', 'workflow', 'priority', 'title', 'id', 'slug'].includes(field));
  const fieldValue = (field: string): string => {
    const value = task[field] ?? object(task.fields)[field];
    if (Array.isArray(value)) return value.map((entry) => text(entry)).filter(Boolean).join(', ');
    return text(value);
  };
  return (
    <>
      <div className="top">
        {done ? (
          <span className="done-ic" style={{ margin: '2px 7px 0 0', display: 'inline-flex' }} aria-label="Done">
            <StatusGlyph status="done" />
          </span>
        ) : null}
        <button type="button" className="title" style={{ textAlign: 'left' }} onClick={() => openDoc(idOf(task))}>
          {titleOf(task)}
        </button>
      </div>
      {why ? <p className="why">{why}</p> : null}
      <div className="meta">
        <span className="key">{idOf(task)}</span>
        <span className="m faint">{text(task.workflow)}</span>
        {priority ? <span className="m">{chip(priority === 'must' ? 'accent' : 'gray', priorityLabel(priority))}</span> : null}
        {extra.map((field) => {
          const value = fieldValue(field);
          return value ? <span key={field} className="m faint" title={`${field}: ${value}`}>{value}</span> : null;
        })}
        {refs.length ? (
          <span className="m refs mono" data-tip={`Implements ${refs.join(', ')}`}>
            {refs.slice(0, 2).join(' ')}
            {refs.length > 2 ? ` +${refs.length - 2}` : ''}
          </span>
        ) : null}
        {phaseTitle ? (
          <span className="m phase faint" data-tip={phaseTitle}>
            <span className="trunc" title={phaseTitle}>{phaseTitle}</span>
          </span>
        ) : null}
        {outcome && outcome.length ? (
          <span className="m outcome faint" data-tip={'Serves the outcome: ' + outcome.map((entry) => entry.text).join('; ')}>
            <Target size={12} strokeWidth={1.8} aria-hidden="true" className="i" />
            <span className="trunc" title={outcome.map((entry) => entry.text).join('; ')}>{outcomeLabel(outcome[0])}</span>
          </span>
        ) : null}
        {claim ? (
          <span className={cn('m claim', Boolean(claim.expired) && 'expired')} data-tip={claimTip}>
            <Lock size={12} strokeWidth={1.8} aria-hidden="true" className="i" />
            <span className="trunc">{who}</span>
          </span>
        ) : null}
      </div>
    </>
  );
}

function DraggableCard({
  task,
  claim,
  phaseTitle,
  outcome,
  statuses,
  busy,
  openDoc,
  onMove,
  snapshot,
}: {
  task: Data;
  claim?: Data;
  phaseTitle: string;
  outcome?: Outcome[];
  statuses: string[];
  busy: boolean;
  openDoc: (id: string) => void;
  onMove: (task: Data, status: string) => void;
  onOpenWorkspace: (task: Data) => void;
  snapshot?: Snapshot;
}) {
  const id = idOf(task);
  const selectId = useId();
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id, disabled: busy, data: { status: text(task.status) } });
  const done = text(task.category) === 'done';
  /* The status picker and keyboard grip live in the legacy hover-actions corner: invisible at
     rest (so the card matches the legacy markup pixel for pixel) but revealed on focus, so
     keyboard and assistive-tech users still reach the same move affordances. */
  const [actions, setActions] = useState(false);
  const options = Array.isArray(task.states) && task.states.length
    ? task.states.filter((state): state is string => typeof state === 'string')
    : statuses;
  return (
    <div
      ref={setNodeRef}
      className={cn('kcard rail-card-wrap', done && 'done', isDragging && 'dragging')}
      data-drag-card={id}
      onFocus={() => setActions(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setActions(false);
      }}
    >
      <BoardCardBody task={task} claim={claim} phaseTitle={phaseTitle} outcome={outcome} done={done} openDoc={openDoc} snapshot={snapshot} />
      <div className="rail-card-actions" style={{ position: 'absolute', top: 6, right: 6, opacity: actions ? 1 : 0 }}>
        <button
          type="button"
          className="rail-button rail-button-ghost rail-drag-handle"
          aria-label={`Move ${titleOf(task)} with the keyboard`}
          title="Move with the keyboard: press space, then the arrow keys, then space to drop"
          disabled={busy}
          {...listeners}
          {...attributes}
        >
          <GripVertical size={14} strokeWidth={1.8} aria-hidden="true" className="i" />
        </button>
        <button
          type="button"
          className="rail-button rail-button-ghost"
          aria-label={'Open ' + titleOf(task)}
          title="Open the document"
          disabled={busy}
          onClick={() => openDoc(id)}
        >
          <ListChecks size={14} strokeWidth={1.8} aria-hidden="true" />
        </button>
        <label className="sr" htmlFor={selectId}>
          Move to
        </label>
        <select id={selectId} value={text(task.status)} disabled={busy} onChange={(event) => onMove(task, event.target.value)}>
          {options.map((status) => (
            <option key={status} value={status}>
              {titleCase(status)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function BoardColumn({
  status,
  name,
  tasks,
  statuses,
  categories,
  claimFor,
  phaseTitleOf,
  outcomeOf,
  busy,
  doneAll,
  onShowMore,
  openDoc,
  onMove,
  onOpenWorkspace,
  snapshot,
}: {
  status: string;
  name: string;
  tasks: Data[];
  statuses: string[];
  categories: Data;
  claimFor: (id: string) => Data | undefined;
  phaseTitleOf: (task: Data) => string;
  outcomeOf: (task: Data) => Outcome[];
  busy: boolean;
  doneAll: boolean;
  onShowMore: () => void;
  openDoc: (id: string) => void;
  onMove: (task: Data, status: string) => void;
  onOpenWorkspace: (task: Data) => void;
  snapshot?: Snapshot;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${COLUMN_PREFIX}${status}`, data: { status } });
  const column = tasks.filter((task) => text(task.status) === status);
  const isDone = status === 'done' || column.some((task) => text(task.category) === 'done');
  const ordered = isDone ? [...column].sort(recentFirst) : column;
  const shown = isDone && !doneAll ? ordered.slice(0, DONE_LIMIT) : ordered;
  return (
    <section ref={setNodeRef} className={cn('bcol', isOver && 'over')} data-drop-col={status} aria-label={`${name} column`}>
      <div className="bcol-h">
        <StatusGlyph status={status} statuses={statuses} category={text(categories[status])} />
        <span>{name}</span>
        <span className="cnt">{column.length}</span>
      </div>
      <div className="bcol-b" data-col-body={status}>
        {shown.length ? (
          shown.map((task) => (
            <DraggableCard
              key={idOf(task)}
              task={task}
              claim={claimFor(idOf(task))}
              phaseTitle={phaseTitleOf(task)}
              outcome={outcomeOf(task)}
              statuses={statuses}
              busy={busy}
              openDoc={openDoc}
              onMove={onMove}
              onOpenWorkspace={onOpenWorkspace}
              snapshot={snapshot}
            />
          ))
        ) : (
          <p className="bcol-empty">{BOARD_EMPTY[status] ?? 'Nothing here.'}</p>
        )}
        {isDone && column.length > DONE_LIMIT ? (
          <button type="button" className="bcol-more" onClick={onShowMore} aria-expanded={doneAll}>
            {doneAll ? `Show the latest ${DONE_LIMIT} only` : `Show ${column.length - shown.length} more`}
          </button>
        ) : null}
      </div>
    </section>
  );
}

/* The most recently touched first: `updated` is an ISO stamp, so plain string order is time order. */
function recentFirst(a: Data, b: Data): number {
  const left = String(a.updated ?? a.created ?? '');
  const right = String(b.updated ?? b.created ?? '');
  return right.localeCompare(left) || idOf(a).localeCompare(idOf(b));
}

export function BoardPage({ snapshot, project, read, request, refresh, busy, mutate, openDoc, params, navigate }: PageProps) {
  const categories = object(snapshot.status_categories);
  const workflows = rows(snapshot.workflows);
  const unitWorkflow = text(unitOf(snapshot).workflow);
  const requestedWorkflow = params.get('workflow') ?? '';
  const knownWorkflow = (name: string) => workflows.some((entry) => text(entry.name) === name);
  const workflowName = knownWorkflow(requestedWorkflow) ? requestedWorkflow : knownWorkflow(unitWorkflow) ? unitWorkflow : '';
  const unit = unitLabel(snapshot);
  const workflowDoc = workflows.find((entry) => text(entry.name) === workflowName);
  const statuses = Array.isArray(workflowDoc?.states) && workflowDoc!.states.length
    ? workflowDoc!.states.filter((state): state is string => typeof state === 'string')
    : statusesOf(snapshot);
  const requestedPhase = params.get('phase') ?? '';
  const phase = snapshot.phases.some(p => idOf(p) === requestedPhase) ? requestedPhase : '';
  const setPhase = (value: string) => navigate('board', { phase: value, workflow: workflowName, doc: params.get('doc') ?? '' });
  const setWorkflow = (value: string) => navigate('board', { workflow: value, phase: '', doc: params.get('doc') ?? '' });
  const [doneAll, setDoneAll] = useState(false);
  const [notice, setNotice] = useState('');
  const [finishing, setFinishing] = useState<Data | null>(null);
  const [lastResult, setLastResult] = useState<Data | null>(null);
  const [lastResultTask, setLastResultTask] = useState('');
  const [workspace, setWorkspace] = useState<Data | null>(null);
  const [active, setActive] = useState<Data | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));

  const phases = snapshot.phases;
  const hierarchyFirst = hierarchyLabels(snapshot)[0]?.toLowerCase() ?? 'container';
  const all = snapshot.tasks;
  const boardPreset = text(object(snapshot.operating).board_preset) || (() => {
    const screen = rows(object(snapshot.operating).screens).find((entry) => text(entry.kind) === 'board');
    return text(screen?.preset) || text(unitOf(snapshot).preset);
  })();
  const byPreset = useMemo(() => (boardPreset ? all.filter((task) => text(task.preset) === boardPreset) : all), [all, boardPreset]);
  const scoped = useMemo(() => byPreset.filter((task) => !workflowName || text(task.workflow) === workflowName), [byPreset, workflowName]);
  const tasks = useMemo(() => scoped.filter((task) => !phase || phaseRef(task) === phase), [scoped, phase]);
  const openCount = (list: Data[]) => list.filter((task) => text(task.category) !== 'done' && text(task.category) !== 'dropped').length;
  const doneCount = (list: Data[]) => list.filter((task) => text(task.category) === 'done').length;
  const claimFor = useCallback((id: string) => rows(snapshot.claims).find((claim) => text(claim.task) === id), [snapshot]);
  const phaseById = useMemo(() => new Map(phases.map((p) => [idOf(p), p])), [phases]);
  const phaseTitleOf = useCallback((task: Data) => (phase ? '' : titleOf(phaseById.get(phaseRef(task)) ?? {})), [phase, phaseById]);
  /* The outcome each card serves: the goal that names the task, read once. */
  const outcomes = useOutcomes(project, read, busy);
  const outcomeOf = useCallback((task: Data) => outcomes.get(idOf(task)) ?? [], [outcomes]);
  const selectedPhase = phase ? phaseById.get(phase) : undefined;

  /* The only place a status becomes a tool call. Arguments mirror the legacy moveTask exactly. */
  const move = useCallback(
    async (task: Data, status: string) => {
      const id = idOf(task);
      if (!id || busy || text(task.status) === status) return;
      setNotice('');
      try {
        await mutate('change move', { slug: id, status, expect: text(task.etag) });
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error));
      }
    },
    [busy, mutate],
  );
  const finish = useCallback(async (payload: Record<string, unknown>) => {
    try {
      const result = await mutate('done', payload, payload.skip_verify !== true);
      setLastResult(result);
      setLastResultTask(text(payload.task));
      setFinishing(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    }
  }, [mutate]);
  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActive(null);
      const overId = event.over ? String(event.over.id) : '';
      if (!overId.startsWith(COLUMN_PREFIX)) return;
      const task = tasks.find((candidate) => idOf(candidate) === String(event.active.id));
      if (task) void move(task, overId.slice(COLUMN_PREFIX.length));
    },
    [move, tasks],
  );
  const phaseOption = (p: Data) => {
    const list = scoped.filter((task) => phaseRef(task) === idOf(p));
    const open = openCount(list);
    return (
      <option key={idOf(p)} value={idOf(p)}>
        {`${titleOf(p)} · ${list.length === 0 ? 'no items' : open ? `${open} open` : `${list.length} done`}`}
      </option>
    );
  };
  const progress = tasks.length ? fractionPct(doneCount(tasks) / tasks.length) ?? 0 : 0;

  return (
    <>
      <div className="ph" style={{ padding: '16px 20px 0' }}>
        <div>
          <h1>{screenTitle(snapshot, 'board', 'Board')}</h1>
          <p>{`Drag a ${unit} to a column to move it, or open a card and pick a status. The item's workflow decides what is legal; a refused move puts the card back and says why.`}</p>
        </div>
      </div>
      <p className="sr">A drop asks the server, never the screen. Nothing moves until the store confirms it. Keyboard: focus a card's grip, press space, use the arrow keys, then space to drop. Each card also has a status picker.</p>
      {notice ? (
        <div role="alert" className="offline-bar">
          <TriangleAlert size={15} strokeWidth={1.8} aria-hidden="true" className="i" />
          <span>
            <b>The store refused that move.</b>
            <span> {notice}</span>
          </span>
        </div>
      ) : null}
      <div className="board-bar">
        {workflows.length > 1 ? (
          <div className="row" style={{ gap: 8 }}>
            <label className="faint" htmlFor="board-workflow">Workflow</label>
            <select className="select" id="board-workflow" value={workflowName} onChange={(event) => setWorkflow(event.target.value)} disabled={busy}>
              <option value="">{`All workflows · ${plural(all.length, 'item')}`}</option>
              {workflows.map((entry) => (
                <option key={text(entry.name)} value={text(entry.name)}>
                  {`${text(entry.name)} · ${plural(all.filter((task) => text(task.workflow) === text(entry.name)).length, 'item')}`}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div className="row" style={{ gap: 8 }}>
          <label className="faint" htmlFor="board-phase">
            {titleCase(hierarchyFirst)}
          </label>
          <select className="select" id="board-phase" value={phase} onChange={(event) => setPhase(event.target.value)} disabled={busy}>
            <option value="">{`All ${hierarchyFirst}s · ${openCount(scoped)} open`}</option>
            {phases.filter((p) => openCount(scoped.filter((task) => phaseRef(task) === idOf(p))) > 0).length ? (
              <optgroup label="With open work">
                {phases.filter((p) => openCount(scoped.filter((task) => phaseRef(task) === idOf(p))) > 0).map(phaseOption)}
              </optgroup>
            ) : null}
            {phases.filter((p) => openCount(scoped.filter((task) => phaseRef(task) === idOf(p))) === 0).length ? (
              <optgroup label="Finished">
                {phases.filter((p) => openCount(scoped.filter((task) => phaseRef(task) === idOf(p))) === 0).map(phaseOption)}
              </optgroup>
            ) : null}
          </select>
        </div>
        <span className="scope">
          {selectedPhase ? (
            <>
              <span className="prog" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
                <i style={{ '--p': Math.max(0, Math.min(100, progress ?? 0)) / 100 } as CSSProperties} />
              </span>
              <span className="num">{`${doneCount(tasks)}/${tasks.length} done`}</span>
              {chip(PHASE_STATE[text(selectedPhase.state)] ?? 'gray', text(selectedPhase.state, 'planned'))}
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPhase('')}>
                Show all {hierarchyFirst}s
              </button>
            </>
          ) : (
            <span className="num">{`${openCount(scoped)} open, ${doneCount(scoped)} done in ${phases.length} ${hierarchyFirst}${phases.length === 1 ? '' : 's'}`}</span>
          )}
        </span>
        {busy ? <span className="faint">Working...</span> : null}
      </div>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={(event: DragStartEvent) => setActive(tasks.find((task) => idOf(task) === String(event.active.id)) ?? null)}
        onDragEnd={onDragEnd}
        onDragCancel={() => setActive(null)}
        accessibility={{
          announcements: {
            ...defaultAnnouncements,
            onDragStart: ({ active: dragged }) => `Picked up task ${String(dragged.id)}.`,
            onDragOver: ({ active: dragged, over }) => (over ? `Task ${String(dragged.id)} is over ${String(over.id).replace(COLUMN_PREFIX, '')}.` : `Task ${String(dragged.id)} is no longer over a column.`),
            onDragEnd: ({ active: dragged, over }) =>
              over ? `Task ${String(dragged.id)} dropped on ${String(over.id).replace(COLUMN_PREFIX, '')}; the store decides whether it moves.` : `Task ${String(dragged.id)} was dropped outside a column and did not move.`,
          },
          screenReaderInstructions: {
            draggable: 'To move a task, focus its grip and press space, use the arrow keys to reach a column, then press space to drop. Escape cancels. Each card also has a status picker.',
          },
        }}
      >
        <div className="board rail-board-grid" data-keep="board" aria-busy={busy}>
          {statuses.map((status) => (
            <BoardColumn
              key={status}
              status={status}
              name={titleCase(status)}
              tasks={tasks}
              statuses={statuses}
              categories={categories}
              claimFor={claimFor}
              phaseTitleOf={phaseTitleOf}
              outcomeOf={outcomeOf}
              busy={busy}
              doneAll={doneAll}
              onShowMore={() => setDoneAll((value) => !value)}
              openDoc={openDoc}
              onMove={(task, status2) => void move(task, status2)}
              onOpenWorkspace={setWorkspace}
              snapshot={snapshot}
            />
          ))}
        </div>
        <DragOverlay dropAnimation={null}>
          {active ? (
            <div className="kcard">
              <BoardCardBody task={active} claim={claimFor(idOf(active))} phaseTitle={phaseTitleOf(active)} outcome={outcomeOf(active)} done={text(active.status) === 'done'} openDoc={openDoc} snapshot={snapshot} />
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
      {/* The legacy done confirmation (actions.js CONFIRM_TEXT.done + overlays/layer.js
          confirmHtml): the shared .scrim/.modal-wrap/.modal.sm geometry, role alertdialog,
          title "Finish this task?", Cancel and Run. Cancel only closes: it never POSTs. */}
      {lastResult ? <FinishResultPanel result={lastResult} onReload={refresh ? () => void refresh() : undefined} /> : null}
      {finishing !== null ? (
        <FinishDialog
          task={finishing}
          busy={busy}
          onSubmit={(payload) => void finish(payload)}
          onClose={() => setFinishing(null)}
        />
      ) : null}
      {workspace !== null ? (
        <TaskWorkspaceDrawer
          task={workspace}
          project={project}
          read={read}
          request={request}
          busy={busy}
          openDoc={openDoc}
          result={lastResult && lastResultTask === idOf(workspace) ? lastResult : null}
          onRecordCorrection={() => { navigate('rules', { task: idOf(workspace) }); setWorkspace(null); }}
          onClose={() => setWorkspace(null)}
        />
      ) : null}
    </>
  );
}

/* =====================================================================
   NOW
   ===================================================================== */
/* The legacy cursor section (core.js pageNow section()): a panel head plus one padded
   inline body - store markdown when there is text, otherwise one faint line with the
   section's own glyph. */
function CursorSection({ title, text: body, icon }: { title: string; text: unknown; icon: ReactNode }) {
  const prose = text(body).trim();
  return (
    <section className="panel">
      <div className="panel-h"><h2>{title}</h2></div>
      <div style={{ padding: '4px 14px 12px' }}>
        {prose ? <Markdown>{prose}</Markdown> : <span className="faint rail-inline-note">{icon}Nothing here.</span>}
      </div>
    </section>
  );
}

const CURSOR_LEFT: [string, string, ReactNode][] = [
  ['In progress', 'in_progress', <Loader key="in" size={13} aria-hidden="true" />],
  ['Blocked', 'blocked', <Ban key="blocked" size={13} aria-hidden="true" />],
  ['Next', 'next', <ArrowRight key="next" size={13} aria-hidden="true" />],
];
const CURSOR_RIGHT: [string, string, ReactNode][] = [
  ['Completed', 'completed', <CircleCheck key="completed" size={13} aria-hidden="true" />],
];

export function NowPage({ snapshot, project, read, openDoc, navigate, busy }: PageProps) {
  const next = useQuery({ queryKey: readKey(project, 'next'), queryFn: ({ signal }) => read('next', {}, signal), retry: false, enabled: !busy });
  const tasks = snapshot.tasks;
  const cursor = object(snapshot.cursor);
  const claims = rows(snapshot.claims);
  const count = (category: string) => tasks.filter((task) => text(task.category) === category).length;
  const queue = rows(object(next.data).queue);
  const nextError = text(object(next.data).error);
  const focus = focusName(snapshot);
  /* The outcome each queued task serves: the parent context the reader needs
     before they act, read once and shared with the drawer. */
  const outcomes = useOutcomes(project, read, busy);
  const outcomeOf = useCallback((id: string) => outcomes.get(id) ?? [], [outcomes]);
  /* The legacy Now page (pages/core.js pageNow) is transcribed here rather than composed
     from the generic Panel/Row primitives: the cursor sections use a padded inline body, the
     queue and claim rows are legacy .mini rows, and the "Up next"/"Claims" panels carry no
     description. */
  return (
    <div>
      <div className="ph">
        <div>
          <h1>Now</h1>
          {/* The legacy chain is `status_summary || state || default`: an empty status_summary
              falls through, where text()'s fallback would keep the empty string. */}
          <p>{text(object(snapshot.project).status_summary) || text(object(snapshot.project).state) || 'Where the project stands right now.'}</p>
        </div>
      </div>
      <div className="stats" style={{ marginBottom: 16 }}>
        <div className="stat">
          <span className="k"><CircleDashed size={14} aria-hidden="true" />To do</span>
          <span className="v">{count('not_started')}</span>
          <span className="d">{count('done')} of {tasks.length} items done</span>
        </div>
        <div className="stat">
          <span className="k"><Loader size={14} aria-hidden="true" />In progress</span>
          <span className="v">{count('active')}</span>
          <span className="d">{count('active') ? 'work is in flight' : 'nothing is started'}</span>
        </div>
        <div className="stat">
          <span className="k"><Ban size={14} aria-hidden="true" />Blocked</span>
          <span className="v" style={count('blocked') ? { color: 'var(--red)' } : undefined}>{count('blocked')}</span>
          <span className="d">{count('blocked') ? 'waiting to be unblocked' : 'nothing is stuck'}</span>
        </div>
        <button type="button" className="stat" onClick={() => navigate('roadmap')}>
          <span className="k"><CircleCheck size={14} aria-hidden="true" />Completed</span>
          <span className="v">{count('done')}</span>
          <span className="d">{count('done') ? 'items are closed' : 'nothing finished yet'}</span>
        </button>
      </div>
      <div className="grid2">
        <div className="stack">
          {CURSOR_LEFT.map(([label, key, icon]) => (
            <CursorSection key={key} title={label} text={cursor[key]} icon={icon} />
          ))}
        </div>
        <div className="stack">
          <section className="panel">
            <div className="panel-h"><h2>Up next</h2>{focus ? <div className="acts"><span className="faint">{'Focus: ' + focus}</span></div> : null}</div>
            {next.isError ? (
              <ErrorState error={next.error.message} retry={() => void next.refetch()} />
            ) : next.isPending ? (
              <div style={{ padding: '12px 14px' }} className="faint">Loading...</div>
            ) : nextError ? (
              <div style={{ padding: '12px 14px' }} className="faint" role="alert">{nextError}</div>
            ) : queue.length ? (
              queue.slice(0, 6).map((entry) => {
                const outcome = outcomeOf(idOf(entry));
                return (
                  <div className="mini" key={idOf(entry)} role="button" tabIndex={0} onClick={() => openDoc(idOf(entry))}>
                    <StatusGlyph status={text(entry.status)} statuses={statusesOf(snapshot)} category={text(entry.category)} />
                    <div className="grow">
                      <div className="trunc" style={{ fontWeight: 500 }} title={titleOf(entry)}>{titleOf(entry)}</div>
                      <div className="faint trunc" style={{ fontSize: 12 }} title={text(entry.why)}>{text(entry.why)}</div>
                      {entry.focus ? <div className="faint" style={{ fontSize: 12 }}>in focus</div> : null}
                      {Array.isArray(entry.actions) && entry.actions.length ? (
                        <div className="faint trunc" style={{ fontSize: 12 }} title={entry.actions.join(', ')}>
                          {'actions: ' + entry.actions.join(', ')}
                        </div>
                      ) : null}
                      {outcome.length ? (
                        <div className="faint trunc" style={{ fontSize: 12 }} title={'Serves the outcome: ' + outcome.map((o) => o.text).join('; ')}>
                          {'serves ' + outcomeLabel(outcome[0])}
                        </div>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      className="ibtn ibtn-sm"
                      aria-label={'Open ' + titleOf(entry)}
                      title="Open the document"
                      onClick={(event) => { event.stopPropagation(); openDoc(idOf(entry)); }}
                    >
                      <ListChecks size={14} strokeWidth={1.8} aria-hidden="true" />
                    </button>
                  </div>
                );
              })
            ) : (
              <div style={{ padding: '12px 14px' }} className="faint">The queue is empty.</div>
            )}
            {next.data && text(object(next.data).state) ? (
              <div style={{ padding: '8px 14px', borderTop: '1px solid var(--divider)' }} className="faint">
                {'State: '}
                {chip(['blocked', 'stuck'].includes(text(object(next.data).state)) ? 'red' : text(object(next.data).state) === 'done' ? 'green' : 'accent', text(object(next.data).state))}
                {' '}
                {readableScore(text(object(next.data).score))}
              </div>
            ) : null}
          </section>
          <section className="panel">
            <div className="panel-h"><h2>Claims</h2></div>
            {claims.length ? (
              claims.map((claim) => (
                <div className="mini" key={text(claim.task)} role="button" tabIndex={0} onClick={() => openDoc(text(claim.task))}>
                  <Lock size={14} aria-hidden="true" />
                  <div className="grow">
                    <div className="trunc">{text(claim.task)}</div>
                    <div className="faint trunc" style={{ fontSize: 12 }}>
                      {text(claim.actor, text(claim.holder, ''))}
                      {claim.mine ? ' (you)' : ''}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="ibtn ibtn-sm"
                    aria-label={'Open ' + text(claim.task)}
                    title="Open the document"
                    onClick={(event) => { event.stopPropagation(); openDoc(text(claim.task)); }}
                  >
                    <ListChecks size={14} strokeWidth={1.8} aria-hidden="true" />
                  </button>
                </div>
              ))
            ) : (
              <div style={{ padding: '12px 14px' }} className="faint">No task is claimed.</div>
            )}
          </section>
          {CURSOR_RIGHT.map(([label, key, icon]) => (
            <CursorSection key={key} title={label} text={cursor[key]} icon={icon} />
          ))}
        </div>
      </div>
    </div>
  );
}

/* =====================================================================
   ROADMAP
   ===================================================================== */
export function RoadmapPage({ snapshot, project, read, openDoc, busy }: PageProps) {
  const phases = snapshot.phases;
  const tasks = snapshot.tasks;
  const milestones = rows(snapshot.milestones);
  const hierarchy = hierarchyLabels(snapshot);
  const levelPreset = (() => {
    const level = hierarchyLevels(snapshot)[0];
    return level ? text(level.preset) : '';
  })();
  const singular = levelPreset ? labelOf(snapshot, levelPreset).toLowerCase() : (hierarchy[0] || 'container').toLowerCase();
  const pluralWord = levelPreset ? labelOf(snapshot, levelPreset, true).toLowerCase() : `${singular}s`;
  /* The outcome a phase's work serves: the parent context of the work, not a
     second copy of the outcome's status. */
  const outcomes = useOutcomes(project, read, busy);
  const outcomeOf = useCallback((id: string) => outcomes.get(id) ?? [], [outcomes]);
  /* A phase reference may be a plain id, a [[wikilink]] or scope.phase, so resolve it exactly
     like the board does - a plain string compare loses every task of a linked phase. */
  /* Depth-first walk of the phase's tree: a phase holds stories, a story holds its tasks;
     cycles and the same document under two parents stop the walk. */
  const treeOf = (parentId: string, depth = 0, seen: Set<string> = new Set()): { row: Data; depth: number }[] =>
    tasks
      .filter((task) => phaseRef(task) === parentId && !seen.has(idOf(task)))
      .flatMap((task) => {
        const childSeen = new Set(seen);
        childSeen.add(idOf(task));
        return [{ row: task, depth }, ...treeOf(idOf(task), depth + 1, childSeen)];
      });
  /* The legacy Roadmap page (pages/core.js pageRoadmap): each phase is one panel whose head
     links the phase document, with the run progress bar and the task progress, then at most
     eight task rows and the exact remainder. */
  return (
    <div>
      <div className="ph">
        <div>
          <h1>{screenTitleForRoute(snapshot, 'roadmap', 'Plan')}</h1>
          <p>{`${plural(phases.length, singular, pluralWord)}, in order.`}</p>
          {/* Containers are pack work that holds other work; completion is progress, not proof. */}
          <p className="faint">
            Items completed is work, not proof. Gates are shown on each item's workflow card.
          </p>
        </div>
      </div>
      {phases.length ? (
        <div className="stack">
          {phases.map((p) => {
            const tree = treeOf(idOf(p));
            const list = tree.map((entry) => entry.row);
            const done = list.filter((task) => text(task.category) === 'done').length;
            const share = list.length ? fractionPct(done / list.length) ?? 0 : 0;
            return (
              <section className="panel" key={idOf(p)}>
                <div className="panel-h">
                  <h2><DocLink id={idOf(p)} label={titleOf(p)} openDoc={openDoc} /></h2>
                  <div className="acts">{chip(PHASE_STATE[text(p.state)] ?? 'gray', text(p.state, 'planned'))}</div>
                </div>
                <div style={{ padding: '6px 14px 12px' }}>
                  <div className="row" style={{ gap: 10 }}>
                    <span className="prog" role="progressbar" aria-valuenow={share} aria-valuemin={0} aria-valuemax={100}>
                      <i style={{ '--p': share / 100 } as CSSProperties} />
                    </span>
                    <span className="num faint" style={{ fontSize: 12, whiteSpace: 'nowrap' }} title="Item progress, not proof">
                      {list.length ? <>{'Items completed '}{done}{'/'}{list.length}{' ('}{share}{'%)'}</> : 'No item is filed under this container'}
                    </span>
                  </div>
                  {text(p.description) ? (
                    <div className="md" style={{ marginTop: 8 }}>
                      <Markdown>{text(p.description)}</Markdown>
                    </div>
                  ) : null}
                </div>
                {tree.slice(0, 8).map(({ row: task, depth }) => {
                  const outcome = outcomeOf(idOf(task));
                  return (
                    <div className="mini" key={idOf(task)} role="button" tabIndex={0} onClick={() => openDoc(idOf(task))} style={depth ? { paddingLeft: 14 + depth * 18, borderLeft: '1px solid var(--divider)' } : undefined}>
                      <StatusGlyph status={text(task.status)} statuses={statusesOf(snapshot)} category={text(task.category)} />
                      <span className="grow trunc" title={titleOf(task)}>{titleOf(task)}</span>
                      {depth ? null : outcome.length ? (
                        <span className="faint trunc" style={{ fontSize: 12, maxWidth: 160 }} title={'Serves the outcome: ' + outcome.map((o) => o.text).join('; ')}>
                          {outcomeLabel(outcome[0])}
                        </span>
                      ) : null}
                      <span className="faint" style={{ fontSize: 12 }}>{text(task.status)}</span>
                    </div>
                  );
                })}
                {tree.length > 8 ? <div className="faint" style={{ padding: '6px 14px' }}>{'+' + String(tree.length - 8) + ' more on the board'}</div> : null}
              </section>
            );
          })}
        </div>
      ) : (
        <LegacyEmptyBlock icon={<RouteGlyph size={20} aria-hidden="true" />} title={`No ${pluralWord} yet`} text="Add one with rail change add <preset> &quot;...&quot;." />
      )}
      {milestones.length ? (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-h"><h2>Milestones</h2></div>
          {milestones.map((milestone) => (
            <div className="mini" key={idOf(milestone)} role="button" tabIndex={0} onClick={() => openDoc(idOf(milestone))}>
              <Flag size={14} aria-hidden="true" />
              <span className="grow trunc">{titleOf(milestone)}</span>
            </div>
          ))}
        </section>
      ) : null}
    </div>
  );
}

/* =====================================================================
   SCOPE
   ===================================================================== */
export function ScopePage({ snapshot, project, read, openDoc, busy }: PageProps) {
  const roll = useQuery({ queryKey: readKey(project, 'scopeScore'), queryFn: ({ signal }) => read('scope_score', {}, signal), retry: false, enabled: !busy });
  const items = snapshot.scope_items;
  const rollData = object(roll.data);
  const scoreById = useMemo(() => new Map(rows(rollData.items).map((item) => [text(item.id, text(item.item)), item])), [rollData.items]);
  const priorities = priorityOrderOf(snapshot);
  /* The legacy Scope page (pages/core.js pageScope): one panel per priority group, each row a
     legacy .mini that leads with the item id, then the claim pill, the proven share and - only
     when it says something else - the ticked share. */
  return (
    <div>
      <div className="ph">
        <div>
          <h1>{screenTitle(snapshot, 'scope', 'Scope')}</h1>
          {/* A refused score read never reads as healthy proof: the headline says the number is
              not assessed, and the shell's offline bar is the visible read failure (Lead-owned). */}
          <p>{roll.isError ? evidenceTitle('proven') + ' ' + evidenceWord('not_assessed') : roll.data ? scoreLine(rollData) : 'What must be true, and how much of it is proven.'}</p>
        </div>
      </div>
      {priorities.map((priority) => {
        const group = items.filter((item) => text(item.priority) === priority);
        if (!group.length) return null;
        return (
          <section className="panel" key={priority} style={{ marginBottom: 16 }}>
            <div className="panel-h">
              <h2>{priorityLabel(priority)}</h2>
              <div className="acts"><span className="faint">{group.length}</span></div>
            </div>
            {group.map((item) => {
              const score = scoreById.get(idOf(item));
              const itemProven = score ? provenPct(score) : null;
              const itemChecked = score ? checkedPct(score) : null;
              const notProven = score ? rows(score.checks).filter((check) => check.checked && !['pass', 'pending'].includes(text(check.state))) : [];
              return (
                <div className="mini rail-scope-row" key={idOf(item)} role="button" tabIndex={0} onClick={() => openDoc(idOf(item))}>
                  <span className="mono faint" style={{ fontSize: 11.5, minWidth: 46 }}>{idOf(item)}</span>
                  <div className="grow">
                    <div className="trunc" style={{ fontWeight: 500 }} title={titleOf(item)}>{titleOf(item)}</div>
                  </div>
                  <span className="rail-scope-claim">{text(item.claim) ? chip('gray', text(item.claim)) : null}</span>
                  <span className="num faint rail-scope-proven">
                    {itemProven !== null ? EVIDENCE.proven.word + ' ' + String(itemProven) + '%' : EVIDENCE.proven.word + ' ' + EVIDENCE.not_assessed.word}
                  </span>
                  <span className="faint rail-scope-checked" style={{ fontSize: 12 }} title={itemChecked !== null && itemChecked !== itemProven ? 'Ticked checks, proven or not' + (notProven.length ? ': ' + String(notProven.length) + ' not proven' : '') : undefined}>
                    {itemChecked !== null && itemChecked !== itemProven ? EVIDENCE.checked.word + ' ' + String(itemChecked) + '%' : ''}
                  </span>
                </div>
              );
            })}
          </section>
        );
      })}
      {items.length ? null : <LegacyEmptyBlock icon={<Target size={20} aria-hidden="true" />} title="No scope items yet" text='Add one with rail change add <scope preset> "..." --set priority=must.' />}
    </div>
  );
}

/* =====================================================================
   DECISIONS
   ===================================================================== */
export function DecisionsPage({ snapshot, openDoc }: PageProps) {
  const decisions = snapshot.decisions;
  const risks = snapshot.risks;
  const questions = snapshot.questions;
  const otherRecords = rows(snapshot.records).filter(
    (doc) => !['decision', 'risk', 'question'].includes(text(doc.preset)),
  );
  const byPreset = useMemo(() => {
    const map = new Map<string, Data[]>();
    for (const doc of otherRecords) {
      const preset = text(doc.preset) || 'other';
      map.set(preset, [...(map.get(preset) ?? []), doc]);
    }
    return [...map.entries()];
  }, [snapshot.records]);
  const isOpen = (doc: Data) => !['done', 'dropped'].includes(text(doc.category));
  const openRisks = risks.filter(isOpen).length;
  const openQuestions = questions.filter(isOpen).length;
  const title = screenTitle(snapshot, 'records', 'Records');
  /* The legacy Decisions page plus the pack's other record presets: records first, then the
     risk/question concerns in a two-column grid, every row a legacy .mini. */
  return (
    <div>
      <div className="ph">
        <div>
          <h1>{title}</h1>
          <p>{plural(decisions.length + otherRecords.length, 'record')}, {plural(openRisks, 'open risk')}, {plural(openQuestions, 'open question')}.</p>
        </div>
      </div>
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h"><h2>Decisions</h2></div>
        {decisions.length ? (
          decisions.map((decision) => (
            <div className="mini" key={idOf(decision)} role="button" tabIndex={0} onClick={() => openDoc(idOf(decision))}>
              <div className="grow">
                <div className="trunc" style={{ fontWeight: 500 }} title={text(decision.question, titleOf(decision))}>{text(decision.question, titleOf(decision))}</div>
                <div className="faint trunc" style={{ fontSize: 12 }} title={text(decision.choice)}>{text(decision.choice)}</div>
              </div>
              {chip(DECISION_STATUS_CLASS[text(decision.status)] ?? 'gray', text(decision.status))}
            </div>
          ))
        ) : (
          <div style={{ padding: '12px 14px' }} className="faint">None.</div>
        )}
      </section>
      {byPreset.map(([preset, list]) => (
        <section className="panel" style={{ marginBottom: 16 }} key={preset}>
          <div className="panel-h"><h2>{labelOf(snapshot, preset, true)}</h2></div>
          {list.map((record) => (
            <div className="mini" key={idOf(record)} role="button" tabIndex={0} onClick={() => openDoc(idOf(record))}>
              <span className="grow trunc" title={titleOf(record)}>{titleOf(record)}</span>
              <span className="faint" style={{ fontSize: 12 }}>{labelOf(snapshot, text(record.preset), true)}</span>
            </div>
          ))}
        </section>
      ))}
      <div className="grid2 decisions-grid">
        <section className="panel">
          <div className="panel-h"><h2>Risks</h2></div>
          {risks.length ? (
            risks.map((risk) => (
              <div className="mini" key={idOf(risk)} role="button" tabIndex={0} onClick={() => openDoc(idOf(risk))}>
                <span className="grow trunc" title={titleOf(risk)}>{titleOf(risk)}</span>
                {chip(RISK_SEVERITY_CLASS[text(risk.severity)] ?? 'gray', text(risk.severity))}
                {chip(isOpen(risk) ? 'amber' : 'green', isOpen(risk) ? 'open' : 'closed')}
              </div>
            ))
          ) : (
            <div style={{ padding: '12px 14px' }} className="faint">None.</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-h"><h2>Questions</h2></div>
          {questions.length ? (
            questions.map((question) => (
              <div className="mini" key={idOf(question)} role="button" tabIndex={0} onClick={() => openDoc(idOf(question))}>
                <span className="grow trunc" title={text(question.question, titleOf(question))}>{text(question.question, titleOf(question))}</span>
                {chip(isOpen(question) ? 'amber' : 'green', isOpen(question) ? 'open' : 'closed')}
              </div>
            ))
          ) : (
            <div style={{ padding: '12px 14px' }} className="faint">None.</div>
          )}
        </section>
      </div>
    </div>
  );
}

/* =====================================================================
   LENS - one task's context in rings, and the work order the agent gets
   ===================================================================== */
const LENS_RINGS: { key: string; label: string; icon: ReactNode; hint: string }[] = [
  { key: 'why', label: 'Why', icon: <Target size={15} strokeWidth={1.8} aria-hidden="true" className="i" />, hint: 'The scope items this task delivers' },
  { key: 'what', label: 'What', icon: <ListChecks size={15} strokeWidth={1.8} aria-hidden="true" className="i" />, hint: 'Acceptance: when it counts as done' },
  { key: 'how', label: 'How', icon: <GitBranch size={15} strokeWidth={1.8} aria-hidden="true" className="i" />, hint: 'Decisions to follow, ones rejected, conventions' },
  { key: 'plan', label: 'Plan', icon: <ClipboardCheck size={15} strokeWidth={1.8} aria-hidden="true" className="i" />, hint: 'The scope plan and its gate' },
  { key: 'reach', label: 'Reach', icon: <RouteGlyph size={15} strokeWidth={1.8} aria-hidden="true" className="i" />, hint: 'Files, and what else the change reaches' },
  { key: 'proof', label: 'Proof', icon: <ShieldCheck size={15} strokeWidth={1.8} aria-hidden="true" className="i" />, hint: 'The cmd: checks and the recorded runs' },
  { key: 'open', label: 'Open', icon: <CircleHelp size={15} strokeWidth={1.8} aria-hidden="true" className="i" />, hint: 'Risks and questions' },
];

function LensRow({icon,main,extra}:{icon:ReactNode;main:ReactNode;extra?:ReactNode}) {
  return <div className="mini lens-row" style={{cursor:'default',alignItems:'flex-start'}}>{icon}<div className="grow" style={{minWidth:0,overflowWrap:'anywhere'}}>{main}</div>{extra}</div>;
}
function LensItem({ item, openDoc }: { item: Data; openDoc: (id: string) => void }) {
  const kind = text(item.kind);
  switch (kind) {
    case 'goal':
      return (
        <LensRow
          icon={<Flag size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={
            <>
              <span className="faint rail-muted">Goal</span> {text(item.text)}
            </>
          }
        />
      );
    case 'scope_item': {
      const proof = provenPct(item);
      const priority = text(item.priority);
      return (
        <LensRow
          icon={<Target size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={
            <>
              <DocLink id={text(item.id)} label={`${text(item.id)} ${text(item.title)}`} openDoc={openDoc} />
              <Sub value={item.acceptance} />
            </>
          }
          extra={
            <>
              {priority ? <Badge tone={PRIORITY_TONE[priority] ?? 'neutral'}>{priority}</Badge> : null}
              {proof === null ? null : <span className="num faint" style={{fontSize:12}}>proven {pctLabel(item.proven)}</span>}
            </>
          }
        />
      );
    }
    case 'acceptance':
      return <LensRow icon={<Check size={14} strokeWidth={1.8} aria-hidden="true" className="i" />} main={<><span>{text(item.text)}</span><Sub value={item.source} /></>} />;
    case 'follow':
    case 'avoid':
      return (
        <LensRow
          icon={kind === 'follow' ? <CircleCheck size={14} strokeWidth={1.8} aria-hidden="true" className="i" /> : <CircleX size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={
            <>
              <DocLink id={text(item.id)} label={titleOf(item)} openDoc={openDoc} />
              <Sub value={text(item.choice) ? `${kind === 'avoid' ? `${text(item.status)}: ` : ''}${text(item.choice)}` : ''} />
              <Sub value={item.rationale} />
            </>
          }
          extra={<Badge tone={kind === 'follow' ? 'good' : 'neutral'}>{kind === 'follow' ? 'follow' : text(item.status, 'avoid')}</Badge>}
        />
      );
    case 'convention':
      return (
        <LensRow
          icon={<BookOpen size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={
            <>
              <b>{text(item.key).replace(/_/g, ' ')}</b> {text(item.text)}
            </>
          }
        />
      );
    case 'plan':
      return (
        <LensRow
          icon={<ClipboardCheck size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={
            <>
              {text(item.plan) ? <DocLink id={text(item.plan)} openDoc={openDoc} /> : <span>{text(item.item)}: no plan</span>}
              <Sub value={Object.entries(object(item.labels)).map(([key, value]) => `${text(value)} ${key}`).join(', ')} />
            </>
          }
          extra={
            <>
              <Badge tone={item.ready ? 'good' : item.required ? 'danger' : 'neutral'}>{text(item.status, 'none')}</Badge>
              {item.required ? null : <Badge tone="neutral">optional</Badge>}
            </>
          }
        />
      );
    case 'file':
      return (
        <LensRow
          icon={<FileGlyph size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={<span className="mono rail-code" style={{fontSize:12}}>{text(item.path)}</span>}
          extra={item.exists ? null : <Badge tone="warning">not created yet</Badge>}
        />
      );
    case 'also_affects':
      return (
        <LensRow
          icon={<GitMerge size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={
            <>
              <DocLink id={text(item.id)} label={`${text(item.id)} ${text(item.title)}`} openDoc={openDoc} />
              <Sub value={stringList(item.rerun).length ? `keep green: ${stringList(item.rerun).join(' ; ')}` : ''} />
            </>
          }
          extra={<Badge tone={PRIORITY_TONE[text(item.priority)] ?? 'neutral'}>{text(item.priority)}</Badge>}
        />
      );
    case 'check':
      return (
        <LensRow
          icon={<Terminal size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={
            <>
              <span>{text(item.text)}</span>
              <div className="mono faint" style={{fontSize:11.5,overflowWrap:'anywhere'}}>{text(item.command)}</div>
              <Sub value={item.detail} />
            </>
          }
          extra={<Badge tone={CHECK_TONE[text(item.state)] ?? 'neutral'}>{text(item.state, 'pending')}</Badge>}
        />
      );
    case 'risk':
    case 'question':
      return (
        <LensRow
          icon={kind === 'risk' ? <TriangleAlert size={14} strokeWidth={1.8} aria-hidden="true" className="i" /> : <CircleHelp size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
          main={<DocLink id={text(item.id)} label={titleOf(item)} openDoc={openDoc} />}
          extra={<Badge tone="warning">{kind}</Badge>}
        />
      );
    default:
      /* An unknown kind is shown as compact JSON text, never dropped and never evaluated. */
      return <LensRow icon={<Circle size={14} strokeWidth={1.8} aria-hidden="true" className="i" />} main={<span className="mono rail-code" style={{fontSize:12}}>{JSON.stringify(item).slice(0, 240)}</span>} />;
  }
}

function LensRing({ ring, data, gap, openDoc }: { ring: { key: string; label: string; icon: ReactNode; hint: string }; data: Data; gap?: Data; openDoc: (id: string) => void }) {
  const items = rows(data.items);
  const isOpen = ring.key === 'open';
  const emptyFlag = data.empty === true && !isOpen;
  return (
    <section className="panel lens-ring" data-ring={ring.key}><header className="panel-h">{ring.icon}<h2>{ring.label}</h2><div className="acts">{emptyFlag ? <Badge tone="warning">empty</Badge> : <span className="faint num">{items.length}</span>}</div></header>
      {items.length ? items.map((item,index)=><LensItem key={`${ring.key}-${index}`} item={item} openDoc={openDoc}/>) : <div className="faint" style={{padding:'4px 14px 12px'}}>{isOpen ? 'Nothing open: no risk or question. That is good news.' : ring.hint}</div>}
      {text(data.fill) ? <div className="lens-fill"><span className="faint">{isOpen?'Record one':'Fill it'}:</span> <code>{text(data.fill)}</code>{gap?.needs_input === true ? <> <Badge tone="warning">fill the &lt;placeholder&gt; first</Badge></> : null}{gap&&<div className="faint" style={{fontSize:12}}>{text(gap.reason)}</div>}</div>:null}
    </section>
  );
}
export function LensPage({ snapshot, project, read, openDoc, busy, params, navigate, refresh }: PageProps) {
  const focus = params.get('task') ?? '';
  const setFocus = (value: string) => navigate('lens', { task: value, doc: params.get('doc') ?? '' });
  const [search, setSearch] = useState('');
  const query = useQuery({ queryKey: readKey(project, 'lens', focus), queryFn: ({ signal }) => read('context_graph', focus ? { focus } : {}, signal), retry: false, enabled: !busy });
  const tasks = snapshot.tasks;
  const shoal = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return tasks.filter((task) => !needle || `${titleOf(task)} ${idOf(task)}`.toLowerCase().includes(needle));
  }, [tasks, search]);
  const activeTasks = shoal.filter((task) => ACTIVE_STATUSES.has(text(task.status).trim().toLowerCase()));
  const openTasks = shoal.filter((task) => text(task.status) !== 'done' && !ACTIVE_STATUSES.has(text(task.status).trim().toLowerCase()));
  const doneTasks = shoal.filter((task) => text(task.status) === 'done');
  const data = object(query.data);
  const rings = object(data.rings);
  const gaps = new Map(rows(data.missing).map((missing) => [text(missing.ring), missing]));
  const focusDoc = object(data.focus);
  const runs = rows(data.runs);
  const workOrder = object(data.work_order);
  const trims = rows(workOrder.trims);
  const trimLabel = (trim: Data): string => {
    if (stringList(trim.actions).length) return `brief trimmed: ${stringList(trim.actions).join(', ')}${trim.over ? ' (still over budget)' : ''}`;
    if (text(trim.cut)) return `${text(trim.section)}: ${text(trim.cut)} cut past ${text(trim.cap)}`;
    return `${text(trim.section)}: ${text(trim.clipped)} clipped at ${text(trim.cap)} chars`;
  };
  const missing = rows(data.missing).length;
  const head = (
    <>
      <PageHeading
        eyebrow="Work"
        title="Context lens"
        description="One task's context in rings. An empty ring names the command that fills it."
        action={<Button onClick={() => void (refresh ? refresh() : query.refetch())} disabled={query.isFetching}><RotateCcw size={14} strokeWidth={1.8} aria-hidden="true" className="i" />Reload</Button>}
      />
      <div className="row" style={{padding:'0 0 12px',gap:6,flexWrap:'wrap'}}>
        <label className="faint picker-label" htmlFor="lens-focus">Task</label>
        <input id="lens-search" aria-label="Find a task" className="input picker-q rail-search" type="search" maxLength={MAX_SEARCH} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search tasks" />
        <select className="select picker-sel" aria-label="Task" id="lens-focus" value={focus || text(focusDoc.id)} onChange={(event) => setFocus(event.target.value)}>
          <option value="">The task in progress</option>
          {activeTasks.length ? (
            <optgroup label="Active">
              {activeTasks.map((task) => (
                <option key={idOf(task)} value={idOf(task)}>
                  {titleOf(task)}
                </option>
              ))}
            </optgroup>
          ) : null}
          {openTasks.length ? (
            <optgroup label="Open">
              {openTasks.map((task) => (
                <option key={idOf(task)} value={idOf(task)}>
                  {titleOf(task)}
                </option>
              ))}
            </optgroup>
          ) : null}
          {doneTasks.length ? (
            <optgroup label="Completed">
              {doneTasks.map((task) => (
                <option key={idOf(task)} value={idOf(task)}>
                  {titleOf(task)}
                </option>
              ))}
            </optgroup>
          ) : null}
        </select><span className="faint picker-n num">{shoal.length===tasks.length?plural(tasks.length,'task'):shoal.length+' of '+plural(tasks.length,'task')}</span>
      </div>
    </>
  );
  if (query.isPending) return <div>{head}<Loading /></div>;
  /* A refusal (nothing to focus on) is the page's own state, not a broken screen: it says what to do. */
  if (query.isError)
    return (
      <div>
        {head}
        <Empty title="Pick a task">{lensRefusal(query.error.message)}</Empty>
      </div>
    );
  if (!text(data.rings) && !Object.keys(rings).length) {
    return (
      <div>
        {head}
        <Empty title="Not a task">{`${text(focusDoc.id)} is a ${text(focusDoc.type, 'doc')}; the lens shows tasks. Pick one above.`}</Empty>
      </div>
    );
  }
  return (
    <div>
      {head}
      <p className="faint rail-muted" style={{margin:'0 0 12px'}}>
        <DocLink id={text(focusDoc.id)} openDoc={openDoc} /> {missing ? <Badge tone="warning">{`${missing} empty ring${missing === 1 ? '' : 's'}`}</Badge> : <Badge tone="good">every ring filled</Badge>}
      </p>
      <div className="lens-grid rail-lens-grid">
        {LENS_RINGS.map((ring) => (
          <LensRing key={ring.key} ring={ring} data={object(rings[ring.key])} gap={gaps.get(ring.key)} openDoc={openDoc} />
        ))}
      </div>
      <div className="stack rail-stack mt-4">
        <section className="panel" data-ring="runs"><header className="panel-h"><History size={15} strokeWidth={1.8} aria-hidden="true" className="i"/><h2>Recorded runs</h2><div className="acts"><span className="faint num">{runs.length}</span></div></header>
          {runs.length ? (
            runs.map((run, index) => (
              <LensRow
                key={`${text(run.kind)}-${index}`}
                icon={text(run.kind) === 'run.work_order' ? <FileText size={14} strokeWidth={1.8} aria-hidden="true" className="i" /> : <Terminal size={14} strokeWidth={1.8} aria-hidden="true" className="i" />}
                main={
                  <>
                    <span>{text(run.kind)}</span>
                    {text(run.command) ? <div className="mono faint" style={{fontSize:11.5,overflowWrap:'anywhere'}}>{text(run.command)}</div> : null}
                    <Sub value={`${text(run.at)} ${text(run.actor)}`} />
                    {text(run.tail) ? <pre className="lens-tail rail-lens-tail">{text(run.tail)}</pre> : null}
                  </>
                }
                extra={run.exit === null || run.exit === undefined ? null : <Badge tone={Number(run.exit) === 0 ? 'good' : 'danger'}>{`exit ${text(run.exit)}`}</Badge>}
              />
            ))
          ) : (
            <div className="faint" style={{padding:'4px 14px 12px'}}>{text(data.runs_note, 'No run recorded for this task yet.')}</div>
          )}
        </section>
        <section className="panel" data-ring="work-order"><header className="panel-h"><FileText size={15} strokeWidth={1.8} aria-hidden="true" className="i"/><h2>What the agent receives</h2><div className="acts"><span className="faint" style={{fontSize:12}}>{`ctx-rail work-order ${text(focusDoc.id)}`}</span></div></header>
          <div className="faint" style={{padding:'0 14px 8px'}}>{trims.length ? 'Caps that cut or clipped something:' : 'Nothing was cut or clipped.'}</div>
          {trims.length ? (
            <div className="row rail-chip-row" style={{gap:6,flexWrap:'wrap',padding:'0 14px 10px'}}>
              {trims.map((trim, index) => (
                <Badge key={index} tone="warning">
                  {trimLabel(trim)}
                </Badge>
              ))}
            </div>
          ) : null}
          <pre className="lens-pre rail-lens-pre" tabIndex={0}>
            {text(workOrder.markdown)}
          </pre>
        </section>
      </div>
    </div>
  );
}

/* =====================================================================
   GRAPH
   ===================================================================== */
function graphLayout(data: Data): { W: number; H: number; pos: Map<string, [number, number]>; nodes: Data[]; hidden: number } {
  const focusId = text(object(data.focus).id);
  const allNodes = rows(data.nodes);
  const nodes = allNodes.slice(0, MAX_NODES);
  const edges = rows(data.edges);
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    const from = text(edge.from);
    const to = text(edge.to);
    if (!from || !to) continue;
    if (!adjacency.has(from)) adjacency.set(from, []);
    if (!adjacency.has(to)) adjacency.set(to, []);
    adjacency.get(from)?.push(to);
    adjacency.get(to)?.push(from);
  }
  const distance = new Map<string, number>([[focusId, 0]]);
  const queue = [focusId];
  while (queue.length) {
    const current = queue.shift() as string;
    for (const neighbour of adjacency.get(current) ?? []) {
      if (!distance.has(neighbour)) {
        distance.set(neighbour, (distance.get(current) ?? 0) + 1);
        queue.push(neighbour);
      }
    }
  }
  const byHop: Data[][] = [[], [], []];
  for (const node of nodes) byHop[Math.min(distance.get(idOf(node)) ?? 2, 2)].push(node);
  const W = 640;
  const H = 440;
  const cx = W / 2;
  const cy = H / 2;
  const pos = new Map<string, [number, number]>();
  byHop[0].forEach((node) => pos.set(idOf(node), [cx, cy]));
  const place = (list: Data[], radius: number, phase: number) =>
    list.forEach((node, index) => {
      const angle = phase + (index / Math.max(list.length, 1)) * Math.PI * 2;
      pos.set(idOf(node), [cx + Math.cos(angle) * radius * 1.35, cy + Math.sin(angle) * radius]);
    });
  place(byHop[1], 120, -Math.PI / 2);
  place(byHop[2], 195, -Math.PI / 2 + 0.3);
  return { W, H, pos, nodes, hidden: allNodes.length - nodes.length };
}

const clipText = (value: unknown, limit: number): string => {
  const body = String(value ?? '');
  return body.length > limit ? `${body.slice(0, limit - 1)}…` : body;
};

function GraphSvg({ data, openDoc }: { data: Data; openDoc: (id: string) => void }) {
  const { W, H, pos, nodes, hidden } = graphLayout(data);
  const focusId = text(object(data.focus).id);
  const edges = rows(data.edges).filter((edge) => pos.has(text(edge.from)) && pos.has(text(edge.to)));
  return (
    <>
      <svg className="g-svg rail-graph-svg" viewBox={`-70 0 ${W + 140} ${H}`} width="100%" role="img" aria-label={`Typed-link graph around ${focusId}`}>
        {edges.map((edge, index) => {
          const from = pos.get(text(edge.from)) as [number, number];
          const to = pos.get(text(edge.to)) as [number, number];
          return (
            <line key={index} className="g-edge" x1={from[0].toFixed(1)} y1={from[1].toFixed(1)} x2={to[0].toFixed(1)} y2={to[1].toFixed(1)}>
              <title>{`${text(edge.from)} ${text(edge.field)} ${text(edge.to)}`}</title>
            </line>
          );
        })}
        {nodes.map((node) => {
          const id = idOf(node);
          const [x, y] = pos.get(id) as [number, number];
          const isFocus = id === focusId;
          const tip = `${id} (${text(node.type)}${text(node.freshness) ? `, ${text(node.freshness)}` : ''})`;
          return (
            <g
              key={id}
              className={cn('g-node', isFocus && 'focus')}
              data-a="openDoc"
              data-id={id}
              role="button"
              tabIndex={0}
              aria-label={tip}
              onClick={() => openDoc(id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  openDoc(id);
                }
              }}
            >
              <title>{tip}</title>
              <circle cx={x.toFixed(1)} cy={y.toFixed(1)} r={isFocus ? 13 : 9} fill={FRESH_FILL[text(node.freshness)] ?? FRESH_FILL.unknown} />
              <text className="g-label" x={x.toFixed(1)} y={(y + (isFocus ? 28 : 22)).toFixed(1)} textAnchor="middle">
                {clipText(text(node.title, id), 26)}
              </text>
            </g>
          );
        })}
      </svg>
      {hidden > 0 ? <p className="faint" style={{ fontSize: 12 }}>{hidden} more node(s) not drawn - narrow the types.</p> : null}
    </>
  );
}

const splitParam = (value: string | null): string[] => (value ? value.split(',').map((part) => part.trim()).filter(Boolean) : []);

export function GraphPage({ snapshot, project, params, read, navigate, openDoc, busy, refresh }: PageProps) {
  const [focus, setFocus] = useState(() => params.get('focus') ?? '');
  const [depth, setDepth] = useState<1 | 2>(() => (params.get('depth') === '1' ? 1 : 2));
  const [types, setTypes] = useState<string[]>(() => splitParam(params.get('types')));
  const [fields, setFields] = useState<string[]>(() => splitParam(params.get('fields')));
  const [search, setSearch] = useState('');
  /* Back/Forward and a pasted link restore the focus and filters from the address. */
  useEffect(() => {
    setFocus(params.get('focus') ?? '');
    setDepth(params.get('depth') === '1' ? 1 : 2);
    setTypes(splitParam(params.get('types')));
    setFields(splitParam(params.get('fields')));
  }, [params]);
  const signature = useMemo(() => [focus, String(depth), types.join(','), fields.join(',')].join('|'), [focus, depth, types, fields]);
  const focusArgs: Record<string, string> = { view: 'focus', depth: String(depth) };
  if (focus) focusArgs.focus = focus;
  if (types.length) focusArgs.types = types.join(',');
  if (fields.length) focusArgs.fields = fields.join(',');
  const focusQuery = useQuery({ queryKey: readKey(project, 'graph', 'focus', signature), queryFn: ({ signal }) => read('context_graph', focusArgs, signal), retry: false, enabled: !busy });
  const lineageQuery = useQuery({ queryKey: readKey(project, 'graph', 'lineage'), queryFn: ({ signal }) => read('context_graph', { view: 'lineage' }, signal), retry: false, enabled: !busy });
  const freshnessQuery = useQuery({ queryKey: readKey(project, 'graph', 'freshness'), queryFn: ({ signal }) => read('context_graph', { view: 'freshness' }, signal), retry: false, enabled: !busy });
  const focusData = object(focusQuery.data);
  const presentFields = stringList(focusData.fields);
  const sync = useCallback(
    (change: { focus?: string; depth?: 1 | 2; types?: string[]; fields?: string[] }) => {
      const nextFocus = change.focus ?? focus;
      const nextDepth = change.depth ?? depth;
      const nextTypes = change.types ?? types;
      const nextFields = change.fields ?? fields;
      const next: Record<string, string> = { depth: String(nextDepth) };
      const doc = params.get('doc');
      if (doc) next.doc = doc;
      if (nextFocus) next.focus = nextFocus;
      if (nextTypes.length) next.types = nextTypes.join(',');
      if (nextFields.length) next.fields = nextFields.join(',');
      navigate('graph', next);
    },
    [depth, fields, focus, navigate, params, types],
  );
  const toggle = (list: string[], value: string) => (list.includes(value) ? list.filter((entry) => entry !== value) : [...list, value]);
  const docGroups = useMemo(() => {
    const core = snapshot.core_docs.map((doc) => ({ ...doc, kind: text(doc.type, 'core doc') }));
    return [
      { kind: 'task', items: snapshot.tasks },
      { kind: 'scope item', items: snapshot.scope_items },
      { kind: 'decision', items: snapshot.decisions },
      { kind: 'risk', items: snapshot.risks },
      { kind: 'question', items: snapshot.questions },
      { kind: 'phase', items: snapshot.phases },
      { kind: 'milestone', items: rows(snapshot.milestones) },
      { kind: 'core doc', items: core },
    ];
  }, [snapshot]);
  /* The legacy picker (ui/doc-picker.js): every doc is an option, the current focus first, then
     live work, then the open docs, and the closed ones last; the search filters on label, id, kind and status. */
  const needle = search.trim().toLowerCase();
  const pickerRows = useMemo(() => {
    const flat: { id: string; label: string; kind: string; status: string; open: boolean; active: boolean }[] = [];
    for (const group of docGroups) {
      for (const doc of group.items) {
        const id = idOf(doc);
        if (!id) continue;
        const status = text((doc as Data).status);
        const lower = status.trim().toLowerCase();
        flat.push({ id, label: titleOf(doc), kind: text(doc.kind, group.kind), status, open: !CLOSED_STATUSES.has(lower), active: ACTIVE_STATUSES.has(lower) });
      }
    }
    return flat;
  }, [docGroups]);
  const pickerFiltered = needle ? pickerRows.filter((row) => (row.label + ' ' + row.id + ' ' + row.kind + ' ' + row.status).toLowerCase().includes(needle)) : pickerRows;
  /* The legacy picker's current doc is the one the read returned (pageGraph: G.focus.focus.id || G.id),
     so the server's default focus is shown as selected even when the address names none. */
  const pickerCurrent = text(object(focusData.focus).id) || focus;
  const pickerSelected = pickerFiltered.some((row) => row.id === pickerCurrent) ? pickerCurrent : '';
  /* Live work (in progress, blocked) is what a reader looks for first: it ranks
     above the open docs and far above the closed ones. */
  const pickerBucket = (row: { id: string; open: boolean; active: boolean }) => (row.id === pickerSelected ? 0 : row.active ? 1 : row.open ? 2 : 3);
  const pickerOrdered = [...pickerFiltered].sort((a, b) => pickerBucket(a) - pickerBucket(b) || a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
  const pickerCount =
    pickerOrdered.length === pickerRows.length
      ? String(pickerRows.length) + ' document' + (pickerRows.length === 1 ? '' : 's')
      : String(pickerOrdered.length) + ' of ' + String(pickerRows.length) + ' documents';
  const pickerGrouped: [string, (row: { id: string; open: boolean; active: boolean }) => boolean][] = [
    ['Selected', (row) => row.id === pickerSelected],
    ['Active', (row) => row.id !== pickerSelected && row.active],
    ['Open', (row) => row.id !== pickerSelected && row.open && !row.active],
    ['Completed', (row) => row.id !== pickerSelected && !row.open],
  ];
  return (
    <div>
      {/* The legacy Graph head (pages/graph.js pageGraph): ph + acts, then the searchable native
          picker row and the chip control stack - no toolbar and no rail chips. */}
      <div className="ph">
        <div><h1>Graph</h1><p>The links around one doc, how decisions replaced each other, and how old everything is.</p></div>
        <div className="acts">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              /* One refresh for the whole project, so the graph and the task
                 list it sits next to describe the same moment. */
              if (refresh) {
                void refresh();
                return;
              }
              void focusQuery.refetch();
              void lineageQuery.refetch();
              void freshnessQuery.refetch();
            }}
          >
            <RotateCcw size={14} aria-hidden="true" />Reload
          </button>
        </div>
      </div>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap', padding: '0 0 12px' }} data-keep="graph-picker">
        {pickerRows.length ? (
          <>
            <label className="faint picker-label" htmlFor="graph-focus-q">Focus document</label>
            <input
              className="input picker-q"
              id="graph-focus-q"
              type="search"
              data-in="pickerSearch"
              data-picker="graph"
              value={search}
              placeholder="Search documents"
              aria-label="Focus document search"
              autoComplete="off"
              onChange={(event) => setSearch(event.target.value)}
            />
            <select className="select picker-sel" id="graph-focus-sel" data-in="graphFocus" aria-label="Focus document" value={pickerSelected} onChange={(event) => sync({ focus: event.target.value })}>
              <option value="">Pick a document...</option>
              {pickerGrouped.map(([name, keep]) => {
                const items = pickerOrdered.filter(keep);
                return items.length ? (
                  <optgroup key={name} label={name}>
                    {items.map((row) => (
                      <option key={row.id} value={row.id}>{row.label + ' · ' + row.kind}</option>
                    ))}
                  </optgroup>
                ) : null;
              })}
            </select>
            <span className="faint picker-n num">{pickerCount}</span>
            {pickerOrdered.length ? null : <span className="faint picker-empty">No match for the search.</span>}
          </>
        ) : (
          <span className="faint">Nothing to focus on yet.</span>
        )}
      </div>
      <div className="stack" style={{ gap: 6, marginBottom: 12 }}>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="faint" style={{ fontSize: 12, minWidth: 48 }}>Depth</span>
          {[1, 2].map((hop) => (
            <button key={hop} type="button" className={cn('chip', depth === hop && 'on')} onClick={() => sync({ depth: hop as 1 | 2 })}>
              {hop} hop{hop === 1 ? '' : 's'}
            </button>
          ))}
        </div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="faint" style={{ fontSize: 12, minWidth: 48 }}>Types</span>
          {GRAPH_TYPES.map((type) => (
            <button key={type} type="button" className={cn('chip', types.includes(type) && 'on')} onClick={() => sync({ types: toggle(types, type) })}>
              {type.replace('_', ' ')}
            </button>
          ))}
          {types.length ? (
            <button type="button" className="chip" onClick={() => sync({ types: [] })}>clear</button>
          ) : null}
        </div>
        {presentFields.length ? (
          <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span className="faint" style={{ fontSize: 12, minWidth: 48 }}>Links</span>
            {presentFields.map((field) => (
              <button key={field} type="button" className={cn('chip', fields.includes(field) && 'on')} onClick={() => sync({ fields: toggle(fields, field) })}>
                {field}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <div className="lens-grid">
        {text(object(focusData.focus).id) && !focusQuery.isError && !['refused', 'error'].includes(text(focusData.status)) ? (
          <section className="panel" data-ring="graph">
            <div className="panel-h">
              <GitBranch size={15} aria-hidden="true" />
              <h2>{'Around ' + text(object(focusData.focus).id)}</h2>
              <div className="acts">
                <span className="faint num">{String(rows(focusData.nodes).length) + ' nodes, ' + String(rows(focusData.edges).length) + ' links'}</span>
              </div>
            </div>
            <div className="rail-graph" style={{ padding: '4px 14px 8px' }}><GraphSvg data={focusData} openDoc={openDoc} /></div>
            <div className="row" style={{ gap: 6, padding: '0 14px 10px', flexWrap: 'wrap' }}>
              <span className="faint" style={{ fontSize: 12 }}>Node colour = freshness:</span>
              {['fresh', 'aging', 'stale', 'unknown'].map((key) => chip(FRESH_CLASS[key] ?? 'gray', key, key))}
            </div>
            {rows(focusData.edges).length ? (
              rows(focusData.edges)
                .slice(0, 40)
                .map((edge, index) => (
                  <GraphRow
                    key={index}
                    icon={<ArrowRight size={14} aria-hidden="true" />}
                    main={
                      <>
                        <DocLink id={text(edge.from)} openDoc={openDoc} /> <span className="faint">{text(edge.field)}</span>{' '}
                        <DocLink id={text(edge.to)} openDoc={openDoc} />
                      </>
                    }
                  />
                ))
            ) : (
              <div style={{ padding: '4px 14px 12px' }} className="faint">No links reach this doc at this depth and filter.</div>
            )}
          </section>
        ) : (
          <section className="panel" data-ring="graph">
            <div className="panel-h"><GitBranch size={15} aria-hidden="true" /><h2>Around one doc</h2></div>
            <LegacyEmptyBlock
              icon={<CircleHelp size={20} aria-hidden="true" />}
              title="Pick a doc"
              text={focusQuery.isError ? focusQuery.error.message : text(focusData.error) || 'Choose a task or scope item above.'}
              level={3}
            />
          </section>
        )}
        <div className="stack">
          <section className="panel" data-ring="lineage">
            <div className="panel-h">
              <History size={15} aria-hidden="true" />
              <h2>Decision lineage</h2>
              <div className="acts">
                <span className="faint num">{String(rows(object(lineageQuery.data).chains).length) + ' chain(s), ' + text(object(lineageQuery.data).standalone, '0') + ' standalone'}</span>
              </div>
            </div>
            {lineageQuery.isPending || lineageQuery.isError || text(object(lineageQuery.data).status) !== 'ok' ? (
              <div style={{ padding: '4px 14px 12px' }} className="faint">
                {lineageQuery.isError ? lineageQuery.error.message : 'Loading...'}
              </div>
            ) : rows(object(lineageQuery.data).chains).length ? (
              rows(object(lineageQuery.data).chains).map((chain) => (
                <div key={text(chain.head)} data-chain={text(chain.head)} style={{ padding: '6px 14px', borderTop: '1px solid var(--divider)' }}>
                  {rows(chain.members).map((member, index) => (
                    <GraphRow
                      key={idOf(member)}
                      icon={index === 0 ? <CircleCheck size={14} aria-hidden="true" /> : <CircleX size={14} aria-hidden="true" />}
                      main={
                        <>
                          <DocLink id={idOf(member)} label={titleOf(member)} openDoc={openDoc} />
                          {text(member.choice) ? <div className="faint" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{clipText(member.choice, 160)}</div> : null}
                          {text(member.reason) ? <div className="faint" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{'replaced the previous one: ' + text(member.reason)}</div> : null}
                        </>
                      }
                      extra={
                        <>
                          {chip(text(member.status) === 'active' ? 'green' : 'gray', text(member.status))}
                          {chip(FRESH_CLASS[text(member.freshness)] ?? 'gray', text(member.freshness, 'unknown'))}
                        </>
                      }
                    />
                  ))}
                </div>
              ))
            ) : (
              <div style={{ padding: '4px 14px 12px' }} className="faint">No decision has been superseded yet.</div>
            )}
          </section>
          <section className="panel" data-ring="freshness">
            <div className="panel-h">
              <ClockGlyph size={15} />
              <h2>Freshness</h2>
              <div className="acts">
                {['fresh', 'recent', 'aging', 'stale', 'ancient', 'unknown']
                  .filter((key) => !!object(object(freshnessQuery.data).by_label)[key])
                  .map((key) => chip(FRESH_CLASS[key] ?? 'gray', key + ' ' + legacyNum(object(object(freshnessQuery.data).by_label)[key]), key))}
              </div>
            </div>
            {freshnessQuery.isPending || freshnessQuery.isError || text(object(freshnessQuery.data).status) !== 'ok' ? (
              <div style={{ padding: '4px 14px 12px' }} className="faint">
                {freshnessQuery.isError ? freshnessQuery.error.message : 'Loading...'}
              </div>
            ) : (
              <>
                <div style={{ borderTop: '1px solid var(--divider)' }}>
                  <div className="faint" style={{ padding: '6px 14px 0', fontSize: 12 }}>Oldest docs</div>
                {rows(object(freshnessQuery.data).docs)
                  .filter((doc) => !doc.quiet)
                  .slice(0, 10)
                  .map((doc) => (
                    <GraphRow
                      key={idOf(doc)}
                      icon={<FileGlyph size={14} aria-hidden="true" />}
                      main={
                        <>
                          <DocLink id={idOf(doc)} label={titleOf(doc)} openDoc={openDoc} />
                          <div className="faint" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{text(doc.type) + (text(doc.updated) ? ' - updated ' + text(doc.updated) : '')}</div>
                        </>
                      }
                      extra={chip(FRESH_CLASS[text(doc.freshness)] ?? 'gray', text(doc.freshness))}
                    />
                  ))}
                {rows(object(freshnessQuery.data).docs).filter((doc) => !doc.quiet).length ? null : (
                  <div style={{ padding: '4px 14px 10px' }} className="faint">No doc to show.</div>
                )}
                </div>
                <div style={{ borderTop: '1px solid var(--divider)' }}>
                  <div className="faint" style={{ padding: '6px 14px 0', fontSize: 12 }}>Stale proofs (a file they cover changed)</div>
                  {rows(object(freshnessQuery.data).stale_proofs).length ? (
                    rows(object(freshnessQuery.data).stale_proofs).map((proof, index) => (
                      <GraphRow
                        key={index}
                        icon={<ShieldAlert size={14} aria-hidden="true" />}
                        main={
                          <>
                            <span>{text(proof.item) + ' ' + text(proof.text)}</span>
                            {text(proof.command) ? <div className="faint" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{text(proof.command)}</div> : null}
                            <div className="faint" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{'changed: ' + stringList(proof.changed).join(', ')}</div>
                          </>
                        }
                        extra={chip('amber', text(proof.via, 'stale'))}
                      />
                    ))
                  ) : (
                    <div style={{ padding: '4px 14px 10px' }} className="faint">Every recorded proof still matches its files.</div>
                  )}
                </div>
                <div style={{ borderTop: '1px solid var(--divider)' }}>
                  <div className="faint" style={{ padding: '6px 14px 0', fontSize: 12 }}>Task descriptions older than their code</div>
                  {rows(object(freshnessQuery.data).stale_descriptions).length ? (
                    rows(object(freshnessQuery.data).stale_descriptions).map((entry, index) => (
                      <GraphRow
                        key={index}
                        icon={<FileText size={14} aria-hidden="true" />}
                        main={
                          <>
                            <DocLink id={text(entry.task)} openDoc={openDoc} />
                            <div className="faint" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{legacyNum(entry.commits) + ' commit(s) since it was written: ' + stringList(entry.files).join(', ')}</div>
                          </>
                        }
                        extra={chip('amber', 'may be out of date')}
                      />
                    ))
                  ) : (
                    <div style={{ padding: '4px 14px 10px' }} className="faint">None.</div>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

/* =====================================================================
   DOCTOR - exact, fail-closed source links
   ===================================================================== */
export interface DoctorIndex {
  byFile: Map<string, Data[]>;
  pathsById: Map<string, Set<string>>;
  ambiguous: Set<string>;
  unknown: boolean;
}
const DOC_KEYS = ['core_docs', 'tasks', 'phases', 'milestones', 'scope_items', 'decisions', 'risks', 'questions'];
/* Trailing slashes go in one linear pass: /\/+$/ backtracks quadratically on a long slash run. */
function stripTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value.charCodeAt(end - 1) === 47) end -= 1;
  return end === value.length ? value : value.slice(0, end);
}
const normalizePath = (value: unknown): string => (typeof value === 'string' ? stripTrailingSlashes(value.replace(/\\/g, '/').replace(/^\.\//, '')) : '');

/* The structured metadata the server ships in snapshot.project: `store_root` is the store exactly as scanned,
   the prefix run_doctor builds its absolute `file` strings from, and `ambiguous_doc_ids` names the stems
   /api/doc/<stem> cannot resolve to one document. `present` distinguishes "the server did not send this"
   (an old snapshot) from "the server sent something we cannot read" (fail closed, never guess). */
export function buildDoctorIndex(snapshot: Snapshot): DoctorIndex {  const meta = object(snapshot.project);
  const hasRoot = Object.hasOwn(meta, 'store_root');
  const root = hasRoot ? normalizePath(meta.store_root) : '';
  const hasAmbiguity = Object.hasOwn(meta, 'ambiguous_doc_ids');
  const rawAmbiguity = meta.ambiguous_doc_ids;
  const ids = hasAmbiguity && Array.isArray(rawAmbiguity) && rawAmbiguity.every((entry) => typeof entry === 'string') ? new Set(rawAmbiguity as string[]) : null;
  const byFile = new Map<string, Data[]>();
  const pathsById = new Map<string, Set<string>>();
  for (const key of DOC_KEYS) {
    for (const doc of rows(snapshot[key])) {
      const id = idOf(doc);
      const path = normalizePath(doc.path);
      if (!id || !path) continue;
      const spellings = [path, `context-rail/${path}`];
      /* store_root + '/' + path is exactly the string the doctor reports as this doc's `file`. */
      if (hasRoot && root) spellings.push(`${root}/${path}`);
      for (const spelling of spellings) {
        const group = byFile.get(spelling);
        if (group) group.push(doc);
        else byFile.set(spelling, [doc]);
      }
      const known = pathsById.get(id);
      if (known) known.add(path);
      else pathsById.set(id, new Set([path]));
    }
  }
  return { byFile, pathsById, ambiguous: ids ?? new Set<string>(), unknown: hasAmbiguity && ids === null };
}

/* A source link only when the finding's file names exactly one document: no basename guesses,
   no arbitrary external or traversal paths, no ambiguous stems. */
export function doctorSource(issue: Data, index: DoctorIndex): Data | null {
  const file = normalizePath(issue.file);
  if (!file || file.split('/').includes('..')) return null;
  if (index.unknown) return null;
  const matches = index.byFile.get(file) ?? [];
  const unique = [...new Map(matches.map((doc) => [`${idOf(doc)}|${text(doc.path)}`, doc])).values()];
  if (unique.length !== 1) return null;
  const doc = unique[0];
  if (index.ambiguous.has(idOf(doc))) return null;
  return index.pathsById.get(idOf(doc))?.size === 1 ? doc : null;
}

export function filterFindings(issues: Data[], query: string, severity: string): Data[] {
  const needle = query.trim().toLowerCase();
  return issues.filter(
    (issue) =>
      (!severity || text(issue.severity) === severity) &&
      (!needle || ['severity', 'check', 'message', 'file', 'field', 'fix_hint'].some((key) => text(issue[key]).toLowerCase().includes(needle))),
  );
}

function DoctorFinding({ issue, index, openDoc }: { issue: Data; index: DoctorIndex; openDoc: (id: string) => void }) {
  const source = doctorSource(issue, index);
  const location = [text(issue.file), text(issue.field)].filter(Boolean).join(' · ');
  return (
    <div className="mini" style={{alignItems:'flex-start',cursor:'default'}} data-doctor-check={text(issue.check)}>
      <div style={{paddingTop:2}}><Badge tone={SEVERITY_TONE[text(issue.severity)] ?? 'neutral'}>{text(issue.severity, 'unknown')}</Badge></div>
      <div className="grow" style={{minWidth:0,overflowWrap:'anywhere'}}>
        <div style={{fontWeight:500}}>{text(issue.check, 'Finding')}</div>
        <div className="muted" style={{fontSize:13}}>{text(issue.message)}</div>
        {location ? <div className="faint" style={{fontSize:12}}>{source ? <DocLink id={idOf(source)} label={location} openDoc={openDoc} /> : location}</div> : null}
        {text(issue.fix_hint) ? (
          <div className="faint" style={{fontSize:12,marginTop:6}}>
            <b>Suggested manual action:</b> <code>{text(issue.fix_hint)}</code>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function DoctorPage({ snapshot, project, read, openDoc, busy, refresh }: PageProps) {
  const query = useQuery({ queryKey: readKey(project, 'doctor'), queryFn: ({ signal }) => read('doctor', {}, signal), retry: false, enabled: !busy });
  const [search, setSearch] = useState('');
  const [severity, setSeverity] = useState('');
  const index = useMemo(() => buildDoctorIndex(snapshot), [snapshot]);
  const head = (
    <PageHeading
      eyebrow="Inspect"
      title="Doctor"
      description="Project health findings and source-backed manual guidance."
      action={<Button onClick={() => void (refresh ? refresh() : query.refetch())} disabled={query.isFetching}><RotateCcw size={14} strokeWidth={1.8} aria-hidden="true" className="i" />Run again</Button>}
    />
  );
  const unavailable = (
    <div role="alert">
      <Empty title="Doctor result unavailable">The server returned malformed findings. Run again; no clean result is asserted.</Empty>
    </div>
  );
  let body: ReactNode;
  if (query.isPending) body = <Loading />;
  else if (query.isError) body = <ErrorState error={query.error.message} retry={() => void query.refetch()} />;
  else {
    const data = object(query.data);
    const issues = rows(data.issues);
    if (!Array.isArray(data.issues) || issues.length !== data.issues.length) body = unavailable;
    else {
      const shown = filterFindings(issues, search, severity);
      body = (
        <>
          <div className="row" style={{flexWrap:'wrap',gap:8,marginBottom:12}}>
            <div className="row" style={{gap:8,minWidth:0}}>
              <label htmlFor="doctor-query">Search findings</label>
              <input
                id="doctor-query"
                className="input rail-search"
                type="search"
                maxLength={MAX_SEARCH}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Check, message, source or guidance"
              />
            </div>
            <div className="row" style={{gap:8,flex:'none'}}>
              <label htmlFor="doctor-severity">Severity</label>
              <select id="doctor-severity" className="select" value={severity} onChange={(event) => setSeverity(event.target.value)}>
                <option value="">All severities</option>
                {SEVERITIES.map((entry) => (
                  <option key={entry} value={entry}>
                    {entry}
                  </option>
                ))}
              </select>
            </div>
            <Button
              variant="secondary"
              onClick={() => {
                setSearch('');
                setSeverity('');
              }}
            >
              Clear filters
            </Button>
            <span className="faint rail-muted" role="status">
              {shown.length} of {issues.length} findings
            </span>
          </div>
          <p className="faint">Guidance is shown for review; no suggested command runs automatically.</p>
          {issues.length ? (
            shown.length ? (
              <section className="panel">
                {shown.map((issue, position) => (
                  <DoctorFinding key={`${text(issue.check)}-${position}`} issue={issue} index={index} openDoc={openDoc} />
                ))}
              </section>
            ) : (
              <Empty title="No matching findings">Clear filters to see every finding.</Empty>
            )
          ) : (
            <Empty title="All clear">The doctor found nothing.</Empty>
          )}
        </>
      );
    }
  }
  return (
    <div>
      {head}
      {body}
    </div>
  );
}

/* =====================================================================
   PORTFOLIO - GET /api/portfolio through the shell's request hook
   ===================================================================== */
/* The legacy count helper (pages/portfolio.js num()): a finite number rounds, anything else is 0. */
const legacyNum = (value: unknown): string => (typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value)) : '0');

export function PortfolioPage({ project, request, navigate, busy }: PageProps) {
  const query = useQuery({ queryKey: ['rail-global', 'portfolio'], queryFn: ({ signal }) => request('/api/portfolio', { signal }), retry: false, enabled: !busy });
  /* The legacy "open here" badge compares against S.ui.project || projects.current, so the launch
     store is marked even when the address carries no project parameter. */
  const registry = useQuery({ queryKey: ['rail-global', 'projects'], queryFn: ({ signal }) => request('/api/projects', { signal }), retry: false, enabled: !busy });
  const current = project || text(object(registry.data).current);
  /* The legacy Projects page (pages/portfolio.js): the head carries the run control, which is
     also the retry, and the table keeps the legacy .tbl.portfolio-tbl markup and cell classes. */
  const head = (
    <div className="ph">
      <div>
        <h1>Projects</h1>
        <p>The doctor in every registered project. Add one with <code>ctx-rail projects add</code>.</p>
      </div>
      <div className="acts">
        <button type="button" className="btn btn-secondary" onClick={() => void query.refetch()} disabled={query.isFetching}>
          <RotateCcw size={14} aria-hidden="true" />Run the doctors
        </button>
      </div>
    </div>
  );
  let body: ReactNode;
  if (query.isPending) body = <LegacyEmptyBlock icon={<Loader size={20} aria-hidden="true" />} title="Loading" text="Running the doctor in each project..." />;
  else if (query.isError) body = <LegacyEmptyBlock icon={<TriangleAlert size={20} aria-hidden="true" />} title="The portfolio could not load" text={query.error.message} />;
  else {
    const data = object(query.data);
    const stores = rows(data.stores);
    const totals = object(data.totals);
    if (text(data.status) === 'error' || !Array.isArray(data.stores)) {
      body = <LegacyEmptyBlock icon={<TriangleAlert size={20} aria-hidden="true" />} title="The portfolio could not load" text={text(data.error, 'No data')} />;
    } else if (!stores.length) {
      body = <LegacyEmptyBlock icon={<Folders size={20} aria-hidden="true" />} title="No projects yet" text="Register a project with ctx-rail projects add <path>." />;
    } else {
      body = (
        <>
          <p className="faint" style={{ margin: '0 0 12px' }}>
            {plural(Number(legacyNum(data.store_count)), 'project')}: {plural(Number(legacyNum(totals.errors)), 'error')}, {plural(Number(legacyNum(totals.warnings)), 'warning')}, {plural(Number(legacyNum(totals.infos)), 'info')}
          </p>
          <section className="panel" data-ring="portfolio">
            <div className="tbl-wrap" data-keep="portfolio">
              <table className="tbl portfolio-tbl">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th className="num">Errors</th>
                    <th className="num">Warnings</th>
                    <th className="num">Info</th>
                    <th className="num">Debt</th>
                    <th>Top checks</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {stores.map((store) => {
                    const id = idOf(store);
                    const checks = Object.entries(object(store.top_checks))
                      .map(([key, value]) => key + ': ' + legacyNum(value))
                      .join(', ');
                    const openable = !!id && !store.stale;
                    return (
                      <tr key={id} data-id={id}>
                        <td>
                          <div style={{ fontWeight: 500 }}>{id}</div>
                          <div className="faint mono trunc" style={{ fontSize: 11.5, maxWidth: 320 }} title={text(store.vault)}>{text(store.vault)}</div>
                          {text(store.stale_reason) ? <div className="faint" style={{ fontSize: 12 }}>{text(store.stale_reason)}</div> : null}
                        </td>
                        <td className={cn('num', !!store.errors && 'bad')}>{legacyNum(store.errors)}</td>
                        <td className="num">{legacyNum(store.warnings)}</td>
                        <td className="num faint">{legacyNum(store.infos)}</td>
                        <td className="num">{legacyNum(store.debt)}</td>
                        <td className="faint rail-pf-checks" style={{ fontSize: 12 }}>{checks}</td>
                        <td>
                          <div className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                            {id === current ? chip('accent', 'open here') : null}
                            {store.stale ? chip('amber', 'stale') : null}
                            {store.registered === false ? chip('gray', 'not registered') : null}
                            {openable ? (
                              <button type="button" className="btn btn-sm btn-secondary" onClick={() => navigate('now', { project: id })}>Open</button>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      );
    }
  }
  return (
    <div>
      {head}
      {body}
    </div>
  );
}

/* =====================================================================
   SETTINGS - appearance and keyboard shortcuts (UI preferences only)
   The preference model (closed enums, read/save/apply/restore) lives in
   ../preferences; the shell calls restorePreferences() at startup so the saved
   accent/side/density/motion hold on every route, not only while this page is
   mounted. Theme stays shell-owned (PageProps.theme/setTheme).
   ===================================================================== */
/* Every value about to land in a style attribute is a fixed swatch from ACCENTS, never store text. */
const swatch = (value: string): CSSProperties => ({ '--c': /^#[0-9a-f]{3,8}$/i.test(value) ? value : '#4B5BD6' }) as CSSProperties;
const SHORTCUTS: [string, [string, string[]][]][] = [
  [
    'General',
    [
      ['Command menu', ['Ctrl', 'K']],
      ['Keyboard shortcuts', ['?']],
      ['Toggle sidebar', ['[']],
      ['Toggle dark mode', ['Ctrl', 'Shift', 'L']],
      ['Close panel or dialog', ['Esc']],
    ],
  ],
  [
    'Navigate (press G, then a key)',
    [
      ['Summary', ['G', 'U']],
      ['Now', ['G', 'N']],
      ['Board', ['G', 'B']],
      ['Roadmap', ['G', 'R']],
      ['Scope', ['G', 'S']],
      ['Decisions', ['G', 'D']],
      ['Doctor', ['G', 'H']],
      ['Activity', ['G', 'A']],
      ['Projects', ['G', 'P']],
      ['Graph', ['G', 'G']],
    ],
  ],
  [
    'Command menu',
    [
      ['Move selection', ['↑', '↓']],
      ['Run command', ['↵']],
    ],
  ],
];
const THEMES: ['light' | 'dark' | 'system', string, ReactNode][] = [
  ['light', 'Light', <Sun className="i" size={13} strokeWidth={1.8} aria-hidden="true" key="light" />],
  ['dark', 'Dark', <Moon className="i" size={13} strokeWidth={1.8} aria-hidden="true" key="dark" />],
  ['system', 'System', <Monitor className="i" size={13} strokeWidth={1.8} aria-hidden="true" key="system" />],
];

export function SettingsPage({ params, navigate, theme, setTheme, request, mutate, busy, project, refresh }: PageProps) {
  const section = params.get('sec') === 'shortcuts' ? 'shortcuts' : 'appearance';
  /* The automation/config sections are local state, not a URL section: the shell's router
     only validates sec=appearance|shortcuts, and this section must not change
     that contract (it is reachable from the nav and by keyboard). */
  const [automation, setAutomation] = useState(false);
  const [config, setConfig] = useState(false);
  const [prefs, setPrefs] = useState<Prefs>(readPrefs);
  useEffect(() => {
    applyPrefs(prefs);
  }, [prefs]);
  const update = (patch: Partial<Prefs>) =>
    setPrefs((current) => {
      const next = { ...current, ...patch };
      savePrefs(next);
      return next;
    });
  const segmented = (key: keyof Prefs, options: string[]) => (
    <div className="seg rail-segmented" role="radiogroup" aria-label={key}>
      {options.map((option) => (
        <button key={option} type="button" role="radio" aria-checked={prefs[key] === option} className={cn('rail-segment', prefs[key] === option && 'on rail-segment-on')} onClick={() => update({ [key]: option } as Partial<Prefs>)}>
          {titleCase(option)}
        </button>
      ))}
    </div>
  );
  return (
    <div className="set rail-settings">
      <nav className="set-nav rail-settings-nav" aria-label="Settings">
        <button type="button" className={cn("sitem rail-nav-link", !automation && !config && section === 'appearance' && "on")} aria-current={!automation && !config && section === 'appearance' ? 'page' : undefined} onClick={() => { setAutomation(false); setConfig(false); navigate('settings', { sec: 'appearance' }); }}>
          <Palette className="i" size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Appearance</span>
        </button>
        <button type="button" className={cn("sitem rail-nav-link", !automation && !config && section === 'shortcuts' && "on")} aria-current={!automation && !config && section === 'shortcuts' ? 'page' : undefined} onClick={() => { setAutomation(false); setConfig(false); navigate('settings', { sec: 'shortcuts' }); }}>
          <KeyboardGlyph className="i" size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Keyboard shortcuts</span>
        </button>
        <button type="button" className={cn("sitem rail-nav-link", config && "on")} aria-current={config ? 'page' : undefined} onClick={() => { setAutomation(false); setConfig(true); }}>
          <Palette className="i" size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Configuration</span>
        </button>
      </nav>
      <div className="set-body rail-settings-body"><div className="set-in">
        {config ? (
          <ConfigSettings snapshot={snapshot} project={project} params={params} navigate={navigate} openDoc={openDoc} read={read} mutate={mutate} busy={busy} refresh={refresh} request={request} theme={theme} setTheme={setTheme} />
        ) : automation ? (
          <AutomationSettings request={request} mutate={mutate} busy={busy} project={project} refresh={refresh} />
        ) : section === 'shortcuts' ? (
          <>
            <PageHeading title="Keyboard shortcuts" description="Move without the mouse." />
            {SHORTCUTS.map(([group, list]) => (
              <Panel key={group} title={group}>
                {list.map(([name, keys]) => (
                  <div className="srow rail-settings-item" key={name}>
                    <span>{name}</span>
                    <span className="row rail-row">
                      {/* A shortcut can repeat a key (Graph is G then G), so the key is the
                          tuple position, not the label - duplicate labels are not a bug. */}
                      {keys.map((key, index) => (
                        <kbd className="kbd rail-kbd" key={`${key}-${index}`}>
                          {key}
                        </kbd>
                      ))}
                    </span>
                  </div>
                ))}
              </Panel>
            ))}
          </>
        ) : (
          <>
            <h1>Appearance</h1><p className="lead">How Context Rail looks on this device. These are the only things the browser remembers.</p>
            <section className="sblock" style={{marginTop:0}}><h2>Theme</h2>
              <div className="seg rail-segmented" role="radiogroup" aria-label="Theme" style={{marginTop:12}}>
                {THEMES.map(([key, label, glyph]) => (
                  <button key={key} type="button" role="radio" aria-checked={theme === key} className={cn('rail-segment', theme === key && 'on rail-segment-on')} onClick={() => setTheme(key)}>
                    {glyph}
                    {label}
                  </button>
                ))}
              </div>
            </section>
            <section className="sblock"><h2>Accent</h2>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Accent colour</div>
                  <div className="d rail-list-sub">Used for primary buttons, focus rings and selection.</div>
                </div>
                <div className="row"><div className="swatches rail-swatches" role="radiogroup" aria-label="Accent colour">
                  {ACCENTS.map(([key, colour]) => (
                    <button
                      key={key}
                      type="button"
                      role="radio"
                      aria-checked={prefs.accent === key}
                      aria-label={key}
                      className={cn('sw rail-swatch', prefs.accent === key && 'on')}
                      style={swatch(colour)}
                      onClick={() => update({ accent: key })}
                    >
                      {prefs.accent === key ? <Check className="i" size={13} strokeWidth={1.8} aria-hidden="true" /> : null}
                    </button>
                  ))}
                </div></div>
              </div>
            </section>
            <section className="sblock"><h2>Density</h2>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Sidebar</div>
                  <div className="d rail-list-sub">Compact fits more on screen.</div>
                </div>
                <div className="row">{segmented('side', ['comfortable', 'compact'])}</div>
              </div>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Cards and rows</div>
                  <div className="d rail-list-sub">Row height in lists and padding on the board.</div>
                </div>
                <div className="row">{segmented('density', ['comfortable', 'compact'])}</div>
              </div>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Motion</div>
                  <div className="d rail-list-sub">Reduce animation for the drawer and menus.</div>
                </div>
                <div className="row">{segmented('motion', ['system', 'reduce'])}</div>
              </div>
            </section>
          </>
        )}
      </div></div>
    </div>
  );
}
/* =====================================================================
   TASK WORKSPACE, FINISH DIALOG AND THE HONEST RESULT (SC-55 UI-03)

   One task workspace reads its facts from the contracts the store already
   serves: the pure prepare read (task body, why, scope acceptance, governing
   conventions and decisions, plans, checks) and the UI-02 ui_task_episode
   read (baseline, episodes, captures, durable closeout). It keeps no truth
   of its own: the pre-start consideration preview is read-only and says the
   ready-plan gate is server-owned, finishing sends only what the dialog
   collected, the full result renders task_done and closeout_complete
   independently, a refresh failure after success reads "applied, view
   stale", and an absent closeout stays unknown on a reload.
   ===================================================================== */

/* ---------- the shared phase resolver (Board filter/counts and Roadmap) ---------- */
export function phaseRef(task: Data): string {
  const direct = linkRefs(task.phase)[0];
  if (direct) return direct;
  const nested = linkRefs(object(task.scope).phase)[0];
  if (nested) return nested;
  return linkRefs(task['scope.phase'])[0] ?? '';
}

/* ---------- the finish dialog payload (pure, unit-tested) ---------- */
export interface FinishInput {
  task: string;
  summary?: string;
  verifyScope?: string;
  timeout?: string | number;
  skipVerify?: boolean;
}
export function donePayload(input: FinishInput): Record<string, unknown> {
  const payload: Record<string, unknown> = { task: input.task };
  const summary = (input.summary ?? '').trim();
  if (summary) payload.summary = summary;
  if (input.skipVerify) {
    payload.skip_verify = true;
    return payload;
  }
  const scope = (input.verifyScope ?? 'task').trim();
  if (scope && scope !== 'task') payload.verify_scope = scope;
  const raw = String(input.timeout ?? '').trim();
  if (raw) {
    const seconds = Number(raw);
    if (Number.isFinite(seconds) && seconds > 0) payload.verify_timeout = Math.floor(seconds);
  }
  return payload;
}

/* ---------- the operation result (pure, unit-tested) ---------- */
export type VerifyState = 'passed' | 'failed' | 'skipped' | 'unknown';
export function verifyState(result: Data): VerifyState {
  const verify = object(result.verify);
  if (verify.skipped === true) return 'skipped';
  if (verify.passed === true) return 'passed';
  if (verify.passed === false) return 'failed';
  return 'unknown';
}

export function splitFollowUps(result: Data): { required: Data[]; optional: Data[] } {
  const required = rows(result.required_follow_ups);
  const optional = rows(result.optional_follow_ups);
  if (required.length || optional.length) return { required, optional };
  const all = rows(result.follow_ups);
  return {
    required: all.filter((entry) => text(entry.type) !== 'context_ring'),
    optional: all.filter((entry) => text(entry.type) === 'context_ring'),
  };
}

export function followUpLabel(followUp: Data): string {
  const kind = text(followUp.type, 'follow-up');
  const target = text(followUp.doc, text(followUp.task, text(followUp.command)));
  return target ? kind + ' · ' + target : kind;
}

export interface FinishOutcome {
  applied: boolean;
  taskDone: boolean | null;
  closeoutComplete: boolean | null;
  verify: VerifyState;
  required: Data[];
  optional: Data[];
  captureRecord: Data | null;
  captureFailed: boolean;
  refreshStale: boolean;
  refreshReason: string;
  staleMessage: string;
  message: string;
  raw: Data;
}
export function finishOutcome(result: Data): FinishOutcome {
  const refresh = object(result.refresh_failure);
  const refreshStale = Object.keys(refresh).length > 0;
  const refreshReason = text(refresh.error);
  const taskDone = typeof result.task_done === 'boolean' ? result.task_done : null;
  const closeoutComplete = typeof result.closeout_complete === 'boolean' ? result.closeout_complete : null;
  const verify = verifyState(result);
  const { required, optional } = splitFollowUps(result);
  const captureRecord = Object.keys(object(result.capture_record)).length ? object(result.capture_record) : null;
  const capture = object(result.capture);
  const captureFailed = (captureRecord !== null && captureRecord.expected === true && captureRecord.recorded !== true)
    || (typeof capture.record_error === 'string' && capture.record_error !== '');
  const failure = text(result.error);
  const applied = result.status !== 'refused' && !failure && taskDone === true;
  const staleMessage = refreshStale
    ? 'The change was applied, but the view is stale: ' + (refreshReason || 'the refresh failed') + '. Reload to see the current state.'
    : '';
  let message: string;
  if (staleMessage) message = staleMessage;
  else if (taskDone === false) message = 'The task was not marked done: ' + (failure || 'the store kept it open');
  else if (taskDone === true && failure && result.status !== 'refused') {
    /* The status write landed, then a follow-up write failed (the observed v1
       cursor failure). "Nothing was applied" would be false: the task is done. */
    message = 'The task is done, but the follow-up write failed: ' + failure.replace(/[.!?]+$/, '') + '.';
  } else if (!applied) message = 'The store refused the change; nothing was applied.';
  else if (closeoutComplete === false) message = 'The task is done, but the closeout is incomplete: ' + plural(required.length, 'required follow-up') + ' remain.';
  else if (closeoutComplete === true) message = 'The task is done and the closeout is complete.';
  else message = 'The task is done; the closeout state is unknown (no durable record yet).';
  return { applied, taskDone, closeoutComplete, verify, required, optional, captureRecord, captureFailed, refreshStale, refreshReason, staleMessage, message, raw: result };
}

/* ---------- the work-capture panel (pure, unit-tested) ---------- */
export interface CapturePanelData {
  known: boolean;
  baseline: Data | null;
  holder: string;
  worktree: string;
  acquired: string;
  expires: string;
  episode: Data | null;
  episodesTotal: number;
  capture: Data | null;
  captured: boolean | null;
  attribution: string;
  reasonCode: string;
  observedFiles: number | null;
  recordedFiles: number | null;
  recordedCommits: number | null;
  exclusions: Data;
  uncertainty: string;
}
export function capturePanel(episodeView: Data | undefined): CapturePanelData {
  const envelope = object(episodeView);
  const data = envelope.data !== undefined ? object(envelope.data) : envelope;
  const episodes = rows(data.episodes);
  const current = episodes.length ? episodes[episodes.length - 1] : null;
  const captures = rows(current?.captures);
  const capture = captures.length ? captures[captures.length - 1] : null;
  const live = Object.keys(object(data.live)).length ? object(data.live) : null;
  const baseline = live ? object(live.baseline) : null;
  const captured = capture && typeof capture.captured === 'boolean' ? capture.captured : null;
  const observed = object(data.pointers ? object(data.pointers).observed : undefined);
  const exclusions = {
    ambiguous: capture?.ambiguous ?? null,
    unverifiable: capture?.unverifiable ?? null,
    merged_in: capture?.merged_in ?? null,
    overlap: capture && Array.isArray(capture.overlap) ? capture.overlap : [],
    late: capture ? capture.late === true : false,
  };
  const uncertainty = capture === null
    ? (text(observed.reason) || 'No capture event: the observed work is unknown, not empty.')
    : captured === false
      ? 'The capture recorded a failure: the observed work is not complete.'
      : captured === null
        ? 'The capture event has no usable payload: the observed work is unknown.'
        : 'Attribution: ' + text(capture.attribution, 'unknown') + ' - no known conflict, an observation and never proof of provenance.';
  return {
    known: episodes.length > 0 || live !== null,
    baseline: baseline && Object.keys(baseline).length ? baseline : null,
    holder: live ? text(live.holder) : '',
    worktree: live ? text(live.worktree) : '',
    acquired: live ? text(live.acquired) : '',
    expires: live ? text(live.expires) : '',
    episode: current,
    episodesTotal: typeof data.episodes_total === 'number' ? data.episodes_total : episodes.length,
    capture,
    captured,
    attribution: capture ? text(capture.attribution) : '',
    reasonCode: capture ? text(capture.reason_code) : '',
    observedFiles: capture && typeof capture.observed_files === 'number' ? capture.observed_files : null,
    recordedFiles: capture && typeof capture.recorded_files === 'number' ? capture.recorded_files : null,
    recordedCommits: capture && typeof capture.recorded_commits === 'number' ? capture.recorded_commits : null,
    exclusions,
    uncertainty,
  };
}

/* ---------- the pre-start consideration preview (pure, unit-tested) ---------- */
export interface ConsiderationData {
  ready: boolean | null;
  blockers: Data[];
  nextActions: Data[];
  plans: Data[];
  conventions: Data;
  rails: Data[];
  follow: Data[];
  avoid: Data[];
  files: Data[];
  checks: Data[];
  acceptance: string[];
  why: Data;
  body: string;
  future: Data;
  open: Data;
  note: string;
}
export function considerationPreview(packet: Data): ConsiderationData {
  const order = object(packet.work_order);
  const readiness = object(packet.readiness);
  const doneWhen = object(order.done_when);
  return {
    ready: typeof readiness.ready === 'boolean' ? readiness.ready : null,
    blockers: rows(readiness.blockers),
    nextActions: rows(readiness.next_actions),
    plans: rows(packet.scope_plans),
    conventions: object(order.conventions),
    rails: rows(order.rails),
    follow: rows(object(order.past).follow),
    avoid: rows(object(order.past).avoid),
    files: rows(order.files),
    checks: rows(doneWhen.checks),
    acceptance: stringList(doneWhen.acceptance),
    why: object(order.why),
    body: text(object(packet.task).body),
    future: object(order.future),
    open: object(order.open),
    note: 'Read-only preview: the ready-plan gate is decided by the server at start. The UI keeps no considered checklist of its own.',
  };
}

export async function readIntelView(request: PageProps['request'], view: string, args: Record<string, string> = {}, signal?: AbortSignal): Promise<Data> {
  const query = new URLSearchParams(args).toString();
  const path = '/api/read/' + view + (query ? '?' + query : '');
  const document = await (request as unknown as (value: string, init?: RequestInit) => Promise<Data>)(path, { signal });
  if (text(object(document).contract) !== INTEL_CONTRACT) throw new Error('The ' + view + ' read has an unsupported contract');
  return document;
}

/* ---------- presentational pieces ---------- */
function WorkspaceSection({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="panel" style={{ marginBottom: 10 }}>
      <div className="panel-h"><h2>{title}</h2></div>
      <div style={{ padding: '6px 14px 12px' }}>
        {hint ? <p className="faint" style={{ fontSize: 12, marginTop: 0 }}>{hint}</p> : null}
        {children}
      </div>
    </section>
  );
}
function CapturePanel({ capture }: { capture: CapturePanelData }) {
  const facts: [string, string][] = [
    ['Episode', capture.episode ? 'acquired ' + (capture.acquired || 'unknown') + (capture.episode.released ? ', released ' + text(capture.episode.released) : ', live') : 'no episode recorded'],
    ['Episodes', String(capture.episodesTotal)],
    ['Baseline', capture.baseline ? 'recorded on the lease' : 'no live claim: baseline unknown'],
    ['Observed files', capture.observedFiles === null ? 'unknown' : String(capture.observedFiles)],
    ['Recorded files', capture.recordedFiles === null ? 'unknown' : String(capture.recordedFiles)],
    ['Recorded commits', capture.recordedCommits === null ? 'unknown' : String(capture.recordedCommits)],
    ['Exclusions', 'ambiguous ' + String(capture.exclusions.ambiguous ?? 'unknown') + ' / unverifiable ' + String(capture.exclusions.unverifiable ?? 'unknown') + ' / merged ' + String(capture.exclusions.merged_in ?? 'unknown') + ' / overlap ' + String(Array.isArray(capture.exclusions.overlap) ? capture.exclusions.overlap.length : 0)],
  ];
  return (
    <div>
      {facts.map(([key, value]) => (
        <div className="row" key={key} style={{ gap: 8, alignItems: 'baseline', padding: '2px 0' }}>
          <span className="faint" style={{ fontSize: 12, minWidth: 110 }}>{key}</span>
          <span style={{ fontSize: 13, overflowWrap: 'anywhere' }}>{value}</span>
        </div>
      ))}
      <p className="muted" style={{ fontSize: 12, lineHeight: 1.5, marginBottom: 0 }}>{capture.uncertainty}</p>
      {capture.baseline ? (
        <details style={{ marginTop: 6 }}>
          <summary className="faint" style={{ fontSize: 12 }}>Recorded baseline</summary>
          <pre className="rail-code" style={{ fontSize: 11, overflowX: 'auto' }}>{JSON.stringify(capture.baseline, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}
function CloseoutPanel({ episodeView, result }: { episodeView: Data | undefined; result: Data | null }) {
  const envelope = object(episodeView);
  const data = envelope.data !== undefined ? object(envelope.data) : envelope;
  const closeout = object(data.closeout);
  const outcome = result ? finishOutcome(result) : null;
  const state = text(closeout.state, 'unknown');
  const complete = typeof closeout.complete === 'boolean' ? closeout.complete : null;
  return (
    <div>
      <p style={{ marginTop: 0, fontSize: 13 }}>
        {state === 'recorded'
          ? (complete === true ? 'Closeout complete: the done record says every required follow-up closed.' : complete === false ? 'Closeout incomplete: the done record lists required follow-ups.' : 'A durable record exists but its closeout value is unknown.')
          : 'Closeout unknown: no durable done record exists. It is never inferred from the task status.'}
      </p>
      {state === 'recorded' ? (
        <div className="faint" style={{ fontSize: 12 }}>
          {'required ' + String(closeout.required_follow_ups_count ?? 'unknown') + ' · optional ' + String(closeout.optional_follow_ups_count ?? 'unknown') + (closeout.event ? ' · event ' + text(closeout.event) : '')}
        </div>
      ) : null}
      {outcome ? (
        <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
          {'This finish: task done ' + (outcome.taskDone === true ? 'yes' : outcome.taskDone === false ? 'no' : 'unknown') + '; closeout ' + (outcome.closeoutComplete === true ? 'complete' : outcome.closeoutComplete === false ? 'incomplete' : 'unknown') + '.'}
        </p>
      ) : null}
    </div>
  );
}

/* ---------- the workspace drawer ---------- */
export function TaskWorkspaceDrawer({ task, project, read, request, busy, openDoc, onClose, result, onRecordCorrection }: {
  task: Data; project: string; read: PageProps['read']; request: PageProps['request'];
  busy: boolean; openDoc: (id: string) => void; onClose: () => void; result?: Data | null;
  onRecordCorrection?: () => void;
}) {
  const taskId = idOf(task);
  /* A done task has no pre-start package to prepare (the server refuses it and
     must keep refusing); its durable record is the episode read. */
  const done = text(task.status) === 'done';
  const prepare = useQuery({ queryKey: [RAIL_KEY, project, 'task-workspace', taskId, 'prepare'], queryFn: ({ signal }) => read('prepare', { task: taskId }, signal), retry: false, enabled: !busy && !done });
  const episode = useQuery({ queryKey: [RAIL_KEY, project, 'task-workspace', taskId, 'episode'], queryFn: ({ signal }) => readIntelView(request, 'ui_task_episode', { task: taskId }, signal), retry: false, enabled: !busy });
  const ref = useRef<HTMLElement>(null);
  useOverlayEscape(10, onClose);
  const trapKey = useModalFocus(ref);
  const preview = prepare.data ? considerationPreview(prepare.data) : null;
  const capture = capturePanel(episode.data);
  return (
    <>
      <div className="scrim" style={{ zIndex: 70 }} onClick={onClose} />
      <aside className="drawer" ref={ref} role="dialog" aria-modal="true" aria-label={'Task workspace: ' + titleOf(task)} tabIndex={-1} onKeyDown={trapKey} style={{ zIndex: 71 }}>
        <div className="drawer-h">
          <span className="mono faint" style={{ fontSize: '11.5px' }}>{taskId}</span>
          <span className="badge gray">{text(task.status, 'todo')}</span>
          <span className="sp" />
          {onRecordCorrection ? (
            <button type="button" className="btn btn-sm btn-secondary" aria-label={'Record a correction for ' + titleOf(task)} onClick={onRecordCorrection}>
              Record a correction
            </button>
          ) : null}
          <button type="button" className="ibtn ibtn-sm" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="drawer-b">
          <h2 style={{ marginTop: 0, fontSize: 16 }}>{titleOf(task)}</h2>
          {done ? (
            <>
              <p className="faint" style={{ fontSize: 12 }}>
                {'This task is done: the workspace shows its durable record instead of the pre-start package.'}
              </p>
              <WorkspaceSection title="Observed work" hint="Baseline, episode and captures from the durable observation log.">
                {episode.isError ? <p className="faint" style={{ fontSize: 12 }}>{'Observed work unavailable: ' + episode.error.message}</p> : <CapturePanel capture={capture} />}
              </WorkspaceSection>
              <WorkspaceSection title="Closeout" hint="The durable done record; an absent record stays unknown.">
                <CloseoutPanel episodeView={episode.data} result={result ?? null} />
              </WorkspaceSection>
            </>
          ) : null}
          {!done && prepare.isError ? <ErrorState error={prepare.error.message} retry={() => void prepare.refetch()} /> : null}
          {prepare.data ? (
            <>
              <WorkspaceSection title="Why and acceptance" hint="From the task package the store serves.">
                {preview && preview.body ? <Markdown>{preview.body}</Markdown> : <p className="faint" style={{ fontSize: 13 }}>The store records no task body yet.</p>}
                {preview && preview.why.goal ? <p className="muted" style={{ fontSize: 13 }}>{'Goal: ' + text(preview.why.goal)}</p> : null}
                {preview ? preview.files.slice(0, 12).map((file) => (
                  <button type="button" key={text(file.path)} className="link rail-link" style={{ fontSize: 12, marginRight: 8 }} onClick={() => openDoc(text(file.path))}>
                    {text(file.path) + (file.exists === false ? ' (missing)' : '')}
                  </button>
                )) : null}
                {preview && preview.acceptance.length ? (
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 13 }}>
                    {preview.acceptance.slice(0, 12).map((line, index) => <li key={index}>{line}</li>)}
                  </ul>
                ) : <p className="faint" style={{ fontSize: 12 }}>No acceptance line is recorded for this task.</p>}
              </WorkspaceSection>
              <WorkspaceSection title="Governing conventions and decisions" hint="What the store says governs this work; nothing is authored here.">
                {preview && Object.entries(preview.conventions).length ? (
                  <div style={{ fontSize: 13 }}>
                    {Object.entries(preview.conventions).map(([key, value]) => (
                      <div key={key} style={{ marginBottom: 4 }}><b>{titleCase(key)}</b>{' · ' + text(value)}</div>
                    ))}
                  </div>
                ) : <p className="faint" style={{ fontSize: 12 }}>No governing convention text is recorded.</p>}
                {preview && preview.follow.length ? (
                  <div style={{ marginTop: 8, fontSize: 13 }}>
                    <div className="faint" style={{ fontSize: 12 }}>Decisions to follow</div>
                    {preview.follow.slice(0, 8).map((row) => <div key={text(row.id)}><b>{text(row.id)}</b>{' · ' + text(row.title) + (row.choice ? ' · ' + text(row.choice) : '')}</div>)}
                  </div>
                ) : null}
                {preview && preview.avoid.length ? (
                  <div style={{ marginTop: 8, fontSize: 13 }}>
                    <div className="faint" style={{ fontSize: 12 }}>Boundaries and rejected decisions</div>
                    {preview.avoid.slice(0, 8).map((row, index) => <div key={index}>{text(row.id, '') + (row.id ? ' · ' : '') + text(row.title, text(row.reason))}</div>)}
                  </div>
                ) : null}
                {preview && preview.rails.length ? (
                  <div style={{ marginTop: 8, fontSize: 12 }}>
                    {preview.rails.slice(0, 8).map((rail) => <div key={text(rail.id) + text(rail.source)}><b>{text(rail.id)}</b>{' [' + text(rail.status) + '] ' + text(rail.command)}</div>)}
                  </div>
                ) : null}
              </WorkspaceSection>
              <WorkspaceSection title="Consideration plan (read-only preview)" hint={preview ? preview.note : undefined}>
                {preview && preview.ready !== null ? (
                  <p style={{ fontSize: 13, marginTop: 0 }}>{'Server readiness: ' + (preview.ready ? 'ready to start' : 'blocked')}</p>
                ) : null}
                {preview ? preview.blockers.map((blocker, index) => (
                  <p key={index} className="faint" style={{ fontSize: 12 }}>{text(blocker.code) + ': ' + text(blocker.message, text(blocker.error))}</p>
                )) : null}
                {preview && preview.plans.length ? preview.plans.map((plan) => (
                  <div key={text(plan.item)} style={{ fontSize: 13 }}>{text(plan.item) + ': plan ' + text(plan.status, 'not recorded')}</div>
                )) : <p className="faint" style={{ fontSize: 12 }}>The store holds no scope plan for this task yet.</p>}
              </WorkspaceSection>
              <WorkspaceSection title="Observed work" hint="Baseline, episode and captures from the durable observation log.">
                {episode.isError ? <p className="faint" style={{ fontSize: 12 }}>{'Observed work unavailable: ' + episode.error.message}</p> : <CapturePanel capture={capture} />}
              </WorkspaceSection>
              <WorkspaceSection title="Checks" hint="The checks the finish runs, from the task acceptance contract.">
                {preview && preview.checks.length ? preview.checks.slice(0, 12).map((check, index) => (
                  <div key={index} style={{ fontSize: 12 }}>{text(check.text, text(check.command)) + (check.command && check.command !== check.text ? ' · ' + text(check.command) : '')}</div>
                )) : <p className="faint" style={{ fontSize: 12 }}>No check is recorded for this task: the finish runs nothing it cannot name.</p>}
              </WorkspaceSection>
              <WorkspaceSection title="Closeout" hint="The durable done record; an absent record stays unknown.">
                <CloseoutPanel episodeView={episode.data} result={result ?? null} />
              </WorkspaceSection>
            </>
          ) : !done && prepare.isPending ? <Loading /> : null}
        </div>
      </aside>
    </>
  );
}

/* ---------- the finish dialog ---------- */
export function FinishDialog({ task, busy, onSubmit, onClose }: { task: Data; busy: boolean; onSubmit: (payload: Record<string, unknown>) => void; onClose: () => void }) {
  const [summary, setSummary] = useState('');
  const [scope, setScope] = useState('task');
  const [timeout, setTimeoutValue] = useState('');
  const [skip, setSkip] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  /* Escape is owned by the shared ordered stack (confirm priority), and focus
     enters the alertdialog and cycles inside it while the finish runs. */
  useOverlayEscape(40, () => { if (!busy) onClose(); });
  const trapKey = useModalFocus(ref, '');
  const payload = donePayload({ task: idOf(task), summary, verifyScope: scope, timeout, skipVerify: skip });
  return (
    <>
      <div className="scrim" style={{ zIndex: 80 }} onClick={() => { if (!busy) onClose(); }} />
      <div className="modal-wrap" style={{ zIndex: 81 }} onClick={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
        <div className="modal sm" ref={ref} role="alertdialog" aria-modal="true" aria-labelledby="mt-finish" tabIndex={-1} data-focus-scope="confirm" onKeyDown={trapKey}>
          <div className="modal-b" style={{ paddingTop: 18, gap: 10 }}>
            <h2 id="mt-finish" style={{ fontSize: 15, fontWeight: 600, margin: '4px 0 6px' }}>Finish this task?</h2>
            <p className="muted" style={{ marginTop: 0, fontSize: 13, lineHeight: 1.5 }}>
              {skip
                ? 'No proof is claimed: the task is marked done without running any check.'
                : 'Finishing runs the task checks first: shell commands from its scope items, on this machine, within the budget. The task stays open if one fails.'}
            </p>
            <label className="faint" style={{ fontSize: 12, display: 'block' }} htmlFor="finish-summary">Summary (optional)</label>
            <textarea id="finish-summary" aria-label="Summary (optional)" className="input" rows={2} value={summary} disabled={busy} onChange={(event) => setSummary(event.target.value)} placeholder="One line for the Completed log" />
            <label className="faint" style={{ fontSize: 12, display: 'block' }} htmlFor="finish-scope">Verification</label>
            <select id="finish-scope" aria-label="Verification" className="select" value={scope} disabled={busy || skip} onChange={(event) => setScope(event.target.value)}>
              <option value="task">Linked task checks (default)</option>
              <option value="project-impact">Project impact (wider: changed files and stale checks)</option>
            </select>
            <label className="faint" style={{ fontSize: 12, display: 'block' }} htmlFor="finish-timeout">Verification timeout in seconds (optional)</label>
            <input id="finish-timeout" aria-label="Verification timeout in seconds" className="input" type="number" min={1} step={1} value={timeout} disabled={busy || skip} onChange={(event) => setTimeoutValue(event.target.value)} placeholder="300 (the default budget)" />
            <label className="row" style={{ gap: 6, fontSize: 13, alignItems: 'center' }}>
              <input type="checkbox" checked={skip} disabled={busy} onChange={(event) => setSkip(event.target.checked)} />
              <span>Skip verification - explicitly no proof</span>
            </label>
          </div>
          <div className="modal-f">
            <span className="sp" />
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onSubmit(payload)}>{skip ? 'Finish without checks' : 'Run'}</button>
          </div>
        </div>
      </div>
    </>
  );
}

/* ---------- the retained semantic result ---------- */
export function FinishResultPanel({ result, onReload }: { result: Data; onReload?: () => void }) {
  const outcome = finishOutcome(result);
  const requirement = outcome.closeoutComplete === false;
  return (
    <section className="panel" style={{ margin: '12px 20px 0' }} data-testid="finish-result">
      <div className="panel-h">
        <h2>The store answer</h2>
        {outcome.refreshStale ? <Badge tone="warning">view stale</Badge> : requirement ? <Badge tone="danger">closeout incomplete</Badge> : outcome.applied ? <Badge tone="good">applied</Badge> : <Badge tone="warning">not applied</Badge>}
      </div>
      <div style={{ padding: '8px 14px 12px' }}>
        {outcome.staleMessage ? (
          <p role="alert" style={{ fontSize: 13, marginTop: 0 }}>
            {outcome.staleMessage}
            {onReload ? <button type="button" className="btn btn-sm btn-secondary" style={{ marginLeft: 8 }} onClick={onReload}>Reload the view</button> : null}
          </p>
        ) : (
          <p style={{ fontSize: 13, marginTop: 0 }}>{outcome.message}</p>
        )}
        <div className="row" style={{ gap: 18, flexWrap: 'wrap', fontSize: 13 }}>
          <span data-state={'task-done:' + String(outcome.taskDone)}>{'Task done: ' + (outcome.taskDone === true ? 'yes' : outcome.taskDone === false ? 'no' : 'unknown')}</span>
          <span data-state={'closeout:' + String(outcome.closeoutComplete)}>{'Closeout complete: ' + (outcome.closeoutComplete === true ? 'yes' : outcome.closeoutComplete === false ? 'no' : 'unknown')}</span>
          <span data-state={'verify:' + outcome.verify}>{'Verification: ' + outcome.verify}</span>
          {outcome.captureFailed ? <span className="bad">{'Capture record failed: ' + text(outcome.captureRecord?.error, 'the observed work was not recorded')}</span> : null}
        </div>
        <div style={{ marginTop: 8 }}>
          <div className="faint" style={{ fontSize: 12 }}>Required follow-ups</div>
          {outcome.required.length ? (
            <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 13 }}>
              {outcome.required.map((entry, index) => <li key={index}>{followUpLabel(entry) + (text(entry.command) ? ' - ' + text(entry.command) : '')}</li>)}
            </ul>
          ) : <p className="faint" style={{ fontSize: 12, margin: '2px 0 0' }}>None: every required follow-up is closed.</p>}
        </div>
        <div style={{ marginTop: 8 }}>
          <div className="faint" style={{ fontSize: 12 }}>Optional context rings (not blockers)</div>
          {outcome.optional.length ? (
            <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 13 }}>
              {outcome.optional.map((entry, index) => <li key={index}>{followUpLabel(entry) + (text(entry.command) ? ' - ' + text(entry.command) : '')}</li>)}
            </ul>
          ) : <p className="faint" style={{ fontSize: 12, margin: '2px 0 0' }}>None recorded.</p>}
        </div>
        <details style={{ marginTop: 8 }}>
          <summary className="faint" style={{ fontSize: 12 }}>Full semantic result</summary>
          <pre className="rail-code" style={{ fontSize: 11, overflowX: 'auto', maxHeight: 260 }}>{JSON.stringify(result, null, 2)}</pre>
        </details>
      </div>
    </section>
  );
}