import { useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RAIL_KEY } from '../refresh';
import { object, rows, text, type Data, type PageProps } from '../contracts';

/* UI-04 Rules and corrections review.
 *
 * Reads are the source-backed UI contracts (ui_rules, ui_corrections,
 * ui_correction_history, ui_correction_detail and the adoption preview
 * ui_correction_review). Writes go through the App mutate path - the same
 * envelope, confirm flag, refresh and stale handling every other form uses.
 *
 * The stale-preview fence is the ENGINE's: the preview revision (a text digest)
 * and the reviewed-text digest travel back with the adoption, and the engine
 * refuses an adoption whose governing text moved meanwhile. The browser never
 * decides that by comparing strings itself.
 */

const INTEL_CONTRACT = 'ui-intel-v1';

async function intel(request: PageProps['request'], view: string, query: Record<string, string> = {}, signal?: AbortSignal): Promise<Data> {
  const path = '/api/read/' + view + (Object.keys(query).length ? '?' + new URLSearchParams(query).toString() : '');
  const init = signal ? { signal } : undefined;
  const body = await (request as unknown as (path: string, init?: RequestInit) => Promise<Data>)(path, init);
  if (text(body.contract) !== INTEL_CONTRACT) throw new Error('unsupported read contract for ' + view);
  return body;
}

export function correctionId(entry: Data): string {
  return text(object(entry.identity).id, text(entry.id));
}

export function isTerminal(status: string): boolean {
  return status === 'adopted' || status === 'retired';
}

/* A line diff: unchanged lines kept in order, removed lines before the added
 * ones at each change point. Enough for a reviewed-text before/after view. */
export function diffLines(before: string, after: string): { kind: 'same' | 'add' | 'del'; text: string }[] {
  const left = before ? before.split('\n') : [];
  const right = after ? after.split('\n') : [];
  const out: { kind: 'same' | 'add' | 'del'; text: string }[] = [];
  let start = 0;
  while (start < left.length && start < right.length && left[start] === right[start]) start++;
  let endLeft = left.length, endRight = right.length;
  while (endLeft > start && endRight > start && left[endLeft - 1] === right[endRight - 1]) { endLeft--; endRight--; }
  for (let i = 0; i < start; i++) out.push({ kind: 'same', text: left[i] });
  for (let i = start; i < endLeft; i++) out.push({ kind: 'del', text: left[i] });
  for (let i = start; i < endRight; i++) out.push({ kind: 'add', text: right[i] });
  for (let i = endLeft; i < left.length; i++) out.push({ kind: 'same', text: left[i] });
  return out;
}

export async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value.trim());
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export type RecordForm = {
  text: string; task: string; files: string; kind: 'correction' | 'scope-change';
  convention: string; check: string; event_id: string;
};

export function newRecordForm(task = ''): RecordForm {
  const suffix = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  return { text: '', task, files: '', kind: 'correction', convention: '', check: '', event_id: 'ui-' + suffix };
}

/* The payload keeps the event identity across a retry: a failed response is
 * retried with the SAME event_id, so the store either records once or reports
 * the conflict - it never doubles the correction. */
export function recordPayload(form: RecordForm): Record<string, string> {
  const payload: Record<string, string> = {
    text: form.text.trim(), task: form.task.trim(), files: form.files.trim(),
    kind: form.kind, via: 'ui', event_id: form.event_id,
  };
  if (form.convention.trim()) payload.convention = form.convention.trim();
  if (form.check.trim()) payload.check = form.check.trim();
  return payload;
}

export type AdoptArgs = {
  correction: string; action: 'adopt'; text: string; confirm_replace: boolean;
  expected_revision: string; reviewed_digest: string;
};

/* The adoption payload pins BOTH sides: the governing revision the preview
 * showed and the digest of the text being adopted. Empty governing text needs no
 * confirm_replace because nothing is replaced. */
export async function adoptPayload(packet: Data, reviewed: string): Promise<AdoptArgs> {
  const governing = object(packet.governing);
  const current = text(governing.text);
  const correction = text(object(packet.correction).id);
  return {
    correction, action: 'adopt', text: reviewed.trim(),
    confirm_replace: current !== '' && current !== reviewed.trim(),
    expected_revision: text(governing.revision),
    reviewed_digest: await sha256Hex(reviewed),
  };
}

export function retirePayload(correction: string): Record<string, string> {
  return { correction, action: 'retire' };
}

