/* Configuration: profiles, skills, charter, packs and measurement.
 * A sibling of Appearance/Keyboard shortcuts: the same nav, section, row and
 * segmented-control idioms. Options come from the engine's own vocabularies
 * (snapshot.profile_options), never from a hard-coded list in a component.
 * Every save goes through the gateway with the current etag. */
import { useEffect, useState } from 'react';
import { rows, object, text, type Data, type PageProps } from '../contracts';
import { FileText, Keyboard, Layers, ShieldCheck, Users } from '../icons';
import { Button, PageHeading, Panel, cn } from '../ui';

type Tab = 'profiles' | 'skills' | 'charter' | 'packs' | 'measure';
const TABS: Array<{ key: Tab; label: string; icon: typeof Users }> = [
  { key: 'profiles', label: 'Profiles', icon: Users },
  { key: 'skills', label: 'Skills', icon: FileText },
  { key: 'charter', label: 'Charter', icon: ShieldCheck },
  { key: 'packs', label: 'Packs', icon: Layers },
  { key: 'measure', label: 'Measure', icon: Keyboard },
];

const num = (v: unknown): string => (typeof v === 'number' ? String(v) : v === null || v === undefined ? '' : text(v));
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map(item => text(item)).filter(Boolean) : []);
const budgetOf = (doc: Data) => object(object(doc.fields).budgets);

export function ConfigSettings({ snapshot, mutate, read, busy, refresh }: PageProps) {
  const [tab, setTab] = useState<Tab>('profiles');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Data | null>(null);
  const [options, setOptions] = useState<Data>({});

  useEffect(() => {
    let live = true;
    Promise.resolve(read('config_options'))
      .then(value => { if (live) setOptions(object(value)); })
      .catch(() => { if (live) setOptions({}); });
    return () => { live = false; };
  }, [read]);

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

  return (
    <>
      <PageHeading
        title="Configuration"
        description="Profiles, skills and charter. Every save goes through the gateway with the current etag."
      />
      <div className="seg rail-segmented" role="tablist" aria-label="Configuration" style={{ marginBottom: 20 }}>
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={cn('rail-segment', tab === key && 'on rail-segment-on')}
            onClick={() => setTab(key)}
          >
            <Icon className="i" size={14} strokeWidth={1.8} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      {error ? <p role="alert" className="offline-bar" style={{ marginBottom: 12 }}>{error}</p> : null}
      {result ? (
        <section className="sblock">
          <h2>Last change</h2>
          <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{JSON.stringify(result, null, 2)}</pre>
        </section>
      ) : null}
      {tab === 'profiles' ? <Profiles actors={rows(snapshot.actors)} options={options} busy={busy} run={run} /> : null}
      {tab === 'skills' ? <Skills skills={rows(snapshot.skills)} options={options} busy={busy} run={run} /> : null}
      {tab === 'charter' ? <Charter charter={object(snapshot.charter)} busy={busy} run={run} /> : null}
      {tab === 'packs' ? <Packs busy={busy} run={run} read={read} /> : null}
      {tab === 'measure' ? <Measure busy={busy} run={run} /> : null}
    </>
  );
}

/* One setting row, exactly the Appearance pattern: title + description on the
   left, the control on the right. */
function Row({ title, description, children }: { title: string; description?: string; children?: React.ReactNode }) {
  return (
    <div className="srow rail-settings-item">
      <div>
        <div className="t rail-settings-label">{title}</div>
        {description ? <div className="d rail-list-sub">{description}</div> : null}
      </div>
      <div className="row">{children}</div>
    </div>
  );
}

/* The Appearance segmented control: one radio group of buttons. `allowEmpty`
   adds a "Default" choice for a nullable field. */
function Segmented({ label, options, value, onChange, allowEmpty = false }: {
  label: string; options: string[]; value: string; onChange: (v: string) => void; allowEmpty?: boolean;
}) {
  const choices = allowEmpty ? ['', ...options] : options;
  return (
    <div className="seg rail-segmented" role="radiogroup" aria-label={label}>
      {choices.map(choice => (
        <button
          key={choice || 'default'}
          type="button"
          role="radio"
          aria-checked={value === choice}
          className={cn('rail-segment', value === choice && 'on rail-segment-on')}
          onClick={() => onChange(choice)}
        >
          {choice === '' ? 'Default' : choice}
        </button>
      ))}
    </div>
  );
}

