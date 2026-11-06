/* Configuration: profiles, skills, charter, packs and measurement.
 * One Settings sibling page: same nav/section/row idioms as Appearance and
 * Keyboard shortcuts. Every save goes through the gateway with the current
 * etag — no direct files, no second write path. */
import { useState } from 'react';
import { rows, object, text, type Data, type PageProps } from '../contracts';
import { Keyboard, Layers, ListChecks, Palette, ShieldCheck, Users } from '../icons';
import { Button, PageHeading, Panel, cn } from '../ui';

type Tab = 'profiles' | 'skills' | 'charter' | 'packs' | 'measure';
const TABS: Array<{ key: Tab; label: string; icon: typeof Palette }> = [
  { key: 'profiles', label: 'Profiles', icon: Users },
  { key: 'skills', label: 'Skills', icon: ListChecks },
  { key: 'charter', label: 'Charter', icon: ShieldCheck },
  { key: 'packs', label: 'Packs', icon: Layers },
  { key: 'measure', label: 'Measure', icon: Keyboard },
];

const num = (v: unknown): string => (typeof v === 'number' ? String(v) : v === null || v === undefined ? '' : text(v));
const budgetOf = (doc: Data) => object(object(doc.fields).budgets);

export function ConfigSettings({ snapshot, mutate, read, busy, refresh }: PageProps) {
  const [tab, setTab] = useState<Tab>('profiles');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Data | null>(null);
  const actors = rows(snapshot.actors);
  const skills = rows(snapshot.skills);
  const charter = object(snapshot.charter);

  const run = async (tool: string, args: Record<string, unknown>, confirm = false) => {
    setError('');
    setResult(null);
    try {
      const out = await mutate(tool, args, confirm);
      setResult(out);
      if (refresh) await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const nav = (key: Tab, label: string, Icon: typeof Palette) => (
    <button
      type="button"
      className={cn('sitem rail-nav-link', tab === key && 'on')}
      aria-current={tab === key ? 'page' : undefined}
      onClick={() => setTab(key)}
    >
      <Icon className="i" size={15} strokeWidth={1.8} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );

  return (
    <>
      <PageHeading
        title="Configuration"
        description="Profiles, skills and charter. Every save goes through the gateway with the current etag; budget increases and charter edits are human-only."
      />
      <nav className="set-nav rail-settings-nav" aria-label="Configuration" style={{ marginBottom: 12 }}>
        {TABS.map(t => nav(t.key, t.label, t.icon))}
      </nav>
      {error ? <p role="alert" className="offline-bar">{error}</p> : null}
      {result ? (
        <section className="sblock">
          <h2>Last change</h2>
          <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>{JSON.stringify(result, null, 2)}</pre>
        </section>
      ) : null}
      {tab === 'profiles' ? <Profiles actors={actors} busy={busy} run={run} /> : null}
      {tab === 'skills' ? <Skills skills={skills} busy={busy} run={run} /> : null}
      {tab === 'charter' ? <Charter charter={charter} busy={busy} run={run} /> : null}
      {tab === 'packs' ? <Packs busy={busy} run={run} read={read} /> : null}
      {tab === 'measure' ? <Measure busy={busy} run={run} /> : null}
    </>
  );
}

function Profiles({ actors, busy, run }: { actors: Data[]; busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void }) {
  const [sel, setSel] = useState('');
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
    set.push(`budgets={ attempts: ${attempts || 'null'}, seconds: ${seconds || 'null'} }`);
    run('update', { slug: text(doc.id), set, expect: text(doc.etag) });
  };
  return (
    <section className="sblock">
      <h2>Profiles</h2>
      <div className="field" data-tool-field>
        <label className="label" htmlFor="cfg-profile">Profile</label>
        <select id="cfg-profile" className="input" value={text(doc?.id)} onChange={e => setSel(e.target.value)}>
          {actors.map(a => <option key={text(a.id)} value={text(a.id)}>{text(a.title, text(a.id))}</option>)}
        </select>
      </div>
      {doc ? <>
        <Fields items={[
          ['model', model, setModel],
          ['effort (low · medium · high, or empty)', effort, setEffort],
          ['runner', runner, setRunner],
          ['budgets.attempts', attempts, setAttempts],
          ['budgets.seconds', seconds, setSeconds],
        ]} />
        <div className="srow rail-settings-item">
          <div>
            <div className="t rail-settings-label">Save changes</div>
            <div className="d rail-list-sub">allowed_methods may only narrow; budget increases are refused for the CTO.</div>
          </div>
          <div className="row"><Button disabled={busy} onClick={save}>Save profile</Button></div>
        </div>
      </> : <p className="faint" style={{ fontSize: 12 }}>No profiles in this store yet.</p>}
    </section>
  );
}

function Skills({ skills, busy, run }: { skills: Data[]; busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void }) {
  const [sel, setSel] = useState('');
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
    <section className="sblock">
      <h2>Skills</h2>
      <div className="field" data-tool-field>
        <label className="label" htmlFor="cfg-skill">Skill</label>
        <select id="cfg-skill" className="input" value={text(doc?.id)} onChange={e => setSel(e.target.value)}>
          {skills.map(s => <option key={text(s.id)} value={text(s.id)}>{text(s.title, text(s.id))}</option>)}
        </select>
      </div>
      {doc ? <>
        <div className="field" data-tool-field>
          <label className="label" htmlFor="cfg-sdesc">description</label>
          <textarea className="textarea" id="cfg-sdesc" rows={3} value={desc} onChange={e => setDesc(e.target.value)} />
        </div>
        <div className="field" data-tool-field>
          <label className="label" htmlFor="cfg-stools">allowed_tools (comma-separated)</label>
          <input className="input" id="cfg-stools" type="text" value={tools} onChange={e => setTools(e.target.value)} />
        </div>
        <div className="srow rail-settings-item">
          <div>
            <div className="t rail-settings-label">Save skill</div>
            <div className="d rail-list-sub">Name stays pinned to the file stem; version bumps automatically.</div>
          </div>
          <div className="row"><Button disabled={busy} onClick={save}>Save skill</Button></div>
        </div>
      </> : <p className="faint" style={{ fontSize: 12 }}>No skills in this store yet.</p>}
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
  const present = Object.keys(charter).length > 0;
  return (
    <section className="sblock">
      <h2>Charter</h2>
      {present ? <>
        <div className="srow rail-settings-item">
          <div>
            <div className="t rail-settings-label">Human-only</div>
            <div className="d rail-list-sub">Saving asks for the phrase APPROVE charter; semantics moving report approval_mismatch until reapproved.</div>
          </div>
          <div className="row"></div>
        </div>
        <div className="field" data-tool-field>
          <label className="label" htmlFor="cfg-charter-att">budget item attempts</label>
          <input className="input" id="cfg-charter-att" type="text" value={item} onChange={e => setItem(e.target.value)} />
        </div>
        <div className="srow rail-settings-item">
          <div>
            <div className="t rail-settings-label">Save charter</div>
            <div className="d rail-list-sub">Goal, scope and gates stay human-only; this form edits budgets.</div>
          </div>
          <div className="row"><Button disabled={busy} onClick={save}>Save charter</Button></div>
        </div>
      </> : <p className="faint" style={{ fontSize: 12 }}>No charter yet — run <span className="mono">rail init --interview</span> first.</p>}
    </section>
  );
}

function Packs({ busy, run, read }: { busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void; read: (tool: string, args?: Record<string, string>, signal?: AbortSignal) => Promise<Data> }) {
  const [name, setName] = useState('');
  const [out, setOut] = useState<Data | null>(null);
  const show = async (tool: string, args: Record<string, string>) => {
    try { setOut(await read(tool, args)); } catch (e) { setOut({ error: String(e) || 'refused' }); }
  };
  return (
    <section className="sblock">
      <h2>Packs</h2>
      <div className="field" data-tool-field>
        <label className="label" htmlFor="cfg-pack">pack name</label>
        <input className="input" id="cfg-pack" type="text" value={name} placeholder="basic" onChange={e => setName(e.target.value)} />
      </div>
      <div className="srow rail-settings-item">
        <div>
          <div className="t rail-settings-label">Inspect and create</div>
          <div className="d rail-list-sub">show is a read; validate is a read-only report; create writes a draft without switching.</div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <Button variant="secondary" disabled={busy || !name} onClick={() => void show('pack show', { name })}>Show</Button>
          <Button variant="secondary" disabled={busy || !name} onClick={() => void show('pack validate', { name })}>Validate</Button>
          <Button variant="secondary" disabled={busy || !name} onClick={() => run('pack create', { name, from: 'basic' })}>Create draft</Button>
        </div>
      </div>
      {out ? <Panel title="Result"><pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(out, null, 2)}</pre></Panel> : null}
    </section>
  );
}

