/* UI-06 Catch up: the reader's view over one bounded read model.
 *
 * Reads exactly one document through the generic request helper:
 *   GET /api/read/ui_catchup?kinds=&limit=&since=
 * and requires the ui-intel-v1 envelope BEFORE rendering anything, so this page
 * can never draw a different contract as if it were this one.
 *
 * The mark-seen boundary is STATED, exactly as the feed displayed it
 * (through_id on an event log, through_at on a v1 status_log). The only write
 * this page offers is "Mark as seen", which posts those exact arguments through
 * the shared confirmed-tool envelope (ask first, send what the panel shows) and
 * then refreshes the read; the displayed boundary never moves by itself.
 *
 * Counts are rendered with countLabel(): a null/absent/non-integer value is
 * "unknown", never 0 and never a success. The Metrics tab renders the read's
 * six groups with their denominator and as-of; it emits no percentage, no
 * coverage ratio, no effectiveness claim and no recorded-file target, because
 * the read itself refuses to publish them.
 *
 * App.tsx maps the existing 'activity' route (label "Catch up", alias #catchup)
 * to this page, and refresh.ts lists the read so the shell's Reload reaches it.
 */
import { useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RAIL_KEY } from '../refresh';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { Badge, Button, ErrorState, Loading, PageHeading } from '../ui';

const INTEL_CONTRACT = 'ui-intel-v1';
const VIEW = 'ui_catchup';
const PAYLOAD_VIEW = 'catchup';
const PAGE = 50;
const PAGE_MAX = 100;

/* The six groups UI-06 names, in the order the Metrics tab renders them. */
export const GROUP_ORDER = ['observation', 'recorded_artifacts', 'uncertainty',
  'no_code_change', 'historical_unmeasured', 'denominator'] as const;

export const GROUP_TITLES: Record<string, string> = {
  observation: 'Observation availability',
  recorded_artifacts: 'Tasks with recorded work',
  uncertainty: 'Uncertainty',
  no_code_change: 'Tasks that completed with no code change',
  historical_unmeasured: 'Historic completed tasks with no capture',
  denominator: 'Current denominator',
};

const TYPE_LABELS: Record<string, string> = {
  capture: 'capture',
  correction: 'correction',
  scope_change: 'scope change',
  retirement: 'retirement',
  adoption: 'adoption',
  moment: 'moment',
};

type RawRequest = (path: string, init?: RequestInit) => Promise<Data>;

/* The generic request helper with the one envelope this page accepts. A wrong
 * contract, a wrong view or a missing state is an error here, never an empty
 * success rendered as a clean board. */
export async function catchupRead(
  request: PageProps['request'],
  query: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<Data> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) if (value) search.set(key, value);
  const path = '/api/read/' + VIEW + (search.size ? '?' + search.toString() : '');
  const body = await (request as unknown as RawRequest)(path, signal ? { signal } : undefined);
  return catchupView(body);
}

export function catchupView(body: Data): Data {
  if (text(body.contract) !== INTEL_CONTRACT) throw new Error('The catch-up read returned an unsupported contract');
  if (text(body.view) !== PAYLOAD_VIEW) throw new Error('The catch-up read returned another view');
  const status = text(body.status);
  if (!['ok', 'unknown', 'refused', 'error'].includes(status)) throw new Error('The catch-up read returned no state');
  return body;
}

const integer = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;

/* A harness-reported exit code is a number, not a count: a negative value is
 * legitimate and is reported as itself, never coerced to 0. */
export function exitLabel(value: unknown): string {
  return typeof value === 'number' && Number.isInteger(value) ? String(value) : 'unknown';
}

/* A list the read publishes as plain strings (kinds, examples, reasons,
 * labels). rows() only keeps objects, so these lists read through here: a
 * value that is not a string is dropped, never rendered as "[object Object]". */
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => text(item)).filter(Boolean) : [];
}

/* An empty string in the payload is missing data, not a name: it falls back
 * exactly like an absent field, so a row never renders a blank source/actor. */
function shown(value: unknown, fallback: string): string {
  return text(value) || fallback;
}

