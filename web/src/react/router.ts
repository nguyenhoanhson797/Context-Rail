import { type Route } from './contracts';
import { docValid } from './transport';
const ROUTES = new Set(['summary','now','board','roadmap','scope','catalog','decisions','doctor','pack','settings','timeline','workflow','execution','overview','atlas','outcomes','traceability','impact','lens','graph','rules','activity','portfolio']);
const ALIASES: Record<string,string> = { home:'summary',tasks:'board',health:'doctor' };
const PARAMS = new Set(['project','sec','alt','task','focus','phase','workflow','depth','types','fields','all','doc','goal','item','files','since','limit','correction','overlay','lens','attention','record']);
export function parseAddress(hash: string): {route: string; params: URLSearchParams} {
  const raw=hash.replace(/^#/,'');const cut=raw.indexOf('?');const head=(cut<0?raw:raw.slice(0,cut)).trim();
  const input=new URLSearchParams(cut<0?'':raw.slice(cut+1));const params=new URLSearchParams();
  for(const [key,value] of input) {
    if(!PARAMS.has(key)) continue;
    // Never discard an invalid project and silently fall back to the launch store.
    if(key==='project') {params.set(key,value);continue;}
    if(value.length>(key==='files'?8000:200)||/\p{Cc}/u.test(value)) continue;
    if(['doc','task','focus','phase'].includes(key)&&!docValid(value)) continue;
    if(key==='sec'&&!['appearance','shortcuts','config'].includes(value)) continue;
    if(key==='depth'&&!['1','2'].includes(value)) continue;
    if(key==='limit'&&(!/^[1-9]\d{0,3}$/.test(value)||Number(value)>1000)) continue;
    params.set(key,value);
  }
  if(head.startsWith('doc/')) {
    let id=head.slice(4);try {id=decodeURIComponent(id);} catch {return {route:'notfound',params};}
    if(!docValid(id))return {route:'notfound',params};params.set('doc',id);return {route:'summary',params};
  }
  const [name,...rest]=head.split('/');
  /* The bare address is the owner's layer: the Summary. */
  const route=!name?'summary':Object.hasOwn(ALIASES,name)?ALIASES[name]:name;
  if(!ROUTES.has(route))return {route:'notfound',params};
  /* A sub-route of the Atlas is its altitude; one the page does not have is an address the app does not know. */
  if(route==='atlas'&&rest.length) {
    if(rest.length>1||!ALT_IDS.has(rest[0]))return {route:'notfound',params};
    if(rest[0]==='cover')params.delete('alt');else params.set('alt',rest[0]);
  }
  return {route,params};
}
export function routeHash(route:Route, project:string, values:Record<string,string>={}):string {
  const params=new URLSearchParams(values);
  if(!Object.hasOwn(values,'project')&&project)params.set('project',project);
  if(params.get('project')==='')params.delete('project');
  /* The Cover is the bare address; the other altitudes are a path segment (#atlas/map). */
  const alt=route==='atlas'?params.get('alt'):'';
  if(alt){params.delete('alt');}
  const seg=alt&&ALT_IDS.has(alt)&&alt!=='cover'?'/'+alt:'';
  return '#'+route+seg+(params.size?'?'+params:'');
}
/* The Atlas's four altitudes (web/src/core/constants.js ATLAS_ALTS). */
const ALT_IDS = new Set(['cover','map','consequences','records']);