export function resultHeadline(result: Data): string {
  const action = text(result.action);
  if (text(result.error) || text(result.reason_code)) {
    const code = text(result.reason_code, text(result.error_code));
    if (code === 'STALE_CONVENTION') return 'The governing text changed since the preview: nothing was replaced. Read the current text and review again.';
    if (code === 'STALE_REVIEW') return 'The reviewed text changed after the preview: nothing was replaced. Reopen the review.';
    if (code === 'NEEDS_CONFIRMATION') return 'The convention already has different text: explicit replacement confirmation is required.';
    return text(result.error);
  }
  if (action === 'adopt') return result.idempotent === true ? 'Already adopted with this text: nothing changed.' : 'Adopted: the governing prose changed, its rail was preserved and the proposed check was not activated.';
  if (action === 'retire') return result.idempotent === true ? 'Already retired: nothing changed.' : 'Retired: the proposal is kept as evidence and no longer in the pending window.';
  return 'Done.';
}

function Flag({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div className="row" style={{ gap: 8, alignItems: 'baseline', fontSize: 12 }}>
    <span className="faint" style={{ minWidth: 110 }}>{label}</span>
    <span>{value}</span>
    {hint ? <span className="faint">{hint}</span> : null}
  </div>;
}

export function CorrectionDetail({ packet, detail, busy, onAdopt, onRetire, error }: {
  packet: Data; detail: Data; busy: boolean; error?: string;
  onAdopt: (args: AdoptArgs) => void; onRetire: (args: Record<string, string>) => void;
}) {
  const correction = object(packet.correction);
  const governing = object(packet.governing);
  const id = text(correction.id);
  const status = text(correction.status, 'proposed');
  const proposed = text(correction.text);
  const current = text(governing.text);
  const [reviewed, setReviewed] = useState(proposed);
  const [step, setStep] = useState<'edit' | 'confirm' | 'retire' | 'done'>('edit');
  const diff = useMemo(() => diffLines(current, reviewed), [current, reviewed]);
  const rail = object(governing.rail);
  const railLine = text(rail.status) ? ('declared ' + text(rail.status) + (text(rail.check) ? ' - ' + text(rail.check) : '')) : 'no rail declared';
  const terminal = status === 'adopted' || status === 'retired';
  const history = object(detail.history);

  if (text(packet.error_code)) {
    return <section aria-label="Correction review" className="card" style={{ padding: 12 }}>
      <h3 style={{ marginTop: 0 }}>Correction review</h3>
      <p role="alert">{error || text(packet.reason, 'This correction cannot be reviewed: the identity is unknown, ambiguous or unsafe.')}</p>
      {Array.isArray(packet.candidates) && packet.candidates.length ? <p className="faint">Candidates: {packet.candidates.map(candidate => String(candidate)).join(', ')}</p> : null}
    </section>;
  }

  const adopt = async () => {
    const args = await adoptPayload(packet, reviewed);
    setStep('done');
    onAdopt(args);
  };

  return <section aria-label="Correction review" className="card" style={{ padding: 12 }}>
    <h3 style={{ marginTop: 0 }}>{text(correction.title, 'Correction')}</h3>
    <Flag label="Identity" value={id} />
    <Flag label="Status" value={status} hint={terminal ? 'terminal: history keeps the evidence' : 'proposed, not governing'} />
    <Flag label="Kind" value={text(correction.kind)} />
    <Flag label="Task" value={text(correction.task, 'none')} />
    <Flag label="Files" value={rows(correction.files).length ? rows(correction.files).map(file => text(file)).join(', ') : 'none'} />
    <Flag label="Target" value={text(governing.convention, 'none')} />
    <Flag label="Rail" value={railLine} hint="adoption never changes it" />
    {error ? <p role="alert" className="offline-bar">{error}</p> : null}

    <h4>Feedback as recorded</h4>
    <pre className="mono" style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{proposed}</pre>

    <h4>Current governing text</h4>
    {governing.exists === false ? <p className="faint">No existing governing text: adoption is refused (a convention is never created).</p>
      : <pre className="mono" style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{current || '(empty)'}</pre>}

    {!terminal && text(governing.revision) ? <>
      <h4>Reviewed text</h4>
      <p className="faint" style={{ fontSize: 12 }}>Adopting writes this text into the existing convention. The rail above and the proposed check are untouched; nothing is activated.</p>
      <textarea aria-label="Reviewed convention text" value={reviewed} onChange={event => { setReviewed(event.target.value); setStep('edit'); }} rows={8} style={{ width: '100%', fontSize: 12 }} />
      <h4>Before / after</h4>
      <pre className="mono" aria-label="Reviewed text diff" style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{diff.map((line, index) => <div key={index} data-diff={line.kind} style={{ color: line.kind === 'del' ? '#b42318' : line.kind === 'add' ? '#067647' : undefined }}>{(line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : '  ') + line.text}</div>)}</pre>
      {step === 'edit' ? <div className="row" style={{ gap: 8 }}>
        <button type="button" className="btn btn-primary" disabled={busy || !reviewed.trim()} onClick={() => setStep('confirm')}>Review adoption</button>
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setStep('retire')}>Retire this proposal</button>
      </div> : null}
      {step === 'confirm' ? <div role="alertdialog" aria-label="Replace the governing text?" className="card" style={{ padding: 10, marginTop: 8 }}>
        <b>Replace the {text(governing.convention, 'target')} governing text?</b>
        <p style={{ fontSize: 12 }}>The reviewed text replaces the current prose after this confirmation. The rail stays {railLine}; the proposed check is not activated.</p>
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void adopt()}>Replace the governing text</button>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setStep('edit')}>Cancel</button>
        </div>
      </div> : null}
      {step === 'retire' ? <div role="alertdialog" aria-label="Retire this proposal?" className="card" style={{ padding: 10, marginTop: 8 }}>
        <b>Retire this proposal?</b>
        <p style={{ fontSize: 12 }}>The proposal is kept as evidence and leaves the pending window. No governing text changes.</p>
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => { setStep('done'); onRetire(retirePayload(id)); }}>Retire the proposal</button>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setStep('edit')}>Cancel</button>
        </div>
      </div> : null}
    </> : <p className="faint">{terminal ? 'This correction is terminal: no adoption or retirement is offered.' : 'This proposal names no existing convention text, so adoption is not offered.'}</p>}

    <h4>History</h4>
    {history.known === false ? <p className="faint">Transitions are unknown on this store (no event log). The current document state is shown above.</p>
      : rows(history.entries).length ? rows(history.entries).map((entry, index) => <div key={index} style={{ fontSize: 12 }}>{text(entry.at)} - {text(entry.to)} - {text(entry.actor, 'unknown')}{entry.via ? ' - ' + text(entry.via) : ''}</div>)
        : <p className="faint">No transitions recorded yet.</p>}
  </section>;
}