/* The one coercion in this page, deliberately narrow: a value that is not a
 * non-negative integer is "unknown". It is never 0, never "ok" and never a
 * success - the failing control in the suite breaks exactly this function. */
export function countLabel(value: unknown): string {
  const number = integer(value);
  return number === null ? 'unknown' : String(number);
}

export function capOf(value: unknown): number {
  const number = integer(value);
  if (number === null || number < 1) return PAGE;
  return Math.min(number, PAGE_MAX);
}

/* Show more advances the bounded page by PAGE rows and stops at the read's own
 * cap: the page never asks for more than the read will return. */
export function pageNext(limit: number): number {
  const current = integer(limit) ?? PAGE;
  return Math.min(current + PAGE, PAGE_MAX);
}

export function pageCapped(limit: number): boolean {
  return (integer(limit) ?? PAGE) >= PAGE_MAX;
}

/* The exact mark-seen arguments the displayed boundary implies. Supplying a
 * boundary is a WRITE, so the page only names it; when no boundary is displayed
 * there is nothing to mark. ``mark_seen`` is part of the argument set: without it
 * the activity tool refuses the boundary (a read never writes) and the click
 * would fail silently. */
export function markSeenArgs(markSeen: Data): Record<string, string | boolean> | null {
  if (markSeen.available !== true) return null;
  const id = text(markSeen.through_id);
  const at = text(markSeen.through_at);
  if (id && !at) return { mark_seen: true, through_id: id };
  if (at && !id) return { mark_seen: true, through_at: at };
  return null;
}

/* The one rendering of those arguments: the panel and the confirmation show the
 * exact object the write posts, so they can never disagree. */
export function markSeenText(args: Record<string, string | boolean>): string {
  return '{ ' + Object.entries(args)
    .map(([key, value]) => key + ': ' + JSON.stringify(value)).join(', ') + ' }';
}

export function markSeenLine(markSeen: Data): string {
  const boundary = object(markSeen.boundary);
  const id = text(boundary.id);
  const at = text(boundary.at);
  if (markSeen.available !== true) return text(markSeen.note, 'Nothing is displayed to mark seen.');
  if (id) return 'Exactly the newest event shown: ' + id + (at ? ' at ' + at : '');
  return 'Exactly the newest status_log entry shown: ' + at;
}

export function rawAuditHref(audit: Data, project: string): string {
  const args = object(audit.args);
  const search = new URLSearchParams();
  const since = text(args.since);
  const kinds = text(args.kinds);
  if (since) search.set('since', since);
  if (kinds) search.set('kinds', kinds);
  const limit = integer(args.limit);
  if (limit !== null) search.set('limit', String(limit));
  if (project) search.set('project', project);
  return '/api/read/activity' + (search.size ? '?' + search.toString() : '');
}

export function typeCounts(typed: Data): string {
  const types = object(typed.types);
  const parts = Object.keys(types)
    .filter(key => integer(types[key]) !== null)
    .sort()
    .map(key => (TYPE_LABELS[key] ?? key) + ' ' + countLabel(types[key]));
  return parts.join(' · ');
}

export function feedCountLines(feed: Data): string[] {
  const lines = [
    'Showing ' + countLabel(feed.shown) + ' of ' + countLabel(feed.total)
      + ' matching events · page cap ' + countLabel(rowCap(feed)),
  ];
  for (const trim of rows(feed.trims)) {
    lines.push('Trimmed ' + text(trim.section, 'section') + ': kept ' + countLabel(trim.kept)
      + ' of cap ' + countLabel(trim.cap) + ', cut ' + countLabel(trim.cut));
  }
  const untyped = strings(feed.untyped_kinds);
  if (untyped.length) {
    lines.push('Not typed by this read: ' + untyped.join(', ')
      + ' (' + countLabel(feed.untyped_events) + ' events, shown raw)');
  }
  return lines;
}

function rowCap(feed: Data): number {
  const trims = rows(feed.trims);
  return trims.length ? integer(trims[0].cap) ?? PAGE : PAGE;
}

