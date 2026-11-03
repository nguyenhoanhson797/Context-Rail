/* =====================================================================
   ATLAS ALTITUDE 3 - CONSEQUENCES (React): what follows.
   The unlock graph with its blast radius, the proof-decay chain, label vs
   truth, and what is waiting for how long.
   ===================================================================== */
import { useState, type ReactNode } from 'react';
import { PROOF, daysBetween, fmtDay, provenOf, type Model, type Phase, type ScopeItem } from './model';
import { blast, graphOf, nameOf, plural, reach, wrap, type GEdge, type GNode, type Graph } from './graph';
import { Chip, DocLink, OpenBtn, PROOF_LABEL, LegendItem, mustState, workParts, Stack, useAtlas } from './ui';

const DEFAULT_INFO = (
  <>
    <b>Hover or focus a phase</b> to light up what it unlocks (violet) and what it waits on (teal). <b>Solid lines</b> are dependencies a
    phase file states (“Depends on …”); <b>dashed lines</b> are roadmap order only — an order is a briefing constraint, not a
    scheduler edge. Where there is no line the dependency is <i>unknown</i>, not absent. The amber <b>!</b> marks a phase whose label
    contradicts its tasks.
  </>
);

/* ---------- one node ---------- */
function NodeShape({ M, n, rel }: { M: Model; n: GNode; rel: 'self' | 'down' | 'up' | null }) {
  const ph: Phase | null = n.ph;
  const prog = M.programs.find((p) => p.id === n.program);
  const gate = !!prog && prog.vis === 'blocked';
  const hasEdge = M.edges.some((e) => e.src === n.id || e.dst === n.id);
  let cls = 'at-gn';
  let sub = '';
  let pctv = 0;
  let mismatch = '';
  if (ph) {
    pctv = ph.derived_pct != null ? Math.max(0, Math.min(100, ph.derived_pct)) : ph.n ? Math.round((100 * ph.tasks.done) / ph.n) : 0;
    sub = ph.n ? pctv + '% · ' + ph.tasks.done + '/' + ph.n + ' tasks' : 'no tasks';
    if (ph.derived === 'done') cls += ' at-s-done';
    if (ph.derived === 'blocked' || (gate && ph.tasks.blocked)) cls += ' at-s-blocked';
    if (ph.mismatch)
      mismatch =
        'Label says “' +
        ph.recorded +
        '” (' +
        (ph.recorded_progress == null ? 'progress unknown' : ph.recorded_progress + '%') +
        '), tasks say ' +
        (ph.derived_pct == null ? 'nothing' : ph.derived_pct + '% done');
    if (gate && !hasEdge) {
      cls += ' at-s-gate';
      sub = sub + ' · waiting on a person';
    }
  } else {
    cls += ' at-s-ghost';
    sub = 'no phase record';
  }
  if (rel === 'self') cls += ' at-on-self';
  if (rel === 'down') cls += ' at-on-down';
  if (rel === 'up') cls += ' at-on-up';
  const title = ph ? ph.title : n.id;
  const lines = wrap(title, 19, 2);
  const label = title + '. ' + sub + '.' + (mismatch ? ' ' + mismatch + '.' : '');
  const t2y = n.y + 19 + lines.length * 15 + 3;
  const { open } = useAtlas();
  return (
    <g
      className={cls}
      tabIndex={0}
      role={ph ? 'button' : 'group'}
      aria-label={label}
      onClick={ph ? () => open('phase', n.id) : undefined}
      onKeyDown={
        ph
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                open('phase', n.id);
              }
            }
          : undefined
      }
    >
      <rect className="at-nb" x={n.x} y={n.y} width={148} height={70} rx={9} />
      {lines.map((l, i) => (
        <text className="at-t1" x={n.x + 10} y={n.y + 19 + i * 15} key={i}>
          {l}
        </text>
      ))}
      <text className="at-t2" x={n.x + 10} y={t2y}>
        {sub.length > 30 ? sub.slice(0, 29) + '…' : sub}
      </text>
      {ph ? (
        <>
          <rect x={n.x + 10} y={n.y + 58} width={128} height={5} rx={2.5} fill="var(--surface-3)" />
          <rect x={n.x + 10} y={n.y + 58} width={((128 * pctv) / 100).toFixed(1)} height={5} rx={2.5} fill="var(--work)" />
        </>
      ) : null}
      {mismatch ? (
        <g data-atip={mismatch}>
          <circle cx={n.x + 147} cy={n.y + 1} r={9} fill="var(--amber)" stroke="var(--surface)" strokeWidth={2} />
          <text x={n.x + 147} y={n.y + 5} textAnchor="middle" className="at-bang">
            !
          </text>
        </g>
      ) : null}
    </g>
  );
}
/* ---------- the graph ---------- */
function edgePath(a: GNode, b: GNode): string {
  const x1 = a.x + 148;
  const y1 = a.y + 35;
  const x2 = b.x - 2;
  const y2 = b.y + 35;
  return y1 === y2
    ? 'M' + x1 + ' ' + y1 + ' L' + x2 + ' ' + y2
    : 'M' + x1 + ' ' + y1 + ' C' + (x1 + 30) + ' ' + y1 + ' ' + (x2 - 30) + ' ' + y2 + ' ' + x2 + ' ' + y2;
}

