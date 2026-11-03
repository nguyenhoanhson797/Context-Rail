/* =====================================================================
   UNDERSTANDING SCREENS (React migration - task-1)

   Overview, Outcomes, Traceability, Impact and Catch up (Activity). Every
   screen takes PageProps from ../contracts and renders ../ui primitives plus
   the semantic rail-* classes the shell styles (Lead-owned CSS).

   Data comes from the real read model, never from a screen-local mock:
   - read('understanding') is the composed project model (vision -> goals ->
     items -> proof) the CLI and MCP serve; Overview, Outcomes and Traceability
     share one TanStack Query entry ['rail', project, 'understanding'], pass the
     query's AbortSignal to the read and are disabled while a write is running.
   - read('impact') is the reader's own output with the deep-link parameters
     (files, since) as its arguments.
   - read('activity') is the event log (or project.json's status_log on a v1
     store) with limit/all as its arguments; the one write is mark-seen, which
     acks exactly the newest DISPLAYED boundary through mutate('activity').

   Rules kept from the legacy screens, on purpose:
   - Proof, work and mapping are separate. `proof` is the weighted scope-score
     ratio; `execution` is task-status counts and is never shown as a percent of
     proof; a goal with no scope item and no gap reads "mapping unknown", never a
     pass. A fraction below 1 never reads 100% (0.999 -> 99.9%), a value outside
     0..1 or a non-number is "not assessed", and a scope item with no rubric
     (has_checklist false, proven null) is never coerced to 0%.
   - A tone is good only for a full ratio with no failed or stale check; a
     partial or zero ratio is a warning, and no score at all is neutral. No
     total sums overlapping items: a shared scope item shows under each goal.
   - Overview stays concise (bounded previews, a show-all link); Outcomes shows
     the full acceptance, every check, the work and the gaps; Traceability is
     the matrix and its gaps; the declared-relation list is a collapsed
     disclosure capped at ten rows plus the exact total.
   - Mark as seen is offered only for the boundary this render actually
     displayed, and it is disabled when that boundary is missing or malformed.
     A plain read never moves the marker.
   - Prose from the store goes through the reviewed safe Markdown component in
     ../ui (skipHtml, external http(s) links only). Store text is rendered as
     React text nodes; nothing reaches dangerouslySetInnerHTML.
   ===================================================================== */
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  ArrowLeft,
  Ban,
  Check,
  ChevronDown,
  CircleCheck,
  Circle,
  CircleDashed,
  CircleDot,
  CircleHelp,
  ClipboardCheck,
  File as FileGlyph,
  FileText,
  Flag,
  GitBranch,
  History,
  House,
  ListChecks,
  Loader,
  Lock,
  Route as RouteGlyph,
  RotateCcw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Square,
  SquareKanban,
  Stethoscope,
  Target,
  TriangleAlert,
  Users,
} from '../icons';
import { Files as FilesGlyph } from '../icons';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { readKey } from '../refresh';
import { EVIDENCE } from './evidence';
import { outcomeByItem, outcomeLabel, type Outcome } from '../outcomes';
import { Badge, Button, ErrorState, Loading, Markdown, PageHeading, cn } from '../ui';

type Tone = 'neutral' | 'good' | 'warning' | 'danger' | 'accent';

/* ---------- vocabulary ---------- */
const PRIORITY_TONE: Record<string, Tone> = { must: 'accent', should: 'accent', could: 'neutral', wont: 'neutral' };
/* The priority pill class exactly as the legacy badge() helper names it: a "should"
   item is violet, which the shared Badge (accent/good/warning/danger/neutral) cannot express. */
const PRIORITY_CLASS: Record<string, string> = { must: 'accent', should: 'violet', could: 'gray', wont: 'gray' };
const WORK_TONE: Record<string, Tone> = { done: 'good', 'in-progress': 'accent', blocked: 'danger', todo: 'neutral' };
const CHECK_TONE: Record<string, Tone> = { pass: 'good', fail: 'danger', pending: 'neutral', asserted: 'warning' };
const RISK_TONE: Record<string, Tone> = { critical: 'danger', high: 'danger', medium: 'warning', low: 'neutral' };

/* Overview stays concise: a few rows per section, then a show-all link. The
   unbounded lists (declared relations, a phase's scope items) are summarised:
   an exact total plus at most this many rows that touch what is on screen. */
const PREVIEW_ITEMS = 6;
const PREVIEW_GAPS = 2;
const NOW_PREVIEW = 1;
const DONE_PREVIEW = 5;
const RELATION_PREVIEW = 10;
const UPCOMING_ITEMS = 6;
/* Summary-first (owner-approved) bounds: the default view keeps exact counts and a
   bounded preview; every remaining row stays in the DOM behind a native, closed
   disclosure, so nothing is dropped and no total is reduced to a preview size. */
const GOAL_CARD_PREVIEW = 4;
const PROGRESS_PREVIEW = 2;
const UPCOMING_PHASE_PREVIEW = 1;
const UNMAPPED_PREVIEW = 3;
const ITEM_PREVIEW = 5;
const GOAL_GAP_PREVIEW = 3;
const GOAL_WORK_PREVIEW = 5;
const SOURCE_PREVIEW = 160;

const titleOf = (doc: unknown): string => {
  const d = object(doc);
  return text(d.title, text(d.id));
};
const stringList = (value: unknown): string[] => (Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []);
const numOf = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;
/* A stored instant reads as its day ("2026-10-17"); the exact time stays in the tooltip, never as raw ISO text in a sentence. */
export const dayOf = (value: string): string => value.match(/^(\d{4}-\d{2}-\d{2})T/)?.[1] ?? value;
/* Stored headings keep their markdown marks ("## Sub Goals"); a reader never needs the hashes. */
export const plainHeading = (value: string): string => value.replace(/^#+\s*/, '').trim();
/* A quoted source line shows [[SC-24]] / [[SC-24|label]] as the name a reader sees, not as link syntax. */
export const plainLinks = (value: string): string => value.replace(/\[\[([^\]|]+)(?:\|([^\]]*))?\]\]/g, (_match, target: string, label?: string) => (label || target).trim());

/* ---------- score units (ported from the legacy helpers so a tick never reads as proof) ---------- */
/* Only a real fraction in 0..1 is a percent. A fraction below 1 never rounds up
   to 100%: 0.999 keeps a decimal (99.9%), because 100% is a claim that nothing is
   left. A missing, out-of-range or non-numeric value is "not assessed" rather
   than a reassuring 0 that looks scored. */
export function fractionPct(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) return null;
  if (value >= 1) return 100;
  const n = Math.round(value * 1000) / 10;
  return n >= 100 ? 99.9 : n;
}
export const pctLabel = (value: unknown, missing = 'not assessed'): string => {
  const n = fractionPct(value);
  return n === null ? missing : `${n}%`;
};
/* A stale or failed diagnostic anywhere behind the item means a full ratio is no
   longer green: the proof stopped holding. */
export function hasProblem(checks: unknown): boolean {
  return rows(checks).some((check) => text(check.state) === 'fail' || (text(check.detail) && text(check.detail).toLowerCase().includes('stale')));
}
/* Tone for a proof ratio: good only for a full ratio (exactly 100%) with nothing
   stale or failed, warning for a partial or 0 result, neutral when there is no
   score at all. */
export function ratioTone(proven: number | null, checks: unknown): Tone {
  if (proven === null) return 'neutral';
  return proven === 100 && !hasProblem(checks) ? 'good' : 'warning';
}
/* The proof block as the model states it: `available: false` means a weighted
   item had no usable score, so the ratios are null rather than a coerced 0. */
export function proofValues(proof: unknown): { proven: number | null; checked: number | null; available: boolean } {
  const p = object(proof);
  const available = p.available !== false;
  return { proven: available ? fractionPct(p.proven) : null, checked: fractionPct(p.checked), available };
}
const allChecks = (items: unknown): Data[] => rows(items).flatMap((item) => rows(item.checks));

/* ---------- small presentation pieces ---------- */
function DocLink({ id, label, openDoc, className }: { id: string; label?: string; openDoc: (id: string) => void; className?: string }) {
  if (!id) return null;
  return (
    <button type="button" className={cn('link rail-link', className)} onClick={() => openDoc(id)}>
      {label || id}
    </button>
  );
}
function Prose({ value, missing }: { value: unknown; missing: string }) {
  const body = text(value).trim();
  if (!body) return <p className="faint rail-muted">{missing}</p>;
  /* The reviewed safe renderer from ../ui: skipHtml, external http(s) links only, images omitted. */
  return <Markdown>{body}</Markdown>;
}
/* A source reference: {id, path, section, line_start, line_end}. The location text
   is built in plain code and every part is a React text node. */
function SrcRef({ source, label, openDoc, compact = false }: { source: unknown; label?: string; openDoc: (id: string) => void; compact?: boolean }) {
  const src = object(source);
  const id = text(src.id);
  if (!id) return null;
  const start = numOf(src.line_start);
  const end = numOf(src.line_end);
  const line = start === null ? '' : `L${start}${end !== null && end !== start ? `-${end}` : ''}`;
  const where = [plainHeading(text(src.section)), line].filter(Boolean).join(' ');
  const tip = `${text(src.path, id)}${where ? ` - ${where}` : ''}`;
  return (
    <button
      type="button"
      className="src-ref rail-src-ref"
      aria-label={compact ? `Source: ${label || id}` : undefined}
      title={tip}
      onClick={() => openDoc(id)}
      data-path={text(src.path)}
      data-section={text(src.section)}
      data-line-start={start === null ? '' : String(start)}
      data-line-end={end === null ? '' : String(end)}
    >
      <FileText size={12} aria-hidden="true" />
      <span className="src-id rail-src-id">{label || id}</span>
      {!compact && where ? <span className="src-loc rail-src-loc">{where}</span> : null}
    </button>
  );
}
/* A native, keyboard-reachable disclosure built from the legacy .ov-details block.
   It deliberately omits rail-details: that hook stays unique to the declared
   relations disclosure, which the parity test resolves with querySelector. The
   summary always names the true count of what it holds. */
function Disclosure({ name, label, children }: { name: string; label: ReactNode; children: ReactNode }) {
  return (
    <details className="ov-details" data-disclosure={name}>
      <summary>{label}</summary>
      {children}
    </details>
  );
}
/* The one status vocabulary (pages/evidence.ts): every label below is the same
   words the Summary, the Atlas and the Scope page use. */
const PROVEN = EVIDENCE.proven.word;
const CHECKED = EVIDENCE.checked.word;
const NOT_ASSESSED = EVIDENCE.not_assessed.word;
const UNKNOWN_WORD = EVIDENCE.unknown.word;

/* Proof and work, shown side by side and never as one number. */
function ProofLine({ proof, checks, mapped }: { proof: unknown; checks: unknown; mapped: boolean }) {
  const { proven, checked, available } = proofValues(proof);
  const tone = ratioTone(proven, checks);
  return (
    <span className="proof-line rail-proof-line" data-proof-tone={tone} data-proven={proven === null ? '' : String(proven)} data-checked={checked === null ? '' : String(checked)} data-available={String(available)}>
      <Badge tone={tone}>{proven === null ? PROVEN + ' ' + NOT_ASSESSED : `${PROVEN} ${proven}%`}</Badge>
      {checked !== null && checked !== proven ? <Badge tone="neutral">{`${CHECKED} ${checked}%`}</Badge> : null}
      {available ? null : <Badge tone={mapped ? 'neutral' : 'warning'}>{mapped ? 'proof unavailable' : 'no mapping'}</Badge>}
    </span>
  );
}
function ExecLine({ exec }: { exec: unknown }) {
  const e = object(exec);
  const total = numOf(e.total) ?? 0;
  const done = numOf(e.done) ?? 0;
  const parts: [string, unknown][] = [
    ['done', e.done],
    ['in-progress', e.in_progress],
    ['blocked', e.blocked],
    ['todo', e.todo],
  ];
  return (
    <span className="exec-line rail-exec-line" data-exec-total={String(total)} data-exec-done={String(done)}>
      <span className="exec-label rail-exec-label">work</span>
      <Badge tone="neutral">{`${done}/${total} done`}</Badge>
      {parts
        .filter(([, value]) => numOf(value) !== null && (numOf(value) as number) > 0)
        .map(([label, value]) => (
          <Badge key={label} tone={WORK_TONE[label] ?? 'neutral'}>{`${numOf(value)} ${label}`}</Badge>
        ))}
    </span>
  );
}
function StatusBadge({ status }: { status: string }) {
  /* The legacy task rows show the status as a plain pill, never with a glyph. */
  return <Badge tone={WORK_TONE[status] ?? 'neutral'}>{status || 'unknown'}</Badge>;
}
function CheckLine({ check }: { check: unknown }) {
  const c = object(check);
  const tone = CHECK_TONE[text(c.state)] ?? 'neutral';
  const detail = text(c.detail) || text(c.trust);
  return (
    <div className="ov-check rail-ov-check" data-check-state={text(c.state)} data-check-trust={text(c.trust)} data-check-mode={text(c.mode)}>
      <span className="ov-check-box rail-ov-check-box" aria-hidden="true">{c.checked ? <Check size={12} /> : <Square size={12} />}</span>
      <span className="ov-check-text rail-ov-check-text">{text(c.text)}</span>
      <Badge tone={tone}>{text(c.state, c.checked ? 'ticked' : 'pending')}</Badge>
      {detail ? <span className="ov-check-detail rail-ov-check-detail">{detail}</span> : null}
    </div>
  );
}
/* `compact` moves the raw command out of the row into its own closed disclosure,
   placed as a sibling in the enclosing block so the .gap flex row is untouched. The
   command stays in the DOM; only its default visibility changes. */
function GapRow({ gap, openDoc, compact = false }: { gap: unknown; openDoc: (id: string) => void; compact?: boolean }) {
  const g = object(gap);
  const command = text(g.command);
  const row = (
    <div className="gap rail-gap" data-gap-kind={text(g.kind)} data-gap-id={text(g.id)}>
      <span className="gap-kind rail-gap-kind">{text(g.kind, 'gap').replace(/_/g, ' ')}</span>
      <div className="gap-body rail-gap-body">
        <span className="gap-id rail-gap-id">
          <DocLink id={text(g.id)} openDoc={openDoc} />
        </span>
        {text(g.why) ? <span className="gap-why rail-gap-why">{text(g.why)}</span> : null}
        {command && !compact ? <code className="gap-cmd rail-gap-cmd">{command}</code> : null}
        <SrcRef source={g.source} openDoc={openDoc} compact={compact} />
        {g.needs_input ? <Badge tone="warning">needs input</Badge> : null}
      </div>
    </div>
  );
  if (!compact || !command) return row;
  return (
    <>
      {row}
      <Disclosure name="gap-command" label="Show the command">
        <code className="gap-cmd rail-gap-cmd">{command}</code>
      </Disclosure>
    </>
  );
}

