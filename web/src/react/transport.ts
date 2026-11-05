import { z } from 'zod';
import { INTEL_CONTRACT, object, rows, text, type Data, type Snapshot, type RailDoc, type Health, type IntelEnvelope, type IntelRules, type IntelCorrections, type IntelCorrectionDetail, type IntelCorrectionHistory, type IntelTaskEpisode, type IntelExecutionMetrics, type IntelAdapterDiagnostics, type IntelFilters } from './contracts';
export const projectValid = (id: string) => id === '' || /^[a-z0-9][a-z0-9-]{0,78}$/.test(id);
export const docValid = (id: string) => id.length > 0 && id.length < 200 && !/[\p{Cc}"'<>&`\\/]/u.test(id);
const envelope = z.record(z.string(), z.unknown());
const intelArgs = (filters: IntelFilters): Record<string, string> => {
  const args: Record<string, string> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === '') continue;
    args[key] = String(value);
  }
  return args;
};
export class RailClient {
  constructor(readonly project: string) { if (!projectValid(project)) throw new Error('Invalid project identifier'); }
  /** The one fetch: the HTTP status is kept so a caller can tell an absent
   * route (404) from a failed read (5xx) or a non-JSON answer. */
  private async fetchJson(path: string, init: RequestInit = {}): Promise<{ status: number; value: Data }> {
    const global = ['/api/projects', '/api/portfolio', '/api/tools'].includes(path);
    const address = new URL(path, window.location.origin);
    if (!global && this.project) address.searchParams.set('project', this.project);
    /* The launch page carries the token for this server. Send it on every
       request: the cookie is host-wide, so a second local UI on another port
       must not be able to shadow this one. A caller-supplied header wins. */
    const headers = new Headers(init.headers);
    const launchToken = typeof document === 'undefined' ? '' : document.querySelector<HTMLMetaElement>('meta[name="ctx-rail-token"]')?.content || '';
    if (launchToken && !headers.has('X-Rail-Token') && !headers.has('X-Context-Rail-Token')) headers.set('X-Rail-Token', launchToken);
    const response = await fetch(address, { credentials: 'same-origin', cache: 'no-store', ...init, headers });
    if ([401, 403].includes(response.status)) throw new Error('Access refused. Reopen the link printed by ctx-rail ui.');
    let value: unknown;
    try { value = await response.json(); } catch { throw new Error(`Unexpected response (${response.status})`); }
    const parsed = envelope.safeParse(value);
    if (!parsed.success || (!response.ok && typeof parsed.data.status !== 'string')) throw new Error(`Unexpected response (${response.status})`);
    return { status: response.status, value: parsed.data };
  }
  async request(path: string, init: RequestInit = {}): Promise<Data> {
    return (await this.fetchJson(path, init)).value;
  }
  async read(tool: string, args: Record<string, string> = {}, signal?: AbortSignal): Promise<Data> {
    const result = await this.request(`/api/read/${encodeURIComponent(tool)}?${new URLSearchParams(args)}`, { signal });
    if (result.status !== 'ok' || result.error) throw new Error(text(result.error, text(result.note, 'The read could not be completed')));
    return result;
  }
  private toSnapshot(value: Data): Snapshot {
    const collection = (name: string): RailDoc[] => rows(value[name]).filter(d => docValid(text(d.id))).map(d => ({ ...d, id: text(d.id), title: text(d.title, text(d.id)), type: text(d.type), path: text(d.path) }));
    if (!Array.isArray(value.tasks) || !value.project || typeof value.project !== 'object') throw new Error('Malformed project snapshot');
    return { ...value, project: object(value.project), tasks: collection('tasks'), phases: collection('phases'), scope_items: collection('scope_items'), decisions: collection('decisions'), risks: collection('risks'), questions: collection('questions'), core_docs: collection('core_docs'), statuses: Array.isArray(value.statuses) ? value.statuses.filter((s): s is string => typeof s === 'string') : [] };
  }
  async snapshot(fresh = false, signal?: AbortSignal): Promise<Snapshot> {
    const value = await this.request(`/api/snapshot${fresh ? '?fresh=1' : ''}`, { signal });
    if (value.status === 'error') throw new Error(text(value.error, 'Snapshot unavailable'));
    return this.toSnapshot(value);
  }
  /** The fast first-view document: /api/context, the snapshot minus diagnostics.
   *
   * The page gates on this read, so it must never wait for health. Exactly two
   * cases fall back to the legacy /api/snapshot read: the route is genuinely
   * absent (HTTP 404, an older server) or the request failed at the network
   * level. Any other bad answer - a 5xx, or a 2xx that is not the context
   * document - throws, so a production defect in /api/context surfaces in the
   * health/status band instead of hiding behind the slow path. */
  async context(signal?: AbortSignal): Promise<Snapshot> {
    let result: { status: number; value: Data };
    try {
      result = await this.fetchJson('/api/context', { signal });
    } catch (error) {
      if (error instanceof TypeError) return this.snapshot(false, signal); // network failure
      throw error;
    }
    if (result.status === 404) return this.snapshot(false, signal); // route genuinely absent
    if (result.status < 200 || result.status >= 300) throw new Error('The context read failed (' + result.status + ')');
    return this.toSnapshot(result.value); // malformed: throws, never silently downgraded
  }
  /** The diagnostics document: explicit ok|unknown|error, never implicitly clean.
   *
   * A missing or malformed answer is ``unknown`` with a reason - it is never
   * coerced to a clean board. */
  async health(signal?: AbortSignal): Promise<Health> {
    const unknown = (reason: string): Health => ({ schema: 'ui-health-v1', state: 'unknown', as_of: null, doctor: null, cache: null, error: null, error_code: null, reasons: [reason] });
    let value: Data;
    try {
      value = await this.request('/api/health', { signal });
    } catch (error) {
      if (error instanceof Error && error.message === 'Access refused. Reopen the link printed by ctx-rail ui.') throw error;
      return unknown(error instanceof Error ? error.message : 'the health read failed');
    }
    if (typeof value.schema !== 'string' || !['ok', 'unknown', 'error'].includes(text(value.state))) return unknown('the health response was not a health document');
    return { schema: 'ui-health-v1', state: text(value.state) as Health['state'], as_of: typeof value.as_of === 'string' ? value.as_of : null, doctor: value.doctor && typeof value.doctor === 'object' ? object(value.doctor) : null, cache: value.cache && typeof value.cache === 'object' ? object(value.cache) : null, error: typeof value.error === 'string' ? value.error : null, error_code: typeof value.error_code === 'string' ? value.error_code : null, reasons: Array.isArray(value.reasons) ? value.reasons.filter((r): r is string => typeof r === 'string') : [] };
  }
  async doc(id: string, signal?: AbortSignal): Promise<Data> {
    if (!docValid(id)) throw new Error('Invalid document identifier');
    const result = await this.request(`/api/doc/${encodeURIComponent(id)}`, { signal });
    if (result.status === 'error' || result.status === 'refused') throw new Error(text(result.error, 'Document unavailable'));
    return result;
  }
  async call(tool: string, args: Record<string, unknown>, confirm = false): Promise<Data> {
    const token = document.querySelector<HTMLMetaElement>('meta[name="ctx-rail-token"]')?.content || '';
    return this.request(`/api/tool/${encodeURIComponent(tool)}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Context-Rail-Token': token }, body: JSON.stringify({ args, confirm }) });
  }
  /* -- UI-02: source-backed intelligence read contracts -------------------
     Each view is one versioned, bounded document. Missing data is rendered
     from the envelope (null + reasons), never as zero/success/failure; an
     unsupported contract or a missing state is an error, never an empty
     success. The browser never reads event or receipt files directly. */
  private async intel<T extends IntelEnvelope>(view: string, args: Record<string, string> = {}, signal?: AbortSignal): Promise<T> {
    const query = new URLSearchParams(args).toString();
    const result = await this.request('/api/read/' + view + (query ? '?' + query : ''), { signal });
    if (result.contract !== INTEL_CONTRACT) throw new Error('The ' + view + ' read has an unsupported contract');
    if (!['ok', 'unknown', 'error', 'refused'].includes(text(result.status))) throw new Error('The ' + view + ' read returned no state');
    return result as T;
  }
  rules(signal?: AbortSignal): Promise<IntelRules> {
    return this.intel('ui_rules', {}, signal);
  }
  corrections(filters: IntelFilters = {}, signal?: AbortSignal): Promise<IntelCorrections> {
    return this.intel('ui_corrections', intelArgs(filters), signal);
  }
  correctionDetail(correction: string, signal?: AbortSignal): Promise<IntelCorrectionDetail> {
    if (!correction || correction.length > 200 || /[\p{Cc}]/u.test(correction)) return Promise.reject(new Error('Invalid correction identity'));
    return this.intel('ui_correction_detail', { correction }, signal);
  }
  correctionHistory(filters: IntelFilters = {}, signal?: AbortSignal): Promise<IntelCorrectionHistory> {
    return this.intel('ui_correction_history', intelArgs(filters), signal);
  }
  taskEpisode(task = '', signal?: AbortSignal): Promise<IntelTaskEpisode> {
    if (task.length > 120 || /[\p{Cc}]/u.test(task)) return Promise.reject(new Error('Invalid task identity'));
    return this.intel('ui_task_episode', task ? { task } : {}, signal);
  }
  executionMetrics(perTask = false, limit = 20, signal?: AbortSignal): Promise<IntelExecutionMetrics> {
    return this.intel('ui_execution_metrics', { per_task: String(perTask), limit: String(limit) }, signal);
  }
  adapterDiagnostics(signal?: AbortSignal): Promise<IntelAdapterDiagnostics> {
    return this.intel('ui_adapter_diagnostics', {}, signal);
  }

}
/** The capability the server publishes per tool row; never a client permission. */
export function capabilityOf(tool: Data): Data | null {
  const value = tool.capability;
  return value && typeof value === 'object' && !Array.isArray(value) ? object(value) : null;
}
/** Whether the person must confirm this exact call, by the server's own rule.
 *
 * Older servers publish no capability: the legacy confirm list is the fallback.
 * The confirmation modal is presentational; the POST is still refused without
 * ``confirm: true``, so a stale bundle cannot approve its own action. */
export function toolNeedsConfirm(tool: Data, args: Record<string, unknown>, confirmTools: string[]): boolean {
  const capability = capabilityOf(tool);
  if (!capability) return confirmTools.includes(text(tool.name));
  if (capability.confirmable !== true) return false;
  const name = text(tool.name);
  const action = typeof args.action === 'string' ? args.action.trim().toLowerCase() : '';
  if (name === 'adapter') return action === 'install' || action === 'remove';
  if (name === 'correction_apply') return action === 'adopt';
  if (name === 'sync') return args.dry_run !== true;
  if (name === 'done') return args.skip_verify !== true;
  return capability.confirm === true; // dispatch, verify, and a cmd: scope tick
}
/** Hint-only: whether any call of this tool may need confirmation. */
export function mayNeedConfirm(tool: Data, confirmTools: string[]): boolean {
  const capability = capabilityOf(tool);
  return capability ? capability.confirmable === true : confirmTools.includes(text(tool.name));
}

export function refusal(tool: string, result: Data): string | null {
  if (result.status !== 'ok') return text(result.error, text(result.note, 'The server refused this change'));
  if (tool === 'start' && !result.started && (result.start_error || (!result.task && result.note))) return text(result.start_error, text(result.note, 'The task was not started'));
  if (tool === 'done' && !(typeof result.task_done === 'boolean' ? result.task_done : result.applied === true)) return text(result.note, 'The task was not marked done: verification or another gate did not pass');
  return null;
}