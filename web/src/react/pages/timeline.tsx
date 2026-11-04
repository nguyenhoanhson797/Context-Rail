/* The Timeline screen: hierarchy containers in sequence order.
   For packs whose hierarchy level declares `order: sequence` (scene,
   phase), this renders the containers left-to-right with focus and
   completion — the plan as a sequence, not a list. When there is no
   sequence, it falls back to the list form in RoadmapPage. */
import { useMemo } from 'react';
import { object, text, type Data, type PageProps } from '../contracts';
import { Badge, Empty, PageHeading, Panel } from '../ui';
import { focusName, hierarchyLevels, labelOf, screenTitle, unitLabel } from './operating';

const idOf = (doc: Data): string => text(doc.id);
const titleOf = (doc: Data): string => text(doc.title, text(doc.id));

export function TimelinePage({ snapshot, openDoc }: PageProps) {
  const title = screenTitle(snapshot, 'timeline', screenTitle(snapshot, 'plan', 'Plan'));
  const levels = hierarchyLevels(snapshot);
  const levelPreset = levels[0] ? text(levels[0].preset) : '';
  const singular = levelPreset ? labelOf(snapshot, levelPreset).toLowerCase() : 'container';
  const pluralWord = levelPreset ? labelOf(snapshot, levelPreset, true).toLowerCase() : 'containers';
  const unitMany = unitLabel(snapshot, true).toLowerCase();
  const focus = focusName(snapshot);

  const ordered = useMemo(() => {
    const tasks = snapshot.tasks;
    return snapshot.phases.map((phase) => {
      const kids = tasks.filter((task) => {
        const taskPhase = text(task.phase) || text(object(task.fields).phase);
        return taskPhase === idOf(phase);
      });
      const done = kids.filter((kid) => text(kid.category) === 'done').length;
      return { phase, kids, done };
    });
  }, [snapshot.phases, snapshot.tasks]);

  const totalDone = ordered.reduce((sum, entry) => sum + entry.done, 0);
  const totalKids = ordered.reduce((sum, entry) => sum + entry.kids.length, 0);

  return (
    <div>
      <PageHeading
        title={title}
        description={`${pluralWord} in sequence. ${focus ? `Focus: ${focus}.` : ''} ${totalKids ? `${totalDone}/${totalKids} ${unitMany} done.` : ''}`}
      />

      {ordered.length ? (
        <div style={{ overflowX: 'auto', paddingBottom: 8 }}>
          <div style={{ display: 'flex', gap: 12, minWidth: 'min-content' }}>
            {ordered.map(({ phase, kids, done }, index) => {
              const state = text(phase.state, 'planned');
              const isFocus = focus && titleOf(phase) === focus;
              return (
                <div key={idOf(phase)} style={{ minWidth: 220, maxWidth: 260, flex: '0 0 auto' }}>
                  <Panel title={titleOf(phase)}>
                    <div className="row" style={{ gap: 6, marginBottom: 8, alignItems: 'center' }}>
                      <span className="faint" style={{ fontSize: 12 }}>#{index + 1}</span>
                      <Badge tone={state === 'done' ? 'good' : state === 'active' ? 'accent' : 'neutral'}>
                        {state}
                      </Badge>
                      {isFocus ? <Badge tone="accent">focus</Badge> : null}
                    </div>
                    <button
                      type="button"
                      className="link rail-link"
                      onClick={() => openDoc(idOf(phase))}
                      style={{ fontSize: 12, marginBottom: 8 }}
                    >
                      Open the {singular}
                    </button>
                    <div className="faint" style={{ fontSize: 12, marginBottom: 4 }}>
                      {done}/{kids.length} {unitMany} done
                    </div>
                    {kids.length ? (
                      kids.slice(0, 6).map((kid) => (
                        <div
                          className="mini"
                          key={idOf(kid)}
                          role="button"
                          tabIndex={0}
                          onClick={() => openDoc(idOf(kid))}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') openDoc(idOf(kid));
                          }}
                        >
                          <span className="grow trunc" title={titleOf(kid)}>{titleOf(kid)}</span>
                          <span className="faint" style={{ fontSize: 12 }}>{text(kid.status)}</span>
                        </div>
                      ))
                    ) : (
                      <div className="faint">No {unitMany} yet.</div>
                    )}
                    {kids.length > 6 ? (
                      <div className="faint" style={{ fontSize: 12 }}>+{kids.length - 6} more</div>
                    ) : null}
                  </Panel>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <Empty title={`No ${pluralWord} yet`}>
          {`Add one with rail change add <${singular}> "..." `}
        </Empty>
      )}
    </div>
  );
}
