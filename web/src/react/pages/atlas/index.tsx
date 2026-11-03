/* =====================================================================
   PAGE: the Atlas (React) - the project top down, in four altitudes (Cover,
   Map, Consequences, Records) and six questions.
   It reads the `atlas` tool through the shell's read() and renders it.
   Nothing here fetches a second way, writes or mutates the store; model.ts turns
   anything missing into an honest "unknown", and React escapes every value.
   ===================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { PageProps, Route } from '../../contracts';
import { readKey } from '../../refresh';
import { modelOf, type Model } from './model';
import { fetchOwnerSummary, ownerSummaryKey, ownerSummaryOf } from '../evidence';
import { AtlasContext, useAtlas, type AtlasHandlers } from './ui';
import { Cover } from './cover';
import { MapAltitude } from './map';
import { Consequences } from './consequences';
import { Records } from './records';
import { drawerContent, type DrawerKind, type DrawerRequest } from './drawer';

export const ALTS = [
  { id: 'cover', n: 1, name: 'Cover', t: '30 seconds' },
  { id: 'map', n: 2, name: 'Map', t: '2 minutes' },
  { id: 'consequences', n: 3, name: 'Consequences', t: '5 minutes' },
  { id: 'records', n: 4, name: 'Records', t: 'when needed' },
];
const ALT_IDS = new Set(ALTS.map((a) => a.id));
export const altOf = (raw: string | null): string => (raw && ALT_IDS.has(raw) ? raw : 'cover');

/* The six questions: each is a place on the page (an altitude and a section of it). */
export const QUESTIONS = [
  { n: 1, q: 'What is it?', s: 'identity · the story', alt: 'cover', anchor: 'c-what' },
  { n: 2, q: 'How far does it go?', s: 'the rail · the fence', alt: 'cover', anchor: 'c-reach' },
  { n: 3, q: 'Where are we, honestly?', s: 'built vs proven', alt: 'cover', anchor: 'c-status' },
  { n: 4, q: 'What is moving now?', s: 'in flight · who holds it', alt: 'cover', anchor: 'c-now' },
  { n: 5, q: 'What comes next?', s: 'queue · needs you', alt: 'cover', anchor: 'c-now' },
  { n: 6, q: 'What follows from it?', s: 'unlocks · decay · risk', alt: 'consequences', anchor: 'k-graph' },
];
/* The sections a link may scroll to, per altitude: an address value is never used as a selector without this list. */
export const ANCHORS: Record<string, string[]> = {
  cover: ['c-what', 'c-reach', 'c-status', 'c-now', 'c-follow'],
  map: ['m-what', 'm-fence', 'm-ladder', 'm-pace'],
  consequences: ['k-graph', 'k-decay', 'k-truth', 'k-wait'],
  records: ['r-doors'],
};
const FIRST_Q: Record<string, string> = { cover: '1', map: '1', consequences: '6', records: '' };
const reduced = (): boolean => {
  try {
    return matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.getAttribute('data-motion') === 'reduce';
  } catch {
    return false;
  }
};

/* ---------- the head: title, the four altitudes, the read's own metadata ---------- */
function Head({ alt, M }: { alt: string; M: Model | null }) {
  const { go } = useAtlas();
  const meta = M
    ? [M.asOf ? 'as of ' + M.asOf.replace('T', ' ').slice(0, 16) + ' UTC' : '', M.schema ? 'store schema ' + M.schema : ''].filter(Boolean).join(' · ')
    : '';
  return (
    <header className="at-head">
      <div className="at-title">
        <h1>Atlas</h1>
        {M && M.project.name ? <span className="at-pname">{M.project.name}</span> : null}
      </div>
      <nav className="at-alt" aria-label="Altitude: from the whole project down to the records">
        {ALTS.map((a) => (
          <button
            type="button"
            className="at-altb"
            key={a.id}
            aria-keyshortcuts={String(a.n)}
            aria-current={a.id === alt ? 'page' : undefined}
            onClick={() => go(a.id, '')}
          >
            <span className="at-n" aria-hidden="true">
              {a.n}
            </span>
            <span className="at-altt">
              <b>{a.name}</b>
              <span className="at-t">{a.t}</span>
            </span>
          </button>
        ))}
      </nav>
      {meta ? <span className="at-meta">{meta}</span> : null}
    </header>
  );
}

