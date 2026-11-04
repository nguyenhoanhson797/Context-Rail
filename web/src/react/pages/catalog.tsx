/* The Catalog: work presets that are not the unit, not priority scope and not
   records. It is built from the compiled labels, never from a preset name.
   A screen may declare `presets: [asset, render]` to pin the catalog to those. */
import { text, type Data, type PageProps } from '../contracts';
import { Empty, PageHeading, Panel } from '../ui';
import { catalogItems, labelOf, screenDef, screenTitle, unitOf } from './operating';

export function CatalogPage({ snapshot, openDoc }: PageProps) {
  const def = screenDef(snapshot, 'catalog');
  const items = catalogItems(snapshot, def?.presets);
  const unit = text(unitOf(snapshot).preset);
  const groups = new Map<string, Data[]>();
  for (const item of items) {
    const preset = text(item.preset, 'other');
    groups.set(preset, [...(groups.get(preset) ?? []), item]);
  }
  const title = screenTitle(snapshot, 'catalog', 'Catalog');

  return (
    <div>
      <PageHeading
        title={title}
        description={def?.presets.length
          ? `${def.presets.map((preset) => labelOf(snapshot, preset, true)).join(', ')}.`
          : `Other work presets beside the unit${unit ? ` (${labelOf(snapshot, unit)})` : ''}. Records and priority scope live on their own screens.`}
      />
      {items.length ? (
        [...groups.entries()].map(([preset, list]) => (
          <Panel key={preset} title={labelOf(snapshot, preset, true)}>
            {list.map((item) => (
              <div
                className="mini"
                key={text(item.id)}
                role="button"
                tabIndex={0}
                onClick={() => openDoc(text(item.id))}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') openDoc(text(item.id));
                }}
              >
                <span className="grow trunc" title={text(item.title)}>{text(item.title, text(item.id))}</span>
                <span className="faint" style={{ fontSize: 12 }}>{text(item.status)}</span>
              </div>
            ))}
          </Panel>
        ))
      ) : (
        <Empty title={`No ${title.toLowerCase()} yet`}>
          {def?.presets.length
            ? `Add one with rail change add ${def.presets[0]} "..." `
            : unit
              ? `Add one with rail change add <preset> "..."; it appears here unless it is the unit (${labelOf(snapshot, unit)}), priority scope or a record.`
              : 'Add one with rail change add <preset> "..." '}
        </Empty>
      )}
    </div>
  );
}