export function RecordCorrectionForm({ initialTask = '', busy, onSubmit }: {
  initialTask?: string; busy: boolean; onSubmit: (payload: Record<string, string>) => void;
}) {
  const [form, setForm] = useState<RecordForm>(() => newRecordForm(initialTask));
  const [sent, setSent] = useState('');
  return <section aria-label="Record a correction" className="card" style={{ padding: 12 }}>
    <h3 style={{ marginTop: 0 }}>Record a correction</h3>
    <p className="faint" style={{ fontSize: 12 }}>A correction is proposed feedback, never a rule: it is stored as a proposal and only adoption changes governing prose. The task and files are explicit so the proposal keeps its provenance.</p>
    <label>What should be done differently?
      <textarea aria-label="Correction text" value={form.text} onChange={event => setForm({ ...form, text: event.target.value })} rows={4} style={{ width: '100%', fontSize: 12 }} />
    </label>
    <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      <label>Task <input aria-label="Correction task" value={form.task} onChange={event => setForm({ ...form, task: event.target.value })} /></label>
      <label>Files <input aria-label="Correction files" value={form.files} onChange={event => setForm({ ...form, files: event.target.value })} placeholder="comma-separated repo paths" /></label>
      <label>Kind <select aria-label="Correction kind" value={form.kind} onChange={event => setForm({ ...form, kind: event.target.value === 'scope-change' ? 'scope-change' : 'correction' })}>
        <option value="correction">Correction</option><option value="scope-change">Scope change</option>
      </select></label>
      <label>Convention <input aria-label="Correction convention" value={form.convention} onChange={event => setForm({ ...form, convention: event.target.value })} placeholder="optional target" /></label>
      <label>Check <input aria-label="Correction check" value={form.check} onChange={event => setForm({ ...form, check: event.target.value })} placeholder="optional cmd:" /></label>
    </div>
    <div className="row" style={{ gap: 8, marginTop: 8 }}>
      <button type="button" className="btn btn-primary" disabled={busy || !form.text.trim() || !form.task.trim()} onClick={() => {
        setSent(form.event_id);
        onSubmit(recordPayload(form));
      }}>Record the proposal</button>
      <span className="faint" style={{ fontSize: 11 }}>Retry keeps event identity {form.event_id}{sent ? ' (sent)' : ''}</span>
    </div>
  </section>;
}

