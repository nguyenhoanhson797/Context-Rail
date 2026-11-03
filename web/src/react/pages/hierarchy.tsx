/* =====================================================================
   OWNER VIEW: From your goal to the work (read-only).
   Four levels, one tree: Goal bullet -> sub-goal record -> scope item -> task,
   grouped by the Goal's own Sub Goals bullets and never by phase, milestone or
   sprint. The rows render the sourced ui_hierarchy read (authored sub-goal
   title/status/revision and the scope's priority, proof and plan status) and fall
   back to the page's own authored links only when that read does not answer -
   saying which source it used. The authored links are the business chain's
   (scope.primary_subgoal, task.primary_scope); a record without one is listed
   under "outside the chain" with the reason the read reports, never guessed.
   Every row is a drill-down: scope, sub-goal and task records open in the
   document drawer the shell already provides, and a task opens the agent rail at
   that task. Nothing here writes anything.
   ===================================================================== */
import { useQuery } from '@tanstack/react-query';
import { readKey } from '../refresh';
import { docValid } from '../transport';
import { text, type PageProps, type Route, type Snapshot } from '../contracts';
import { Badge } from '../ui';
import { Route as RouteGlyph } from '../icons';
import type { Model } from './atlas/model';
import {
  hierarchyOf, type OwnerScope, type OwnerSubgoal, type OwnerTask, type UnlinkedTask,
} from './hierarchy-model';

export interface OwnerHierarchyProps {
  snapshot: Snapshot;
  model: Model | null;
  project: string;
  params: URLSearchParams;
  read: PageProps['read'];
  busy: boolean;
  navigate: (route: Route, params?: Record<string, string>) => void;
  openDoc: PageProps['openDoc'];
}

function phaseNote(phases: string[]): string {
  return phases.length ? 'historical phase ' + phases.join(', ') : '';
}

function priorityTone(priority: string): 'danger' | 'warning' | 'accent' | 'neutral' {
  if (priority === 'must') return 'danger';
  if (priority === 'should') return 'warning';
  if (priority === 'could') return 'accent';
  return 'neutral';
}

function provenance(scope: OwnerScope): string {
  if (scope.provenKnown === false || scope.proven === null) return 'not assessed';
  return Math.round(scope.proven * 100) + '% proven';
}

function planLabel(scope: OwnerScope): string {
  const plan = scope.plan;
  if (!plan || !plan.known) return '';
  if (plan.reconciled) return 'plan reconciled';
  return plan.status ? 'plan ' + plan.status : '';
}

function TaskLine({ task, navigate }: { task: OwnerTask; navigate: OwnerHierarchyProps['navigate'] }) {
  return (
    <li className="sm-hierarchy-task" data-hierarchy-task={task.id} data-status={task.status}>
      <button type="button" className="link rail-link" aria-label={'Open task ' + task.title}
              onClick={() => navigate('now', { task: task.id })}>
        {task.title}
      </button>
      <span className="faint">{task.status}</span>
      {task.phases.length ? <span className="faint" data-historical-phase={task.phases.join(' ')}>{phaseNote(task.phases)}</span> : null}
    </li>
  );
}

function ScopeBlock({ scope, focus, navigate, openDoc }: {
  scope: OwnerScope;
  focus: string;
  navigate: OwnerHierarchyProps['navigate'];
  openDoc: OwnerHierarchyProps['openDoc'];
}) {
  const plan = planLabel(scope);
  return (
    <div className="sm-hierarchy-scope" data-hierarchy-scope={scope.id}
         data-focus={scope.id === focus ? 'true' : undefined}
         data-scope-code={scope.code || undefined}>
      <div className="sm-hierarchy-scope-h">
        <Badge tone={priorityTone(scope.priority)}>{scope.priority}</Badge>
        {docValid(scope.id) ? (
          <button type="button" className="link rail-link" aria-label={'Open the scope item ' + scope.title}
                  onClick={() => openDoc(scope.id)}>
            {scope.title}{scope.titleTruncated ? '…' : ''}
          </button>
        ) : <span>{scope.title}{scope.titleTruncated ? '…' : ''}</span>}
        <span className="faint mono">{scope.id}</span>
        <span className="faint" data-scope-proven={scope.proven === null ? 'none' : String(scope.proven)}>{provenance(scope)}</span>
        {plan ? <span className="faint" data-scope-plan={scope.plan?.status || 'unknown'}>{plan}</span> : null}
      </div>
      {scope.phases.length ? (
        <p className="faint sm-hierarchy-phase" data-historical-phase={scope.phases.join(' ')}>{phaseNote(scope.phases)}</p>
      ) : null}
      {scope.reason ? <p className="faint" data-scope-reason>{scope.reason}</p> : null}
      {scope.tasks.length ? (
        <ul className="sm-hierarchy-tasks">
          {scope.tasks.map((task) => <TaskLine key={task.id} task={task} navigate={navigate} />)}
        </ul>
      ) : (
        <p className="faint">No task names this scope item as its authored primary scope.</p>
      )}
    </div>
  );
}

