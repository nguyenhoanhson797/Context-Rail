/* The Progress screen: how far this project is from its own definition of
   done — the rule the pack compiled, the per-container tally and the
   by-category ledger. Execution progress (runs) is reported separately and
   never merged into it. Nothing here invents velocity or estimates: the
   numbers and the rule are the snapshot's own. */
import type { CSSProperties } from 'react';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { Badge, Empty, PageHeading, Panel } from '../ui';
import { hierarchyLabels, labelOf, screenTitle, unitLabel } from './operating';

const CATEGORY_TONE: Record<string, 'neutral' | 'accent' | 'danger' | 'good'> = {
  not_started: 'neutral',
  active: 'accent',
  blocked: 'danger',
  done: 'good',
  dropped: 'neutral',
};

const list = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];

export function ExecutionPage({ snapshot, openDoc }: PageProps) {
  const title = screenTitle(snapshot, 'execution', 'Progress');
  const operating = object(snapshot.operating);
  const progress = object(operating.progress);
  const children = rows(progress.children);
  const byCategory = object(progress.by_category);
  const group = text(progress.group);
  const unitMany = unitLabel(snapshot, true);
  const groupLabels = hierarchyLabels(snapshot);
  const groupLabel = group && groupLabels[0] ? groupLabels[0] : 'groups';
  const total = text(progress.total, '0');
  const missing = Number(total) === 0 && !children.length && !Object.keys(byCategory).length;

  /* The completion rule travels inside the compiled snapshot: the population,
     the states (or category, or per-preset states) that count as complete. */
  const specOf = list(progress.of);
  const rule = object(progress.rule);
  const states = list(rule.states);
  const category = text(rule.category);
  const ofLabel = specOf.length === 1 ? labelOf(snapshot, specOf[0], true) : unitMany;
  const scope = states.length
    ? `states ${states.join(', ')}`
    : category
      ? `category ${category}`
      : 'the done category';
  const ruleSentence = `Complete = ${scope}, counted in ${ofLabel}${group ? `, grouped by ${groupLabel}` : ''}.`;

  return (
    <div className="stack">
      <PageHeading
        title={title}
        description="How far this project is from its own definition of done — the pack's rule, the per-container tally and the by-category ledger. Execution runs count separately."
      />
      {missing ? (
        <Empty title="No progress model compiled">The pack could not compute progress. Check rail model and rail ops doctor.</Empty>
      ) : (
        <>
          <Panel title="The rule">
            <p style={{ margin: 0 }}>{ruleSentence}</p>
            <p className="faint" style={{ marginTop: 6, marginBottom: 0, fontSize: 13 }}>
              Execution (runs) is never merged with workflow completion; neither answers the other.
            </p>
          </Panel>
          {children.length ? (
            <Panel title={`By ${groupLabel} · ${children.length}`}>
              {children.map((child) => {
                const slug = text(child.slug);
                const doneOf = `${text(child.complete, '0')}/${text(child.total, '0')}`;
                const share = Number(child.total) > 0 ? (Number(child.complete) / Number(child.total)) * 100 : 0;
                return (
                  <div className="mini" key={slug || doneOf} role="button" tabIndex={0} onClick={() => slug && openDoc(slug)}>
                    <span className="grow trunc" title={text(child.title, slug)}>{text(child.title, slug)}</span>
                    <span className="row" style={{ gap: 8, alignItems: 'center', flexShrink: 0 }}>
                      <span className="prog" role="progressbar" aria-valuenow={Math.round(share)} aria-valuemin={0} aria-valuemax={100} style={{ width: 120 }}>
                        <i style={{ '--p': share / 100 } as CSSProperties} />
                      </span>
                      <span className="faint" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{doneOf}</span>
                    </span>
                  </div>
                );
              })}
            </Panel>
          ) : null}
          <Panel title="By category">
            {Object.entries(byCategory).map(([cat, count]) => (
              <div className="mini" key={cat}>
                <span className="grow">
                  <Badge tone={CATEGORY_TONE[cat] ?? 'neutral'}>{cat}</Badge>
                </span>
                <span className="faint" style={{ fontSize: 13 }}>{text(count)} of {total}</span>
              </div>
            ))}
          </Panel>
          <RunsPanel progress={progress} />
        </>
      )}
    </div>
  );
}

function RunsPanel({ progress }: { progress: Data }) {
  const runs = object(progress.runs);
  const byState = object(runs.by_state);
  const names = Object.keys(byState);
  if (!names.length) {
    return (
      <Panel title="Execution runs">
        <div className="faint">No execution runs yet — when the pack's declared actions run, their results appear here and count as execution progress, never as done.</div>
      </Panel>
    );
  }
  return (
    <Panel title="Execution runs">
      {names.map((state) => (
        <div className="mini" key={state}>
          <span className="grow">{state}</span>
          <Badge tone={state === 'failed' ? 'danger' : state === 'succeeded' ? 'good' : 'neutral'}>{text(byState[state])}</Badge>
        </div>
      ))}
    </Panel>
  );
}
