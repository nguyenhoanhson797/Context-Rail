/* The Hierarchy Explorer: one tree per pack hierarchy level.
   A container preset (scene, sprint, phase, epic…) groups the work items
   that name it through part_of / phase. A record without a container link is
   listed under "outside the chain" with its reason, never guessed into it.
   It renders snapshot.phases + snapshot.tasks only — the same snapshot the
   board uses — grouped by the container each task actually references. */
import { useMemo, type CSSProperties } from 'react';
import { object, text, type Data, type PageProps } from '../contracts';
import { Badge, Empty, PageHeading, Panel } from '../ui';
import { hierarchyLevels, labelOf, screenTitle, unitLabel } from './operating';

const idOf = (doc: Data): string => text(doc.id);
const titleOf = (doc: Data): string => text(doc.title, text(doc.id));

function phaseOf(task: Data): string {
  const direct = text(task.phase);
  if (direct) return direct;
  const fields = object(task.fields);
  const partOf = task.part_of ?? fields.part_of;
  if (Array.isArray(partOf)) return text(partOf[0]);
  return text(partOf);
}

export function ExplorerPage({ snapshot, openDoc }: PageProps) {
  const title = screenTitle(snapshot, 'explorer', 'Hierarchy');
  const levels = hierarchyLevels(snapshot);
  const levelPreset = levels[0] ? text(levels[0].preset) : '';
  const singular = levelPreset ? labelOf(snapshot, levelPreset).toLowerCase() : 'container';
  const pluralWord = levelPreset ? labelOf(snapshot, levelPreset, true).toLowerCase() : 'containers';
  const unit = unitLabel(snapshot);
  const unitMany = unitLabel(snapshot, true).toLowerCase();

  const grouped = useMemo(() => {
    const containers = snapshot.phases;
    const byContainer = new Map<string, Data[]>();
    const orphans: Data[] = [];
    for (const task of snapshot.tasks) {
      const parent = phaseOf(task);
      if (parent && containers.some((c) => idOf(c) === parent)) {
        byContainer.set(parent, [...(byContainer.get(parent) ?? []), task]);
      } else {
        orphans.push(task);
      }
    }
    return { containers, byContainer, orphans };
  }, [snapshot.phases, snapshot.tasks]);

  const totalChildren = grouped.byContainer.size
    ? [...grouped.byContainer.values()].reduce((sum, list) => sum + list.length, 0)
    : 0;

  return (
    <div>
      <PageHeading
        title={title}
        description={
          levels.length
            ? `${levels.map((level) => labelOf(snapshot, text(level.preset), true)).join(' → ')} → ${unit}. A ${unit.toLowerCase()} with no ${singular} link sits outside the chain and says so.`
            : `No hierarchy declared. ${unit} items are listed flat.`
        }
      />

      {grouped.containers.length ? (
        <div className="stack">
          {grouped.containers.map((container) => {
            const kids = grouped.byContainer.get(idOf(container)) ?? [];
            const done = kids.filter((kid) => text(kid.category) === 'done').length;
            const share = kids.length ? Math.round((done / kids.length) * 100) : 0;
            return (
              <Panel key={idOf(container)} title={titleOf(container)}>
                <div className="row" style={{ gap: 10, marginBottom: 8 }}>
                  <span className="prog" role="progressbar" aria-valuenow={share} aria-valuemin={0} aria-valuemax={100} style={{ flex: 1 }}>
                    <i style={{ '--p': share / 100 } as CSSProperties} />
                  </span>
                  <span className="faint" style={{ fontSize: 12 }}>
                    {done}/{kids.length} {unitMany} done
                  </span>
                  <Badge tone={text(container.state) === 'done' ? 'good' : text(container.state) === 'active' ? 'accent' : 'neutral'}>
                    {text(container.state, 'planned')}
                  </Badge>
                </div>
                {kids.length ? (
                  kids.map((kid) => (
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
                      <span className="faint" style={{ fontSize: 12 }}>{text(kid.preset)}</span>
                    </div>
                  ))
                ) : (
                  <div className="faint">No {unitMany} name this {singular}.</div>
                )}
              </Panel>
            );
          })}
        </div>
      ) : (
        <Empty title={`No ${pluralWord} yet`}>
          {`Add one with rail change add <${singular}> "...", then a ${unit.toLowerCase()} with --part-of.`}
        </Empty>
      )}

      {grouped.orphans.length ? (
        <Panel title={`Outside the chain · ${grouped.orphans.length}`}>
          <p className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
            These {unitMany} do not name a {singular}. They are shown here, never guessed into one.
          </p>
          {grouped.orphans.map((orphan) => (
            <div
              className="mini"
              key={idOf(orphan)}
              role="button"
              tabIndex={0}
              onClick={() => openDoc(idOf(orphan))}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') openDoc(idOf(orphan));
              }}
            >
              <span className="grow trunc" title={titleOf(orphan)}>{titleOf(orphan)}</span>
              <span className="faint" style={{ fontSize: 12 }}>{text(orphan.status)}</span>
              <span className="faint" style={{ fontSize: 12 }}>
                {phaseOf(orphan) ? `names ${phaseOf(orphan)}` : 'no container link'}
              </span>
            </div>
          ))}
        </Panel>
      ) : null}

      {grouped.containers.length && totalChildren === 0 && grouped.orphans.length === 0 ? (
        <div className="faint" style={{ marginTop: 8 }}>No {unitMany} yet.</div>
      ) : null}
    </div>
  );
}


