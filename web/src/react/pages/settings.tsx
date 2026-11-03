/* UI-07 project-scoped automation settings: the Settings section.
 *
 * One read (the existing ui_adapter_diagnostics document, extended with the
 * additive UI-07 keys) and, when the person confirms, the existing adapter
 * tool call. The section keeps the three facts apart on purpose:
 *
 *   generated  - the canonical Claude block (wrote is false; the operator merges)
 *   installed  - the marked post-commit hook in this project's hooks directory
 *   delivered  - harness.moment events whose data.via names the adapter
 *
 * Every row renders the source it came from. An absent, unreadable or
 * unsourced state is unknown with the read's reason - never a default, never
 * "enabled" and never "working". Nothing here writes global git or harness
 * configuration: the git install refuses a hooks directory outside the project
 * (the read names the refusal before the page offers the button) and a Claude
 * generation writes nothing at all.
 *
 * A change is always confirmed in this section's own dialog, then sent through
 * the App mutate path with confirm: true - the ordinary tool envelope, never a
 * silent write. After the write the read is invalidated and the result names
 * the refresh boundary, or says "applied, view stale" when the refresh failed.
 */
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RAIL_KEY } from '../refresh';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { Badge, Button, ErrorState, Loading } from '../ui';

const INTEL_CONTRACT = 'ui-intel-v1';
const VIEW = 'ui_adapter_diagnostics';
const PAYLOAD_VIEW = 'adapter_diagnostics';

type RawRequest = (path: string, init?: RequestInit) => Promise<Data>;
type Action = 'install' | 'remove';

export async function automationRead(request: PageProps['request'], signal?: AbortSignal): Promise<Data> {
  const body = await (request as unknown as RawRequest)('/api/read/' + VIEW, signal ? { signal } : undefined);
  if (text(body.contract) !== INTEL_CONTRACT) throw new Error('The automation read returned an unsupported contract');
  if (text(body.view) !== PAYLOAD_VIEW) throw new Error('The automation read returned another view');
  if (!['ok', 'unknown', 'refused', 'error'].includes(text(body.status))) throw new Error('The automation read returned no state');
  return body;
}

/* A list the read publishes as plain strings (paths, reasons, candidates).
 * rows() only keeps objects, so these lists read through here. */
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => text(item)).filter(Boolean) : [];
}

/* A count that is not an integer is unknown - never 0, never a success. */
export function countText(value: unknown): string {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? String(value) : 'unknown';
}

export function automationRows(data: Data): Data[] {
  return rows(data.automation);
}

export function adapterOf(data: Data, kind: string): Data {
  return automationRows(data).find(row => text(row.kind) === kind) ?? {};
}

export function stateTone(state: string): 'neutral' | 'good' | 'warning' | 'danger' {
  if (state === 'installed' || state === 'configured') return 'good';
  if (state === 'foreign' || state === 'modified' || state === 'symlink' || state === 'outside-project') return 'warning';
  if (state === 'unknown' || state === 'unavailable') return 'neutral';
  return 'neutral';
}

/* The authored policy is the ONLY source for enabled/disabled/paused. */
export function policyState(policy: Data, kind: string): { value: string; sourced: boolean } {
  if (text(policy.state) !== 'recorded') return { value: 'unknown', sourced: false };
  const value = text(policy[kind], 'unknown');
  return { value: value || 'unknown', sourced: true };
}

export function ownershipText(row: Data): string {
  const owner = text(row.ownership, 'unknown');
  const labels: Record<string, string> = {
    'ctx-rail': 'owned by ctx-rail (the marker line is present)',
    operator: 'owned by the operator (ctx-rail never writes it)',
    foreign: 'owned by something else (no ctx-rail marker)',
    'outside-project': 'outside this project (a global or custom hooks directory)',
    none: 'no hook installed',
    unknown: 'ownership unknown',
  };
  return labels[owner] ?? owner;
}

export function installTarget(row: Data): string {
  const install = object(row.install);
  const writes = strings(install.writes);
  const path = text(install.path);
  if (text(install.refused)) return 'refused: ' + text(install.refused);
  if (writes.length) return 'would write ' + writes.join(', ');
  if (path) return 'no write is offered for ' + path;
  return 'no writable path is sourced';
}

