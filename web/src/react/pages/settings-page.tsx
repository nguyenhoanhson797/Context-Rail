import { type CSSProperties, type ReactNode, useEffect, useState } from 'react';
import type { PageProps } from '../contracts';
import { ACCENTS, applyPrefs, readPrefs, savePrefs, type Prefs } from '../preferences';
import { Panel, PageHeading, cn } from '../ui';
import { Check, Keyboard as KeyboardGlyph, Monitor, Moon, Palette, Sun } from '../icons';
import { ConfigSettings } from './config';

const titleCase = (id: string): string => id.replace(/-/g, ' ').replace(/(^|\s)\w/g, (c) => c.toUpperCase());

/* Every value about to land in a style attribute is a fixed swatch from ACCENTS, never store text. */
const swatch = (value: string): CSSProperties => ({ '--c': /^#[0-9a-f]{3,8}$/i.test(value) ? value : '#4B5BD6' }) as CSSProperties;
const SHORTCUTS: [string, [string, string[]][]][] = [
  [
    'General',
    [
      ['Command menu', ['Ctrl', 'K']],
      ['Keyboard shortcuts', ['?']],
      ['Toggle sidebar', ['[']],
      ['Toggle dark mode', ['Ctrl', 'Shift', 'L']],
      ['Close panel or dialog', ['Esc']],
    ],
  ],
  [
    'Navigate (press G, then a key)',
    [
      ['Flow', ['G', 'B']],
      ['Plan', ['G', 'R']],
      ['Records', ['G', 'D']],
    ],
  ],
  [
    'Command menu',
    [
      ['Move selection', ['↑', '↓']],
      ['Run command', ['↵']],
    ],
  ],
];
const THEMES: ['light' | 'dark' | 'system', string, ReactNode][] = [
  ['light', 'Light', <Sun className="i" size={13} strokeWidth={1.8} aria-hidden="true" key="light" />],
  ['dark', 'Dark', <Moon className="i" size={13} strokeWidth={1.8} aria-hidden="true" key="dark" />],
  ['system', 'System', <Monitor className="i" size={13} strokeWidth={1.8} aria-hidden="true" key="system" />],
];

export function SettingsPage({ params, navigate, theme, setTheme, request, mutate, busy, project, refresh, snapshot, read, openDoc }: PageProps) {
  const rawSection = params.get('sec');
  const section = rawSection === 'shortcuts' || rawSection === 'config' ? rawSection : 'appearance';
  const [prefs, setPrefs] = useState<Prefs>(readPrefs);
  useEffect(() => {
    applyPrefs(prefs);
  }, [prefs]);
  const update = (patch: Partial<Prefs>) =>
    setPrefs((current) => {
      const next = { ...current, ...patch };
      savePrefs(next);
      return next;
    });
  const segmented = (key: keyof Prefs, options: string[]) => (
    <div className="seg rail-segmented" role="radiogroup" aria-label={key}>
      {options.map((option) => (
        <button key={option} type="button" role="radio" aria-checked={prefs[key] === option} className={cn('rail-segment', prefs[key] === option && 'on rail-segment-on')} onClick={() => update({ [key]: option } as Partial<Prefs>)}>
          {titleCase(option)}
        </button>
      ))}
    </div>
  );
  return (
    <div className="set rail-settings">
      <nav className="set-nav rail-settings-nav" aria-label="Settings">
        <button type="button" className={cn("sitem rail-nav-link", section === 'appearance' && "on")} aria-current={section === 'appearance' ? 'page' : undefined} onClick={() => navigate('settings', { sec: 'appearance' })}>
          <Palette className="i" size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Appearance</span>
        </button>
        <button type="button" className={cn("sitem rail-nav-link", section === 'shortcuts' && "on")} aria-current={section === 'shortcuts' ? 'page' : undefined} onClick={() => navigate('settings', { sec: 'shortcuts' })}>
          <KeyboardGlyph className="i" size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Keyboard shortcuts</span>
        </button>
        <button type="button" className={cn("sitem rail-nav-link", section === 'config' && "on")} aria-current={section === 'config' ? 'page' : undefined} onClick={() => navigate('settings', { sec: 'config' })}>
          <Palette className="i" size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>Configuration</span>
        </button>
      </nav>
      <div className="set-body rail-settings-body"><div className="set-in">
        {section === 'config' ? (
          <ConfigSettings snapshot={snapshot} project={project} params={params} navigate={navigate} openDoc={openDoc} read={read} mutate={mutate} busy={busy} refresh={refresh} request={request} theme={theme} setTheme={setTheme} />
        ) : section === 'shortcuts' ? (
          <>
            <PageHeading title="Keyboard shortcuts" description="Move without the mouse." />
            {SHORTCUTS.map(([group, list]) => (
              <Panel key={group} title={group}>
                {list.map(([name, keys]) => (
                  <div className="srow rail-settings-item" key={name}>
                    <span>{name}</span>
                    <span className="row rail-row">
                      {/* One chord can repeat a key (G then G), so the key is the tuple
                          position, not the label - a duplicate label is not a bug. */}
                      {keys.map((key, index) => (
                        <kbd className="kbd rail-kbd" key={`${key}-${index}`}>
                          {key}
                        </kbd>
                      ))}
                    </span>
                  </div>
                ))}
              </Panel>
            ))}
          </>
        ) : (
          <>
            <h1>Appearance</h1><p className="lead">How Context Rail looks on this device. These are the only things the browser remembers.</p>
            <section className="sblock" style={{marginTop:0}}><h2>Theme</h2>
              <div className="seg rail-segmented" role="radiogroup" aria-label="Theme" style={{marginTop:12}}>
                {THEMES.map(([key, label, glyph]) => (
                  <button key={key} type="button" role="radio" aria-checked={theme === key} className={cn('rail-segment', theme === key && 'on rail-segment-on')} onClick={() => setTheme(key)}>
                    {glyph}
                    {label}
                  </button>
                ))}
              </div>
            </section>
            <section className="sblock"><h2>Accent</h2>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Accent colour</div>
                  <div className="d rail-list-sub">Used for primary buttons, focus rings and selection.</div>
                </div>
                <div className="row"><div className="swatches rail-swatches" role="radiogroup" aria-label="Accent colour">
                  {ACCENTS.map(([key, colour]) => (
                    <button
                      key={key}
                      type="button"
                      role="radio"
                      aria-checked={prefs.accent === key}
                      aria-label={key}
                      className={cn('sw rail-swatch', prefs.accent === key && 'on')}
                      style={swatch(colour)}
                      onClick={() => update({ accent: key })}
                    >
                      {prefs.accent === key ? <Check className="i" size={13} strokeWidth={1.8} aria-hidden="true" /> : null}
                    </button>
                  ))}
                </div></div>
              </div>
            </section>
            <section className="sblock"><h2>Density</h2>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Sidebar</div>
                  <div className="d rail-list-sub">Compact fits more on screen.</div>
                </div>
                <div className="row">{segmented('side', ['comfortable', 'compact'])}</div>
              </div>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Cards and rows</div>
                  <div className="d rail-list-sub">Row height in lists and padding on the board.</div>
                </div>
                <div className="row">{segmented('density', ['comfortable', 'compact'])}</div>
              </div>
              <div className="srow rail-settings-item">
                <div>
                  <div className="t rail-settings-label">Motion</div>
                  <div className="d rail-list-sub">Reduce animation for the drawer and menus.</div>
                </div>
                <div className="row">{segmented('motion', ['system', 'reduce'])}</div>
              </div>
            </section>
          </>
        )}
      </div></div>
    </div>
  );
}