function Measure({ busy, run }: { busy: boolean; run: (t: string, a: Record<string, unknown>, c?: boolean) => void }) {
  const [target, setTarget] = useState('');
  const [before, setBefore] = useState('1');
  const [after, setAfter] = useState('2');
  return (
    <section className="sblock">
      <h2>Measure and rollback</h2>
      <div className="field" data-tool-field>
        <label className="label" htmlFor="cfg-mtarget">target (profile or skill name)</label>
        <input className="input" id="cfg-mtarget" type="text" value={target} onChange={e => setTarget(e.target.value)} />
      </div>
      <Fields items={[
        ['before version', before, setBefore],
        ['after version', after, setAfter],
      ]} />
      <div className="srow rail-settings-item">
        <div>
          <div className="t rail-settings-label">Compare and restore</div>
          <div className="d rail-list-sub">Measurement is observational, never an auto-rollout. Zero recurrences is not proof of resolution.</div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <Button variant="secondary" disabled={busy || !target} onClick={() => run('measure versions', { target_class: 'profile', target, before: Number(before), after: Number(after) })}>Compare versions</Button>
          <Button variant="secondary" disabled={busy || !target} onClick={() => run('rollback', { target_class: 'profile', target, version: Number(before) })}>Rollback to before</Button>
        </div>
      </div>
    </section>
  );
}

function Fields({ items }: { items: Array<[string, string, (v: string) => void]> }) {
  return (
    <>
      {items.map(([label, value, onChange]) => (
        <div className="field" data-tool-field key={label}>
          <label className="label" htmlFor={`cfg-${label}`}>{label}</label>
          <input className="input" id={`cfg-${label}`} type="text" value={value} onChange={e => onChange(e.target.value)} />
        </div>
      ))}
    </>
  );
}
