import { useEffect, useRef } from 'react';
import type { Route } from './contracts';
const G_KEYS: Record<string,Route> = {b:'board',r:'roadmap',d:'decisions'};
export function useRailKeyboard({navigate,togglePalette,toggleTheme,toggleSidebar,dialogOpen,paletteOpen,sidebarOpen=false}:{navigate:(route:Route,params?:Record<string,string>)=>void;togglePalette:()=>void;toggleTheme:()=>void;toggleSidebar:()=>void;dialogOpen:boolean;paletteOpen:boolean;sidebarOpen?:boolean}) {
  const pending=useRef(0);
  useEffect(()=>{
    const keydown=(event:KeyboardEvent)=>{
      if(event.defaultPrevented||event.isComposing||event.keyCode===229)return;
      const target=event.target instanceof Element?event.target:null;
      const typing=!!target?.closest('input,textarea,select,[contenteditable="true"],[contenteditable=""]');
      const mod=event.ctrlKey||event.metaKey;
      const key=event.key.toLowerCase();
      if(mod&&key==='k'&&!event.altKey) {event.preventDefault();pending.current=0;if(!dialogOpen||paletteOpen)togglePalette();return;}
      if(mod&&event.shiftKey&&key==='l'&&!event.altKey) {event.preventDefault();pending.current=0;toggleTheme();return;}
      if(event.key==='Escape'&&sidebarOpen&&!dialogOpen&&!target?.closest('[role="dialog"]')) {event.preventDefault();toggleSidebar();return;}
      if(typing||mod||event.altKey||dialogOpen||target?.closest('[role="dialog"]')){pending.current=0;return;}
      if(pending.current&&Date.now()-pending.current<1200){pending.current=0;const route=G_KEYS[key];if(route){event.preventDefault();navigate(route);}return;}
      pending.current=0;
      if(key==='g'){pending.current=Date.now();return;}
      if(event.key==='?'){event.preventDefault();navigate('settings',{sec:'shortcuts'});return;}
      if(event.key==='['){event.preventDefault();toggleSidebar();}
    };
    window.addEventListener('keydown',keydown);return()=>window.removeEventListener('keydown',keydown);
  },[navigate,togglePalette,toggleTheme,toggleSidebar,dialogOpen,paletteOpen,sidebarOpen]);
}
