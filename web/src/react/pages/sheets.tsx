/* Overlays answer a question without becoming a destination. Each one reads
   the compiled facts it explains; an absent fact removes its section instead
   of leaving an empty control behind. */
import { useRef, type CSSProperties, type ReactNode } from 'react';
import { X } from '../icons';
import { object, rows, text, type Health, type PageProps } from '../contracts';
import { useModalFocus, useOverlayEscape } from '../overlays';
import {
  completionOf,
  completionSentence,
  declaredActions,
  executionActions,
  focusPolicyOf,
  focusState,
  gateFor,
  gateStatuses,
  hasActionPreconditions,
  hasExecution,
  hasFocus,
  hasOptionalHierarchy,
  hasOrderedHierarchy,
  hasPerPresetCompletion,
  hierarchyLevels,
  workflowFacts,
  type GateFact,
} from '../capabilities';
import { focusName, labelOf, screenTitle, titleCase, unitLabel } from './operating';

function RailSheet({
  title,
  kicker,
  onClose,
  children,
  footer,
  wide = false,
}: {
  title: string;
  kicker?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLElement>(null);
  useOverlayEscape(35, onClose);
  const trap = useModalFocus(ref, '[data-first]');
  return (
    <>
      <div className="scrim rs-scrim" onClick={onClose} />
      <section
        ref={ref}
        className={'rs-sheet' + (wide ? ' wide' : '')}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onKeyDown={trap}
      >
        <header className="rs-head">
          <div>
            {kicker ? <div className="rf-kicker">{kicker}</div> : null}
            <h2>{title}</h2>
          </div>
          <button type="button" className="ibtn ibtn-sm" aria-label="Close" onClick={onClose}>
            <X size={16} strokeWidth={1.8} />
          </button>
        </header>
        <div className="rs-body" data-first tabIndex={-1}>
          {children}
        </div>
        {footer ? <footer className="rs-foot">{footer}</footer> : null}
      </section>
    </>
  );
}

function RuleList({ snapshot, workflow, gates }: { snapshot: PageProps['snapshot']; workflow: string; gates: Record<string, GateFact | null> }) {
  const fact = workflowFacts(snapshot).find((entry) => text(entry.name) === workflow);
  const states = fact && Array.isArray(fact.states) ? (fact.states as unknown[]).filter((value): value is string => typeof value === 'string') : [];
  return (
    <div className="rs-workflow">
      <div className="rs-workflow-h">
        <b>{workflow}</b>
        <span>{states.length} states</span>
      </div>
      <div className="rs-states">
        {states.map((state) => {
          const gate = gates[state];
          return (
            <span key={state} className={'rs-state' + (gate ? ' gated' : '')}>
              {titleCase(state)}
              {gate ? <em>{gate.gate}</em> : null}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function ProgressSheet({ snapshot, onClose }: { snapshot: PageProps['snapshot']; onClose: () => void }) {
  const progress = object(object(snapshot.operating).progress);
  const children = rows(progress.children);
  const byCategory = object(progress.by_category);
  const total = Number(text(progress.total, '0'));
  const complete = Number(text(progress.complete, '0'));
  const hasPopulation = total > 0 || children.length > 0 || Object.keys(byCategory).length > 0;
  const rule = completionOf(snapshot);
  const runs = object(progress.runs);
  const runStates = object(runs.by_state);
  const actions = Array.isArray(runs.actions) ? (runs.actions as unknown[]).filter((value): value is string => typeof value === 'string') : executionActions(snapshot);
  const groupLabel = rule.group ? labelOf(snapshot, rule.group, true) : 'groups';
  const title = screenTitle(snapshot, 'execution', 'Progress');
  const pct = total > 0 ? Math.round((complete / total) * 100) : 0;
  return (
    <RailSheet
      title={title}
      kicker={`${complete}/${total} ${unitLabel(snapshot, true).toLowerCase()}`}
      onClose={onClose}
      wide
      footer={
        <>
          <span>{hasExecution(snapshot) ? `execution: ${actions.join(', ') || 'declared'}` : 'no execution actions declared'}</span>
          <span className="rs-sep" />
          <span>execution is never counted as done</span>
          <span className="sp" />
          <span>no ETA</span>
        </>
      }
    >
      {!hasPopulation ? (
        <div className="rs-empty">
          <b>No progress population</b>
          <span>The pack could not compute progress. Check <code>rail model</code> and <code>rail ops doctor</code>.</span>
        </div>
      ) : (
        <>
          <div className="rs-rule">{completionSentence(snapshot)}</div>
          {hasPerPresetCompletion(snapshot) ? (
            <div className="rs-preset-rules">
              {Object.entries(rule.presets).map(([preset, states]) => (
                <div key={preset} className="rs-preset-rule">
                  <b>{labelOf(snapshot, preset, true)}</b>
                  <span>{states.join(' or ')}</span>
                </div>
              ))}
            </div>
          ) : null}
          <div className="rs-progressbar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Completion">
            <i style={{ '--p': pct / 100 } as CSSProperties} />
          </div>
          {children.length ? (
            <section className="rs-section">
              <h3>By {groupLabel} · {children.length}</h3>
              {children.map((child) => {
                const childTotal = Number(text(child.total, '0'));
                const childComplete = Number(text(child.complete, '0'));
                const share = childTotal > 0 ? (childComplete / childTotal) * 100 : 0;
                return (
                  <div className="rs-bar" key={text(child.slug, text(child.title))}>
                    <span className="rs-bar-name trunc" title={text(child.title, text(child.slug))}>{text(child.title, text(child.slug))}</span>
                    <span className="rs-track"><i style={{ width: `${share}%` }} /></span>
                    <span className="rs-bar-num">{childComplete}/{childTotal}</span>
                  </div>
                );
              })}
            </section>
          ) : null}
          <section className="rs-section">
            <h3>By category</h3>
            <div className="rs-cats">
              {Object.entries(byCategory).map(([category, count]) => (
                <div className="rs-cat" key={category}>
                  <span className={'rs-cat-dot rs-cat-' + category} />
                  <span>{titleCase(category.replace(/_/g, ' '))}</span>
                  <b>{text(count)}</b>
                </div>
              ))}
            </div>
          </section>
          {hasExecution(snapshot) ? (
            <section className="rs-section">
              <h3>Execution · never merged</h3>
              {Number(text(runs.total, '0')) > 0 ? (
                <div className="rs-runs">
                  {Object.entries(runStates).map(([state, count]) => (
                    <div className="rs-run" key={state}>
                      <span className={'rs-cat-dot rs-run-' + state} />
                      <span>{titleCase(state)}</span>
                      <b>{text(count)}</b>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="rs-note">No {actions.join(' or ') || 'execution'} runs yet. When one runs, its state appears here and counts only as execution.</p>
              )}
            </section>
          ) : null}
        </>
      )}
    </RailSheet>
  );
}

export function HealthSheet({ health, onClose }: { health: Health | undefined; onClose: () => void }) {
  const doctor = object(health?.doctor);
  const counts = object(doctor.by_severity);
  const issues = rows(doctor.issues);
  const state = health?.state ?? 'unknown';
  const errors = Number(text(counts.error, '0'));
  const warnings = Number(text(counts.warning, '0'));
  const clean = state === 'ok' && errors === 0 && warnings === 0;
  return (
    <RailSheet
      title="Health"
      kicker={health?.as_of ? 'as of ' + new Date(health.as_of).toLocaleTimeString() : 'no dated read'}
      onClose={onClose}
      footer={
        <>
          <span>codes and remedies live together</span>
          <span className="sp" />
          <span>{health?.reasons?.[0] ?? 'no silent zeros'}</span>
        </>
      }
    >
      {!health ? (
        <p className="rs-note">Checking health…</p>
      ) : state === 'error' || !doctor || !Object.keys(doctor).length ? (
        <div className="rs-empty bad">
          <b>{health?.error_code || 'HEALTH_UNAVAILABLE'}</b>
          <span>{health?.error || health?.reasons?.[0] || 'The diagnostics read did not complete.'}</span>
        </div>
      ) : (
        <>
          <div className={'rs-healthstate ' + (clean ? 'ok' : errors ? 'bad' : 'warn')}>
            <span className="rs-health-dot" />
            <div>
              <b>{clean ? 'No findings' : `${errors} errors · ${warnings} warnings`}</b>
              <span>{doctor.total !== undefined ? `${text(doctor.total)} findings` : 'counted by severity'}</span>
            </div>
          </div>
          {issues.map((issue) => (
            <div className="rs-finding" key={text(issue.code) + text(issue.path)}>
              <div className="rs-finding-h">
                <b>{text(issue.code, 'FINDING')}</b>
                <span className={'rs-sev rs-sev-' + text(issue.severity)}>{text(issue.severity)}</span>
              </div>
              <p>{text(issue.message)}</p>
              {text(issue.path) ? <div className="rs-finding-path">{text(issue.path)}</div> : null}
              {text(issue.fix) ? <div className="rs-remedy"><span>remedy</span>{text(issue.fix)}</div> : null}
            </div>
          ))}
          {!issues.length ? (
            <div className="rs-cats">
              {Object.entries(counts).map(([severity, count]) => (
                <div className="rs-cat" key={severity}>
                  <span className={'rs-cat-dot rs-sev-' + severity} />
                  <span>{titleCase(severity)}</span>
                  <b>{text(count)}</b>
                </div>
              ))}
            </div>
          ) : null}
        </>
      )}
    </RailSheet>
  );
}

export function RulesSheet({ snapshot, onClose }: { snapshot: PageProps['snapshot']; onClose: () => void }) {
  const workflows = workflowFacts(snapshot);
  const hierarchy = hierarchyLevels(snapshot);
  const digests = object(snapshot.digests);
  const actions = declaredActions(snapshot);
  const unit = unitLabel(snapshot);
  const focus = focusName(snapshot);
  const focusPolicy = focusPolicyOf(snapshot);
  const title = 'Rules';
  return (
    <RailSheet
      title={title}
      kicker={`pack · ${text(object(snapshot.project).pack, text(snapshot.pack))}`}
      onClose={onClose}
      wide
      footer={
        <>
          <span>presentation changes update the presentation digest</span>
          <span className="sp" />
          <span>semantic changes need reapproval</span>
        </>
      }
    >
      <section className="rs-section">
        <h3>Unit and hierarchy</h3>
        <div className="rs-kv"><span>Unit</span><b>{unit}</b></div>
        {hierarchy.length ? (
          hierarchy.map((level) => (
            <div className="rs-kv" key={text(level.preset)}>
              <span>{labelOf(snapshot, text(level.preset), true)}</span>
              <b>{level.required === false ? 'optional' : 'required'}{text(level.order) ? ` · ${text(level.order)}` : ''}</b>
            </div>
          ))
        ) : (
          <div className="rs-kv"><span>Hierarchy</span><b>flat</b></div>
        )}
        <div className="rs-kv"><span>Optional containment</span><b>{hasOptionalHierarchy(snapshot) ? 'yes' : 'no'}</b></div>
        <div className="rs-kv"><span>Declared order</span><b>{hasOrderedHierarchy(snapshot) ? 'yes' : 'no'}</b></div>
      </section>

      <section className="rs-section">
        <h3>Workflows · {workflows.length}</h3>
        {workflows.map((fact) => {
          const name = text(fact.name);
          const gates: Record<string, GateFact | null> = {};
          for (const status of gateStatuses(snapshot, name)) gates[status] = gateFor(snapshot, name, status);
          return <RuleList key={name} snapshot={snapshot} workflow={name} gates={gates} />;
        })}
      </section>

      <section className="rs-section">
        <h3>Completion</h3>
        <div className="rs-rule">{completionSentence(snapshot)}</div>
        {hasPerPresetCompletion(snapshot) ? (
          <div className="rs-preset-rules">
            {Object.entries(completionOf(snapshot).presets).map(([preset, states]) => (
              <div key={preset} className="rs-preset-rule"><b>{labelOf(snapshot, preset, true)}</b><span>{states.join(' or ')}</span></div>
            ))}
          </div>
        ) : null}
      </section>

      {hasFocus(snapshot) ? (
        <section className="rs-section">
          <h3>Focus</h3>
          <div className="rs-kv"><span>Container</span><b>{labelOf(snapshot, text(focusPolicy.preset), true)}</b></div>
          <div className="rs-kv"><span>Category</span><b>{text(focusPolicy.category) || '—'}</b></div>
          <div className="rs-kv"><span>Resolve</span><b>{text(focusPolicy.resolve) || '—'}</b></div>
          <div className="rs-kv"><span>Now</span><b>{focusState(snapshot) === 'resolved' ? focus : focusState(snapshot)}</b></div>
          {focusState(snapshot) === 'ambiguous' ? <p className="rs-note">Ambiguous focus lists its candidates; the UI never auto-picks.</p> : null}
        </section>
      ) : null}

      {hasExecution(snapshot) ? (
        <section className="rs-section">
          <h3>Execution actions</h3>
          <div className="rs-kv"><span>Actions</span><b>{executionActions(snapshot).join(', ')}</b></div>
          <p className="rs-note">Execution progress is reported separately from workflow completion.</p>
        </section>
      ) : null}

      {actions.length ? (
        <section className="rs-section">
          <h3>Declared actions · {actions.length}</h3>
          {actions.map((action) => {
            const requires = object(action.requires);
            const preconditions = [
              requires.children === true ? 'every child finished' : '',
              requires.review === true ? 'review required' : '',
              text(requires.check) ? `check ${text(requires.check)}` : '',
              text(requires.field) ? `field ${text(requires.field)}` : '',
            ].filter(Boolean);
            return (
              <div className="rs-action" key={text(action.name)}>
                <div>
                  <b>{text(action.name)}</b>
                  <span>{text(action.use_when)}</span>
                </div>
                <div className="rs-action-meta">
                  <span>{text(action.on) === '*' ? 'any preset' : labelOf(snapshot, text(action.on))}</span>
                  {preconditions.length ? <span className="rs-pre">{preconditions.join(' · ')}</span> : null}
                </div>
              </div>
            );
          })}
          {hasActionPreconditions(snapshot) ? <p className="rs-note">A precondition is shown before the action; the action never runs in the UI's place.</p> : null}
        </section>
      ) : null}

      <section className="rs-section">
        <h3>Digests</h3>
        <div className="rs-kv"><span>semantic</span><b className="mono">{text(digests.semantic).slice(0, 16) || '—'}</b></div>
        <div className="rs-kv"><span>presentation</span><b className="mono">{text(digests.presentation).slice(0, 16) || '—'}</b></div>
        <div className="rs-kv"><span>full</span><b className="mono">{text(digests.full).slice(0, 16) || '—'}</b></div>
      </section>
    </RailSheet>
  );
}
