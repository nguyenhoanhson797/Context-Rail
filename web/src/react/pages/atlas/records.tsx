/* =====================================================================
   ATLAS ALTITUDE 4 - RECORDS (React): the existing pages, unchanged, as doors.
   Each door is the page's own route; nothing here reads or writes a record.
   ===================================================================== */
import type { Model } from './model';
import { useAtlas } from './ui';

const n = (v: number | null | undefined): string => (v == null ? '—' : String(v));

type Door = [string, number | null, string, string];

/* The door list. The route is one the shell already has; the count is the model's own, or an em dash when the read has none. */
export function doors(M: Model): [string, Door[]][] {
  const c = M.counts;
  const d = M.doctor;
  const proposed = M.loops.proposed.length;
  return [
    [
      'Understand',
      [
        ['overview', M.goals.length, 'Overview', 'The project top down as text: vision, goals, what is proven, what is open.'],
        ['outcomes', M.goals.length, 'Outcomes', 'Sub-goal → scope → acceptance → proof, one drill-down per goal.'],
        ['traceability', c.scope, 'Traceability', 'Sub-goals × scope × acceptance × tasks × checks, with the gaps.'],
        ['roadmap', c.phases, 'Roadmap', 'Every phase in order, with its plan and milestones.'],
        ['scope', c.scope, 'Scope', 'Must / Should / Could / Won’t with each item’s checklist.'],
      ],
    ],
    [
      'Work',
      [
        ['now', M.inflight.length, 'Now', 'What is in flight and who holds it.'],
        ['board', c.tasks, 'Board', 'All tasks by status; moves go through the tools.'],
        ['lens', null, 'Lens', 'One task’s context in rings: why, what, how, plan, reach, proof, open.'],
      ],
    ],
    [
      'Changes',
      [
        ['activity', c.events, 'Catch up', 'What changed since you last looked, by actor.'],
        ['impact', null, 'Impact', 'What a change to given files reaches: scope, checks, decisions.'],
        ['decisions', c.decisions, 'Decisions', proposed + ' proposed · ' + M.decisionStates.active + ' active · ' + M.decisionStates.closed + ' closed.'],
      ],
    ],
    [
      'Inspect',
      [
        ['graph', null, 'Graph', 'Every authored relation between records.'],
        [
          'doctor',
          d.total,
          'Doctor',
          d.total == null ? 'The store’s health findings.' : d.error + ' errors · ' + d.warning + ' warnings · ' + d.info + ' info.',
        ],
      ],
    ],
  ];
}

export function Records({ M }: { M: Model }) {
  const { page } = useAtlas();
  return (
    <section className="at-section at-first" id="atlas-r-doors">
      <div className="at-sec-h">
        <span className="at-qtag">Altitude 4</span>
        <h2>Records — the existing pages, unchanged</h2>
        <p>The Atlas sits above them. Every shape on Cover, Map and Consequences already links down to one of these.</p>
      </div>
      {doors(M).map(([grp, ds]) => (
        <div key={grp}>
          <h3 className="at-mini-h at-grp">{grp}</h3>
          <div className="at-grid at-doors">
            {ds.map(([r, count, label, desc]) => (
              <button
                type="button"
                className="at-card at-door"
                key={r}
                aria-label={'Open ' + label + ': ' + desc}
                onClick={() => page(r)}
              >
                <span className="at-dn">{n(count)}</span>
                <span className="at-dl">{label}</span>
                <span className="at-dd">{desc}</span>
                <span className="at-dr">#{r}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      <div className="at-howto">
        <b>Why not lead with these?</b>{' '}
        <span className="at-note">
          They answer “show me the records of kind X”. A returning owner’s question is “what is going on and what should I
          do?” — which spans several kinds at once. The Atlas answers that first; the records are for when you already know what to
          look at.
        </span>
      </div>
    </section>
  );
}