/* The priority pill exactly as the legacy badge(it.priority, PRIORITY_TONE) drew it. */
function PriorityPill({ priority }: { priority: string }) {
  if (!priority) return null;
  return <span className={`badge ${PRIORITY_CLASS[priority] ?? 'gray'}`}>{priority}</span>;
}

/* A scope item on the Overview: id, title, priority and the proof tone only -
   the full acceptance, checks and diagnostics live on the Outcomes page. */
function ItemLine({ item, openDoc, compact = false }: { item: unknown; openDoc: (id: string) => void; compact?: boolean }) {
  const it = object(item);
  const proven = fractionPct(it.proven);
  const tone = ratioTone(proven, it.checks);
  const checks = rows(it.checks);
  const tasks = rows(it.tasks);
  return (
    <div className="ov-item rail-ov-item" data-item-id={text(it.id)} data-item-tone={tone}>
      <div className="ov-item-head rail-ov-item-head">
        <Target size={14} aria-hidden="true" />
        <span className="ov-item-id mono rail-ov-item-id rail-code">{text(it.id)}</span>
        <span className="ov-item-title trunc rail-ov-item-title">{text(it.title)}</span>
        <PriorityPill priority={text(it.priority)} />
        {proven === null ? <Badge tone="neutral">{NOT_ASSESSED}</Badge> : <Badge tone={tone}>{`${PROVEN} ${proven}%`}</Badge>}
      </div>
      <div className="ov-item-meta rail-ov-item-meta">
        <Badge tone="neutral">{plural(tasks.length, 'task')}</Badge>
        <Badge tone="neutral">{plural(checks.length, 'check')}</Badge>
        <SrcRef source={it.source} label={text(it.id)} openDoc={openDoc} compact={compact}/>
      </div>
    </div>
  );
}

/* One task with the outcome it is mapped to. The outcome link is drawn only when
   a goal actually lists the task - it is never inferred. `updated` is the last
   touch, so it is labelled "last touched", never a completion date. The task title
   opens the task's context lens, exactly like the legacy openLens handler; only the
   source reference beside it opens the raw document. */
function TaskEntry({ entry, openDoc, navigate }: { entry: { task: Data; goal: Data | null }; openDoc: (id: string) => void; navigate: PageProps['navigate'] }) {
  const t = entry.task;
  const g = entry.goal;
  const status = text(t.status);
  return (
    <div className="ov-task rail-ov-task" data-task-id={text(t.id)} data-task-status={status} data-goal-id={g ? text(g.id) : ''} data-mapped={String(!!g)}>
      <CircleDot size={13} aria-hidden="true" />
      <button type="button" className="link rail-link" onClick={() => navigate('lens', { task: text(t.id) })}>
        {titleOf(t)}
      </button>
      <StatusBadge status={status} />
      {g ? (
        <button type="button" className="chip ov-outcome-link" title={text(g.text, text(g.id))} onClick={() => navigate('outcomes', { goal: text(g.id) })}>
          <Target size={12} aria-hidden="true" />
          <span className="trunc rail-ov-outcome-text">{text(g.text, text(g.id))}</span>
        </button>
      ) : (
        <Badge tone="neutral">unmapped</Badge>
      )}
      {text(t.blocked_why) ? <span className="ov-why">{text(t.blocked_why)}</span> : null}
      {text(t.updated) ? <span className="faint ov-touched rail-muted rail-ov-touched" title={text(t.updated)}>{`last touched ${dayOf(text(t.updated))}`}</span> : null}
      {/* Overview-only row: the location text is technical detail, so it is not drawn on
          the face; the full location stays in the button title and data attributes. */}
      <SrcRef source={object(t.source).id ? t.source : { id: text(t.id), path: text(t.path) }} openDoc={openDoc} compact />
    </div>
  );
}
/* A task row on the Outcomes page and in the Traceability matrix: the title opens the
   lens, the source reference still opens the document. */
function TaskRow({ task, openDoc, navigate, compact = false }: { task: unknown; openDoc: (id: string) => void; navigate: PageProps['navigate']; compact?: boolean }) {
  const t = object(task);
  return (
    <div className="ov-task rail-ov-task" data-task-id={text(t.id)} data-task-status={text(t.status)}>
      <CircleDot size={13} aria-hidden="true" />
      <button type="button" className="link rail-link" onClick={() => navigate('lens', { task: text(t.id) })}>
        {titleOf(t)}
      </button>
      <StatusBadge status={text(t.status)} />
      <SrcRef source={t.source} openDoc={openDoc} compact={compact} />
    </div>
  );
}

/* ---------- Overview: goal cards ---------- */
/* The card face is the summary: goal, proof, work and the gap/mapping state. With
   summaryFirst the item preview, the gap rows and the goal source reference move
   into one closed card-detail disclosure whose summary carries the exact counts.
   The label '24 more items', the 'Open outcome' control and every row stay in the
   DOM; only their default visibility changes. */
function GoalCard({ goal, current, openDoc, navigate, summaryFirst = false, headingLevel = 3 }: { goal: unknown; current: string; openDoc: (id: string) => void; navigate: PageProps['navigate']; summaryFirst?: boolean; headingLevel?: 2 | 3 }) {
  const g = object(goal);
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  const items = rows(g.items);
  const gaps = rows(g.gaps);
  const id = text(g.id);
  const on = !!id && id === current;
  const preview = items.slice(0, PREVIEW_ITEMS);
  const moreItems = items.length - preview.length;
  const gapPreview = gaps.slice(0, PREVIEW_GAPS);
  const moreGaps = gaps.length - gapPreview.length;
  const unmapped = !items.length && !gaps.length;
  const emptyText = unmapped ? 'No scope item and no gap: the mapping is unknown.' : 'No scope item is mapped to this goal.';
  const itemsBlock = (
    <div className="ov-card-items rail-ov-card-items">
      {preview.length ? preview.map((item) => <ItemLine key={text(item.id)} item={item} openDoc={openDoc} />) : <p className="faint rail-muted">{emptyText}</p>}
      {moreItems > 0 ? (
        <Button variant="ghost" className="ov-more rail-ov-more btn-sm" onClick={() => navigate('outcomes', { goal: id })}>
          {`${moreItems} more item${moreItems === 1 ? '' : 's'}`}
        </Button>
      ) : null}
    </div>
  );
  const gapsBlock = gapPreview.length ? (
    <div className="ov-card-gaps rail-ov-card-gaps">
      {gapPreview.map((gap, index) => (
        <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} />
      ))}
      {moreGaps > 0 ? <div className="faint ov-gap-more rail-muted rail-ov-gap-more">{`+${moreGaps} more gap${moreGaps === 1 ? '' : 's'} - open the outcome`}</div> : null}
    </div>
  ) : null;
  const detailLabel = (
    <>
      <Target size={14} aria-hidden="true" />
      Scope items <span className="faint">{`${preview.length} of ${items.length}`}</span>
      {gaps.length ? <span className="faint">{`- ${plural(gaps.length, 'gap')}`}</span> : null}
    </>
  );
  return (
    <section className={cn('ov-card rail-ov-card', on && 'on rail-ov-card-on')} data-goal-id={id}>
      <div className="ov-card-head rail-ov-card-head">
        <Heading className="ov-goal rail-ov-goal">{text(g.text, id)}</Heading>
        {summaryFirst ? null : <SrcRef source={g.source} label="Goal" openDoc={openDoc} />}
      </div>
      <div className="ov-card-nums rail-ov-card-nums">
        <ProofLine proof={g.proof} checks={allChecks(items)} mapped={items.length > 0} />
        <ExecLine exec={g.execution} />
      </div>
      {summaryFirst ? (
        <Disclosure name="card-detail" label={detailLabel}>
          <SrcRef source={g.source} label="Goal" openDoc={openDoc} />
          {itemsBlock}
          {gapsBlock}
        </Disclosure>
      ) : (
        <>{itemsBlock}{gapsBlock}</>
      )}
      <div className="ov-card-foot rail-ov-card-foot">
        <Badge tone="neutral">{plural(items.length, 'item')}</Badge>
        {unmapped ? <Badge tone="warning">mapping unknown</Badge> : gaps.length ? <Badge tone="warning">{plural(gaps.length, 'gap')}</Badge> : <Badge tone="good">no gap</Badge>}
        <span className="sp rail-spacer" />
        <Button className="btn-sm" onClick={() => navigate('outcomes', { goal: id })}>Open outcome</Button>
      </div>
    </section>
  );
}

/* The delivery status is derived from the phase's tasks and never stored; the
   stored label is shown only when it disagrees with them (the decision "How do
   phases declare their program and prerequisites?"). */
const DELIVERY_LABEL: Record<string, string> = {
  no_tasks: 'no tasks',
  not_started: 'not started',
  in_flight: 'in flight',
  blocked: 'blocked',
  done: 'done',
};
const DELIVERY_TONE: Record<string, Tone> = { no_tasks: 'neutral', not_started: 'neutral', in_flight: 'accent', blocked: 'danger', done: 'good' };

function UpcomingRow({ phase, openDoc, navigate, priorityOf }: { phase: unknown; openDoc: (id: string) => void; navigate: PageProps['navigate']; priorityOf: (id: string) => string }) {
  const p = object(phase);
  const items = stringList(p.items);
  const explicit = text(p.relations_state) === 'explicit';
  const shown = items.slice(0, UPCOMING_ITEMS);
  const more = items.length - shown.length;
  /* What the phase declares: the prerequisites it names, and the program it
     belongs to. A phase that names none has no prerequisite RECORDED - the
     store does not say it has none. */
  const dependsOn = stringList(p.depends_on);
  const program = text(p.program);
  /* The derived delivery and the stored label, side by side, only when they
     disagree: the tasks are the truth and the file's label is the claim. */
  const delivery = object(p.delivery);
  const derived = text(delivery.status);
  const counts = object(delivery.tasks);
  const total = numOf(counts.total);
  const done = numOf(counts.done);
  const blocked = numOf(counts.blocked);
  const stored = text(p.state, 'planned');
  const disagrees = text(p.label_disagrees);
  return (
    <div className="ov-upcoming rail-ov-upcoming" data-phase-id={text(p.id)} data-relations-state={text(p.relations_state)} data-item-total={String(items.length)}>
      <RouteGlyph size={14} aria-hidden="true" />
      <DocLink id={text(p.id)} label={text(p.title, text(p.id))} openDoc={openDoc} />
      {derived ? <Badge tone={DELIVERY_TONE[derived] ?? 'neutral'}>{DELIVERY_LABEL[derived] ?? derived}</Badge> : <Badge tone="neutral">{stored}</Badge>}
      {total != null && done != null ? <Badge tone="neutral">{`${done}/${total} tasks done`}</Badge> : null}
      {blocked ? <Badge tone="danger">{`${blocked} blocked`}</Badge> : null}
      {program ? <Badge tone="neutral">{program}</Badge> : null}
      {explicit ? <Badge tone="good">explicit relation</Badge> : <Badge tone="warning">relations unknown</Badge>}
      <SrcRef source={p.source} openDoc={openDoc} compact />
      <span className="ov-upcoming-deps rail-ov-upcoming-deps">
        <span className="faint">depends on</span>
        {dependsOn.length ? (
          dependsOn.map((id, index) => <DocLink key={`${id}-${index}`} id={id} openDoc={openDoc} />)
        ) : (
          <span className="faint rail-muted">no prerequisite recorded</span>
        )}
      </span>
      {disagrees ? <span className="faint ov-upcoming-disagrees rail-ov-upcoming-disagrees">{`the file says ${stored}: ${disagrees}`}</span> : null}
      <span className="ov-upcoming-items rail-ov-upcoming-items">
        {items.length ? (
          shown.map((item, index) => {
            const priority = priorityOf(item);
            return (
              <span className="ov-upcoming-item rail-ov-upcoming-item" key={`${item}-${index}`}>
                <Badge tone="neutral">{item}</Badge>
                {priority ? <PriorityPill priority={priority} /> : null}
              </span>
            );
          })
        ) : (
          <span className="faint rail-muted">No scope item is tied to this phase.</span>
        )}
        {more > 0 ? <button type="button" className="chip" onClick={() => navigate('traceability')}>{`+${more} more (${items.length})`}</button> : null}
      </span>
    </div>
  );
}

function RelRow({ rel, openDoc }: { rel: unknown; openDoc: (id: string) => void }) {
  const r = object(rel);
  return (
    <div className="rel rail-rel" data-rel-field={text(r.field)}>
      <GitBranch size={13} aria-hidden="true" />
      <DocLink id={text(r.from)} openDoc={openDoc} />
      <span className="rel-field faint rail-rel-field rail-muted">{text(r.field)}</span>
      <DocLink id={text(r.to)} openDoc={openDoc} />
      <SrcRef source={r.source} openDoc={openDoc} />
    </div>
  );
}
function QuestionRow({ question, openDoc }: { question: unknown; openDoc: (id: string) => void }) {
  const q = object(question);
  return (
    <div className="ov-task rail-ov-task" data-question-id={text(q.id)}>
      <CircleHelp size={13} aria-hidden="true" />
      <DocLink id={text(q.id)} label={text(q.question, titleOf(q))} openDoc={openDoc} />
      <Badge tone={text(q.status) === 'open' ? 'warning' : 'neutral'}>{text(q.status, 'open')}</Badge>
      <SrcRef source={object(q.source).id ? q.source : { id: text(q.id), path: text(q.path) }} openDoc={openDoc} compact />
    </div>
  );
}
function RiskRow({ risk, openDoc }: { risk: unknown; openDoc: (id: string) => void }) {
  const r = object(risk);
  return (
    <div className="ov-task rail-ov-task" data-risk-id={text(r.id)}>
      <TriangleAlert size={13} aria-hidden="true" />
      <DocLink id={text(r.id)} label={titleOf(r)} openDoc={openDoc} />
      {text(r.severity) ? <Badge tone={RISK_TONE[text(r.severity)] ?? 'neutral'}>{text(r.severity)}</Badge> : null}
      <Badge tone={text(r.status) === 'open' ? 'warning' : 'good'}>{text(r.status, 'open')}</Badge>
    </div>
  );
}

