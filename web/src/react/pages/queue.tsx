/* The Queue screen: the operating queue, full and explained.
   It renders snapshot.operating.queue — the same items `next` returns —
   with why each is here, its status, preset, workflow and focus flag, plus
   the queue policy (which presets, what makes one eligible). */
import { useQuery } from '@tanstack/react-query';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { readKey } from '../refresh';
import { Badge, Empty, PageHeading, Panel } from '../ui';
import { labelOf, screenTitle, unitLabel } from './operating';

const titleOf = (doc: Data): string => text(doc.title, text(doc.slug, text(doc.id)));

export function QueuePage({ snapshot, project, read, openDoc, busy }: PageProps) {
  const title = screenTitle(snapshot, 'queue', 'Queue');
  const next = useQuery({
    queryKey: readKey(project, 'next'),
    queryFn: ({ signal }) => read('next', {}, signal),
    retry: false,
    enabled: !busy,
  });
  const operating = object(snapshot.operating);
  const queue = object(operating.queue);
  const policy = object(operating.queue_policy);
  const items = rows(queue.items);
  const unit = unitLabel(snapshot);

  /* When the snapshot queue is empty (neutral pack, no operating block),
     fall back to the tasks that are not done — the same shape so the screen
     never goes blank. */
  const fallback = items.length ? items : snapshot.tasks.filter(
    (task) => text(task.category) !== 'done' && text(task.category) !== 'dropped',
  );

  return (
    <div>
      <PageHeading
        title={title}
        description="The executable queue, in the order `next` ranks it. The queue policy below says which presets are eligible and what makes one eligible."
      />

      <Panel title="Queue policy">
        <div className="row" style={{ gap: 10, padding: '6px 0', borderBottom: '1px solid var(--divider)' }}>
          <span className="faint" style={{ minWidth: 130 }}>Presets</span>
          <span className="grow">
            {(Array.isArray(policy.presets) ? policy.presets : [text(policy.preset)])
              .map((preset) => labelOf(snapshot, text(preset), true))
              .filter(Boolean)
              .join(', ') || 'all work presets'}
          </span>
        </div>
        <div className="row" style={{ gap: 10, padding: '6px 0', borderBottom: '1px solid var(--divider)' }}>
          <span className="faint" style={{ minWidth: 130 }}>Eligible when</span>
          <span className="grow">{text(policy.require, 'transition')}</span>
        </div>
        <div className="row" style={{ gap: 10, padding: '6px 0' }}>
          <span className="faint" style={{ minWidth: 130 }}>Focus</span>
          <span className="grow">{text(policy.focus, 'priority')}</span>
        </div>
      </Panel>

      {next.isError ? (
        <Panel title="Queue unavailable">
          <div className="faint" role="alert">{next.error.message}</div>
        </Panel>
      ) : next.isPending ? (
        <div className="faint">Reading the queue…</div>
      ) : fallback.length ? (
        <Panel title={`${fallback.length} ${unit.toLowerCase()}${fallback.length === 1 ? '' : 's'} in queue`}>
          {fallback.map((item) => {
            const slug = text(item.slug) || text(item.id);
            const why = text(item.why) || text(item.reason);
            return (
              <div
                className="mini"
                key={slug || titleOf(item)}
                role="button"
                tabIndex={0}
                onClick={() => slug && openDoc(slug)}
                onKeyDown={(event) => {
                  if ((event.key === 'Enter' || event.key === ' ') && slug) openDoc(slug);
                }}
              >
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="trunc" style={{ fontWeight: 500 }} title={titleOf(item)}>{titleOf(item)}</div>
                  <div className="faint trunc" style={{ fontSize: 12 }} title={why}>
                    {why || `${labelOf(snapshot, text(item.preset))} · ${text(item.workflow) || text(item.status)}`}
                  </div>
                  <div className="faint" style={{ fontSize: 12 }}>
                    {text(item.preset) && labelOf(snapshot, text(item.preset))} · {text(item.status)}
                    {item.focus === true ? ' · in focus' : ''}
                  </div>
                </div>
                {text(item.status) ? <Badge tone="neutral">{text(item.status)}</Badge> : null}
                {Array.isArray(item.actions) && item.actions.length ? (
                  <span className="faint" style={{ fontSize: 12 }}>{item.actions.join(', ')}</span>
                ) : null}
              </div>
            );
          })}
        </Panel>
      ) : (
        <Empty title="The queue is empty">Nothing is executable right now. Start a {unit.toLowerCase()} or unblock one.</Empty>
      )}
    </div>
  );
}