export function typedCountLines(typed: Data): string[] {
  if (typed.known !== true) {
    return ['Typed event summaries are unknown: ' + text(typed.reason,
      'this store keeps no event log, so the feed is its status_log')];
  }
  const lines = [
    countLabel(typed.total) + ' typed events in scope · ' + countLabel(typed.count)
      + ' shown · ' + countLabel(typed.omitted) + ' outside this page',
  ];
  const counts = typeCounts(typed);
  if (counts) lines.push(counts);
  const families = object(typed.families);
  if (Object.keys(families).length) {
    lines.push(Object.keys(families).sort()
      .map(kind => kind + ' ' + countLabel(families[kind])).join(' · '));
  }
  return lines;
}

export type MetricGroup = {
  key: string; title: string; count: string; definition: string; details: string[]; state: string;
};

/* The six groups, flattened for rendering. Every number goes through
 * countLabel(): a null cell is "unknown" even while a neighbouring cell is a
 * real count, so a partially measured store is never rounded into a zero. */
export function metricGroups(metrics: Data): MetricGroup[] {
  const groups = object(metrics.groups);
  return GROUP_ORDER.map(key => {
    const group = object(groups[key]);
    const details: string[] = [];
    if (key === 'observation') {
      details.push('Event log: ' + (group.log === true ? 'present' : 'absent')
        + ' · ' + countLabel(group.tasks_total) + ' tasks in the store');
      details.push('Eligible completions without a usable capture: '
        + countLabel(group.captured_unavailable));
      const examples = strings(group.unavailable_examples);
      if (examples.length) details.push('Examples: ' + examples.join(', '));
      if (text(group.reason)) details.push(text(group.reason));
    } else if (key === 'recorded_artifacts') {
      const files = object(group.file_totals);
      details.push('Files observed ' + countLabel(files.observed)
        + ' · recorded ' + countLabel(files.recorded) + ' (' + text(files.denominator, 'eligible tasks only') + ')');
      if (text(files.note)) details.push(text(files.note));
      details.push('Recorded means observed files with no known conflict; it is not proven authorship.');
    } else if (key === 'uncertainty') {
      const detail = object(group.details);
      const byReason = object(detail.by_reason);
      const reasons = Object.keys(byReason).sort()
        .map(reason => reason + ' ' + countLabel(byReason[reason]));
      if (reasons.length) details.push('Why: ' + reasons.join(' · '));
      details.push('Ambiguous files ' + countLabel(detail.ambiguous_files)
        + ' · unverifiable ' + countLabel(detail.unverifiable_files)
        + ' · merged in ' + countLabel(detail.merged_in_files)
        + ' · tasks with an overlapping claim ' + countLabel(detail.tasks_with_overlap));
    } else if (key === 'historical_unmeasured') {
      const examples = strings(group.examples);
      if (examples.length) details.push('Examples: ' + examples.join(', '));
      const outside = object(group.denominator);
      details.push('Outside the denominator: ' + text(outside.unit, 'not counted'));
    } else if (key === 'denominator') {
      details.push('Unit: ' + text(group.unit, 'tasks currently done that carry a capture record'));
      const outside = object(group.outside);
      details.push('Historic completed tasks outside it: ' + countLabel(outside.historical_unmeasured));
    }
    if (text(group.definition) && key !== 'recorded_artifacts') details.push(text(group.definition));
    return {
      key,
      title: GROUP_TITLES[key] ?? key,
      count: countLabel(key === 'denominator' ? group.value : key === 'observation' ? group.capture_events : group.count),
      definition: text(group.definition),
      details,
      state: text(group.state, 'unknown'),
    };
  });
}

export function metricsReason(metrics: Data): string {
  if (metrics.measured !== true || text(metrics.state) !== 'measured') {
    return text(metrics.reason,
      'This store cannot be measured yet, so the captures are unknown - not zero successes.');
  }
  return '';
}

export function partitionLine(metrics: Data): string {
  const partition = object(metrics.partition);
  const sum = countLabel(partition.sum);
  const denominator = countLabel(partition.denominator);
  const matches = partition.matches_denominator;
  const verdict = matches === true ? 'they match'
    : matches === false ? 'they do NOT match' : 'not measured';
  return 'Recorded work + uncertainty + no code change + unavailable = ' + sum
    + ' against a denominator of ' + denominator + ' (' + verdict + ').';
}

