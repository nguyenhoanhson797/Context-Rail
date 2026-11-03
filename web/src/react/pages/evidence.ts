/* =====================================================================
   OWNER EVIDENCE (pure): ONE status vocabulary for the owner-facing views,
   and the only place product-evidence and conditional-gate claims are read.

   The dimensions stay separate - task execution is not verification, and
   verification is not live evaluation. Nothing here computes a score: the
   scope-score math is the server's and this module never changes it.

   Sourcing rule (SC-57 / UI-05): a claim is rendered only when a record
   carries it. A gate is a row an authored/evidence-backed model returned
   (never a slug test or prose pattern), and "Implementation verified; live
   evaluation pending" needs a record that says both, with a source. Absent
   records stay unknown - never zero, clean, open or proven.
   ===================================================================== */
import { object, text, type Data, type PageProps } from '../contracts';
import { RAIL_KEY } from '../refresh';

export type EvidenceWord =
  | 'proven' | 'checked' | 'stale' | 'failed' | 'unrun' | 'attention'
  | 'not_assessed' | 'unknown';

export type EvidenceTone = 'green' | 'amber' | 'red' | 'neutral';

export interface EvidenceTerm { word: string; title: string; tone: EvidenceTone }

/* The vocabulary. The lower-case word is the shared label; the title is the
   one-line explanation every surface may show instead of inventing its own. */
export const EVIDENCE: Record<EvidenceWord, EvidenceTerm> = {
  proven: { word: 'proven', title: 'Proven - a check ran and its evidence is fresh', tone: 'green' },
  checked: { word: 'checked', title: 'Checked - a tick is recorded, the proof is not fresh', tone: 'amber' },
  stale: { word: 'stale', title: 'Stale - code changed since the run', tone: 'amber' },
  failed: { word: 'failed', title: 'Failed - the check ran and did not pass', tone: 'red' },
  unrun: { word: 'never run', title: 'Never run - there is no result to read', tone: 'neutral' },
  attention: { word: 'attention', title: 'Needs attention - a false pass, an asserted tick or no task', tone: 'red' },
  not_assessed: { word: 'not assessed', title: 'Not assessed - no usable denominator or no read', tone: 'neutral' },
  unknown: { word: 'unknown', title: 'Unknown - the source did not say', tone: 'neutral' },
};

export const evidenceWord = (word: EvidenceWord): string => EVIDENCE[word].word;
export const evidenceTone = (word: EvidenceWord): EvidenceTone => EVIDENCE[word].tone;
/* The heading form of the same word: "Proven not assessed", never a second table. */
export const evidenceTitle = (word: EvidenceWord): string => {
  const value = evidenceWord(word);
  return value.charAt(0).toUpperCase() + value.slice(1);
};

/* The Atlas proof classes (the server's) map onto the same words. The Atlas
   keeps its capitalised chips, but the words themselves come from here. */
export const PROOF_NAMES: Record<string, string> = {
  proven: 'Proven', stale: 'Stale', failed: 'Failed last run', unrun: 'Never run', attention: 'Needs attention',
};
export const PROOF_LABELS: Record<string, string> = {
  proven: 'Proven — fresh evidence',
  stale: EVIDENCE.stale.title,
  failed: 'Failed on its last run',
  unrun: EVIDENCE.unrun.title,
  attention: EVIDENCE.attention.title,
};
export const PROOF_SHORTS: Record<string, string> = {
  proven: EVIDENCE.proven.word, stale: EVIDENCE.stale.word, failed: EVIDENCE.failed.word,
  unrun: EVIDENCE.unrun.word, attention: EVIDENCE.attention.word,
};

/* ---------- counts: a count exists only when the read returned a number ---------- */
export function countWord(value: unknown, one: string, many = one + 's'): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return EVIDENCE.unknown.word;
  return value + ' ' + (value === 1 ? one : many);
}

/* ---------- the owner summary read: counts, unknown said out loud ----------
   ui_owner_summary returns the two counts the Summary shows. A null count is
   unknown with the read's own reason; it is never rendered as 0. */
export interface OwnerSummary {
  state: 'ok' | 'unknown' | 'error' | 'refused';
  reason: string;
  corrections: number | null;
  correctionsReason: string | null;
  closeout: number | null;
  closeoutReason: string | null;
  closeoutTotal: number | null;
  closeoutUnknown: number | null;
  closeoutUnknownReason: string | null;
  closeoutTruncated: boolean;
  /* The same read owns the two authored claims: the Summary and the Atlas Map
     render them from here, never from a second synthetic injection. */
  product: ProductEvidence | null;
  productReason: string;
  gates: GateRow[];
}

const finiteCount = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

const firstReason = (value: unknown): string => {
  const reason = Array.isArray(value) ? value.map((item) => text(item)).filter(Boolean)[0] : '';
  return reason || '';
};

