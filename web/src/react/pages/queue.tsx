/* Next up is the engine's queue, not a re-sort of the board. The order, the
   eligibility reason and the offered actions all come from `resolve_queue`; this
   screen may filter and re-order the view, and says so when it does, but it
   never invents eligibility and never runs an action the pack did not declare. */
import { useMemo, useState } from 'react';
import { ArrowRight, CircleDot, RotateCcw, Search, Target, X } from '../icons';
import { rows, text, type PageProps } from '../contracts';
import { cn } from '../ui';
import { CATEGORY_LABEL, categoryOf, itemById } from '../capabilities';
import { focusName, labelOf, queueOf, queuePolicyOf, titleCase } from './operating';

type Sort = 'engine' | 'title' | 'status' | 'kind';

const WHY_LABEL: Record<string, string> = {
  focus: 'in focus',
  scope: 'project scope',
  project: 'project scope',
  container: 'its container',
  action: 'declared action',
};

export function QueuePage({ snapshot, params, navigate, openDoc, busy, refresh }: PageProps) {
  const queue = queueOf(snapshot);
  const policy = queuePolicyOf(snapshot);
  const items = useMemo(() => rows(queue.items), [queue]);
  const byId = useMemo(() => itemById(snapshot), [snapshot]);
  const focus = focusName(snapshot);
  const error = text(queue.error);
  const omitted = Number(queue.omitted) || 0;

  const query = params.get('q') ?? '';
  const kind = params.get('kind') ?? 'all';
  const [workflow, setWorkflow] = useState('all');
  const [onlyFocus, setOnlyFocus] = useState(false);
  const [sort, setSort] = useState<Sort>('engine');

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      const name = text(item.preset);
      if (name) counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [items]);

  const workflows = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      const name = text(item.workflow);
      if (name) counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [items]);

  const matched = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = items.filter((item) => {
      if (kind !== 'all' && text(item.preset) !== kind) return false;
      if (workflow !== 'all' && text(item.workflow) !== workflow) return false;
      if (onlyFocus && item.focus !== true) return false;
      if (!needle) return true;
      return [text(item.title), text(item.slug), text(item.preset), text(item.status), text(item.why)]
        .join(' ')
        .toLowerCase()
        .includes(needle);
    });
    if (sort === 'engine') return filtered;
    const keyed = [...filtered];
    keyed.sort((a, b) => {
      if (sort === 'title') return text(a.title).localeCompare(text(b.title));
      if (sort === 'status') return text(a.status).localeCompare(text(b.status));
      return text(a.preset).localeCompare(text(b.preset)) || text(a.title).localeCompare(text(b.title));
    });
    return keyed;
  }, [items, query, kind, workflow, onlyFocus, sort]);

  const focusCount = items.filter((item) => item.focus === true).length;
  const filtersOn = kind !== 'all' || workflow !== 'all' || onlyFocus || query.trim() !== '';

  return (
    <div className="rq-page">
      <header className="rq-head">
        <div>
          <div className="rf-kicker">{`${items.length} eligible · ${text(queue.scope, 'project')} scope · ${text(policy.require) === 'action' ? 'declared actions' : 'workflow transitions'}`}</div>
          <h1>Next up</h1>
          <p>The engine ranks this list. Focus first, then the declared order; every row says why it is eligible.</p>
        </div>
        <div className="rq-head-side">
          {focus ? (
            <span className="rq-focus"><Target size={12} aria-hidden="true" /> <b className="trunc">{focus}</b></span>
          ) : (
            <span className="rq-focus none">no single focus resolved</span>
          )}
          <button type="button" className="rf-chip" disabled={busy || !refresh} onClick={() => void refresh?.()}>
            <RotateCcw size={13} aria-hidden="true" /> {busy ? 'Reading…' : 'Refresh'}
          </button>
        </div>
      </header>

      {error ? (
        <div className="rq-refused" role="alert">
          <b>The engine refused to resolve this queue</b>
          <span>{error}</span>
          <p>Nothing is guessed when focus is ambiguous. Resolve the focus, or scope the read explicitly.</p>
        </div>
      ) : null}

      <div className="rq-stats">
        <div className="rq-stat"><span>Eligible now</span><b>{items.length}</b></div>
        <div className="rq-stat"><span>In focus</span><b>{focusCount}</b></div>
        <div className="rq-stat"><span>Kinds</span><b>{kinds.length}</b></div>
        <div className="rq-stat"><span>Workflows</span><b>{workflows.length}</b></div>
      </div>

      <div className="rq-toolbar">
        <label className="rm-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            placeholder="Search the queue…"
            aria-label="Search the queue"
            onChange={(event) => setParam('q', event.target.value)}
          />
          {query ? (
            <button type="button" className="rm-clear" aria-label="Clear search" onClick={() => setParam('q', '')}>
              <X size={12} aria-hidden="true" />
            </button>
          ) : null}
        </label>
        <label className="ra-select">
          <span>Workflow</span>
          <select value={workflow} aria-label="Filter by workflow" onChange={(event) => setWorkflow(event.target.value)}>
            <option value="all">All</option>
            {workflows.map(([name, count]) => (
              <option key={name} value={name}>{`${name} (${count})`}</option>
            ))}
          </select>
        </label>
        <label className="ra-select">
          <span>Order</span>
          <select value={sort} aria-label="Sort the queue" onChange={(event) => setSort(event.target.value as Sort)}>
            <option value="engine">Engine order</option>
            <option value="title">Title</option>
            <option value="status">Status</option>
            <option value="kind">Kind</option>
          </select>
        </label>
        <button type="button" className={cn('rf-chip', onlyFocus && 'rf-chip-focus')} aria-pressed={onlyFocus} onClick={() => setOnlyFocus((value) => !value)}>
          <Target size={12} aria-hidden="true" /> Focus only
        </button>
        {filtersOn ? (
          <button type="button" className="rf-chip" onClick={() => clearFilters()}>
            <X size={12} aria-hidden="true" /> Clear
          </button>
        ) : null}
        <span className="ra-spacer" />
        <span className="ra-count">{`${matched.length} of ${items.length}`}</span>
      </div>

      <div className="rm-presets" role="group" aria-label="Filter by kind">
        <button type="button" className={cn('ra-kindchip', kind === 'all' && 'on')} aria-pressed={kind === 'all'} onClick={() => pickKind('all')}>
          All kinds <b>{items.length}</b>
        </button>
        {kinds.map(([name, count]) => (
          <button type="button" key={name} className={cn('ra-kindchip', kind === name && 'on')} aria-pressed={kind === name} onClick={() => pickKind(name)}>
            {labelOf(snapshot, name, true)} <b>{count}</b>
          </button>
        ))}
      </div>

      {sort !== 'engine' ? (
        <p className="rq-reorder">
          <CircleDot size={12} aria-hidden="true" /> Showing a {sort} ordering of the same eligible set. The engine's own rank is the default.
        </p>
      ) : null}

      <main className="rq-body">
        {!items.length ? (
          <div className="rf-emptystate">
            <b>{error ? 'The queue is refused' : 'Nothing is eligible right now'}</b>
            <span>{error ? 'Resolve the refusal above and the queue will fill.' : 'Every item is either done, blocked by a gate, or outside the resolved focus.'}</span>
            <button type="button" className="rf-chip" onClick={() => navigate('board')}>Open the board</button>
          </div>
        ) : !matched.length ? (
          <div className="rf-emptystate">
            <b>No eligible item matches</b>
            <span>{`${items.length} items are eligible. Widen the filter to see them.`}</span>
            <button type="button" className="rf-chip" onClick={() => clearFilters()}>Clear filters</button>
          </div>
        ) : (
          <div className="rq-table-wrap">
            <table className="rq-table">
              <thead>
                <tr>
                  <th className="rq-th-rank">#</th>
                  <th>Work</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>Why</th>
                  <th>Offered actions</th>
                </tr>
              </thead>
              <tbody>
                {matched.map((item, index) => {
                  const slug = text(item.slug);
                  const status = text(item.status);
                  const category = categoryOf(snapshot, status);
                  const actions = Array.isArray(item.actions) ? item.actions.filter((a): a is string => typeof a === 'string') : [];
                  const doc = byId.get(slug);
                  const parent = text(doc?.phase);
                  return (
                    <tr key={slug} className={cn(item.focus === true && 'focused')}>
                      <td className="rq-rank">{sort === 'engine' ? index + 1 : '·'}</td>
                      <td>
                        <button type="button" className="rq-work" onClick={() => openDoc(slug)}>
                          <b className="trunc">{text(item.title, slug)}</b>
                          <ArrowRight size={11} aria-hidden="true" />
                        </button>
                        <span className="rq-sub">
                          {parent ? text(byId.get(parent)?.title, parent) : 'no container'}
                        </span>
                      </td>
                      <td><span className="rm-preset">{labelOf(snapshot, text(item.preset))}</span></td>
                      <td>
                        <span className={cn('rm-status', `cat-${category}`)}>{status || '—'}</span>
                        <span className="rq-cat">{text(CATEGORY_LABEL[category], titleCase(category))}</span>
                      </td>
                      <td>
                        <span className={cn('rq-why', item.focus === true && 'focus')}>
                          {text(WHY_LABEL[text(item.why)], titleCase(text(item.why, 'eligible')))}
                        </span>
                      </td>
                      <td>
                        <span className="rq-actions">
                          {actions.length ? actions.map((action) => (
                            <span className="rq-action" key={action}>{action}</span>
                          )) : <span className="faint">transition only</span>}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {omitted ? (
          <p className="rq-omitted">{`${omitted} further eligible ${omitted === 1 ? 'item is' : 'items are'} beyond the snapshot cap. Narrow the focus to bring them forward.`}</p>
        ) : null}
      </main>
      <p className="rf-note">Actions are the pack's declared verbs. This screen lists them; running one goes through the gateway as you.</p>
    </div>
  );

  function setParam(name: string, next: string) {
    const values: Record<string, string> = {};
    for (const [key, value] of params) values[key] = value;
    if (!next) delete values[name];
    else values[name] = next;
    navigate('now', values);
  }

  function pickKind(next: string) {
    setParam('kind', next === 'all' ? '' : next);
  }

  function clearFilters() {
    setWorkflow('all');
    setOnlyFocus(false);
    const values: Record<string, string> = {};
    for (const [key, value] of params) {
      if (key !== 'kind' && key !== 'q') values[key] = value;
    }
    navigate('now', values);
  }
}
