/* The Pack screen: what the compiled model says the project is.
   It is the one place a reader can see presets, workflows, quality rules,
   digests and the approval state without opening pack.yaml. */
import { type ReactNode } from 'react';
import { object, rows, text, type PageProps } from '../contracts';
import { PageHeading, Panel } from '../ui';
import { labelOf, operatingOf, titleCase } from './operating';

const list = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];

function Row({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="row" style={{ gap: 10, padding: '6px 0', borderBottom: '1px solid var(--divider)' }}>
      <span className="faint" style={{ minWidth: 130 }}>{name}</span>
      <span className="grow">{children}</span>
    </div>
  );
}

export function PackPage({ snapshot }: PageProps) {
  const operating = operatingOf(snapshot);
  const digests = object(snapshot.digests);
  const approval = text(object(snapshot.project).state, 'missing');
  const unit = object(operating.unit);
  const queue = object(operating.queue);
  const progress = object(operating.progress);

  return (
    <div>
      <PageHeading
        title="Pack"
        description="The compiled operating model: presets, workflows, quality, digests and approval. Read from the engine, never from pack.yaml."
      />
      <Panel title="Approval and digests">
        <Row name="Approval">{approval}</Row>
        <Row name="Semantic">{text(digests.semantic, 'not reported')}</Row>
        <Row name="Presentation">{text(digests.presentation, 'not reported')}</Row>
        <Row name="Full">{text(digests.full, 'not reported')}</Row>
      </Panel>
      <Panel title="Operating model">
        <Row name="Unit">
          {unit.preset
            ? `${labelOf(snapshot, text(unit.preset))} (${text(unit.workflow, 'default')})`
            : 'neutral: no unit'}
        </Row>
        <Row name="Hierarchy">
          {rows(operating.hierarchy).map((level) => text(level.label, titleCase(text(level.preset)))).join(' / ') || 'none'}
        </Row>
        <Row name="Focus">
          {text(object(operating.focus).state, 'none')}
          {object(operating.focus).focus ? ` · ${text(object(object(operating.focus).focus).title, text(object(object(operating.focus).focus).slug))}` : ''}
        </Row>
        <Row name="Queue">
          {list(queue.presets).map((preset) => labelOf(snapshot, preset, true)).join(', ') || 'none'}
        </Row>
        <Row name="Progress">
          {`${text(progress.complete, '0')}/${text(progress.total, '0')}`}
          {rows(progress.children).length ? ` in ${String(rows(progress.children).length)} group(s)` : ''}
        </Row>
      </Panel>
      <Panel title="Presets">
        <div className="stack">
          {rows(snapshot.presets).map((preset) => (
            <Row key={text(preset.name)} name={labelOf(snapshot, text(preset.name))}>
              {`${text(preset.primitive)} · ${text(preset.workflow)}${preset.container === true ? ' · container' : ''}`}
            </Row>
          ))}
        </div>
      </Panel>
      <Panel title="Workflows and quality">
        <div className="stack">
          {rows(snapshot.workflows).map((workflow) => {
            const quality = Object.entries(object(workflow.quality));
            return (
              <Row key={text(workflow.name)} name={text(workflow.name)}>
                {`${list(workflow.states).join(' → ') || 'no states'}`}
                {quality.length
                  ? ` · gates ${quality.map(([state, rule]) => `${text(object(rule).gate)} on ${state}`).join(', ')}`
                  : ''}
              </Row>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}
