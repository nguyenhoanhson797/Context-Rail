/* Health is a screen about the store, not another board. Doctor findings are
   facts with a severity and a remedy; the digests are the approval contract, so
   a reader can see which change would need reapproval. Nothing here resolves a
   finding by hand: the remedy is shown, and the store stays the truth. */
import { useMemo, useState } from 'react';
import { Check, CircleCheck, FileText, GitBranch, Lock, RotateCcw, ShieldAlert, ShieldCheck, Terminal, TriangleAlert } from '../icons';
import { object, rows, text, type PageProps } from '../contracts';
import { cn } from '../ui';
import { titleCase } from './operating';

const SEVERITIES = ['error', 'warning', 'info'] as const;

const SEVERITY_TONE: Record<string, string> = { error: 'bad', warning: 'warn', info: 'info' };

export function HealthPage({ snapshot, health, healthError, openDoc, busy, refresh }: PageProps) {
  const snap = object(snapshot);
  const digests = object(snap.digests);
  const project = object(snap.project);
  const charter = object(snap.charter);
  const execution = object(object(snap.operating).execution);
  const actions = Array.isArray(execution.actions) ? execution.actions.filter((a): a is string => typeof a === 'string') : [];
  const doctor = object(health?.doctor);
  const issues = useMemo(() => rows(doctor.issues), [doctor]);
  const bySeverity = object(doctor.by_severity);
  const state = health ? text(health.state, 'unknown') : 'unknown';
  const [severity, setSeverity] = useState('all');
  const [copied, setCopied] = useState('');
  const [reveal, setReveal] = useState('');

  const tallies = useMemo(() => {
    const out: Record<string, number> = { error: 0, warning: 0, info: 0 };
    for (const issue of issues) {
      const name = text(issue.severity, 'info');
      out[name] = (out[name] ?? 0) + 1;
    }
    for (const name of SEVERITIES) {
      if (bySeverity[name] !== undefined) out[name] = Number(bySeverity[name]) || 0;
    }
    return out;
  }, [issues, bySeverity]);

  const totalFindings = doctor.total !== undefined ? Number(doctor.total) || 0 : issues.length;
  const shown = severity === 'all' ? issues : issues.filter((issue) => text(issue.severity, 'info') === severity);
  const approval = text(charter.state, text(project.state, 'unknown'));
  const projectDoc = text(project.goal_slug, text(charter.id));
  const asOf = text(health?.as_of);
  const apiError = text(health?.error);
  const apiErrorCode = text(health?.error_code);
  const failureMessage = healthError || apiError;
  const reasons = Array.isArray(health?.reasons) ? health!.reasons.filter((r): r is string => typeof r === 'string') : [];

  const tone = state === 'ok' && !tallies.error ? 'ok' : tallies.error || state === 'error' || Boolean(healthError) ? 'bad' : 'warn';

  return (
    <div className="rh-page">
      <header className="rh-head">
        <div>
          <div className="rf-kicker">{`store schema ${text(health?.schema, text(snap.schema))} · ${state === 'ok' ? `${totalFindings} ${totalFindings === 1 ? 'finding' : 'findings'}` : `health ${state}`}`}</div>
          <h1>Health</h1>
          <p>Facts about the store and the approval contract. A finding is never a work item, and a remedy is never applied silently.</p>
        </div>
        <div className="rh-head-side">
          <button type="button" className="rf-chip" disabled={busy || !refresh} onClick={() => void refresh?.()}>
            <RotateCcw size={13} aria-hidden="true" /> {busy ? 'Reading…' : 'Re-run'}
          </button>
        </div>
      </header>

      <div className={cn('rh-banner', `tone-${tone}`)} role="status">
        <span className="rh-banner-icon" aria-hidden="true">
          {tone === 'ok' ? <ShieldCheck size={18} /> : tone === 'bad' ? <ShieldAlert size={18} /> : <TriangleAlert size={18} />}
        </span>
        <div className="rh-banner-main">
          <b>
            {tone === 'ok'
              ? 'The store is consistent'
              : state === 'ok' && tallies.error
                ? `${tallies.error} ${tallies.error === 1 ? 'error needs' : 'errors need'} attention`
                : apiErrorCode
                  ? `Health check failed (${apiErrorCode})`
                  : 'Health could not be fully read'}
          </b>
          <span>
            {failureMessage
              ? failureMessage
              : asOf
                ? `Read as of ${new Date(asOf).toLocaleString()}`
                : 'No dated health read has arrived yet.'}
          </span>
          {reasons.length ? <span className="rh-reasons">{reasons.join(' · ')}</span> : null}
        </div>
        <div className="rh-sevs">
          {SEVERITIES.map((name) => (
            <button
              type="button"
              key={name}
              className={cn('rh-sev', `tone-${SEVERITY_TONE[name]}`, severity === name && 'on')}
              aria-pressed={severity === name}
              onClick={() => setSeverity(severity === name ? 'all' : name)}
            >
              <i aria-hidden="true" /> {name} <b>{state === 'ok' ? (tallies[name] ?? 0) : '—'}</b>
            </button>
          ))}
        </div>
      </div>

      <div className="rh-grid">
        <section className="rh-card wide">
          <div className="rh-card-head">
            <h2>Doctor findings</h2>
            <span className="rh-hint">{state !== 'ok' ? state : severity === 'all' ? `${totalFindings} total` : `${shown.length} ${severity}`}</span>
            {severity !== 'all' ? (
              <button type="button" className="rf-chip" onClick={() => setSeverity('all')}>Show all</button>
            ) : null}
          </div>
          {state !== 'ok' ? (
            <div className="rh-clean">
              <TriangleAlert size={16} aria-hidden="true" />
              <div>
                <b>{state === 'error' || Boolean(failureMessage) ? (apiErrorCode || 'Health check failed') : 'Checking health…'}</b>
                <span>{failureMessage || reasons[0] || 'Diagnostics have not completed yet; findings are unknown.'}</span>
              </div>
            </div>
          ) : !totalFindings && !issues.length ? (
            <div className="rh-clean">
              <CircleCheck size={16} aria-hidden="true" />
              <div>
                <b>No findings</b>
                <span>The store passed every check this build runs.</span>
              </div>
            </div>
          ) : !shown.length ? (
            <div className="rh-clean">
              <Check size={16} aria-hidden="true" />
              <div>
                <b>{`No ${severity} findings`}</b>
                <span>Switch severity to see the rest.</span>
              </div>
            </div>
          ) : (
            <ul className="rh-findings">
              {shown.map((issue, index) => {
                const level = text(issue.severity, 'info');
                const path = text(issue.path);
                const code = text(issue.code);
                const fix = text(issue.fix);
                return (
                  <li className={cn('rh-finding', `tone-${SEVERITY_TONE[level] ?? 'info'}`)} key={`${code}-${path}-${index}`}>
                    <span className="rh-finding-dot" aria-hidden="true" />
                    <div className="rh-finding-main">
                      <div className="rh-finding-line">
                        <b>{text(issue.message, code)}</b>
                        {code ? <code className="rh-code">{code}</code> : null}
                      </div>
                      <div className="rh-finding-meta">
                        <span className="rh-level">{level}</span>
                        {path ? <span className="rh-file trunc" title={path}>{path}</span> : null}
                      </div>
                      {fix ? (
                        <div className="rh-fix">
                          <Terminal size={11} aria-hidden="true" />
                          <span>{fix}</span>
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="rh-card">
          <div className="rh-card-head">
            <h2><Lock size={13} aria-hidden="true" /> Approval</h2>
          </div>
          <div className={cn('rh-approval', approval === 'approved' ? 'ok' : 'warn')}>
            <b>{approval === 'approved' ? 'Charter approved' : approval === 'missing' ? 'Not approved yet' : titleCase(approval)}</b>
            <span>
              {approval === 'approved'
                ? 'Execution is unlocked. A semantic change still needs reapproval.'
                : 'Nothing executes until the picture is approved.'}
            </span>
          </div>
          <dl className="rh-digests">
            {(['semantic', 'presentation', 'full'] as const).map((name) => {
              const value = text(digests[name]);
              return (
                <div className="rh-digest" key={name}>
                  <dt>{name}</dt>
                  <dd>
                    <code title={value}>{value ? (reveal === name ? value : `${value.slice(0, 16)}…`) : '—'}</code>
                    {value ? (
                      <button
                        type="button"
                        className="rh-copy"
                        aria-label={`Copy the ${name} digest`}
                        onClick={() => void copy(name, value)}
                      >
                        {copied === name ? <Check size={11} aria-hidden="true" /> : <FileText size={11} aria-hidden="true" />}
                        {copied === name ? 'Copied' : reveal === name ? 'Select it' : 'Copy'}
                      </button>
                    ) : null}
                  </dd>
                </div>
              );
            })}
          </dl>
          <p className="rh-note">A label or card change moves the presentation digest only. An operating, transition, preset or placement change moves the semantic digest and needs reapproval.</p>
        </section>

        <section className="rh-card">
          <div className="rh-card-head">
            <h2><GitBranch size={13} aria-hidden="true" /> Execution</h2>
          </div>
          {actions.length ? (
            <div className="rh-actions">
              {actions.map((action) => (
                <span className="rq-action" key={action}>{action}</span>
              ))}
            </div>
          ) : (
            <p className="rh-empty">This pack declares no execution actions. Workflow completion and execution progress stay separate.</p>
          )}
          <p className="rh-note">Execution progress comes from execution records only and is never merged into workflow completion.</p>
        </section>

        <section className="rh-card wide">
          <div className="rh-card-head">
            <h2>Where this store lives</h2>
          </div>
          <dl className="rh-digests">
            <div className="rh-digest"><dt>project</dt><dd><code>{text(project.name, '—')}</code></dd></div>
            <div className="rh-digest"><dt>pack</dt><dd><code>{text(project.pack, text(snap.pack))}</code></dd></div>
            <div className="rh-digest"><dt>store root</dt><dd><code className="trunc" title={text(project.store_root)}>{text(project.store_root, '—')}</code></dd></div>
          </dl>
          <div className="rh-links">
            <button type="button" className="rf-chip" onClick={() => openDoc(projectDoc)} disabled={!projectDoc}>
              <FileText size={12} aria-hidden="true" /> Open the project doc
            </button>
          </div>
        </section>
      </div>
    </div>
  );

  async function copy(name: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      flash(name);
      return;
    } catch {
      /* The async clipboard needs a focused document, so a background tab
         refuses it. Fall back, and if that fails too, show the whole digest
         rather than leaving the button looking like it did nothing. */
    }
    const field = document.createElement('textarea');
    field.value = value;
    field.setAttribute('readonly', '');
    field.style.cssText = 'position:fixed;left:-9999px;top:0;';
    document.body.appendChild(field);
    field.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    field.remove();
    if (ok) flash(name);
    else {
      setCopied('');
      setReveal(name);
    }
  }

  function flash(name: string) {
    setReveal('');
    setCopied(name);
    setTimeout(() => setCopied(''), 1600);
  }
}