export function RulesPage({ request, mutate, busy, params }: PageProps) {
  const client = useQueryClient();
  const [tab, setTab] = useState<'governing' | 'pending' | 'history'>('governing');
  const [selected, setSelected] = useState(() => params.get('correction') || '');
  const [result, setResult] = useState<Data | null>(null);
  const [writeError, setWriteError] = useState('');
  const [recording, setRecording] = useState(() => Boolean(params.get('task')));
  const rules = useQuery({ queryKey: [RAIL_KEY, 'rules', 'governing'], queryFn: ({ signal }) => intel(request, 'ui_rules', {}, signal), staleTime: 30_000 });
  const pending = useQuery({ queryKey: [RAIL_KEY, 'rules', 'pending'], queryFn: ({ signal }) => intel(request, 'ui_corrections', { status: 'proposed', limit: '50' }, signal), staleTime: 30_000 });
  const history = useQuery({ queryKey: [RAIL_KEY, 'rules', 'history'], queryFn: ({ signal }) => intel(request, 'ui_correction_history', { limit: '100' }, signal), staleTime: 30_000 });
  const detail = useQuery({ queryKey: [RAIL_KEY, 'rules', 'detail', selected], enabled: !!selected, queryFn: ({ signal }) => intel(request, 'ui_correction_detail', { correction: selected }, signal), staleTime: 30_000 });
  const review = useQuery({ queryKey: [RAIL_KEY, 'rules', 'correction-review', selected], enabled: !!selected, queryFn: ({ signal }) => intel(request, 'ui_correction_review', { correction: selected }, signal), staleTime: 30_000 });

  const refreshAll = async () => {
    await client.invalidateQueries({ queryKey: [RAIL_KEY, 'rules'] });
  };
  const write = async (build: () => Promise<Record<string, unknown>> | Record<string, unknown>, confirm: boolean) => {
    setWriteError('');
    try {
      const payload = await build();
      const outcome = await mutate('ui_correction_apply', payload, confirm) as Data;
      setResult(outcome);
      await refreshAll();
    } catch (error) {
      setWriteError(error instanceof Error ? error.message : String(error));
    }
  };
  const record = async (payload: Record<string, string>) => {
    setWriteError('');
    try {
      const outcome = await mutate('correction_record', payload, false) as Data;
      setResult(outcome);
      setRecording(false);
      await refreshAll();
    } catch (error) {
      setWriteError(error instanceof Error ? error.message : String(error));
    }
  };

  const pendingData = object(pending.data?.data);
  const historyData = object(history.data?.data);
  const rulesData = object(rules.data?.data);
  const detailData = object(detail.data?.data);
  const reviewData = object(review.data?.data);
  const undefinedState = (query: { isError: boolean; error: Error | null; data?: Data }) => query.isError
    ? <p role="alert">This read failed: {query.error?.message}.</p>
    : null;

  return <div className="page understanding">
    <header className="page-head">
      <div><h1>Rules &amp; corrections</h1>
        <p className="faint">Governing conventions stay separate from proposed corrections and from adopted/retired history. Active rails are listed apart from prose; nothing here runs a check.</p></div>
      <div className="row" style={{ gap: 8 }}>
        <button type="button" className="btn btn-secondary" disabled={busy || recording} onClick={() => setRecording(value => !value)}>Record a correction</button>
      </div>
    </header>
    {writeError ? <p role="alert" className="offline-bar">{writeError}</p> : null}
    {result ? <section className="card" style={{ padding: 12 }} aria-label="Last result">
      <b>{resultHeadline(result)}</b>
      {text(result.reason_code) === 'STALE_CONVENTION' && text(result.existing_text) ? <pre className="mono" style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{text(result.existing_text)}</pre> : null}
      <p className="faint" style={{ fontSize: 11 }}>The result is kept in full below; a retried click is idempotent.</p>
      <details><summary>Full result</summary><pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(result, null, 2)}</pre></details>
    </section> : null}
    {recording ? <RecordCorrectionForm initialTask={params.get('task') || ''} busy={busy} onSubmit={payload => void record(payload)} /> : null}
    <nav className="row" role="tablist" aria-label="Rules sections" style={{ gap: 6 }}>
      {(['governing', 'pending', 'history'] as const).map(name => <button key={name} type="button" role="tab" aria-selected={tab === name} className={'btn ' + (tab === name ? 'btn-primary' : 'btn-secondary')} onClick={() => setTab(name)}>{name === 'governing' ? 'Governing conventions' : name === 'pending' ? 'Pending corrections' : 'History'}</button>)}
    </nav>
    {tab === 'governing' ? <section aria-label="Governing conventions">
      {undefinedState(rules)}
      {rows(rulesData.conventions).map((convention, index) => {
        const declared = rows(rulesData.rails).find(rail => text(rail.id) === text(convention.key));
        return <article key={index} className="card" style={{ padding: 12, marginTop: 8 }}>
          <h3 style={{ marginTop: 0 }}>{text(convention.title, text(convention.key))}</h3>
          <p className="faint" style={{ fontSize: 12 }}>Governing prose - this is policy, not a proposal.</p>
          <pre className="mono" style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{convention.truncated ? text(convention.text) + ' ... (truncated: open the source to read the rest)' : text(convention.text, '(no governing text yet)')}</pre>
          <div className="row" style={{ gap: 8, alignItems: 'baseline', fontSize: 12 }}>
            <b>{declared ? 'Active rail' : 'No rail'}</b>
            <span>{declared ? text(declared.state, 'missing') + (text(declared.command) ? ' - ' + text(declared.command) : '') : 'none declared'}</span>
            <span className="faint">the rail is separate from the prose and is never changed by adoption</span>
          </div>
          {text(declared?.error) ? <p role="alert" className="faint">{text(declared?.error)}</p> : null}
        </article>;
      })}
    </section> : null}
    {tab === 'pending' ? <section aria-label="Pending corrections">
      {undefinedState(pending)}
      <div className="row" style={{ gap: 8, fontSize: 12 }}>
        <span>{text(pendingData.count, '0')} shown of {text(pendingData.matched, '0')} matching; {text(object(pendingData.window).undecided, '0')} undecided; newest {text(object(pendingData.window).in_window, '0')} in the brief window.</span>
      </div>
      {pendingData.truncated === true ? <p role="status" className="faint">This list is only a page: pass its next_cursor to read the rest. Nothing is inferred from the truncation.</p> : null}
      {rows(pendingData.ambiguous).length ? <p role="alert" className="faint">Some identities are carried by disagreeing records and are reported, not guessed: {rows(pendingData.ambiguous).map(item => text(item.id)).join(', ')}.</p> : null}
      {rows(pendingData.entries).map((entry, index) => <button key={index} type="button" className={'card ' + (correctionId(entry) === selected ? 'on' : '')} style={{ display: 'block', textAlign: 'left', padding: 12, marginTop: 8, width: '100%' }} onClick={() => setSelected(correctionId(entry))}>
        <b>{text(entry.title, 'Correction')}</b>
        <div className="faint" style={{ fontSize: 12 }}>{text(entry.kind)} - {text(entry.status)} - {text(entry.task, 'no task')} - {correctionId(entry)}</div>
        <div style={{ fontSize: 12 }}>{text(entry.text).slice(0, 240)}</div>
        <div className="faint" style={{ fontSize: 11 }}>Target {text(entry.proposes, 'none')} - adoption never activates a rail</div>
      </button>)}
      {!rows(pendingData.entries).length ? <p className="faint">No pending proposals: nothing waits for a decision.</p> : null}
    </section> : null}
    {tab === 'history' ? <section aria-label="Correction history">
      {undefinedState(history)}
      {historyData.known === false ? <p className="faint">This store keeps no event log: terminal identity is unknown, not inferred.</p> : null}
      {historyData.truncated === true ? <p role="status" className="faint">History is truncated to this page; older transitions remain in the log.</p> : null}
      {rows(historyData.entries).map((entry, index) => <div key={index} className="card" style={{ padding: 8, marginTop: 6 }}>
        <button type="button" className="link" onClick={() => setSelected(text(entry.correction))}>{text(entry.correction)}</button>
        <span className="faint" style={{ fontSize: 12 }}> {text(entry.to)} - {text(entry.at)} - {text(entry.actor, 'unknown')}</span>
      </div>)}
      {rows(historyData.current).length ? <p className="faint" style={{ fontSize: 12 }}>Current states from the documents: {rows(historyData.current).map(item => text(item.id) + ': ' + text(item.status)).join('; ')}</p> : null}
    </section> : null}
    {selected ? <div style={{ marginTop: 12 }}>
      {review.isError || detail.isError ? <p role="alert">The correction could not be read.</p> : null}
      {review.data ? <CorrectionDetail
        key={text(object(reviewData.correction).id) + '|' + text(object(reviewData.governing).revision)}
        packet={reviewData} detail={detailData} busy={busy}
        error={text((review.data.reasons as unknown[] | undefined)?.[0]) || text((detail.data?.reasons as unknown[] | undefined)?.[0])}
        onAdopt={args => void write(() => args, true)}
        onRetire={args => void write(() => args, false)} /> : null}
    </div> : <p className="faint" style={{ marginTop: 12 }}>Select a proposal to read its full feedback and review its adoption.</p>}
  </div>;
}