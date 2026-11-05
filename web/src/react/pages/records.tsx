/* Records is the audit screen: decisions, risks, questions and evidence as a
   ledger. The "about" column follows the declared relation, never a free-text
   tag. A record explains work; it never becomes another working board. */
import { useMemo, useState } from 'react';
import { ArrowRight, BookOpen } from '../icons';
import { object, text, type Data, type PageProps } from '../contracts';
import { cn } from '../ui';
import { allItems, itemById, ledgerScreen, recordsOf, type RecordFact } from '../capabilities';
import { screenTitle } from './operating';

const recordValue = (value: unknown): string => {
  if (Array.isArray(value)) return value.map((entry) => text(entry)).filter(Boolean).join(', ');
  if (value && typeof value === 'object') return JSON.stringify(value);
  return text(value);
};

const KIND_CLASS: Record<string, string> = {
  decision: 'rr-kind-decision',
  risk: 'rr-kind-risk',
  question: 'rr-kind-question',
  review: 'rr-kind-review',
  event: 'rr-kind-event',
  record: 'rr-kind-record',
};

function RecordDetail({
  record,
  byId,
  openDoc,
}: {
  record: RecordFact;
  byId: Map<string, Data>;
  openDoc: (id: string) => void;
}) {
  const doc = record.doc;
  const relations = object(doc.relations);
  const about = Array.isArray(relations.about) ? relations.about.filter((value): value is string => typeof value === 'string') : [];
  const fields = Object.entries(object(doc.fields)).filter(
    ([key]) => !['about', 'status', 'workflow', 'title'].includes(key),
  );
  return (
    <aside className="rr-detail" aria-label={`${record.title} detail`}>
      <div className="rr-detail-head">
        <span className={cn('rr-kind', KIND_CLASS[record.kind] ?? 'rr-kind-record')}>{record.kind}</span>
        <span className="rf-chip rf-chip-muted">{record.status || 'no status'}</span>
      </div>
      <h2>{record.title}</h2>
      <div className="rr-detail-id">{record.id}</div>
      {about.length ? (
        <div className="rr-about">
          <div className="rf-k2">About</div>
          {about.map((id) => (
            <button type="button" key={id} className="rr-link" onClick={() => openDoc(id)}>
              <span className="trunc">{text(byId.get(id)?.title, id)}</span>
              <ArrowRight size={12} />
            </button>
          ))}
        </div>
      ) : null}
      {fields.length ? (
        <div className="rr-fields">
          <div className="rf-k2">Recorded</div>
          {fields.map(([key, value]) => (
            <div className="rr-field" key={key}>
              <span>{key}</span>
              <b>{recordValue(value)}</b>
            </div>
          ))}
        </div>
      ) : null}
      {Object.keys(relations).length ? (
        <div className="rr-fields">
          <div className="rf-k2">Relations</div>
          {Object.entries(relations).map(([rel, values]) => (
            <div className="rr-field" key={rel}>
              <span>{rel}</span>
              <b>
                {(Array.isArray(values) ? values : []).filter((value): value is string => typeof value === 'string').map((id) => text(byId.get(id)?.title, id)).join(', ') || '—'}
              </b>
            </div>
          ))}
        </div>
      ) : null}
      <button type="button" className="rr-open" onClick={() => openDoc(record.id)}>
        <BookOpen size={14} /> Open the document
      </button>
      <p className="rr-note">Records are append-only. The UI never edits history.</p>
    </aside>
  );
}

export function RecordsPage({ snapshot, params, navigate, openDoc }: PageProps) {
  const screen = ledgerScreen(snapshot);
  const title = screen?.title ?? screenTitle(snapshot, 'decisions', 'Records');
  const [all, setAll] = useState(false);
  const records = useMemo(() => recordsOf(snapshot, all), [snapshot, all]);
  const byId = useMemo(() => itemById(snapshot), [snapshot]);
  const units = useMemo(() => allItems(snapshot), [snapshot]);
  const requested = params.get('record') ?? '';
  const [selected, setSelected] = useState<string>(requested);
  const chosen = records.find((record) => record.id === selected) ?? records[0];
  const lensParam = params.get('lens') ?? 'ledger';
  const lens = lensParam === 'grid' ? 'grid' : 'ledger';

  return (
    <div className="rr-page">
      <header className="rr-head">
        <div>
          <div className="rf-kicker">{`${records.length} records · ${units.length} work items`}</div>
          <h1>{title}</h1>
          <p>Decisions, risks, questions and evidence, linked to the work they govern.</p>
        </div>
        <div className="rr-head-tools">
          <button type="button" className={cn('rf-chip', all && 'rf-chip-focus')} aria-pressed={all} onClick={() => setAll((value) => !value)}>
            {all ? 'All records' : 'Governance'}
          </button>
          <div className="rf-segmented" role="group" aria-label="Records lens">
            <button type="button" className={cn('rf-seg', lens === 'ledger' && 'on')} aria-pressed={lens === 'ledger'} onClick={() => setLens('ledger')}>
              Ledger
            </button>
            <button type="button" className={cn('rf-seg', lens === 'grid' && 'on')} aria-pressed={lens === 'grid'} onClick={() => setLens('grid')}>
              Grid
            </button>
          </div>
        </div>
      </header>

      <div className={cn('rr-layout', lens === 'grid' && 'grid')}>
        <main className="rr-main">
          {!records.length ? (
            <div className="rf-emptystate">
              <b>No records yet</b>
              <span>Decisions, risks, questions and evidence appear here once the store records them.</span>
            </div>
          ) : lens === 'grid' ? (
            <div className="rr-cards">
              {records.map((record) => (
                <button type="button" key={record.id} className={cn('rr-card', chosen?.id === record.id && 'sel')} onClick={() => setSelected(record.id)}>
                  <span className={cn('rr-kind', KIND_CLASS[record.kind] ?? 'rr-kind-record')}>{record.kind}</span>
                  <b>{record.title}</b>
                  <span className="rr-card-id">{record.id}</span>
                  <span className="rr-card-about">{record.about ? text(byId.get(record.about)?.title, record.about) : 'no subject'}</span>
                </button>
              ))}
            </div>
          ) : (
            <table className="rr-ledger">
              <thead>
                <tr><th>Kind</th><th>Record</th><th>About</th><th>Status</th></tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr key={record.id} className={chosen?.id === record.id ? 'sel' : undefined} onClick={() => setSelected(record.id)} tabIndex={0} onKeyDown={(event) => event.key === 'Enter' && setSelected(record.id)}>
                    <td><span className={cn('rr-kind', KIND_CLASS[record.kind] ?? 'rr-kind-record')}>{record.kind}</span></td>
                    <td><b>{record.title}</b><span className="rr-ledger-id">{record.id}</span></td>
                    <td>{record.about ? text(byId.get(record.about)?.title, record.about) : <span className="faint">—</span>}</td>
                    <td><span className="rf-chip rf-chip-muted">{record.status || '—'}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="rf-note">Every row is a real record. The subject follows the memory relation; the UI never tags one by hand.</p>
        </main>
        {chosen ? <RecordDetail record={chosen} byId={byId} openDoc={openDoc} /> : null}
      </div>
    </div>
  );

  function setLens(next: 'ledger' | 'grid') {
    const values: Record<string, string> = {};
    for (const [key, value] of params) values[key] = value;
    if (next === 'ledger') delete values.lens;
    else values.lens = next;
    navigate('decisions', values);
  }
}