function FeedLine({ row }: { row: Data }) {
  const typed = object(row.typed);
  if (!Object.keys(typed).length) {
    return <div className="card" data-event={text(row.id)} data-type="raw" style={{ padding: 8, marginTop: 6 }}>
      <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
        <Badge>raw</Badge>
        <b className="mono" style={{ fontSize: 12 }}>{text(row.kind, 'event')}</b>
        <span className="faint" style={{ fontSize: 11 }}>{text(row.at)} · {text(row.actor, 'unknown actor')}</span>
      </div>
      {text(row.summary) ? <div style={{ fontSize: 12 }}>{text(row.summary)}</div> : null}
      <div className="faint" style={{ fontSize: 11 }}>Not typed by this read · event {shown(row.id, 'no id')} · source {shown(row.source, 'status_log')}</div>
    </div>;
  }
  const type = text(typed.type);
  const state = text(typed.state, 'unknown');
  if (type === 'capture') {
    const counts = object(typed.counts);
    const tone = state === 'recorded' ? 'good' : state === 'failed' ? 'warning' : 'neutral';
    const label = state === 'recorded' ? 'capture recorded' : state === 'failed' ? 'capture failed' : 'capture unknown';
    return <div className="card" data-event={text(row.id)} data-type="capture" data-state={state} style={{ padding: 8, marginTop: 6 }}>
      <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
        <Badge tone={tone}>{label}</Badge>
        <b className="mono" style={{ fontSize: 12 }}>{text(typed.task, 'no task')}</b>
        <span className="faint" style={{ fontSize: 11 }}>{text(row.at)} · {text(row.actor, 'unknown actor')}</span>
      </div>
      <div className="faint" style={{ fontSize: 12 }}>
        observed files {countLabel(counts.observed_files)} · recorded files {countLabel(counts.recorded_files)}
        {' · '}commits {countLabel(counts.recorded_commits)} · ambiguous {countLabel(counts.ambiguous)}
        {' · '}unverifiable {countLabel(counts.unverifiable)} · merged in {countLabel(counts.merged_in)}
      </div>
      <div className="faint" style={{ fontSize: 12 }}>
        attribution {text(typed.attribution, 'unknown')}
        {text(typed.reason_code) ? ' · reason ' + text(typed.reason_code) : ''}
      </div>
      {text(typed.note) ? <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>{text(typed.note)}</p> : null}
      <div className="faint" style={{ fontSize: 11 }}>not acceptance proof · event {text(row.id)} · source {shown(row.source, 'unknown')}</div>
    </div>;
  }
  if (type === 'moment') {
    return <div className="card" data-event={text(row.id)} data-type="moment" data-state={state} style={{ padding: 8, marginTop: 6 }}>
      <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
        <Badge tone="warning">unverified observation</Badge>
        <b style={{ fontSize: 12 }}>{text(typed.moment, 'moment')}</b>
        <span className="faint" style={{ fontSize: 11 }}>{text(row.at)} · {text(row.actor, 'unknown actor')} · via {text(typed.via, 'unknown')}</span>
      </div>
      <div className="faint" style={{ fontSize: 12 }}>
        Harness-reported exit code {exitLabel(typed.observed_exit_code)}
      </div>
      {text(typed.note) ? <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>{text(typed.note)}</p> : null}
      <div className="faint" style={{ fontSize: 11 }}>not acceptance proof · event {text(row.id)} · source {shown(row.source, 'unknown')}</div>
    </div>;
  }
  const tone = type === 'adoption' ? 'good' : type === 'retirement' ? 'warning'
    : type === 'scope_change' ? 'accent' : 'neutral';
  const label = TYPE_LABELS[type] ?? type;
  return <div className="card" data-event={text(row.id)} data-type={type} data-state={state} style={{ padding: 8, marginTop: 6 }}>
    <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
      <Badge tone={tone}>{label}</Badge>
      <b className="mono" style={{ fontSize: 12 }}>{text(typed.correction, 'unnamed record')}</b>
      <span className="faint" style={{ fontSize: 11 }}>{text(row.at)} · {text(row.actor, 'unknown actor')}</span>
    </div>
    <div className="faint" style={{ fontSize: 12 }}>
      State {text(typed.to, 'unknown')} · task {text(typed.task, 'none')}
      {' · '}convention {text(typed.convention, 'none')} · via {text(typed.via, 'unknown')}
    </div>
    {text(typed.note) ? <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>{text(typed.note)}</p> : null}
    <div className="faint" style={{ fontSize: 11 }}>not acceptance proof · event {text(row.id)} · source {shown(row.source, 'unknown')}</div>
  </div>;
}

