
/* =====================================================================
   ATLAS ALTITUDE 1 - COVER (30 seconds): what is it, how far, where honestly,
   what is moving, what follows. Every sentence is composed from the model,
   never typed: where the model does not know a number the text says so.
   ===================================================================== */
import type { CSSProperties, ReactNode } from 'react';
import { fmtDay, pc, pcLabel, shortId, shortName, sumv, type Model, type Program, type Task } from './model';
import {
  Chip,
  GoBtn,
  LegendItem,
  Marker,
  OpenBtn,
  PROOF_SHORT,
  Stack,
  VIS_LABEL,
  idOk,
  mustState,
  proofParts,
  useAtlas,
  when,
  workParts,
  workText,
} from './ui';

const list = (items: string[], last = 'and'): string =>
  items.length < 2 ? items.join('') : items.slice(0, -1).join(', ') + ' ' + last + ' ' + items[items.length - 1];
const plural = (n: number, one: string, many = one + 's'): string => n + ' ' + (n === 1 ? one : many);
const em = (cls: string, text: string): ReactNode => <em className={cls}>{text}</em>;

/* ---------- the story: three sentences, each from the data ---------- */
export function Story({ M }: { M: Model }) {
  const progs = M.programs;
  const done = progs.filter((p) => p.vis === 'done' || p.vis === 'built');
  const blocked = progs.filter((p) => p.vis === 'blocked');
  let s1: ReactNode;
  if (!progs.length) s1 = (M.project.name || 'This project') + ' has no program recorded yet.';
  else if (!done.length) s1 = <>Nothing is {em('at-em-warn', 'built')} yet.</>;
  else {
    const lead = done.filter((p) => p.vis === 'built').pop() || done[done.length - 1];
    s1 = (
      <>
        {list(done.slice(0, 3).map((p) => p.label))}
        {done.length > 3 ? ' and ' + (done.length - 3) + ' more' : ''}
        {done.length === 1 ? ' is' : ' are'} {em('at-em-ok', 'built')}
        {lead.n ? ' — ' + lead.tasks.done + ' of ' + lead.n + ' ' + lead.label + ' tasks done' : ''}
      </>
    );
  }
  let tail: ReactNode = null;
  if (blocked.length) {
    const b = blocked[0];
    const waiting = M.tasks.find((t) => t.program === b.id && t.status === 'blocked');
    tail = (
      <>
        , but {em('at-em-bad', 'blocked')}: {b.label} waits on a person
        {waiting ? ' — ' + waiting.title : ''}
        {blocked.length > 1 ? ' (and ' + (blocked.length - 1) + ' more blocked)' : ''}.
      </>
    );
  } else if (progs.length) tail = '.';

  const o = M.proof.official;
  const must = M.proof.must_total;
  let s2: ReactNode;
  if (o.final_score == null) s2 = <>{em('at-em-warn', 'Proof is not assessed')} yet: the scope score is unknown.</>;
  else {
    const lag = o.checked_score != null && o.checked_score - o.final_score >= 0.05;
    s2 = (
      <>
        {lag ? em('at-em-warn', 'Proof lags the work') : 'Proof'}
        {lag ? ': ' : ': '}
        {pcLabel(o.final_score)} proven
        {o.checked_score != null ? ' against ' + pcLabel(o.checked_score) + ' claimed' : ''}
        {must ? ', and ' + (M.proof.must_proven_now < must ? 'only ' : '') + M.proof.must_proven_now + ' of ' + must + ' Must items are fully proven' : ''}.
      </>
    );
  }
  let s3: ReactNode;
  if (!M.inflight.length) s3 = 'Nothing is in flight right now.';
  else {
    const by = new Map<string, number>();
    for (const t of M.inflight) by.set(t.program, (by.get(t.program) || 0) + 1);
    const top = [...by.entries()].sort((a, b) => b[1] - a[1])[0];
    const p = M.P.get(top[0]);
    s3 = (
      <>
        Right now {plural(M.inflight.length, 'task')}
        {M.inflight.length === 1 ? ' is' : ' are'} in flight
        {p ? (
          <>
            , {top[1]} of them on {em('at-em-inf', p.label)}
          </>
        ) : ''}
        .
      </>
    );
  }
  return (
    <p className="at-story">
      {s1}
      {tail}
      {' '}
      {s2} {s3}
    </p>
  );
}