function Profiles({ actors, options, busy, run }: {
  actors: Data[]; options: Data; busy: boolean;
  run: (t: string, a: Record<string, unknown>, c?: boolean) => void;
}) {
  const runners = strings(options.runners);
  const efforts = strings(options.efforts);
  const autos = strings(options.autos);
  const tools = strings(options.tools);
  const methods = strings(options.methods);

  const [sel, setSel] = useState('');
  const doc = actors.find(d => text(d.id) === sel) ?? actors[0];
  const f = object(doc?.fields);
  const b = budgetOf(doc ?? {});

  const [model, setModel] = useState(text(f.model));
  const [effort, setEffort] = useState(text(f.effort));
  const [auto, setAuto] = useState(text(f.auto));
  const [runner, setRunner] = useState(text(f.runner, runners[0] ?? 'droid'));
  const [selectedTools, setSelectedTools] = useState<string[]>(strings(f.tools));
  const [selectedMethods, setSelectedMethods] = useState<string[]>(strings(f.allowed_methods));
  const [attempts, setAttempts] = useState(num(b.attempts));
  const [seconds, setSeconds] = useState(num(b.seconds));

  const toggle = (list: string[], item: string) =>
    list.includes(item) ? list.filter(entry => entry !== item) : [...list, item];

  const save = () => {
    if (!doc) return;
    const set: string[] = [];
    if (model) set.push(`model=${model}`);
    set.push(`effort=${effort || 'null'}`, `auto=${auto || 'null'}`);
    if (runner) set.push(`runner=${runner}`);
    set.push(`tools=${JSON.stringify(selectedTools)}`);
    set.push(`allowed_methods=${JSON.stringify(selectedMethods)}`);
    set.push(`budgets={ attempts: ${attempts || 'null'}, seconds: ${seconds || 'null'} }`);
    run('update', { slug: text(doc.id), set, expect: text(doc.etag) });
  };

  return (
    <section className="sblock" style={{ marginTop: 0 }}>
      <h2>Profile</h2>
      {actors.length ? <>
        <Row title="Which actor" description="The document this form edits.">
          <select className="input" aria-label="Profile" value={text(doc?.id)} onChange={e => setSel(e.target.value)}>
            {actors.map(a => <option key={text(a.id)} value={text(a.id)}>{text(a.title, text(a.id))}</option>)}
          </select>
        </Row>
        <Row title="Model" description="The model id this profile asks its runner for.">
          <input className="input" aria-label="Model" value={model} onChange={e => setModel(e.target.value)} />
        </Row>
        <Row title="Runner" description="Which agent CLI the engine launches for this profile.">
          <Segmented label="Runner" options={runners} value={runner} onChange={setRunner} />
        </Row>
        <Row title="Effort" description="Passed through to the runner when it supports it.">
          <Segmented label="Effort" options={efforts} value={effort} onChange={setEffort} allowEmpty />
        </Row>
        <Row title="Autonomy" description="Only the CTO recipe may run at high.">
          <Segmented label="Autonomy" options={autos} value={auto} onChange={setAuto} allowEmpty />
        </Row>
        <Row title="Tools" description="The tool ids this worker may use; the reviewer may hold only read tools.">
          <Chips label="Tools" options={tools} selected={selectedTools} onToggle={item => setSelectedTools(current => toggle(current, item))} />
        </Row>
        <details className="sblock" style={{ padding: 12 }}>
          <summary className="t rail-settings-label">Advanced: allowed methods ({selectedMethods.length} selected)</summary>
          <div className="d rail-list-sub" style={{ margin: '6px 0 8px' }}>Narrowing only: a profile can shrink its actor&apos;s methods, never widen them.</div>
          <Chips label="Allowed methods" options={methods} selected={selectedMethods} onToggle={item => setSelectedMethods(current => toggle(current, item))} />
        </details>
        <Row title="Attempts" description="Worker attempts this profile allows; raising a budget needs a human.">
          <input className="input" aria-label="Attempts" value={attempts} onChange={e => setAttempts(e.target.value)} />
        </Row>
        <Row title="Seconds" description="Worker runtime this profile allows; raising a budget needs a human.">
          <input className="input" aria-label="Seconds" value={seconds} onChange={e => setSeconds(e.target.value)} />
        </Row>
        <Row title="Save profile" description="Version bumps once; the change is one gateway write.">
          <Button disabled={busy} onClick={save}>Save profile</Button>
        </Row>
      </> : <p className="faint" style={{ fontSize: 12 }}>No profiles in this store yet.</p>}
    </section>
  );
}

