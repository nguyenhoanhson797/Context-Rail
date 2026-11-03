/* =====================================================================
   ATLAS DRAWER (React): the small drawer for COMPUTED shapes - a program, a
   phase, a scope item, a task, a list of tasks, a queued command, the proposed
   decisions. "Open the record" goes to the app's own doc drawer; the drawer
   itself shows only what the model computed.
   ===================================================================== */
import { type ReactNode } from 'react';
import { PROOF, daysBetween, fmtDay, provenOf, sumv, type Model, type Task } from './model';
import { Chip, DocLink, OpenBtn, PROOF_LABEL, PRIORITY_NAME, STATUS_TONE, Tank, VIS_KIND, itemTip, programName, proofParts, shellSafe, Stack, useAtlas, when, workParts, idOk } from './ui';

export type DrawerKind = 'prog' | 'scope' | 'phase' | 'task' | 'tasks' | 'queue' | 'decisions';
export interface DrawerRequest { kind: DrawerKind; id: string }
export interface DrawerContent { kind: string; title: string; body: ReactNode }

const plural = (n: number, one: string, many = one + 's'): string => n + ' ' + (n === 1 ? one : many);
const tone = (s: string): string => STATUS_TONE[s] ?? '';
const flat = { flex: 'none' } as const;
const H5 = ({ children }: { children: ReactNode }) => <h3 className="at-dh5">{children}</h3>;

