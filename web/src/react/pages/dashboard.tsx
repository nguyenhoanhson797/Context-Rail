/* Dashboard is the owner layer: the pack's summary screen rendered as one
   narrative canvas. The runway is the declared progress group; the spotlight
   is the focus container's own children. Every number comes from the compiled
   snapshot, and every panel names its population. */
import { Fragment, useEffect, useMemo, type CSSProperties, type ReactNode } from 'react';
import { ArrowRight, CircleDashed } from '../icons';
import { object, rows, text, type Data, type PageProps, type Snapshot } from '../contracts';
import { cn } from '../ui';
import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  allItems,
  attentionOf,
  categoryOf,
  completionOf,
  completionSentence,
  executionActions,
  focusCandidates,
  focusFact,
  focusPolicyOf,
  focusState,
  hasExecution,
  hasFocus,
  itemById,
  itemsNeedingGate,
  recordsOf,
  screenOf,
  SUMMARY_KINDS,
} from '../capabilities';
import { focusOf, labelOf, progressOf, queuePolicyOf, titleCase, unitLabel } from './operating';

const number = (value: unknown): number => {
  const parsed = Number(text(value, '0'));
  return Number.isFinite(parsed) ? parsed : 0;
};
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
const presetOf = (doc: Data): string => text(doc.preset, text(doc.type));
const plural = (count: number, one: string, many = `${one}s`): string =>
  `${count} ${count === 1 ? one : many}`;
const CATEGORY_RANK: Record<string, number> = {
  active: 0,
  blocked: 1,
  not_started: 2,
  done: 3,
  dropped: 4,
};
const categoryRank = (category: string): number => CATEGORY_RANK[category] ?? 5;
const shortTime = (value: string): string => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const parentsOf = (snapshot: Snapshot): Map<string, string[]> => {
  const parents = new Map<string, string[]>();
  for (const item of allItems(snapshot)) {
    parents.set(text(item.id), strings(object(item.relations).part_of));
  }
  return parents;
};
const ancestorsOf = (id: string, parents: Map<string, string[]>): Set<string> => {
  const found = new Set<string>();
  const queue = [...(parents.get(id) ?? [])];
  while (queue.length) {
    const current = queue.shift() as string;
    if (found.has(current)) continue;
    found.add(current);
    queue.push(...(parents.get(current) ?? []));
  }
  return found;
};
const sitsInside = (id: string, parents: Map<string, string[]>, focus: string): boolean =>
  Boolean(focus) && (id === focus || ancestorsOf(id, parents).has(focus));

function Donut({
  complete,
  active,
  blocked,
  total,
  unit,
}: {
  complete: number;
  active: number;
  blocked: number;
  total: number;
  unit: string;
}) {
  const remaining = Math.max(total - complete - active - blocked, 0);
  const pct = total > 0 ? Math.round((complete / total) * 100) : null;
  const p1 = total > 0 ? (complete / total) * 100 : 0;
  const p2 = total > 0 ? p1 + (active / total) * 100 : 0;
  const p3 = total > 0 ? Math.min(p2 + (blocked / total) * 100, 100) : 100;
  const background =
    total > 0
      ? `conic-gradient(var(--green) 0% ${p1}%, var(--blue) ${p1}% ${p2}%, var(--red) ${p2}% ${p3}%, var(--surface-3) ${p3}% 100%)`
      : 'var(--surface-3)';
  return (
    <div className="rdb-donut-cluster">
      <div>
        <div className="rdb-donut" role="img" aria-label={`${complete} of ${total} ${unit} complete`} style={{ background }}>
          <div className="rdb-donut-center">
            <b>{pct === null ? '—' : `${pct}%`}</b>
            <span>{total > 0 ? `${complete}/${total} ${unit.toLowerCase()}` : 'no items'}</span>
          </div>
        </div>
        <div className="rdb-donut-label">declared population: {unit}</div>
      </div>
      <div className="rdb-legend">
        <span className="rdb-legend-row done"><i />Complete <b>{complete}</b></span>
        <span className="rdb-legend-row active"><i />Active <b>{active}</b></span>
        <span className="rdb-legend-row blocked"><i />Blocked <b>{blocked}</b></span>
        <span className="rdb-legend-row other"><i />Remaining <b>{remaining}</b></span>
      </div>
    </div>
  );
}

