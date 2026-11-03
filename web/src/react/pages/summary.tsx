/* =====================================================================
   PAGE: Summary - the owner's layer of the two-layer rail.
   The project in plain words: where it stands, what needs the owner, what
   changed and what comes next. It reads the `atlas` tool through the shell's
   read() with the Atlas's own query key, so both pages share one read. Nothing
   here is written by an agent or the owner: every line is composed from the
   store's records and opens the record it came from. React escapes every value.
   ===================================================================== */
import { useMemo, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { object, rows, text, type Data, type PageProps, type Route } from '../contracts';
import { Badge, Button, ErrorState, Loading, PageHeading } from '../ui';
import { docValid } from '../transport';
import { readKey } from '../refresh';
import { CircleCheck, History, House, Loader, MapIcon, Route as RouteGlyph, ShieldCheck, Terminal, TriangleAlert, Users, Flag } from '../icons';
import { dayNum, fmtDay, modelOf, type Model, type Program, type Task } from './atlas/model';
import {
  EVIDENCE, countWord, evidenceSentence, evidenceWord, fetchOwnerSummary,
  gateSentence, ownerSummaryKey, ownerSummaryOf, type OwnerSummary,
} from './evidence';
import { OwnerHierarchy } from './hierarchy';

/* How many rows each section shows before the rest move behind a closed disclosure. */
export const NEEDS_PREVIEW = 3;
const LANDED_PREVIEW = 5;
const INFLIGHT_PREVIEW = 3;

type Tone = 'danger' | 'warning' | 'accent' | 'neutral';
export interface Need { key: string; kind: string; label: string; tone: Tone; title: string; id: string; hint: string }
const NEED_KINDS: Record<string, [string, Tone]> = {
  blocked_task: ['Blocked', 'danger'],
  dispatch_failed: ['Hand-off failed', 'danger'],
  question: ['Question for you', 'warning'],
  risk: ['High risk', 'warning'],
  expired_claim: ['Agent stopped', 'warning'],
  decision: ['Your call', 'accent'],
};

/* What needs the owner: the queue's escalations first (in the order next ranks them), then decisions still waiting for a choice. */
export function needsOf(M: Model): Need[] {
  const out: Need[] = [];
  const seen = new Set<string>();
  const add = (kind: string, id: string, title: string, hint: string) => {
    const key = kind + ':' + id;
    if (!id || seen.has(key)) return;
    seen.add(key);
    const [label, tone] = NEED_KINDS[kind] ?? ['Needs a look', 'warning'];
    out.push({ key, kind, label, tone, title: title || id, id, hint });
  };
  for (const e of M.loops.escalate) {
    if (e.kind === 'expired_claim') {
      const task = M.TASK.get(e.id);
      add(e.kind, e.id, 'An agent stopped working on "' + (task ? task.title : e.id) + '" and still holds it', e.command);
    } else add(e.kind, e.id, e.title, e.command);
  }
  for (const d of M.loops.proposed) add('decision', d.id, d.title, 'A proposed decision is waiting for a choice.');
  return out;
}

/* The agents' next step, in words: one sentence per state `next` reports. */
export const STATE_TEXT: Record<string, string> = {
  working: 'Agents have work they can start.',
  blocked: 'Work is waiting on you.',
  stuck: 'Agents are stuck and need a look.',
  done: 'Every must-have and should-have is proven by checks that ran.',
};
const QUEUE_WORDS: Record<string, [string, string]> = {
  rerun: ['check to re-run', 'checks to re-run'],
  fix: ['guarantee to fix', 'guarantees to fix'],
  repair: ['record to repair', 'records to repair'],
  record: ['result to record', 'results to record'],
  resume: ['task to resume', 'tasks to resume'],
  plan: ['plan to write', 'plans to write'],
  start: ['task to start', 'tasks to start'],
  define: ['item to define', 'items to define'],
};
export function queueWords(M: Model): string {
  const tally = new Map<string, number>();
  for (const q of M.queue) if (QUEUE_WORDS[q.kind]) tally.set(q.kind, (tally.get(q.kind) || 0) + 1);
  const parts = [...tally].sort((a, b) => b[1] - a[1]).map(([k, n]) => n + ' ' + QUEUE_WORDS[k][n === 1 ? 0 : 1]);
  return parts.join(', ');
}

/* A capability's state in one word or two, from the Atlas's own reading of the lane. */
export function capabilityState(p: Program): string {
  if (p.vis === 'done') return 'finished';
  if (p.vis === 'blocked') return 'blocked';
  if (p.vis === 'later') return 'later';
  if (p.vis === 'built') return 'built, being proven';
  if (p.vis === 'now') return 'most active';
  return p.tasks['in-progress'] > 0 ? 'in progress' : p.status === 'active' ? 'started' : 'planned';
}

/* Tasks finished in the seven days up to the read, from the read's own pace rows. */
export function finishedThisWeek(M: Model): number | null {
  if (!M.asOfDay || !M.pace.length) return null;
  const end = dayNum(M.asOfDay);
  return M.pace.filter((p) => end - dayNum(p.date) >= 0 && end - dayNum(p.date) < 7).reduce((sum, p) => sum + p.done, 0);
}

const plural = (n: number, one: string, many = one + 's') => n + ' ' + (n === 1 ? one : many);

function Meter({ value, total, label }: { value: number; total: number; label: string }) {
  const pct = total > 0 ? Math.max(0, Math.min(100, Math.round((value / total) * 100))) : 0;
  return (
    <span className="sm-meter" role="img" aria-label={label}>
      <span className="sm-meter-fill" style={{ width: pct + '%' }} />
    </span>
  );
}

function SourceLink({ id, children, openDoc }: { id: string; children: ReactNode; openDoc: (id: string) => void }) {
  return docValid(id) ? (
    <button type="button" className="link rail-link sm-src" onClick={() => openDoc(id)}>
      {children}
    </button>
  ) : (
    <span className="sm-src">{children}</span>
  );
}

function NeedRow({ need, openDoc }: { need: Need; openDoc: (id: string) => void }) {
  return (
    <div className="sm-row sm-need" data-need-kind={need.kind} title={need.hint || undefined}>
      <Badge tone={need.tone}>{need.label}</Badge>
      <SourceLink id={need.id} openDoc={openDoc}>{need.title}</SourceLink>
    </div>
  );
}

function TaskRow({ task, M, openDoc, when }: { task: Task; M: Model; openDoc: (id: string) => void; when?: string }) {
  const lane = M.P.get(task.program);
  return (
    <div className="sm-row" data-task-id={task.id}>
      <SourceLink id={task.id} openDoc={openDoc}>{task.title}</SourceLink>
      <span className="faint sm-row-meta">{[lane ? lane.label : '', when || ''].filter(Boolean).join(' · ')}</span>
    </div>
  );
}

function Section({ name, icon, title, count, children }: { name: string; icon: ReactNode; title: string; count?: string; children: ReactNode }) {
  return (
    <section className="panel sm-section" data-summary={name} aria-labelledby={'sm-' + name}>
      <div className="panel-h">
        {icon}
        <h2 id={'sm-' + name}>{title}</h2>
        {count ? <div className="acts"><span className="faint">{count}</span></div> : null}
      </div>
      {children}
    </section>
  );
}

/* ---------- needs-review and required closeout ----------
   Both counts come from ui_owner_summary, the one read that owns them: the
   corrections count uses the same undecided predicate, and required closeout is
   the newest durable closeout record per done task. A null count or a failed
   read stays unknown with the read's reason - never 0 - and the closeout list
   is bounded by the read, so truncation and recordless tasks are said out loud. */
export function OwnerCounts({ owner, loading, error, navigate }: {
  owner: OwnerSummary | null;
  loading: boolean;
  error: string;
  navigate: PageProps['navigate'];
}) {
  if (loading) return <p className="faint" data-owner-summary="pending">Reading the owner summary...</p>;
  if (error || !owner) {
    return (
      <div data-owner-summary="error">
        <p className="faint">{'Pending corrections: ' + evidenceWord('unknown') + ' - ' + (error || 'the owner summary read has not answered') + '.'}</p>
        <p className="faint">{'Required closeout: ' + evidenceWord('unknown') + ' - ' + (error || 'the owner summary read has not answered') + '.'}</p>
      </div>
    );
  }
  const summary = owner;
  const sentence = (value: string | null): string => {
    if (!value) return 'the read did not report a reason.';
    return /[.!?]$/.test(value) ? value : value + '.';
  };
  const corrections = summary.corrections === null
    ? 'Pending corrections: ' + evidenceWord('unknown') + ' - ' + sentence(summary.correctionsReason)
    : 'Pending corrections: ' + countWord(summary.corrections, 'correction')
      + (summary.corrections ? ' waiting for an adopt or retire decision.' : ' - nothing is waiting for a decision.');
  const closeout = summary.closeout === null
    ? 'Required closeout: ' + evidenceWord('unknown') + ' - ' + sentence(summary.closeoutReason)
    : 'Required closeout: ' + countWord(summary.closeout, 'task')
      + (summary.closeoutTruncated && summary.closeoutTotal !== null && summary.closeout > 0 ? ' (the read lists only the first one)' : '');
  return (
    <div className="sm-rows" data-owner-summary={summary.state}>
      <p data-review="corrections" data-count={summary.corrections === null ? undefined : summary.corrections}>
        {corrections}
        {summary.corrections ? <> <button type="button" className="link rail-link" onClick={() => navigate('rules')}>Review them</button></> : null}
      </p>
      <p data-review="closeout" data-count={summary.closeout === null ? undefined : summary.closeout}>
        {closeout}
        {summary.closeout !== null && summary.closeoutUnknown ? (
          <span className="faint"> {' · ' + countWord(summary.closeoutUnknown, 'done task') + ' with no record: unknown, not complete.'}</span>
        ) : null}
        {' '}<Button variant="ghost" className="btn-sm" onClick={() => navigate('now')}>Open a task</Button>
      </p>
    </div>
  );
}

export function SummaryPage({ snapshot, project, navigate, openDoc, read, busy }: PageProps) {
  const next = useQuery({ queryKey: readKey(project, 'next'), queryFn: ({ signal }) => read('next', {}, signal), retry: false, enabled: !busy });
  const idOf = (doc: Data) => text(doc.id);
  const tasks = snapshot.tasks;
  const workflows = rows(snapshot.workflows);
  const counts = object(snapshot.counts);
  const open = tasks.filter((task) => !['done', 'dropped'].includes(text(task.category)));
  const active = tasks.filter((task) => text(task.category) === 'active');
  const blocked = tasks.filter((task) => text(task.category) === 'blocked');
  const queue = rows(object(next.data).queue);
  const approval = text(snapshot.project.state, 'missing');
  const goal = text(snapshot.project.goal);
  const phaseDone = snapshot.phases.filter((phase) => text(phase.state) === 'done').length;
  const recent = [...tasks].sort((a, b) => text(b.updated).localeCompare(text(a.updated))).slice(0, 5);
  const workflowCount = (name: string) => tasks.filter((task) => text(task.workflow) === name).length;

  const go = (route: Route, params?: Record<string, string>) => navigate(route, params);
  return (
    <div className="understanding">
      <PageHeading
        title="Summary"
        description="Where the project stands, in plain words: what needs you, what is moving and what comes next."
        action={<Button onClick={() => go('board')}>Open the board</Button>}
      />
      <div className="stats" style={{ marginBottom: 16 }}>
        <div className="stat">
          <span className="k"><Terminal size={14} aria-hidden="true" />Open</span>
          <span className="v">{open.length}</span>
          <span className="d">{tasks.length - open.length} of {tasks.length} items closed</span>
        </div>
        <div className="stat">
          <span className="k"><Loader size={14} aria-hidden="true" />In motion</span>
          <span className="v">{active.length}</span>
          <span className="d">{active.length ? 'work is being done' : 'nothing is started'}</span>
        </div>
        <div className="stat">
          <span className="k"><TriangleAlert size={14} aria-hidden="true" />Blocked</span>
          <span className="v" style={blocked.length ? { color: 'var(--red)' } : undefined}>{blocked.length}</span>
          <span className="d">{blocked.length ? 'waiting to be unblocked' : 'nothing is stuck'}</span>
        </div>
        <button type="button" className="stat" onClick={() => go('roadmap')}>
          <span className="k"><RouteGlyph size={14} aria-hidden="true" />Roadmap</span>
          <span className="v">{snapshot.phases.length}</span>
          <span className="d">{phaseDone} of {snapshot.phases.length} containers done</span>
        </button>
      </div>
      <div className="grid2">
        <div className="stack">
          <Section name="where" icon={<Flag size={15} aria-hidden="true" />} title="Where we are">
            <div className="sm-rows">
              <p>{goal || 'No goal document yet.'}</p>
              <p className="faint">
                Pack <b>{text(snapshot.pack)}</b> · approval <b>{approval}</b>
                {approval !== 'approved' ? ' · rail approve charter' : ''}
              </p>
            </div>
          </Section>
          <Section name="needs" icon={<TriangleAlert size={15} aria-hidden="true" />} title="What needs you" count={blocked.length ? plural(blocked.length, 'item') : 'none'}>
            <div className="sm-rows">
              {blocked.length ? blocked.map((task) => (
                <div className="sm-row sm-need" key={idOf(task)} data-need-kind="blocked_task">
                  <Badge tone="danger">Blocked</Badge>
                  <SourceLink id={idOf(task)} openDoc={openDoc}>{text(task.title)}</SourceLink>
                </div>
              )) : <p className="faint">Nothing is blocked.</p>}
              {approval !== 'approved' ? (
                <div className="sm-row sm-need" data-need-kind="approval">
                  <Badge tone="warning">Approval</Badge>
                  <span>The charter is {approval}; run <code>rail approve charter</code>.</span>
                </div>
              ) : null}
            </div>
          </Section>
          <Section name="work" icon={<Loader size={15} aria-hidden="true" />} title="Work in motion" count={active.length ? plural(active.length, 'item') : undefined}>
            <div className="sm-rows">
              {active.length ? active.slice(0, INFLIGHT_PREVIEW).map((task) => (
                <div className="sm-row" key={idOf(task)} data-task-id={idOf(task)}>
                  <SourceLink id={idOf(task)} openDoc={openDoc}>{text(task.title)}</SourceLink>
                  <span className="faint sm-row-meta">{text(task.workflow)} · {text(task.status)}</span>
                </div>
              )) : <p className="faint">Nothing is in progress.</p>}
            </div>
          </Section>
        </div>
        <div className="stack">
          <Section name="next" icon={<RouteGlyph size={15} aria-hidden="true" />} title="What comes next" count={queue.length ? plural(queue.length, 'item') : undefined}>
            <div className="sm-rows">
              {next.isError ? <p className="faint">{next.error.message}</p> : null}
              {queue.length ? queue.slice(0, 5).map((entry) => (
                <div className="sm-row" key={idOf(entry)} data-task-id={idOf(entry)}>
                  <SourceLink id={idOf(entry)} openDoc={openDoc}>{text(entry.title, idOf(entry))}</SourceLink>
                  <span className="faint sm-row-meta">{text(entry.workflow)} · {text(entry.status)}</span>
                </div>
              )) : !next.isPending ? <p className="faint">Nothing is executable right now.</p> : null}
            </div>
          </Section>
          <Section name="workflows" icon={<Users size={15} aria-hidden="true" />} title="Workflows">
            <div className="sm-rows">
              {workflows.length ? workflows.map((workflow) => (
                <div className="sm-row" key={text(workflow.name)}>
                  <button type="button" className="link rail-link" onClick={() => go('board', { workflow: text(workflow.name) })}>
                    {text(workflow.name)}
                  </button>
                  <span className="faint sm-row-meta">{workflowCount(text(workflow.name))} items · {Object.keys(object(workflow.quality)).length} gates</span>
                </div>
              )) : <p className="faint">This pack declares no workflows.</p>}
            </div>
          </Section>
          <Section name="changed" icon={<History size={15} aria-hidden="true" />} title="Recently updated">
            <div className="sm-rows">
              {recent.length ? recent.map((task) => (
                <div className="sm-row" key={idOf(task)} data-task-id={idOf(task)}>
                  <SourceLink id={idOf(task)} openDoc={openDoc}>{text(task.title)}</SourceLink>
                  <span className="faint sm-row-meta">{text(task.updated).slice(0, 10)}</span>
                </div>
              )) : <p className="faint">No items yet.</p>}
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
