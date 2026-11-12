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

test('the hub offers Automatic and 0 to 30 and saves through set_dock_cap',()=>{
  assert.match(hub,/<select id="dock-cap"/);
  assert.match(hub,/invoke\("set_dock_cap", \{ cap \}\)/);
  assert.match(hub,/Array\.from\(\{ length: 31 \}/);
});
