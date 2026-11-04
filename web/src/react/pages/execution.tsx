/* The Execution screen: what the pack treats as progress beyond statuses.
   It reads snapshot.operating.progress.runs — the execution records the
   engine keeps separate from workflow completion — plus the declared
   execution actions (e.g. Video's `render`). Completion counts workflow
   states; execution progress comes from runs only and is never merged. */
import { object, rows, text, type PageProps } from '../contracts';
import { Badge, Empty, PageHeading, Panel } from '../ui';
import { screenTitle } from './operating';

export function ExecutionPage({ snapshot }: PageProps) {
  const title = screenTitle(snapshot, 'execution', 'Execution');
  const operating = object(snapshot.operating);
  const progress = object(operating.progress);
  const runs = object(progress.runs);
  const byState = object(runs.by_state);
  const actions = Array.isArray(object(operating.execution).actions)
    ? object(operating.execution).actions as string[]
    : [];

  const succeeded = text(byState.succeeded, '0');
  const failed = text(byState.failed, '0');
  const running = text(byState.running, '0');
  const hasRuns = Object.keys(runs).length > 0;

  return (
    <div>
      <PageHeading
        title={title}
        description="Execution records the pack declares as progress. They are kept separate from workflow completion and never merged into it."
      />

      <Panel title="Execution actions">
        {actions.length ? (
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            {actions.map((action) => (
              <Badge key={action} tone="accent">{action}</Badge>
            ))}
          </div>
        ) : (
          <div className="faint">No execution actions declared. Runs do not count as progress in this pack.</div>
        )}
      </Panel>

      {hasRuns ? (
        <div className="stats" style={{ marginBottom: 16 }}>
          <div className="stat">
            <span className="k">Succeeded</span>
            <span className="v">{succeeded}</span>
            <span className="d">recorded runs</span>
          </div>
          <div className="stat">
            <span className="k">Failed</span>
            <span className="v" style={failed !== '0' ? { color: 'var(--red)' } : undefined}>{failed}</span>
            <span className="d">need a look</span>
          </div>
          <div className="stat">
            <span className="k">Running</span>
            <span className="v">{running}</span>
            <span className="d">in flight</span>
          </div>
        </div>
      ) : (
        <Empty title="No execution runs yet">When the pack's declared actions run, their results appear here.</Empty>
      )}

      {hasRuns && (
        <Panel title="Runs by state">
          {Object.entries(byState).map(([state, count]) => (
            <div className="mini" key={state}>
              <span className="grow">{state}</span>
              <Badge tone={state === 'failed' ? 'danger' : state === 'succeeded' ? 'good' : 'neutral'}>{text(count)}</Badge>
            </div>
          ))}
        </Panel>
      )}

      {rows(progress.children).length ? (
        <Panel title={`Progress · ${rows(progress.children).length} groups`}>
          {rows(progress.children).map((child, index) => (
            <div className="mini" key={index}>
              <span className="grow trunc" title={text(child.title, text(child.slug))}>
                {text(child.title, text(child.slug, text(child.id, `Group ${index + 1}`)))}
              </span>
              <span className="faint" style={{ fontSize: 12 }}>
                {text(child.complete, '0')}/{text(child.total, '0')}
              </span>
            </div>
          ))}
        </Panel>
      ) : null}
    </div>
  );
}