export function deliveryText(row: Data): string {
  const delivery = object(row.delivery);
  if (!Object.keys(delivery).length) return 'delivery unknown: this read sources no observations';
  const state = text(delivery.state, 'unknown');
  if (state === 'unknown') return 'delivery unknown: ' + text(delivery.reason, 'no evidence');
  if (state === 'receipt-only') return 'delivery unobservable here: ' + text(delivery.reason, 'the hook is receipt-only');
  const count = countText(delivery.count);
  const last = text(delivery.last_at);
  return 'delivered ' + count + ' observed moment(s)' + (last ? ', newest ' + last : '') + ' (' + text(delivery.source) + ')';
}

export function limitLines(data: Data): string[] {
  const limits = object(data.limits);
  const lines = rows(limits.rows).map(row => {
    const unit = text(row.unit);
    return text(row.name, 'limit') + ': ' + countText(row.value) + (unit ? ' ' + unit : '')
      + ' - ' + text(row.consequence) + ' (source ' + text(row.source, 'unknown') + ')';
  });
  const platform = object(limits.platform);
  for (const key of Object.keys(platform).sort()) {
    lines.push(key + ': ' + text(platform[key]));
  }
  return lines;
}

export function refusalText(result: Data): string {
  const code = text(result.refused) || text(result.error_code);
  const message = text(result.error) || text(result.message);
  return (code ? 'refused (' + code + '): ' : '') + (message || 'the tool refused the call');
}

export function effectText(result: Data): string {
  const status = text(result.status);
  if (status === 'refused') return refusalText(result);
  const written = text(result.hook) || text(result.removed);
  if (result.installed === true && written) return 'wrote ' + written + ' (the hook is installed; its running is not claimed)';
  if (result.removed) return 'removed ' + text(result.removed);
  if (result.wrote === false) return 'nothing was written: ' + text(result.message, 'the tool generated output and wrote no file');
  if (written) return 'wrote ' + written;
  return text(result.message, 'the tool answered ' + (status || 'no state'));
}

function Flag({ label, value }: { label: string; value: string }) {
  return <div className="row rail-row" style={{ gap: 8, alignItems: 'baseline', fontSize: 12 }}>
    <span className="faint" style={{ minWidth: 132 }}>{label}</span>
    <span style={{ overflowWrap: 'anywhere' }}>{value}</span>
  </div>;
}

