
/* =====================================================================
   ATLAS ALTITUDE 2 - MAP (2 minutes): what it is, the fence, per-goal proof, pace.
   ===================================================================== */
import type { ReactNode } from 'react';
import {
  PROOF,
  dayNum,
  dayOk,
  fmtDay,
  fmtRange,
  pc,
  programTone,
  shortId,
  shortName,
  sumv,
  type Model,
  type Program,
  type ScopeItem,
} from './model';
import { LegendItem, OpenBtn, PRIORITY_NAME, Stack, Tank, itemTip, workParts } from './ui';
import { EVIDENCE, evidenceSentence, gateSentence, type OwnerSummary } from '../evidence';

const plural = (n: number, one: string, many = one + 's'): string => n + ' ' + (n === 1 ? one : many);

/* ---------- what it is: the cutaway of the store's five layers ---------- */
export function Layers({ M }: { M: Model }) {
  const sc = M.scope;
  const by = (k: string) => sc.filter((s) => s.priority === k).length;
  const chk = sc.filter((s) => s.priority !== 'wont').reduce((a, s) => a + s.checks.length, 0);
  const c = M.counts;
  const L: [string, string, string, ReactNode, number][] = [
    [
      'WHY',
      'Why does it exist?',
      'context/Goal.md',
      <>
        <b>1 final goal</b> and <b>{plural(M.goals.length, 'sub-goal')}</b>, each naming the scope items that serve it
      </>,
      52,
    ],
    [
      'WHAT',
      'What must be true?',
      'context/Scope.md · scope/',
      <>
        <b>{plural(c.scope, 'scope item')}</b> — {by('must')} must · {by('should')} should · {by('could')} could · {by('wont')}{' '}
        won’t — with <b>{plural(chk, 'check')}</b> that prove them
      </>,
      64,
    ],
    [
      'HOW',
      'How is it built?',
      'Architecture.md · conventions/',
      <>
        The <b>architecture</b>, the <b>validation</b> strategy and the <b>conventions</b> every task must follow
      </>,
      76,
    ],
    [
      'WHEN',
      'In what order?',
      'when/',
      <>
        <b>{plural(c.phases, 'phase')}</b> · <b>{plural(c.milestones, 'milestone')}</b> ·{' '}
        <b>{plural(sumv(M.plans), 'gated scope plan')}</b>
      </>,
      88,
    ],
    [
      'NOW',
      'What is happening?',
      'what/tasks · now/',
      <>
        <b>{plural(c.tasks, 'task')}</b> ({c.taskStatus.done || 0} done) · <b>{plural(M.loops.claims.length, 'live claim')}</b> · a cursor
        that is computed, not written
      </>,
      100,
    ],
  ];
  const mem = [
    c.decisions != null ? <b key="d">{plural(c.decisions, 'decision')}</b> : null,
    'notes',
    c.risks != null ? <b key="r">{plural(c.risks, 'risk')}</b> : null,
    c.questions != null ? <b key="q">{plural(c.questions, 'question')}</b> : null,
    c.events != null ? <b key="e">{plural(c.events, 'logged event')}</b> : null,
  ].filter(Boolean);
  return (
    <div className="at-layers">
      {L.map((l) => (
        <div className="at-layer" style={{ width: l[4] + '%' }} key={l[0]}>
          <div className="at-lh">
            {l[0]}
            <span className="at-lq">{l[1]}</span>
          </div>
          <div>
            <div className="at-lc">{l[3]}</div>
            <div className="at-fd">{l[2]}</div>
          </div>
        </div>
      ))}
      <div className="at-layer at-mem">
        <div className="at-lh at-dim">
          MEMORY<span className="at-lq">What did we learn?</span>
        </div>
        <div>
          <div className="at-lc">
            {mem.map((m, i) => (
              <span key={i}>
                {i > 0 ? ' · ' : ''}
                {m}
              </span>
            ))}
            {c.events != null ? <span>, each stamped with who did it</span> : null}
          </div>
          <div className="at-fd">history/ · log/</div>
        </div>
      </div>
    </div>
  );
}