function BoundaryPanel({ markSeen }: { markSeen: Data }) {
  const args = markSeenArgs(markSeen);
  return <section className="card" aria-label="Mark-seen boundary" style={{ padding: 10, marginTop: 8 }}>
    <h3 style={{ marginTop: 0 }}>Mark seen</h3>
    <p style={{ fontSize: 13, margin: '4px 0' }} data-boundary-line>{markSeenLine(markSeen)}</p>
    {args ? <p className="mono" style={{ fontSize: 12 }} data-mark-args>
      {markSeenText(args)}
    </p> : null}
    <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>{text(markSeen.covers)}</p>
    <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>{text(markSeen.write)}</p>
    {markSeen.inclusive === true ? <p className="faint" style={{ fontSize: 12 }}>
      A v1 status_log time is inclusive: entries sharing the marked time repeat rather than being lost.
    </p> : null}
  </section>;
}

/* A partial view is surfaced, never smoothed over: when the event log holds a
 * file no reader can parse, the count, the files, the skip reasons and what the
 * exclusion means for the counts are all displayed above the feed. */
function PartialPanel({ partial }: { partial: Data }) {
  /* Only a read that actually states a partial state gets a panel: an older
   * payload with no block renders nothing, and 'clean' is stated nowhere. */
  const state = text(partial.state);
  if (!state || state === 'clean') return null;
  const files = strings(partial.files);
  const reasons = strings(partial.reasons);
  const count = countLabel(partial.invalid_event_files);
  return <section className="card" role="alert" aria-label="Partial feed"
    data-partial-state={state} style={{ padding: 10, marginTop: 8, borderColor: 'var(--warning)' }}>
    <b style={{ fontSize: 13 }}>
      {state === 'invalid-events'
        ? 'Partial feed: ' + count + ' unreadable event file(s) were skipped'
        : 'Partial feed: unknown - this store keeps no event log to compare'}
    </b>
    {files.length ? <p className="mono" style={{ fontSize: 11, margin: '4px 0' }} data-partial-files>{files.join(', ')}</p> : null}
    {reasons.map((reason, index) => <p key={index} className="faint" style={{ fontSize: 12, margin: '2px 0' }}>{reason}</p>)}
    <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>{text(partial.reason, 'a skipped event file is not evidence of a clean feed')}</p>
    <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>{text(partial.note)}</p>
    <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>Nothing here reads as a clean, complete history while this block is shown.</p>
  </section>;
}

export const MARK_SEEN_TITLE = 'Confirm marking this boundary seen';

/* The confirmation names the exact arguments the write will post, before it is
 * sent: a mark-seen write can never run silently or post a boundary other than
 * the one the feed displayed. */
