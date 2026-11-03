import { createContext } from 'react';
import { docValid } from './transport';
export const DocumentNavigation = createContext<((id: string) => void) | null>(null);
type Node = { type: string; value?: string; url?: string; children?: Node[] };
/** Transform only prose AST text; code, existing links and HTML never gain wiki actions. */
export function remarkWikiLinks() {
  return (tree: Node) => {
    const walk = (node: Node) => {
      if (!node.children || ['link','linkReference','code','inlineCode','html'].includes(node.type)) return;
      const next: Node[] = [];
      for (const child of node.children) {
        if (child.type !== 'text' || !child.value) { walk(child); next.push(child); continue; }
        let last = 0;
        for (const match of child.value.matchAll(/\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]/g)) {
          const id = match[1].trim();
          if (!docValid(id)) continue;
          const index = match.index ?? 0;
          if (index > last) next.push({ type:'text', value:child.value.slice(last,index) });
          next.push({ type:'link', url:'#rail-doc/'+encodeURIComponent(id), children:[{type:'text',value:(match[2]||id).trim()}] });
          last = index + match[0].length;
        }
        if (last === 0) next.push(child);
        else if (last < child.value.length) next.push({type:'text',value:child.value.slice(last)});
      }
      node.children = next;
    };
    walk(tree);
  };
}