function SubgoalHead({ subgoal, sourced, openDoc }: {
  subgoal: OwnerSubgoal;
  sourced: boolean;
  openDoc: OwnerHierarchyProps['openDoc'];
}) {
  const revision = subgoal.revisionBound && subgoal.revision !== null && subgoal.revision !== undefined
    ? 'rev ' + subgoal.revision
    : 'unversioned';
  return (
    <div className="sm-hierarchy-sub-h">
      <Badge tone="accent">Sub-goal</Badge>
      {sourced && subgoal.title ? (
        <span data-hierarchy-subgoal-title={subgoal.title}>{subgoal.title}{subgoal.titleTruncated ? '…' : ''}</span>
      ) : null}
      <span className="mono faint">{subgoal.id}</span>
      {subgoal.status ? <span className="faint" data-hierarchy-subgoal-status={subgoal.status}>{subgoal.status}</span> : null}
      {sourced ? <span className="faint" data-hierarchy-subgoal-revision={revision}>{revision}</span> : null}
      {subgoal.retired ? <Badge tone="warning">retired</Badge> : null}
      {subgoal.staleBinding ? <span className="faint" data-hierarchy-subgoal-binding="stale">parent binding stale</span> : null}
      <button type="button" className="link rail-link" aria-label={'Open the sub-goal record ' + subgoal.id}
              disabled={!docValid(subgoal.id)} onClick={() => openDoc(subgoal.id)}>
        Open the sub-goal record
      </button>
    </div>
  );
}

function UnlinkedTaskLine({ task, navigate }: { task: UnlinkedTask; navigate: OwnerHierarchyProps['navigate'] }) {
  return (
    <li data-unlinked-task={task.id} data-status={task.status}>
      {docValid(task.id) ? (
        <button type="button" className="link rail-link" aria-label={'Open task ' + task.title}
                onClick={() => navigate('now', { task: task.id })}>
          {task.title}
        </button>
      ) : <span>{task.title}</span>}
      <span className="faint">{task.reason}</span>
    </li>
  );
}

