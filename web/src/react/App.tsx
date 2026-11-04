import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Menu, RotateCcw, Search, PanelLeft, LayoutDashboard, Target, GitBranch, Route as RouteIcon, House, SquareKanban, Stethoscope, Settings, Sun, FileText, TriangleAlert, Folders } from './icons';
import { RailClient, docValid, projectValid, refusal } from './transport';
import { RefreshGeneration, projectKey, readKey, refreshProject } from './refresh';
import { ACTIVE_STATUSES, object, rows, text, type Data, type PageProps, type Route } from './contracts';
import { Button, ErrorState, Loading, PageHeading } from './ui';
import { SummaryPage } from './pages/summary';
import { NowPage, BoardPage, RoadmapPage, ScopePage, DecisionsPage, DoctorPage, SettingsPage } from './pages/workspace';
import { PackPage } from './pages/pack';
import { screenTitle } from './pages/operating';
const pages: Partial<Record<Route, import('react').ComponentType<PageProps>>> = { summary: SummaryPage, now: NowPage, board: BoardPage, roadmap: RoadmapPage, scope: ScopePage, decisions: DecisionsPage, doctor: DoctorPage, pack: PackPage, settings: SettingsPage };
const labels: Record<Route, string> = { summary: 'Summary', overview: 'Overview', atlas: 'Atlas', outcomes: 'Outcomes', traceability: 'Traceability', impact: 'Impact', activity: 'Catch up', now: 'Now', board: 'Board', roadmap: 'Plan', scope: 'Scope', decisions: 'Decisions', rules: 'Rules', lens: 'Lens', graph: 'Graph', doctor: 'Doctor', pack: 'Pack', portfolio: 'Projects', settings: 'Settings' };
/* Phase one keeps the owner's summary and the agent rail; inspect screens follow the data. */
const groups: [string, Route[]][] = [['For you', ['summary']], ['Agent rail', ['now','board','roadmap','scope','decisions']], ['Inspect', ['doctor','pack']]];
import { parseAddress, routeHash } from './router';
import { crumbModel, trailOf, TRAIL_ROUTES } from './crumbs';
import { ToolForm } from './ToolForm';
import { DocumentContent } from './DocumentContent';
import { DocumentNavigation } from './wikiLinks';
import { useRailKeyboard } from './keyboard';
import { restorePreferences } from './preferences';
import { CommandPalette, ConfirmDialog, DocDrawer, StatusGlyph, WrenchIcon, type PaletteGroup, type PaletteItem } from './overlays';
const address = () => parseAddress(location.hash);
const routeIcons: Partial<Record<Route, typeof House>> = {summary:LayoutDashboard,now:House,board:SquareKanban,roadmap:RouteIcon,scope:Target,decisions:GitBranch,doctor:Stethoscope,pack:FileText,settings:Settings};
/* The legacy palette order and its G-key labels (web/src/core/palette.js). */
const paletteRoutes: Route[] = ['summary','now','board','roadmap','scope','decisions','doctor','pack','settings'];
const navKeys: Partial<Record<Route,string>> = {summary:'U',now:'N',board:'B',roadmap:'R',scope:'S',decisions:'D',doctor:'H'};
const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MOD = isMac ? '⌘' : 'Ctrl';
const CONFIRM_TEXT: Record<string, [string, string]> = {
  done: ['Finish this task?', "Finishing runs the task's checks first: shell commands from the scope items, on this machine, for up to a few minutes. The task stays open if one fails."],
  dispatch: ['Dispatch to a worker?', 'This runs the worker command you gave, then the checks, on this machine.'],
  verify: ['Run the checks?', 'Verifying runs the scope checks that carry a cmd: line, on this machine.'],
  scope_check_tick: ['Tick this check?', 'Ticking a cmd: check runs its command on this machine.'],
};
type ConfirmRequest = { title: string; body: string; ok: string; run: () => void };
export default function App() {
  const [locationState, setLocationState] = useState(address);
  const [project, setProject] = useState(() => locationState.params.get('project') || '');
  /* Two separate flags, as in the legacy shell: `collapsed` is the desktop icon rail, `mnav` the mobile drawer. One shared flag put both classes
     on the shell, so the opened phone drawer inherited the icon-only rail and showed fifteen unlabeled icons. */
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem('ctx-rail.react-sidebar') === 'collapsed'; } catch { return false; } });
  const [mnav, setMnav] = useState(false);
  const [narrow, setNarrow] = useState(() => typeof matchMedia === 'function' && matchMedia('(max-width: 900px)').matches);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [readTimes, setReadTimes] = useState<Record<string, string>>({});
  const [readErrors, setReadErrors] = useState<Record<string, Record<string,string>>>({});
  const [doc, setDoc] = useState('');
  const [docFull, setDocFull] = useState(false);
  const [search, setSearch] = useState('');
  const [palette, setPalette] = useState(false);
  const [activeTool, setActiveTool] = useState<Data|null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmRequest|null>(null);
  const [theme, setTheme] = useState<'light' | 'dark' | 'system'>(() => { try { const saved = localStorage.getItem('ctx-rail.react-theme'); if(saved === 'light' || saved === 'dark' || saved === 'system') return saved; const legacy=object(JSON.parse(localStorage.getItem('ctx-rail.ui') || 'null'));const selected=object(legacy.prefs).theme;return selected === 'light' || selected === 'dark' ? selected : 'system'; } catch { return 'system'; } });
  const [systemDark, setSystemDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches);
  const dark = theme === 'dark' || (theme === 'system' && systemDark);
  const writeLock = useRef(false);
  const projectRef = useRef(project); projectRef.current = project;
  const operation = useRef(0);
  const main = useRef<HTMLElement>(null);
  const queryClient = useQueryClient();
  /* The refresh guard: the newest refresh wins, whatever order the answers
     arrive in. A superseded refresh never seeds the snapshot. */
  const refreshGuard = useRef(new RefreshGeneration());
  const registry = useQuery({ queryKey:['rail-global','projects'], queryFn:({signal}) => new RailClient('').request('/api/projects',{signal}), retry:false });
  const client = useMemo(() => new RailClient(projectValid(project) && project !== registry.data?.current ? project : ''), [project,registry.data?.current]);
  const tools = useQuery({queryKey:['rail-global','tools'],queryFn:({signal})=>new RailClient('').request('/api/tools',{signal}),retry:false,enabled:palette});
  const known = !project || rows(registry.data?.projects).some(p => p.id === project && p.exists === true) || registry.data?.current === project;
  const invalid = !projectValid(project) || (registry.isSuccess && !known);
  /* The page gate reads the fast context document (/api/context): the snapshot
     minus doctor_summary. Diagnostics are a separate read, so the first view
     never waits for the doctor. client.context() only falls back on an absent
     route (404) or a network failure; any other failure is surfaced. */
  const contextRead = useQuery({ queryKey:readKey(project,'snapshot'), queryFn:({signal}) => client.context(signal), enabled:registry.isSuccess && !invalid, retry:false });
  /* The one deliberate degradation, never silent: if the context read failed,
     the legacy full read keeps the page usable and the health/status band says
     so. It is not enabled on the normal path. */
  const legacy = useQuery({ queryKey:readKey(project,'legacySnapshot'), queryFn:({signal}) => client.snapshot(false,signal), enabled:registry.isSuccess && !invalid && contextRead.isError, retry:false });
  const snapshot = !contextRead.data && legacy.data ? legacy : contextRead;
  /* The diagnostics read: explicit loading/unknown/error, never implicitly clean. */
  const health = useQuery({ queryKey:readKey(project,'health'), queryFn:({signal}) => client.health(signal), enabled:registry.isSuccess && !invalid, retry:false, staleTime:30000 });
  const documentQuery = useQuery({ queryKey:readKey(project,'doc',doc), queryFn:({signal}) => client.doc(doc,signal), enabled:!!doc && !invalid, retry:false });
  const readPage = useCallback(async(tool:string,args?:Record<string,string>,signal?:AbortSignal):Promise<Data> => {
    const key=project+'|'+locationState.route;
    try {
      const result=await client.read(tool,args,signal);
      if(!signal?.aborted) {
        setReadTimes(prev=>({...prev,[key]:new Date().toISOString()}));
        setReadErrors(prev=>({...prev,[key]:{...prev[key],[tool]:''}}));
      }
      return result;
    } catch(error) {
      if(!signal?.aborted && !(error instanceof Error && error.name==='AbortError')) setReadErrors(prev=>({...prev,[key]:{...prev[key],[tool]:error instanceof Error?error.message:String(error)}}));
      throw error;
    }
  },[client,project,locationState.route]);
  // Match legacy openDrawer: subscribe to the shared understanding read when a
  // drawer opens on a route that has not loaded it; reuse the same query/cache.
  // The top bar's parent trail reads the same model, so the read is enabled on
  // the drill-down routes too - one read, one cache entry, one source of names.
  const trailRoute=(TRAIL_ROUTES as readonly string[]).includes(locationState.route);
  /* The address itself asks for a document (a doc link, a drawer deep link). That
     request subscribes the shared outcome-context read in the first render, before
     the page snapshot answers: the drawer must not spend a second serial roundtrip
     after it opens deciding which outcome names the document, and the same query
     is then already cached when the drawer mounts. */
  const docRequested=text(locationState.params.get('doc'));
  const understanding=useQuery({queryKey:readKey(project,'understanding'),queryFn:({signal})=>readPage('understanding',undefined,signal),enabled:((!!doc||!!docRequested&&docValid(docRequested))||trailRoute)&&!invalid&&!busy,retry:false,staleTime:Infinity});
  useEffect(() => {
    const update = () => { const next=address(); setLocationState(next); setProject(next.params.get('project') || ''); setMnav(false); setDoc(''); setNotice(''); setActiveTool(null); setPalette(false); setConfirmation(null); };
    window.addEventListener('hashchange',update); return () => window.removeEventListener('hashchange',update);
  }, []);
  useEffect(() => { restorePreferences(); },[]);
  useEffect(() => { document.documentElement.dataset.railTheme=dark?'dark':'light'; document.documentElement.dataset.theme=dark?'dark':'light'; try { localStorage.setItem('ctx-rail.react-theme',theme); } catch { /* preferences only */ } },[dark,theme]);
  useEffect(() => { const media=matchMedia('(prefers-color-scheme: dark)');const change=()=>setSystemDark(media.matches);media.addEventListener('change',change);return()=>media.removeEventListener('change',change); },[]);
  useEffect(() => { try { localStorage.setItem('ctx-rail.react-sidebar',collapsed?'collapsed':'expanded'); } catch { /* preferences only */ } },[collapsed]);
  useEffect(() => { if(typeof matchMedia!=='function') return; const media=matchMedia('(max-width: 900px)');const change=()=>{setNarrow(media.matches);if(!media.matches) setMnav(false);};change();media.addEventListener?.('change',change);return()=>media.removeEventListener?.('change',change); },[]);
  useEffect(() => { main.current?.focus({preventScroll:true}); },[locationState.route]);
  useEffect(() => { setActiveTool(null); setPalette(false); setSearch(''); setConfirmation(null); setDocFull(false); },[project]);
  /* A project switch supersedes whatever refresh is still in flight: the
     answer it is waiting for belongs to a project the reader has left. */
  useEffect(() => { refreshGuard.current.begin(); },[project]);
  useEffect(() => { if(!palette) setSearch(''); },[palette]);
  const openDoc = useCallback((id:string) => {
    const metadata = snapshot.data?.project.ambiguous_doc_ids;
    if (!docValid(id) || !Array.isArray(metadata) || metadata.some(x => typeof x !== 'string') || metadata.includes(id)) { setNotice('This document source cannot be resolved uniquely. No document was opened.'); return; }
    const next=address();next.params.set('doc',id);
    location.hash=next.route+'?'+next.params;
  },[snapshot.data]);
  useEffect(() => {
    const id=locationState.params.get('doc') || '';
    if(!id || !snapshot.data) { setDoc('');return; }
    const metadata=snapshot.data.project.ambiguous_doc_ids;
    if(!docValid(id)||!Array.isArray(metadata)||metadata.some(x=>typeof x!=='string')||metadata.includes(id)) { setDoc('');setNotice('This document source cannot be resolved uniquely. No document was opened.');return; }
    setDoc(id);
  },[locationState.params,snapshot.data]);
  const navigate = useCallback((route:Route, params:Record<string,string>={}) => {
    location.hash=routeHash(route,projectRef.current,params);
  },[]);
  const togglePalette=useCallback(()=>setPalette(p=>!p),[]);
  const toggleTheme=useCallback(()=>setTheme(dark?'light':'dark'),[dark]);
  /* The `[` key and the palette item act on whichever sidebar this width shows: the drawer on a phone, the icon rail on a desktop. */
  const toggleSidebar=useCallback(()=>{ if(typeof matchMedia==='function'&&matchMedia('(max-width: 900px)').matches) setMnav(v=>!v); else setCollapsed(v=>!v); },[]);
  const dialogOpen=palette||!!doc||!!activeTool||!!confirmation;
  useRailKeyboard({navigate,togglePalette,toggleTheme,toggleSidebar,dialogOpen,paletteOpen:palette,sidebarOpen:mnav});
  const mutate = useCallback(async(tool:string,args:Record<string,unknown>,confirm=false):Promise<Data> => {
    if(writeLock.current) throw new Error('Another change is still running');
    writeLock.current=true;
    const selected=project;const ticket=++operation.current;setBusy(true);setNotice('');
    let result:Data|undefined; let error:unknown; let stale='';
    try { await queryClient.cancelQueries({queryKey:projectKey(selected)}); result=await client.call(tool,args,confirm); const refused=refusal(tool,result); /* A done that was refused semantically keeps its full envelope: the result panel renders the verification failure instead of a bare notice. */ if(refused && !(tool==='done' && typeof result.task_done==='boolean')) throw new Error(refused); } catch(e) { error=e; }
    finally {
      try {
        /* The write is followed by the one refresh: the snapshot the write
           produced and the reads that are on screen, so the view never mixes
           a new body with an old task list. */
        await refreshProject(queryClient, client, { project:selected, fresh:false, guard:refreshGuard.current, refetch:selected===projectRef.current });
      } catch(e) {
        /* The change already reached the store: a refresh failure is a stale
           view, not a failed change. It is never reported as "run again" and
           the change is never retried here. */
        if(!error) stale=e instanceof Error?e.message:String(e);
      }
      finally {
        if(selected===projectRef.current && error) setNotice(error instanceof Error?error.message:String(error));
        if(ticket===operation.current) { writeLock.current=false;setBusy(false); }
      }
    }
    if(error) throw error;
    if(stale) {
      const note=`The change was applied, but the view is stale: ${stale}. Reload to see the current state.`;
      if(selected===projectRef.current) setNotice(note);
      return {...(result??{}), refresh_failure:{stale:true, error:stale, message:note}} as Data;
    }
    return result!;
  },[busy,project,client,queryClient]);
  const requestConfirm = useCallback((tool:string,args:Record<string,unknown>,then?:(problem?:string)=>void) => {
    const [title,body]=CONFIRM_TEXT[tool]||[`Run ${tool}?`,'This tool runs commands on this machine.'];
    setConfirmation({ title, body, ok:'Run', run: () => {
      setConfirmation(null);
      void mutate(tool,args,true).then(()=>then?.(),problem=>then?.(problem instanceof Error?problem.message:String(problem)));
    }});
  },[mutate]);
  /* The one refresh (web/src/react/refresh.ts): the shell's Reload, the
     offline bar's Try again and every screen's own Reload go through it, so
     a reload always leaves the whole view - the snapshot's task list, cursor,
     claims and doctor counts included - describing one moment. A refresh a
     newer one has superseded never lands. */
  const refresh=useCallback(async(fresh=true)=>{setNotice('');try { await refreshProject(queryClient, client, { project, fresh, guard:refreshGuard.current }); } catch(e) {setNotice(e instanceof Error?e.message:String(e));} },[project,client,queryClient]);
  const route=locationState.route; const Page=Object.hasOwn(pages,route)?pages[route as Route]:null;
  /* The parent trail: the drill-down keeps its parents visible. It is derived
     from the address and the read model, so a deep link, a reload and
     Back/Forward all show the same trail - and a parent the store does not
     state is never named. */
  const trail=useMemo(()=>trailOf(route,locationState.params,crumbModel(understanding.data,snapshot.data)),[route,locationState.params,understanding.data,snapshot.data]);
  const confirmTools=Array.isArray(tools.data?.confirm)?tools.data.confirm.filter((v):v is string=>typeof v==='string'):[];
  const closeDoc=useCallback(() => { setDoc(''); const next=address();next.params.delete('doc');history.replaceState(null,'','#'+next.route+(next.params.size?'?'+next.params:''));setLocationState(next); },[]);
  const moveDocStatus=useCallback((status:string) => {
    if(!doc) return;
    const etag=text(object(object(documentQuery.data).doc).etag);
    if(!etag) return;
    void mutate('change move',{slug:doc,status,expect:etag}).catch(() => {});
  },[doc,mutate,documentQuery.data]);
  const switchProject=(id:string)=>{if(busy) return;const q=new URLSearchParams(locationState.params); if(id)q.set('project',id);else q.delete('project');location.hash=route+(q.size?'?'+q:'');};
  const projectName=text(snapshot.data?.project.name,'Context Rail');
  /* The health line: a clean board is only ever ``ok`` with a dated summary; an
     absent, still-loading or failed read says so instead of showing zeroes. */
  const healthState=health.isPending?'loading':health.isError?'error':(health.data?.state??'unknown');
  const healthDoctor=object(health.data?.doctor);
  const healthReason=health.data?.reasons?.[0]??'';
  /* A failed /api/context read is an explicit error line here, not a silent
     downgrade: the legacy read may keep the page usable, but the band names it. */
  const contextFailure=contextRead.isError?text(contextRead.error?.message,'the context read failed'):'';
  const healthLabel=contextFailure?'Project context unavailable: '+contextFailure+' (showing the legacy full read)'
    :healthState==='loading'?'Checking health…'
    :healthState==='ok'?`${text(object(healthDoctor.by_severity).error,'0')} errors, ${text(object(healthDoctor.by_severity).warning,'0')} warnings`
    :healthState==='error'?`Health unavailable: ${health.isError?health.error.message:text(health.data?.error,'the health read failed')}`
    :`Health unknown${healthReason?' — '+healthReason:''}`;
  const navItem=(r:Route)=>{const Icon=routeIcons[r]??FileText;return <button type="button" key={r} className={`sitem rail-nav-link ${route===r?'on':''}`} aria-current={route===r?'page':undefined} onClick={()=>navigate(r)}><Icon className="i" strokeWidth={1.8} size={16}/><span className="trunc">{screenTitle(snapshot.data,r,labels[r])}</span></button>;};
  /* ---------- command palette items (legacy groups: Navigate, Run a tool, Docs) ---------- */
  /* A group that shows only its first matches says so and offers the rest: a
     truncated list must never hide work without a way to reach it. */
  const NAV_LIMIT = 8;
  const NAV_LIMIT_QUERY = 6;
  const TOOL_LIMIT = 8;
  const TOOL_LIMIT_QUERY = 14;
  const DOC_LIMIT = 40;
  const query=search.trim().toLowerCase();
  const match=(...parts:unknown[])=>!query||parts.some(part=>String(part??'').toLowerCase().includes(query));
  const [expanded,setExpanded]=useState<string[]>([]);
  useEffect(()=>{setExpanded([]);},[query]);
  const expandGroup=(name:string)=>setExpanded(previous=>previous.includes(name)?previous:[...previous,name]);
  const isExpanded=(name:string)=>expanded.includes(name);
  const pagination=(group:string,shown:number,total:number,what:string):PaletteItem|undefined=>total<=shown?undefined:{ key:'more-'+group, name:'Show all '+String(total)+' '+what, sub:String(shown)+' shown', icon:<Search size={15}/>, keepOpen:true, run:()=>expandGroup(group) };
  const paletteGroups: PaletteGroup[] = (() => {
    const list: PaletteGroup[] = [];
    const pages=[...paletteRoutes].filter(r=>match(labels[r],'go to '+r)).map((r):PaletteItem => { const Icon=routeIcons[r]??FileText; return { key:'page-'+r, name:'Go to '+labels[r], icon:<Icon size={15}/>, kbd:navKeys[r]?['G',navKeys[r] as string]:undefined, run:()=>navigate(r) }; });
    const extras:PaletteItem[] = [
      { key:'theme', name:'Toggle dark mode', icon:<Sun size={15}/>, kbd:[MOD,'Shift','L'], run:toggleTheme },
      { key:'side', name:'Toggle sidebar', icon:<PanelLeft size={15}/>, kbd:['['], run:toggleSidebar },
      { key:'reload', name:'Reload from the store', icon:<RotateCcw size={15}/>, run:()=>void refresh() },
    ].filter(item=>match(item.name));
    const navAll=[...pages,...extras];
    const nav=isExpanded('Navigate')?navAll:navAll.slice(0,query?NAV_LIMIT_QUERY:NAV_LIMIT);
    if(nav.length) list.push({name:'Navigate',items:nav,more:pagination('Navigate',nav.length,navAll.length,'pages')});
    const toolAll=rows(tools.data?.tools).filter(t=>match(t.name,t.cli_name,t.help)).map((t):PaletteItem => ({ key:'tool-'+text(t.name), name:text(t.name), sub:text(t.help), icon:(t.writes||t.writes_when)?<WrenchIcon size={15}/>:<Search size={15}/>, run:()=>setActiveTool(t) }));
    const toolItems=isExpanded('Run a tool')?toolAll:toolAll.slice(0,query?TOOL_LIMIT_QUERY:TOOL_LIMIT);
    if(toolItems.length) list.push({name:'Run a tool',items:toolItems,more:pagination('Run a tool',toolItems.length,toolAll.length,'tools')});
    if(query) {
      const statuses=snapshot.data?.statuses?.length?snapshot.data.statuses:['todo','in-progress','blocked','done'];
      /* The kinds keep their order (core doc, task, scope, phase, decision, risk,
         question); the rank is the tie-breaker behind an exact match and live work. */
      const candidates:[Data,string,number][]=[
        ...rows(snapshot.data?.core_docs).map((d):[Data,string,number]=>[d,'Core doc',0]),
        ...rows(snapshot.data?.tasks).map((d):[Data,string,number]=>[d,'Task',1]),
        ...rows(snapshot.data?.scope_items).map((d):[Data,string,number]=>[d,'Scope',2]),
        ...rows(snapshot.data?.phases).map((d):[Data,string,number]=>[d,'Phase',3]),
        ...rows(snapshot.data?.decisions).map((d):[Data,string,number]=>[d,'Decision',4]),
        ...rows(snapshot.data?.risks).map((d):[Data,string,number]=>[d,'Risk',5]),
        ...rows(snapshot.data?.questions).map((d):[Data,string,number]=>[d,'Question',6]),
      ];
      const seen=new Set<string>();
      const exact=(d:Data)=>[d.id,d.title,d.question].some(value=>String(value??'').toLowerCase()===query);
      /* Live work first: a reader looking for what is happening now finds it
         before the long tail of finished and not-started records. */
      const live=(d:Data,kind:string)=>kind==='Task'&&ACTIVE_STATUSES.has(text(d.status).toLowerCase());
      const docAll=[...candidates].filter(([d])=>{const id=text(d.id);if(!id||seen.has(id))return false;seen.add(id);return true;}).filter(([d])=>match(d.id,d.title,d.question)).sort((a,b)=>Number(exact(b[0]))-Number(exact(a[0]))||Number(live(b[0],b[1]))-Number(live(a[0],a[1]))||a[2]-b[2]||text(a[0].id).localeCompare(text(b[0].id))).map(([d,kind]):PaletteItem => ({ key:'doc-'+text(d.id), name:text(d.title,text(d.question,text(d.id))), sub:kind+' · '+text(d.id), icon:d.status&&kind==='Task'?<StatusGlyph status={text(d.status)} statuses={statuses}/>:<FileText size={15}/>, run:()=>openDoc(text(d.id)) }));
      const docItems=isExpanded('Docs')?docAll:docAll.slice(0,DOC_LIMIT);
      if(docItems.length) list.push({name:'Docs',items:docItems,more:pagination('Docs',docItems.length,docAll.length,'documents')});
    }
    return list;
  })();
  const docRow=rows(snapshot.data?.tasks).find(taskRow=>text(taskRow.id)===doc);
  const docWorkflow=text(docRow?.workflow);
  const docWorkflowDoc=rows(snapshot.data?.workflows).find(workflowRow=>text(workflowRow.name)===docWorkflow);
  const docCard=object(object(documentQuery.data).card);
  const docNext=Array.isArray(docCard.next)?docCard.next.filter((state):state is string=>typeof state==='string'):[];
  const docStates=docNext.length?docNext:(Array.isArray(docWorkflowDoc?.states)?docWorkflowDoc!.states.filter((s):s is string=>typeof s==='string'):(snapshot.data?.statuses??[]));
  const docData=documentQuery.data??{};
  const docObject=Object.keys(object(docData.doc)).length?object(docData.doc):docData;
  const docType=text(docObject.type,docRow?'task':'');
  const claimRow=rows(snapshot.data?.claims).find(claim=>text(claim.task)===doc);
  const claimText=claimRow?text(claimRow.actor,text(claimRow.holder,'claimed')):'';
  const routeErrors=Object.values(readErrors[project+'|'+route]??{}).filter(Boolean);
  /* The trail of the current address. A route outside the drill-down keeps the
     single crumb it always had: the page's own name. */
  const crumbs=trail.length?trail:[{key:'page',label:Object.hasOwn(labels,route)?labels[route as Route]:'Not found',route:route as Route,params:{},current:true,hint:undefined}];
  useEffect(()=>{ document.title=(Object.hasOwn(labels,route)?labels[route as Route]:'Page not found')+' · '+projectName+' · Context Rail'; },[route,projectName]);
  return <DocumentNavigation.Provider value={openDoc}><div className={`rail-app shell ${collapsed&&!mnav?'collapsed':''} ${mnav?'mnav':''}`}><a href="#main" className="skip rail-skip" onClick={e=>{e.preventDefault();main.current?.focus();}}>Skip to content</a>
    {mnav&&<button type="button" className="side-scrim" aria-label="Close navigation" onClick={()=>setMnav(false)}/>}
    <aside className="side rail-sidebar" data-open={mnav} data-collapsed={collapsed&&!mnav} inert={narrow&&!mnav?true:undefined}><div className="side-top"><div className="row" style={{gap:2}}><div className="ws grow rail-brand"><span className="ws-logo rail-brand-logo" style={{width:22,height:22}}>{projectName[0]||'C'}</span><span className="ws-name trunc">{projectName}</span></div>{!collapsed&&<button type="button" className="ibtn ibtn-sm hide-m" aria-label="Collapse sidebar" onClick={()=>setCollapsed(true)}><PanelLeft className="i" strokeWidth={1.8} size={15}/></button>}</div></div><nav className="side-scroll" aria-label="Main navigation">{collapsed&&!mnav&&<button type="button" className="sitem" aria-label="Expand sidebar" onClick={()=>setCollapsed(false)}><PanelLeft className="i" strokeWidth={1.8} size={16}/></button>}{rows(registry.data?.projects).some(p=>p.exists&&p.id!==registry.data?.current)&&<div className="side-projects" role="group" aria-label="Projects">{!(collapsed&&!mnav)&&<div className="side-label">Projects</div>}{[{id:text(registry.data?.current),label:projectName,isCurrent:true},...rows(registry.data?.projects).filter(p=>p.exists&&p.id!==registry.data?.current).map(p=>({id:text(p.id),label:text(p.name,text(p.id)),isCurrent:false}))].map(p=>{const on=p.isCurrent?!project||project===p.id:project===p.id;return <button type="button" key={p.isCurrent?'current':p.id} className={`sitem rail-project-link ${on?'on':''}`} aria-current={on?'true':undefined} disabled={busy} title={p.id&&p.id!==p.label?p.id:undefined} onClick={()=>switchProject(p.isCurrent?'':p.id)}><Folders className="i" strokeWidth={1.8} size={16}/><span className="trunc">{p.label}</span></button>;})}</div>}{groups.map(([title,routes])=><section role="group" aria-label={title} key={title}><div className="side-label">{title}</div>{routes.map(navItem)}</section>)}</nav><div className="side-bot">{navItem('settings')}</div></aside>
    <div className="main rail-main-wrap"><header className="topbar rail-topbar"><button type="button" className="ibtn" id="mnav-btn" aria-label="Toggle navigation" aria-expanded={mnav} onClick={()=>setMnav(v=>!v)}><Menu className="i" strokeWidth={1.8} size={17}/></button><nav className="crumbs trunc" aria-label="Breadcrumb">{crumbs.map((crumb,index)=><Fragment key={crumb.key}>{index>0?<span className="sep" aria-hidden="true">/</span>:null}<button type="button" className={crumb.current?'cur':undefined} aria-current={crumb.current?'page':undefined} title={crumb.hint} onClick={crumb.current?undefined:()=>navigate(crumb.route,crumb.params)}>{crumb.label}</button></Fragment>)}</nav><span className="sp"/><span className="faint hide-m" style={{fontSize:12}}>{snapshot.data?'store v'+snapshot.data.schema:''}</span><span className="faint hide-m" data-health={contextFailure?'error':healthState} style={{fontSize:11}} title="the diagnostics read">{healthLabel}</span>{readTimes[project+'|'+route] && <span className="faint hide-m" style={{fontSize:11}} title="the time of the last read">{'as of '+new Date(readTimes[project+'|'+route]).toLocaleTimeString().replace(/\u202f/g,' ')}</span>}<button type="button" className="ibtn" disabled={busy||invalid} aria-label="Reload" onClick={()=>void refresh()}><RotateCcw className="i" strokeWidth={1.8} size={15}/></button><button type="button" className="topsearch" aria-label="Search and commands" onClick={()=>setPalette(true)}><Search className="i" strokeWidth={1.8} size={14}/><span className="lbltxt">Search or run a tool...</span><span className="kbd">{MOD}{isMac?'':' '}K</span></button></header>
    {routeErrors.length>0&&<div className="offline-bar" role="alert"><TriangleAlert size={15}/><span><b>{(Object.hasOwn(labels,route)?labels[route as Route]:route)+" could not load."}</b> {routeErrors.join(" ")}{readTimes[project+"|"+route]&&(" Last good read "+new Date(readTimes[project+"|"+route]).toLocaleTimeString()+".")}</span><span className="sp"/><button type="button" className="btn btn-sm btn-secondary" onClick={()=>void refresh()}>Try again</button></div>}
    <main id="main" ref={main} tabIndex={-1} className="content">{notice&&<div role="alert" className="offline-bar rail-error">{notice}</div>}<div className={route==='settings'?'rail-settings-wrap rail-main':route==='board'?'page flush wide rail-main':route==='atlas'?'rail-main':'page understanding rail-main'}>{invalid?<ErrorState error="This project is invalid or not registered. No project data was requested."/>:registry.isError?<ErrorState error={registry.error.message} retry={()=>void registry.refetch()}/>:snapshot.isError?<ErrorState error={snapshot.error.message} retry={()=>void snapshot.refetch()}/>:!snapshot.data?<Loading/>:Page?<Page snapshot={snapshot.data} project={project} params={locationState.params} navigate={navigate} openDoc={openDoc} read={readPage} mutate={mutate} busy={busy} refresh={()=>refresh()} request={(path,init)=>client.request(path,init)} theme={theme} setTheme={setTheme}/>:<><PageHeading title="Page not found" description="This address does not name a screen in Context Rail." action={<Button onClick={()=>navigate('summary')}>Go to Summary</Button>}/></>}</div></main></div>
    {doc&&<DocDrawer id={doc} type={docType} claim={claimText} full={docFull} onToggleFull={()=>setDocFull(value=>!value)} onClose={closeDoc}><DocumentContent id={doc} data={docData} task={rows(snapshot.data?.tasks).some(taskRow=>text(taskRow.id)===doc)} context={understanding.data} contextLoading={understanding.isFetching} contextError={understanding.isError?understanding.error.message:''} status={text(docRow?.status)} statuses={docStates} busy={busy} loading={documentQuery.isPending} error={documentQuery.isError?documentQuery.error.message:''} navigate={navigate} openDoc={openDoc} onMoveStatus={moveDocStatus}/></DocDrawer>}
    {activeTool&&<ToolForm tool={activeTool} confirmTools={confirmTools} busy={busy} hidden={!!confirmation} onClose={()=>setActiveTool(null)} mutate={mutate} onConfirm={requestConfirm}/>}
    {confirmation&&<ConfirmDialog title={confirmation.title} body={confirmation.body} ok={confirmation.ok} busy={busy} onRun={confirmation.run} onClose={()=>setConfirmation(null)}/>}
    {palette&&<CommandPalette query={search} onQueryChange={setSearch} groups={paletteGroups} onClose={()=>setPalette(false)} onRun={item=>{if(!item.keepOpen) setPalette(false);item.run();}}/>}
    <div className="sr-only" role="status" aria-live="polite">{busy?'Applying change and reconciling the store...':''}</div>
  </div></DocumentNavigation.Provider>;
}