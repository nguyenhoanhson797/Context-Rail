import {Fragment} from 'react';
import {object,text,type Data,type PageProps} from './contracts';
import {Button,Markdown} from './ui';
import {docValid} from './transport';
import {Layers} from './icons';
import {StatusGlyph} from './overlays';
const titleCase = (id:string) => id.replace(/-/g,' ').replace(/(^|\s)\w/g,c=>c.toUpperCase());
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
export function DocumentContent({id,data,task,bodyOnly=false,status='',statuses=[],busy=false,loading=false,error='',navigate,openDoc,onMoveStatus}:{id:string;data:Data;task:boolean;bodyOnly?:boolean;context:unknown;contextLoading?:boolean;contextError?:string;status?:string;statuses?:string[];busy?:boolean;loading?:boolean;error?:string;navigate:PageProps['navigate'];openDoc:PageProps['openDoc'];onMoveStatus?:(status:string)=>void}) {
 const doc=Object.keys(object(data.doc)).length?object(data.doc):data;
 const hasDoc=Object.keys(doc).length>0;
 const title=text(doc.title,id);
 const fm=Object.entries(object(doc.frontmatter)).filter(([key])=>!['title','type'].includes(key));
 const states=statuses.length?statuses:['todo','in-progress','blocked','done'];
 const card=object(data.card);
 const cardFocus=object(card.focus);
 const cardActions=Array.isArray(card.actions)?card.actions.filter((entry):entry is string=>typeof entry==='string'):[];
 const presetLabel=text(card.preset_label);
 return <>{!bodyOnly&&<><h1 id="d-h" style={{fontSize:20,margin:'0 0 6px'}}>{title}</h1>
 {presetLabel||text(cardFocus.slug)||cardActions.length?<p className="faint" style={{fontSize:12,margin:'0 0 8px'}} data-operating="card">
   {presetLabel?<span>{presetLabel}</span>:null}
   {text(cardFocus.slug)?<span>{' · focus '}<button type="button" className="link" onClick={()=>openDoc(text(cardFocus.slug))}>{text(cardFocus.title,text(cardFocus.slug))}</button></span>:null}
   {cardActions.length?<span>{' · actions ' + cardActions.join(', ')}</span>:null}
 </p>:null}
 {task&&<div className="row" style={{gap:6,flexWrap:'wrap',margin:'8px 0 14px'}}>{states.map(state=><Button key={state} className={status===state?'btn-sm rail-status-current':'btn-sm'} variant={status===state?'primary':'secondary'} disabled={busy||status===state} aria-current={status===state?'true':undefined} onClick={()=>onMoveStatus?.(state)}><StatusGlyph status={state} statuses={states}/>{titleCase(state)}</Button>)}</div>}
 {task&&<Button className="btn-sm btn-secondary" style={{marginBottom:12}} onClick={()=>navigate('board')}><Layers size={14}/>Open the board</Button>}
 </>}
 {error?<div className="alert" role="alert"><span>{error}</span></div>:null}
 {loading?<p className="faint" role="status">Loading...</p>:null}
 {fm.length>0&&<dl className="kv" style={{marginTop:8}}>{fm.map(([key,value])=><Fragment key={key}><dt>{key}</dt><dd className="trunc" style={{whiteSpace:'normal',overflowWrap:'anywhere'}}><LinkedValue value={value} openDoc={openDoc}/></dd></Fragment>)}</dl>}
 {hasDoc?<div className="dsec"><div className="dsec-h"><h3>Body</h3></div>{doc.body?<Markdown>{String(doc.body)}</Markdown>:<div className="md"><span className="faint">Empty.</span></div>}</div>:null}
 </>;
}
