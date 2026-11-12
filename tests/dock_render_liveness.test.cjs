const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');
test('a failed Dock render retries and never counts as a successful frame',async()=>{
 const source=html.slice(html.indexOf('async function poll(){'),html.indexOf('/* ---------------- Update dot'));
 const context=vm.createContext({JSON,renderCalls:0,reports:0,invoke:async()=>({organize:true,windows:[{hwnd:42,category:'Tools',active:false,title:'Example'}]}),
  render:()=>{context.renderCalls++;if(context.renderCalls===1)throw new Error('fixture render failure');},report:()=>context.reports++});
 vm.runInContext("let state={},sig='';\n"+source,context);
 await context.poll();assert.equal(context.renderCalls,1);assert.equal(context.reports,0);assert.equal(vm.runInContext('sig',context),'');
 await context.poll();assert.equal(context.renderCalls,2);assert.notEqual(vm.runInContext('sig',context),'');
 await context.poll();assert.equal(context.renderCalls,2);assert.equal(context.reports,1);
});
test('rapid folder close cannot be undone by a stale animation frame',()=>{
 const source=html.slice(html.indexOf('function setOpen(cat){'),html.indexOf('$("strip").addEventListener("click"'));
 const frames=[];
 const button={classList:{contains:name=>name==='grp',remove:()=>{}},attrs:{},setAttribute(k,v){this.attrs[k]=v;}};
 const tray={style:{width:'0px'},attrs:{},inert:true,classList:{remove:()=>{},add:()=>{}},setAttribute(k,v){this.attrs[k]=v;},querySelector:()=>({scrollWidth:80})};
 const group={dataset:{cat:'Tools'},isConnected:true,querySelector:s=>s==='.tray'?tray:button};
 const strip={scrollWidth:44,querySelectorAll:()=>[group]};
 const context=vm.createContext({$:()=>strip,invoke:()=>{},requestAnimationFrame:cb=>frames.push(cb),setTimeout:()=>{},report:()=>{},render:()=>{},applyFocus:()=>{},room:()=>Infinity});
 vm.runInContext("let open=null,pick=null;\n"+source,context);
 context.setOpen('Tools');assert.equal(tray.inert,false);assert.equal(button.attrs['aria-label'],'Collapse Tools');
 context.setOpen(null);assert.equal(tray.inert,true);assert.equal(button.attrs['aria-label'],'Expand Tools');
 frames.forEach(cb=>cb());assert.equal(tray.style.width,'0px');assert.equal(vm.runInContext('open',context),null);
});
