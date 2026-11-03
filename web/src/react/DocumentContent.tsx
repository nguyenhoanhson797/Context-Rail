import {Fragment} from 'react';
import {object,rows,text,type Data,type PageProps} from './contracts';
import {Button,Markdown} from './ui';
import {docValid} from './transport';
import {FileText,Layers,Target} from './icons';
import {StatusGlyph} from './overlays';
const titleCase = (id:string) => id.replace(/-/g,' ').replace(/(^|\s)\w/g,c=>c.toUpperCase());
const linkName = (v:unknown) => typeof v==='string'?v.trim().replace(/^\[\[/,'').replace(/\]\]$/,'').split('|')[0].trim():'';
/* A front-matter value reads as prose ("a, b" / "phase: x"), not as JSON with quotes and brackets around the link buttons. */
const plainValue = (value:unknown):string => {
 if(value===null||value===undefined)return '';
 if(typeof value==='string')return value;
 if(Array.isArray(value))return value.map(plainValue).filter(Boolean).join(', ');
 if(typeof value==='object')return Object.entries(value as Data).map(([key,entry])=>key+': '+plainValue(entry)).join(', ');
 return String(value);
};
export function LinkedValue({value,openDoc}:{value:unknown;openDoc:(id:string)=>void}) {
 const body=plainValue(value);
 const parts=[];let last=0;
 for(const match of body.matchAll(/\[\[([^\]\n|#]+)(?:[#|][^\]\n]*)?\]\]/g)) {
  const id=match[1].trim();const index=match.index??0;
  parts.push(body.slice(last,index));parts.push(docValid(id)?<button key={index} type="button" className="link" onClick={()=>openDoc(id)}>{id}</button>:match[0]);last=index+match[0].length;
 }
 parts.push(body.slice(last));return <>{parts}</>;
}
/* A source reference: opens the doc by its internal id, never an href. */
function SourceRef({source,openDoc}:{source:unknown;openDoc:(id:string)=>void}) {
 const src=object(source);const id=text(src.id);
 if(!id||!docValid(id))return null;
 const line=src.line_start!==null&&src.line_start!==undefined?'L'+text(src.line_start)+(src.line_end!==null&&src.line_end!==undefined&&src.line_end!==src.line_start?'-'+text(src.line_end):''):'';
 const where=[text(src.section).replace(/^#+\s*/,'').trim(),line].filter(Boolean).join(' ');
 const tip=(text(src.path)||id)+(where?' - '+where:'');
 return <button type="button" className="src-ref" title={tip} onClick={()=>openDoc(id)}><FileText size={12}/><span className="src-id">{id}</span>{where?<span className="src-loc">{where}</span>:null}</button>;
}
/* Why the current understanding model names this id: a task of a goal, a scope item, or a requirement. */
function namesDoc(goal:Data,id:string) {
 if(rows(goal.tasks).some(task=>text(task.id)===id))return 'a task of';
 if(rows(goal.items).some(item=>text(item.id)===id))return 'it is a scope item of';
 for(const item of rows(goal.items)) if(rows(item.requirements).some(requirement=>linkName(requirement)===id))return 'a requirement names it';
 return '';
}
export function DocumentContent({id,data,task,context,contextLoading=false,contextError='',status='',statuses=[],busy=false,loading=false,error='',navigate,openDoc,onMoveStatus}:{id:string;data:Data;task:boolean;context:unknown;contextLoading?:boolean;contextError?:string;status?:string;statuses?:string[];busy?:boolean;loading?:boolean;error?:string;navigate:PageProps['navigate'];openDoc:PageProps['openDoc'];onMoveStatus?:(status:string)=>void}) {
 const doc=Object.keys(object(data.doc)).length?object(data.doc):data;
 const hasDoc=Object.keys(doc).length>0;
 const title=text(doc.title,id);
 const fm=Object.entries(object(doc.frontmatter)).filter(([key])=>!['title','type'].includes(key));
 const goals=rows(object(context).goals).map(goal=>({goal,why:namesDoc(goal,id)})).filter(entry=>entry.why&&docValid(text(entry.goal.id)));
 const states=statuses.length?statuses:['todo','in-progress','blocked','done'];
 return <><h1 id="d-h" style={{fontSize:20,margin:'0 0 6px'}}>{title}</h1>
 {task&&<div className="row" style={{gap:6,flexWrap:'wrap',margin:'8px 0 14px'}}>{states.map(state=><Button key={state} className={status===state?'btn-sm rail-status-current':'btn-sm'} variant={status===state?'primary':'secondary'} disabled={busy||status===state} aria-current={status===state?'true':undefined} onClick={()=>onMoveStatus?.(state)}><StatusGlyph status={state} statuses={states}/>{titleCase(state)}</Button>)}</div>}
 {task&&<Button className="btn-sm btn-secondary" style={{marginBottom:12}} onClick={()=>navigate('board')}><Layers size={14}/>Open the board</Button>}
 {error?<div className="alert" role="alert"><span>{error}</span></div>:null}
 {loading?<p className="faint" role="status">Loading...</p>:null}
 {fm.length>0&&<dl className="kv" style={{marginTop:8}}>{fm.map(([key,value])=><Fragment key={key}><dt>{key}</dt><dd className="trunc" style={{whiteSpace:'normal',overflowWrap:'anywhere'}}><LinkedValue value={value} openDoc={openDoc}/></dd></Fragment>)}</dl>}
 {hasDoc?<div className="dsec"><div className="dsec-h"><h3>Body</h3></div>{doc.body?<Markdown>{String(doc.body)}</Markdown>:<div className="md"><span className="faint">Empty.</span></div>}</div>:null}
 </>;
}
