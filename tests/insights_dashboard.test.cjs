const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../insights/dashboard.html'),'utf8');
const between=(a,b)=>html.slice(html.indexOf(a),html.indexOf(b));
const helpers=()=>{
 const ctx=vm.createContext({document:{documentElement:{dataset:{}}},window:{}});
 vm.runInContext('const RANGES=["day","week","month","year"];'+between('function rangeFrom(','/* end pure helpers */'),ctx);
 return ctx;
};

test('a saved period is used only when it is one of the four periods',()=>{
 const h=helpers();
 for(const r of ['day','week','month','year']) assert.equal(h.rangeFrom(r),r);
 for(const r of [null,'','forever','__proto__']) assert.equal(h.rangeFrom(r),'month');
});

test('offline status keeps the time of the last good data and says why',()=>{
 const h=helpers();
 assert.deepEqual({...h.liveText(false,0,'')},{cls:'bad',text:'Offline',title:''});
 const s=h.liveText(false,Date.UTC(2026,10,18,20,5,9),'the dashboard answered 500');
 assert.match(s.text,/^Offline · showing /);
 assert.equal(s.title,'Last refresh failed: the dashboard answered 500');
 assert.match(h.liveText(true,Date.now()).text,/^Live · updated /);
});

test('GitHub note explains errors, stale numbers, traffic access and the token hint',()=>{
 const h=helpers();
 assert.deepEqual({...h.ghNote({error:'Limit reached.'})},{warn:true,text:'Limit reached.'});
 assert.match(h.ghNote({error:'Limit reached.',stale:true}).text,/last numbers GitHub sent/);
 assert.equal(h.ghNote({traffic_error:'no push'}).warn,true);
 assert.match(h.ghNote({}).text,/GITHUB_TOKEN/);
 assert.equal(h.ghNote({traffic:{referrers:[{referrer:'google.com',count:3}]}}).text,'Top sources: google.com (3)');
});

test('chart keys move one point at a time and stop at both ends',()=>{
 const h=helpers();
 assert.equal(h.keyStep('ArrowRight',null,30),0);
 assert.equal(h.keyStep('ArrowLeft',null,30),29);
 assert.equal(h.keyStep('ArrowRight',29,30),29);
 assert.equal(h.keyStep('ArrowLeft',0,30),0);
 assert.equal(h.keyStep('Home',12,30),0);
 assert.equal(h.keyStep('End',3,30),29);
 assert.equal(h.keyStep('a',3,30),undefined);
 assert.equal(h.keyStep('ArrowRight',null,0),null);
});

test('number animation follows the suite motion setting',()=>{
 for(const motion of ['off','reduced']){
  const ctx=vm.createContext({document:{documentElement:{dataset:{motion}}},window:{}});
  vm.runInContext(between('function quietMotion()','/* end pure helpers */'),ctx);
  assert.equal(ctx.quietMotion(),true,motion);
 }
 const full=vm.createContext({document:{documentElement:{dataset:{motion:'full'}}},window:{matchMedia:()=>({matches:false})},matchMedia:()=>({matches:false})});
 vm.runInContext(between('function quietMotion()','/* end pure helpers */'),full);
 assert.equal(full.quietMotion(),false);
});

test('page wiring: one live status, newest-request guard, hidden-tab pause, safe removal',()=>{
 assert.match(html,/id="live" role="status" aria-live="polite"/);
 assert.match(html,/if\(my!==seq\) return/);
 assert.match(html,/document\.hidden/);
 assert.match(html,/confirm\("Remove this message/);
 assert.match(html,/href="\/api\/export\.csv\?range=month"/);
 assert.equal((html.match(/<button(?![^>]*type=)/g)||[]).length,0,'every button declares its type');
 assert.doesNotMatch(html,/—/,'no em dashes in UI text');
});