export function GraphView({ M, showFinished, onToggleFinished }: { M: Model; showFinished: boolean; onToggleFinished: () => void }) {
  const G = graphOf(M, showFinished);
  const [active, setActive] = useState<string | null>(null);
  const down = active ? reach(G, active, false) : new Set<string>();
  const up = active ? reach(G, active, true) : new Set<string>();
  const rel = (id: string): 'self' | 'down' | 'up' | null =>
    !active ? null : id === active ? 'self' : down.has(id) ? 'down' : up.has(id) ? 'up' : null;
  const edgeRel = (e: GEdge): 'down' | 'up' | null => {
    if (!active) return null;
    if ((e.src === active || down.has(e.src)) && down.has(e.dst)) return 'down';
    if ((e.dst === active || up.has(e.dst)) && up.has(e.src)) return 'up';
    return null;
  };
  if (!G.nodes.length && G.hidden)
    return (
      <p className="at-note">
        Every phase in the graph is finished ({G.hidden}), so nothing is left to unlock. Use “Show finished phases” to see the history.
      </p>
    );
  if (!G.nodes.length)
    return (
      <p className="at-note">
        No dependency between phases is declared, and no program is blocked. That is <i>unknown</i>, not absent: a phase file states a
        dependency with “Depends on …”.
      </p>
    );
  return (
    <>
      <div className={'at-gwrap' + (active ? ' at-hl' : '')} id="at-gbox" data-keep="at-gbox">
        <svg
          viewBox={'0 0 ' + G.W + ' ' + G.H}
          role="group"
          aria-label="Phase dependency graph; the same links are listed under the graph"
          onMouseLeave={() => setActive(null)}
        >
          <defs>
            <marker id="at-ar" viewBox="0 0 10 10" refX={9} refY={5} markerWidth={7} markerHeight={7} orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10z" fill="context-stroke" />
            </marker>
          </defs>
          {G.lanes.map((l) => (
            <text className="at-lane-l" x={18} y={l.y + 14} key={'lane' + l.id}>
              {l.label}
            </text>
          ))}
          <g aria-hidden="true">
            {G.edges.map((e) => {
              const a = G.byId.get(e.src);
              const b = G.byId.get(e.dst);
              if (!a || !b) return null;
              const er = edgeRel(e);
              return (
                <path
                  key={e.src + '>' + e.dst}
                  className={'at-ge at-' + e.kind + (er ? ' at-on-' + er : '')}
                  data-s={e.src}
                  data-d={e.dst}
                  d={edgePath(a, b)}
                  markerEnd="url(#at-ar)"
                >
                  <title>{(e.kind === 'authored' ? 'Authored dependency: ' : 'Roadmap order only: ') + (e.why || 'no reason recorded')}</title>
                </path>
              );
            })}
          </g>
          {G.nodes.map((n) => (
            <g
              key={n.id}
              onMouseEnter={() => setActive(n.id)}
              onFocus={() => setActive(n.id)}
              onBlur={() => setActive(null)}
            >
              <NodeShape M={M} n={n} rel={rel(n.id)} />
            </g>
          ))}
        </svg>
      </div>
      <div className="at-legend at-mt8">
        <span>
          <svg className="at-lg" width="30" height="8" aria-hidden="true">
            <path d="M0 4H30" className="at-ge at-authored" />
          </svg>
          states a dependency
        </span>
        <span>
          <svg className="at-lg" width="30" height="8" aria-hidden="true">
            <path d="M0 4H30" className="at-ge at-ordered" />
          </svg>
          roadmap order only
        </span>
        <span className="at-muted">no line = unknown, not absent</span>
        <span className="at-muted at-gscroll">The graph is wider than this panel: scroll sideways, or Tab through the phases.</span>
        {showFinished || G.hidden ? (
          <button type="button" className="at-link" aria-pressed={showFinished} onClick={onToggleFinished}>
            {showFinished ? 'Hide finished phases' : 'Show ' + G.hidden + ' finished ' + (G.hidden === 1 ? 'phase' : 'phases')}
          </button>
        ) : null}
      </div>
      <div className="at-ginfo" id="at-ginfo" aria-live="polite">
        <BlastInfo M={M} G={G} id={active} />
      </div>
      <GraphList M={M} showFinished={showFinished} />
    </>
  );
}

