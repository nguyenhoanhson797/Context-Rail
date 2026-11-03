/* Legacy overlay markup for the React shell.
   The legacy layer (web/src/overlays/layer.js) and its CSS (web/src/styles/overlays.css)
   are the source of truth: these components reproduce its class names, structure, labels
   and keyboard behavior so the React front end renders the same overlay pixels. */
import { createElement, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject } from 'react';
import { Command as CommandNode, Info as InfoNode, Maximize2 as Maximize2Node, Minimize2 as Minimize2Node, SearchX as SearchXNode, Wrench as WrenchNode } from 'lucide';
import { Lock, Terminal, X } from './icons';

type Node = readonly [string, Record<string, string | number>, (readonly [string, Record<string, string | number>])[]?];

/* Same wrapper as the trusted React icon set: one lucide node, the legacy size/stroke. */
function nodeIcon(name: string, node: Node) {
  return function Icon({ size = 16, strokeWidth = 1.8, className = '' }: { size?: number; strokeWidth?: number; className?: string }) {
    const children = node[0] === 'svg' ? node[2] ?? [] : [node];
    const kebab = name.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase();
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={'i lucide lucide-' + kebab + ' ' + className}>
        {children.map(([tag, attrs], i) => createElement(tag, { ...attrs, key: i }))}
      </svg>
    );
  };
}
export const CommandIcon = nodeIcon('Command', CommandNode);
export const InfoIcon = nodeIcon('Info', InfoNode);
export const Maximize2Icon = nodeIcon('Maximize2', Maximize2Node);
export const Minimize2Icon = nodeIcon('Minimize2', Minimize2Node);
export const SearchXIcon = nodeIcon('SearchX', SearchXNode);
export const WrenchIcon = nodeIcon('Wrench', WrenchNode);

/* ---------- the legacy status glyph (web/src/ui/helpers.js stIcon) ---------- */
export function StatusGlyph({ status, statuses, category, size = 14 }: { status: string; statuses: string[]; category?: string; size?: number }) {
  const key = category || status;
  const color = `var(--st-${key.replace(/[^a-z0-9-]/gi, '')}, var(--st-todo))`;
  const ring = <circle cx="7" cy="7" r={5.4} fill="none" stroke={color} strokeWidth={1.5} />;
  const idx = statuses.indexOf(status);
  const last = statuses.length - 1;
  let inner: ReactNode;
  const done = category ? category === 'done' : status === 'done' || (idx === last && idx > 0);
  const blocked = category ? category === 'blocked' : status === 'blocked';
  const active = category ? category === 'active' : idx * 2 >= last;
  if (done) {
    inner = <><circle cx="7" cy="7" r="6.2" fill={color} /><path d="M4.4 7.2 6.2 9 9.7 5.3" fill="none" stroke="var(--surface)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></>;
  } else if (blocked) {
    inner = <>{ring}<path d="M4.6 7h4.8" stroke={color} strokeWidth="1.6" strokeLinecap="round" /></>;
  } else if (idx <= 0 && !category) {
    inner = ring;
  } else if (active) {
    inner = <>{ring}<path d="M7 3.4A3.6 3.6 0 1 1 3.4 7L7 7Z" fill={color} /></>;
  } else {
    inner = <>{ring}<path d="M7 3.4A3.6 3.6 0 0 1 7 10.6Z" fill={color} /></>;
  }
  return <svg width={size} height={size} viewBox="0 0 14 14" aria-hidden="true" style={{ flexShrink: 0 }}>{inner}</svg>;
}

/* ---------- focus + Escape shared by every overlay ---------- */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
function visible(el: HTMLElement) {
  for (let node: HTMLElement | null = el; node; node = node.parentElement) {
    if (node.hidden || node.getAttribute('aria-hidden') === 'true' || node.getAttribute('inert') != null) return false;
    const cs = typeof getComputedStyle === 'function' ? getComputedStyle(node) : null;
    if (cs && (cs.display === 'none' || cs.visibility === 'hidden')) return false;
  }
  return true;
}
export function tabbables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(el => !el.hasAttribute('disabled') && (el.tabIndex ?? 0) >= 0 && visible(el));
}