function MarkSeenDialog({ args, busy, onCancel, onConfirm }: {
  args: Record<string, string | boolean>;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return <div className="card" role="alertdialog" aria-label={MARK_SEEN_TITLE}
    style={{ padding: 10, marginTop: 8, borderColor: 'var(--accent)' }}>
    <b style={{ fontSize: 13 }}>{MARK_SEEN_TITLE}</b>
    <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>
      This is a write: it posts exactly the boundary the feed displayed, through the activity tool.
      Nothing after that boundary is marked.
    </p>
    <p className="mono" style={{ fontSize: 12 }} data-confirmed-args>
      {markSeenText(args)}
    </p>
    <div className="row" style={{ gap: 8, marginTop: 8 }}>
      <Button variant="primary" disabled={busy} onClick={onConfirm}>Mark this boundary seen</Button>
      <Button variant="secondary" disabled={busy} onClick={onCancel}>Cancel</Button>
    </div>
  </div>;
}

function RawAuditPanel({ data, project }: { data: Data; project: string }) {
  const audit = object(data.raw_audit);
  const feed = object(data.feed);
  return <section className="card" aria-label="Raw audit" style={{ padding: 10, marginTop: 8 }}>
    <h3 style={{ marginTop: 0 }}>Raw audit</h3>
    <p style={{ fontSize: 13, margin: '4px 0' }}>
      <a href={rawAuditHref(audit, project)} target="_blank" rel="noopener noreferrer">
        Open the raw activity read (the underlying events)
      </a>
      <span className="faint"> - the untyped event data for these same ids; a typed summary is a projection of it.</span>
    </p>
    <p className="faint" style={{ fontSize: 12 }} data-raw-total>
      {countLabel(audit.total)} events matched by this read · view {text(audit.view, 'activity')}
    </p>
    <details>
      <summary className="faint" style={{ fontSize: 12 }}>The displayed events and the file each one came from</summary>
      <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
        {rows(feed.events).map((row, index) => <li key={text(row.id, String(index))} style={{ fontSize: 12 }}>
          <span className="mono">{text(row.id, 'no id')}</span> · {text(row.kind)} · {text(row.at)} · {text(row.actor, 'unknown actor')}
          {' · '}<span className="mono">{shown(row.source, 'status_log (no event id)')}</span>
        </li>)}
      </ul>
    </details>
  </section>;
}

function FeedTab({ data, project }: { data: Data; project: string }) {
  const feed = object(data.feed);
  const typed = object(data.typed);
  const events = rows(feed.events);
  return <div role="tabpanel" aria-label="Feed">
    <BoundaryPanel markSeen={object(data.mark_seen)} />
    <RawAuditPanel data={data} project={project} />
    <section className="card" aria-label="Event page" style={{ padding: 10, marginTop: 8 }}>
      <h3 style={{ marginTop: 0 }}>Events</h3>
      {feedCountLines(feed).map((line, index) => <p key={index} className="faint" style={{ fontSize: 12, margin: '2px 0' }}>{line}</p>)}
      {typedCountLines(typed).map((line, index) => <p key={index} className="faint" style={{ fontSize: 12, margin: '2px 0' }} data-typed-counts={index === 0 ? '' : undefined}>{line}</p>)}
      {events.length ? events.map((row, index) => <FeedLine key={text(row.id, String(index))} row={row} />) : <p className="faint" style={{ fontSize: 12 }}>
        No events match this selection. An empty feed is not an empty store: the since marker and the family filter both narrow it.
      </p>}
    </section>
  </div>;
}

function MetricsTab({ metrics, typed }: { metrics: Data; typed: Data }) {
  const reason = metricsReason(metrics);
  const target = object(metrics.target);
  const partition = partitionLine(metrics);
  const basis = object(metrics.basis);
  const groups = metricGroups(metrics);
  const refusals = object(metrics.not_reported);
  const labels = strings(metrics.labels);
  return <div role="tabpanel" aria-label="Metrics">
    <section className="card" aria-label="Metrics state" style={{ padding: 10, marginTop: 8 }}>
      <p style={{ fontSize: 13, margin: '0 0 4px' }} data-metrics-state={text(metrics.state, 'unknown')}>
        {text(metrics.state) === 'measured'
          ? 'Counts only, with the denominator and the moment they describe.'
          : 'Metrics unknown'}
        {reason ? ' - ' + reason : ''}
      </p>
      {text(basis.source) ? <p className="faint" style={{ fontSize: 12, margin: '2px 0' }}>
        Source: {text(basis.source)} · scope {text(basis.scope, 'the whole store')} · as of {text(basis.as_of, 'unknown')}
      </p> : null}
      <p className="faint" style={{ fontSize: 12, margin: '2px 0' }} data-partition>{partition}</p>
      {typed.known !== true ? <p className="faint" style={{ fontSize: 12 }}>Typed events: unknown ({text(typed.reason, 'no event log')}).</p> : null}
    </section>
    {groups.map(group => <article key={group.key} className="card" data-group={group.key} data-group-state={group.state} style={{ padding: 10, marginTop: 8 }}>
      <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
        <b>{group.title}</b>
        <span className="mono" data-metric-count={group.key}>{group.count}</span>
        {group.state !== 'measured' ? <Badge tone="warning">unknown</Badge> : null}
      </div>
      {group.details.map((detail, index) => <p key={index} className="faint" style={{ fontSize: 12, margin: '2px 0' }}>{detail}</p>)}
    </article>)}
    <section className="card" aria-label="Target" style={{ padding: 10, marginTop: 8 }}>
      <b>Recorded-file target</b>
      <p className="faint" style={{ fontSize: 12, margin: '2px 0' }} data-target-state={text(target.state, 'undefined')}>
        {text(target.state) === 'undefined'
          ? 'Not displayed: ' + text(target.reason, 'no event-based metric with an owner-approved denominator exists yet.')
          : 'State: ' + text(target.state) + ' - value ' + countLabel(target.value)}
      </p>
    </section>
    <section className="card" aria-label="How to read these counts" style={{ padding: 10, marginTop: 8 }} data-panel="honesty">
      <h3 style={{ marginTop: 0 }}>How to read these counts</h3>
      {labels.map((label, index) => <p key={index} className="faint" style={{ fontSize: 12, margin: '2px 0' }}>{label}</p>)}
      <h4>Not reported here</h4>
      {Object.keys(refusals).sort().map(key => <p key={key} className="faint" style={{ fontSize: 12, margin: '2px 0' }}>
        <span className="mono">{key.replace(/_/g, ' ')}</span>: {text(refusals[key])}
      </p>)}
    </section>
  </div>;
}

export function CatchupPage({ request, project, params, refresh, mutate, navigate }: PageProps) {
  const client = useQueryClient();
  const [tab, setTab] = useState<'feed' | 'metrics'>('feed');
  const [limit, setLimit] = useState(() =>
    capOf(params.get('limit') ? Number(params.get('limit')) : PAGE));
  const [sinceCleared, setSinceCleared] = useState(false);
  const since = sinceCleared ? '' : (params.get('since') || '');
  const [kinds, setKinds] = useState('');
  const [confirming, setConfirming] = useState<Record<string, string | boolean> | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  /* The key follows the refresh contract's layout ([rail, project, read, ...]),
   * so the shell's Reload reaches this read (refresh.ts lists it). */
  const read = useQuery({
    queryKey: [RAIL_KEY, project, 'catchup', String(limit), since, kinds],
    queryFn: ({ signal }) => catchupRead(request, { limit: String(limit), since, kinds }, signal),
    staleTime: 30_000,
    retry: false,
  });
  const reload = async () => {
    if (refresh) { await refresh(); return; }
    await client.invalidateQueries({ queryKey: [RAIL_KEY, project, 'catchup'] });
  };
  /* A filter or page change keeps the previous page on screen while the next
   * one loads: the actions never flicker away and a loading read is never drawn
   * as an empty, clean-looking board. The as-of line still dates the rows. */
  const latest = useRef<Data | undefined>(undefined);
  if (read.data) latest.current = read.data;
  const body = read.data ?? latest.current;
  const data = useMemo(() => object(body?.data), [body]);
  const status = text(body?.status);
  const reasons = useMemo(() => strings(body?.reasons), [body]);
  const markArgs = useMemo(() => markSeenArgs(object(data.mark_seen)), [data]);
  const filtered = Boolean(kinds || params.get('since'));
  const showEverything = () => {
    setKinds('');
    setSinceCleared(true);
    /* Clearing the filter is a read change, not a write: the URL drops the
     * since marker so a reload cannot quietly restore it. */
    navigate('activity');
  };
  const showMore = () => setLimit(pageNext(limit));
  const markSeen = async () => {
    if (!confirming || !mutate) return;
    setBusy(true);
    try {
      const answer = await mutate('activity', confirming, true);
      const answered = text(answer.status);
      const boundary = text(answer.through_id) || text(answer.through_at)
        || confirming.through_id || confirming.through_at || '';
      setResult(answered === 'ok'
        ? { ok: true, text: 'Marked seen through ' + boundary + '. The read was refreshed: the boundary it now displays is the newest event of that refreshed page, never the log\'s newest.' }
        : { ok: false, text: 'The write answered ' + (answered || 'no state') + '; nothing is claimed as marked and the feed is unchanged.' });
      await reload();
    } catch (error) {
      setResult({ ok: false, text: 'The write failed: ' + (error instanceof Error ? error.message : String(error)) + ' Nothing is marked.' });
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  };

  return <div className="page understanding rail-catchup">
    <PageHeading title="Catch up" description="What happened since you last looked: typed event summaries, the exact mark-seen boundary that was displayed, the raw audit behind every row, and a compact count of what the observation layer can and cannot attribute." action={
      <Button variant="secondary" onClick={reload} disabled={read.isFetching}>Reload</Button>
    } />
    {read.isPending ? <Loading /> : null}
    {read.isError ? <ErrorState error={read.error instanceof Error ? read.error.message : String(read.error)} retry={reload} /> : null}
    {body && status !== 'ok' ? <section className="card" role="alert" aria-label="Read state" style={{ padding: 10, marginTop: 8 }}>
      <b>The store answered {status || 'no state'}.</b>
      {reasons.map((reason, index) => <p key={index} className="faint" style={{ fontSize: 12 }}>{reason}</p>)}
      <p className="faint" style={{ fontSize: 12 }}>Nothing is shown as measured, zero or successful from a read that did not answer.</p>
    </section> : null}
    {body && status === 'ok' ? <PartialPanel partial={object(data.partial)} /> : null}
    {result ? <section className="card" role="status" aria-label="Last result"
      data-result-state={result.ok ? 'ok' : 'failed'} data-refresh-boundary={text(body?.as_of)}
      style={{ padding: 10, marginTop: 8 }}>
      <b style={{ fontSize: 13 }}>{result.text}</b>
      <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>
        The displayed boundary line above is the refreshed read\'s boundary as of {text(body?.as_of, 'unknown')}; an event that arrived after that display stays unseen.
      </p>
    </section> : null}
    {body && status === 'ok' ? <>
      <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <label className="faint" style={{ fontSize: 12 }}>Event families
          <select aria-label="Event families" value={kinds} onChange={event => setKinds(event.target.value)} style={{ marginLeft: 6 }}>
            <option value="">All events</option>
            <option value="work.captured">Captures</option>
            <option value="correction.*">Corrections</option>
            <option value="convention.corrected">Adoptions</option>
            <option value="harness.moment">Harness moments</option>
          </select>
        </label>
        <span className="faint" style={{ fontSize: 12 }}>as of {text(body.as_of, 'unknown')} · page {String(limit)} of at most {String(PAGE_MAX)}</span>
      </div>
      <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }} aria-label="Catch up actions">
        <Button variant="primary" disabled={!markArgs || busy || !mutate}
          onClick={() => markArgs && setConfirming(markArgs)}>Mark as seen</Button>
        <Button variant="secondary" disabled={!filtered || read.isFetching} onClick={showEverything}>Show everything</Button>
        <Button variant="secondary" disabled={pageCapped(limit) || read.isFetching} onClick={showMore}>Show more</Button>
        {pageCapped(limit) ? <span className="faint" style={{ fontSize: 12 }}>page cap reached: the read will not return more than {String(PAGE_MAX)} rows</span> : null}
      </div>
      {confirming ? <MarkSeenDialog args={confirming} busy={busy}
        onCancel={() => setConfirming(null)} onConfirm={markSeen} /> : null}
      <nav className="row" role="tablist" aria-label="Catch up sections" style={{ gap: 6, marginTop: 8 }}>
        <button type="button" role="tab" aria-selected={tab === 'feed'} className={'btn ' + (tab === 'feed' ? 'btn-primary' : 'btn-secondary')} onClick={() => setTab('feed')}>Feed</button>
        <button type="button" role="tab" aria-selected={tab === 'metrics'} className={'btn ' + (tab === 'metrics' ? 'btn-primary' : 'btn-secondary')} onClick={() => setTab('metrics')}>Metrics</button>
      </nav>
      {tab === 'feed' ? <FeedTab data={data} project={project} /> : <MetricsTab metrics={object(data.metrics)} typed={object(data.typed)} />}
    </> : null}
  </div>;
}