/* The explanation under the graph: the blast radius of the phase under the pointer, in words. */
export function BlastInfo({ M, G, id }: { M: Model; G: Graph; id: string | null }) {
  if (!id) return <>{DEFAULT_INFO}</>;
  const b = blast(M, G, id);
  if (!b) return <>{DEFAULT_INFO}</>;
  return (
    <>
      <b>{b.name}</b>
      {' — '}
      {!b.down.length ? (
        <>
          nothing in the roadmap is recorded as waiting on it. That is either a clean leaf or a missing link — the data cannot say
          which.
        </>
      ) : (
        <>
          if it slips, <b>{b.down.length}</b> downstream {plural(b.down.length, 'step')} ({b.down.join(' → ')}){' '}
          {b.openTasks || b.scopeItems ? (
            <>
              carry <b>{b.openTasks}</b> open {plural(b.openTasks, 'task')}
              {b.scopeItems ? (
                <>
                  {' '}touching <b>{b.scopeItems}</b> scope {plural(b.scopeItems, 'item')}
                </>
              ) : null}
            </>
          ) : (
            'are held up'
          )}
          .
          {b.ordered ? (
            <>
              {' '}
              <span className="at-muted">
                {b.ordered} of those links {b.ordered === 1 ? 'is' : 'are'} order only.
              </span>
            </>
          ) : null}
        </>
      )}
      {b.up.length ? (
        <>
          {' '}It waits on <b>{b.up.join(' ← ')}</b>.
        </>
      ) : (
        ' It waits on nothing.'
      )}
    </>
  );
}