/* ---------- Q2: the rail, from the first commit to the horizon ---------- */
const reached = (p: Program): boolean => p.vis === 'done' || p.vis === 'built' || p.vis === 'blocked' || p.vis === 'now';
export function Rail({ M }: { M: Model }) {
  const ps = M.programs;
  if (!ps.length)
    return <p className="at-note">No program is recorded, so there is no rail to draw. A program groups phases; the roadmap names them.</p>;
  return (
    <ol className="at-rail" style={{ '--n': ps.length } as CSSProperties} aria-label="Programs from the first commit to the horizon">
      {ps.map((p, i) => {
        const l = reached(p) ? '' : ' at-ld';
        const r = ps[i + 1] && reached(ps[i + 1]) ? '' : ' at-rd';
        const pt = sumv(p.proof);
        const pp = p.proof.proven;
        const gate = p.vis === 'blocked' ? M.tasks.find((t) => t.program === p.id && t.status === 'blocked') : null;
        const label =
          p.label +
          ', ' +
          VIS_LABEL[p.vis] +
          '. ' +
          when(p) +
          '. Work ' +
          workText(p) +
          (p.n ? ' tasks done' : '') +
          '. Proof ' +
          (pt ? pp + ' of ' + pt + ' checks proven' : 'has no checks') +
          '.' +
          (p.vis === 'now' ? ' You are here.' : '');
        return (
          <li className="at-stop-w" key={p.id}>
            <OpenBtn k="prog" id={p.id} cls={'at-stop' + (reached(p) ? '' : ' at-dashl')} label={label}>
              {p.vis === 'now' && <span className="at-here">YOU ARE HERE</span>}
              <span className="at-when">{when(p)}</span>
              <span className={'at-mk' + l + r}>
                <Marker p={p} />
              </span>
              <span className="at-nm">{p.label}</span>
              <span className="at-bl">{p.blurb}</span>
              <span className="at-bars">
                <span className="at-brow">
                  <span>Work</span>
                  <Stack parts={workParts(p.tasks)} />
                  <b>{workText(p)}</b>
                </span>
                <span className="at-brow">
                  <span>Proof</span>
                  {pt ? <Stack parts={proofParts(p.proof)} /> : <span className="at-stack at-empty" />}
                  <b>{pt ? pp + '/' + pt : '—'}</b>
                </span>
              </span>
              {gate ? (
                <span className="at-gate-note">Waiting on you — {gate.title}.</span>
              ) : p.vis === 'blocked' ? (
                <span className="at-gate-note">Blocked: waiting on a person.</span>
              ) : null}
            </OpenBtn>
          </li>
        );
      })}
    </ol>
  );
}