/* The legacy empty block (helpers.empty): a centered glyph, an es-h heading and one
   body line. The shared Empty is a different shell, so the legacy one is drawn here. */
function LegacyEmpty({ icon, title, text: body }: { icon: ReactNode; title: string; text: string }) {
  return (
    <div className="empty-state">
      <div className="glyph">{icon}</div>
      <h2 className="es-h">{title}</h2>
      <p>{body}</p>
    </div>
  );
}

/* One compact first-view progress row: the goal, what its own checks prove and its
   task counts, laid out with the legacy inline row so the icon shares the line, and
   the text wraps (never ellipsized) so the figures stay painted at 320px. It
   never sums overlapping scope items into one total; a goal with no scope item and no
   gap stays "mapping unknown" and never shows a green proof; a missing score stays
   "proven not assessed"; and a missing, negative or over-count execution stays
   "work not assessed" instead of looking like healthy progress. No timestamp or
   source location is drawn here, and name, proof and work share one text node so no
   fragment of the row is mistaken for a full card label. */
function ProgressLine({ goal }: { goal: unknown }) {
  const g = object(goal);
  const items = rows(g.items);
  const mapped = items.length > 0 || rows(g.gaps).length > 0;
  const proof = proofValues(g.proof);
  const exec = object(g.execution);
  const done = numOf(exec.done);
  const total = numOf(exec.total);
  const tidy = done !== null && total !== null && done >= 0 && total >= 0 && done <= total;
  const tone = mapped ? ratioTone(proof.proven, allChecks(items)) : 'neutral';
  const proofText = !mapped ? 'mapping ' + UNKNOWN_WORD : proof.proven === null ? PROVEN + ' ' + NOT_ASSESSED : `${PROVEN} ${proof.proven}%`;
  const workText = tidy ? `work ${done}/${total} done` : 'work ' + NOT_ASSESSED;
  return (
    <div className="row rail-ov-progress-line" data-progress-goal={text(g.id)} data-progress-tone={tone}>
      <Target size={13} aria-hidden="true" />
      <span className="grow rail-ov-progress-text">{`${text(g.text, text(g.id))} - ${proofText} - ${workText}`}</span>
    </div>
  );
}

/* A goal with no scope item and no gap is "mapping unknown", never a pass. */
/* summaryFirst bounds the visible cards and keeps the rest, with the exact totals,
   behind one closed disclosure - no card is dropped from the DOM. */
function GoalGrid({ goals, current, openDoc, navigate, summaryFirst = false, headingLevel = 3 }: { goals: Data[]; current: string; openDoc: (id: string) => void; navigate: PageProps['navigate']; summaryFirst?: boolean; headingLevel?: 2 | 3 }) {
  if (!goals.length) return <LegacyEmpty icon={<Target size={20} aria-hidden="true" />} title="No outcome yet" text="Goal.md has no sub-goal bullet. Add one with ctx-rail goal-edit --sub-goals." />;
  const shown = summaryFirst ? goals.slice(0, GOAL_CARD_PREVIEW) : goals;
  const rest = summaryFirst ? goals.slice(GOAL_CARD_PREVIEW) : [];
  const cards = (list: Data[]) => (
    <div className="ov-grid rail-ov-grid">
      {list.map((goal) => (
        <GoalCard key={text(goal.id)} goal={goal} current={current} openDoc={openDoc} navigate={navigate} summaryFirst={summaryFirst} headingLevel={headingLevel} />
      ))}
    </div>
  );
  return (
    <>
      {cards(shown)}
      {rest.length ? (
        <Disclosure name="goals-more" label={<><Target size={14} aria-hidden="true" /> Show the other <span className="faint">{`${rest.length} of ${goals.length}`}</span> goals</>}>
          {cards(rest)}
        </Disclosure>
      ) : null}
    </>
  );
}

/* =====================================================================
   OVERVIEW
   ===================================================================== */
export function OverviewPage({ snapshot, project, read, openDoc, navigate, busy }: PageProps) {
  const query = useQuery({ queryKey: readKey(project, 'understanding'), queryFn: ({ signal }) => read('understanding', {}, signal), retry: false, enabled: !busy });
  const data = object(query.data);
  const vision = object(data.vision);
  const nonGoals = object(data.non_goals);
  const goals = rows(data.goals);
  const unmapped = object(data.unmapped);
  const unmappedItems = rows(unmapped.items);
  const unmappedGaps = rows(unmapped.gaps);
  const upcoming = rows(data.upcoming);
  const relations = rows(data.relations);
  const coreDocs = rows(data.core_docs);

  /* The task set is the snapshot's (it also holds work no goal names); the mapping
     to an outcome comes only from a goal that lists the task. Every hook runs before
     the loading/error returns below, so the hook order never changes. */
  const outcomeOf = useMemo(() => {
    const map = new Map<string, Data>();
    for (const goal of goals) for (const task of rows(goal.tasks)) { const id = text(task.id); if (id && !map.has(id)) map.set(id, goal); }
    return map;
  }, [data]);
  /* The priority of every scope item the model knows: the future scope says
     which of its items are required and which are optional or a boundary. An
     item the model does not carry (a Won't item, say) keeps no priority claim. */
  const priorityOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const goal of goals) for (const item of rows(goal.items)) { const id = text(item.id); const p = text(item.priority); if (id && p && !map.has(id)) map.set(id, p); }
    for (const item of unmappedItems) { const id = text(item.id); const p = text(item.priority); if (id && p && !map.has(id)) map.set(id, p); }
    return (id: string) => map.get(id) ?? '';
  }, [goals, unmappedItems]);
  const head = (
    <PageHeading
      eyebrow="Understand"
      title="Overview"
      description="Purpose, progress, current work and what comes next."
      action={
        <>
          <Button onClick={() => navigate('outcomes')}>
            <Target size={14} aria-hidden="true" />
            Outcomes
          </Button>
          <Button onClick={() => navigate('traceability')}>
            <GitBranch size={14} aria-hidden="true" />
            Traceability
          </Button>
          <Button onClick={() => navigate('impact')}>
            <RouteGlyph size={14} aria-hidden="true" />
            Impact
          </Button>
          <Button onClick={() => navigate('activity')}>
            <History size={14} aria-hidden="true" />
            Catch up
          </Button>
        </>
      }
    />
  );
  if (query.isPending) return <div>{head}<Loading /></div>;
  if (query.isError) return <div>{head}<ErrorState error={query.error.message} retry={() => void query.refetch()} /></div>;
  const tasks = snapshot.tasks;
  const entries = (status: string): { task: Data; goal: Data | null }[] =>
    tasks.filter((task) => text(task.status) === status).map((task) => ({ task, goal: outcomeOf.get(text(task.id)) ?? null }));
  const work = entries('in-progress');
  const blocked = entries('blocked');
  const done = entries('done');
  const openQuestions = snapshot.questions.filter((question) => text(question.status) === 'open');
  const openRisks = snapshot.risks.filter((risk) => text(risk.status) === 'open');
  const inputGaps: Data[] = [];
  for (const goal of goals) for (const gap of rows(goal.gaps)) if (gap.needs_input) inputGaps.push(gap);
  for (const gap of unmappedGaps) if (gap.needs_input) inputGaps.push(gap);
  const unmappedWork = [...work, ...blocked, ...done].filter((entry) => !entry.goal).length;
  const hasBlockers = blocked.length > 0 || openQuestions.length > 0 || openRisks.length > 0 || inputGaps.length > 0;

  /* A relation list can be huge (the real sample has 500+). Show the exact total
     always, then at most RELATION_PREVIEW relations that touch a scope item already
     on this page - never a fabricated relationship, never the whole list inline. */
  const visibleIds = new Set<string>();
  for (const goal of goals) for (const item of rows(goal.items)) if (text(item.id)) visibleIds.add(text(item.id));
  for (const item of unmappedItems) if (text(item.id)) visibleIds.add(text(item.id));
  const touching = relations.filter((rel) => visibleIds.has(text(rel.from)) || visibleIds.has(text(rel.to)));
  const relShown = touching.slice(0, RELATION_PREVIEW);
  const relMore = touching.length - relShown.length;
  const relNote = relations.length
    ? `${plural(relations.length, 'declared relation')}; ${touching.length} touch a visible scope item; showing ${relShown.length}.`
    : '';

  /* The first view answers purpose -> progress -> current -> blockers -> next before
     the goal map. Every compact panel keeps its exact total in the panel head and
     leaves the remaining rows in the DOM behind a native, closed disclosure. */
  const blockerTotal = blocked.length + openQuestions.length + openRisks.length + inputGaps.length;
  const progressRows = goals.slice(0, PROGRESS_PREVIEW);
  const nowRest = work.slice(NOW_PREVIEW);
  const upcomingRest = upcoming.slice(UPCOMING_PHASE_PREVIEW);
  const unmappedItemRest = unmappedItems.slice(UNMAPPED_PREVIEW);
  const unmappedGapRest = unmappedGaps.slice(UNMAPPED_PREVIEW);
  return (
    <div>
      {head}
      <section className="panel ov-vision" data-ring="vision">
        <div className="panel-h"><Flag size={15} aria-hidden="true"/><h2>Vision</h2><div className="acts"><SrcRef source={vision.source} label="Roadmap" openDoc={openDoc} compact/></div></div>
        <div className="panel-b md"><Prose value={vision.text} missing="No vision is recorded in Roadmap.md yet."/></div>
        {/* Progress sits inside the purpose panel: compact, per goal, never a summed
            total, and the full goal cards stay below with every row intact. */}
        <div className="panel-b rail-ov-progress" data-summary="progress" data-goal-total={String(goals.length)}>
          <p className="faint ov-note rail-ov-progress-label">{goals.length > PROGRESS_PREVIEW ? `Progress - ${progressRows.length} of ${goals.length} goals` : 'Progress'}</p>
          {progressRows.length ? progressRows.map((goal) => <ProgressLine key={text(goal.id)} goal={goal} />) : <p className="faint ov-note rail-muted">No goal is recorded in Goal.md yet.</p>}
        </div>
      </section>
      <section className="panel" data-ring="now" data-summary="current">
        <div className="panel-h"><Loader size={15} aria-hidden="true" /><h2>Now</h2><div className="acts"><span className="faint">{work.length} in progress</span></div></div>
        {work.length ? (
          <>
            <div className="ov-work">
              {work.slice(0, NOW_PREVIEW).map((entry) => <TaskEntry key={text(entry.task.id)} entry={entry} openDoc={openDoc} navigate={navigate} />)}
            </div>
            {nowRest.length || unmappedWork ? (
              <Disclosure name="now-more" label={nowRest.length ? <>Show the other <span className="faint">{`${nowRest.length} of ${work.length}`}</span> in-progress tasks</> : 'Work notes'}>
                {nowRest.length ? (
                  <div className="ov-work">
                    {nowRest.map((entry) => <TaskEntry key={text(entry.task.id)} entry={entry} openDoc={openDoc} navigate={navigate} />)}
                  </div>
                ) : null}
                {unmappedWork ? <p className="faint ov-note">{`${unmappedWork === 1 ? '1 task is' : `${unmappedWork} tasks are`} not mapped to a goal.`}</p> : null}
              </Disclosure>
            ) : null}
          </>
        ) : (
          <div className="panel-b faint">No task is in progress.</div>
        )}
        <div className="ov-links">
          <Button variant="ghost" className="btn-sm" onClick={() => navigate('now')}>
            <House size={13} aria-hidden="true" /> Open Now
          </Button>
          <Button variant="ghost" className="btn-sm" onClick={() => navigate('board')}>
            <SquareKanban size={13} aria-hidden="true" /> Board
          </Button>
          <Button variant="ghost" className="btn-sm" onClick={() => navigate('activity')}>
            <History size={13} aria-hidden="true" /> Catch up
          </Button>
        </div>
      </section>

      <section className="panel" data-ring="blockers" data-summary="blockers">
        <div className="panel-h"><TriangleAlert size={15} aria-hidden="true" /><h2>Blockers and open questions</h2><div className="acts"><span className="faint">{blockerTotal} open</span></div></div>
        {hasBlockers ? (
          <>
            <p className="faint ov-note rail-ov-blocker-counts">
              <Badge tone={blocked.length ? 'danger' : 'neutral'}>{plural(blocked.length, 'blocked task')}</Badge>
              <Badge tone={openQuestions.length ? 'warning' : 'neutral'}>{plural(openQuestions.length, 'open question')}</Badge>
              <Badge tone={openRisks.length ? 'warning' : 'neutral'}>{plural(openRisks.length, 'open risk')}</Badge>
              <Badge tone={inputGaps.length ? 'warning' : 'neutral'}>{inputGaps.length} waiting on input</Badge>
            </p>
            <Disclosure name="blockers-more" label={<>Show the <span className="faint">{blockerTotal}</span> blockers and open items</>}>
              <div className="ov-blockers">
                {blocked.map((entry) => <TaskEntry key={text(entry.task.id)} entry={entry} openDoc={openDoc} navigate={navigate} />)}
                {openQuestions.map((question) => <QuestionRow key={text(question.id)} question={question} openDoc={openDoc} />)}
                {openRisks.map((risk) => <RiskRow key={text(risk.id)} risk={risk} openDoc={openDoc} />)}
                {inputGaps.map((gap, index) => <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} compact />)}
              </div>
            </Disclosure>
          </>
        ) : (
          <div className="panel-b faint">Nothing is blocked, and no question, risk or open input is waiting.</div>
        )}
      </section>

      <section className="panel" data-ring="upcoming" data-summary="next">
        <div className="panel-h"><RouteGlyph size={15} aria-hidden="true" /><h2>Upcoming</h2><div className="acts"><span className="faint">{`${plural(upcoming.length, 'planned phase')}`}</span></div></div>
        {upcoming.length ? (
          <>
            <div className="ov-upcoming-list">
              {upcoming.slice(0, UPCOMING_PHASE_PREVIEW).map((phase) => <UpcomingRow key={text(phase.id)} phase={phase} openDoc={openDoc} navigate={navigate} priorityOf={priorityOf} />)}
            </div>
            {upcomingRest.length ? (
              <Disclosure name="upcoming-more" label={<>Show the other <span className="faint">{`${upcomingRest.length} of ${upcoming.length}`}</span> planned phases</>}>
                <div className="ov-upcoming-list">
                  {upcomingRest.map((phase) => <UpcomingRow key={text(phase.id)} phase={phase} openDoc={openDoc} navigate={navigate} priorityOf={priorityOf} />)}
                </div>
                <p className="faint ov-note">A relation is shown only when the store declares one; a phase with no explicit link stays unknown - no dependency is inferred.</p>
              </Disclosure>
            ) : (
              <p className="faint ov-note">A relation is shown only when the store declares one; a phase with no explicit link stays unknown - no dependency is inferred.</p>
            )}
          </>
        ) : (
          <div className="panel-b faint">No planned phase is recorded.</div>
        )}
        <div className="ov-links">
          <Button variant="ghost" className="btn-sm" onClick={() => navigate('roadmap')}>
            <RouteGlyph size={13} aria-hidden="true" /> Open the roadmap
          </Button>
        </div>
      </section>

      <div className="ov-stats">
        <div className="stat">
          <span className="k"><Target size={14} aria-hidden="true" />Goals</span>
          <span className="v">{goals.length}</span>
          <span className="d">outcomes named</span>
        </div>
        <div className="stat">
          <span className="k"><CircleDashed size={14} aria-hidden="true" />Unmapped</span>
          <span className="v">{unmappedItems.length}</span>
          <span className="d">scope items no goal names</span>
        </div>
        <div className="stat">
          <span className="k"><RouteGlyph size={14} aria-hidden="true" />Upcoming</span>
          <span className="v">{upcoming.length}</span>
          <span className="d">planned phases</span>
        </div>
        <div className="stat">
          <span className="k"><Loader size={14} aria-hidden="true" />Now</span>
          <span className="v">{work.length}</span>
          <span className="d">tasks in progress</span>
        </div>
      </div>

      <section className="panel ov-nongoals" data-ring="non-goals">
        <div className="panel-h"><Ban size={15} aria-hidden="true"/><h2>Non-goals</h2><div className="acts"><SrcRef source={nonGoals.source} label="Roadmap" openDoc={openDoc} compact/></div></div>
        <Disclosure name="non-goals" label={<><Ban size={14} aria-hidden="true" /> Recorded non-goals <span className="faint">{nonGoals.text ? 'recorded' : 'none recorded'}</span></>}>
          <div className="panel-b md"><Prose value={nonGoals.text} missing="No non-goal is recorded yet."/></div>
        </Disclosure>
      </section>
      <h2 className="ov-h">Outcome map</h2>
      <p className="faint ov-note">
        One concise card per goal: proof and work, counts and gaps. Open a goal for its scope, acceptance and checks.
      </p>
      <GoalGrid goals={goals} current="" openDoc={openDoc} navigate={navigate} summaryFirst />

      <section className="panel" data-ring="done">
        <div className="panel-h"><CircleCheck size={15} aria-hidden="true" /><h2>Completed work</h2><div className="acts"><span className="faint">{done.length}</span></div></div>
        {done.length ? (
          <div className="ov-work">
            {done.slice(0, DONE_PREVIEW).map((entry) => <TaskEntry key={text(entry.task.id)} entry={entry} openDoc={openDoc} navigate={navigate} />)}
          </div>
        ) : (
          <div className="panel-b faint">No completed task is recorded.</div>
        )}
        {done.length > DONE_PREVIEW ? (
          <div className="ov-links">
            <Button variant="ghost" className="btn-sm" onClick={() => navigate('board')}>{`Show the rest on the Board (${done.length})`}</Button>
          </div>
        ) : null}
        <p className="faint ov-note">A date is the last touch, not when a task was completed.</p>
      </section>

      <section className="panel" data-ring="unmapped">
        <div className="panel-h"><CircleDashed size={15} aria-hidden="true" /><h2>Unmapped scope</h2><div className="acts"><span className="faint">{unmappedItems.length}</span></div></div>
        {unmappedItems.length ? (
          <>
            <div className="ov-card-items">
              {unmappedItems.slice(0, UNMAPPED_PREVIEW).map((item, index) => <ItemLine key={`${text(item.id)}-${index}`} item={item} openDoc={openDoc} compact />)}
            </div>
            {unmappedItemRest.length ? (
              <Disclosure name="unmapped-items-more" label={<>Show the other <span className="faint">{`${unmappedItemRest.length} of ${unmappedItems.length}`}</span> unmapped scope items</>}>
                <div className="ov-card-items">
                  {unmappedItemRest.map((item, index) => <ItemLine key={`${text(item.id)}-${index}`} item={item} openDoc={openDoc} />)}
                </div>
              </Disclosure>
            ) : null}
          </>
        ) : (
          <div className="panel-b faint">Every non-Won't scope item is named by a goal.</div>
        )}
        {unmappedGaps.length ? (
          <>
            <div className="ov-card-gaps">
              {unmappedGaps.slice(0, UNMAPPED_PREVIEW).map((gap, index) => <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} compact />)}
            </div>
            {unmappedGapRest.length ? (
              <Disclosure name="unmapped-gaps-more" label={<>Show the other <span className="faint">{`${unmappedGapRest.length} of ${unmappedGaps.length}`}</span> unmapped gaps</>}>
                <div className="ov-card-gaps">
                  {unmappedGapRest.map((gap, index) => <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} />)}
                </div>
              </Disclosure>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="panel" data-ring="relations">
        <div className="panel-h"><GitBranch size={15} aria-hidden="true" /><h2>Declared relations</h2><div className="acts"><span className="faint num" data-rel-total={String(relations.length)}>{`${relations.length} declared`}</span></div></div>
        {relations.length ? (
          <>
            <p className="faint ov-note">{relNote}</p>
            <details className="ov-details rail-details">
              <summary>
                <GitBranch size={14} aria-hidden="true" />
                Relations touching visible scope <span className="faint">{`${relShown.length}${relMore > 0 ? ` of ${touching.length}` : ''}`}</span>
              </summary>
              {relShown.length ? (
                <div className="rel-list">
                  {relShown.map((rel, index) => <RelRow key={`${text(rel.from)}-${text(rel.field)}-${text(rel.to)}-${index}`} rel={rel} openDoc={openDoc} />)}
                </div>
              ) : (
                <div className="panel-b faint">No declared relation touches a visible scope item.</div>
              )}
              <p className="faint ov-note">These are the links the docs declare, generic relationships - not causal dependencies.</p>
            </details>
          </>
        ) : (
          <div className="panel-b faint">No explicit relation is declared.</div>
        )}
        <div className="ov-links">
          <Button variant="ghost" className="btn-sm" onClick={() => navigate('graph')}>
            <GitBranch size={13} aria-hidden="true" /> Full graph
          </Button>
          <Button variant="ghost" className="btn-sm" onClick={() => navigate('traceability')}>
            <Target size={13} aria-hidden="true" /> Matrix
          </Button>
        </div>
      </section>

      {coreDocs.length ? (
        <section className="panel" data-ring="core-docs">
          <div className="panel-h"><FilesGlyph size={15} strokeWidth={1.8} className="i" aria-hidden="true" /><h2>Core documents</h2></div>
          <Disclosure name="core-docs" label={<><FilesGlyph size={14} aria-hidden="true" /> Open the <span className="faint">{coreDocs.length}</span> core documents</>}>
            <div className="chips">
              {coreDocs.map((doc, index) => (
                <button key={`${text(doc.id)}-${index}`} type="button" className="chip" onClick={() => openDoc(text(doc.id))}>
                  <FileText size={13} aria-hidden="true" />
                  {titleOf(doc)}
                </button>
              ))}
            </div>
          </Disclosure>
        </section>
      ) : null}
    </div>
  );
}