function RowTask({ t }: { t: Task }) {
  return (
    <OpenBtn k="task" id={t.id} cls="at-li" label={t.title + ' — ' + t.status}>
      <Chip text={t.status} tone={tone(t.status)} extra={{ style: flat }} />
      <span className="at-lt">
        {t.title}
        {t.claim ? (
          <>
            <br />
            <span className="at-muted">◉ {t.claim}</span>
          </>
        ) : null}
      </span>
    </OpenBtn>
  );
}
/* The button that opens the record itself, in the app's own doc drawer. */
function OpenRecord({ id, label }: { id: string; label: string }) {
  const { openDoc } = useAtlas();
  if (!idOk(id)) return null;
  return (
    <div className="at-dact">
      <button type="button" className="btn btn-sm btn-secondary" onClick={() => openDoc(id)}>
        {label}
      </button>
    </div>
  );
}
function ProgramDrawer({ M, id }: { M: Model; id: string }): DrawerContent | null {
  const p = M.P.get(id);
  if (!p) return null;
  const phases = M.phases.filter((x) => x.program === id);
  const items = M.scope.filter((s) => s.program === id && s.priority !== 'wont');
  const open = M.tasks.filter((t) => t.program === id && t.status !== 'done');
  const pt = sumv(p.proof);
  return {
    kind: 'Program · ' + VIS_KIND[p.vis],
    title: p.label,
    body: (
      <>
        <p className="at-note">
          {p.blurb ? p.blurb + '. ' : ''}
          {when(p)}.
        </p>
        <H5>Work</H5>
        {p.n ? (
          <>
            <Stack parts={workParts(p.tasks)} cls="at-tall" />
            <p className="at-brk at-mt6">
              {p.tasks.done} of {p.n} tasks done · {p.tasks['in-progress']} in progress · {p.tasks.blocked} blocked ·{' '}
              {p.tasks.todo} to do
            </p>
          </>
        ) : (
          <p className="at-brk">No tasks were recorded for this program (phases only).</p>
        )}
        <H5>Proof</H5>
        {pt ? (
          <>
            <Stack parts={proofParts(p.proof)} cls="at-tall" />
            <p className="at-brk at-mt6">
              {PROOF.filter((k) => p.proof[k])
                .map((k) => p.proof[k] + ' ' + k)
                .join(' · ')}{' '}
              (checks on the {plural(items.length, 'scope item')} it delivers)
            </p>
          </>
        ) : (
          <p className="at-brk">No scope checks attached to this program.</p>
        )}
        <H5>Phases · label vs tasks</H5>
        {phases.length ? (
          phases.map((x) => (
            <OpenBtn key={x.id} k="phase" id={x.id} cls="at-li" label={x.title + ', labelled ' + x.recorded}>
              <Chip text={x.recorded} tone={tone(x.recorded)} extra={{ style: flat }} />
              <span className="at-lt">{x.title}</span>
              <span className="at-lm">
                {x.derived_pct == null ? 'no tasks' : Math.round(x.derived_pct) + '% by tasks'}
                {x.mismatch ? <> <b className="at-warn">!</b></> : null}
              </span>
            </OpenBtn>
          ))
        ) : (
          <p className="at-brk">No phase is placed in this program.</p>
        )}
        {items.length ? (
          <>
            <H5>Scope items it delivers</H5>
            <div className="at-mtiles at-roomy">
              {items.map((s) => (
                <OpenBtn key={s.id} k="scope" id={s.id} cls="at-mt at-mt-lg" label={itemTip(s)} tipText={itemTip(s)}>
                  <Tank s={s} />
                </OpenBtn>
              ))}
            </div>
          </>
        ) : null}
        {open.length ? (
          <>
            <H5>Open tasks ({open.length})</H5>
            {open.slice(0, 12).map((t) => (
              <RowTask key={t.id} t={t} />
            ))}
            {open.length > 12 ? <p className="at-brk">+ {open.length - 12} more</p> : null}
          </>
        ) : null}
      </>
    ),
  };
}
function ScopeDrawer({ M, id }: { M: Model; id: string }): DrawerContent | null {
  const s = M.SC.get(id);
  if (!s) return null;
  const goals = s.goals.map((g) => M.goals.find((x) => x.id === g)?.label).filter(Boolean);
  const pr = provenOf(s);
  const prog = programName(M.P, s.program);
  const refresh = s.checks.some((c) => c.cls === 'stale' || c.cls === 'unrun') && shellSafe(s.id);
  const cmd = 'ctx-rail verify --scope-item ' + s.id + ' --record';
  const taskCounts = Object.entries(s.tasks).filter(([, v]) => v > 0);
  return {
    kind: 'Scope item · ' + (PRIORITY_NAME[s.priority] || 'Unranked').toUpperCase() + (s.claim ? ' · ' + s.claim : ''),
    title: s.id + ' ' + s.title,
    body: (
      <>
        {s.acceptance ? (
          <>
            <H5>Acceptance</H5>
            <p className="at-note at-note-2">{s.acceptance}</p>
          </>
        ) : null}
        <div className="at-kv">
          {goals.length ? (
            goals.map((g) => (
              <Chip key={String(g)} text={String(g)} tone="i" />
            ))
          ) : (
            <Chip text="no sub-goal names this item" tone="a" />
          )}
          {prog ? <Chip text={prog} /> : <Chip text="not placed in a program" tone="a" />}
        </div>
        <H5>Checks · {pr} of {s.checks.length} proven</H5>
        {s.checks.length ? (
          s.checks.map((c, i) => (
            <div className="at-chk" key={i}>
              <span className="at-sq">
                <i className={'at-p-' + c.cls} />
              </span>
              <span>
                {c.text}
                <small>
                  {PROOF_LABEL[c.cls]}
                  {c.detail && c.cls !== 'proven' ? ' — ' + c.detail.replace(/\(e\.g\. [^)]*\)/, '') : ''}
                </small>
              </span>
            </div>
          ))
        ) : (
          <p className="at-brk">No checks: this item is not scored.</p>
        )}
        <H5>Tasks delivering it</H5>
        <p className="at-brk">{taskCounts.length ? taskCounts.map(([k, v]) => v + ' ' + k).join(' · ') : 'none linked'}</p>
        {refresh ? (
          <>
            <H5>To refresh the proof</H5>
            <Cmd text={cmd} />
          </>
        ) : null}
        <OpenRecord id={s.id} label="Open the record" />
      </>
    ),
  };
}

