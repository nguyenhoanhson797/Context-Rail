/* The Workflow screen: what is actually moving through this workflow right
   now — in motion, waiting on proof, blocked, not started, done — with the
   legal next move and the gate for each item. The pack's state machine is
   kept, one disclosure down, for the pack author; the lead is for the person
   tracking the project. No pack name is branched on. */
import { useMemo, useState } from 'react';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { Badge, Empty, PageHeading, Panel } from '../ui';
import { labelOf, screenTitle } from './operating';

const list = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];

const ageDays = (iso: string): number | null => {
  const when = Date.parse(iso);
  if (Number.isNaN(when)) return null;
  return Math.floor((Date.now() - when) / 86_400_000);
};

interface FlowItem {
  item: Data;
  id: string;
  title: string;
  preset: string;
  state: string;
  category: string;
  container: string;
  age: number | null;
  priority: string;
}

/* Where a work item lives in the graph — stop at the container that declared
   the parent, never guess one in. */
const containerIds = (phases: Data[]) => new Set(phases.map((phase) => text(phase.id)));
const containerTitleOf = (phases: Data[]) => {
  const byId = new Map(phases.map((phase) => [text(phase.id), text(phase.title, text(phase.id))]));
  return (id: string) => byId.get(id) ?? id;
};

export function WorkflowPage({ snapshot, openDoc }: PageProps) {
  const title = screenTitle(snapshot, 'workflow', 'Workflow');
  const workflows = rows(snapshot.workflows);
  const tasks = snapshot.tasks;
  const phases = snapshot.phases;
  const [selected, setSelected] = useState('');

  const active = useMemo(
    () => workflows.find((entry) => text(entry.name) === selected) ?? workflows[0] ?? null,
    [workflows, selected],
  );

  const { inMotion, waitingOnProof, blocked, notStarted, done, dropped, gateStates } = useMemo(() => {
    const workflowName = text(active?.name);
    const quality = object(active?.quality);
    const gates = Object.entries(quality)
      .filter(([, entry]) => text(object(entry).gate))
      .map(([state]) => state);
    const inWorkflow = tasks.filter((task) => text(task.workflow) === workflowName);
    const byId = containerIds(phases);
    const titleOfContainer = containerTitleOf(phases);
    const itemOf = (task: Data): FlowItem => {
      const id = text(task.id);
      const parent = text(task.phase) || text(rows(object(task.relations).part_of)[0]) || text(rows(object(task.fields).part_of)[0]);
      return {
        item: task,
        id,
        title: text(task.title, id),
        preset: text(task.preset),
        state: text(task.status),
        category: text(task.category),
        container: parent && byId.has(parent) ? titleOfContainer(parent) : '',
        age: ageDays(text(task.updated)),
        priority: text(object(task.fields).priority, text(task.priority)),
      };
    };
    const buckets: Record<string, FlowItem[]> = {};
    for (const task of inWorkflow) {
      const state = text(task.status);
      (buckets[state] ??= []).push(itemOf(task));
    }
    /* A state maps to exactly one category, so bucket by it. */
    const toGroups = (entries: [string, FlowItem[]][]) =>
      entries.map(([state, list]) => ({ state, list }));
    const byCategory = (cat: string) =>
      toGroups(Object.entries(buckets).filter(([, items]) => items[0]?.category === cat));
    return {
      inMotion: byCategory('active'),
      waitingOnProof: toGroups(
        Object.entries(buckets).filter(
          ([state, items]) => gates.includes(state) && items[0]?.category !== 'done',
        ),
      ),
      blocked: byCategory('blocked'),
      notStarted: byCategory('not_started'),
      done: byCategory('done'),
      dropped: byCategory('dropped'),
      gateStates: gates,
    };
  }, [tasks, phases, active]);

  const count = (groups: { state: string; list: FlowItem[] }[]) =>
    groups.reduce((sum, group) => sum + group.list.length, 0);

  const total = count(inMotion) + count(waitingOnProof) + count(blocked) + count(notStarted) + count(done) + count(dropped);

  const itemRow = (entry: FlowItem) => (
    <div
      className="mini"
      key={entry.id}
      role="button"
      tabIndex={0}
      onClick={() => openDoc(entry.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') openDoc(entry.id);
      }}
    >
      <span className="grow trunc" title={entry.title}>{entry.title}</span>
      {entry.priority ? <Badge tone="accent">{entry.priority}</Badge> : null}
      {entry.container ? <span className="faint" style={{ fontSize: 12 }}>{entry.container}</span> : null}
      <span className="faint" style={{ fontSize: 12 }}>{entry.preset ? labelOf(snapshot, entry.preset) : ''}</span>
      {entry.age !== null && entry.age >= 14 ? (
        <span className="faint" style={{ fontSize: 12 }} title="Days since the document was last touched">{entry.age}d</span>
      ) : null}
    </div>
  );

  const groupRow = (title: string, groups: { state: string; list: FlowItem[] }[], tone: 'accent' | 'warning' | 'danger' | 'neutral' | 'good', note?: string) => (
    <Panel key={title} title={`${title} · ${count(groups)}`}>
      {groups.map(({ state, list }) => (
        <div key={state}>
          <div className="faint" style={{ fontSize: 12, margin: '4px 0 6px' }}>
            <Badge tone={tone}>{state}</Badge>
            {note ? <span> {note}</span> : null}
          </div>
          {list.map(itemRow)}
        </div>
      ))}
    </Panel>
  );

  return (
    <div className="stack">
      <PageHeading
        title={title}
        description="What is moving, waiting or stuck in this workflow — and the gate that finishes it."
      />

      {workflows.length > 1 ? (
        <div className="row" style={{ gap: 8 }}>
          <label className="faint" htmlFor="workflow-pick">Workflow</label>
          <select
            className="select"
            id="workflow-pick"
            value={text(active?.name)}
            onChange={(event) => setSelected(event.target.value)}
          >
            {workflows.map((entry) => (
              <option key={text(entry.name)} value={text(entry.name)}>
                {text(entry.name)}
                {' ('}
                {tasks.filter((task) => text(task.workflow) === text(entry.name)).length}
                {')'}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {active ? (
        total === 0 ? (
          <Empty title={`Nothing in ${text(active.name)} yet`}>
            {`Start one with rail change add <preset> "..." — the pack's own verb tells you the first legal move.`}
          </Empty>
        ) : (
          <>
            {gateStates.length ? (
              <Panel title="Proof owed at the finish line">
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  {gateStates.map((state) => {
                    const quality = object(object(active.quality)[state]);
                    const gate = text(quality.gate);
                    const checks = list(quality.checks);
                    return (
                      <span key={state} className="chip">
                        <strong>{state}</strong> needs {gate}
                        {checks.length ? <> · {checks.join(', ')}</> : null}
                      </span>
                    );
                  })}
                </div>
              </Panel>
            ) : null}
            {groupRow('In motion', inMotion, 'accent', 'actively being worked')}
            {waitingOnProof.length ? groupRow('Waiting on proof', waitingOnProof, 'warning', 'in a finishing state — the gate decides') : null}
            {groupRow('Blocked', blocked, 'danger')}
            {groupRow('Not started', notStarted, 'neutral')}
            {groupRow('Done', done, 'good')}
            {dropped.length ? <div className="faint" style={{ fontSize: 13 }}>{count(dropped)} dropped or out of scope — never counted as done.</div> : null}
          </>
        )
      ) : (
        <Empty title="No workflows declared">This pack uses the default workflow.</Empty>
      )}

      {active ? (
        <details className="panel" style={{ padding: '10px 14px' }}>
          <summary className="faint" style={{ cursor: 'pointer', fontSize: 13 }}>
            What this workflow allows (the definition)
          </summary>
          <div style={{ overflowX: 'auto', marginTop: 10 }}>
            <table className="table" style={{ width: '100%', fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', fontWeight: 500 }}>From</th>
                  <th style={{ textAlign: 'left', fontWeight: 500 }}>Can go to</th>
                  <th style={{ textAlign: 'left', fontWeight: 500 }}>Quality gate</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(object(active.transitions)).map(([from, targets]) => {
                  const quality = object(object(active.quality)[from]);
                  const gate = text(quality.gate);
                  const checks = list(quality.checks);
                  return (
                    <tr key={from}>
                      <td><Badge tone="neutral">{from}</Badge></td>
                      <td>{list(targets).join('  →  ') || '—'}</td>
                      <td>
                        {gate ? <code>{gate}</code> : <span className="faint">—</span>}
                        {checks.length ? <span className="faint"> · {checks.join(', ')}</span> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  );
}

export type WorkflowData = Data;
