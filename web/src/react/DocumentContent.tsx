import {Fragment} from 'react';
import {object,type Data} from './contracts';
import {Markdown} from './ui';
import {docValid} from './transport';
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
/* The document body is read-only. Work state moves through the gateway as an
   agent action; this component has no status control and no write callback. */
export function DocumentContent({id,data,loading=false,error='',openDoc}:{id:string;data:Data;loading?:boolean;error?:string;openDoc:(id:string)=>void}) {
 const doc=Object.keys(object(data.doc)).length?object(data.doc):data;
 const hasDoc=Object.keys(doc).length>0;
 const fm=Object.entries(object(doc.frontmatter)).filter(([key])=>!['title','type'].includes(key));
 return <>{error?<div className="alert" role="alert"><span>{error}</span></div>:null}
 {loading?<p className="faint" role="status">Loading...</p>:null}
 {fm.length>0&&<dl className="kv" style={{marginTop:8}}>{fm.map(([key,value])=><Fragment key={key}><dt>{key}</dt><dd className="trunc" style={{whiteSpace:'normal',overflowWrap:'anywhere'}}><LinkedValue value={value} openDoc={openDoc}/></dd></Fragment>)}</dl>}
 {hasDoc?<div className="dsec"><div className="dsec-h"><h3>Body</h3></div>{doc.body?<Markdown>{String(doc.body)}</Markdown>:<div className="md"><span className="faint">Empty.</span></div>}</div>:null}
 <span className="sr-only">{id}</span>
 </>;
}
