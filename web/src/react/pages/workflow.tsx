/* The Workflow screen: the pack's state machines, drawn as text diagrams.
   It reads snapshot.workflows — every workflow the pack declares, with its
   initial state, transitions and quality gates. No pack name is branched on:
   a Scrum pack shows sprint + bugfix, a Video pack shows scene + shot. */
import { useMemo, useState } from 'react';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { Badge, Empty, PageHeading, Panel } from '../ui';
import { labelOf, screenTitle } from './operating';

const list = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];

export function WorkflowPage({ snapshot }: PageProps) {
  const title = screenTitle(snapshot, 'workflow', 'Workflow');
  const workflows = rows(snapshot.workflows);
  const [selected, setSelected] = useState('');
  const active = useMemo(
    () => workflows.find((entry) => text(entry.name) === selected) ?? workflows[0] ?? null,
    [workflows, selected],
  );

  return (
    <div>
      <PageHeading
        title={title}
        description="How work moves in this pack: states, allowed moves, and the gate on each finishing state."
      />

      {workflows.length > 1 ? (
        <div className="row" style={{ gap: 8, marginBottom: 12 }}>
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
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {active ? (
        <Panel title={text(active.name, 'Workflow')}>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
            <span className="faint" style={{ fontSize: 12 }}>Initial</span>
            <Badge tone="accent">{text(active.initial)}</Badge>
            <span className="faint" style={{ fontSize: 12 }}>
              {list(active.presets).map((preset) => labelOf(snapshot, preset, true)).join(', ')}
            </span>
          </div>

          <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
            {list(active.states).map((state) => (
              <span key={state} className="chip">
                {state}
              </span>
            ))}
          </div>

          <div style={{ overflowX: 'auto' }}>
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
                      <td>
                        <Badge tone="neutral">{from}</Badge>
                      </td>
                      <td>{list(targets).join('  →  ') || '—'}</td>
                      <td>
                        {gate ? (
                          <>
                            <code>{gate}</code>
                            {checks.length ? <span className="faint"> · {checks.join(', ')}</span> : null}
                          </>
                        ) : (
                          <span className="faint">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="faint" style={{ fontSize: 12, marginTop: 8 }}>
            Chain: {list(active.states).join(' → ')}
          </p>
        </Panel>
      ) : (
        <Empty title="No workflows declared">This pack uses the default workflow.</Empty>
      )}
    </div>
  );
}

export type WorkflowData = Data;