function Cmd({ text, brk = false }: { text: string; brk?: boolean }) {
  const { copy } = useAtlas();
  return (
    <div className="at-cmd">
      <span className={'at-mono' + (brk ? ' at-break' : '')}>{text}</span>
      <button type="button" className="at-copy" aria-label={'Copy: ' + text} onClick={() => copy(text)}>
        copy
      </button>
    </div>
  );
}

function PhaseDrawer({ M, id }: { M: Model; id: string }): DrawerContent | null {
  const x = M.PH.get(id);
  if (!x) return null;
  const ts = M.tasks.filter((t) => t.phase === id);
  const up = M.edges.filter((e) => e.dst === id);
  const dn = M.edges.filter((e) => e.src === id);
  const nm = (i: string): string => M.PH.get(i)?.title || i;
  const edgeRow = (e: { src: string; dst: string; kind: string; why: string }, other: string) => (
    <div className="at-li at-static" key={e.src + '>' + e.dst}>
      <Chip text={e.kind === 'authored' ? 'authored' : 'order only'} tone={e.kind === 'authored' ? 'i' : ''} extra={{ style: flat }} />
      <span className="at-lt">
        {nm(other)}
        <br />
        <span className="at-muted">{e.why || 'no reason recorded'}</span>
      </span>
    </div>
  );
  return {
    kind: 'Phase · labelled “' + x.recorded + '” · by tasks: ' + x.derived,
    title: x.title,
    body: (
      <>
        {x.mismatch ? (
          <div className="at-rootcause at-flush">
            <b className="at-warn">Label disagrees with tasks.</b> Recorded {x.recorded}
            {x.recorded_progress == null ? '' : ' at ' + Math.round(x.recorded_progress) + '%'}; tasks say{' '}
            {x.derived_pct == null ? 'nothing' : Math.round(x.derived_pct) + '% done'}.
          </div>
        ) : null}
        <H5>Tasks</H5>
        {x.n ? <Stack parts={workParts(x.tasks)} cls="at-tall" /> : <p className="at-brk">No tasks.</p>}
        {up.length ? (
          <>
            <H5>Waits on</H5>
            {up.map((e) => edgeRow(e, e.src))}
          </>
        ) : null}
        {dn.length ? (
          <>
            <H5>Unlocks</H5>
            {dn.map((e) => edgeRow(e, e.dst))}
          </>
        ) : null}
        {!up.length && !dn.length ? <p className="at-brk at-mt10">No dependency is declared for this phase: unknown, not absent.</p> : null}
        {ts.length ? (
          <>
            <H5>All tasks ({ts.length})</H5>
            {ts.map((t) => (
              <RowTask key={t.id} t={t} />
            ))}
          </>
        ) : null}
        <OpenRecord id={x.id} label="Open the record" />
      </>
    ),
  };
}
function TaskDrawer({ M, id }: { M: Model; id: string }): DrawerContent | null {
  const t = M.TASK.get(id);
  if (!t) return null;
  const prog = programName(M.P, t.program);
  const { openDoc, lens } = useAtlas();
  return {
    kind: 'Task · ' + t.status,
    title: t.title,
    body: (
      <>
        <div className="at-kv">
          <Chip text={t.status} tone={tone(t.status)} />
          {prog ? <Chip text={prog} /> : <Chip text="not placed in a program" tone="a" />}
          {t.claim ? <Chip text={'◉ ' + t.claim} tone="b" /> : <Chip text="no live claim" />}
        </div>
        <p className="at-brk">
          Phase: <span className="at-mono">{t.phase || 'none'}</span>
          <br />
          Created {fmtDay(t.created) || 'date unknown'} · updated {fmtDay(t.updated) || 'date unknown'}
        </p>
        <H5>Implements</H5>
        {t.implements.length ? (
          <div className="at-kv">
            {t.implements.map((i) => {
              const item = M.SC.get(i);
              return item ? (
                <OpenBtn key={i} k="scope" id={i} cls="at-chip at-t-i at-btnchip" label={itemTip(item)}>
                  {i}
                </OpenBtn>
              ) : (
                <Chip key={i} text={i} tone="i" />
              );
            })}
          </div>
        ) : (
          <p className="at-brk">No scope item — this task is not traced to any acceptance.</p>
        )}
        {shellSafe(t.id) ? (
          <>
            <H5>Brief for a worker</H5>
            <Cmd text={'ctx-rail work-order ' + t.id} />
          </>
        ) : null}
        <div className="at-dact">
          {idOk(t.id) ? (
            <>
              <button type="button" className="btn btn-sm btn-secondary" onClick={() => openDoc(t.id)}>
                Open the record
              </button>
              <button type="button" className="btn btn-sm btn-secondary" onClick={() => lens(t.id)}>
                Context lens
              </button>
            </>
          ) : null}
        </div>
      </>
    ),
  };
}

