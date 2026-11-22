/* The Item sheet: the one place a person sees the gate, the legal moves, the
   declared actions and the relations together. It opens beside the work and
   keeps the canvas's selection. */
import { useState, type FormEvent } from 'react';
import { Check, Circle, CircleDashed, Lock } from '../icons';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { Button, cn } from '../ui';
import { DocumentContent } from '../DocumentContent';
import { StatusGlyph } from '../overlays';
import {
  actionsFor,
  allItems,
  fieldValue,
  gateFor,
  itemsNeedingGate,
  transitionsOf,
  workflowForPreset,
  workflowStatuses,
} from '../capabilities';
import { labelOf, titleCase } from './operating';

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];

const presetOf = (doc: Data): string => text(doc.preset, text(doc.type));

export function ItemSheet({
  id,
  snapshot,
  document,
  openDoc,
  mutate,
  busy = false,
  loading = false,
  error = '',
  retry,
}: {
  id: string;
  snapshot: PageProps['snapshot'];
  document: Data;
  openDoc: (id: string) => void;
  mutate: PageProps['mutate'];
  busy?: boolean;
  loading?: boolean;
  error?: string;
  retry?: () => void;
}) {
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approver, setApprover] = useState('');
  const [words, setWords] = useState('');
  const [approvalError, setApprovalError] = useState('');
  const [approvalNote, setApprovalNote] = useState('');
  const [approving, setApproving] = useState(false);
  const item = allItems(snapshot).find((entry) => text(entry.id) === id);
  const doc = object(document.doc);
  const card = object(document.card);
  const proof = object(card.proof);
  const checks = rows(document.checks);
  const preset = text(card.preset, item ? presetOf(item) : '');
  const workflow = text(card.workflow, workflowForPreset(snapshot, preset));
  const status = text(card.state, item ? text(item.status) : '');
  const title = text(doc.title, item ? text(item.title, id) : id);
  const category = text(item?.category, '');
  const kindLabel = preset ? labelOf(snapshot, preset) : text(doc.type, 'Document');
  const headerKicker = [kindLabel, status ? titleCase(status) : '', category].filter(Boolean).join(' · ');
  const declaredNext = workflow ? transitionsOf(snapshot, workflow, status) : [];
  const next = declaredNext.length ? declaredNext : strings(card.next);
  const owed = itemsNeedingGate(snapshot, preset, status);
  const gateTarget = owed[0] ?? status;
  const gate = workflow ? gateFor(snapshot, workflow, gateTarget) : null;
  const gateChecks = gate ? gate.checks : strings(proof.checks);
  const checkByName = new Map(checks.map((check) => [text(check.id, text(check.item)), check]));
  const openChecks = gateChecks.filter((name) => checkByName.get(name)?.passed !== true);
  const acceptance = proof.acceptance === true;
  const review = proof.review === true;
  const human = proof.human === true;
  const children = proof.children !== undefined && proof.children !== null;
  const claim = rows(object(snapshot).claims).find((entry) => text(entry.task) === id);
  const who = claim ? text(claim.actor, text(claim.holder, 'claimed')) : '';
  const priority = text(item?.priority, text(object(item?.fields).priority));
  const actions = !item
    ? []
    : (() => {
        const known = actionsFor(snapshot, preset, status);
        const named = strings(card.actions);
        return named.length ? known.filter((action) => named.includes(text(action.name))) : known;
      })();
  const relations = Object.entries(object(item?.relations)).filter(([, value]) => Array.isArray(value) && value.length);
  const byId = new Map(allItems(snapshot).map((entry) => [text(entry.id), entry]));

  const all = allItems(snapshot);
  const childIds = all
    .filter((entry) => strings(object(entry.relations).part_of).includes(id))
    .map((entry) => text(entry.id));
  const childrenDone = childIds.every((child) => {
    const childDoc = all.find((entry) => text(entry.id) === child);
    return childDoc ? ['done', 'dropped'].includes(text(childDoc.category)) : false;
  });

  const gateHasRequirements = gateChecks.length > 0 || acceptance || review || human || children;
  const gateOpen = () => openChecks.length > 0 || acceptance || review || human || (children && !childrenDone);
  const reason = openChecks.length
    ? `${openChecks.join(', ')} open`
    : acceptance
      ? 'owner sign-off required'
      : review
        ? 'review required'
        : human
          ? 'human approval required'
          : children && !childrenDone
            ? 'children are still open'
            : '';

  const unboundPlaceholders = (action: Data): string[] => {
    const body = JSON.stringify(object(action.args));
    const names = [...new Set([...body.matchAll(/\{([A-Za-z0-9_.-]+)\}/g)].map((match) => match[1]))];
    return names.filter((name) => !['item', 'etag', 'parent'].includes(name));
  };

  const blockedAction = (action: Data): string => {
    const unbound = unboundPlaceholders(action);
    if (unbound.length) return `needs input: ${unbound.join(', ')}`;
    const requires = object(action.requires);
    if (requires.children === true && !childrenDone) return 'children are still open';
    if (requires.review === true) return 'review must be recorded';
    if (text(requires.check)) return `check ${text(requires.check)} must pass`;
    if (text(requires.field)) return `field ${text(requires.field)} must be set`;
    return '';
  };

  const consentAction = actions.find((action) => object(action.requires).consent === true) ?? null;
  const consent = object(document.consent);
  const approvalBlocked = consentAction ? blockedAction(consentAction) : '';

  const recordApproval = async (event: FormEvent) => {
    event.preventDefault();
    if (!consentAction || approving) return;
    const name = approver.trim();
    const said = words.trim();
    if (!name) {
      setApprovalError('Name the person giving the approval.');
      return;
    }
    if (!said) {
      setApprovalError('Type what that person actually said.');
      return;
    }
    const etag = text(doc.etag);
    if (!etag) {
      setApprovalError('The item has no etag; reload it before approving.');
      return;
    }
    setApproving(true);
    setApprovalError('');
    setApprovalNote('');
    try {
      await mutate('consent', { item: id, on_behalf_of: name, text: said, expect: etag });
      await mutate('do', { action: text(consentAction.name), item: id, etag });
      setApprovalOpen(false);
      setWords('');
      setApprovalNote('Approval recorded and the workflow moved.');
    } catch (problem) {
      setApprovalError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setApproving(false);
    }
  };

  return (
    <div className="ri-sheet">
      <header className="ri-head">
        <div className="rf-kicker">{headerKicker}</div>
        <h1 id="d-h">{title}</h1>
        <div className="ri-chips">
          {status ? <span className="rf-chip rf-chip-status">{titleCase(status)}</span> : null}
          {owed.length ? <span className="rf-chip rf-chip-gate">gate at {owed.join(', ')}</span> : null}
          {priority ? <span className="rf-chip">{titleCase(priority)}</span> : null}
          {who ? <span className="rf-chip rf-chip-owner"><Lock size={10} /> {who}</span> : null}
          {item ? <span className="rf-chip rf-chip-muted">{text(item.id)}</span> : <span className="rf-chip rf-chip-muted">{text(doc.type, 'document')}</span>}
        </div>
      </header>

      {gate && gateHasRequirements ? (
        <section className="ri-section">
          <h3>Gate · {gate.gate}{gateTarget !== status ? ` at ${titleCase(gateTarget)}` : ''}</h3>
          <div className="ri-checks">
            {gateChecks.length ? gateChecks.map((name) => {
              const check = checkByName.get(name);
              const passed = check?.passed === true;
              return (
                <div className="ri-check" key={name}>
                  <span className={cn('ri-check-ic', passed ? 'ok' : check ? 'no' : 'unknown')}>
                    {passed ? <Check size={13} /> : <Circle size={13} />}
                  </span>
                  <span className="grow">{name}</span>
                  <span className="faint">{passed ? 'passed' : check ? 'open' : 'declared'}</span>
                </div>
              );
            }) : <div className="ri-check"><span className="ri-check-ic na">–</span><span className="grow">No checks declared</span></div>}
          </div>
          {acceptance ? <div className="ri-require"><CircleDashed size={13} /> owner sign-off required</div> : null}
          {review ? <div className="ri-require"><CircleDashed size={13} /> a review is required</div> : null}
          {human ? <div className="ri-require"><CircleDashed size={13} /> human approval required</div> : null}
          {children ? <div className="ri-require"><CircleDashed size={13} /> every child must finish first</div> : null}
        </section>
      ) : null}

      {item ? <section className="ri-section">
        <div className="ri-section-head">
          <h3>Workflow</h3>
          <span className="rf-chip rf-chip-muted">agent-driven</span>
        </div>
        {next.length ? (
          <div className="ri-next">
            <span className="ri-next-label">Next</span>
            {next.map((target) => {
              const targetGate = workflow ? gateFor(snapshot, workflow, target) : null;
              return (
                <span className="ri-next-target" key={target}>
                  {titleCase(target)}
                  {targetGate ? <span className="ri-next-gate">{targetGate.gate}</span> : null}
                </span>
              );
            })}
          </div>
        ) : actions.length ? (
          <p className="ri-muted">No transition is declared from {titleCase(status)}.</p>
        ) : (
          <p className="ri-muted">This state has no declared move.</p>
        )}
        {next.length ? (
          <details className="ri-details">
            <summary>Workflow details</summary>
            {next.map((target) => {
              const targetGate = workflow ? gateFor(snapshot, workflow, target) : null;
              const targetOwed = itemsNeedingGate(snapshot, preset, status).includes(target);
              const blocked = targetOwed && gateOpen();
              return (
                <div className="ri-action" key={target}>
                  <span className="grow">
                    Move to {titleCase(target)}
                    {blocked ? <span className="ri-reason">{reason || 'gate is open'}</span> : null}
                  </span>
                  {targetGate ? <span className="rf-chip rf-chip-gate">{targetGate.gate}</span> : null}
                </div>
              );
            })}
            <p className="ri-agent-note">The sheet reports the compiled moves and actions. Work moves stay agent-driven; an action that requires consent can be approved here.</p>
          </details>
        ) : null}
        {actions.map((action) => {
          const reasonFor = blockedAction(action);
          return (
            <div className="ri-action" key={text(action.name)}>
              <span className="grow">
                {text(action.name)}
                <span className="ri-reason">{reasonFor || text(action.use_when)}</span>
              </span>
            </div>
          );
        })}
        {consentAction || consent.record ? (
          <div className="ri-approval-box">
            <h4>{consentAction ? 'Needs your approval' : 'Approval recorded'}</h4>
            {consent.record ? (
              <div className="ri-consent">
                <p>“{text(consent.text)}”</p>
                <span className="ri-consent-meta">
                  {text(consent.on_behalf_of, 'A human')} · {text(consent.at)}
                  {' · recorded by '}{text(consent.signer)} ({text(consent.signer_kind)})
                  {consent.current === false ? ' · a later edit needs a new approval' : ''}
                </span>
              </div>
            ) : null}
            {consentAction ? approvalOpen ? (
              <form className="ri-approval-form" onSubmit={recordApproval}>
                <label>
                  Your name
                  <input value={approver} onChange={(event) => setApprover(event.target.value)} autoComplete="name" required />
                </label>
                <label>
                  What you said
                  <textarea rows={3} value={words} onChange={(event) => setWords(event.target.value)} placeholder="Type the words you actually said, not a summary." required />
                </label>
                {approvalBlocked ? <p className="ri-reason">{approvalBlocked}</p> : null}
                {approvalError ? <p role="alert" className="ri-approve-error">{approvalError}</p> : null}
                <div className="ri-approval-actions">
                  <Button type="submit" variant="primary" disabled={busy || approving || Boolean(approvalBlocked)}>
                    {approving ? 'Recording…' : 'Approve and start'}
                  </Button>
                  <Button variant="ghost" onClick={() => { setApprovalOpen(false); setApprovalError(''); }}>Cancel</Button>
                </div>
              </form>
            ) : (
              <div className="ri-approval-actions">
                <p className="ri-muted">The workflow waits for your words before it can start.</p>
                <Button variant="primary" disabled={busy} onClick={() => { setApprovalOpen(true); setApprovalNote(''); }}>
                  Approve this change
                </Button>
              </div>
            ) : null}
            {approvalNote ? <p role="status" className="ri-approve-note">{approvalNote}</p> : null}
          </div>
        ) : null}
      </section> : null}

      {relations.length ? (
        <section className="ri-section">
          <h3>Relations</h3>
          {relations.map(([rel, values]) => (
            <div className="ri-relation" key={rel}>
              <b>{rel}</b>
              <span>
                {strings(values).map((target, index) => (
                  <button type="button" key={target} className="ri-link" title={target} onClick={() => openDoc(target)}>
                    {text(byId.get(target)?.title, target)}{index < strings(values).length - 1 ? ', ' : ''}
                  </button>
                ))}
              </span>
            </div>
          ))}
        </section>
      ) : null}

      {item && Object.keys(object(item.fields)).length ? (
        <section className="ri-section">
          <h3>Fields</h3>
          <div className="ri-fields">
            {Object.entries(object(item.fields)).map(([key, value]) => (
              <div className="ri-field" key={key}>
                <span>{key}</span>
                <b>{fieldValue(item, key) || text(value)}</b>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="ri-section ri-body">
        <DocumentContent id={id} data={document} loading={loading} error={error} retry={retry} openDoc={openDoc} />
      </section>

      {item ? <footer className="ri-foot">
        <StatusGlyph status={status} statuses={workflow ? workflowStatuses(snapshot, workflow) : []} category={category} size={12} />
        <span>{workflow ? `workflow ${workflow}` : 'no workflow'}</span>
        <span className="sp" />
        <span className="faint">Agents do the work. Approvals are recorded here.</span>
      </footer> : null}
    </div>
  );
}