export function OwnerHierarchy({ snapshot, model, project, params, read, busy, navigate, openDoc }: OwnerHierarchyProps) {
  const focus = { item: text(params.get('item')), task: text(params.get('task')) };
  const sourced = useQuery({
    queryKey: readKey(project, 'hierarchy'),
    queryFn: ({ signal }) => read('ui_hierarchy', {}, signal),
    retry: false,
    enabled: !busy,
  });
  const H = hierarchyOf(snapshot, model, focus, sourced.data);
  const isRead = H.source === 'read';
  const counts = H.counts;
  const summary = counts.bullets + ' goal ' + (counts.bullets === 1 ? 'bullet' : 'bullets')
    + ' - ' + counts.subgoals + ' sub-' + (counts.subgoals === 1 ? 'goal' : 'goals')
    + ' - ' + counts.scopes + ' scope ' + (counts.scopes === 1 ? 'item' : 'items')
    + ' - ' + counts.tasks + ' ' + (counts.tasks === 1 ? 'task' : 'tasks');
  return (
    <section className="panel sm-section" data-summary="hierarchy" data-hierarchy-source={H.source}
             aria-labelledby="sm-hierarchy">
      <div className="panel-h">
        <RouteGlyph size={15} aria-hidden="true" />
        <h2 id="sm-hierarchy">From your goal to the work</h2>
        <div className="acts"><span className="faint" data-hierarchy-counts={JSON.stringify(counts)}>{summary}</span></div>
      </div>
      <div className="panel-b sm-hierarchy">
        <p className="faint sm-hierarchy-note">
          Grouped by the Goal&apos;s own Sub Goals bullets - not by phase, milestone or sprint.
          Historical phase links stay visible on each row. A record with no authored link is listed
          outside the chain with the reason the read reports, never guessed into it.
        </p>
        {!isRead ? (
          <p className="faint" data-hierarchy="fallback">
            The sourced hierarchy read did not answer, so this view shows the authored links it can
            read from this page. The sub-goal identity is shown without a sourced title until it does.
          </p>
        ) : null}
        <div className="sm-hierarchy-goal" data-hierarchy-goal={H.goal.id}>
          <Badge tone="neutral">Goal</Badge>
          <button type="button" className="link rail-link" disabled={!docValid(H.goal.id)}
                  onClick={() => openDoc(H.goal.id)}>
            Open the Goal record
          </button>
        </div>
        {H.empty ? (
          <p className="faint" data-hierarchy="empty">
            No Goal bullet names a scope item yet, and no task carries an authored scope.
          </p>
        ) : null}
        {H.bullets.map((bullet) => (
          <article className="sm-hierarchy-bullet" key={bullet.id} data-hierarchy-bullet={bullet.id}>
            <h3 className="sm-hierarchy-intent" data-hierarchy-intent>{bullet.text}</h3>
            {bullet.subgoals.map((group) => (
              <div className="sm-hierarchy-sub" key={group.id} data-hierarchy-subgoal={group.id}
                   data-focus={group.id === focus.item ? 'true' : undefined}>
                <SubgoalHead subgoal={group} sourced={isRead} openDoc={openDoc} />
                {group.scopes.map((scope) => (
                  <ScopeBlock key={scope.id} scope={scope} focus={focus.item} navigate={navigate} openDoc={openDoc} />
                ))}
              </div>
            ))}
            {bullet.unbound.length ? (
              <div className="sm-hierarchy-sub" data-hierarchy-subgoal="none">
                <div className="sm-hierarchy-sub-h">
                  <Badge tone="neutral">No authored sub-goal</Badge>
                  <span className="faint">a scope item the bullet names, with no usable primary_subgoal</span>
                </div>
                {bullet.unbound.map((scope) => (
                  <ScopeBlock key={scope.id} scope={scope} focus={focus.item} navigate={navigate} openDoc={openDoc} />
                ))}
              </div>
            ) : null}
          </article>
        ))}
        {H.gaps.length ? (
          <div className="sm-hierarchy-gaps" data-hierarchy-gaps={H.gaps.length}>
            <h3>What the read reports</h3>
            <ul>
              {H.gaps.slice(0, 6).map((gap, index) => (
                <li key={gap.code + ':' + gap.id + ':' + index} data-gap={gap.code} data-gap-id={gap.id}>
                  <span className="mono faint">{gap.id || gap.code}</span> <span className="faint">{gap.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {H.unlinked.scopes.length || H.unlinked.tasks.length ? (
          <div className="sm-hierarchy-unlinked" data-hierarchy-unlinked>
            <h3>Supporting records outside the chain</h3>
            {H.unlinked.scopes.length ? (
              <ul>
                {H.unlinked.scopes.map((entry) => (
                  <li key={entry.scope.id} data-unlinked-scope={entry.scope.id}
                      data-focus={entry.scope.id === focus.item ? 'true' : undefined}>
                    <button type="button" className="link rail-link" aria-label={'Open the scope item ' + entry.scope.title}
                            disabled={!docValid(entry.scope.id)} onClick={() => openDoc(entry.scope.id)}>
                      {entry.scope.title}{entry.scope.titleTruncated ? '…' : ''}
                    </button>
                    <span className="faint">{entry.reason}</span>
                  </li>
                ))}
              </ul>
            ) : null}
            {H.unlinked.tasks.length ? (
              <ul data-unlinked-tasks>
                {H.unlinked.tasks.map((task) => (
                  <UnlinkedTaskLine key={task.id} task={task} navigate={navigate} />
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
