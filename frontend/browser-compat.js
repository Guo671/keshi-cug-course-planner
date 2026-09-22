"use strict";
// Loaded before application scripts so a failed bundle cannot claim successful startup.
window.keshiUiFailed=false;
window.addEventListener('error',event=>{
  if(event.error || event.target?.tagName==='SCRIPT')window.keshiUiFailed=true;
},true);
window.addEventListener('unhandledrejection',()=>{window.keshiUiFailed=true;});
// The fragment never travels in HTTP request logs.
document.addEventListener('DOMContentLoaded',()=>{
  const token=new URLSearchParams(location.hash.slice(1)).get('keshi-launch');
  if(!token || !/^[A-Za-z0-9_-]{40,64}$/.test(token))return;
  history.replaceState(null,'',location.pathname+location.search);
  let sent=false;
  const acknowledge=()=>{
    if(sent || !window.keshiUiReady || window.keshiUiFailed)return;
    sent=true;
    fetch('/_keshi/browser-ready',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token})}).catch(()=>{});
  };
  window.addEventListener('keshi-ui-ready',acknowledge,{once:true});
  acknowledge();
});