function Skills({ skills, options, busy, run }: {
  skills: Data[]; options: Data; busy: boolean;
  run: (t: string, a: Record<string, unknown>, c?: boolean) => void;
}) {
  const toolIds = strings(options.tools);
  const [sel, setSel] = useState('');
  const doc = skills.find(d => text(d.id) === sel) ?? skills[0];
  const f = object(doc?.fields);
  const [desc, setDesc] = useState(text(f.description));
  const [tools, setTools] = useState<string[]>(strings(f.allowed_tools));

  const save = () => {
    if (!doc) return;
    run('update', {
      slug: text(doc.id),
      set: [`description=${desc}`, `allowed_tools=${JSON.stringify(tools)}`],
      expect: text(doc.etag),
    });
  };

  return (
    <section className="sblock" style={{ marginTop: 0 }}>
      <h2>Skill</h2>
      {skills.length ? <>
        <Row title="Which skill" description="The document this form edits.">
          <select className="input" aria-label="Skill" value={text(doc?.id)} onChange={e => setSel(e.target.value)}>
            {skills.map(s => <option key={text(s.id)} value={text(s.id)}>{text(s.title, text(s.id))}</option>)}
          </select>
        </Row>
        <div className="srow rail-settings-item" style={{ alignItems: 'stretch' }}>
          <div style={{ flex: 1 }}>
            <div className="t rail-settings-label">Description</div>
            <div className="d rail-list-sub">One line; a skill without a description is refused.</div>
            <textarea className="textarea" aria-label="Description" rows={3} style={{ marginTop: 8 }} value={desc} onChange={e => setDesc(e.target.value)} />
          </div>
        </div>
        <Row title="Allowed tools" description="Known tool ids only; the schema refuses anything else.">
          <Chips label="Allowed tools" options={toolIds} selected={tools} onToggle={item => setTools(current => toggleItem(current, item))} />
        </Row>
        <Row title="Save skill" description="The name stays pinned to the file stem; version bumps once.">
          <Button disabled={busy} onClick={save}>Save skill</Button>
        </Row>
      </> : <p className="faint" style={{ fontSize: 12 }}>No skills in this store yet.</p>}
    </section>
  );
}

function Charter({ charter, busy, run }: {
  charter: Data; busy: boolean;
  run: (t: string, a: Record<string, unknown>, c?: boolean) => void;
}) {
  const f = object(charter.fields);
  const budgets = object(f.budgets);
  const [attempts, setAttempts] = useState(num(object(budgets.item).attempts));
  const [seconds, setSeconds] = useState(num(object(budgets.item).seconds));
  const present = Object.keys(charter).length > 0;
  const save = () => {
    const item = { attempts: attempts || null, seconds: seconds || null };
    run('update', {
      slug: 'charter',
      set: [`budgets={"item": ${JSON.stringify(item)}}`],
      expect: text(charter.etag),
      confirm: 'APPROVE charter',
    }, true);
  };
  return (
    <section className="sblock" style={{ marginTop: 0 }}>
      <h2>Charter</h2>
      {present ? <>
        <Row title="Human-only" description="Saving asks for the phrase APPROVE charter; a semantic change reports approval_mismatch until reapproved." />
        <Row title="Item attempts" description="The cap every worker run is measured against.">
          <input className="input" aria-label="Item attempts" value={attempts} onChange={e => setAttempts(e.target.value)} />
        </Row>
        <Row title="Item seconds" description="The runtime cap every worker run is measured against.">
          <input className="input" aria-label="Item seconds" value={seconds} onChange={e => setSeconds(e.target.value)} />
        </Row>
        <Row title="Save charter" description="Goal, scope and gates stay human-only; this form edits budgets.">
          <Button disabled={busy} onClick={save}>Save charter</Button>
        </Row>
      </> : <p className="faint" style={{ fontSize: 12 }}>No charter yet — run <span className="mono">rail init --interview</span> first.</p>}
    </section>
  );
}