function TasksDrawer({ M, id }: { M: Model; id: string }): DrawerContent | null {
  const list = id === 'inflight' ? M.inflight : M.landed;
  return {
    kind: 'Tasks',
    title: id === 'inflight' ? 'In flight · ' + list.length : 'Recently landed',
    body: list.length ? (
      <>
        {list.map((t) => (
          <RowTask key={t.id} t={t} />
        ))}
      </>
    ) : (
      <p className="at-brk">No task.</p>
    ),
  };
}

function QueueDrawer({ M, id }: { M: Model; id: string }): DrawerContent | null {
  const q = M.queue.find((x) => x.id === id);
  if (!q) return M.SC.has(id) ? ScopeDrawer({ M, id }) : null;
  return {
    kind: 'Next · ' + (q.kind || 'queued'),
    title: q.title,
    body: (
      <>
        <p className="at-note">{q.why ? q.why : 'No reason recorded.'}</p>
        {q.command ? (
          <>
            <H5>Command</H5>
            <Cmd text={q.command} brk />
          </>
        ) : null}
        {M.SC.has(q.id) ? (
          <div className="at-dact">
            <OpenBtn k="scope" id={q.id} cls="btn btn-sm btn-secondary" label={'Open ' + q.id}>
              {'Open ' + q.id}
            </OpenBtn>
          </div>
        ) : (
          <OpenRecord id={q.id} label="Open the record" />
        )}
      </>
    ),
  };
}

function DecisionsDrawer({ M }: { M: Model }): DrawerContent {
  const list = M.loops.proposed.slice().sort((a, b) => (a.created < b.created ? -1 : 1));
  const { page } = useAtlas();
  return {
    kind: 'Decisions · proposed',
    title: plural(list.length, 'decision') + ' waiting for acceptance',
    body: (
      <>
        <p className="at-note">
          Proposed decisions are recorded but not yet accepted by the owner. Until then agents treat them as guidance, not law.
        </p>
        {list.map((d) => {
          const a = daysBetween(d.created, M.asOfDay);
          return (
            <div className="at-li at-static" key={d.id}>
              <Chip text="proposed" tone="i" extra={{ style: flat }} />
              <span className="at-lt">
                <DocLink id={d.id} label={d.title || d.id} />
              </span>
              <span className="at-lm">{a == null ? 'age unknown' : a + 'd'}</span>
            </div>
          );
        })}
        <div className="at-dact">
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => page('decisions')}>
            Open Decisions
          </button>
        </div>
      </>
    ),
  };
}

/* The content of the open drawer, or null when its record is not in the model. */
export function drawerContent(M: Model, d: DrawerRequest | null): DrawerContent | null {
  if (!d) return null;
  switch (d.kind) {
    case 'prog':
      return ProgramDrawer({ M, id: d.id });
    case 'scope':
      return ScopeDrawer({ M, id: d.id });
    case 'phase':
      return PhaseDrawer({ M, id: d.id });
    case 'task':
      return TaskDrawer({ M, id: d.id });
    case 'tasks':
      return TasksDrawer({ M, id: d.id });
    case 'queue':
      return QueueDrawer({ M, id: d.id });
    case 'decisions':
      return DecisionsDrawer({ M });
    default:
      return null;
  }
}