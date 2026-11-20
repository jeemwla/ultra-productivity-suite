const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');
const hub=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/hub.html'),'utf8');

function load(){
  const source=html.slice(html.indexOf('const CAP_MAX = 30;'),html.indexOf('async function setCap(c){'));
  const context=vm.createContext({state:{cap:null,layout:{items:[]}},layoutItems:()=>[],esc:s=>String(s)});
  vm.runInContext(source,context);
  return context;
}

test('minus and plus start from what the strip shows and stay within 0 to 30',()=>{
  const c=load();
  assert.equal(c.nextCap(null,1,4),5,'Automatic plus: one more than shown now');
  assert.equal(c.nextCap(null,-1,4),3,'Automatic minus: one fewer than shown now');
  assert.equal(c.nextCap(null,-1,0),0);
  assert.equal(c.nextCap(5,1,0),6);
  assert.equal(c.nextCap(0,-1,0),0);
  assert.equal(c.nextCap(30,1,0),30);
  assert.equal(c.nextCap(null,1,40),30);
});

test('the menu control shows Automatic or the chosen number and disables steps at the ends',()=>{
  const c=load();
  assert.match(c.capControl(),/Apps: Auto/);
  c.state.cap=0;assert.match(c.capControl(),/data-act="cap-"[^>]*disabled/);
  c.state.cap=30;assert.match(c.capControl(),/data-act="cap\+"[^>]*disabled/);
  c.state.cap=10;const h=c.capControl();assert.match(h,/Apps: 10/);assert.doesNotMatch(h,/disabled/);
});

function hubStepper(initial){
  const source=hub.slice(hub.indexOf('const CAP_MAX = 30;'),hub.indexOf('$("dock-settings-btn")'));
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:false,attrs:{},classList:{set:new Set(),add(c){this.set.add(c);},remove(c){this.set.delete(c);}},setAttribute(k,v){this.attrs[k]=v;}});return nodes.get(id);};
  const saved=[];let fail=false;
  const invoke=async(name,args)=>{saved.push(args.cap);if(fail)throw new Error('disk full');return {cap:args.cap};};
  const context=vm.createContext({$:node,invoke,info:{dock_cap:initial,dock_shown:4},Math,Number,String});
  vm.runInContext(source,context);
  context.showCap();
  return {context,node,saved,setFail(v){fail=v;}};
}

test('the hub uses the same minus, count, plus and Automatic stepper instead of a 31-entry list',async()=>{
  assert.doesNotMatch(hub,/<select id="dock-cap"/);
  assert.doesNotMatch(hub,/Array\.from\(\{ length: 31 \}/);
  assert.match(hub,/role="group" aria-labelledby="dock-cap-label" aria-describedby="dock-cap-help"/);
  assert.match(hub,/<output id="dock-cap-count" aria-live="polite" aria-atomic="true"/);
  const h=hubStepper(null);
  assert.equal(h.node('dock-cap-count').textContent,'Automatic');
  assert.equal(h.node('dock-cap-auto').attrs['aria-pressed'],'true');
  await h.node('dock-cap-plus').onclick();
  assert.equal(h.node('dock-cap-count').textContent,'5 apps','Automatic plus starts from what the taskbar shows');
  await h.node('dock-cap-minus').onclick();await h.node('dock-cap-minus').onclick();
  assert.equal(h.node('dock-cap-count').textContent,'3 apps');
  await h.node('dock-cap-auto').onclick();
  assert.deepEqual(h.saved,[5,4,3,null]);
  assert.equal(h.node('dock-cap-auto').disabled,true);
});

test('the hub stepper stops at 0 and 30 and restores the saved count when saving fails',async()=>{
  const low=hubStepper(0);
  assert.equal(low.node('dock-cap-minus').disabled,true);
  assert.equal(low.node('dock-cap-count').textContent,'0 apps');
  const high=hubStepper(30);
  assert.equal(high.node('dock-cap-plus').disabled,true);
  const f=hubStepper(7);f.setFail(true);
  await f.node('dock-cap-plus').onclick();
  assert.equal(f.node('dock-cap-count').textContent,'7 apps');
  assert.match(f.node('dock-cap-help').textContent,/Could not save the app count/);
  assert.equal(f.node('dock-cap-help').classList.set.has('err'),true);
  assert.equal(f.node('dock-cap-plus').disabled,false);
});