function AdapterCard({ row, policy, busy, onAction }: {
  row: Data; policy: Data; busy: boolean;
  onAction: (row: Data, action: Action) => void;
}) {
  const kind = text(row.kind);
  const install = object(row.install);
  const remove = object(row.remove);
  const state = text(row.state, 'unknown');
  const authored = policyState(policy, kind);
  /* A Claude generation writes nothing, so it is offered even with no write
   * path; a git install is offered only when the read names a writable path
   * and no refusal. */
  const installable = !text(install.refused)
    && (kind === 'claude' || strings(install.writes).length > 0);
  const removable = strings(remove.writes).length > 0;
  return <section className="card" data-adapter={kind} aria-label={text(row.label, kind)} style={{ padding: 12, marginTop: 10 }}>
    <div className="row" style={{ gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
      <b>{text(row.label, kind)}</b>
      <Badge tone={stateTone(state)}>{state}</Badge>
      <span className="faint" style={{ fontSize: 12 }}>{ownershipText(row)}</span>
    </div>
    <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>Source: {text(row.owner_source, 'unknown')}</div>
    {text(object(row.artifact).path) ? <Flag label={kind === 'claude' ? 'Config path' : 'Hook path'} value={text(object(row.artifact).path)} /> : null}
    {text(object(row.artifact).core_hooks_path) ? <Flag label="core.hooksPath" value={text(object(row.artifact).core_hooks_path)} /> : null}
    {text(row.reason) ? <p className="faint" style={{ fontSize: 12, margin: '4px 0' }} data-state-reason>{text(row.reason)}</p> : null}
    <div className="faint" style={{ fontSize: 12 }}>
      Authored policy: <b data-policy={kind}>{authored.value}</b>
      {authored.sourced ? ' (from ' + text(policy.source) + ' in ' + text(policy.declared_in) + ')' : ' - no authored record, so unknown'}
    </div>
    <Flag label="A project install" value={installTarget(row)} />
    {text(install.preview) ? <div className="faint" style={{ fontSize: 12 }}>Preview: {text(install.preview)}</div> : null}
    {text(install.marker) ? <div className="faint mono" style={{ fontSize: 11 }}>Hook marker: {text(install.marker)}</div> : null}
    {install.requires_operator === true ? <div className="faint" style={{ fontSize: 12 }}>
      Generation is not installation: the operator reviews and merges the block themselves. ctx-rail never writes .claude/settings.json.
    </div> : null}
    <Flag label="Delivery" value={deliveryText(row)} />
    <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
      <Button variant="primary" disabled={busy || !installable} onClick={() => onAction(row, 'install')}>
        {text(row.kind) === 'git' ? 'Install the project hook' : 'Generate the block'}
      </Button>
      <Button variant="secondary" disabled={busy || !removable} onClick={() => onAction(row, 'remove')}>{text(row.kind) === 'git' ? 'Remove the hook' : 'Remove the block'}</Button>
      <span className="faint" style={{ fontSize: 11 }}>{text(row.note)}</span>
    </div>
  </section>;
}

function ConfirmDialog({ row, action, busy, onCancel, onConfirm }: {
  row: Data; action: Action; busy: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  const kind = text(row.kind);
  const install = object(row.install);
  const remove = object(row.remove);
  const target = (action === 'install' ? strings(install.writes) : strings(remove.writes)).join(', ');
  const title = action === 'install' ? 'Confirm this project-scoped write' : 'Confirm this removal';
  return <div className="card" role="alertdialog" aria-label={title} style={{ padding: 10, marginTop: 8 }}>
    <b>{title}</b>
    <p style={{ fontSize: 12, margin: '4px 0' }}>
      {kind === 'claude'
        ? 'The tool answers with the canonical block and writes nothing; you merge it into .claude/settings.json yourself.'
        : action === 'install'
          ? 'The tool writes exactly this file in this project and nowhere else:'
          : 'The tool removes exactly this file in this project and nowhere else:'}
    </p>
    <pre className="mono" aria-label="Exact path" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{target || '(no path is sourced)'}</pre>
    {kind === 'git' ? <p className="faint" style={{ fontSize: 11, margin: '4px 0' }}>
      No global git or harness configuration is touched. A global or custom core.hooksPath is refused by the tool.
    </p> : null}
    <div className="row" style={{ gap: 8 }}>
      <Button variant="primary" disabled={busy} onClick={onConfirm}>{busy ? 'Running...' : 'Confirm'}</Button>
      <Button variant="secondary" disabled={busy} onClick={onCancel}>Cancel</Button>
    </div>
  </div>;
}

export function AutomationSettings({ request, mutate, busy, project, refresh }: Pick<PageProps, 'request' | 'mutate' | 'busy' | 'project' | 'refresh'>) {
  const client = useQueryClient();
  const [pending, setPending] = useState<{ row: Data; action: Action } | null>(null);
  const [result, setResult] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const read = useQuery({
    queryKey: [RAIL_KEY, project, 'automation-settings'],
    queryFn: ({ signal }) => automationRead(request, signal),
    staleTime: 15_000,
    retry: false,
  });
  const body = read.data;
  const data = useMemo(() => object(body?.data), [body]);
  const rowsOut = useMemo(() => automationRows(data), [data]);
  const policy = object(data.policy);
  const status = text(body?.status);
  const asOf = text(body?.as_of);
  const reasons = strings(body?.reasons);

  const reload = () => {
    void client.invalidateQueries({ queryKey: [RAIL_KEY, project, 'automation-settings'] });
  };
  const run = async () => {
    if (!pending) return;
    const args: Record<string, unknown> = { kind: text(pending.row.kind), action: pending.action };
    setError('');
    try {
      const outcome = await mutate('adapter', args, true);
      setResult(outcome);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setPending(null);
      if (refresh) void refresh();
      reload();
    }
  };
  const failure = object(result ? object(result).refresh_failure : {});
  const boundary = result
    ? (failure.stale === true
        ? 'applied, view stale: ' + text(failure.error, 'the refresh failed')
        : 'the view was refreshed; this read is as of ' + (asOf || 'unknown'))
    : '';

  return <div className="rail-automation" data-section="automation">
    <PageTitle />
    {read.isPending ? <Loading /> : null}
    {read.isError ? <ErrorState error={read.error instanceof Error ? read.error.message : String(read.error)} retry={reload} /> : null}
    {body && status !== 'ok' ? <section className="card" role="alert" aria-label="Automation read state" style={{ padding: 10, marginTop: 8 }}>
      <b>The store answered {status || 'no state'}.</b>
      {reasons.map((reason, index) => <p key={index} className="faint" style={{ fontSize: 12 }}>{reason}</p>)}
      <p className="faint" style={{ fontSize: 12 }}>Nothing is shown as installed, enabled or delivered from a read that did not answer.</p>
    </section> : null}
    {body && status === 'ok' ? <>
      <section className="card" aria-label="Authored automation policy" data-policy-panel style={{ padding: 12, marginTop: 8 }}>
        <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
          <b>Authored policy</b>
          <Badge tone={text(policy.state) === 'recorded' ? 'good' : 'neutral'}>{text(policy.state, 'unknown')}</Badge>
        </div>
        <p className="faint" style={{ fontSize: 12, margin: '4px 0' }}>
          The only source for enabled, disabled or paused: an authored frontmatter record with its own source. Nothing here matches a slug or a task name.
        </p>
        {text(policy.state) === 'recorded' ? <div>
          <Flag label="Source" value={text(policy.source, 'unknown')} />
          <Flag label="Declared in" value={text(policy.declared_in, 'unknown')} />
          <Flag label="Git automation" value={policyState(policy, 'git').value} />
          <Flag label="Claude automation" value={policyState(policy, 'claude').value} />
          <Flag label="Moment contract" value={text(policy.moment_contract, 'unknown')} />
        </div> : <p className="faint" style={{ fontSize: 12 }} data-policy-reason>
          unknown - {text(policy.reason, 'no authored automation record in this store')}
          {strings(policy.candidates).length ? ' · candidates: ' + strings(policy.candidates).join(', ') : ''}
        </p>}
        {text(policy.reason) && text(policy.state) === 'recorded' ? <p className="faint" style={{ fontSize: 12 }}>{text(policy.reason)}</p> : null}
      </section>
      {rowsOut.map(row => <AdapterCard key={text(row.kind)} row={row} policy={policy} busy={busy} onAction={(target, action) => { setResult(null); setError(''); setPending({ row: target, action }); }} />)}
      {pending ? <ConfirmDialog row={pending.row} action={pending.action} busy={busy} onCancel={() => setPending(null)} onConfirm={() => void run()} /> : null}
      {error ? <p role="alert" className="offline-bar">{error}</p> : null}
      {result ? <section className="card" aria-label="Last result" data-result style={{ padding: 12, marginTop: 10 }}>
        <b>{effectText(result)}</b>
        <p className="faint" style={{ fontSize: 12, margin: '4px 0' }} data-refresh-boundary>{boundary}</p>
        {text(result.settings_json) ? <div>
          <p className="faint" style={{ fontSize: 12 }}>Generated configuration - merge it yourself; nothing was installed:</p>
          <pre className="mono" aria-label="Generated configuration" style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}>{text(result.settings_json)}</pre>
        </div> : null}
        <details><summary className="faint" style={{ fontSize: 11 }}>Full tool result</summary>
          <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(result, null, 2)}</pre>
        </details>
      </section> : null}
      <section className="card" aria-label="Engine limits" data-limits style={{ padding: 12, marginTop: 10 }}>
        <b>Engine limits and unexecuted paths</b>
        {limitLines(data).map((line, index) => <p key={index} className="faint" style={{ fontSize: 12, margin: '2px 0' }}>{line}</p>)}
      </section>
      <section className="card" aria-label="Not claimed here" data-not-reported style={{ padding: 12, marginTop: 10 }}>
        <b>Not claimed here</b>
        {Object.keys(object(data.not_reported)).sort().map(key => <p key={key} className="faint" style={{ fontSize: 12, margin: '2px 0' }}>
          <span className="mono">{key.replace(/_/g, ' ')}</span>: {text(object(data.not_reported)[key])}
        </p>)}
        <p className="faint" style={{ fontSize: 12 }}>Project scope: {text(object(data.scope).note, 'no scope note')}</p>
      </section>
    </> : null}
  </div>;
}

function PageTitle() {
  return <header className="ph rail-page-heading"><div>
    <h1>Automation</h1>
    <p className="faint">Project-scoped only: hook ownership and delivery for this project, with the exact write a change would make. Configuration generated, hook installed and delivery observed are three separate facts; absent evidence is unknown, and no global git or harness configuration is ever written here.</p>
  </div></header>;
}