/* ---------- Q3: built vs proven ---------- */
export function Bvp({ M }: { M: Model }) {
  const o = M.proof.official;
  const cl = M.proof.classes;
  const tot = sumv(cl);
  const st = M.proof.stale;
  const fin = pc(o.final_score);
  const chk = pc(o.checked_score);
  const musts = M.scope.filter((s) => s.priority === 'must');
  const debt = fin != null && chk != null ? Math.max(0, chk - fin) : null;
  const meter =
    fin == null && chk == null ? (
      <div className="at-meter" role="img" aria-label="Claimed and proven scores are not known" />
    ) : (
      <div
        className="at-meter"
        role="img"
        aria-label={'Claimed ' + (chk == null ? 'unknown' : chk + '%') + ', proven ' + (fin == null ? 'unknown' : fin + '%')}
        data-atip="Green = proven by fresh evidence. Hatched = ticked but not currently provable."
      >
        <i className="at-claimed" style={{ width: (chk ?? 0) + '%' }} />
        <i className="at-proven" style={{ width: (fin ?? 0) + '%' }} />
      </div>
    );
  return (
    <div className="at-card">
      <h3>
        Built vs proven <span className="at-sub">scope score, today</span>
      </h3>
      <div className="at-bvp-num">
        <div>
          <b>{chk == null ? 'unknown' : chk + '%'}</b>
          <span>claimed — checks ticked</span>
        </div>
        <div>
          <b className="at-ok">{fin == null ? 'unknown' : fin + '%'}</b>
          <span>proven — fresh evidence</span>
        </div>
        <div className="at-gp">
          <b>{debt == null ? '—' : debt + ' pts'}</b>
          <span>proof debt</span>
        </div>
      </div>
      {meter}
      <div className="at-mini-h at-mt14">
        Why the gap <span className="at-ct">{plural(tot, 'check')}</span>
      </div>
      <Stack parts={proofParts(cl)} cls="at-tall" />
      <div className="at-legend at-mt8">
        {PROOF_ORDER.filter((k) => cl[k]).map((k) => (
          <LegendItem key={k} cls={'at-p-' + k} text={cl[k] + ' ' + PROOF_SHORT[k]} />
        ))}
      </div>
      <div className="at-mini-h at-mt14">
        Must items{' '}
        <span className="at-ct">
          {M.proof.must_proven_now} of {musts.length} fully proven
        </span>
      </div>
      {musts.length ? <div className="at-dots">{musts.map((s) => <MustDot key={s.id} s={s} />)}</div> : <p className="at-note">No Must item is recorded.</p>}
      <div className="at-legend">
        <LegendItem cls="at-p-proven" text="proven" />
        <LegendItem cls="at-p-stale" text="proven after re-run" />
        <span>
          <i className="at-sw at-sw-fix" />
          needs a fix
        </span>
      </div>
      {st.files ? (
        <div className="at-rootcause">
          <b>Root cause:</b> {st.files} files changed
          {st.since ? (
            <>
              {' '}since <span className="at-mono">{st.since}</span>
            </>
          ) : null}
          , so {cl.stale} passing checks went stale. <GoBtn alt="consequences" anchor="k-decay" label="See the decay map ›" cls="at-link" />
        </div>
      ) : null}
    </div>
  );
}
const PROOF_ORDER = ['proven', 'stale', 'failed', 'unrun', 'attention'] as const;
function MustDot({ s }: { s: Model['scope'][number] }) {
  const st = mustState(s);
  return (
    <OpenBtn k="scope" id={s.id} cls={'at-dot at-d-' + st.k} label={s.id + ' — ' + s.title + ' · ' + st.text} tipText={s.id + ' — ' + s.title + ' · ' + st.text}>
      {shortId(s.id)}
    </OpenBtn>
  );
}

