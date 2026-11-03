import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { rows, text, type Data } from './contracts';
import { X } from './icons';
import { InfoIcon, useModalFocus, useOverlayEscape } from './overlays';
import { mayNeedConfirm, toolNeedsConfirm } from './transport';
export function toolArguments(params: Data[], values: Record<string,string|boolean>): Record<string,unknown> {
  const args: Record<string,unknown>={};
  for(const param of params) {
    const name=text(param.name);const value=values[name];
    if(!name||name==='vault')continue;
    if(param.type==='boolean') {args[name]=value===true;continue;}
    const raw=typeof value==='string'?value:'';
    if(!raw.trim()) {if(param.required)throw new Error(`${name} is required`);continue;}
    if(param.type==='integer') {const n=Number(raw);if(!Number.isSafeInteger(n))throw new Error(`${name} must be an integer`);args[name]=n;}
    else if(param.type==='array') {let parsed:unknown;try {parsed=JSON.parse(raw);}catch {throw new Error(`${name} must be a JSON array`);}if(!Array.isArray(parsed))throw new Error(`${name} must be a JSON array`);if(param.items==='string'&&parsed.some(item=>typeof item!=='string'))throw new Error(`${name} must contain only strings`);if(param.items==='integer'&&parsed.some(item=>!Number.isSafeInteger(item)))throw new Error(`${name} must contain only integers`);if(param.items==='boolean'&&parsed.some(item=>typeof item!=='boolean'))throw new Error(`${name} must contain only booleans`);args[name]=parsed;}
    else args[name]=raw;
  }
  return args;
}
const LONG_NAMES = ['content','description','statement','acceptance','rationale','summary','body','claim'];
/* One field per declared parameter, typed from the table exactly as the legacy layer did:
   a checkbox for booleans, a number input for integers, a textarea for the declared long
   string fields, and a text input otherwise. The required cue is text, the hint and the
   form error are linked with aria-describedby, and an empty required field after a failed
   submit is marked aria-invalid. */
