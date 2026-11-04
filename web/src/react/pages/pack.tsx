/* The Pack screen: what the compiled model says the project is.
   It is the one place a reader can see presets, workflows, quality rules,
   digests and the approval state without opening pack.yaml. The second half
   is the Pack Studio: a live preview of what surface.yaml renders — screens,
   fields, labels, hierarchy and queue — compiled from the same model. */
import { type ReactNode } from 'react';
import { object, rows, text, type PageProps } from '../contracts';
import { PageHeading, Panel } from '../ui';
import { cardFieldsOf, filterFieldsOf, labelOf, operatingOf, screensOf, titleCase, unitLabel } from './operating';

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

export function PackPage({ snapshot, navigate }: PageProps) {
  const operating = operatingOf(snapshot);
  const digests = object(snapshot.digests);
  const approval = text(object(snapshot.project).state, 'missing');
  const unit = object(operating.unit);
  const queue = object(operating.queue_policy);
  const progress = object(operating.progress);
  const runs = object(progress.runs);
  const screens = screensOf(snapshot);
  const cardFields = cardFieldsOf(snapshot);
  const filterFields = filterFieldsOf(snapshot);

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
        {Object.keys(runs).length ? (
          <Row name="Execution">
            {`${text(object(runs.by_state).succeeded, '0')} succeeded, `}
            {`${text(object(runs.by_state).failed, '0')} failed, `}
            {`${text(object(runs.by_state).running, '0')} running`}
          </Row>
        ) : null}
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

      <Panel title="Pack Studio · screens">
        <p className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
          What <code>surface.yaml</code> renders for this pack, compiled from the same model.
          Open a screen to see it live.
        </p>
        <div className="stack">
          {screens.map((screen) => (
            <div className="mini" key={text(screen.id)}>
              <div className="grow" style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500 }}>{text(screen.title)}</div>
                <div className="faint" style={{ fontSize: 12 }}>
                  {text(screen.kind)}
                  {text(screen.preset) ? ` · ${labelOf(snapshot, text(screen.preset))}` : ''}
                  {Array.isArray(screen.presets) && screen.presets.length
                    ? ` · ${screen.presets.map((preset) => labelOf(snapshot, text(preset), true)).join(', ')}`
                    : ''}
                  {text(screen.group_by) ? ` · by ${text(screen.group_by)}` : ''}
                </div>
              </div>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => {
                  const kind = text(screen.kind);
                  const legacy: Record<string, 'summary' | 'now' | 'board' | 'roadmap' | 'scope' | 'catalog' | 'decisions' | 'doctor' | 'pack' | 'settings' | 'queue' | 'workflow' | 'execution' | 'explorer'> = {
                    plan: 'roadmap', timeline: 'roadmap', records: 'decisions', queue: 'queue',
                    board: 'board', summary: 'summary', catalog: 'catalog', pack: 'pack',
                    workflow: 'workflow', execution: 'execution', explorer: 'explorer',
                  };
                  navigate(legacy[kind] ?? 'summary');
                }}
              >
                Open
              </button>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Pack Studio · fields">
        <Row name="Card">{cardFields.length ? cardFields.join(', ') : 'title + status + id (no card fields declared)'}</Row>
        <Row name="Filters">{filterFields.length ? filterFields.join(', ') : 'status'}</Row>
        <Row name="Unit">{unitLabel(snapshot, true)}</Row>
      </Panel>
    </div>
  );
}