/* =====================================================================
   OUTCOMES - one goal's scope, proof, work and gaps
   ===================================================================== */
/* The item head is the summary; acceptance, every check and the full work list sit
   in one closed disclosure that names their exact counts. Nothing is capped inside
   it, so opening it reveals the complete evidence. */
function ItemDetail({ item, openDoc, navigate }: { item: unknown; openDoc: (id: string) => void; navigate: PageProps['navigate'] }) {
  const it = object(item);
  const acceptance = stringList(it.acceptance);
  const checks = rows(it.checks);
  const tasks = rows(it.tasks);
  const proven = fractionPct(it.proven);
  const tone = ratioTone(proven, it.checks);
  const hasDetail = acceptance.length > 0 || checks.length > 0 || tasks.length > 0;
  return (
    <div className="outcome-item" data-item-id={text(it.id)}>
      <div className="outcome-item-head rail-outcome-item-head">
        <Target size={14} aria-hidden="true" />
        <button type="button" className="link rail-link" onClick={() => navigate('traceability', { item: text(it.id) })}>
          {text(it.id)}
        </button>
        <span className="grow trunc">{text(it.title)}</span>
        <PriorityPill priority={text(it.priority)} />
        {proven === null ? <Badge tone="neutral">{PROVEN + ' ' + NOT_ASSESSED}</Badge> : <Badge tone={tone}>{`${PROVEN} ${proven}%`}</Badge>}
        <SrcRef source={it.source} label={text(it.id)} openDoc={openDoc} compact />
      </div>
      {hasDetail ? (
        <Disclosure name="item-detail" label={`Acceptance ${acceptance.length}, checks ${checks.length}, work ${tasks.length}`}>
          {acceptance.length ? (
            <div className="acceptance">
              <span className="faint">Acceptance</span>
              <ul>
                {acceptance.map((line, index) => <li key={index}>{line}</li>)}
              </ul>
            </div>
          ) : null}
          {checks.length ? (
            <div className="checks">
              {checks.map((check, index) => <CheckLine key={index} check={check} />)}
            </div>
          ) : null}
          {tasks.length ? (
            <div className="ov-work">
              {tasks.map((task) => <TaskRow key={text(task.id)} task={task} openDoc={openDoc} navigate={navigate} />)}
            </div>
          ) : null}
        </Disclosure>
      ) : (
        <p className="faint rail-muted">No acceptance, check or task is recorded for this item.</p>
      )}
    </div>
  );
}

/* The selected outcome opens with its own summary - status, proof, work and the
   gap count - before any acceptance, check or task list. Every list keeps its exact
   total in the panel head and drops nothing: the remainder (and a long goal source)
   stays in the DOM behind a native, closed disclosure. */
function OutcomeDetail({ goal, openDoc, navigate }: { goal: unknown; openDoc: (id: string) => void; navigate: PageProps['navigate'] }) {
  const g = object(goal);
  const items = rows(g.items);
  const tasks = rows(g.tasks);
  const gaps = rows(g.gaps);
  const targets = stringList(g.targets);
  const sourceText = plainLinks(text(g.source_text));
  const hasSourceText = !!sourceText && sourceText !== plainLinks(text(g.text));
  const itemRest = items.slice(ITEM_PREVIEW);
  const workShown = tasks.slice(0, GOAL_WORK_PREVIEW);
  const workRest = tasks.slice(GOAL_WORK_PREVIEW);
  const gapShown = gaps.slice(0, GOAL_GAP_PREVIEW);
  const gapRest = gaps.slice(GOAL_GAP_PREVIEW);
  return (
    <div className="outcome-detail" data-goal-id={text(g.id)}>
      <section className="panel" data-ring="outcome">
        <div className="panel-h"><Target size={15} aria-hidden="true" /><h2>{text(g.text, text(g.id))}</h2><div className="acts"><SrcRef source={g.source} label="Goal" openDoc={openDoc} compact /><Button className="btn-sm" onClick={() => navigate('traceability')}><GitBranch size={13} aria-hidden="true" />Matrix</Button></div></div>
        <div className="panel-b">
          <div className="ov-card-nums">
            <ProofLine proof={g.proof} checks={allChecks(items)} mapped={items.length > 0} />
            <ExecLine exec={g.execution} />
          </div>
          <p className="faint ov-note rail-ov-outcome-counts">
            <Badge tone="neutral">{plural(items.length, 'scope item')}</Badge>
            <Badge tone="neutral">{plural(tasks.length, 'task')}</Badge>
            {gaps.length ? <Badge tone="warning">{plural(gaps.length, 'gap')}</Badge> : <Badge tone="good">no gap</Badge>}
          </p>
        </div>
        {hasSourceText ? (
          sourceText.length <= SOURCE_PREVIEW ? (
            <blockquote className="outcome-source">{sourceText}</blockquote>
          ) : (
            <Disclosure name="goal-source" label={<>{sourceText.slice(0, SOURCE_PREVIEW).trimEnd()}... <span className="faint">{`full goal source (${sourceText.length} characters)`}</span></>}>
              <blockquote className="outcome-source">{sourceText}</blockquote>
            </Disclosure>
          )
        ) : null}
        {targets.length ? (
          <div className="chips">
            {targets.map((target, index) => <span key={`${target}-${index}`} className="chip mono">{target}</span>)}
          </div>
        ) : null}
      </section>
      <section className="panel" data-ring="items">
        <div className="panel-h"><ListChecks size={15} aria-hidden="true" /><h2>Scope, acceptance and proof</h2><div className="acts"><span className="faint">{items.length}</span></div></div>
        {items.length ? (
          <>
            {items.slice(0, ITEM_PREVIEW).map((item) => <ItemDetail key={text(item.id)} item={item} openDoc={openDoc} navigate={navigate} />)}
            {itemRest.length ? (
              <Disclosure name="items-more" label={<>Show the other <span className="faint">{`${itemRest.length} of ${items.length}`}</span> scope items</>}>
                {itemRest.map((item) => <ItemDetail key={text(item.id)} item={item} openDoc={openDoc} navigate={navigate} />)}
              </Disclosure>
            ) : null}
          </>
        ) : (
          <div className="panel-b faint">No scope item is mapped to this goal.</div>
        )}
      </section>
      <section className="panel" data-ring="work">
        <div className="panel-h"><ClipboardCheck size={15} aria-hidden="true" /><h2>Work</h2><div className="acts"><span className="faint">{tasks.length}</span></div></div>
        {tasks.length ? (
          <>
            <div className="ov-work">
              {workShown.map((task) => <TaskRow key={text(task.id)} task={task} openDoc={openDoc} navigate={navigate} compact />)}
            </div>
            {workRest.length ? (
              <Disclosure name="goal-work-more" label={<>Show the other <span className="faint">{`${workRest.length} of ${tasks.length}`}</span> tasks</>}>
                <div className="ov-work">
                  {workRest.map((task) => <TaskRow key={text(task.id)} task={task} openDoc={openDoc} navigate={navigate} />)}
                </div>
              </Disclosure>
            ) : null}
          </>
        ) : (
          <div className="panel-b faint">No task delivers this goal's items.</div>
        )}
      </section>
      <section className="panel" data-ring="gaps">
        <div className="panel-h"><TriangleAlert size={15} aria-hidden="true" /><h2>Gaps</h2><div className="acts"><span className="faint">{gaps.length}</span></div></div>
        {gaps.length ? (
          <>
            <div className="ov-card-gaps">
              {gapShown.map((gap, index) => <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} compact />)}
            </div>
            {gapRest.length ? (
              <Disclosure name="goal-gaps-more" label={<>Show the other <span className="faint">{`${gapRest.length} of ${gaps.length}`}</span> gaps</>}>
                <div className="ov-card-gaps">
                  {gapRest.map((gap, index) => <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} />)}
                </div>
              </Disclosure>
            ) : null}
          </>
        ) : (
          <div className="panel-b faint">No gap is recorded for this goal.</div>
        )}
      </section>
    </div>
  );
}