function StationMeter({ complete, total }: { complete: number; total: number }) {
  if (total <= 0) return <span className="rdb-meter" aria-hidden="true" />;
  if (total > 8) {
    return (
      <span className="rdb-meter-bar" aria-hidden="true">
        <i style={{ width: `${Math.min((complete / total) * 100, 100)}%` }} />
      </span>
    );
  }
  return (
    <span className="rdb-meter" aria-hidden="true">
      {Array.from({ length: total }, (_, index) => (
        <i key={index} className={index < complete ? 'on' : undefined} />
      ))}
    </span>
  );
}

function Tag({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={cn('rdb-tag', tone)}>{children}</span>;
}

export function DashboardPage({ snapshot, navigate, openDoc, health, healthError }: PageProps) {
  const summaryScreen = screenOf(snapshot, SUMMARY_KINDS);
  const hasSummary = summaryScreen !== null;
  useEffect(() => {
    if (!hasSummary) navigate('board');
  }, [hasSummary, navigate]);

  const title = summaryScreen?.title ?? 'Summary';
  const progress = progressOf(snapshot);
  const total = number(progress.total);
  const complete = number(progress.complete);
  const byCategory = object(progress.by_category);
  const active = number(byCategory.active);
  const blocked = number(byCategory.blocked);
  const completion = completionOf(snapshot);
  const unitMany = unitLabel(snapshot, true);
  const unitOne = unitLabel(snapshot);
  const population = completion.of.length ? labelOf(snapshot, completion.of[0], true) : unitMany;

  const attention = useMemo(() => attentionOf(snapshot), [snapshot]);
  const gateCount = attention.filter((entry) => entry.kind === 'gate').length;
  const blockedCount = attention.filter((entry) => entry.kind === 'blocked').length;
  const focusCount = attention.filter((entry) => entry.kind === 'focus').length;

  const focus = focusState(snapshot);
  const focusResolved = focus === 'resolved' && Boolean(text(focusFact(snapshot).slug));
  const focusAmbiguous = focus === 'ambiguous';
  const focusInvalid = focus === 'invalid';
  const focusKnown = hasFocus(snapshot) || focusResolved || focusAmbiguous || focusInvalid;
  const focusTitle = focusResolved ? text(focusFact(snapshot).title, text(focusFact(snapshot).slug)) : '';
  const focusSlug = focusResolved ? text(focusFact(snapshot).slug) : '';
  const focusCategory = focusResolved
    ? text(focusFact(snapshot).category, categoryOf(snapshot, text(focusFact(snapshot).status)))
    : '';
  const focusPolicy = focusPolicyOf(snapshot);
  const focusContainer = text(focusPolicy.preset)
    ? labelOf(snapshot, text(focusPolicy.preset), true).toLowerCase()
    : 'containers';
  const candidates = focusCandidates(snapshot);
  const resolvedBy = text(focusOf(snapshot).resolved_by);
  const resolvedLabel =
    resolvedBy === 'unique' ? 'unique match' : resolvedBy === 'first' ? 'first match' : resolvedBy;

  const parents = useMemo(() => parentsOf(snapshot), [snapshot]);
  const byId = useMemo(() => itemById(snapshot), [snapshot]);

  const headline: ReactNode = focusResolved ? (
    <>
      <em>{focusTitle}</em>{' '}
      {focusCategory === 'active'
        ? 'is in motion.'
        : focusCategory === 'blocked'
          ? 'is blocked.'
          : focusCategory === 'done'
            ? 'is complete.'
            : 'is the focus.'}
    </>
  ) : focusAmbiguous ? (
    'Focus needs a choice.'
  ) : focusInvalid ? (
    'Focus is invalid.'
  ) : hasFocus(snapshot) ? (
    `No active ${focusContainer}.`
  ) : total > 0 ? (
    `${complete} of ${total} ${unitMany.toLowerCase()} done.`
  ) : (
    `No ${unitMany.toLowerCase()} yet.`
  );

  const facts: ReactNode[] = [];
  /* The headline already states the population's progress when the pack has
     no focus; repeating it in the subline says nothing new. */
  if (focusKnown && total > 0) {
    facts.push(
      <span key="done">
        <b>{complete}</b> of <b>{total}</b> {unitMany.toLowerCase()} done
      </span>,
    );
  }
  if (gateCount > 0) {
    facts.push(
      <span key="gates">
        <b>{gateCount}</b> {gateCount === 1 ? 'gate' : 'gates'} owed
      </span>,
    );
  }
  if (blockedCount > 0) {
    facts.push(
      <span key="blocked">
        <b>{blockedCount}</b> blocked
      </span>,
    );
  }
  if (focusCount > 0) {
    facts.push(
      <span key="focus">
        <b>{focusCount}</b> focus {focusCount === 1 ? 'candidate' : 'candidates'}
      </span>,
    );
  }

  const goal = text(object(snapshot.project).goal);
  const healthDoctor = object(health?.doctor);
  const healthCounts = object(healthDoctor.by_severity);
  const healthErrors = number(healthCounts.error);
  const healthWarnings = number(healthCounts.warning);
  const healthState = health?.state ?? (healthError ? 'error' : 'unknown');
  const healthTone =
    healthState === 'error' || healthErrors > 0
      ? 'bad'
      : healthState === 'ok' && healthWarnings > 0
        ? 'warn'
        : healthState === 'ok'
          ? 'ok'
          : 'unknown';

  const groups = rows(progress.children);
  const groupPreset = text(progress.group);
  const groupOne = groupPreset ? labelOf(snapshot, groupPreset) : 'Group';
  const groupMany = groupPreset ? labelOf(snapshot, groupPreset, true) : 'Groups';
  const focusIndex = focusSlug ? groups.findIndex((group) => text(group.slug) === focusSlug) : -1;
  let start = 0;
  if (groups.length > 3 && focusIndex >= 0) {
    start = Math.min(Math.max(focusIndex - 1, 0), groups.length - 3);
  }
  const stations = groups.slice(start, start + 3);
  const hiddenBefore = start;
  const hiddenAfter = Math.max(groups.length - start - stations.length, 0);
  const hiddenNote = [hiddenBefore ? `${hiddenBefore} earlier` : '', hiddenAfter ? `${hiddenAfter} later` : '']
    .filter(Boolean)
    .join(' · ');
  const stationColumns = stations
    .map((group) => (text(group.slug) === focusSlug ? '1.16fr' : '1fr'))
    .join(' ');

  const focusItems = useMemo(() => {
    if (!focusSlug) return [];
    const presets = new Set(completion.of);
    return allItems(snapshot)
      .filter(
        (item) =>
          strings(object(item.relations).part_of).includes(focusSlug) &&
          (!presets.size || presets.has(presetOf(item))),
      )
      .sort(
        (a, b) =>
          categoryRank(text(a.category)) - categoryRank(text(b.category)) ||
          text(a.title, text(a.id)).localeCompare(text(b.title, text(b.id))),
      );
  }, [snapshot, focusSlug]);
  const focusGroup = focusIndex >= 0 ? groups[focusIndex] : null;
  const focusComplete = focusGroup ? number(focusGroup.complete) : 0;
  const focusTotal = focusGroup ? number(focusGroup.total) : focusItems.length;
  const shownFocusItems = focusItems.slice(0, 6);

  /* A blocked item outranks a gate, and a gate outranks a focus candidate:
     clear the thing that stops work before reading proof. */
  const attentionRank: Record<string, number> = { blocked: 0, gate: 1, focus: 2 };
  const needShown = [...attention]
    .sort((left, right) => (attentionRank[left.kind] ?? 3) - (attentionRank[right.kind] ?? 3))
    .slice(0, 5);
  const insideFocus = focusSlug
    ? needShown.filter((entry) => sitsInside(entry.id, parents, focusSlug))
    : [];
  const needNote =
    focusSlug && insideFocus.length
      ? insideFocus.length === 1
        ? `${insideFocus[0].title} sits inside ${focusTitle}. Clear it before the container can close.`
        : insideFocus.length === 2
          ? `${insideFocus[0].title} and ${insideFocus[1].title} sit inside ${focusTitle}. Clear them before the container can close.`
          : `${insideFocus.length} of these sit inside ${focusTitle}. Clear them before the container can close.`
      : '';

  const queue = rows(object(object(snapshot.operating).queue).items);
  const queueError = text(object(object(snapshot.operating).queue).error);
  const queueOmitted = number(object(object(snapshot.operating).queue).omitted);
  const queueScope = text(object(object(snapshot.operating).queue).scope);
  const queuePolicy = queuePolicyOf(snapshot);
  const queueShown = queue.slice(0, 5);
  const queueHidden = Math.max(queue.length - queueShown.length, 0);
  const queueHint =
    focusKnown && queueScope === 'focus'
      ? queuePolicy.focus === 'only'
        ? 'focus only'
        : 'focus first'
      : 'project-wide';

  const records = recordsOf(snapshot);
  const recordCounts = new Map<string, number>();
  for (const record of records) {
    recordCounts.set(record.kind, (recordCounts.get(record.kind) ?? 0) + 1);
  }
  const recordParts = [...recordCounts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 3)
    .map(([kind, count]) => `${count} ${labelOf(snapshot, kind, count !== 1).toLowerCase()}`);
  const latestRecord = [...records].sort((left, right) =>
    text(right.doc.updated).localeCompare(text(left.doc.updated)),
  )[0];
  const recent = [...allItems(snapshot)]
    .filter((item) => Boolean(text(item.updated)))
    .sort((left, right) => text(right.updated).localeCompare(text(left.updated)))
    .slice(0, 3);
  const healthIssues = rows(healthDoctor.issues);
  const healthCode =
    text(health?.error_code) || text(healthIssues[0]?.code) || text(healthIssues[0]?.message);

  const runs = object(progress.runs);
  const runStates = object(runs.by_state);
  const runActions = strings(runs.actions);
  const declaredExecution = executionActions(snapshot);
  const executionNames = runActions.length ? runActions : declaredExecution;

  const styleStations: CSSProperties = {
    gridTemplateColumns: stationColumns || 'repeat(3, 1fr)',
  };

  if (!summaryScreen) return null;

  return (
    <div className="rdb-page" data-dashboard={title}>
      <div className="rdb-canvas">
        <section className="rdb-hero" aria-labelledby="rdb-title">
          <div>
            <div className="rdb-kicker">{title} · {text(object(snapshot.project).pack, text(snapshot.pack))} · {unitOne} / {groupOne}</div>
            <h1 id="rdb-title" className="rdb-headline">{headline}</h1>
            {facts.length ? (
              <p className="rdb-subline">
                {facts.map((fact, index) => (
                  <Fragment key={index}>
                    {index > 0 ? <span className="rdb-sep">·</span> : null}
                    {fact}
                  </Fragment>
                ))}
              </p>
            ) : (
              <p className="rdb-subline rdb-subline-muted">{completionSentence(snapshot)}</p>
            )}
            {goal ? <p className="rdb-goal">{goal}</p> : null}
            <div className="rdb-chips">
              {focusKnown ? (
                <span className={cn('rdb-pill', focusAmbiguous || focusInvalid ? 'warn' : focusResolved ? 'blue' : 'ghost')}>
                  <span className="rdb-dot" />
                  Focus · {focusResolved ? focusTitle : focusAmbiguous ? 'ambiguous' : focusInvalid ? 'invalid' : 'none'}
                  {focusResolved && resolvedLabel ? ` · resolved by ${resolvedLabel}` : ''}
                </span>
              ) : null}
              <span className={cn('rdb-pill', healthTone)}>
                <span className="rdb-dot" />
                {healthState === 'ok'
                  ? healthErrors || healthWarnings
                    ? `Health · ${plural(healthErrors, 'error')}, ${plural(healthWarnings, 'warning')}`
                    : 'Health · no findings'
                  : healthState === 'error'
                    ? 'Health · unavailable'
                    : 'Health · unknown'}
              </span>
              <span className="rdb-pill ghost">agent-driven</span>
            </div>
          </div>
          <Donut complete={complete} active={active} blocked={blocked} total={total} unit={population} />
        </section>

        <section className="rdb-runway-section" aria-labelledby="rdb-runway-title">
          <div className="rdb-section-head">
            <h2 id="rdb-runway-title">The runway</h2>
            <span className="rdb-hint">
              {groups.length
                ? `${groupMany} in plan order · ${focusResolved ? 'the active one is the focus' : 'no focus'}`
                : groupPreset
                  ? `No ${groupMany.toLowerCase()} yet · category flow`
                  : 'No progress group declared · category flow'}
            </span>
            <button type="button" className="rdb-link" onClick={() => navigate('roadmap')}>Open Plan →</button>
          </div>

          {groups.length ? (
            <div className="rdb-runway">
              <div className="rdb-stations" style={styleStations}>
                {stations.map((group) => {
                  const groupSlug = text(group.slug);
                  const groupTotal = number(group.total);
                  const groupComplete = number(group.complete);
                  const now = groupSlug === focusSlug;
                  const completeGroup = groupTotal > 0 && groupComplete === groupTotal;
                  return (
                    <div key={groupSlug} className={cn('rdb-station', now && 'now', completeGroup && !now && 'complete')}>
                      <span className="rdb-station-dot" />
                      <div className="rdb-station-label">
                        {groupOne}
                        {now ? <span className="rdb-now-pill">now</span> : null}
                      </div>
                      <div className="rdb-station-title" title={text(group.title, groupSlug)}>{text(group.title, groupSlug)}</div>
                      <div className="rdb-station-count"><b>{groupComplete}/{groupTotal}</b> {unitMany.toLowerCase()} done</div>
                      <StationMeter complete={groupComplete} total={groupTotal} />
                    </div>
                  );
                })}
              </div>

              {focusResolved ? (
                <div className="rdb-spotlight">
                  <div className="rdb-spotlight-head">
                    <span className="rdb-spotlight-k">Inside {text(focusGroup?.title, focusTitle)}</span>
                    <span className="rdb-spotlight-count"><b>{focusComplete} of {focusTotal}</b> {unitMany.toLowerCase()} done</span>
                    <button type="button" className="rdb-link" onClick={() => openDoc(focusSlug)}>Open →</button>
                  </div>
                  {shownFocusItems.length ? (
                    <div className="rdb-story-rows">
                      {shownFocusItems.map((item) => {
                        const id = text(item.id);
                        const status = text(item.status);
                        const category = text(item.category, categoryOf(snapshot, status));
                        const owed = itemsNeedingGate(snapshot, presetOf(item), status);
                        const reason = text(item.reason, text(item.blocked_why));
                        const tag = owed.length
                          ? { tone: 'gate', label: 'proof owed' }
                          : category === 'blocked'
                            ? { tone: 'blocked', label: 'blocked' }
                            : category === 'done'
                              ? { tone: 'done', label: 'done' }
                              : category === 'active'
                                ? { tone: 'active', label: 'in motion' }
                                : { tone: 'next', label: 'next' };
                        const detail = owed.length
                          ? `${titleCase(status).toLowerCase()} · proof gate before ${owed.join(' or ')}`
                          : reason
                            ? `${titleCase(status).toLowerCase()} · ${reason}`
                            : category === 'blocked'
                              ? `${titleCase(status).toLowerCase()} · waiting to be unblocked`
                              : category === 'not_started'
                                ? `${titleCase(status).toLowerCase()} · eligible to start`
                                : category === 'done'
                                  ? `${titleCase(status).toLowerCase()} · complete`
                                  : category === 'active'
                                    ? `${titleCase(status).toLowerCase()} · in motion`
                                    : `${titleCase(status).toLowerCase()} · ${CATEGORY_LABEL[category] ?? category}`;
                        return (
                          <button type="button" key={id} className="rdb-story-row" onClick={() => openDoc(id)}>
                            <span className={cn('rdb-story-dot', category)} />
                            <span className="rdb-story-main">
                              <b>{text(item.title, id)}</b>
                              <span>{detail}</span>
                            </span>
                            <span className="rdb-story-tags"><Tag tone={tag.tone}>{tag.label}</Tag></span>
                          </button>
                        );
                      })}
                      {focusItems.length > shownFocusItems.length ? (
                        <div className="rdb-story-more">+{focusItems.length - shownFocusItems.length} more · open the {groupOne.toLowerCase()} for the full list</div>
                      ) : null}
                    </div>
                  ) : (
                    <p className="rdb-empty-note">No {unitMany.toLowerCase()} are filed under this {groupOne.toLowerCase()} yet.</p>
                  )}
                  <div className="rdb-spotlight-foot">
                    The spotlight holds the focus container's own children. It never borrows work from another {groupOne.toLowerCase()}.
                  </div>
                </div>
              ) : null}

              {focusAmbiguous || focusInvalid || (hasFocus(snapshot) && !focusResolved) ? (
                <div className="rdb-focus-choice">
                  <div className="rdb-spotlight-head">
                    <span className="rdb-spotlight-k">{focusAmbiguous ? 'Focus needs a choice' : focusInvalid ? 'Focus is invalid' : `No active ${focusContainer}`}</span>
                    <span className="rdb-hint">the UI never auto-picks</span>
                  </div>
                  {candidates.length ? (
                    <div className="rdb-choice-list">
                      {candidates.slice(0, 4).map((candidate) => {
                        const id = text(candidate.slug);
                        return (
                          <button type="button" key={id} className="rdb-choice" onClick={() => openDoc(id)}>
                            <CircleDashed size={12} />
                            <span>{text(candidate.title, id)}</span>
                            <ArrowRight size={11} />
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="rdb-empty-note">No container matches the declared focus category.</p>
                  )}
                  <div className="rdb-spotlight-foot">Start or move a container into the active category, then the runway will spotlight it.</div>
                </div>
              ) : null}

              {hiddenNote ? (
                <div className="rdb-runway-more">
                  {hiddenNote} · open Plan for the full {groupMany.toLowerCase()}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="rdb-flow" aria-label="Workflow categories">
              {CATEGORY_ORDER.filter((category) => number(byCategory[category]) > 0).map((category, index, visible) => (
                <Fragment key={category}>
                  <span className={cn('rdb-flow-step', category)}>
                    <b>{number(byCategory[category])}</b>
                    <span>{CATEGORY_LABEL[category] ?? titleCase(category)}</span>
                  </span>
                  {index < visible.length - 1 ? <ArrowRight size={13} className="rdb-flow-arrow" /> : null}
                </Fragment>
              ))}
              {!CATEGORY_ORDER.some((category) => number(byCategory[category]) > 0) ? (
                <span className="rdb-empty-note">No work items counted yet.</span>
              ) : null}
            </div>
          )}
        </section>

        {hasExecution(snapshot) ? (
          <section className="rdb-execution" aria-label="Execution progress">
            <div className="rdb-section-head">
              <h2>Execution · {executionNames.join(', ') || 'declared'}</h2>
              <span className="rdb-hint">never merged with workflow completion</span>
              <button type="button" className="rdb-link" onClick={() => navigate('board', { overlay: 'progress' })}>Open progress →</button>
            </div>
            {number(runs.total) > 0 ? (
              <div className="rdb-execution-row">
                {['running', 'succeeded', 'failed', 'cancelled'].map((state) => (
                  <span key={state} className={cn('rdb-run', state)}>
                    <i />
                    {titleCase(state)}
                    <b>{number(runStates[state])}</b>
                  </span>
                ))}
                <span className="rdb-hint">{number(runs.total)} {number(runs.total) === 1 ? 'run' : 'runs'} tracked</span>
              </div>
            ) : (
              <p className="rdb-empty-note">No {executionNames.join(' or ') || 'execution'} runs yet. Execution is reported here only when it runs.</p>
            )}
          </section>
        ) : null}

        <section className="rdb-split" aria-label="What needs you and what comes next">
          <div className="rdb-needs">
            <div className="rdb-section-head">
              <h2>Needs you · {attention.length}</h2>
              <span className="rdb-hint">clear the path</span>
              <button type="button" className="rdb-link" onClick={() => navigate('board', { attention: '1' })}>Review →</button>
            </div>
            <p className="rdb-lede">Blocked work and proof owed, across all work — not only this screen's population.</p>
            {needShown.length ? (
              <>
                {needShown.map((entry, index) => (
                  <button type="button" key={entry.key} className="rdb-need-row" onClick={() => openDoc(entry.id)}>
                    <span className="rdb-need-no">{String(index + 1).padStart(2, '0')}</span>
                    <span className="rdb-need-main">
                      <b>{entry.title}</b>
                      <span>{entry.reason}</span>
                    </span>
                    <Tag tone={entry.kind === 'gate' ? 'gate' : entry.kind === 'blocked' ? 'blocked' : 'focus'}>
                      {entry.kind === 'gate' ? 'proof owed' : entry.kind}
                    </Tag>
                  </button>
                ))}
                {needNote ? <div className="rdb-need-note">{needNote}</div> : null}
                {attention.length > needShown.length ? (
                  <div className="rdb-more">{attention.length - needShown.length} more in Flow and the item drawer.</div>
                ) : null}
              </>
            ) : (
              <div className="rdb-empty">
                <b>Nothing needs you</b>
                <span>No blocked work, gates owed or ambiguous focus.</span>
              </div>
            )}
            <div className="rdb-split-foot">
              <span>Store health is a separate signal, not a work item.</span>
              <span className="rdb-spacer" />
              <button type="button" className="rdb-link" onClick={() => navigate('board', { attention: '1' })}>Open Flow with attention →</button>
            </div>
          </div>

          <div className="rdb-next">
            <div className="rdb-section-head">
              <h2>Next up · {queue.length ? `${queue.length} eligible` : 'none'}</h2>
              <span className="rdb-hint">{queueHint}</span>
              <button type="button" className="rdb-link" onClick={() => navigate('roadmap')}>Open Plan →</button>
            </div>
            <p className="rdb-lede">The engine's queue. The chip says the next legal move; the drawer holds the full set.</p>
            {queueError ? (
              <div className="rdb-empty bad">
                <b>Queue unavailable</b>
                <span>{queueError}</span>
                {candidates.length ? (
                  <div className="rdb-choice-list">
                    {candidates.slice(0, 4).map((candidate) => {
                      const id = text(candidate.slug);
                      return (
                        <button type="button" key={id} className="rdb-choice" onClick={() => openDoc(id)}>
                          <CircleDashed size={12} />
                          <span>{text(candidate.title, id)}</span>
                          <ArrowRight size={11} />
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            ) : queueShown.length ? (
              <>
                {queueShown.map((item) => {
                  const id = text(item.slug);
                  const doc = byId.get(id);
                  const status = text(item.status, doc ? text(doc.status) : '');
                  const preset = text(item.preset, doc ? presetOf(doc) : '');
                  const owed = itemsNeedingGate(snapshot, preset, status);
                  const states = doc ? strings(doc.states).filter((state) => state !== status) : [];
                  const preferred = ['done', 'active', 'not_started', 'blocked', 'dropped']
                    .map((category) => states.find((state) => categoryOf(snapshot, state) === category))
                    .find(Boolean) ?? '';
                  const nextLabel = owed.length ? 'gate first' : preferred ? `→ ${titleCase(preferred)}` : '—';
                  const nextTitle = states.length ? states.map((state) => titleCase(state)).join(' or ') : '';
                  const why = text(item.why) === 'focus' ? 'in focus' : text(item.why) === 'preset' ? 'preset' : text(item.why);
                  return (
                    <button type="button" key={id} className="rdb-next-row" onClick={() => openDoc(id)} title={nextTitle || undefined}>
                      <span className="rdb-next-arrow">→</span>
                      <span className="rdb-next-main">
                        <b>
                          {text(item.title, text(doc?.title, id))}
                          {item.focus === true ? <span className="rdb-focus-mark">focus</span> : null}
                        </b>
                        <span>{labelOf(snapshot, preset)} · {titleCase(status)} · {why}</span>
                      </span>
                      <span className={cn('rdb-next-move', owed.length && 'gate')}>{nextLabel}</span>
                    </button>
                  );
                })}
                {queueHidden > 0 ? <div className="rdb-more">+{queueHidden} more eligible not shown.</div> : null}
              </>
            ) : (
              <div className="rdb-empty">
                <b>Nothing is eligible</b>
                <span>
                  {queuePolicy.require === 'action'
                    ? 'No item has a declared action from its current state.'
                    : 'No item has a legal transition from its current state.'}
                </span>
              </div>
            )}
            <div className="rdb-split-foot">
              <span>Legal moves are hints; actions live in the drawer. The UI never runs them.</span>
              <span className="rdb-spacer" />
              {queueOmitted > 0 ? <span>{queueOmitted} omitted by eligibility</span> : null}
            </div>
          </div>
        </section>

        <footer className="rdb-signals" aria-label="Project signals">
          <div className="rdb-signal">
            <div className="rdb-kicker">Records</div>
            <div className="rdb-signal-value">{recordParts.length ? recordParts.join(' · ') : 'No records yet'}</div>
            {latestRecord ? <div className="rdb-signal-row"><b>{latestRecord.title}</b><span>{shortTime(text(latestRecord.doc.updated))}</span></div> : null}
            <button type="button" className="rdb-link" onClick={() => navigate('decisions')}>Open Records →</button>
          </div>
          <div className="rdb-signal">
            <div className="rdb-kicker">Recently updated</div>
            <div className="rdb-signal-row">
              {recent.length ? recent.map((item, index) => (
                <Fragment key={text(item.id)}>
                  {index > 0 ? <span className="rdb-sep">·</span> : null}
                  <b>{text(item.title, text(item.id))}</b>
                  <span>{shortTime(text(item.updated))}</span>
                </Fragment>
              )) : <span>No work items yet.</span>}
            </div>
            <button type="button" className="rdb-link" onClick={() => navigate('board')}>Open Flow →</button>
          </div>
          <div className="rdb-signal">
            <div className="rdb-kicker">Health</div>
            <div className="rdb-signal-row">
              <span className={cn('rdb-health-dot', healthTone)} />
              <b>
                {healthState === 'ok'
                  ? healthErrors || healthWarnings
                    ? `${plural(healthErrors, 'error')} · ${plural(healthWarnings, 'warning')}`
                    : 'No findings'
                  : healthState === 'error'
                    ? 'Unavailable'
                    : 'Unknown'}
              </b>
              {healthCode ? <span className="rdb-code">{healthCode}</span> : null}
            </div>
            <button type="button" className="rdb-link" onClick={() => navigate('board', { overlay: 'health' })}>Open Health →</button>
          </div>
        </footer>
      </div>
    </div>
  );
}