/* The same graph as a list, for a reader who cannot see the picture. */
export function GraphList({ M, showFinished }: { M: Model; showFinished: boolean }) {
  const G = graphOf(M, showFinished);
  if (!G.edges.length) return null;
  return (
    <details className="at-alt-data">
      <summary>The dependencies as a list ({G.edges.length})</summary>
      <ul className="at-deps">
        {G.edges.map((e) => (
          <li key={e.src + '>' + e.dst}>
            {nameOf(M, e.src)} → {nameOf(M, e.dst)} <Chip text={e.kind === 'authored' ? 'authored' : 'order only'} tone={e.kind === 'authored' ? 'i' : ''} />
            {e.why ? <> <span className="at-muted">{e.why}</span></> : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

/* ---------- proof decay ---------- */
function HmRow({ s }: { s: ScopeItem }) {
  const st = mustState(s);
  const pro = provenOf(s);
  const tick = st.k === 'ok' ? '✓' : st.k === 'ceil' ? '↻' : '✕';
  return (
    <OpenBtn k="scope" id={s.id} cls="at-hm-row" label={s.id + ' ' + s.title + ': ' + pro + ' of ' + s.checks.length + ' checks proven; ' + st.text}>
      <span className="at-hm-id">{s.id}</span>
      <span className="at-hm-t" data-atip={s.title}>
        {s.title}
      </span>
      <span className="at-sq">
        {s.checks.map((c, i) => (
          <i key={i} className={'at-p-' + c.cls} data-atip={c.text + ' — ' + c.cls} />
        ))}
      </span>
      <span className={'at-tick at-tk-' + st.k} data-atip={st.text} aria-hidden="true">
        {tick}
      </span>
    </OpenBtn>
  );
}
export function Decay({ M }: { M: Model }) {
  const st = M.proof.stale;
  const cl = M.proof.classes;
  const mt = M.proof.must_total;
  const mustNow = M.proof.must_proven_now;
  const mustCeil = M.proof.must_if_stale_repass;
  const big = (n: number | string, label: ReactNode, tone?: string) => (
    <div className="at-big">
      <div className={'at-big-n' + (tone ? ' at-big-' + tone : '')}>{n}</div>
      <div className="at-note">{label}</div>
    </div>
  );
  const arrow = (
    <div className="at-big-arrow" aria-hidden="true">
      →
    </div>
  );
  const sorted = (ks: string[]) =>
    M.scope
      .filter((s) => ks.includes(s.priority) && s.checks.length)
      .sort((a, b) => provenOf(a) / a.checks.length - provenOf(b) / b.checks.length || a.id.localeCompare(b.id));
  const chain =
    st.files || cl.stale ? (
      <>
        <div className="at-chain">
          {big(st.files, <>files changed{st.since ? <> since <span className="at-mono">{st.since}</span></> : null}</>)}
          {arrow}
          {big(cl.stale, 'passing checks went stale', 'a')}
          {arrow}
          {big(Math.max(0, mustCeil - mustNow), 'Must items fell out of “proven”', 'a')}
          {arrow}
          {big(mustNow + '/' + mt, 'Must proven today', 'r')}
          {arrow}
          {big(mustCeil + '/' + mt, 'if the stale checks simply re-pass', 'g')}
        </div>
        {st.composition.length ? (
          <div className="at-comp">
            <span className="at-muted">What the {st.files} files were:</span>
            {st.composition.map(([k, v]) => (
              <span key={k} className={/engine/i.test(k) ? 'at-comp-eng' : undefined}>
                <b>{v}</b> {k}
              </span>
            ))}
          </div>
        ) : null}
      </>
    ) : (
      <p className="at-note">No proof is recorded as stale: nothing in the repo changed since the last proof run, or the read did not report it.</p>
    );
  const must = sorted(['must']);
  const rest = sorted(['should', 'could', 'other']);
  return (
    <>
      <div className="at-card">
        {chain}
        <p className="at-note at-mt8">
          The freshness rule asks <i>“did anything in the repo change since this check last passed?”</i>, not{' '}
          <i>“did anything this check covers change?”</i> That is why a suite that tests unrelated code can read stale after
          unrelated work. Declaring what each check covers would make proof decay only where it should. The last{' '}
          <b>{Math.max(0, mt - mustCeil)}</b> Must items need real fixes or a first run — no re-run will help them. The re-pass figure
          is an upper bound, not a forecast.
        </p>
      </div>
      <div className="at-grid at-g2 at-mt14">
        <div className="at-card">
          <div className="at-hm-h">
            <span>Must · worst first</span>
            <span>checks ▪ re-run</span>
          </div>
          {must.length ? must.map((s) => <HmRow key={s.id} s={s} />) : <p className="at-note">No Must item has a check.</p>}
        </div>
        <div className="at-card">
          <div className="at-hm-h">
            <span>Should &amp; Could</span>
            <span>checks ▪ re-run</span>
          </div>
          {rest.length ? rest.map((s) => <HmRow key={s.id} s={s} />) : <p className="at-note">No Should or Could item has a check.</p>}
          <div className="at-legend at-mt10">
            {PROOF.map((k) => (
              <LegendItem key={k} cls={'at-p-' + k} text={PROOF_LABEL[k].split(' — ')[0]} />
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
/* ---------- label vs truth ---------- */
export function Truth({ M }: { M: Model }) {
  const order = (id: string): number => M.P.get(id)?.index ?? 1e6;
  const rows = M.phases.filter((p) => p.mismatch).sort((a, b) => order(a.program) - order(b.program) || a.order - b.order);
  const tone: Record<string, string> = { done: 'g', active: 'b', planned: '', blocked: 'r' };
  if (!rows.length)
    return (
      <div className="at-card">
        <p className="at-note">No phase label disagrees with its tasks ({M.phases.length} phases checked).</p>
      </div>
    );
  return (
    <div className="at-card">
      <table className="at-table">
        <caption className="at-sr">Phases whose label disagrees with their tasks</caption>
        <thead>
          <tr>
            <th scope="col">
              Phase ({rows.length} of {M.phases.length} disagree)
            </th>
            <th scope="col">Label says</th>
            <th scope="col">Progress field says</th>
            <th scope="col">Its tasks say</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const rp = p.recorded_progress == null ? null : Math.max(0, Math.min(100, Math.round(p.recorded_progress)));
            return (
              <tr key={p.id}>
                <td>
                  <OpenBtn
                    k="phase"
                    id={p.id}
                    cls="at-tbtn"
                    label={p.title + ': label “' + p.recorded + '”, tasks say ' + p.derived}
                  >
                    <span className="at-nm2">{p.title}</span>
                    <span className="at-bang-i" aria-hidden="true">
                      !
                    </span>
                  </OpenBtn>
                </td>
                <td data-label="Label says">
                  <Chip text={p.recorded} tone={tone[p.recorded] ?? ''} />
                </td>
                <td data-label="Progress field says">
                  <span className="at-pct">
                    <Stack
                      parts={[
                        [rp ?? 0, 'at-p-claimed', 'recorded'],
                        [100 - (rp ?? 0), '', ''],
                      ]}
                    />
                    <b>{rp == null ? '—' : rp + '%'}</b>
                  </span>
                </td>
                <td data-label="Its tasks say">
                  <span className="at-pct">
                    <Stack parts={workParts(p.tasks)} />
                    <b>{p.derived_pct == null ? '—' : Math.round(p.derived_pct) + '%'}</b>
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="at-note at-mt10">
        The label is typed by hand; the tasks are updated by every <code>start</code>/<code>done</code>. <b>Recommendation:</b> never show
        phase state as a headline. Derive it from tasks (as the rail and graph here do) and show the label only to flag disagreement.
      </p>
    </div>
  );
}

/* ---------- what is waiting, and for how long ---------- */
interface WaitItem { k: string; t: string; id: string; age: number | null; c: string; task: boolean }
export function Waiting({ M }: { M: Model }) {
  const age = (d: string): number | null => daysBetween(d, M.asOfDay);
  const items: WaitItem[] = [];
  for (const d of M.loops.proposed) items.push({ k: 'decision', t: d.title || d.id, id: d.id, age: age(d.created), c: 'i', task: false });
  for (const r of M.loops.risks.filter((x) => x.status === 'open'))
    items.push({ k: 'risk', t: r.title || r.id, id: r.id, age: age(r.created), c: 'a', task: false });
  for (const q of M.loops.questions.filter((x) => x.status === 'open'))
    items.push({ k: 'question', t: q.title || q.id, id: q.id, age: age(q.created), c: 'v', task: false });
  for (const e of M.loops.escalate.filter((x) => x.kind === 'blocked_task')) {
    const t = M.TASK.get(e.id);
    items.push({ k: 'blocked', t: e.title, id: e.id, task: !!t, age: t ? age(t.created) : null, c: 'r' });
  }
  items.sort((a, b) => (b.age ?? -1) - (a.age ?? -1));
  const max = Math.max(1, ...items.map((i) => i.age ?? 0));
  return (
    <div className="at-card">
      <h3>
        Open loops, oldest first{' '}
        <span className="at-sub">
          {items.length} · days waiting
          {M.asOfDay ? ' (as of ' + fmtDay(M.asOfDay) + ')' : ''}
        </span>
      </h3>
      {items.length ? (
        items.map((i) => (
          <div className="at-wait-row" key={i.k + i.id}>
            <Chip text={i.k} tone={i.c} />
            {i.task ? (
              <OpenBtn k="task" id={i.id} cls="at-link at-wait-t" label={i.t} tipText={i.t}>
                {i.t}
              </OpenBtn>
            ) : (
              <span className="at-wait-t" data-atip={i.t}>
                <DocLink id={i.id} label={i.t} />
              </span>
            )}
            <span className="at-pct">
              {i.age == null ? (
                <>
                  <span className="at-stack at-empty" role="img" aria-label="age unknown" />
                  <b>?</b>
                </>
              ) : (
                <>
                  <span className="at-stack at-age" role="img" aria-label={i.age + ' days waiting'}>
                    <i className={'at-age-' + i.c} style={{ flex: i.age }} />
                    <i style={{ flex: max - i.age + 0.01 }} />
                  </span>
                  <b>{i.age}d</b>
                </>
              )}
            </span>
          </div>
        ))
      ) : (
        <p className="at-note">Nothing is waiting on a person: no proposed decision, open risk, open question or blocked task.</p>
      )}
    </div>
  );
}

/* ---------- the altitude ---------- */
export function Consequences({ M }: { M: Model }) {
  const [showFinished, setShowFinished] = useState(false);
  return (
    <>
      <section className="at-section at-first" id="atlas-k-graph" data-q="6">
        <div className="at-sec-h">
          <span className="at-qtag">Q6</span>
          <h2>What unlocks what — and what a slip would cost</h2>
          <p>Source-backed links only. Unknown stays unknown.</p>
        </div>
        <div className="at-card">
          <GraphView M={M} showFinished={showFinished} onToggleFinished={() => setShowFinished((v) => !v)} />
        </div>
      </section>
      <section className="at-section" id="atlas-k-decay" data-q="6">
        <div className="at-sec-h">
          <span className="at-qtag">Q6</span>
          <h2>How proof decays when code changes</h2>
          <p>The cause-and-effect chain behind the proven score.</p>
        </div>
        <Decay M={M} />
      </section>
      <section className="at-section" id="atlas-k-truth" data-q="6">
        <div className="at-sec-h">
          <span className="at-qtag">Q6</span>
          <h2>Why phase labels cannot be the headline</h2>
          <p>Hand-typed state vs the tasks that actually moved.</p>
        </div>
        <Truth M={M} />
      </section>
      <section className="at-section" id="atlas-k-wait" data-q="6">
        <div className="at-sec-h">
          <span className="at-qtag">Q6</span>
          <h2>What is waiting on a human, and for how long</h2>
        </div>
        <Waiting M={M} />
      </section>
    </>
  );
}