/* A parameter's help may be a bare choice list ("todo|in-progress|blocked|done"); a reader gets a sentence, not a pipe expression. */
export const readableHint = (help: string): string => (/^[\w.-]+(?:\|[\w.-]+)+$/.test(help.trim()) ? `One of: ${help.trim().split('|').join(', ')}` : help);
function Field({ param, index, value, error, first, disabled, onChange }: { param: Data; index: number; value: string | boolean; error: string; first: boolean; disabled: boolean; onChange: (value: string | boolean) => void }) {
  const name = text(param.name);
  const id = `tf-${index}`;
  const hintId = `${id}-hint`;
  const required = param.required === true && param.type !== 'boolean';
  const invalid = !!error && required && String(value ?? '').trim() === '';
  const described = [param.help ? hintId : '', invalid ? 'tf-err' : ''].filter(Boolean).join(' ');
  const aria = { 'aria-invalid': invalid || undefined, 'aria-describedby': described || undefined, required: required || undefined, 'aria-required': required || undefined };
  const hint = param.help ? <span className="hint" id={hintId}>{readableHint(text(param.help))}</span> : null;
  if (param.type === 'boolean') return (
    <div className="field" data-tool-field>
      <label className="row" htmlFor={id} style={{ gap: 8 }}><input type="checkbox" className="check" id={id} checked={value === true} disabled={disabled} data-first={first || undefined} onChange={event => onChange(event.target.checked)} {...aria} /> {name}</label>
      {hint}
    </div>
  );
  const type = param.type === 'integer' ? 'number' : 'text';
  const long = param.type === 'string' && LONG_NAMES.includes(name);
  return (
    <div className="field" data-tool-field>
      <label className="label" htmlFor={id}>{name}{param.required ? <span className="req" aria-hidden="true"> *</span> : null}</label>
      {long
        ? <textarea className="textarea" id={id} rows={3} value={String(value ?? '')} disabled={disabled} data-first={first || undefined} onChange={event => onChange(event.target.value)} {...aria} />
        : <input className="input" id={id} type={type} value={String(value ?? '')} autoComplete="off" disabled={disabled} data-first={first || undefined} onChange={event => onChange(event.target.value)} {...aria} />}
      {hint}
    </div>
  );
}
export function ToolForm({ tool, confirmTools, busy, hidden = false, onClose, mutate, onConfirm }: { tool: Data; confirmTools: string[]; busy: boolean; hidden?: boolean; onClose: () => void; mutate: (tool: string, args: Record<string,unknown>, confirm?: boolean) => Promise<Data>; onConfirm: (tool: string, args: Record<string,unknown>, then?: (problem?: string) => void) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement|null>(null);
  const wasHidden = useRef(hidden);
  const [values, setValues] = useState<Record<string,string|boolean>>({});
  const [error, setError] = useState('');
  useOverlayEscape(20, onClose);
  const onKeyDown = useModalFocus(ref);
  const name = text(tool.name);
  const params = rows(tool.params).filter(param => param.name !== 'vault');
  const mayConfirm = mayNeedConfirm(tool, confirmTools);
  useLayoutEffect(() => {
    // WebKit caches a one-line flex basis before wrapped hint layout.
    // Resolve the natural field size after layout, without fixed pixel heights.
    ref.current?.querySelectorAll<HTMLElement>('[data-tool-field]').forEach(field => {
      field.style.minHeight = '';
      void field.getBoundingClientRect();
      field.style.minHeight = 'max-content';
    });
  }, [tool, values, hidden]);
  const firstIndex = Math.max(0, params.findIndex(param => param.type !== 'boolean'));
  useEffect(() => {
    const initial: Record<string,string|boolean> = {};
    for (const param of rows(tool.params)) {
      const key = text(param.name);
      initial[key] = param.type === 'boolean' ? param.default === true : param.default === null || param.default === undefined ? '' : typeof param.default === 'object' ? JSON.stringify(param.default) : String(param.default);
    }
    setValues(initial);
    setError('');
  }, [tool]);
  /* The confirmation is a stacked modal; the form is hidden under it, and cancel
     brings it back with the Run button that opened the confirmation focused. */
  useEffect(() => {
    if (wasHidden.current && !hidden) {
      const target = returnFocus.current?.isConnected ? returnFocus.current : ref.current?.querySelector<HTMLElement>('[data-first]') ?? ref.current;
      target?.focus({ preventScroll: true });
    }
    wasHidden.current = hidden;
  }, [hidden]);
  const submit = () => {
    if (busy) return;
    setError('');
    let args: Record<string,unknown>;
    try { args = toolArguments(params, values); } catch (problem) { setError(problem instanceof Error ? problem.message : String(problem)); return; }
    if (toolNeedsConfirm(tool, args, confirmTools)) { returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; onConfirm(name, args, problem => { if (problem) setError(problem); else onClose(); }); return; }
    mutate(name, args).then(() => onClose(), problem => setError(problem instanceof Error ? problem.message : String(problem)));
  };
  return (
    <>
      <div className="scrim" style={{ zIndex: 70, display: hidden ? 'none' : undefined }} onClick={onClose} />
      <div className="modal-wrap" style={{ zIndex: 71, display: hidden ? 'none' : undefined }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
        <div className="modal" ref={ref} style={{ display: hidden ? 'none' : undefined }} role="dialog" aria-modal="true" aria-labelledby="mt-form" tabIndex={-1} data-focus-scope="form" onKeyDown={onKeyDown}>
          <div className="modal-h">
            <div><h2 id="mt-form">{name}</h2><div className="muted" style={{ fontSize: '12.5px', marginTop: 2 }}>{text(tool.help)}</div></div>
            <button type="button" className="ibtn" aria-label="Close" onClick={onClose}><X size={16} strokeWidth={1.8} /></button>
          </div>
          <form className="modal-b" id="tool-form" autoComplete="off" onSubmit={event => { event.preventDefault(); submit(); }}>
            {params.length ? params.map((param, index) => {
              const key = text(param.name);
              return <Field key={key} param={param} index={index} value={values[key] ?? (param.type === 'boolean' ? false : '')} error={error} first={index === firstIndex} disabled={busy} onChange={value => setValues(current => ({ ...current, [key]: value }))} />;
            }) : <p className="faint">This tool takes no arguments.</p>}
            {mayConfirm ? <div className="alert info"><InfoIcon size={14} /><span>This tool can run commands or change files on your machine. You will be asked to confirm.</span></div> : null}
            {error ? <div className="alert" role="alert" id="tf-err"><span><b>{error}</b></span></div> : null}
          </form>
          <div className="modal-f"><span className="sp" /><button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Cancel</button><button type="button" className="btn btn-primary" disabled={busy} onClick={submit}>{busy ? 'Running...' : 'Run'}</button></div>
        </div>
      </div>
    </>
  );
}