/* Every task is a square, grouped by program, so the work is countable at a glance. */
export function Waffle({ M }: { M: Model }) {
  const order: Record<string, number> = { done: 0, 'in-progress': 1, blocked: 2, todo: 3 };
  const cls: Record<string, string> = { done: 'at-w-done', 'in-progress': 'at-w-prog', blocked: 'at-w-block', todo: 'at-w-todo' };
  const rank = (t: Task): number => (Object.hasOwn(order, t.status) ? order[t.status] : 4);
  const groups = M.programs
    .map((p) => ({ id: p.id, name: shortName(p.label), full: p.label, tasks: M.tasks.filter((t) => t.program === p.id), done: p.tasks.done }))
    .filter((g) => g.tasks.length);
  const placed = new Set(M.programs.map((p) => p.id));
  const loose = M.tasks.filter((t) => !placed.has(t.program));
  if (loose.length)
    groups.push({ id: '', name: 'No program', full: 'Not placed in a program', tasks: loose, done: loose.filter((t) => t.status === 'done').length });
  const c = M.counts.taskStatus;
  const total = M.counts.tasks;
  const donePct = total ? Math.round((100 * (c.done || 0)) / total) : null;
  const fin = pc(M.proof.official.final_score);
  return (
    <div className="at-card">
      <h3>
        All work, one square per task <span className="at-sub">{plural(total, 'task')}</span>
      </h3>
      {groups.length ? (
        <div className="at-wall">
          {groups.map((g) => {
            const t = g.tasks.slice().sort((a, b) => rank(a) - rank(b));
            const w = Math.min(12, Math.ceil(Math.sqrt(t.length * 1.6))) * 14;
            return (
              <div className="at-wgroup" role="group" aria-label={g.full + ': ' + g.done + ' of ' + t.length + ' tasks done'} key={g.id || 'none'}>
                <div className="at-mini-h">
                  {g.name} <span className="at-ct">{g.done}/{t.length}</span>
                </div>
                <div className="at-wgrid" style={{ maxWidth: w + 'px' }}>
                  {t.map((task) => (
                    <OpenBtn
                      key={task.id}
                      k="task"
                      id={task.id}
                      cls={'at-wsq ' + (Object.hasOwn(cls, task.status) ? cls[task.status] : 'at-w-unk')}
                      label={task.title + ' — ' + task.status + (task.claim ? ' · ' + task.claim : '')}
                      tipText={task.title + ' — ' + task.status + (task.claim ? ' · ' + task.claim : '')}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="at-note">No task is recorded.</p>
      )}
      <div className="at-legend">
        <LegendItem cls="at-w-done" text={(c.done || 0) + ' done'} />
        <LegendItem cls="at-w-prog" text={(c['in-progress'] || 0) + ' in progress'} />
        <LegendItem cls="at-w-block" text={(c.blocked || 0) + ' blocked'} />
        <span>
          <i className="at-sw at-sw-todo" />
          {c.todo || 0} to do
        </span>
      </div>
      {donePct != null ? (
        <p className="at-note at-mt10">
          Blue is <b>work</b>. Compare with the green on the left: <b>{donePct}%</b> of tasks are done,{' '}
          {fin != null ? (
            <>
              but only <b>{fin}%</b> of scope is provable today.
            </>
          ) : (
            'and how much of scope is provable is not known.'
          )}
        </p>
      ) : null}
    </div>
  );
}

/* ---------- needs a human ---------- */
interface LoopRow {
  tag: [string, string];
  t: string;
  m: string;
  k?: string;
  id?: string;
  doc?: string;
}
export function Needs({ M }: { M: Model }) {
  const { openDoc } = useAtlas();
  const L = M.loops;
  const rows: LoopRow[] = [];
  for (const e of L.escalate) {
    if (e.kind === 'question') rows.push({ tag: ['QUESTION', 'v'], t: e.title, m: e.id, doc: e.id });
    else rows.push({ tag: ['BLOCKED', 'r'], t: e.title, m: e.id, k: M.TASK.has(e.id) ? 'task' : '', id: e.id, doc: e.id });
  }
  if (L.proposed.length) {
    const oldest = L.proposed.map((d) => d.created).filter(Boolean).sort()[0];
    rows.push({
      tag: ['DECIDE', 'i'],
      t: plural(L.proposed.length, 'decision') + (L.proposed.length === 1 ? ' is' : ' are') + ' proposed and waiting for acceptance',
      m: oldest ? 'oldest ' + fmtDay(oldest) : 'age unknown',
      k: 'decisions',
      id: 'proposed',
    });
  }
  for (const r of L.risks.filter((x) => x.status === 'open'))
    rows.push({ tag: ['RISK', 'a'], t: r.title || r.id, m: (r.severity || 'unrated') + ' severity · open', doc: r.id });
  const inner = (r: LoopRow) => (
    <>
      <span className="at-tg">
        <Chip text={r.tag[0]} tone={r.tag[1]} />
      </span>
      <span className="at-loop-t">
        <span className="at-tt">{r.t}</span>
        <span className="at-mm">{r.m}</span>
      </span>
    </>
  );
  return (
    <div className="at-card">
      <h3>
        Needs a human <span className="at-sub">{plural(rows.length, 'open loop')}</span>
      </h3>
      {rows.length ? (
        rows.map((r, i) =>
          r.k ? (
            <OpenBtn key={i} k={r.k} id={r.id || ''} cls="at-loop" label={r.tag[0] + ': ' + r.t}>
              {inner(r)}
            </OpenBtn>
          ) : r.doc && idOk(r.doc) ? (
            <button key={i} type="button" className="at-loop" aria-label={'Open the record: ' + r.t} onClick={() => openDoc(r.doc as string)}>
              {inner(r)}
            </button>
          ) : (
            <div key={i} className="at-loop at-static">
              {inner(r)}
            </div>
          ),
        )
      ) : (
        <p className="at-note">Nothing is waiting on a person: no blocked task, open question, open risk or proposed decision.</p>
      )}
      {rows.length ? <p className="at-note at-mt8">Agents can keep working while these wait; none of them can close these loops.</p> : null}
    </div>
  );
}

/* ---------- Q4 / Q5: moving now, up next, just landed ---------- */
export function Trio({ M }: { M: Model }) {
  const by = new Map<string, number>();
  for (const t of M.inflight) by.set(t.program, (by.get(t.program) || 0) + 1);
  const chips = [...by.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => {
      const p = M.P.get(k);
      return <Chip key={k} tone="b" text={(p ? shortName(p.label) : 'No program') + ' ' + v} />;
    });
  const claimed = M.inflight.slice().sort((a, b) => (b.claim ? 1 : 0) - (a.claim ? 1 : 0));
  const holders = new Set(M.loops.claims.map((c) => c.actor).filter(Boolean));
  const li = (t: Task, m: string) => (
    <OpenBtn key={t.id} k="task" id={t.id} cls="at-li" label={t.title + (m ? ' — ' + m : '')}>
      <span className="at-lt">{t.title}</span>
      <span className="at-lm">{m}</span>
    </OpenBtn>
  );
  const q = M.queue.slice(0, 5);
  const allMaint = M.queue.length > 0 && M.queue.every((x) => x.kind === 'fix' || x.kind === 'rerun');
  const milestone = M.milestones.slice().sort((a, b) => (a.date < b.date ? 1 : -1))[0];
  const queued = M.queue.length + M.queueMore;
  return (
    <div className="at-card">
      <div className="at-trio">
        <div>
          <h4>
            Moving now{' '}
            <span className="at-ct">
              {M.inflight.length} in flight · {plural(M.loops.claims.length, 'claim')} · {plural(holders.size, 'holder')}
            </span>
          </h4>
          {chips.length ? <div className="at-pgbar">{chips}</div> : null}
          {M.inflight.length ? (
            claimed.slice(0, 5).map((t) => li(t, t.claim ? '◉ ' + t.claim.split(' ')[0] : 'no claim'))
          ) : (
            <p className="at-note">No task is in progress.</p>
          )}
          {M.inflight.length ? (
            <OpenBtn k="tasks" id="inflight" cls="at-li at-li-more" label={'All ' + M.inflight.length + ' tasks in flight'}>
              <span className="at-lt">All {M.inflight.length} in flight ›</span>
            </OpenBtn>
          ) : null}
        </div>
        <div>
          <h4>
            Up next{' '}
            <span className="at-ct">
              {M.nextState || 'state unknown'} · {queued} queued
            </span>
          </h4>
          {q.length ? (
            q.map((x) => (
              <OpenBtn key={x.id + ':' + x.kind} k="queue" id={x.id} cls="at-li" label={x.kind + ': ' + x.title}>
                <span style={{ flex: 'none' }}>
                  <Chip text={x.kind || 'next'} tone={x.kind === 'fix' ? 'r' : 'a'} />
                </span>
                <span className="at-lt">
                  {x.title}
                  <br />
                  <span className="at-muted">{x.id}</span>
                </span>
              </OpenBtn>
            ))
          ) : (
            <p className="at-note">Nothing is queued.</p>
          )}
          {allMaint ? (
            <p className="at-note at-mt6">
              <b>Every ranked item is proof maintenance</b> — no new feature is queued.
            </p>
          ) : null}
        </div>
        <div>
          <h4>Just landed</h4>
          {M.landed.length ? (
            M.landed.slice(0, 5).map((t) => li(t, fmtDay(t.updated)))
          ) : (
            <p className="at-note">Nothing landed recently.</p>
          )}
          {milestone ? (
            <div className="at-li at-static">
              <span className="at-lt">
                <Chip text="milestone" tone="g" /> {milestone.title}
              </span>
              <span className="at-lm">{fmtDay(milestone.date)}</span>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/* ---------- Q6: consequence teasers, every number read from the data ---------- */
export function Cqs({ M }: { M: Model }) {
  const cards: ReactNode[] = [];
  const card = (icon: string, tone: string, h: string, p: string, src: string, alt: string, anchor: string, key: string) => (
    <GoBtnCard key={key} icon={icon} tone={tone} h={h} p={p} src={src} alt={alt} anchor={anchor} />
  );
  const blocked = M.programs.filter((p) => p.vis === 'blocked');
  if (blocked.length) {
    const b = blocked[0];
    const waiting = M.tasks.find((t) => t.program === b.id && t.status === 'blocked');
    const phases = new Set(b.phases);
    const dependents = M.edges.some((e) => phases.has(e.src));
    cards.push(
      card(
        '⛔',
        'r',
        b.label + ' is blocked',
        b.label +
          ' is ' +
          b.tasks.done +
          '/' +
          b.n +
          ' tasks done' +
          (waiting ? '; the open step is “' + waiting.title + '”, which waits on a person.' : '; it waits on a person.') +
          (dependents ? '' : ' No phase declares anything that waits on it — unknown, not absent.'),
        'Roadmap · ' + b.label,
        'consequences',
        'k-graph',
        'blocked',
      ),
    );
  }
  const st = M.proof.stale;
  const cl = M.proof.classes;
  if (st.files) {
    const top = st.composition.slice(0, 3).map(([k, v]) => v + ' ' + k);
    cards.push(
      card(
        '⏳',
        'a',
        'Every commit ages the proof',
        st.files +
          ' files changed' +
          (st.since ? ' since ' + st.since : '') +
          ' and staled ' +
          cl.stale +
          ' passing checks' +
          (top.length ? ' — ' + list(top) + '.' : '.') +
          ' Re-running could lift Must from ' +
          M.proof.must_proven_now +
          ' to ' +
          M.proof.must_if_stale_repass +
          ' of ' +
          M.proof.must_total +
          ' (an upper bound).',
        'scope-score · git diff',
        'consequences',
        'k-decay',
        'stale',
      ),
    );
  }
  const mism = M.phases.filter((p) => p.mismatch);
  if (mism.length) {
    const lab = M.phases.filter((p) => p.recorded === 'planned' && p.derived === 'done').length;
    const moving = M.phases.filter((p) => p.recorded === 'planned' && p.tasks['in-progress'] > 0).length;
    const bits: string[] = [];
    if (lab) bits.push(plural(lab, 'phase') + ' still read “planned” with every task done');
    if (moving) bits.push(plural(moving, 'phase') + ' read “planned” while work is in flight');
    cards.push(
      card(
        '🏷',
        'v',
        mism.length + ' of ' + M.phases.length + ' phase labels disagree with their tasks',
        (bits.length ? list(bits) + '. ' : '') + 'Phase state cannot be the headline; the tasks are the truth.',
        'phase files vs task files',
        'consequences',
        'k-truth',
        'mismatch',
      ),
    );
  }
  const behind = M.programs.filter((p) => p.vis === 'next' && p.n > p.tasks.done);
  if (behind.length) {
    const open = behind.reduce((a, p) => a + p.n - p.tasks.done, 0);
    const chk = behind.reduce((a, p) => a + sumv(p.proof), 0);
    const pro = behind.reduce((a, p) => a + p.proof.proven, 0);
    cards.push(
      card(
        '📥',
        'b',
        behind.length === 1 ? behind[0].label + ' waits behind the work in motion' : behind.length + ' programs wait behind the work in motion',
        list(behind.map((p) => p.label)) +
          ' hold ' +
          plural(open, 'open task') +
          ' and ' +
          plural(chk, 'check') +
          ' of which ' +
          pro +
          ' are proven. Starting them lowers the proven score until they are proven.',
        'Roadmap · programs not yet started',
        'consequences',
        'k-graph',
        'behind',
      ),
    );
  }
  if (!cards.length)
    return (
      <p className="at-note">
        Nothing in the data implies a consequence worth a card: no blocked program, no stale proof, no disagreeing phase label and no program
        waiting.
      </p>
    );
  return <div className="at-grid at-g4">{cards}</div>;
}
function GoBtnCard({ icon, tone, h, p, src, alt, anchor }: { icon: string; tone: string; h: string; p: string; src: string; alt: string; anchor: string }) {
  const { go } = useAtlas();
  return (
    <button type="button" className="at-card at-cq" onClick={() => go(alt, anchor)}>
      <span className={'at-ic at-ic-' + tone} aria-hidden="true">
        {icon}
      </span>
      <span className="at-cq-h">{h}</span>
      <span className="at-cq-p">{p}</span>
      <span className="at-src">{src} · open ›</span>
    </button>
  );
}

/* ---------- the altitude ---------- */
export function Cover({ M }: { M: Model }) {
  const name = M.project.name;
  const vision = M.project.vision;
  const c = M.counts;
  const bits = [
    name || 'Project',
    M.schema ? 'store schema ' + M.schema : '',
    plural(c.tasks, 'task'),
    plural(c.scope, 'scope item'),
    c.decisions != null ? plural(c.decisions, 'decision') : '',
  ].filter(Boolean);
  return (
    <>
      <section className="at-hero" id="atlas-c-what" data-q="1">
        <div className="at-eyebrow">
          <span className="at-chip at-t-i">Q1 · What is it?</span>
          {M.project.state ? <Chip text={M.project.state} tone="g" /> : null}
          <span>{bits.join(' · ')}</span>
        </div>
        <Story M={M} />
        <p className="at-story-src">
          Composed from the store, not typed.
          {vision ? (
            <>
              {' '}Vision: <i>{vision.length > 150 ? vision.slice(0, 150) + '…' : vision}</i>
            </>
          ) : (
            ' No vision is recorded.'
          )}
        </p>
      </section>
      <section className="at-section" id="atlas-c-reach" data-q="2">
        <div className="at-sec-h">
          <span className="at-qtag">Q2</span>
          <h2>How far does it go — from the first commit to the horizon</h2>
          <p>Ring = share of tasks done. Open any stop.</p>
          <GoBtn alt="map" anchor="m-fence" label="Open the map ›" />
        </div>
        <div className="at-card at-rail-card">
          <Rail M={M} />
          <div className="at-legend at-rail-legend">
            <LegendItem cls="at-w-done" text="work: tasks done" />
            <LegendItem cls="at-w-prog" text="in progress" />
            <LegendItem cls="at-p-proven" text="proof: checks passing, fresh" />
            <LegendItem cls="at-p-stale" text="stale" />
            <LegendItem cls="at-p-failed" text="failed" />
            <LegendItem cls="at-p-unrun" text="never run" />
            <span className="at-muted">Dates are observed; the roadmap commits to none.</span>
          </div>
        </div>
      </section>
      <section className="at-section" id="atlas-c-status" data-q="3">
        <div className="at-sec-h">
          <span className="at-qtag">Q3</span>
          <h2>Where are we, honestly</h2>
          <p>Work and proof are separate truths.</p>
        </div>
        <div className="at-grid at-g-bvp">
          <Bvp M={M} />
          <Waffle M={M} />
          <Needs M={M} />
        </div>
      </section>
      <section className="at-section" id="atlas-c-now" data-q="4">
        <div className="at-sec-h">
          <span className="at-qtag">Q4</span>
          <span className="at-qtag">Q5</span>
          <h2>Moving now · what comes next</h2>
        </div>
        <Trio M={M} />
      </section>
      <section className="at-section" id="atlas-c-follow" data-q="6">
        <div className="at-sec-h">
          <span className="at-qtag">Q6</span>
          <h2>What follows if nothing changes</h2>
          <p>Each card is a consequence the data already implies.</p>
          <GoBtn alt="consequences" label="Open consequences ›" />
        </div>
        <Cqs M={M} />
      </section>
    </>
  );
}