/* ---------- the six questions: a rail that follows the scroll ---------- */
function QuestionRail({ alt, current, onGo }: { alt: string; current: string; onGo: (alt: string, anchor: string) => void }) {
  const on = FIRST_Q[alt];
  return (
    <aside className="at-qrail" aria-label="The six questions this page answers">
      <h2 className="at-qh">Six questions, top down</h2>
      <ol>
        {QUESTIONS.map((q) => {
          const active = String(q.n) === (current || on);
          return (
            <li key={q.n}>
              <button
                type="button"
                className={'at-qb' + (active ? ' on' : '')}
                aria-current={active ? 'location' : undefined}
                onClick={() => onGo(q.alt, q.anchor)}
              >
                <span className="at-qn">{q.n}</span>
                <span>
                  <span className="at-q">{q.q}</span>
                  <span className="at-qs">{q.s}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      <p className="at-qhint">
        Every shape on this page is a door: open it to see the source records behind it. Nothing here is typed in by hand — it is composed
        from the store.
      </p>
    </aside>
  );
}
/* ---------- the drawer for computed shapes ---------- */
function AtlasDrawer({ M, d, onClose }: { M: Model; d: DrawerRequest; onClose: () => void }) {
  const c = drawerContent(M, d);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  if (!c) return null;
  return (
    <>
      <div className="at-scrim" onClick={onClose} />
      <aside className="drawer at-drawer" role="dialog" aria-modal="true" aria-labelledby="at-dt" tabIndex={-1} ref={ref}>
        <div className="drawer-h">
          <span className="at-dk">{c.kind}</span>
          <span className="sp" />
          <button type="button" className="ibtn ibtn-sm" data-tip="Close  Esc" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div className="drawer-b" data-keep={'atlas-drawer:' + d.kind + ':' + d.id}>
          <h2 id="at-dt" className="at-dtitle">{c.title}</h2>
          {c.body}
        </div>
      </aside>
    </>
  );
}

/* ---------- the page ---------- */
export function AtlasPage({ project, params, navigate, openDoc, read, request, busy, refresh }: PageProps) {
  const query = useQuery({ queryKey: readKey(project, 'atlas'), queryFn: ({ signal }) => read('atlas', {}, signal), retry: false, enabled: !busy });
  const M = useMemo(() => (query.data ? modelOf(query.data) : null), [query.data]);
  /* The Atlas read carries no evidence or gate claim; those come from the one
     sourced read that owns them, keyed by project so a refresh reaches it. */
  const ownerQuery = useQuery({
    queryKey: ownerSummaryKey(project, 1),
    queryFn: ({ signal }) => fetchOwnerSummary(request, 1, signal),
    staleTime: 30_000,
  });
  const owner = ownerQuery.data ? ownerSummaryOf(ownerQuery.data) : null;
  const alt = altOf(params.get('alt'));
  const [drawer, setDrawer] = useState<DrawerRequest | null>(null);
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null);
  const [current, setCurrent] = useState('');
  const pending = useRef('');
  const root = useRef<HTMLDivElement>(null);
  const tipEl = useRef<HTMLDivElement>(null);

  const scrollToAnchor = useCallback((anchor: string) => {
    if (!anchor) return;
    const el = document.getElementById('atlas-' + anchor);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start', behavior: reduced() ? 'auto' : 'smooth' });
  }, []);

  const handlers = useMemo<AtlasHandlers>(
    () => ({
      open: (kind, id) => setDrawer({ kind: kind as DrawerKind, id }),
      openDoc: (id) => {
        setDrawer(null);
        openDoc(id);
      },
      lens: (id) => {
        setDrawer(null);
        navigate('lens' as Route, { task: id });
      },
      page: (route) => {
        setDrawer(null);
        navigate(route as Route);
      },
      go: (a, anchor) => {
        if (a === alt) {
          scrollToAnchor(anchor);
          return;
        }
        setDrawer(null);
        pending.current = anchor;
        navigate('atlas' as Route, a === 'cover' ? {} : { alt: a });
      },
      copy: (text) => {
        void navigator.clipboard?.writeText(text).catch(() => {});
      },
    }),
    [alt, navigate, openDoc, scrollToAnchor],
  );

  /* A pending jump lands once the altitude it names is on screen; the rail follows the scroll. */
  useEffect(() => {
    const anchor = pending.current;
    if (anchor && ANCHORS[alt]?.includes(anchor)) {
      pending.current = '';
      scrollToAnchor(anchor);
    }
  }, [alt, scrollToAnchor]);
  useEffect(() => {
    const scroller = document.getElementById('main');
    const measure = () => {
      const secs = Array.from(document.querySelectorAll<HTMLElement>('.at-main [data-q]'));
      if (!secs.length) return;
      const top = scroller ? scroller.getBoundingClientRect().top : 0;
      let cur = '';
      for (const s of secs) if (s.getBoundingClientRect().top - top < 150) cur = s.dataset.q || '';
      setCurrent(cur || secs[0].dataset.q || '');
    };
    measure();
    scroller?.addEventListener('scroll', measure, { passive: true });
    return () => scroller?.removeEventListener('scroll', measure);
  }, [alt, M]);
  /* 1-4 pick the altitude while the Atlas is up, unless the reader is typing or holding a modifier. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey || e.metaKey || e.ctrlKey || e.altKey) return;
      if (!/^[1-4]$/.test(e.key)) return;
      const el = document.activeElement;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || (el as HTMLElement).isContentEditable)) return;
      e.preventDefault();
      const next = ALTS[Number(e.key) - 1];
      if (next) navigate('atlas' as Route, next.id === 'cover' ? {} : { alt: next.id });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);
  /* The tooltip: one element, moved by the pointer or placed beside the focused shape. */
  const showTip = useCallback((text: string, x: number, y: number) => {
    const el = tipEl.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const left = Math.max(8, Math.min(innerWidth - w - 8, x + 14));
    setTip({ text, x: left, y: y + 18 + h > innerHeight ? Math.max(8, y - h - 12) : y + 18 });
  }, []);
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const source = (e: Event): HTMLElement | null => {
      const t = e.target as HTMLElement | null;
      return t && t.closest ? (t.closest('[data-atip]') as HTMLElement | null) : null;
    };
    const over = (e: Event) => {
      const el = source(e);
      if (!el) return;
      const text = el.getAttribute('data-atip') || '';
      const rect = el.getBoundingClientRect();
      if (!text) return;
      const mouse = e as MouseEvent;
      showTip(text, mouse.clientX || rect.left, mouse.clientY || rect.bottom);
    };
    const move = (e: Event) => {
      const mouse = e as MouseEvent;
      if (mouse.clientX) showTip(tip ? tip.text : '', mouse.clientX, mouse.clientY);
    };
    const inFocus = (e: Event) => {
      const el = source(e);
      if (!el) return;
      const text = el.getAttribute('data-atip') || '';
      if (!text) return;
      const rect = el.getBoundingClientRect();
      showTip(text, rect.left, rect.bottom);
    };
    const out = (e: Event) => {
      if (source(e)) setTip(null);
    };
    node.addEventListener('mouseover', over);
    node.addEventListener('mousemove', move);
    node.addEventListener('mouseout', out);
    node.addEventListener('focusin', inFocus);
    node.addEventListener('focusout', out);
    return () => {
      node.removeEventListener('mouseover', over);
      node.removeEventListener('mousemove', move);
      node.removeEventListener('mouseout', out);
      node.removeEventListener('focusin', inFocus);
      node.removeEventListener('focusout', out);
    };
  }, [showTip, tip]);
  useEffect(() => {
    setDrawer(null);
  }, [alt, project]);

  const shell = (inner: ReactNode, model: Model | null) => (
    <AtlasContext.Provider value={handlers}>
      <div className="page wide atlas at-page" data-alt={alt} ref={root}>
        <Head alt={alt} M={model} />
        {inner}
        <div className={'at-tip' + (tip ? ' on' : '')} id="at-tip" role="tooltip" ref={tipEl} style={tip ? { left: tip.x, top: tip.y } : undefined}>
          {tip?.text}
        </div>
        {model && drawer ? <AtlasDrawer M={model} d={drawer} onClose={() => setDrawer(null)} /> : null}
      </div>
    </AtlasContext.Provider>
  );

  if (query.isPending)
    return shell(
      <div className="at-state">
        <p className="at-note">Loading — reading the Atlas from the store…</p>
      </div>,
      null,
    );
  if (query.isError)
    return shell(
      <div className="at-state">
        <h2>The Atlas could not load</h2>
        <p className="at-note">{query.error.message}</p>
        <button type="button" className="btn btn-secondary" onClick={() => void (refresh ? refresh() : query.refetch())}>
          Try again
        </button>
      </div>,
      null,
    );
  if (!M) return shell(<div className="at-state" />, null);
  if (M.empty && alt !== 'records')
    return shell(
      <div className="at-state">
        <h2>Nothing to chart yet</h2>
        <p className="at-note">
          The store has no phase, scope item or task, so there is no program to place on a map. Add them with the usual tools; the Atlas
          composes itself from the store.
        </p>
      </div>,
      M,
    );
  const view = alt === 'map' ? <MapAltitude M={M} owner={owner} /> : alt === 'consequences' ? <Consequences M={M} /> : alt === 'records' ? <Records M={M} /> : <Cover M={M} />;
  return shell(
    <div className="at-shell">
      <QuestionRail
        alt={alt}
        current={current}
        onGo={(a, anchor) => handlers.go(a, anchor)}
      />
      <div className="at-main" id="atlas-main">
        {view}
      </div>
    </div>,
    M,
  );
}