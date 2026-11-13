/* Catch up is the store's own event log, read as a day-grouped feed. Every row
   is a recorded event: the kind, instant and actor come from the record, and
   the subject follows the `about` relation. The screen never infers a change
   from a document's current state, and never edits history. */
import { useMemo, useState } from 'react';
import { ArrowRight, FileText, GitBranch, History, RotateCcw, Search, Users, X } from '../icons';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { cn } from '../ui';
import { itemById } from '../capabilities';
import { labelOf, titleCase } from './operating';

const PAGE = 60;

/* Event kinds are the engine's vocabulary, not a pack's. A kind this build has
   never seen still renders, with a neutral tone and its own payload. */
const KIND_TONE: Record<string, string> = {
  'doc.created': 'ok',
  'doc.updated': 'info',
  'doc.renamed': 'info',
  'doc.relocated': 'info',
  'status.changed': 'focus',
  'relation.changed': 'violet',
  'decision.made': 'violet',
  'check.declared': 'warn',
  'check.verdict': 'warn',
  'review.verdict': 'warn',
  'action.executed': 'focus',
  'pack.created': 'violet',
  'pack.changed': 'violet',
  'sync.integrated': 'info',
  'migration.completed': 'info',
  'upgrade.completed': 'info',
  'onboarding.drafted': 'ok',
  'onboarding.completed': 'ok',
};

const FAMILY_TONE: Record<string, string> = {
  doc: 'info',
  status: 'focus',
  relation: 'violet',
  decision: 'violet',
  check: 'warn',
  review: 'warn',
  action: 'focus',
  pack: 'violet',
  run: 'info',
  sync: 'info',
  migration: 'info',
  upgrade: 'info',
  onboarding: 'ok',
};

export interface EventFact {
  id: string;
  kind: string;
  family: string;
  at: string;
  time: number;
  actor: string;
  about: string;
  data: Data;
  tone: string;
}

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];

/* Records carry `created: null`; the instant lives in the event payload. An
   event with no readable instant sorts last rather than borrowing one. */
export function eventsOf(snapshot: Data): EventFact[] {
  const out: EventFact[] = [];
  const seen = new Set<string>();
  for (const doc of rows(snapshot.records)) {
    if (text(doc.primitive) !== 'record') continue;
    const id = text(doc.id);
    if (!id || seen.has(id)) continue;
    const fields = object(doc.fields);
    const kind = text(fields.kind);
    const at = text(fields.at);
    if (!kind || !at) continue;
    seen.add(id);
    const parsed = Date.parse(at);
    const family = kind.split('.')[0] ?? '';
    out.push({
      id,
      kind,
      family,
      at,
      time: Number.isNaN(parsed) ? 0 : parsed,
      actor: text(fields.actor, 'unknown'),
      about: strings(object(doc.relations).about)[0] ?? '',
      data: object(fields.data),
      tone: KIND_TONE[kind] ?? FAMILY_TONE[family] ?? 'muted',
    });
  }
  return out.sort((a, b) => b.time - a.time || a.id.localeCompare(b.id));
}

const dayKey = (ms: number): string => {
  if (!ms) return 'undated';
  const date = new Date(ms);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
};