export function OutcomesPage({ project, params, read, openDoc, navigate, busy }: PageProps) {
  const query = useQuery({ queryKey: readKey(project, 'understanding'), queryFn: ({ signal }) => read('understanding', {}, signal), retry: false, enabled: !busy });
  const head = (
    <PageHeading
      eyebrow="Understand"
      title="Outcomes"
      description="One goal: its scope, what proves it, the work delivering it and what is still missing."
      action={
        <Button onClick={() => navigate('overview')}>
          <ArrowLeft size={14} aria-hidden="true" /> Overview
        </Button>
      }
    />
  );
  if (query.isPending) return <div>{head}<Loading /></div>;
  if (query.isError) return <div>{head}<ErrorState error={query.error.message} retry={() => void query.refetch()} /></div>;
  const data = object(query.data);
  const goals = rows(data.goals);
  const want = text(params.get('goal'));
  const chosen = want ? goals.find((goal) => text(goal.id) === want) : undefined;
  if (!chosen) {
    const note = want ? `No goal has the id ${want}.` : 'Pick a goal to see its outcome.';
    return (
      <div>
        {head}
        <p className="faint rail-muted" role="status">{note}</p>
        <GoalGrid goals={goals} current="" openDoc={openDoc} navigate={navigate} summaryFirst headingLevel={2} />
      </div>
    );
  }
  return (
    <div>
      {head}
      <OutcomeDetail goal={chosen} openDoc={openDoc} navigate={navigate} />
    </div>
  );
}

/* =====================================================================
   TRACEABILITY - the matrix and its gaps
   ===================================================================== */
/* The rows, unlinked items and gaps, from the understanding model or the raw
   traceability shape (rows / unlinked_items / gaps / summary) - whichever the
   read returned, so the screen never depends on one envelope. */
export function matrixOf(value: unknown): { rows: Data[]; unlinked: Data[]; gaps: Data[] } {
  const d = object(value);
  if (Array.isArray(d.rows)) return { rows: rows(d.rows), unlinked: rows(d.unlinked_items), gaps: rows(d.gaps) };
  const mapped = rows(d.goals).map((goal) => ({ sub_goal: goal.text, targets: goal.targets, items: goal.items, gaps: goal.gaps, source: goal.source }));
  const unmapped = object(d.unmapped);
  const gaps: Data[] = [];
  for (const row of mapped) for (const gap of rows(row.gaps)) gaps.push(gap);
  for (const gap of rows(unmapped.gaps)) gaps.push(gap);
  return { rows: mapped, unlinked: rows(unmapped.items), gaps };
}

/* The scope-item id opens the traceability matrix filtered to that item (the legacy
   openScope handler); the source reference beside it still opens the document. */
function TraceItemRow({ item, focused, openDoc, navigate }: { item: unknown; focused: string; openDoc: (id: string) => void; navigate: PageProps['navigate'] }) {
  const it = object(item);
  const acceptance = stringList(it.acceptance);
  const checks = rows(it.checks);
  const tasks = rows(it.tasks);
  const proven = fractionPct(it.proven);
  const tone = ratioTone(proven, it.checks);
  const on = !!focused && text(it.id) === focused;
  return (
    <tr role="row" className={on ? 'trace-on rail-trace-on' : undefined} data-item-id={text(it.id)}>
      <td role="cell" data-label="Scope item" className="trace-c-id">
        {/* The cell stays a table cell (a flex <td> stops stretching with its row and clips its own pill); the flex row is one level in. */}
        <div className="rail-trace-id">
          <Target size={13} aria-hidden="true" />
          <button type="button" className="link rail-link" onClick={() => navigate('traceability', { item: text(it.id) })}>
            {text(it.id)}
          </button>
          <SrcRef source={it.source} openDoc={openDoc} />
        </div>
      </td>
      <td role="cell" data-label="Title" className="trace-c-title rail-trace-c-title">{text(it.title)}</td>
      <td role="cell" data-label="Priority" className="trace-c-pri"><PriorityPill priority={text(it.priority)} /></td>
      <td role="cell" data-label="Acceptance" className="trace-c-acc">
        {acceptance.length ? (
          <ul className="acceptance">
            {acceptance.map((line, index) => <li key={index}>{line}</li>)}
          </ul>
        ) : (
          <span className="faint">none</span>
        )}
      </td>
      <td role="cell" data-label="Tasks" className="trace-c-work rail-trace-c-work">
        {tasks.length ? (
          <div className="rail-trace-work-scroll" role="region" aria-label={`${tasks.length} tasks for ${text(it.id)}`} tabIndex={0} onFocus={event=>{if(event.target===event.currentTarget) event.currentTarget.scrollIntoView({block:'nearest',inline:'nearest'});}}>
            <ul className="ov-work" aria-label={`Tasks for ${text(it.id)}`}>
              {tasks.map((task) => <li key={text(task.id)}><TaskRow task={task} openDoc={openDoc} navigate={navigate} /></li>)}
            </ul>
          </div>
        ) : (
          <span className="faint">no task</span>
        )}
      </td>
      <td role="cell" data-label="Checks" className="trace-c-checks">
        {checks.length ? (
          <div className="checks">
            {checks.map((check, index) => <CheckLine key={index} check={check} />)}
          </div>
        ) : (
          <span className="faint">no check</span>
        )}
      </td>
      <td role="cell" data-label="Proven" className="trace-c-proof">{proven !== null ? <Badge tone={tone}>{`${PROVEN} ${proven}%`}</Badge> : <Badge tone="neutral">{NOT_ASSESSED}</Badge>}</td>
    </tr>
  );
}

