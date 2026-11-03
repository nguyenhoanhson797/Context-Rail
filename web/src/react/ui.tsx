import { useContext, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X, AlertCircle, CircleHelp } from './icons';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import ReactMarkdown from 'react-markdown';
import { DocumentNavigation, remarkWikiLinks } from './wikiLinks';
import { docValid } from './transport';
export function Markdown({ children }: { children: string }) {
  const openDoc = useContext(DocumentNavigation);
  return <div className="md rail-markdown"><ReactMarkdown skipHtml remarkPlugins={[remarkWikiLinks]} components={{
    h1: ({children: content}) => <h3>{content}</h3>,
    h2: ({children: content}) => <h4>{content}</h4>,
    h3: ({children: content}) => <h5>{content}</h5>,
    h4: ({children: content}) => <h6>{content}</h6>,
    h5: ({children: content}) => <h6>{content}</h6>,
    h6: ({children: content}) => <h6>{content}</h6>,
    a: ({ href, children: label }) => {
      if(href?.startsWith('#rail-doc/') && openDoc) {
        let id='';try { id=decodeURIComponent(href.slice(10)); } catch { return <span>{label}</span>; }
        return docValid(id)?<button type="button" className="rail-link" onClick={()=>openDoc(id)}>{label}</button>:<span>{label}</span>;
      }
      return href && /^https?:\/\//i.test(href) ? <a href={href} target="_blank" rel="noopener noreferrer">{label}</a> : <span>{label}</span>;
    },
    img: ({ alt }) => <span>{alt || '[image omitted]'}</span>,
  }}>{children}</ReactMarkdown></div>;
}
import type { PanelProps } from './contracts';
export const cn = (...values: Parameters<typeof clsx>) => twMerge(clsx(...values));
export function Button({ className, variant = 'secondary', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  return <button type="button" className={cn('btn rail-button', `btn-${variant === 'danger' ? 'danger-ghost' : variant}`, `rail-button-${variant}`, className)} {...props} />;
}
export function Panel({ title, description, children, action }: PanelProps) {
  return <section className="panel rail-panel"><header className="panel-h rail-panel-header"><div><h2>{title}</h2>{description && <p className="faint">{description}</p>}</div>{action && <div className="acts">{action}</div>}</header><div className="panel-b rail-panel-body">{children}</div></section>;
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'good' | 'warning' | 'danger' | 'accent' }) { return <span className={`badge ${{neutral:'gray',good:'green',warning:'amber',danger:'red',accent:'accent'}[tone]} rail-badge rail-badge-${tone}`}>{children}</span>; }
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty-state rail-empty"><div className="glyph"><CircleHelp size={20} aria-hidden="true" /></div><h2 className="es-h">{title}</h2><p>{children}</p></div>; }
export function ErrorState({ error, retry }: { error: string; retry?: () => void }) { return <div role="alert" className="rail-error"><AlertCircle size={18} aria-hidden="true" /><div><strong>Could not load this view</strong><p>{error}</p>{retry && <Button onClick={retry}>Try again</Button>}</div></div>; }
export function Loading() { return <div role="status" className="rail-loading"><div className="rail-skeleton" /><div className="rail-skeleton" /><p>Reading project context...</p></div>; }
export function PageHeading({ title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) { return <header className="ph rail-page-heading"><div><h1>{title}</h1>{description && <p>{description}</p>}</div>{action && <div className="acts">{action}</div>}</header>; }
export function Sheet({ open, onOpenChange, title, description, children }: { open: boolean; onOpenChange: (v: boolean) => void; title: string; description: string; children: ReactNode }) {
  const returnTo = useRef<HTMLElement | null>(null);
  const content = useRef<HTMLDivElement | null>(null);
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="drawer-scrim rail-overlay" /><Dialog.Content ref={content} className="drawer rail-sheet" onOpenAutoFocus={event => { returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; event.preventDefault(); const panel=content.current;const initial=panel?.querySelector<HTMLElement>('[data-rail-autofocus]')??panel?.querySelector<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]');(initial??panel)?.focus(); }} onCloseAutoFocus={event => { event.preventDefault(); const remaining=Array.from(document.querySelectorAll<HTMLElement>('.rail-sheet')).filter(panel=>panel.isConnected&&panel!==content.current).at(-1);if(remaining){if(!remaining.contains(document.activeElement)){const initial=remaining.querySelector<HTMLElement>('[data-rail-autofocus]')??remaining.querySelector<HTMLElement>('button:not(:disabled),input:not(:disabled)');(initial??remaining).focus();}}else if(returnTo.current?.isConnected) returnTo.current.focus(); }}><header className="drawer-h"><div className="grow"><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{description}</Dialog.Description></div><Dialog.Close asChild><Button variant="ghost" aria-label="Close details"><X size={20} /></Button></Dialog.Close></header><div className="drawer-b rail-sheet-body">{children}</div></Dialog.Content></Dialog.Portal></Dialog.Root>;
}