export function ownerSummaryOf(body: unknown): OwnerSummary {
  const env = object(body);
  const stated = text(env.status, 'unknown');
  const state: OwnerSummary['state'] = ['ok', 'unknown', 'error', 'refused'].includes(stated)
    ? (stated as OwnerSummary['state']) : 'unknown';
  const data = object(env.data);
  const needs = object(data.needs_review);
  const closeout = object(data.required_closeout);
  const product = object(data.product_evidence);
  const parsedProduct = evidenceOf(product);
  const productSource = text(product.source) || null;
  const reason = firstReason(env.reasons);
  return {
    state,
    reason,
    corrections: state === 'ok' ? finiteCount(needs.corrections) : null,
    correctionsReason: state === 'ok'
      ? (firstReason(env.reasons) || text(needs.note) || 'the read did not report a count')
      : (reason || 'the read did not report a count'),
    closeout: state === 'ok' ? finiteCount(closeout.count) : null,
    closeoutReason: state === 'ok'
      ? (text(closeout.reason) || 'the read did not report a count')
      : (reason || 'the read did not report a count'),
    closeoutTotal: state === 'ok' ? finiteCount(closeout.total) : null,
    closeoutUnknown: state === 'ok' ? finiteCount(closeout.unknown_count) : null,
    closeoutUnknownReason: text(closeout.unknown_reason) || null,
    closeoutTruncated: state === 'ok' && closeout.truncated === true,
    product: state === 'ok' && text(product.state) === 'recorded' && parsedProduct && productSource
      ? parsedProduct : null,
    productReason: state === 'ok'
      ? (text(product.reason) || (productSource ? 'the read says no usable product evidence' : 'no authored product_evidence record'))
      : (reason || 'the read did not report product evidence'),
    gates: state === 'ok' ? gatesOf(data.gates) : [],
  };
}

/* One key and one fetch for the read, keyed by project AND the read's limit:
   refreshProject invalidates [RAIL_KEY, project, ...], so a project switch or a
   post-write refresh reaches this query instead of surviving it. */
export const ownerSummaryKey = (project: string, limit = 1) =>
  [RAIL_KEY, project, 'owner-summary', String(limit)] as const;

export async function fetchOwnerSummary(
  request: PageProps['request'], limit = 1, signal?: AbortSignal,
): Promise<Data> {
  const path = '/api/read/ui_owner_summary?limit=' + encodeURIComponent(String(limit));
  const body = await (request as unknown as (value: string, init?: RequestInit) => Promise<Data>)(
    path, signal ? { signal } : undefined,
  );
  if (text(body.contract) !== 'ui-intel-v1') throw new Error('the owner summary read has an unsupported contract');
  return body;
}

/* ---------- product evidence: implementation and live evaluation, separately ---------- */
export type Implementation = 'verified' | 'failed' | 'unknown';
export type LiveEvaluation = 'pending' | 'measured' | 'unknown';

export interface ProductEvidence {
  implementation: Implementation;
  live: LiveEvaluation;
  source: string | null;
}

const pick = <T extends string>(value: unknown, allowed: readonly T[]): T | 'unknown' => {
  const word = text(value);
  return (allowed as readonly string[]).includes(word) ? (word as T) : 'unknown';
};

/* Parse only an explicit evidence record. No record means null (nothing to say);
   a record with missing or unrecognised values stays unknown, never verified. */
export function evidenceOf(value: unknown): ProductEvidence | null {
  const record: Data = object(value);
  if (!Object.keys(record).length) return null;
  return {
    implementation: pick(record.implementation, ['verified', 'failed'] as const),
    live: pick(record.live_evaluation, ['pending', 'measured'] as const),
    source: text(record.source) || null,
  };
}

/* The sentence, only where a source says it. "Implementation verified; live
   evaluation pending" is never inferred from a passed check alone. */
export function evidenceSentence(evidence: ProductEvidence | null): string | null {
  if (!evidence || !evidence.source) return null;
  if (evidence.implementation === 'failed') return 'Implementation failed';
  if (evidence.implementation !== 'verified') return null;
  if (evidence.live === 'pending') return 'Implementation verified; live evaluation pending';
  if (evidence.live === 'measured') return 'Implementation verified; live evaluation measured';
  return 'Implementation verified; live evaluation unknown';
}

/* ---------- conditional gates: rows from the authored model, never slug matching ---------- */
export type GateState = 'open' | 'closed' | 'unknown';
export interface GateRow { id: string; title: string; state: GateState; source: string | null }

const GATE_STATES: readonly GateState[] = ['open', 'closed'];

/* A gate is a row the read returned. No id/slug special case: the state comes
   from the row, and an unrecognised state or a missing source is unknown. */
export function gatesOf(value: unknown): GateRow[] {
  const list = Array.isArray(value) ? value : [];
  return list.flatMap((item) => {
    const row = object(item);
    const id = text(row.id);
    if (!id) return [];
    const source = text(row.source) || null;
    const stated = text(row.state) as GateState;
    return [{
      id,
      title: text(row.title) || id,
      state: source && GATE_STATES.includes(stated) ? stated : 'unknown',
      source,
    }];
  });
}

export function gateSentence(gate: GateRow): string {
  const label = gate.state === 'unknown' ? EVIDENCE.unknown.word : gate.state;
  return 'Conditional gate ' + label + ': ' + gate.title + (gate.source ? ' (' + gate.source + ')' : ' - no source, so unknown');
}