/* Escape closes the top overlay by the legacy order (confirm > palette > form > drawer). */
const escStack: { priority: number; close: () => void }[] = [];
let escBound = false;
function bindEscape() {
  if (escBound || typeof window === 'undefined') return;
  escBound = true;
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    const top = escStack.slice().sort((a, b) => b.priority - a.priority)[0];
    if (!top) return;
    event.preventDefault();
    top.close();
  });
}
export function useOverlayEscape(priority: number, close: () => void) {
  useEffect(() => {
    bindEscape();
    const entry = { priority, close };
    escStack.push(entry);
    return () => {
      const i = escStack.indexOf(entry);
      if (i >= 0) escStack.splice(i, 1);
    };
  }, [priority, close]);
}

/* Initial focus on mount (the legacy first field or the first control), Tab trapped in the
   modal/palette/confirm scope, focus returned to the real opener on close. */
export function useModalFocus(ref: RefObject<HTMLElement | null>, initial = '[data-first]') {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const target = (initial ? root.querySelector<HTMLElement>(initial) : null) ?? tabbables(root)[0] ?? root;
    target.focus({ preventScroll: true });
    return () => {
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [ref, initial]);
  return (event: ReactKeyboardEvent) => {
    if (event.key !== 'Tab') return;
    const root = ref.current;
    if (!root) return;
    const list = tabbables(root);
    if (!list.length) {
      event.preventDefault();
      root.focus();
      return;
    }
    const first = list[0];
    const last = list[list.length - 1];
    const active = document.activeElement;
    if (!root.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }
    if (event.shiftKey && (active === first || active === root)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };
}

/* ---------- command palette ---------- */
/* ``keepOpen`` marks an item that changes the palette instead of running a
   command (the "show all" row of a truncated group): the reader stays in the
   palette and sees the rest of the matches. */
export type PaletteItem = { key: string; name: string; sub?: string; icon?: ReactNode; kbd?: string[]; run: () => void; keepOpen?: boolean };
/* ``more`` is the visible pagination row of a group that shows only its first
   matches: without it a truncated group would hide work with no way to reach it. */
export type PaletteGroup = { name: string; items: PaletteItem[]; more?: PaletteItem };

function highlight(name: string, query: string) {
  const q = query.trim();
  if (!q) return name;
  const i = name.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return name;
  return <>{name.slice(0, i)}<mark>{name.slice(i, i + q.length)}</mark>{name.slice(i + q.length)}</>;
}

export function CommandPalette({ query, onQueryChange, groups, onClose, onRun }: { query: string; onQueryChange: (q: string) => void; groups: PaletteGroup[]; onClose: () => void; onRun: (item: PaletteItem) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState(0);
  useOverlayEscape(30, onClose);
  const trapKey = useModalFocus(ref, '#pal-in');
  /* The pagination row is a palette entry too: the arrow keys reach it, so the
     matches behind it are never a mouse-only secret. */
  const flat = groups.flatMap(group => (group.more ? [...group.items, group.more] : group.items));
  const active = flat.length ? Math.min(Math.max(selection, 0), flat.length - 1) : -1;
  useEffect(() => { setSelection(0); }, [query]);
  useEffect(() => { if (active >= 0) list.current?.querySelector<HTMLElement>('#pi-' + active)?.scrollIntoView?.({ block: 'nearest' }); }, [active, groups]);
  const run = (index: number) => { const item = flat[index]; if (item) onRun(item); };
  const renderItem = (item: PaletteItem) => {
    const index = flat.indexOf(item);
    return (
      <div className={'pi' + (index === active ? ' hl' : '')} id={'pi-' + index} key={item.key} role="option" tabIndex={-1} aria-selected={index === active} onClick={() => run(index)} onMouseEnter={() => setSelection(index)}>
        {item.icon}<span className="trunc">{highlight(item.name, query)}</span>{item.sub ? <span className="sub">{item.sub}</span> : null}<span className="sp" />{item.kbd?.map((k, i) => <kbd key={i}>{k}</kbd>)}
      </div>
    );
  };
  return (
    <>
      <div className="scrim" style={{ zIndex: 89 }} onClick={onClose} />
      <div className="palette" ref={ref} role="dialog" aria-modal="true" aria-label="Command menu" tabIndex={-1} data-focus-scope="palette"
        onKeyDown={event => {
          trapKey(event);
          if (event.defaultPrevented || event.nativeEvent.isComposing || !flat.length) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setSelection(value => (Math.min(Math.max(value, 0), flat.length - 1) + (event.key === 'ArrowDown' ? 1 : -1) + flat.length) % flat.length);
          } else if (event.key === 'Enter') {
            event.preventDefault();
            run(active);
          }
        }}>
        <div className="pal-in"><CommandIcon size={17} />
          <input id="pal-in" value={query} onChange={event => onQueryChange(event.target.value)} placeholder="Run a tool, jump to a task, doc or page..." autoComplete="off" role="combobox" aria-expanded="true" aria-haspopup="listbox" aria-autocomplete="list" aria-controls="pal-list" aria-activedescendant={active >= 0 ? 'pi-' + active : undefined} aria-label="Command menu" />
        </div>
        <div className="pal-list" id="pal-list" ref={list} role="listbox" aria-label="Commands">
          {flat.length ? groups.map(group => (
            <div className="pal-group" key={group.name}>
              {group.name ? <div className="pal-sec">{group.name}</div> : null}
              {group.items.map(item => renderItem(item))}
              {group.more ? renderItem(group.more) : null}
            </div>
          )) : (
            <div className="empty-state sm"><div className="glyph"><SearchXIcon size={18} /></div><h2 className="es-h">Nothing matches</h2><p>Try a tool name like task_add, a task id or a page.</p></div>
          )}
        </div>
        <div className="pal-f"><span><kbd>↑</kbd><kbd>↓</kbd>navigate</span><span><kbd>↵</kbd>select</span><span><kbd>Esc</kbd>close</span></div>
      </div>
    </>
  );
}

/* ---------- document drawer ---------- */
export function DocDrawer({ id, type, claim, full, onToggleFull, onClose, children }: { id: string; type: string; claim: string; full: boolean; onToggleFull: () => void; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useOverlayEscape(10, onClose);
  useEffect(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return () => { if (opener.current?.isConnected) opener.current.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => { ref.current?.focus({ preventScroll: true }); }, [id]);
  return (
    <aside className={'drawer' + (full ? ' full' : '')} style={full ? undefined : {borderRadius:0}} ref={ref} role="dialog" aria-modal="false" aria-labelledby="d-h" tabIndex={-1} data-focus-scope="drawer">
      <div className="drawer-h">
        <span className="mono faint" style={{ fontSize: '11.5px' }}>{id}</span>
        {type ? <span className="badge gray">{type}</span> : null}
        {claim ? <span className="faint" style={{ fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 3 }}><Lock size={12} strokeWidth={1.8} /> {claim}</span> : null}
        <span className="sp" />
        <button type="button" className="ibtn ibtn-sm hide-m" data-tip={full ? 'Side panel' : 'Full page'} aria-label="Toggle full page" onClick={onToggleFull}>{full ? <Minimize2Icon size={15} /> : <Maximize2Icon size={15} />}</button>
        <button type="button" className="ibtn ibtn-sm" data-tip="Close  Esc" aria-label="Close" onClick={onClose}><X size={16} strokeWidth={1.8} /></button>
      </div>
      <div className="drawer-b">{children}</div>
    </aside>
  );
}

/* ---------- confirmation ---------- */
export function ConfirmDialog({ title, body, ok = 'Run', busy, onRun, onClose }: { title: string; body: string; ok?: string; busy: boolean; onRun: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useOverlayEscape(40, onClose);
  const onKeyDown = useModalFocus(ref, '');
  return (
    <>
      <div className="scrim" style={{ zIndex: 80 }} onClick={onClose} />
      <div className="modal-wrap" style={{ zIndex: 81 }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
        <div className="modal sm" ref={ref} role="alertdialog" aria-modal="true" aria-labelledby="mt-confirm" tabIndex={-1} data-focus-scope="confirm" onKeyDown={onKeyDown}>
          <div className="modal-b" style={{ paddingTop: 18, gap: 10 }}>
            <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
              <span className="ftype" style={{ '--c': 'var(--amber)', width: 34, height: 34 } as CSSProperties}><Terminal size={16} strokeWidth={1.8} /></span>
              <div className="grow"><h2 id="mt-confirm" style={{ fontSize: 15, fontWeight: 600, margin: '4px 0 6px' }}>{title}</h2><p className="muted" style={{ margin: 0, fontSize: 13, lineHeight: 1.5 }}>{body}</p></div>
            </div>
          </div>
          <div className="modal-f"><span className="sp" /><button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button><button type="button" className="btn btn-primary" disabled={busy} onClick={onRun}>{ok}</button></div>
        </div>
      </div>
    </>
  );
}

