// PC Sentinel page helpers: motion level, number tweening, the health banner wording, the connection state and toasts.
// The page's own code is cut out of index.html and run with small fakes, so these follow the shipped page.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../pc_sentinel/frontend/index.html'),'utf8');
const cut=(a,b)=>{const i=html.indexOf(a),j=html.indexOf(b,i);assert.ok(i>=0&&j>i,`${a} .. ${b}`);return html.slice(i,j);};

function page({motion='full',osReduce=false}={}){
 const els={};const el=id=>els[id]||(els[id]={id,textContent:'',title:'',hidden:false,children:[],attrs:{},setAttribute(k,v){this.attrs[k]=v;},appendChild(c){this.children.push(c);c.parent=this;},set innerHTML(v){this._html=v;},get innerHTML(){return this._html;}});
 const frames=[];
 const ctx=vm.createContext({document:{documentElement:{dataset:{motion}},addEventListener(){},getElementById:el,createElement:()=>({className:'',textContent:'',attrs:{},setAttribute(k,v){this.attrs[k]=v;},remove(){this.parent.children.splice(this.parent.children.indexOf(this),1);}})},
  matchMedia:q=>({matches:osReduce&&/reduce/.test(q)}),performance:{now:()=>0},requestAnimationFrame:f=>{frames.push(f);return frames.length;},setTimeout:()=>1,clearTimeout:()=>{},Date,Math,isNaN,String,Number});
 ctx.els=els;ctx.frames=frames;
 vm.runInContext(cut('const $ = ','const css = ')+cut('const TW = new Map()','function smooth(')+'const HOT={};'+cut('function healthView','function setSt(')+'let snap=null,lastMsg=0;'+cut('function connState','setInterval(() => { const st'),ctx);
 return ctx;
}
test('numbers tween only at full motion; Reduced, Off and the Windows setting show the value at once',()=>{
 for(const [opts,animated] of [[{},true],[{motion:'reduced'},false],[{motion:'off'},false],[{osReduce:true},false]]){
  const c=page(opts),e={textContent:''};
  vm.runInContext('tween',c)(e,42,v=>String(Math.round(v)));
  assert.equal(c.frames.length>0,animated,JSON.stringify(opts));
  if(!animated)assert.equal(e.textContent,'42');
  assert.equal(vm.runInContext('motionLevel()',c),animated?'full':opts.motion==='off'?'off':'reduced');
 }
});
test('a healthy score that lost points never shows a check mark over a warning',()=>{
 const c=page(),hv=vm.runInContext('healthView',c);
 assert.deepEqual({...hv({score:100,label:'All good',notes:['Temperatures and memory are in a healthy range.']},'good')},{cls:'good',icon:'✓',label:'All good'});
 assert.deepEqual({...hv({score:92,label:'All good',notes:['Memory is almost full (92%)']},'good')},{cls:'info',icon:'i',label:'Healthy, one thing to watch'});
 assert.equal(hv({score:88,label:'All good',notes:['a','b']},'good').label,'Healthy, 2 things to watch');
 assert.deepEqual({...hv({score:70,label:'Keep an eye on it',notes:['x']},'warning')},{cls:'warning',icon:'!',label:'Keep an eye on it'});
 assert.equal(hv({score:90,label:'No warnings in available readings',notes:['CPU temperature unavailable']},'good').label,'No warnings in available readings');
});
test('connection state: connecting, live sensor count, late readings and a paused banner with the last time',()=>{
 const c=page();
 assert.equal(vm.runInContext('connState(1000)',c).chip,'Connecting…');
 vm.runInContext('snap={hardware:{sensor_count:1234},network:{}};lastMsg=10000',c);
 let st=vm.runInContext('connState(12000)',c);
 assert.equal(st.fresh,true);assert.match(st.chip,/^1.?234 sensors$/);assert.equal(st.banner,null);
 vm.runInContext('snap.hardware.stale=true;snap.network.stale=true',c);
 st=vm.runInContext('connState(12000)',c);assert.match(st.banner.s,/sensor readings and latency checks/);
 st=vm.runInContext('connState(30000)',c);
 assert.equal(st.fresh,false);assert.equal(st.chip,'Reconnecting…');assert.equal(st.banner.t,'Live data paused');assert.match(st.title,/20 s/);
});
test('a repeated toast is shown once and problems go to the assertive region',()=>{
 const c=page(),toast=vm.runInContext('toast',c);
 toast('Saved');toast('Saved');assert.equal(c.els.toasts.children.length,1);
 toast('Not saved: disk full',true);
 assert.equal(c.els.toasts.children.length,2);assert.equal(c.els.alerts.textContent,'Not saved: disk full');
 assert.equal(c.els.toasts.children[1].attrs['aria-hidden'],'true');
});
test('the page honours reduced motion in CSS and gives keyboard users a skip link and closable dialogs',()=>{
 assert.match(html,/@media \(prefers-reduced-motion:reduce\)\{\*,\*::before,\*::after\{animation-duration:\.01ms!important/);
 assert.match(html,/<a class="skip" href="#main" id="skip">Skip to content<\/a>/);
 assert.match(html,/<main class="main" id="main" tabindex="-1">/);
 assert.match(html,/if \(e\.key === "Escape"\) \{ e\.preventDefault\(\); e\.stopPropagation\(\); modalClose\(bg\)/);
 assert.ok(!/classList\.add\("show"\); setTimeout\(\(\) => \$\("(fb-msg|rn-in)"\)\.focus/.test(html),'dialogs open through modalOpen');
});
test('alert thresholds stay inside each field range and fall back on blanks',()=>{
 const ctx=vm.createContext({});vm.runInContext(cut('const alertValue','const alertField'),ctx);const v=vm.runInContext('alertValue',ctx);
 assert.equal(v('9',50,110,90),50);assert.equal(v('900',50,110,90),110);assert.equal(v('85.6',50,110,90),86);
 assert.equal(v('',50,110,90),90);assert.equal(v('abc',50,110,90),90);assert.equal(v(undefined,50,99,92),92);assert.equal(v(95,50,99,92),95);
});
