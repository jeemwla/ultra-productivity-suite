const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');
test('a taskbar button owns pointer release even if native placement moves',()=>{
 const source=html.slice(html.indexOf('function beginStripPointer(event){'),html.indexOf('$("strip").addEventListener("pointerdown"'));
 const captured=[],button={setPointerCapture:id=>captured.push(id)};
 const context=vm.createContext({});vm.runInContext('let stripPointerDown=false;\n'+source,context);
 context.beginStripPointer({pointerId:17,target:{closest:()=>button}});
 assert.deepEqual(captured,[17]);assert.equal(vm.runInContext('stripPointerDown',context),true);
});
test('a failed Dock render retries and never counts as a successful frame',async()=>{
 const source=html.slice(html.indexOf('async function poll(){'),html.indexOf('/* ---------------- Update dot'));
 const context=vm.createContext({JSON,document:{body:{classList:{toggle:()=>{}}}},renderCalls:0,reports:0,invoke:async()=>({organize:true,windows:[{hwnd:42,category:'Tools',active:false,title:'Example'}]}),
  render:()=>{context.renderCalls++;if(context.renderCalls===1)throw new Error('fixture render failure');},report:()=>context.reports++});
 vm.runInContext("let state={},sig='',stripPointerDown=false;\n"+source,context);
 await context.poll();assert.equal(context.renderCalls,1);assert.equal(context.reports,0);assert.equal(vm.runInContext('sig',context),'');
 await context.poll();assert.equal(context.renderCalls,2);assert.notEqual(vm.runInContext('sig',context),'');
 await context.poll();assert.equal(context.renderCalls,2);assert.equal(context.reports,1);
});
test('polling during a pointer gesture preserves the clicked button and its old render acknowledgement',async()=>{
 const source=html.slice(html.indexOf('async function poll(){'),html.indexOf('/* ---------------- Update dot'));
 const context=vm.createContext({JSON,document:{body:{classList:{toggle:()=>{}}}},renderCalls:0,reports:0,
  invoke:async()=>({organize:true,windows:[{hwnd:42,category:'Tools',active:true,title:'New title'}],layout:{generation:'new'}}),
  render:()=>context.renderCalls++,report:()=>context.reports++});
 vm.runInContext("let state={layout:{generation:'old'}},sig='',stripPointerDown=true;\n"+source,context);
 await context.poll();assert.equal(context.renderCalls,0,'a live pointer target must not be replaced');
 assert.equal(context.reports,0,'new generation has not rendered');
 assert.equal(vm.runInContext('state.layout.generation',context),'old');
 vm.runInContext('stripPointerDown=false',context);await context.poll();
 assert.equal(context.renderCalls,1);assert.equal(vm.runInContext('state.layout.generation',context),'new');
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


test('a menu cannot resize or lift the persistent taskbar strip',()=>{
 const source=html.slice(html.indexOf('function report(){'),html.indexOf('function setOpen(cat){'));
 const calls=[], popup={hidden:false,getBoundingClientRect:()=>({width:520,height:152})};
 const context=vm.createContext({$:id=>id==='dock-menu'?popup:{scrollWidth:90},invoke:(name,args)=>calls.push({name,args}),Math,room:()=>800});
 vm.runInContext('let lastDiagnosticEpoch=0,open="__menu",pick=null,baseW=90,state={layout:{width:90}},shownHwnds=[42];\n'+source,context);
 context.report();
 assert.equal(calls[0].args.width,98);
 assert.equal(calls[0].args.height,48,'menus live in an independent native popup');
 assert.deepEqual([...calls[0].args.rendered],[42]);
 popup.hidden=true; context.report();
 assert.equal(calls[1].args.height,48);
 assert.equal(calls[1].args.width,98);
});
test('paired strips report the partition generation and diagnostics only once per request',()=>{
 const source=html.slice(html.indexOf('function report(){'),html.indexOf('function setOpen(cat){'));
 const calls=[], icon={dataset:{exe:'example.exe'},naturalWidth:128,naturalHeight:128,
  getBoundingClientRect:()=>({width:24,height:24}),closest:()=>null};
 const strip={scrollWidth:192,querySelectorAll:()=>[icon]};
 const context=vm.createContext({$:()=>strip,invoke:(name,args)=>calls.push(args),Math,room:()=>192,window:{devicePixelRatio:2}});
 vm.runInContext('let lastDiagnosticEpoch=0,open=null,pick=null,baseW=0,shownHwnds=[42],state={diagnostic_epoch:1,layout:{centered:true,generation:"11731673731704996325",width:192}};\n'+source,context);
 context.report();context.report();
 assert.equal(calls[0].width,192);assert.equal(calls[0].generation,"11731673731704996325");
 assert.equal(calls[0].diagnostics.icons[0].width,24);
 assert.equal(calls[0].diagnostics.dpr,2);
 assert.equal(calls[1].diagnostics,undefined);
});