function TraceGroup({ group, items, focused, openDoc, navigate }: { group: Data; items: Data[]; focused: string; openDoc: (id: string) => void; navigate: PageProps['navigate'] }) {
  const gaps = rows(group.gaps);
  const targets = stringList(group.targets);
  return (
    <>
      <tr role="row" className="trace-goal">
        <th role="columnheader" colSpan={7} scope="colgroup">
          <Target size={14} aria-hidden="true" />
          <span className="trace-goal-t">{text(group.sub_goal, 'Unnamed goal')}</span>
          <SrcRef source={group.source} label="Goal" openDoc={openDoc} />
          {targets.length ? (
            <span className="trace-targets">
              {targets.map((target, index) => <Badge key={`${target}-${index}`} tone="neutral">{target}</Badge>)}
            </span>
          ) : null}
        </th>
      </tr>
      {items.length ? items.map((item) => <TraceItemRow key={text(item.id)} item={item} focused={focused} openDoc={openDoc} navigate={navigate} />) : (
        <tr className="trace-empty">
          <td colSpan={7} className="faint">No scope item is mapped to this goal.</td>
        </tr>
      )}
      {gaps.length ? (
        <tr className="trace-gaps-row">
          <td colSpan={7}>
            <div className="ov-card-gaps">
              {gaps.map((gap, index) => <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} />)}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function TraceabilityPage({ project, params, read, openDoc, navigate, busy }: PageProps) {
  const query = useQuery({ queryKey: readKey(project, 'understanding'), queryFn: ({ signal }) => read('understanding', {}, signal), retry: false, enabled: !busy });
  const head = (
    <PageHeading
      eyebrow="Understand"
      title="Traceability"
      description="Goal to scope item to acceptance, tasks and checks - and every gap in the chain."
      action={
        <>
          <Button onClick={() => navigate('overview')}>
            <ArrowLeft size={14} aria-hidden="true" /> Overview
          </Button>
          <Button onClick={() => navigate('outcomes')}>
            <Target size={14} aria-hidden="true" /> Outcomes
          </Button>
        </>
      }
    />
  );
  if (query.isPending) return <div>{head}<Loading /></div>;
  if (query.isError) return <div>{head}<ErrorState error={query.error.message} retry={() => void query.refetch()} /></div>;
  const m = matrixOf(query.data);
  const focus = text(params.get('item'));
  const allGroups = m.rows.map((row) => ({ row, items: rows(row.items) }));
  const hasFocus = !!focus && allGroups.some((group) => group.items.some((item) => text(item.id) === focus));
  const groups = allGroups.map((group) => ({ row: group.row, items: hasFocus ? group.items.filter((item) => text(item.id) === focus) : group.items })).filter((group) => group.items.length);
  /* Unique scope ids, never a sum of mapping occurrences (a shared item counts once). */
  const ids = new Set<string>();
  let occurrences = 0;
  for (const group of allGroups) for (const item of group.items) if (text(item.id)) { ids.add(text(item.id)); occurrences += 1; }
  const uniqueItems = ids.size;
  const invalid = !!focus && !hasFocus;
  return (
    <div>
      {head}
      <div className="trace-sum">
        <span className="stat">
          <span className="k"><Target size={13} aria-hidden="true" />Goals</span>
          <span className="v">{m.rows.length}</span>
        </span>
        <span className="stat">
          <span className="k"><ListChecks size={13} aria-hidden="true" />Items</span>
          <span className="v">{uniqueItems}</span>
        </span>
        <span className="stat">
          <span className="k"><CircleDashed size={13} aria-hidden="true" />Unlinked</span>
          <span className="v">{m.unlinked.length}</span>
        </span>
        <span className="stat">
          <span className="k"><TriangleAlert size={13} aria-hidden="true" />Gaps</span>
          <span className="v">{m.gaps.length}</span>
        </span>
      </div>
      {occurrences > uniqueItems ? (
        <p className="faint ov-note">{`${plural(uniqueItems, 'unique scope item')}, ${plural(occurrences, 'goal mapping')} - a shared item is counted once above.`}</p>
      ) : null}
      {invalid ? <p className="faint" role="status">{`No scope item has the id ${focus}; showing the whole matrix.`}</p> : null}
      {hasFocus ? (
        <p className="faint" role="status">
          {`Filtering to ${focus}.`}{' '}
          <Button variant="ghost" className="btn-sm" onClick={() => navigate('traceability')}>
            Clear filter
          </Button>
        </p>
      ) : null}
      <section className="panel" data-ring="matrix">
        <div className="panel-h"><GitBranch size={15} aria-hidden="true" /><h2>Matrix</h2></div>
        <div className="tbl-wrap">
          <table role="table" aria-label="Goal to scope traceability" className="tbl trace-tbl rail-trace-table">
            <thead role="rowgroup">
              <tr role="row">
                <th role="columnheader" scope="col">Scope item</th>
                <th role="columnheader" scope="col">Title</th>
                <th role="columnheader" scope="col">Priority</th>
                <th role="columnheader" scope="col">Acceptance</th>
                <th role="columnheader" scope="col">Tasks</th>
                <th role="columnheader" scope="col">Checks</th>
                <th role="columnheader" scope="col">Proven</th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {groups.length ? groups.map((group) => <TraceGroup key={text(group.row.sub_goal)} group={group.row} items={group.items} focused={focus} openDoc={openDoc} navigate={navigate} />) : (
                <tr>
                  <td colSpan={7} className="faint">No goal and scope item to show.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel" data-ring="unlinked">
        <div className="panel-h"><CircleDashed size={15} aria-hidden="true" /><h2>Unlinked scope items</h2><div className="acts"><span className="faint">{m.unlinked.length}</span></div></div>
        {m.unlinked.length ? (
          <div className="ov-card-items">
            {m.unlinked.map((item) => {
              const it = object(item);
              return (
                <div key={text(it.id)} className="ov-item" data-item-id={text(it.id)}>
                  <div className="ov-item-head">
                    <Target size={14} aria-hidden="true" />
                    <button type="button" className="link rail-link" onClick={() => navigate('traceability', { item: text(it.id) })}>
                      {text(it.id)}
                    </button>
                    <span className="grow trunc">{text(it.title)}</span>
                    <PriorityPill priority={text(it.priority)} />
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="panel-b faint">Every non-Won't scope item is named by a goal.</div>
        )}
      </section>
      <section className="panel" data-ring="gaps">
        <div className="panel-h"><TriangleAlert size={15} aria-hidden="true" /><h2>Gaps</h2><div className="acts"><span className="faint">{m.gaps.length}</span></div></div>
        {m.gaps.length ? (
          <div className="ov-card-gaps">
            {m.gaps.map((gap, index) => <GapRow key={`${text(gap.kind)}-${text(gap.id)}-${index}`} gap={gap} openDoc={openDoc} />)}
          </div>
        ) : (
          <div className="panel-b faint">No gap in the chain.</div>
        )}
      </section>
    </div>
  );
}

/* =====================================================================
   IMPACT - what a change mechanically reaches
   ===================================================================== */
/* The reader's own argument rule, ported from the legacy loadImpact: the file
   list is split on commas or spaces and rejoined with commas; an over-long or
   control-character argument is refused instead of being sent. */
export function splitFiles(input: string): string {
  return input.trim().split(/[,\s]+/).filter(Boolean).join(',');
}
export function impactArgsValid(files: string, since: string): boolean {
  return files.length <= 8000 && since.length <= 200 && !/[\p{Cc}]/u.test(files + since);
}

function FileChip({ path }: { path: string }) {
  return (
    <span className="file-chip mono rail-file-chip rail-code" title={path}>
      {path}
    </span>
  );
}
/* A reached scope item opens the traceability matrix filtered to it (legacy openScope).
   An impact hit has no source reference, so no document link is lost by dropping openDoc.
   The outcome the item serves is named when a goal names it - a reach is a mechanical
   match, and the outcome is reached through the item, never a causal claim. */
function ScopeHit({ hit, navigate, outcomes }: { hit: unknown; navigate: PageProps['navigate']; outcomes: Outcome[] }) {
  const it = object(hit);
  const via = stringList(it.via);
  const rerun = stringList(it.rerun);
  return (
    <div className="impact-hit rail-impact-hit" data-item-id={text(it.id)}>
      <div className="impact-hit-head rail-impact-hit-head">
        <Target size={14} aria-hidden="true" />
        <button type="button" className="link rail-link" onClick={() => navigate('traceability', { item: text(it.id) })}>
          {text(it.id)}
        </button>
        <span className="grow trunc">{text(it.title)}</span>
        {text(it.priority) ? <Badge tone={PRIORITY_TONE[text(it.priority)] ?? 'neutral'}>{text(it.priority)}</Badge> : null}
      </div>
      {outcomes.length ? (
        <div className="impact-outcomes rail-impact-outcomes">
          <span className="faint rail-muted">serves</span>
          {outcomes.map((outcome) => (
            <button key={outcome.id} type="button" className="chip ov-outcome-link" title={outcome.text} onClick={() => navigate('outcomes', { goal: outcome.id })}>
              <Target size={12} aria-hidden="true" />
              <span className="trunc rail-ov-outcome-text">{outcomeLabel(outcome)}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="impact-outcomes rail-impact-outcomes">
          <span className="faint rail-muted">serves</span>
          <Badge tone="warning">no outcome names this item</Badge>
        </div>
      )}
      {via.length ? (
        <div className="impact-via rail-impact-via">
          <span className="faint rail-muted">reached via</span>
          {via.map((entry, index) => <Badge key={`${entry}-${index}`} tone="neutral">{entry}</Badge>)}
        </div>
      ) : null}
      {rerun.length ? (
        <div className="impact-rerun rail-impact-rerun">
          <span className="faint rail-muted">re-run</span>
          {rerun.map((command, index) => <code key={`${command}-${index}`}>{command}</code>)}
        </div>
      ) : null}
    </div>
  );
}
function WontHit({ hit, navigate }: { hit: unknown; navigate: PageProps['navigate'] }) {
  const it = object(hit);
  return (
    <div className="impact-hit impact-wont rail-impact-hit rail-impact-wont" data-item-id={text(it.id)}>
      <div className="impact-hit-head rail-impact-hit-head">
        <Ban size={14} aria-hidden="true" />
        <button type="button" className="link rail-link" onClick={() => navigate('traceability', { item: text(it.id) })}>
          {text(it.id)}
        </button>
        <span className="grow trunc">{text(it.title)}</span>
        <Badge tone="neutral">wont</Badge>
      </div>
      {text(it.wont_reason) ? <div className="impact-why rail-impact-why">{text(it.wont_reason)}</div> : null}
    </div>
  );
}
function DecisionHit({ hit, openDoc }: { hit: unknown; openDoc: (id: string) => void }) {
  const d = object(hit);
  const files = stringList(d.files);
  return (
    <div className="impact-hit rail-impact-hit" data-decision-id={text(d.id)}>
      <div className="impact-hit-head rail-impact-hit-head">
        <GitBranch size={14} aria-hidden="true" />
        <button type="button" className="link rail-link" onClick={() => openDoc(text(d.id))}>
          {text(d.id)}
        </button>
        <span className="grow trunc">{text(d.title)}</span>
      </div>
      {text(d.choice) ? <div className="impact-why rail-impact-why">{text(d.choice)}</div> : null}
      {files.length ? <div className="impact-files rail-impact-files">{files.map((path, index) => <FileChip key={`${path}-${index}`} path={path} />)}</div> : null}
    </div>
  );
}
/* A task hit opens the task's context lens (legacy openLens), like the task rows above. */
function TaskHit({ hit, navigate }: { hit: unknown; navigate: PageProps['navigate'] }) {
  const t = object(hit);
  const files = stringList(t.files);
  return (
    <div className="impact-hit rail-impact-hit" data-task-id={text(t.id)}>
      <div className="impact-hit-head rail-impact-hit-head">
        <CircleDot size={14} aria-hidden="true" />
        <button type="button" className="link rail-link" onClick={() => navigate('lens', { task: text(t.id) })}>
          {text(t.id)}
        </button>
        <span className="grow trunc">{text(t.title)}</span>
      </div>
      {files.length ? <div className="impact-files rail-impact-files">{files.map((path, index) => <FileChip key={`${path}-${index}`} path={path} />)}</div> : null}
    </div>
  );
}
function StaleProof({ proof, navigate }: { proof: unknown; navigate: PageProps['navigate'] }) {
  const s = object(proof);
  const changed = stringList(s.changed);
  const via = text(s.via);
  const tone: Tone = via === 'digest' ? 'danger' : 'warning';
  return (
    <div className="impact-stale rail-impact-stale" data-item-id={text(s.item)} data-via={via}>
      <div className="impact-stale-head rail-impact-stale-head">
        <ShieldAlert size={14} aria-hidden="true" />
        <button type="button" className="link rail-link" onClick={() => navigate('traceability', { item: text(s.item) })}>
          {text(s.item)}
        </button>
        <span className="grow trunc">{text(s.text)}</span>
        <Badge tone={tone}>{`stale: ${via || 'unknown'}`}</Badge>
      </div>
      {text(s.command) ? <code className="impact-cmd rail-impact-cmd">{text(s.command)}</code> : null}
      {text(s.recorded) ? <span className="faint impact-recorded rail-muted rail-impact-recorded">{`recorded ${text(s.recorded)}`}</span> : null}
      {changed.length ? <div className="impact-files rail-impact-files">{changed.map((path, index) => <FileChip key={`${path}-${index}`} path={path} />)}</div> : null}
    </div>
  );
}

export function ImpactPage({ project, params, read, openDoc, navigate, busy }: PageProps) {
  const urlFiles = text(params.get('files'));
  const urlSince = text(params.get('since'));
  const [draftFiles, setDraftFiles] = useState(urlFiles);
  const [draftSince, setDraftSince] = useState(urlSince);
  const [formError, setFormError] = useState('');
  /* Keep the inputs in step with the address (a link, Back/forward) without ever
     remounting them: the element identity is stable, so an IME composition in
     progress is not interrupted by a re-render. */
  useEffect(() => {
    setDraftFiles(urlFiles);
    setDraftSince(urlSince);
  }, [urlFiles, urlSince]);
  const query = useQuery({ queryKey: readKey(project, 'impact', urlFiles, urlSince), queryFn: ({ signal }) => read('impact', { files: urlFiles, since: urlSince }, signal), retry: false, enabled: !busy });
  /* The same understanding read the Overview and the drawer use (one cache
     entry): it names the outcome a scope item serves, so the change names
     the outcomes it reaches instead of only the items. */
  const understanding = useQuery({ queryKey: readKey(project, 'understanding'), queryFn: ({ signal }) => read('understanding', {}, signal), retry: false, enabled: !busy });
  const outcomesByItem = useMemo(() => outcomeByItem(understanding.data), [understanding.data]);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const files = splitFiles(draftFiles);
    const since = draftSince.trim();
    if (!impactArgsValid(files, since)) {
      setFormError('That request is too long or carries control characters. Narrow the file list or the ref.');
      return;
    }
    setFormError('');
    navigate('impact', { files, since });
  };
  const head = (
    <PageHeading
      eyebrow="Changes"
      title="Impact"
      description="What a change reaches: the scope it touches, the proofs it makes stale, and the files the store does not know."
      action={
        <Button onClick={() => navigate('overview')}>
          <ArrowLeft size={14} aria-hidden="true" /> Overview
        </Button>
      }
    />
  );
  const form = (
    <form className="impact-form rail-impact-form" aria-label="Read the impact of a change" onSubmit={submit}>
      <div className="impact-fields rail-impact-fields">
        <div className="impact-field rail-impact-field">
          <label htmlFor="impact-files">Changed files</label>
          <input
            id="impact-files"
            name="files"
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="src/context_rail/cli.py, web/src/pages/core.js"
            value={draftFiles}
            onChange={(event) => setDraftFiles(event.target.value)}
          />
        </div>
        <div className="impact-field rail-impact-field">
          <label htmlFor="impact-since">Since (ref or branch)</label>
          <input id="impact-since" name="since" type="text" autoComplete="off" spellCheck={false} placeholder="main" value={draftSince} onChange={(event) => setDraftSince(event.target.value)} />
        </div>
        <Button type="submit" variant="primary">
          <Search size={14} aria-hidden="true" /> Read impact
        </Button>
      </div>
      <p className="faint impact-hint rail-muted rail-impact-hint">Leave both blank to read the working tree from git. The file list is split on commas or spaces.</p>
      {formError ? <p role="alert" className="impact-hint rail-impact-hint">{formError}</p> : null}
    </form>
  );
  const body = () => {
    /* The legacy impact states (pages/impact.js): loading, a refused read and a bare
       `{error}` all render as the legacy empty block. The retry affordance for a failed
       read is the shell's offline bar (legacy shell/render.js retryRead), never a control
       inside the page, so the pixel contract with legacy stays exact. */
    if (query.isPending) return <LegacyEmpty icon={<Loader size={20} aria-hidden="true" />} title="Loading" text="Reading the change impact..." />;
    if (query.isError) return <LegacyEmpty icon={<TriangleAlert size={20} aria-hidden="true" />} title="The impact could not be read" text={query.error.message} />;
    const d = object(query.data);
    /* The reader returns a bare `{error}` with no `status` when git cannot list the
       changes; that is a failure, not an empty change. */
    if (text(d.error) || (text(d.status) && text(d.status) !== 'ok')) {
      const failed = !!text(d.error);
      const title = failed ? 'The impact could not be read' : 'No impact to show';
      return <LegacyEmpty icon={failed ? <TriangleAlert size={20} aria-hidden="true" /> : <CircleHelp size={20} aria-hidden="true" />} title={title} text={text(d.error, text(d.status))} />;
    }
    const files = stringList(d.files);
    const items = rows(d.scope_items);
    const wont = rows(d.wont);
    const decisions = rows(d.decisions);
    const tasks = rows(d.tasks);
    const unmapped = stringList(d.unmapped);
    const stale = rows(d.stale_proofs);
    /* The outcomes the change reaches, through the scope items they name. An
       item no outcome names is named as such: the mapping is unknown, never
       a claim that the change affects nothing. */
    const affected = new Map<string, { outcome: Outcome; items: string[] }>();
    let unnamed = 0;
    for (const hit of items) {
      const id = text(object(hit).id);
      const owners = outcomesByItem.get(id) ?? [];
      if (!owners.length) unnamed += 1;
      for (const outcome of owners) {
        const entry = affected.get(outcome.id) ?? { outcome, items: [] };
        if (!entry.items.includes(id)) entry.items.push(id);
        affected.set(outcome.id, entry);
      }
    }
    const affectedOutcomes = [...affected.values()].sort((a, b) => b.items.length - a.items.length || a.outcome.id.localeCompare(b.outcome.id));
    const nothing = !files.length && !items.length && !wont.length && !decisions.length && !tasks.length && !stale.length && !unmapped.length;
    if (nothing) return <p className="faint rail-muted" role="status">{text(d.note, 'No change reaches the store.')}</p>;
    return (
      <>
        <section className="panel" data-ring="files"><div className="panel-h"><FileGlyph size={15} aria-hidden="true"/><h2>Changed files</h2><div className="acts"><span className="faint">{files.length}</span></div></div>
          {files.length ? <div className="impact-files rail-impact-files">{files.map((path, index) => <FileChip key={`${path}-${index}`} path={path} />)}</div> : <div className="panel-b faint">No file was given; the change was read from git.</div>}
        </section>
        <section className="panel" data-ring="scope"><div className="panel-h"><Target size={15} aria-hidden="true"/><h2>Reached scope</h2><div className="acts"><span className="faint">{items.length}</span></div></div>
          {items.length ? <div className="impact-list rail-impact-list">{items.map((hit, index) => <ScopeHit key={`${text(hit.id)}-${index}`} hit={hit} navigate={navigate} outcomes={outcomesByItem.get(text(object(hit).id)) ?? []} />)}</div> : <div className="panel-b faint">No scope item is reached by these files.</div>}
          <p className="faint ov-note rail-muted rail-ov-note">A reach is a mechanical match on a link or a check's files. It does not claim the change enabled or blocked the item.</p>
        </section>
        <section className="panel" data-ring="outcomes"><div className="panel-h"><Target size={15} aria-hidden="true"/><h2>Affected outcomes</h2><div className="acts"><span className="faint">{affectedOutcomes.length}</span></div></div>
          {affectedOutcomes.length ? (
            <div className="impact-list rail-impact-list">
              {affectedOutcomes.map((entry) => (
                <div className="impact-hit rail-impact-hit" data-goal-id={entry.outcome.id} key={entry.outcome.id}>
                  <div className="impact-hit-head rail-impact-hit-head">
                    <Target size={14} aria-hidden="true" />
                    <button type="button" className="link rail-link" title={entry.outcome.text} onClick={() => navigate('outcomes', { goal: entry.outcome.id })}>
                      {outcomeLabel(entry.outcome)}
                    </button>
                    <Badge tone="neutral">{plural(entry.items.length, 'scope item')}</Badge>
                  </div>
                  <div className="impact-via rail-impact-via">
                    <span className="faint rail-muted">through</span>
                    {entry.items.map((id) => (
                      <button key={id} type="button" className="link rail-link" onClick={() => navigate('traceability', { item: id })}>{id}</button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="panel-b faint">No outcome names the scope this change reaches.</div>
          )}
          {unnamed ? <p className="faint ov-note rail-muted rail-ov-note">{unnamed === 1 ? 'One reached scope item is' : `${unnamed} reached scope items are`} named by no outcome - the mapping is unknown, not absent.</p> : null}
          <p className="faint ov-note rail-muted rail-ov-note">An outcome is affected through the scope items it names. The reach is mechanical: it says which outcome the change touches, not what the change did to it.</p>
        </section>
        <section className="panel" data-ring="stale"><div className="panel-h"><ShieldAlert size={15} aria-hidden="true"/><h2>Stale proofs</h2><div className="acts"><span className="faint">{stale.length}</span></div></div>
          {stale.length ? <div className="impact-list rail-impact-list">{stale.map((proof, index) => <StaleProof key={`${text(proof.item)}-${index}`} proof={proof} navigate={navigate} />)}</div> : <div className="panel-b faint">No recorded proof this change makes stale.</div>}
        </section>
        <section className="panel" data-ring="decisions"><div className="panel-h"><GitBranch size={15} aria-hidden="true"/><h2>Decisions</h2><div className="acts"><span className="faint">{decisions.length}</span></div></div>
          {decisions.length ? <div className="impact-list rail-impact-list">{decisions.map((hit, index) => <DecisionHit key={`${text(hit.id)}-${index}`} hit={hit} openDoc={openDoc} />)}</div> : <div className="panel-b faint">No active decision names these files.</div>}
        </section>
        <section className="panel" data-ring="tasks"><div className="panel-h"><CircleCheck size={15} aria-hidden="true"/><h2>Tasks</h2><div className="acts"><span className="faint">{tasks.length}</span></div></div>
          {tasks.length ? <div className="impact-list rail-impact-list">{tasks.map((hit, index) => <TaskHit key={`${text(hit.id)}-${index}`} hit={hit} navigate={navigate} />)}</div> : <div className="panel-b faint">No task's files are touched.</div>}
        </section>
        {wont.length ? (
          <section className="panel" data-ring="wont"><div className="panel-h"><Ban size={15} aria-hidden="true"/><h2>Won't boundaries</h2><div className="acts"><span className="faint">{wont.length}</span></div></div>
            <div className="impact-list rail-impact-list">{wont.map((hit, index) => <WontHit key={`${text(hit.id)}-${index}`} hit={hit} navigate={navigate} />)}</div>
          </section>
        ) : null}
        <section className="panel" data-ring="unmapped"><div className="panel-h"><CircleDashed size={15} aria-hidden="true"/><h2>Unmapped files</h2><div className="acts"><span className="faint">{unmapped.length}</span></div></div>
          {unmapped.length ? (
            <>
              <div className="impact-files rail-impact-files">{unmapped.map((path, index) => <FileChip key={`${path}-${index}`} path={path} />)}</div>
              <p className="faint ov-note rail-muted rail-ov-note">No task or check points at these files. Link one: ctx-rail task-update &lt;task&gt; --files &lt;path&gt;</p>
            </>
          ) : (
            <div className="panel-b faint">Every changed file is known to the store.</div>
          )}
        </section>
      </>
    );
  };
  return (
    <div>
      {head}
      {form}
      {body()}
    </div>
  );
}

/* =====================================================================
   CATCH UP (Activity) - grouped by the CURRENT outcome map
   ===================================================================== */
/* The honest caveat the grouping must carry, word for word. */
export const CATCHUP_CAVEAT = 'Grouped by current outcome mapping, not historical causality';
/* How many more events "Show more" asks for. The read keeps its newest-N shape; the hook raises N. */
export const LIMIT_STEP = 50;
/* The activity read's "everything" cursor: a v2 log has no earlier event. */
export const ALL_SINCE = '1970-01-01T00:00:00Z';

/* The seven deterministic categories. Anything the store does not explicitly say is `other`. */
export const CATEGORY_ORDER = ['completed', 'started', 'blocked', 'proof', 'decision', 'scope', 'other'] as const;
export type Category = (typeof CATEGORY_ORDER)[number];
export const CATEGORY_LABEL: Record<Category, string> = {
  completed: 'Completed',
  started: 'Started',
  blocked: 'Blocked',
  proof: 'Proof evidence',
  decision: 'Decision',
  scope: 'Scope',
  other: 'Other',
};

/* ---------- mapping pure helpers (exported for the regression tests) ---------- */
/* `task:<slug>` / `scope_item:<id>` / `project:status` / a bare token -> { kind, id }. */
export function subjectTarget(subject: unknown): { kind: string; id: string } {
  const s = text(subject);
  const i = s.indexOf(':');
  if (i < 0) return { kind: s, id: '' };
  return { kind: s.slice(0, i), id: s.slice(i + 1) };
}
function idSet(value: unknown): Set<string> {
  const out = new Set<string>();
  for (const row of rows(value)) {
    const id = text(row.id) || text(row.slug);
    if (id) out.add(id);
  }
  return out;
}
/* The ids the CURRENT model actually ties to this event, from its subject and its
   own item data. A subject that matches nothing maps to no goal; nothing is
   guessed, and a mapping is not causality. */
export function eventGoalIds(event: unknown, understanding: unknown): string[] {
  const e = object(event);
  const goals = rows(object(understanding).goals);
  if (!goals.length) return [];
  const t = subjectTarget(e.subject);
  const candidates: [string, string][] = [];
  if (t.kind === 'task' && t.id) candidates.push(['task', t.id]);
  if ((t.kind === 'scope_item' || t.kind === 'scope-item') && t.id) candidates.push(['item', t.id]);
  const data = e.data && typeof e.data === 'object' && !Array.isArray(e.data) ? (e.data as Data) : null;
  const item = data ? text(data.item) || text(data.item_id) || text(data.check_item) : '';
  if (item) candidates.push(['item', item]);
  if (!candidates.length) return [];
  const out: string[] = [];
  for (const goal of goals) {
    const id = text(goal.id);
    if (!id) continue;
    const tasks = idSet(goal.tasks);
    const items = idSet(goal.items);
    if (candidates.some(([kind, value]) => (kind === 'task' ? tasks.has(value) : items.has(value)))) out.push(id);
  }
  return out;
}
/* The resulting task state, from the raw `data.to` when present, else the `from -> to` summary.
   Anything that is not one of the three known states stays unknown (the caller labels it `other`). */
export function taskState(event: unknown): string {
  const e = object(event);
  const data = object(e.data);
  const to = text(data.to).trim();
  if (to) return to;
  const m = /->\s*(\S+)\s*$/.exec(text(e.summary));
  return m ? m[1] : '';
}
/* One deterministic category per event, from explicit kind/subject/data only. */
export function categorize(event: unknown): Category {
  const e = object(event);
  const kind = text(e.kind);
  if (kind === 'task.status') {
    const to = taskState(e);
    if (to === 'done') return 'completed';
    if (to === 'in-progress' || to === 'in_progress') return 'started';
    if (to === 'blocked') return 'blocked';
    return 'other';
  }
  if (kind === 'check.tick' || kind === 'check.clear' || kind === 'run.verify') return 'proof';
  const target = subjectTarget(e.subject);
  if (kind.startsWith('decision.') || target.kind === 'decision') return 'decision';
  if (kind.startsWith('scope.') || kind.startsWith('scope_item.') || target.kind === 'scope' || target.kind === 'scope_item') return 'scope';
  return 'other';
}
/* A short plain-text statement for one event in its category. Proof wording never
   claims a pass from a run whose exit is missing or non-zero, and never says "proven". */
export function eventBrief(event: unknown, category: Category): string {
  const e = object(event);
  const data = object(e.data);
  if (category === 'completed') return 'moved to done';
  if (category === 'started') return 'moved to in-progress';
  if (category === 'blocked') return 'moved to blocked';
  if (category === 'proof') {
    const kind = text(e.kind);
    if (kind === 'run.verify') {
      const exit = data.exit;
      if (exit === 0) return 'verify passed (exit 0)';
      if (typeof exit === 'number' && Number.isFinite(exit)) return `verify exit ${Math.round(exit)} - not a pass`;
      return 'verify recorded, no exit - not a pass';
    }
    const what = [text(data.item), text(data.text)].filter(Boolean).join(' ');
    return `${kind === 'check.clear' ? 'cleared ' : 'ticked '}${what || 'a check'}`;
  }
  const id = subjectTarget(e.subject).id;
  if (category === 'decision') return `decision ${id || text(e.kind)}`;
  if (category === 'scope') return `scope ${id || text(e.kind)}`;
  return text(e.kind, 'unknown');
}
export interface CategorySummary {
  id: Category;
  label: string;
  count: number;
  briefs: string[];
  /* The record each brief is about (a task or scope id), parallel to briefs - so "moved to done" says what moved. */
  subjects: string[];
}
/* Counts and brief statements per category, in a fixed order; empty categories are dropped. */
export function summarizeCategories(events: unknown): CategorySummary[] {
  const buckets = new Map<Category, { brief: string; subject: string }[]>(CATEGORY_ORDER.map((category) => [category, []]));
  for (const event of rows(events)) {
    const category = categorize(event);
    (buckets.get(category) ?? buckets.get('other')!).push({ brief: eventBrief(event, category), subject: subjectTarget(event.subject).id });
  }
  return CATEGORY_ORDER.map((id) => {
    const list = buckets.get(id) ?? [];
    return { id, label: CATEGORY_LABEL[id], count: list.length, briefs: list.map((entry) => entry.brief), subjects: list.map((entry) => entry.subject) };
  }).filter((row) => row.count > 0);
}
/* The real source doc ids a goal (or the unmapped items) currently names: goal doc, item ids, task ids. */
function goalSources(goal: unknown): string[] {
  const g = object(goal);
  const out: string[] = [];
  if (text(g.source && object(g.source).id)) out.push(text(object(g.source).id));
  for (const item of rows(g.items)) if (text(item.id)) out.push(text(item.id));
  for (const task of rows(g.tasks)) if (text(task.id)) out.push(text(task.id));
  return [...new Set(out)];
}
export interface CatchupGroup {
  id: string;
  text: string;
  goal: Data;
  events: { event: Data; goals: string[] }[];
  categories: CategorySummary[];
  sources: string[];
  gaps: Data[];
}
export interface Catchup {
  model: boolean;
  groups: CatchupGroup[];
  unmapped: Data[];
  unmappedCategories: CategorySummary[];
  unmappedSources: string[];
  unmappedGaps: Data[];
  shared: number;
  distinct: number;
}
/* Bucket events by CURRENT goal membership, with their category summary, current
   sources and the CURRENT proof gaps the model records. An event the model ties to
   several outcomes appears under each (labeled); the overall distinct count is
   never inflated by that overlap. */
export function groupByOutcome(events: unknown, understanding: unknown): Catchup {
  const list = rows(events);
  const goals = rows(object(understanding).goals);
  const buckets = new Map<string, { event: Data; goals: string[] }[]>();
  for (const goal of goals) if (text(goal.id)) buckets.set(text(goal.id), []);
  const unmapped: Data[] = [];
  let shared = 0;
  for (const event of list) {
    const ids = eventGoalIds(event, understanding);
    if (!ids.length) {
      unmapped.push(event);
      continue;
    }
    if (ids.length > 1) shared += 1;
    for (const id of ids) buckets.get(id)?.push({ event, goals: ids });
  }
  const groups: CatchupGroup[] = goals
    .filter((goal) => text(goal.id) && (buckets.get(text(goal.id)) ?? []).length)
    .map((goal) => ({
      id: text(goal.id),
      text: text(goal.text, text(goal.id)),
      goal,
      events: buckets.get(text(goal.id)) ?? [],
      categories: summarizeCategories((buckets.get(text(goal.id)) ?? []).map((entry) => entry.event)),
      sources: goalSources(goal),
      gaps: rows(goal.gaps),
    }));
  const block = object(object(understanding).unmapped);
  return {
    model: goals.length > 0,
    groups,
    unmapped,
    unmappedCategories: summarizeCategories(unmapped),
    unmappedSources: goalSources({ items: block.items }),
    unmappedGaps: rows(block.gaps),
    shared,
    distinct: list.length,
  };
}
/* The exact seen boundary this render displayed: a ULID for a v2 log, an ISO time
   for a v1 status_log. Anything else is refused rather than guessed, so a
   malformed or absent boundary can never move the marker. */
export const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
export const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
export function seenBoundary(boundary: unknown): { through_id: string } | { through_at: string } | null {
  const b = object(boundary);
  const id = text(b.id);
  const at = text(b.at);
  if (id) return ULID_RE.test(id) ? { through_id: id } : null;
  if (at && ISO_RE.test(at) && Number.isFinite(Date.parse(at))) return { through_at: at };
  return null;
}
/* The debt trend as an inline SVG polyline (no chart library, no canvas). Points are clamped numbers. */
export function sparkPoints(trend: unknown): { points: string; width: number; height: number } | null {
  const values = rows(trend).map((row) => (typeof row.debt === 'number' && Number.isFinite(row.debt) ? row.debt : 0));
  if (values.length < 2) return null;
  const max = Math.max(...values, 1);
  const width = 220;
  const height = 40;
  const step = width / (values.length - 1);
  const points = values.map((value, index) => `${(index * step).toFixed(1)},${(height - 4 - (value / max) * (height - 8)).toFixed(1)}`).join(' ');
  return { points, width, height };
}

/* Two legacy ic() glyphs the shared ./icons module does not export (User, Send).
   Same Lucide node data and the same svg shape legacy ic() emits, so an actor row and a
   run.dispatch event row keep the legacy glyph instead of a substituted one. */
function LegacyGlyph({ name, size = 14, children }: { name: string; size?: number; children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={`i lucide lucide-${name}`}>
      {children}
    </svg>
  );
}
function UserGlyph({ size = 14 }: { size?: number }) {
  return (
    <LegacyGlyph name="user" size={size}>
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </LegacyGlyph>
  );
}
function SendGlyph({ size = 14 }: { size?: number }) {
  return (
    <LegacyGlyph name="send" size={size}>
      <path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" />
      <path d="m21.854 2.147-10.94 10.939" />
    </LegacyGlyph>
  );
}
/* Legacy activity.js KIND_ICON: the glyph an event kind carries; an unknown kind keeps the
   plain circle, exactly as `KIND_ICON[kind] || 'circle'` did. */
function EventGlyph({ kind }: { kind: string }) {
  if (kind === 'task.claim') return <Lock size={14} aria-hidden="true" />;
  if (kind === 'task.status') return <CircleDot size={14} aria-hidden="true" />;
  if (kind === 'run.verify') return <ShieldCheck size={14} aria-hidden="true" />;
  if (kind === 'run.dispatch') return <SendGlyph size={14} />;
  if (kind === 'run.work_order') return <FileText size={14} aria-hidden="true" />;
  if (kind === 'project.state') return <Flag size={14} aria-hidden="true" />;
  return <Circle size={14} aria-hidden="true" />;
}

/* The legacy category row (activity.js .cat): label, exact count, then the briefs. */
function CategoryList({ categories }: { categories: CategorySummary[] }) {
  return (
    <>
      {categories.map((row) => (
        <div key={row.id} className="cat" data-cat={row.id}>
          <b>{row.label}</b>{' '}<span className="num faint">{row.count}</span>
          {row.briefs.length ? (
            <div className="faint" style={{ fontSize: 12 }}>
              {row.briefs.map((brief, index) => (
                <div key={index}>{row.subjects[index] ? <span className="mono rail-event-subject">{row.subjects[index]}</span> : null}{row.subjects[index] ? ' ' : ''}{brief}</div>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </>
  );
}
/* One event as a native, keyboard-reachable disclosure: the real fields we have. */
function EventRow({ event, labels = [] }: { event: unknown; labels?: string[] }) {
  const e = object(event);
  const kind = text(e.kind);
  return (
    <details className="mini" data-event={text(e.id)}>
      <summary>
        <EventGlyph kind={kind} />{' '}<b>{kind}</b>{' '}<span className="faint">{text(e.subject)}</span>
        {labels.length > 1 ? <span className="faint">{` in ${labels.length} outcomes`}</span> : null}
      </summary>
      <div className="grow" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
        {text(e.summary) ? <div className="faint" style={{ fontSize: 12 }}>{text(e.summary)}</div> : null}
        <div className="faint" style={{ fontSize: 12 }}><b>kind</b> {kind}</div>
        <div className="faint" style={{ fontSize: 12 }}><b>subject</b> {text(e.subject)}</div>
        <div className="faint" style={{ fontSize: 12 }}><b>actor</b> {text(e.actor)}</div>
        <div className="faint" style={{ fontSize: 12 }}><b>at</b> {text(e.at)}</div>
      </div>
    </details>
  );
}
function ActorRow({ actor, info }: { actor: string; info: unknown }) {
  const row = object(info);
  const kinds = Object.entries(object(row.kinds))
    .map(([kind, count]) => `${kind} x${text(count, '0')}`)
    .join(', ');
  return (
    <div className="mini lens-row" style={{ cursor: 'default' }}>
      <UserGlyph size={14} />
      <div className="grow" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
        <b>{actor}</b>
        <div className="faint" style={{ fontSize: 12 }}>{kinds}</div>
      </div>
      <span className="num faint">{text(row.count, '0')}</span>
    </div>
  );
}

/* The CURRENT gaps the model records, rendered in the same row shape as a gap. */
function CatchupGaps({ gaps, openDoc }: { gaps: Data[]; openDoc: (id: string) => void }) {
  return (
    <>
      {gaps.map((gap, index) => (
        <div key={`${text(gap.kind)}-${text(gap.id)}-${index}`} className="gap" data-gap-kind={text(gap.kind, 'gap')}>
          <span className="gap-id">{text(gap.id)}</span>
          {text(gap.why) ? <span className="gap-why">{text(gap.why)}</span> : null}
          {text(object(gap.source).id) ? <DocLink id={text(object(gap.source).id)} label={text(object(gap.source).id)} openDoc={openDoc} /> : null}
        </div>
      ))}
    </>
  );
}

/* The catch-up grouping: one panel per CURRENT outcome (category summary + current
   sources + CURRENT gaps + the expandable events), an explicit Unmapped panel, and
   its own summary. Without a model it degrades to a line; the raw audit list
   always renders below. */
function CatchupSection({ events, model, modelError, navigate, openDoc }: { events: Data[]; model: Data; modelError: string; navigate: PageProps['navigate']; openDoc: (id: string) => void }) {
  const grouped = groupByOutcome(events, model);
  if (!grouped.model) {
    return (
      <p className="faint" style={{ margin: '0 0 12px' }}>
        {`No current outcome map to group against${modelError ? ` (${modelError})` : ''}. The raw audit list is below.`}
      </p>
    );
  }
  const byLabel = new Map(rows(model.goals).map((goal) => [text(goal.id), text(goal.text, text(goal.id))]));
  /* The legacy outcome panel (activity.js section()): category summary, the CURRENT sources,
     the CURRENT gaps and the expandable events, each in its own panel body. */
  return (
    <>
      <p className="faint" style={{ margin: '0 0 12px' }}>{`${CATCHUP_CAVEAT}.`}</p>
      <p className="faint" style={{ margin: '0 0 12px' }}>
        {`${plural(grouped.distinct, 'event')} in this view${grouped.shared ? `; ${grouped.shared} map to more than one current outcome and appear under each, so group counts overlap` : ''}.`}
      </p>
      {grouped.groups.map((group) => (
        <section key={group.id} className="panel" data-ring="outcome" data-goal={group.id}>
          <div className="panel-h">
            <Target size={15} aria-hidden="true" />
            <h2>{group.text}</h2>
            <div className="acts">
              <span className="faint num">{group.events.length}</span>
              <button type="button" className="btn btn-sm btn-secondary" onClick={() => navigate('traceability')}>
                <GitBranch size={13} aria-hidden="true" />Matrix
              </button>
            </div>
          </div>
          <div className="panel-b"><CategoryList categories={group.categories} /></div>
          {group.categories.some((row) => row.id === 'proof') ? <p className="faint ov-note" style={{ padding: '0 14px' }}>Proof entries are evidence records, not the proven aggregate.</p> : null}
          <div className="panel-b faint" style={{ fontSize: 12 }}>
            <Badge tone="neutral">CURRENT</Badge>{' '}sources:{' '}
            {group.sources.length ? group.sources.map((id, index) => <DocLink key={`${id}-${index}`} id={id} openDoc={openDoc} />) : <span className="faint">none recorded for this goal</span>}
          </div>
          {group.gaps.length ? (
            <div className="ov-card-gaps" style={{ padding: '4px 14px 10px' }}>
              <Badge tone="warning">CURRENT</Badge>
              <CatchupGaps gaps={group.gaps} openDoc={openDoc} />
            </div>
          ) : null}
          <div className="panel-b">
            {group.events.map((entry, index) => (
              <EventRow key={`${text(entry.event.id)}-${index}`} event={entry.event} labels={entry.goals.map((id) => byLabel.get(id) ?? id)} />
            ))}
          </div>
        </section>
      ))}
      {grouped.unmapped.length ? (
        <section className="panel" data-ring="unmapped">
          <div className="panel-h">
            <CircleDashed size={15} aria-hidden="true" />
            <h2>Unmapped</h2>
            <div className="acts"><span className="faint num">{grouped.unmapped.length}</span></div>
          </div>
          <div className="panel-b"><CategoryList categories={grouped.unmappedCategories} /></div>
          {grouped.unmappedSources.length ? (
            <div className="panel-b faint" style={{ fontSize: 12 }}>
              <Badge tone="neutral">CURRENT</Badge>{' '}unmapped scope: {grouped.unmappedSources.map((id, index) => <DocLink key={`${id}-${index}`} id={id} openDoc={openDoc} />)}
            </div>
          ) : null}
          {grouped.unmappedGaps.length ? (
            <div className="ov-card-gaps" style={{ padding: '4px 14px 10px' }}>
              <Badge tone="warning">CURRENT</Badge>
              <CatchupGaps gaps={grouped.unmappedGaps} openDoc={openDoc} />
            </div>
          ) : null}
          <div className="panel-b">
            {grouped.unmapped.map((event, index) => (
              <EventRow key={`${text(event.id)}-${index}`} event={event} />
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}

export function ActivityPage({ project, params, read, openDoc, navigate, busy, mutate, refresh }: PageProps) {
  const rawLimit = Number.parseInt(text(params.get('limit'), '50'), 10);
  const limit = Number.isFinite(rawLimit) ? Math.min(1000, Math.max(1, rawLimit)) : 50;
  const all = text(params.get('all')) === '1';
  /* A filter step is navigation, not a new view: it carries the open drawer parameter
     forward, like the legacy syncUrl / activityLimit did (and like the Graph F4 repair). */
  const doc = params.get('doc') ?? '';
  const understanding = useQuery({ queryKey: readKey(project, 'understanding'), queryFn: ({ signal }) => read('understanding', {}, signal), retry: false, enabled: !busy });
  const activity = useQuery({
    queryKey: readKey(project, 'activity', limit, all),
    queryFn: ({ signal }) => read('activity', all ? { limit: String(limit), since: ALL_SINCE } : { limit: String(limit) }, signal),
    retry: false,
    enabled: !busy,
  });
  const reload = () => {
    /* One refresh for the whole project: the catch-up and the understanding
       it is grouped by, and the snapshot they sit next to, describe one
       moment. */
    if (refresh) {
      void refresh();
      return;
    }
    void understanding.refetch();
    void activity.refetch();
  };
  const boundary = object(activity.data).boundary;
  /* The one write: ack exactly the newest DISPLAYED event (this render's boundary),
     never the log's newest and never a malformed id. The App reconciles the snapshot
     and refetches this read; a refusal is surfaced by the shell's own alert. */
  const seen = seenBoundary(boundary);
  const markSeen = async () => {
    if (!seen || busy) return;
    try {
      await mutate('activity', { mark_seen: true, ...seen });
    } catch {
      /* The shell reports the refusal; the screen keeps the boundary it displayed. */
    }
  };
  /* The legacy head (activity.js pageActivity): the h1 stays `Activity` while the nav
     label is "Catch up", and the three controls sit directly in .acts. */
  const head = (
    <div className="ph">
      <div><h1>Activity</h1><p>What changed since you last looked, grouped by the current outcome map.</p></div>
      <div className="acts">
        <button type="button" className="btn btn-secondary" onClick={() => navigate('activity', { limit: String(limit), all: all ? '' : '1', doc })}>
          <History size={14} aria-hidden="true" />{all ? 'Only what is new' : 'Show everything'}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={!seen || busy}
          title={seen ? 'Mark seen through the newest displayed event only' : 'Nothing is displayed to mark seen'}
          onClick={() => void markSeen()}
        >
          <Check size={14} aria-hidden="true" />Mark as seen
        </button>
        <button type="button" className="btn btn-secondary" onClick={reload} disabled={activity.isFetching}>
          <RotateCcw size={14} aria-hidden="true" />Reload
        </button>
      </div>
    </div>
  );
  if (activity.isPending) return <div>{head}<LegacyEmpty icon={<Loader size={20} aria-hidden="true" />} title="Loading" text="Reading the activity..." /></div>;
  if (activity.isError) return <div>{head}<LegacyEmpty icon={<TriangleAlert size={20} aria-hidden="true" />} title="Activity could not load" text={activity.error.message} /></div>;
  const a = object(activity.data);
  const events = rows(a.events);
  const actors = Object.entries(object(a.by_actor));
  const trend = rows(a.debt_trend);
  const last = trend.length ? trend[trend.length - 1] : null;
  const spark = sparkPoints(trend);
  const model = understanding.isSuccess ? object(understanding.data) : {};
  const modelError = understanding.isError ? understanding.error.message : '';
  const trendText = trend.length ? 'One recorded run so far.' : 'No recorded runs. ctx-rail doctor --record adds one.';
  return (
    <div>
      {head}
      <p className="faint" style={{ margin: '0 0 12px' }}>
        {a.source === 'log' ? <Badge tone="good">event log</Badge> : <Badge tone="warning">status log (store v1)</Badge>}{' '}
        {text(a.since) ? <span title={text(a.since)}>{`since ${dayOf(text(a.since))}`}</span> : 'from the start'}
        {text(a.seen_at) ? <span title={text(a.seen_at)}>{`, last marked seen ${dayOf(text(a.seen_at))}`}</span> : ''}
        {a.truncated ? `, showing the newest ${events.length} of ${text(a.total, '0')}` : ''}
        {', '}
        {text(object(boundary).id) ? <span title={`Seen marker ${text(object(boundary).id)}`}>marking seen covers the newest event shown</span> : text(object(boundary).at) ? <span title={text(object(boundary).at)}>{`marking seen covers everything through ${dayOf(text(object(boundary).at))}`}</span> : 'nothing shown to mark seen'}
      </p>
      {text(a.note) ? <p className="faint" style={{ margin: '0 0 12px' }}>{text(a.note)}</p> : null}
      <CatchupSection events={events} model={model} modelError={modelError} navigate={navigate} openDoc={openDoc} />
      <div className="lens-grid">
        <section className="panel" data-ring="events">
          <div className="panel-h">
            <Activity size={15} aria-hidden="true" />
            <h2>Raw audit</h2>
            <div className="acts"><span className="faint num">{events.length}</span></div>
          </div>
          {events.length ? (
            [...events].reverse().map((event, index) => <EventRow key={`${text(event.id)}-${index}`} event={event} />)
          ) : (
            <div style={{ padding: '4px 14px 12px' }} className="faint">Nothing new.</div>
          )}
          {a.truncated ? (
            <div style={{ padding: '4px 14px 12px' }}>
              <button type="button" className="btn btn-sm btn-secondary" onClick={() => navigate('activity', { limit: String(Math.min(1000, limit + LIMIT_STEP)), all: all ? '1' : '', doc })}>
                <ChevronDown size={14} aria-hidden="true" />{`Show ${LIMIT_STEP} more`}
              </button>{' '}
              <span className="faint" style={{ fontSize: 11.5 }}>{`newest ${events.length} of ${text(a.total, '0')} shown`}</span>
            </div>
          ) : null}
        </section>
        <div className="stack">
          <section className="panel" data-ring="actors">
            <div className="panel-h">
              <Users size={15} aria-hidden="true" />
              <h2>By actor</h2>
            </div>
            {actors.length ? actors.map(([actor, info]) => <ActorRow key={actor} actor={actor} info={info} />) : <div style={{ padding: '4px 14px 12px' }} className="faint">No actor to show.</div>}
          </section>
          <section className="panel" data-ring="debt">
            <div className="panel-h">
              <Stethoscope size={15} aria-hidden="true" />
              <h2>Doctor debt</h2>
              <div className="acts">{last ? <span className="faint num">{`${text(last.debt, '0')} now`}</span> : null}</div>
            </div>
            <div style={{ padding: '4px 14px 12px' }}>
              {spark ? (
                <svg className="spark" width={spark.width} height={spark.height} viewBox={`0 0 ${spark.width} ${spark.height}`} role="img" aria-label="Doctor debt over the last runs">
                  <polyline fill="none" stroke="var(--accent)" strokeWidth="1.6" points={spark.points} />
                </svg>
              ) : (
                <span className="faint">{trendText}</span>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
