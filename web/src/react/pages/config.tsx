import { useMemo, useState } from 'react';
import { object, rows, text, type Data, type PageProps } from '../contracts';
import { Badge, Button, ErrorState, Loading } from '../ui';

type Tab = 'profiles' | 'skills' | 'charter' | 'packs' | 'measure';
const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'profiles', label: 'Profiles' },
  { key: 'skills', label: 'Skills' },
  { key: 'charter', label: 'Charter' },
  { key: 'packs', label: 'Packs' },
  { key: 'measure', label: 'Measure' },
];

const num = (v: unknown): string => (typeof v === 'number' ? String(v) : v === null ? 'null' : text(v));
const budgetOf = (doc: Data) => object(doc.fields?.budgets ?? doc.budgets);

export function ConfigSettings({ snapshot, mutate, read, busy, refresh }: PageProps) {
  const [tab, setTab] = useState<Tab>('profiles');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Data | null>(null);
  const actors = rows(snapshot.actors);
  const skills = rows(snapshot.skills);
  const charter = object(snapshot.charter);
  const [sel, setSel] = useState('');

  const run = async (tool: string, args: Record<string, unknown>, confirm = false) => {
    setError(''); setResult(null);
    try {
      const out = await mutate(tool, args, confirm);
      setResult(out);
      if (refresh) await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div data-section="config">
      <header className="ph rail-page-heading"><div>
        <h1>Configuration</h1>
        <p className="faint">Profiles, skills and charter — every save goes through the gateway with the current etag. Budget increases and charter edits are human-only and need the typed phrase.</p>
      </div></header>
      <nav className="set-nav rail-settings-nav" aria-label="Configuration">
        {TABS.map(t => (
          <button key={t.key} type="button" className={tab === t.key ? 'sitem rail-nav-link on' : 'sitem rail-nav-link'} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </nav>
      {error ? <p role="alert" className="offline-bar">{error}</p> : null}
      {result ? <section className="card" style={{ padding: 12, marginTop: 8 }}><b>Applied</b><pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(result, null, 2)}</pre></section> : null}

      {tab === 'profiles' ? (
        <Profiles actors={actors} sel={sel} setSel={setSel} busy={busy} run={run} />
      ) : null}
      {tab === 'skills' ? <Skills skills={skills} sel={sel} setSel={setSel} busy={busy} run={run} /> : null}
      {tab === 'charter' ? <Charter charter={charter} busy={busy} run={run} /> : null}
      {tab === 'packs' ? <Packs busy={busy} run={run} read={read} /> : null}
      {tab === 'measure' ? <Measure busy={busy} run={run} /> : null}
    </div>
  );
}

function Profiles({ actors, sel, setSel, busy, run }: { actors: Data[]; sel: string; setSel: (s: string) => void; busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void }) {
  const doc = actors.find(d => text(d.id) === sel) ?? actors[0];
  const f = object(doc?.fields);
  const b = budgetOf(doc ?? {});
  const [model, setModel] = useState(text(f.model));
  const [effort, setEffort] = useState(text(f.effort));
  const [runner, setRunner] = useState(text(f.runner, 'droid'));
  const [attempts, setAttempts] = useState(num(b.attempts));
  const [seconds, setSeconds] = useState(num(b.seconds));
  const save = () => {
    if (!doc) return;
    const set: string[] = [];
    if (model) set.push(`model=${model}`);
    if (effort) set.push(`effort=${effort}`);
    if (runner) set.push(`runner=${runner}`);
    set.push(`budgets={attempts: ${attempts}, seconds: ${seconds}}`);
    run('update', { slug: text(doc.id), set, expect: text(doc.etag) });
  };
  return (
    <section className="card" style={{ padding: 12, marginTop: 8 }} aria-label="Profiles">
      <label className="label" htmlFor="cfg-profile">Profile</label>
      <select id="cfg-profile" className="input" value={text(doc?.id)} onChange={e => setSel(e.target.value)}>
        {actors.map(a => <option key={text(a.id)} value={text(a.id)}>{text(a.title, text(a.id))}</option>)}
      </select>
      {doc ? <>
        <Field label="model" id="cfg-model" value={model} onChange={setModel} />
        <Field label="effort (low|medium|high or empty)" id="cfg-effort" value={effort} onChange={setEffort} />
        <Field label="runner" id="cfg-runner" value={runner} onChange={setRunner} />
        <Field label="budgets.attempts" id="cfg-att" value={attempts} onChange={setAttempts} />
        <Field label="budgets.seconds" id="cfg-sec" value={seconds} onChange={setSeconds} />
        <Button disabled={busy} onClick={save}>Save profile</Button>
        <p className="faint" style={{ fontSize: 12 }}>Narrowing only: allowed_methods may only shrink. Raising any budget is human-only (typed phrase).</p>
      </> : <p className="faint" style={{ fontSize: 12 }}>No profiles in this store.</p>}
    </section>
  );
}

function Skills({ skills, sel, setSel, busy, run }: { skills: Data[]; sel: string; setSel: (s: string) => void; busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void }) {
  const doc = skills.find(d => text(d.id) === sel) ?? skills[0];
  const f = object(doc?.fields);
  const [desc, setDesc] = useState(text(f.description));
  const [tools, setTools] = useState(Array.isArray(f.allowed_tools) ? (f.allowed_tools as unknown[]).join(',') : '');
  const save = () => {
    if (!doc) return;
    const parsed = tools ? JSON.parse(`[${tools.split(',').map(s => JSON.stringify(s.trim())).join(',')}]`) : [];
    run('update', { slug: text(doc.id), set: [`description=${desc}`, `allowed_tools=${JSON.stringify(parsed)}`], expect: text(doc.etag) });
  };
  return (
    <section className="card" style={{ padding: 12, marginTop: 8 }} aria-label="Skills">
      <label className="label" htmlFor="cfg-skill">Skill</label>
      <select id="cfg-skill" className="input" value={text(doc?.id)} onChange={e => setSel(e.target.value)}>
        {skills.map(s => <option key={text(s.id)} value={text(s.id)}>{text(s.title, text(s.id))}</option>)}
      </select>
      {doc ? <>
        <Field label="description" id="cfg-sdesc" value={desc} onChange={setDesc} long />
        <Field label="allowed_tools (comma list, known ids only)" id="cfg-stools" value={tools} onChange={setTools} />
        <Button disabled={busy} onClick={save}>Save skill</Button>
      </> : <p className="faint" style={{ fontSize: 12 }}>No skills in this store.</p>}
    </section>
  );
}

function Charter({ charter, busy, run }: { charter: Data; busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void }) {
  const f = object(charter.fields);
  const b = object(f.budgets);
  const [item, setItem] = useState(num(object(b.item).attempts));
  const save = () => {
    run('update', { slug: 'charter', set: [`budgets={"item": {"attempts": ${item}}}`], expect: text(charter.etag), confirm: 'APPROVE charter' }, true);
  };
  return (
    <section className="card" style={{ padding: 12, marginTop: 8 }} aria-label="Charter">
      {Object.keys(charter).length ? <>
        <p className="faint" style={{ fontSize: 12 }}>Charter edits are human-only. Saving asks for the phrase <span className="mono">APPROVE charter</span>; semantics moving will report <span className="mono">approval_mismatch</span> until reapproved.</p>
        <Field label="budget item attempts (human-only)" id="cfg-charter-att" value={item} onChange={setItem} />
        <Button disabled={busy} onClick={save}>Save charter</Button>
      </> : <p className="faint" style={{ fontSize: 12 }}>No charter document yet — run <span className="mono">rail init --interview</span> first.</p>}
    </section>
  );
}

function Packs({ busy, run, read }: { busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void; read: (tool: string, args?: Record<string, string>, signal?: AbortSignal) => Promise<Data> }) {
  const [name, setName] = useState('');
  const [out, setOut] = useState<Data | null>(null);
  const show = async (tool: string, args: Record<string, string>) => {
    try { setOut(await read(tool, args)); } catch (e) { setOut({ error: e instanceof Error ? e.message : String(e) }); }
  };
  return (
    <section className="card" style={{ padding: 12, marginTop: 8 }} aria-label="Packs">
      <Field label="pack name" id="cfg-pack" value={name} onChange={setName} />
      <div className="row" style={{ gap: 8 }}>
        <Button disabled={busy || !name} onClick={() => void show('pack show', { name })}>Show</Button>
        <Button disabled={busy || !name} onClick={() => void show('pack validate', { name })}>Validate</Button>
        <Button disabled={busy || !name} onClick={() => run('pack create', { name, from: 'basic' })}>Create draft</Button>
      </div>
      {out ? <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(out, null, 2)}</pre> : null}
    </section>
  );
}

function Measure({ busy, run }: { busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void }) {
  const [target, setTarget] = useState('');
  const [before, setBefore] = useState('1');
  const [after, setAfter] = useState('2');
  return (
    <section className="card" style={{ padding: 12, marginTop: 8 }} aria-label="Measure">
      <Field label="target (profile or skill name)" id="cfg-mtarget" value={target} onChange={setTarget} />
      <Field label="before version" id="cfg-mbefore" value={before} onChange={setBefore} />
      <Field label="after version" id="cfg-mafter" value={after} onChange={setAfter} />
      <div className="row" style={{ gap: 8 }}>
        <Button disabled={busy || !target} onClick={() => run('measure versions', { target_class: 'profile', target, before: Number(before), after: Number(after) })}>Compare versions</Button>
        <Button disabled={busy || !target} onClick={() => run('rollback', { target_class: 'profile', target, version: Number(before) })}>Rollback to before</Button>
      </div>
      <p className="faint" style={{ fontSize: 12 }}>Measurement is observational, never an auto-rollout. Zero recurrences is not proof of resolution.</p>
    </section>
  );
}

function Field({ label, id, value, onChange, long }: { label: string; id: string; value: string; onChange: (v: string) => void; long?: boolean }) {
  return (
    <div className="field" data-tool-field>
      <label className="label" htmlFor={id}>{label}</label>
      {long
        ? <textarea className="textarea" id={id} rows={3} value={value} onChange={e => onChange(e.target.value)} />
        : <input className="input" id={id} type="text" value={value} onChange={e => onChange(e.target.value)} />}
    </div>
  );
}