function Packs({ busy, run, read }: {
  busy: boolean;
  run: (t: string, a: Record<string, unknown>, c?: boolean) => void;
  read: (tool: string, args?: Record<string, string>, signal?: AbortSignal) => Promise<Data>;
}) {
  const [name, setName] = useState('');
  const [out, setOut] = useState<Data | null>(null);
  const show = async (tool: string, args: Record<string, string>) => {
    try { setOut(await read(tool, args)); } catch (e) { setOut({ error: e instanceof Error ? e.message : String(e) }); }
  };
  return (
    <section className="sblock" style={{ marginTop: 0 }}>
      <h2>Packs</h2>
      <Row title="Pack name" description="The installed pack to inspect.">
        <input className="input" aria-label="Pack name" placeholder="basic" value={name} onChange={e => setName(e.target.value)} />
      </Row>
      <Row title="Inspect" description="Show and validate are reads; create writes a draft without switching.">
        <Button variant="secondary" disabled={busy || !name} onClick={() => void show('pack show', { name })}>Show</Button>
        <Button variant="secondary" disabled={busy || !name} onClick={() => void show('pack validate', { name })}>Validate</Button>
        <Button variant="secondary" disabled={busy || !name} onClick={() => run('pack create', { name, from: 'basic' })}>Create draft</Button>
      </Row>
      {out ? <Panel title="Result"><pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(out, null, 2)}</pre></Panel> : null}
    </section>
  );
}

function Measure({ busy, run }: {
  busy: boolean;
  run: (t: string, a: Record<string, unknown>, c?: boolean) => void;
}) {
  const [targetClass, setTargetClass] = useState('profile');
  const [target, setTarget] = useState('');
  const [before, setBefore] = useState('1');
  const [after, setAfter] = useState('2');
  return (
    <section className="sblock" style={{ marginTop: 0 }}>
      <h2>Measure and rollback</h2>
      <Row title="Target class" description="A profile or a skill.">
        <Segmented label="Target class" options={['profile', 'skill']} value={targetClass} onChange={setTargetClass} />
      </Row>
      <Row title="Target" description="The profile or skill name.">
        <input className="input" aria-label="Target" value={target} onChange={e => setTarget(e.target.value)} />
      </Row>
      <Row title="Versions" description="The earlier and later version to compare.">
        <input className="input" aria-label="Before version" style={{ width: 80 }} value={before} onChange={e => setBefore(e.target.value)} />
        <input className="input" aria-label="After version" style={{ width: 80 }} value={after} onChange={e => setAfter(e.target.value)} />
      </Row>
      <Row title="Compare" description="Observational only; never an automatic rollout.">
        <Button variant="secondary" disabled={busy || !target} onClick={() => run('measure versions', { target_class: targetClass, target, before: Number(before), after: Number(after) })}>Compare versions</Button>
        <Button variant="secondary" disabled={busy || !target} onClick={() => run('rollback', { target_class: targetClass, target, version: Number(before) })}>Rollback to version {before}</Button>
      </Row>
    </section>
  );
}

function toggleItem(list: string[], item: string): string[] {
  return list.includes(item) ? list.filter(entry => entry !== item) : [...list, item];
}

function Chips({ label, options, selected, onToggle }: {
  label: string; options: string[]; selected: string[]; onToggle: (item: string) => void;
}) {
  return (
    <div className="seg rail-segmented" role="group" aria-label={label} style={{ flexWrap: 'wrap' }}>
      {options.map(option => (
        <button
          key={option}
          type="button"
          aria-pressed={selected.includes(option)}
          className={cn('rail-segment', selected.includes(option) && 'on rail-segment-on')}
          onClick={() => onToggle(option)}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
