const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/dock.html'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function page(cap=7){
  const nodes=new Map(), requests=[], messages=[];
  const $=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:false,attrs:{},setAttribute(k,v){this.attrs[k]=v;}});return nodes.get(id);};
  const c=vm.createContext({$,Promise,Number,Math,String,document:{activeElement:null},
    S:{cap,cap_max:30,apps:Array(12).fill({}),individual_apps:4,config:{keep:['saved']}},
    autosave:{revision:0},dirty:false,editing:false,dragging:null,
    status:(...args)=>messages.push(args),renderApps(){},renderCats(){},renderKeep(){},renderAll(){},
    invoke:(command,args)=>new Promise((resolve,reject)=>requests.push({command,args,resolve,reject}))});
  vm.runInContext(html.slice(html.indexOf('// Cap actions run'),html.indexOf('/* ---------- saving')),c);
  vm.runInContext('function renderTop(){renderCap();}',c);
  vm.runInContext(html.slice(html.indexOf('let refreshSequence ='),html.indexOf('/* ---------- events')),c);
  vm.runInContext(html.match(/\$\("cap-minus"\)\.onclick[^\n]+/)[0]+'\n'+html.match(/\$\("cap-plus"\)\.onclick[^\n]+/)[0]+'\n'+html.match(/\$\("cap-auto"\)\.onclick[^\n]+/)[0],c);
  c.renderCap();
  return {c,$,requests,messages,click:id=>$(id).onclick()};
}
test('compact accessible markup replaces only the count dropdown',()=>{
  assert.doesNotMatch(html,/<select id="cap"/);
  assert.match(html,/role="group" aria-labelledby="cap-label" aria-describedby="cap-help"/);
  assert.match(html,/<output id="cap-count" aria-live="polite" aria-atomic="true"/);
  assert.match(html,/<select data-act="cat"/);
});
test('saved 7 survives render and Automatic resets without toggling to a manual count',async()=>{
  const p=page();assert.equal(p.$('cap-count').textContent,'7 apps');assert.equal(p.requests.length,0);
  let done=p.click('cap-auto');await tick();assert.equal(p.requests[0].args.cap,null);p.requests[0].resolve();await done;
  assert.equal(p.$('cap-auto').attrs['aria-pressed'],'true');
  assert.equal(p.$('cap-auto').disabled,true);
  await p.click('cap-auto');assert.equal(p.requests.length,1);assert.equal(p.c.S.cap,null);
  p.c.S.individual_apps=2;
  done=p.click('cap-plus');await tick();assert.equal(p.requests[1].args.cap,3);p.requests[1].resolve();await done;
  assert.equal(p.c.S.cap,3);assert.equal(p.$('cap-auto').disabled,false);
});
test('rapid clicks serialize writes and polling cannot replace the in-flight cap',async()=>{
  const p=page(),a=p.click('cap-plus'),b=p.click('cap-plus'),d=p.click('cap-minus');await tick();
  assert.equal(p.requests.length,1);assert.equal(p.requests[0].args.cap,8);
  const poll=p.c.refresh(true);await tick();p.requests[1].resolve({cap:7,config:{}});await poll;
  assert.equal(p.c.S.cap,8);
  p.requests[0].resolve();await a;await tick();assert.equal(p.requests[2].args.cap,9);
  p.requests[2].resolve();await b;await tick();assert.equal(p.requests[3].args.cap,8);
  p.requests[3].resolve();await d;assert.equal(p.c.S.cap,8);
});
test('failure refresh completes before queued action uses recovered persisted value',async()=>{
  const p=page(),a=p.click('cap-plus'),b=p.click('cap-plus');await tick();
  p.requests[0].reject(Error('write failed'));await tick();
  assert.equal(p.requests[1].command,'dock_settings_get');assert.equal(p.requests.length,2);
  assert.equal(p.c.S.cap,7);assert.match(p.messages[0][0],/Could not save app count/);
  p.requests[1].resolve({cap:5,config:{keep:[]}});await a;await tick();
  assert.equal(p.requests[2].args.cap,6);assert.deepEqual(p.c.S.config.keep,['saved']);
  p.requests[2].resolve();await b;assert.equal(p.c.S.cap,6);
});
test('bounds disable buttons and queued overshoot never persists out-of-range values',async()=>{
  for(const [start,id,bound] of [[0,'cap-minus',0],[30,'cap-plus',30]]){
    const p=page(start);assert.equal(p.$(id).disabled,true);await p.click(id);assert.equal(p.requests.length,0);assert.equal(p.c.S.cap,bound);
  }
  const p=page(29),a=p.click('cap-plus'),b=p.click('cap-plus');await tick();
  p.requests[0].resolve();await Promise.all([a,b]);assert.equal(p.requests.length,1);assert.equal(p.c.S.cap,30);assert.equal(p.$('cap-plus').disabled,true);
});
test('Automatic steps use actual solo count and failed refresh leaves queue usable',async()=>{
  const p=page(null),a=p.click('cap-minus');await tick();assert.equal(p.requests[0].args.cap,3);
  p.requests[0].reject(Error('offline'));await tick();p.requests[1].reject(Error('offline'));await a;
  assert.equal(p.c.S.cap,null);
  const b=p.click('cap-plus');await tick();assert.equal(p.requests[2].args.cap,5);
  p.requests[2].resolve();await b;assert.equal(p.c.S.cap,5);
});

test('poll started during a write cannot roll back the count after acknowledgement',async()=>{
  const p=page(),done=p.click('cap-plus');await tick();
  const poll=p.c.refresh(true);await tick();
  p.requests[0].resolve();await done;
  p.requests[1].resolve({cap:7,config:{}});await poll;
  assert.equal(p.c.S.cap,8);
});

test('initial Automatic uses solo count rather than total apps, with a zero fallback',async()=>{
  for(const [solo,action,expected] of [[2,'cap-plus',3],[2,'cap-minus',1],[undefined,'cap-plus',1],[undefined,'cap-minus',0],[50,'cap-plus',30],[-2,'cap-minus',0]]){
    const p=page(null);p.c.S.individual_apps=solo;
    assert.equal(p.$('cap-auto').disabled,true);
    const done=p.click(action);await tick();assert.equal(p.requests[0].args.cap,expected);
    p.requests[0].resolve();await done;assert.equal(p.c.S.cap,expected);
  }
});