export function Journey({ M }: { M: Model }) {
  const k = M.eventKinds;
  const n = (x: string): number | null => (Object.hasOwn(k, x) ? k[x] : null);
  const claims = n('task.claim');
  const verifies = n('run.verify');
  const steps: [string, ReactNode][] = [
    ['Plan', <>A scope item’s plan must be <b>ready</b> before its tasks start ({M.plans.ready} ready, {M.plans.reconciled} reconciled).</>],
    ['Claim', <><code>start</code> takes a lease so two agents never hold one task{claims != null ? ' (' + claims + ' claim events)' : ''}.</>],
    ['Work', <>Writes go through checked tools; edits made outside them are caught by the doctor.</>],
    [
      'Prove',
      <>
        <code>done</code> runs the checks first{verifies != null ? ' (' + verifies + ' verify runs logged)' : ''}. Score is <b>proven</b>, never
        asserted.
      </>,
    ],
    ['Remember', <>Every change is an immutable, actor-stamped event; status is rebuilt from the log.</>],
  ];
  return (
    <ol className="at-journey">
      {steps.map((s, i) => (
        <li key={s[0]}>
          <span className="at-jn">{i + 1}</span>
          <span>
            <b>{s[0]}</b>
            <br />
            <span className="at-note">{s[1]}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

/* ---------- how far: the fence ---------- */
const TIERS: [string, string, string][] = [
  ['must', 'at-tone-must', 'the product is not done without these'],
  ['should', 'at-tone-should', 'important, not fatal if late'],
  ['could', 'at-tone-could', 'nice to have'],
  ['other', 'at-tone-could', 'priority not recorded'],
];
export function Tile({ M, s }: { M: Model; s: ScopeItem }) {
  const prog = M.P.get(s.program);
  return (
    <OpenBtn
      k="scope"
      id={s.id}
      cls={'at-tile' + (s.checks.length ? '' : ' at-nochk')}
      label={itemTip(s) + ' · ' + (prog ? prog.label : 'not placed in a program')}
      tipText={itemTip(s)}
    >
      <span className="at-tank">
        <Tank s={s} />
        <span className={'at-pdot ' + (prog ? 'at-pc' + programTone(prog) : 'at-pc-none')} />
      </span>
      <span className="at-tid">{shortId(s.id)}</span>
    </OpenBtn>
  );
}
export function Territory({ M }: { M: Model }) {
  const order = (s: ScopeItem): number => {
    const p = M.P.get(s.program);
    return p ? p.index : 99;
  };
  const inside = TIERS.map(([k, tone, why]) => {
    const items = M.scope.filter((s) => s.priority === k).sort((a, b) => order(a) - order(b) || a.id.localeCompare(b.id));
    if (!items.length) return null;
    const full = items.filter((s) => s.checks.length && s.checks.every((c) => c.cls === 'proven')).length;
    return (
      <div className="at-tier" key={k}>
        <div className="at-tier-h">
          <b className={tone}>{PRIORITY_NAME[k]}</b>
          <span>
            {plural(items.length, 'item')} · {full} fully proven · {why}
          </span>
        </div>
        <div className="at-tiles">
          {items.map((s) => (
            <Tile key={s.id} M={M} s={s} />
          ))}
        </div>
      </div>
    );
  });
  const wont = M.scope.filter((s) => s.priority === 'wont');
  const o = M.proof.official;
  const placed = M.programs.filter((p) => M.scope.some((s) => s.program === p.id));
  const unplaced = M.scope.filter((s) => s.priority !== 'wont' && !M.P.get(s.program));
  return (
    <div className="at-terr">
      <div className="at-in">
        {inside.some(Boolean) ? inside : <p className="at-note">No scope item is recorded.</p>}
        <div className="at-legend at-legend-top">
          <span className="at-muted">Each vessel is one scope item, filled by its checks:</span>
          {PROOF.map((k) => (
            <LegendItem key={k} cls={'at-p-' + k} text={k === 'unrun' ? 'never run' : k} />
          ))}
        </div>
        <div className="at-legend at-mt6">
          <span className="at-muted">Dot = the program that delivers it:</span>
          {placed.map((p) => (
            <span key={p.id}>
              <i className={'at-sw at-dotsw at-pc' + programTone(p)} />
              {shortName(p.label)}
            </span>
          ))}
          {unplaced.length ? (
            <span>
              <i className="at-sw at-dotsw at-pc-none" />
              not placed in a program
            </span>
          ) : null}
        </div>
        {unplaced.length ? (
          <div className="at-unplaced">
            <div className="at-tier-h">
              <b>Not placed in a program</b>
              <span>{plural(unplaced.length, 'item')} no program delivers yet</span>
            </div>
            <div className="at-tiles">
              {unplaced.map((s) => (
                <Tile key={s.id} M={M} s={s} />
              ))}
            </div>
          </div>
        ) : null}
      </div>
      <div className="at-out">
        <div className="at-tier-h at-mb8">
          <b className="at-dim2">Outside the wall</b>
        </div>
        <div className="at-note at-mb8">
          Won’t — deliberately excluded.
          {o.wont_total != null && o.wont_held != null ? (
            <>
              {' '}Held <b>{o.wont_held}/{o.wont_total}</b>.
            </>
          ) : null}
        </div>
        {wont.length ? (
          wont.map((s) => (
            <div className="at-brick" key={s.id}>
              <i aria-hidden="true">🔒</i>
              <span>
                <b>{s.id}</b> {s.title}
              </span>
            </div>
          ))
        ) : (
          <p className="at-note">Nothing is excluded.</p>
        )}
      </div>
    </div>
  );
}

/* ---------- goal -> scope -> work -> proof ---------- */
export function Ladder({ M }: { M: Model }) {
  const mini = (id: string) => {
    const s = M.SC.get(id);
    return s ? (
      <OpenBtn k="scope" id={id} cls="at-mt" label={itemTip(s)} tipText={itemTip(s)} key={id}>
        <Tank s={s} />
      </OpenBtn>
    ) : (
      <span className="at-chip at-t-a" key={id}>
        {id}
      </span>
    );
  };
  const rows: ReactNode[] = [];
  rows.push(
    <div className="at-lrow" role="row" key="head">
      <div className="at-hd" role="columnheader">Sub-goal</div>
      <div className="at-hd" role="columnheader">Scope items</div>
      <div className="at-hd" role="columnheader">Work (tasks)</div>
      <div className="at-hd" role="columnheader">Proof: proven · claimed</div>
      <div className="at-hd at-r" role="columnheader">Gap</div>
    </div>,
  );
  for (const g of M.goals) {
    const e = g.exec;
    const pr = pc(g.proof.proven);
    const ck = pc(g.proof.checked);
    const gap = pr != null && ck != null ? ck - pr : null;
    const exec = { done: e.done, 'in-progress': e.in_progress, blocked: e.blocked, todo: e.todo };
    const proofCell =
      pr == null && ck == null ? (
        <div className="at-pfx">
          <span className="at-muted">{EVIDENCE.not_assessed.word}</span>
        </div>
      ) : (
        <div className="at-pfx">
          <div className="at-meter" role="img" aria-label={'Proven ' + (pr ?? 'unknown') + '%, claimed ' + (ck ?? 'unknown') + '%'}>
            <i className="at-claimed" style={{ width: (ck ?? 0) + '%' }} />
            <i className="at-proven" style={{ width: (pr ?? 0) + '%' }} />
          </div>
          <b>
            {pr == null ? '—' : pr + '%'} · {ck == null ? '—' : ck + '%'}
          </b>
        </div>
      );
    rows.push(
      <div className="at-lrow" role="row" key={g.id}>
        <div className="at-c at-gc" role="cell">
          <div className="at-gl">
            {g.label}
            <small>
              {g.items.join(' · ')}
              {g.gaps.length ? (
                <>
                  {' · '}
                  <span className="at-warn">⚑ {plural(g.gaps.length, 'gap')}</span>
                </>
              ) : null}
            </small>
          </div>
        </div>
        <div className="at-c " role="cell">
          <div className="at-mtiles">{g.items.map(mini)}</div>
        </div>
        <div className="at-c " role="cell">
          <div className="at-pfx">
            <Stack parts={workParts(exec)} />
            <b>
              {e.done}/{e.total}
            </b>
          </div>
        </div>
        <div className="at-c " role="cell">
          {proofCell}
        </div>
        <div className={'at-c at-gapnum' + (gap != null && gap >= 35 ? ' at-gap-hi' : '')} role="cell">
          {gap ? gap + ' pt' : '—'}
        </div>
      </div>,
    );
  }
  const un = M.unlinked;
  rows.push(
    <div className="at-lrow" role="row" key="unlinked">
      <div className="at-c at-gc" role="cell">
        <div className="at-gl at-dim">
          Under no sub-goal
          <small>
            {plural(un.length, 'item')} nobody asked for, or nobody linked
          </small>
        </div>
      </div>
      <div className="at-c " role="cell">
        <div className="at-mtiles">{un.map(mini)}</div>
      </div>
      <div className="at-c " role="cell" />
      <div className="at-c " role="cell" />
      <div className="at-c " role="cell" />
    </div>,
  );
  return (
    <div className="at-lad" role="table" aria-label="Sub-goals: scope items, work, proof and the gap between claimed and proven">
      {rows}
    </div>
  );
}

/* ---------- pace ---------- */
const GAP_DAYS = 14;
interface Burst { min: number; max: number; cols: number; dayW: number; x0: number }
export interface PaceAxis {
  bursts: Burst[];
  X0: number;
  end: number;
  W: number;
  x: (isoDay: string) => number;
  dayW: (isoDay: string) => number;
}
export function paceAxis(M: Model): PaceAxis | null {
  const days = new Set<number>();
  const add = (d: string) => {
    if (dayOk(d)) days.add(dayNum(d));
  };
  for (const p of M.programs) [p.start, p.end, p.lastTouch].forEach(add);
  M.pace.forEach((p) => add(p.date));
  M.milestones.forEach((m) => add(m.date));
  add(M.asOfDay);
  const sorted = [...days].sort((a, b) => a - b);
  if (!sorted.length) return null;
  const bursts: Burst[] = [];
  for (const d of sorted) {
    const last = bursts[bursts.length - 1];
    if (last && d - last.max <= GAP_DAYS) last.max = d;
    else bursts.push({ min: d, max: d, cols: 0, dayW: 0, x0: 0 });
  }
  const X0 = 150;
  const PLOT = 940;
  const BRK = 44;
  for (const b of bursts) {
    b.min -= 1;
    b.max += 1;
    b.cols = b.max - b.min + 1;
  }
  const avail = PLOT - BRK * (bursts.length - 1);
  const weights = bursts.map((b, i) => b.cols * (i === bursts.length - 1 ? 4 : 1));
  const wsum = weights.reduce((a, b) => a + b, 0);
  let x = X0;
  bursts.forEach((b, i) => {
    b.dayW = Math.min(72, (avail * weights[i]) / wsum / b.cols);
    b.x0 = x;
    x += b.cols * b.dayW + BRK;
  });
  const end = x - BRK;
  return {
    bursts,
    X0,
    end,
    W: Math.max(end + 24, 620),
    x(isoDay: string) {
      const n = dayNum(isoDay);
      const b = bursts.find((q) => n >= q.min && n <= q.max) || (n < bursts[0].min ? bursts[0] : bursts[bursts.length - 1]);
      const c = Math.max(b.min, Math.min(b.max, n));
      return b.x0 + (c - b.min) * b.dayW;
    },
    dayW(isoDay: string) {
      const n = dayNum(isoDay);
      const b = bursts.find((q) => n >= q.min && n <= q.max) || bursts[bursts.length - 1];
      return b.dayW;
    },
  };
}
const iso = (n: number): string => new Date(n * 864e5).toISOString().slice(0, 10);
const MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

export interface PaceView {
  svg: ReactNode;
  note: string;
  peak?: { date: string; events: number; done: number };
}
export function pace(M: Model): PaceView {
  const ax = paceAxis(M);
  if (!ax)
    return { svg: null, note: 'No date is recorded for any program, event or milestone, so there is no timeline to draw.' };
  const rows = M.programs;
  const rowY = (i: number): number => 112 + i * 25;
  const H = rowY(rows.length) + 56;
  const ids = ax.bursts;
  const out: ReactNode[] = [];
  ids.forEach((b, bi) => {
    const step = Math.max(1, Math.ceil(16 / b.dayW));
    let month = -1;
    for (let n = b.min; n <= b.max; n++) {
      const d = new Date(n * 864e5);
      const x = b.x0 + (n - b.min) * b.dayW;
      out.push(<line key={'g' + n} x1={x} x2={x} y1={20} y2={H - 36} stroke="var(--divider)" />);
      if ((n - b.min) % step === 0)
        out.push(
          <text key={'t' + n} x={x + b.dayW / 2} y={14} textAnchor="middle">
            {d.getUTCDate()}
          </text>,
        );
      if (d.getUTCMonth() !== month) {
        month = d.getUTCMonth();
        out.push(
          <text key={'m' + n} x={x + 2} y={-1} className="at-bold">
            {MONTH[month] + ' ' + d.getUTCFullYear()}
          </text>,
        );
      }
    }
    const nx = ids[bi + 1];
    if (nx) {
      const gx = b.x0 + b.cols * b.dayW;
      const quiet = Math.round(nx.min - b.max - 1);
      out.push(
        <path
          key={'brk' + bi}
          d={
            'M' + (gx + 8) + ' 22 l7 10 l-7 10 l7 10 l-7 10 M' + (gx + 20) + ' 22 l7 10 l-7 10 l7 10 l-7 10'
          }
          fill="none"
          stroke="var(--border-strong)"
          strokeWidth="1.5"
        />,
      );
      out.push(
        <text key={'qb' + bi} x={gx + 22} y={70} textAnchor="middle" className="at-tiny">
          {quiet} quiet
        </text>,
        <text key={'qd' + bi} x={gx + 22} y={81} textAnchor="middle" className="at-tiny">
          days
        </text>,
      );
    }
  });
  const max = Math.max(...M.pace.map((p) => p.events), 1);
  out.push(
    <text key="evh" x={0} y={46} className="at-rl">
      Log events / day
    </text>,
    <text key="evs" x={0} y={60} className="at-tiny">
      per observed day
    </text>,
  );
  for (const p of M.pace) {
    const x = ax.x(p.date);
    const dw = ax.dayW(p.date);
    const h = 3 + (38 * p.events) / max;
    out.push(
      <rect
        key={'bar' + p.date}
        x={x + 5}
        y={74 - h}
        width={Math.max(2, dw - 10)}
        height={h}
        rx="3"
        fill="var(--accent)"
        opacity=".75"
        data-atip={fmtDay(p.date) + ': ' + p.events + ' events, ' + p.done + ' tasks last updated as done'}
      />,
    );
    if (p.events > max * 0.15)
      out.push(
        <text key={'bl' + p.date} x={x + dw / 2} y={74 - h - 3} textAnchor="middle" className="at-tiny">
          {p.events}
        </text>,
      );
  }
  out.push(<line key="ax" x1={ax.X0} x2={ax.end} y1={76} y2={76} stroke="var(--border-strong)" />);
  rows.forEach((p: Program, i: number) => {
    const y = rowY(i);
    const label = p.label.length > 24 ? shortName(p.label, 22) : p.label;
    const work = p.n ? p.tasks.done + '/' + p.n + ' tasks' : p.phases_done + '/' + p.phases_n + ' phases';
    if (p.vis === 'later' || !p.start) {
      out.push(
        <PaceRow key={p.id} p={p} y={y} W={ax.W} tipText="not dated">
          <text x={0} y={y + 14} className="at-rl">
            {label}
          </text>
          <text x={ax.X0} y={y + 14} className="at-ital">
            not dated
          </text>
        </PaceRow>,
      );
      return;
    }
    const endIso = p.vis === 'done' ? p.end || p.start : p.vis === 'built' ? p.end || p.lastTouch || p.start : M.asOfDay || p.lastTouch || p.start;
    const x1 = ax.x(p.start) + 2;
    const x2 = ax.x(endIso) + ax.dayW(endIso) - 2;
    const w = Math.max(10, x2 - x1);
    const cls = p.vis === 'blocked' ? 'at-bar-block' : p.vis === 'next' ? 'at-bar-next' : 'at-bar-work';
    const bar: ReactNode[] = [<rect key="b" x={x1} y={y + 2} width={w} height={17} rx={5} className={cls} />];
    if (p.n && p.vis !== 'done')
      bar.push(<rect key="d" x={x1} y={y + 2} width={(w * p.tasks.done) / p.n} height={17} rx={5} className="at-bar-work" />);
    if (w > 60)
      bar.push(
        <text key="t" x={x1 + 8} y={y + 14.5} className="at-bar-t">
          {p.n ? p.tasks.done + '/' + p.n : p.phases_done + '/' + p.phases_n + ' phases'}
        </text>,
      );
    if (p.vis === 'now' || p.vis === 'next' || p.vis === 'blocked')
      bar.push(<path key="a" d={'M' + (x2 + 2) + ' ' + (y + 6) + ' l7 5 l-7 5'} fill="none" stroke="var(--text-4)" strokeWidth="1.6" />);
    out.push(
      <PaceRow key={p.id} p={p} y={y} W={ax.W} tipText={fmtRange(p.start, endIso) + ' · ' + work}>
        <text x={0} y={y + 14} className="at-rl">
          {label}
        </text>
        {bar}
      </PaceRow>,
    );
  });
  if (M.asOfDay) {
    const tx = ax.x(M.asOfDay) + ax.dayW(M.asOfDay) / 2;
    out.push(
      <line key="today" x1={tx} x2={tx} y1={20} y2={H - 36} stroke="var(--accent)" strokeWidth="1.5" strokeDasharray="4 3" />,
      <text key="todayt" x={tx} y={H - 24} textAnchor="middle" className="at-today">
        as of {fmtDay(M.asOfDay)}
      </text>,
    );
  }
  const ms = new Map<string, { id: string; title: string; date: string }[]>();
  for (const m of M.milestones) ms.set(m.date, [...(ms.get(m.date) || []), m]);
  const my = rowY(rows.length) + 6;
  out.push(
    <text key="msh" x={0} y={my + 11} className="at-rl">
      Milestones
    </text>,
  );
  for (const [d, msList] of ms) {
    const x = ax.x(d) + ax.dayW(d) / 2;
    const text = fmtDay(d) + ': ' + msList.map((m) => m.title).join('; ');
    out.push(
      <g key={'ms' + d} role="img" aria-label={'Milestone — ' + text} data-atip={text}>
        <path d={'M' + x + ' ' + (my + 1) + ' l6 6 l-6 6 l-6 -6z'} fill="var(--violet)" />
        {msList.length > 1 ? (
          <text x={x} y={my + 25} textAnchor="middle" className="at-tiny">
            {msList.length}
          </text>
        ) : null}
      </g>,
    );
  }
  const svg = (
    <svg viewBox={'0 -18 ' + ax.W + ' ' + (H + 18)} role="group" aria-label="Timeline of programs and daily activity; the same numbers are listed below">
      {out}
    </svg>
  );
  const peak = M.pace.slice().sort((a, b) => b.events - a.events)[0];
  const burstText = ids.map((b) => fmtRange(iso(b.min + 1), iso(b.max - 1)));
  const quiet = ids.slice(1).map((b, i) => Math.round(b.min - ids[i].max - 1));
  const note =
    'Observed, not planned: the roadmap commits to no dates. ' +
    (ids.length > 1
      ? plural(ids.length, 'burst') + ' of activity (' + burstText.join('; ') + '), separated by ' + quiet.join(' and ') + ' quiet days. '
      : 'All activity falls in one stretch (' + burstText[0] + '). ') +
    (peak && peak.events ? 'Busiest day: ' + fmtDay(peak.date) + ' (' + peak.events + ' events).' : 'No log events are recorded per day.');
  return { svg, note, peak };
}
function PaceRow({ p, y, W, tipText, children }: { p: Program; y: number; W: number; tipText: string; children: ReactNode }) {
  const { open } = useAtlasRow();
  return (
    <g
      className="at-prog-row"
      role="button"
      tabIndex={0}
      aria-label={p.label + ' — ' + tipText}
      data-atip={p.label + ' · ' + tipText}
      onClick={() => open('prog', p.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open('prog', p.id);
        }
      }}
    >
      <rect x={0} y={y - 1} width={W} height={23} fill="transparent" />
      {children}
    </g>
  );
}
function useAtlasRow() {
  // The row opens the program drawer exactly like an OpenBtn; reuse the same context.
  return useAtlasHandlers();
}
import { useAtlas } from './ui';
const useAtlasHandlers = useAtlas;

export function PaceTable({ M }: { M: Model }) {
  if (!M.pace.length) return null;
  return (
    <details className="at-alt-data">
      <summary>Daily numbers as a table</summary>
      <table>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Log events</th>
            <th scope="col">Tasks last updated as done</th>
          </tr>
        </thead>
        <tbody>
          {M.pace.map((p) => (
            <tr key={p.date}>
              <th scope="row">{fmtDay(p.date)}</th>
              <td>{p.events}</td>
              <td>{p.done}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/* ---------- the altitude ---------- */
export function MapAltitude({ M, owner = null }: { M: Model; owner?: OwnerSummary | null }) {
  const o = M.proof.official;
  const pa = pace(M);
  const inside = M.scope.filter((s) => s.priority !== 'wont').length;
  const out = M.scope.filter((s) => s.priority === 'wont').length;
  const tight = M.goals.filter((g) => g.exec.total && g.exec.done === g.exec.total && g.proof.proven != null && g.proof.proven < 0.6).length;
  return (
    <>
      <section className="at-section at-first" id="atlas-m-what" data-q="1">
        <div className="at-sec-h">
          <span className="at-qtag">Q1</span>
          <h2>What is it, and how does work travel through it</h2>
          <p>The five layers every project carries, and the loop that keeps them true.</p>
        </div>
        <div className="at-grid at-g-what">
          <div className="at-card">
            <h3>
              The store, as a cutaway <span className="at-sub">abstract at the top, concrete at the bottom</span>
            </h3>
            <Layers M={M} />
          </div>
          <div className="at-card">
            <h3>How a task travels</h3>
            <Journey M={M} />
          </div>
        </div>
        {/* Evidence and gates come from the sourced owner-summary read, never
            from the Atlas model: the Atlas read carries no claim, so an absent
            answer is unknown - never "open", "verified" or "pending". */}
        <div className="at-card at-evidence" data-atlas="evidence" style={{ marginTop: 12 }}>
          <h3>Evidence and gates <span className="at-sub">only what a read says</span></h3>
          <p className="at-note" data-atlas="product-evidence">
            {evidenceSentence(owner?.product ?? null) || ('Product evidence: ' + EVIDENCE.unknown.word + ' - '
              + (owner ? owner.productReason : 'the owner summary read has not answered') + '.')}
            {owner?.product?.source ? ' Source: ' + owner.product.source + '.' : ''}
          </p>
          {(owner?.gates.length ?? 0) > 0 ? (
            <ul className="at-note" data-atlas="gates">
              {(owner?.gates ?? []).map((gate) => (
                <li key={gate.id} data-gate-id={gate.id} data-gate-state={gate.state}>{gateSentence(gate)}</li>
              ))}
            </ul>
          ) : (
            <p className="at-note" data-atlas="gates" data-gates="unknown">No conditional gate is authored in this read: the gate state is {EVIDENCE.unknown.word}.</p>
          )}
        </div>
      </section>
      <section className="at-section" id="atlas-m-fence" data-q="2">
        <div className="at-sec-h">
          <span className="at-qtag">Q2</span>
          <h2>How far does it go — the fence</h2>
          <p>
            {inside} things inside, {out} deliberately outside. Proven today:{' '}
            <b>{pc(o.final_score) == null ? 'unknown' : pc(o.final_score) + '%'}</b>.
          </p>
        </div>
        <Territory M={M} />
      </section>
      <section className="at-section" id="atlas-m-ladder" data-q="3">
        <div className="at-sec-h">
          <span className="at-qtag">Q3</span>
          <h2>From goal to proof, per sub-goal</h2>
          <p>Work can be 100% while proof is half: that gap is what is left to earn.</p>
        </div>
        <div className="at-card">
          <Ladder M={M} />
          <p className="at-note at-mt12">
            {plural(tight, 'sub-goal')}
            {tight === 1 ? ' is' : ' are'} fully executed yet below 60% proven. ⚑ marks a missing link (e.g. a Must item that no task delivers).
          </p>
        </div>
      </section>
      <section className="at-section" id="atlas-m-pace" data-q="3">
        <div className="at-sec-h">
          <span className="at-qtag">Q3</span>
          <h2>Pace — what actually happened, when</h2>
          <p>Observed, not planned.</p>
        </div>
        <div className="at-card at-pace">
          {pa.svg ? <div className="at-pace-scroll">{pa.svg}</div> : null}
          <p className="at-note">{pa.note}</p>
          <PaceTable M={M} />
        </div>
      </section>
    </>
  );
}