function dayLabel(key: string, today: string, yesterday: string): string {
  if (key === 'undated') return 'No recorded instant';
  if (key === today) return 'Today';
  if (key === yesterday) return 'Yesterday';
  const parsed = new Date(`${key}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? key
    : parsed.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

const clockOf = (ms: number): string =>
  ms ? new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '--:--';

/* The payload is engine-shaped and differs per kind, so the two transitions a
   reader scans for are named and everything else is listed as recorded. */
function EventDetail({ event }: { event: EventFact }) {
  const data = event.data;
  const from = text(data.from);
  const to = text(data.to);
  if (event.kind === 'status.changed' && (from || to)) {
    return (
      <span className="ra-change">
        <span className="ra-state">{from || '—'}</span>
        <ArrowRight size={11} aria-hidden="true" />
        <span className="ra-state to">{to || '—'}</span>
        {text(data.workflow) ? <span className="ra-qual">in {text(data.workflow)}</span> : null}
        {text(data.resolution) ? <span className="ra-qual">· {text(data.resolution)}</span> : null}
      </span>
    );
  }
  const entries = Object.entries(data).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  );
  if (!entries.length) return <span className="ra-qual">no payload recorded</span>;
  return (
    <span className="ra-payload">
      {entries.slice(0, 4).map(([key, value]) => (
        <span className="ra-pair" key={key}>
          <span className="ra-pair-k">{key}</span>
          <span className="ra-pair-v">
            {Array.isArray(value)
              ? value.map((entry) => text(entry)).filter(Boolean).join(', ') || '—'
              : typeof value === 'object'
                ? JSON.stringify(value)
                : text(value)}
          </span>
        </span>
      ))}
      {entries.length > 4 ? <span className="ra-qual">+{entries.length - 4} more</span> : null}
    </span>
  );
}

export function ActivityPage({ snapshot, params, navigate, openDoc, busy, refresh }: PageProps) {
  const byId = useMemo(() => itemById(snapshot), [snapshot]);
  const events = useMemo(() => eventsOf(object(snapshot)), [snapshot]);
  const [query, setQuery] = useState(params.get('q') ?? '');
  const [kind, setKind] = useState(params.get('kind') ?? 'all');
  const [actor, setActor] = useState(params.get('actor') ?? 'all');
  const [shown, setShown] = useState(PAGE);

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const event of events) counts.set(event.kind, (counts.get(event.kind) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [events]);

  const actors = useMemo(() => {
    const counts = new Map<string, number>();
    for (const event of events) counts.set(event.actor, (counts.get(event.actor) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [events]);

  const subjectOf = (event: EventFact): string =>
    event.about ? text(byId.get(event.about)?.title, event.about) : text(event.data.path, 'the store');

  const matched = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return events.filter((event) => {
      if (kind !== 'all' && event.kind !== kind) return false;
      if (actor !== 'all' && event.actor !== actor) return false;
      if (!needle) return true;
      const haystack = [
        event.kind,
        event.actor,
        event.about,
        subjectOf(event),
        JSON.stringify(event.data),
      ].join(' ').toLowerCase();
      return haystack.includes(needle);
    });
  }, [events, kind, actor, query, byId]);

  const now = new Date();
  const today = dayKey(now.getTime());
  const yesterday = dayKey(now.getTime() - 86_400_000);
  const todayCount = events.filter((event) => dayKey(event.time) === today).length;
  const moves = events.filter((event) => event.kind === 'status.changed').length;
  const created = events.filter((event) => event.kind === 'doc.created').length;

  const visible = matched.slice(0, shown);
  const groups = useMemo(() => {
    const buckets = new Map<string, EventFact[]>();
    for (const event of visible) {
      const key = dayKey(event.time);
      const bucket = buckets.get(key);
      if (bucket) bucket.push(event);
      else buckets.set(key, [event]);
    }
    return [...buckets.entries()];
  }, [visible]);

  const filtersOn = kind !== 'all' || actor !== 'all' || query.trim() !== '';

  return (
    <div className="ra-page">
      <header className="ra-head">
        <div>
          <div className="rf-kicker">{`${events.length} events · ${actors.length} ${actors.length === 1 ? 'actor' : 'actors'}`}</div>
          <h1>Catch up</h1>
          <p>Every recorded change, newest first, with who made it and what it touched.</p>
        </div>
        <div className="ra-head-tools">
          <button
            type="button"
            className="rf-chip"
            disabled={busy || !refresh}
            onClick={() => void refresh?.()}
          >
            <RotateCcw size={13} aria-hidden="true" /> {busy ? 'Reading…' : 'Refresh'}
          </button>
        </div>
      </header>

      <div className="ra-stats">
        <div className="ra-stat">
          <span className="ra-stat-k"><History size={12} aria-hidden="true" /> Today</span>
          <b>{todayCount}</b>
          <span className="ra-stat-n">{todayCount === 1 ? 'event' : 'events'}</span>
        </div>
        <div className="ra-stat">
          <span className="ra-stat-k"><GitBranch size={12} aria-hidden="true" /> Status moves</span>
          <b>{moves}</b>
          <span className="ra-stat-n">workflow transitions</span>
        </div>
        <div className="ra-stat">
          <span className="ra-stat-k"><FileText size={12} aria-hidden="true" /> Items created</span>
          <b>{created}</b>
          <span className="ra-stat-n">documents added</span>
        </div>
        <div className="ra-stat">
          <span className="ra-stat-k"><Users size={12} aria-hidden="true" /> Actors</span>
          <b>{actors.length}</b>
          <span className="ra-stat-n trunc">{actors.map(([name]) => name).join(', ') || 'none yet'}</span>
        </div>
      </div>

      <div className="ra-toolbar">
        <label className="ra-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            placeholder="Search kind, actor, subject or payload…"
            aria-label="Search the event log"
            onChange={(event) => {
              setQuery(event.target.value);
              setShown(PAGE);
            }}
          />
          {query ? (
            <button type="button" className="ra-clear" aria-label="Clear search" onClick={() => setQuery('')}>
              <X size={12} aria-hidden="true" />
            </button>
          ) : null}
        </label>
        <label className="ra-select">
          <span>Actor</span>
          <select
            value={actor}
            aria-label="Filter by actor"
            onChange={(event) => {
              setActor(event.target.value);
              setShown(PAGE);
            }}
          >
            <option value="all">Everyone</option>
            {actors.map(([name, count]) => (
              <option key={name} value={name}>{`${name} (${count})`}</option>
            ))}
          </select>
        </label>
        {filtersOn ? (
          <button type="button" className="rf-chip" onClick={() => clearFilters()}>
            <X size={12} aria-hidden="true" /> Clear filters
          </button>
        ) : null}
        <span className="ra-spacer" />
        <span className="ra-count">{`${matched.length} of ${events.length}`}</span>
      </div>

      <div className="ra-kinds" role="group" aria-label="Filter by event kind">
        <button
          type="button"
          className={cn('ra-kindchip', kind === 'all' && 'on')}
          aria-pressed={kind === 'all'}
          onClick={() => pickKind('all')}
        >
          All <b>{events.length}</b>
        </button>
        {kinds.map(([name, count]) => (
          <button
            type="button"
            key={name}
            className={cn('ra-kindchip', `tone-${KIND_TONE[name] ?? FAMILY_TONE[name.split('.')[0] ?? ''] ?? 'muted'}`, kind === name && 'on')}
            aria-pressed={kind === name}
            onClick={() => pickKind(name)}
          >
            <i aria-hidden="true" /> {titleCase(name.replace('.', ' '))} <b>{count}</b>
          </button>
        ))}
      </div>

      <main className="ra-feed">
        {!events.length ? (
          <div className="rf-emptystate">
            <b>No events recorded yet</b>
            <span>The store writes an event for every create, move and check. Nothing has happened here yet.</span>
          </div>
        ) : !matched.length ? (
          <div className="rf-emptystate">
            <b>No event matches this filter</b>
            <span>{`${events.length} events are recorded. Widen the kind, actor or search to see them.`}</span>
            <button type="button" className="rf-chip" onClick={() => clearFilters()}>Clear filters</button>
          </div>
        ) : (
          groups.map(([key, entries]) => (
            <section className="ra-day" key={key}>
              <div className="ra-day-head">
                <h2>{dayLabel(key, today, yesterday)}</h2>
                <span className="ra-day-n">{`${entries.length} ${entries.length === 1 ? 'event' : 'events'}`}</span>
              </div>
              <div className="ra-rows">
                {entries.map((event) => {
                  const doc = event.about ? byId.get(event.about) : undefined;
                  const preset = text(doc?.preset);
                  return (
                    <div className={cn('ra-row', `tone-${event.tone}`)} key={event.id}>
                      <span className="ra-time">{clockOf(event.time)}</span>
                      <span className="ra-mark" aria-hidden="true" />
                      <div className="ra-main">
                        <div className="ra-line">
                          <span className="ra-kind">{titleCase(event.kind.replace('.', ' '))}</span>
                          {preset ? <span className="ra-preset">{labelOf(snapshot, preset)}</span> : null}
                          {event.about ? (
                            <button type="button" className="ra-subject" onClick={() => openDoc(event.about)}>
                              <span className="trunc">{subjectOf(event)}</span>
                              <ArrowRight size={11} aria-hidden="true" />
                            </button>
                          ) : (
                            <span className="ra-subject flat trunc">{subjectOf(event)}</span>
                          )}
                        </div>
                        <EventDetail event={event} />
                      </div>
                      <span className="ra-actor" title={`recorded by ${event.actor}`}>{event.actor}</span>
                    </div>
                  );
                })}
              </div>
            </section>
          ))
        )}
        {matched.length > visible.length ? (
          <button type="button" className="ra-more" onClick={() => setShown((value) => value + PAGE)}>
            {`Show ${Math.min(PAGE, matched.length - visible.length)} older events · ${matched.length - visible.length} remaining`}
          </button>
        ) : null}
      </main>
      <p className="rf-note">Events are append-only. This screen reads history; it never rewrites it.</p>
    </div>
  );

  function pickKind(next: string) {
    setKind(next);
    setShown(PAGE);
    const values: Record<string, string> = {};
    for (const [key, value] of params) values[key] = value;
    if (next === 'all') delete values.kind;
    else values.kind = next;
    navigate('activity', values);
  }

  function clearFilters() {
    setKind('all');
    setActor('all');
    setQuery('');
    setShown(PAGE);
    const values: Record<string, string> = {};
    for (const [key, value] of params) {
      if (key !== 'kind' && key !== 'actor' && key !== 'q') values[key] = value;
    }
    navigate('activity', values);
  }
}
