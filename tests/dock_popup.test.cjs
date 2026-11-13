const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/dock_menu.html'),'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
async function fixture(initial=7){
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',className:'',disabled:false,attrs:{},setAttribute(k,v){this.attrs[k]=v;},focus(){}});return nodes.get(id);};
  const calls=[];
  let save=async()=>{};
  const document={getElementById:node,addEventListener(){},querySelectorAll(){return [];}};
  const invoke=async(name,args)=>{
    calls.push([name,args]);
    if(name==='dock_settings_get')return {cap:initial,cap_max:30,organize:true};
    if(name==='get_windows')return {layout:{items:Array.from({length:4},()=>({folder:false}))}};
    if(name==='edition_info')return {edition:'Dev'};
    if(name==='set_dock_cap')return save(args.cap);
  };
  const context=vm.createContext({window:{__TAURI__:{core:{invoke}}},document,console,setInterval(){}});
  vm.runInContext(source,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {node,calls,setSave(fn){save=fn;},context};
}
test('compact popup preserves the saved count and steps without a long submenu',async()=>{
  const f=await fixture();
  assert.equal(f.node('count').textContent,7);
  assert.equal(f.node('title').textContent,'Ultra Dock (Dev)');
  await f.node('less').onclick();assert.equal(f.node('count').textContent,6);
  await f.node('more').onclick();assert.equal(f.node('count').textContent,7);
  await f.node('auto').onclick();assert.equal(f.node('count').textContent,4);
  assert.equal(f.node('auto').attrs['aria-pressed'],'true');
  await f.node('more').onclick();assert.equal(f.node('count').textContent,5);
  assert.deepEqual(f.calls.filter(c=>c[0]==='set_dock_cap').map(c=>c[1].cap),[6,7,null,5]);
  assert.doesNotMatch(html,/<select\b|<option\b/);
});
test('pending count writes cannot race and failed writes restore the saved value',async()=>{
  const f=await fixture();let reject;
  f.setSave(()=>new Promise((_,r)=>reject=r));
  const pending=f.node('more').onclick();
  assert.equal(f.node('more').disabled,true);
  await f.node('less').onclick();
  assert.equal(f.calls.filter(c=>c[0]==='set_dock_cap').length,1);
  reject(new Error('fixture write failure'));await pending;
  assert.equal(f.node('count').textContent,7);
  assert.match(f.node('status').textContent,/Could not save/);
  assert.equal(f.node('more').disabled,false);
});
test('zero and maximum stay reachable with bounded controls',async()=>{
  for(const [initial,id,expected,disabled] of [[1,'less',0,'less'],[29,'more',30,'more']]){
    const f=await fixture(initial);await f.node(id).onclick();
    assert.equal(f.node('count').textContent,expected);
    assert.equal(f.node(disabled).disabled,true);
    assert.equal(f.calls.at(-1)[1].cap,expected);
  }
});
test('rapid waffle clicks issue one open request and release the gate after completion',async()=>{
  const strip=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');
  const handler=strip.slice(strip.indexOf('let menuOpening = false;'),strip.indexOf('$("strip").addEventListener("click"'));
  const calls=[];let finish;
  const context=vm.createContext({closeAll(){},invoke:async name=>{calls.push(name);await new Promise(resolve=>finish=resolve);}});
  vm.runInContext(handler,context);
  const event={target:{closest:selector=>selector==='.grp,.more'?{dataset:{cat:'__menu'}}:null}};
  const first=context.handleDockClick(event);
  await context.handleDockClick(event);
  assert.deepEqual(calls,['show_dock_menu']);
  finish();await first;
  const next=context.handleDockClick(event);
  assert.equal(calls.length,2);finish();await next;
});
test('failed app activation keeps its folder open and reports the failure',async()=>{
  const strip=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');
  const handler=strip.slice(strip.indexOf('let menuOpening = false;'),strip.indexOf('$("strip").addEventListener("click"'));
  const status={textContent:''};let closes=0;
  const button={dataset:{h:'42'},classList:{add(){}},hasAttribute:()=>false};
  const context=vm.createContext({invoke:async()=>false,$:()=>status,setTimeout(){closes++;}});
  vm.runInContext(handler,context);
  await context.handleDockClick({target:{closest:s=>s==='.win'?button:null}});
  assert.equal(closes,0);
  assert.match(status.textContent,/could not switch/);
});
