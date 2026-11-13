const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../code_red_alarm/frontend/index.html'),'utf8');
const section=(from,to)=>html.slice(html.indexOf(from),html.indexOf(to));
const logic=section('/* ---------- Time format ---------- */','/* ---------- Popovers ---------- */');

function fakeEl(){
 const attrs={},listeners={},classes=new Set();
 return {children:[],style:{},dataset:{},scrollTop:0,tabIndex:-1,hidden:false,disabled:false,value:'',textContent:'',checked:false,
  classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),toggle:(c,on)=>(on??!classes.has(c))?classes.add(c):classes.delete(c),contains:c=>classes.has(c)},
  setAttribute(k,v){attrs[k]=String(v)},getAttribute:k=>attrs[k],appendChild(n){this.children.push(n);return n},
  set innerHTML(v){this.children=[];this._html=v},get innerHTML(){return this._html||''},
  addEventListener(k,fn){(listeners[k]??=[]).push(fn)},dispatch(k,e){(listeners[k]||[]).forEach(f=>f(e))},
  scrollTo({top}){this.scrollTop=top},focus(){},getBoundingClientRect:()=>({top:0,height:200})};
}
function harness(){
 const fields=new Map(),requests=[],messages=[],timers=[];let now=10000,renders=0,timerRenders=0;
 const $=id=>{if(!fields.has(id))fields.set(id,fakeEl());return fields.get(id)};
 const ctx=vm.createContext({$,document:{createElement:fakeEl,querySelectorAll:()=>[],addEventListener(){},documentElement:{dataset:{}}},
  performance:{now:()=>now},setTimeout:(fn)=>{timers.push(fn);return timers.length},clearTimeout(){},requestAnimationFrame:()=>0,cancelAnimationFrame(){},
  matchMedia:()=>({matches:false}),Intl,Date,Math,JSON,Number,String,Array,Set,Object,
  store:{get:(k,d)=>d,set(){}},stopBrowserSiren(){},toast:m=>messages.push(m),
  render:()=>renders++,renderTimers:()=>timerRenders++,
  api:async(url,options)=>{requests.push({url,options});return {...idle,revision:9,sampled_at:9}}});
 vm.runInContext(logic,ctx);
 vm.runInContext('globalThis.clockText = clockText; globalThis.wheelIndex = wheelIndex;',ctx);
 return {ctx,$,requests,messages,setNow:n=>now=n,flush:()=>{while(timers.length)timers.shift()()},renders:()=>renders,timerRenders:()=>timerRenders};
}
const idle={timers:[],revision:0,sampled_at:1,delivery_enabled:true,edition:'Public',max_timers:20};
const tmr=(over={})=>({id:'tmr-1',label:'Tea',state:'running',duration_sec:300,remaining_sec:300,deadline:null,code_red:false,...over});

/* ---------- Time picker logic ---------- */
test('every minute of the day round-trips through the 12-hour and 24-hour wheels',()=>{
 const {ctx}=harness();
 for(let h=0;h<24;h++)for(let m=0;m<60;m++){
  const value=`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`,parts=ctx.pickerParts(value);
  assert.equal(ctx.pickerValue(parts),value);assert.equal(ctx.pickerValue(parts,true),value);
 }
 assert.equal(ctx.clockText('00:05',false),'12:05 AM');assert.equal(ctx.clockText('12:00',false),'12:00 PM');assert.equal(ctx.clockText('07:30',true),'07:30');
});
test('typed times accept the common ways people write them and reject the rest',()=>{
 const {ctx}=harness();
 const ok={'7':'07:00','730':'07:30','7:30':'07:30','7.30 pm':'19:30','730p':'19:30','12a':'00:00','12:15 PM':'12:15','1930':'19:30','0:05':'00:05','23:59':'23:59','noon':'12:00','Midnight':'00:00','6 45':'06:45','11:59 p.m.':'23:59'};
 for(const [text,want] of Object.entries(ok))assert.equal(ctx.parseTypedTime(text),want,text);
 for(const text of ['','24:00','7:60','13pm','0am','abc','7:3','12345','-1'])assert.equal(ctx.parseTypedTime(text),null,text);
});
test('relative quick picks round up to a whole minute and cross midnight',()=>{
 const {ctx}=harness();
 assert.equal(ctx.timeAfter(new Date(2026,10,12,13,51,20),5),'13:57');
 assert.equal(ctx.timeAfter(new Date(2026,10,12,13,51,0),15),'14:06');
 assert.equal(ctx.timeAfter(new Date(2026,10,12,23,50,0),60),'00:50');
});

/* ---------- Date picker logic ---------- */
test('the calendar shows six Monday-first weeks and month steps clamp the day',()=>{
 const {ctx}=harness();const cells=ctx.monthCells('2026-11-12');
 assert.equal(cells.length,42);assert.equal(cells[0],'2026-10-26');assert.equal(cells[6],'2026-11-01');assert.equal(cells[41],'2026-12-06');
 assert.equal(ctx.addMonthsKey('2027-01-31',1),'2027-02-28');assert.equal(ctx.addMonthsKey('2026-12-15',1),'2027-01-15');assert.equal(ctx.addMonthsKey('2026-03-31',-1),'2026-02-28');
 assert.equal(ctx.addDaysKey('2026-12-31',1),'2027-01-01');
 assert.match(ctx.dateLabel('2026-11-12','2026-11-12'),/^Today, /);assert.match(ctx.dateLabel('2026-11-13','2026-11-12'),/^Tomorrow, /);
 assert.match(ctx.dateLabel('2027-01-04','2026-11-12'),/2027/);assert.equal(ctx.dateLabel('','2026-11-12'),'Choose a date');
});
test('a one-time alarm date is never in the past',()=>{
 const {ctx}=harness();const now=new Date(2026,10,12,13,51);
 assert.equal(ctx.nextDateFor('19:30','',now),'2026-11-12');
 assert.equal(ctx.nextDateFor('07:00','',now),'2026-11-13');
 assert.equal(ctx.nextDateFor('07:00','2026-11-20',now),'2026-11-20');
 assert.equal(ctx.nextDateFor('07:00','2026-11-01',now),'2026-11-13');
 assert.equal(ctx.nextDateFor('13:51','2026-11-12',now),'2026-11-13','the current minute has already started');
});

/* ---------- Wheel ---------- */
test('wheel helpers snap, step, wrap and match typed digits',()=>{
 const {ctx}=harness();
 assert.equal(ctx.wheelIndex(0,60),0);assert.equal(ctx.wheelIndex(59,60),1);assert.equal(ctx.wheelIndex(-30,60),0);assert.equal(ctx.wheelIndex(99999,60),59);
 assert.equal(ctx.wheelStep(59,1,60,true),0);assert.equal(ctx.wheelStep(0,-1,60,true),59);assert.equal(ctx.wheelStep(59,1,60,false),59);assert.equal(ctx.wheelStep(3,-10,60,false),0);
 const minutes=Array.from({length:60},(_,i)=>i),pad=v=>String(v).padStart(2,'0');
 assert.equal(ctx.wheelTyped('4',minutes,pad),4);assert.equal(ctx.wheelTyped('45',minutes,pad),45);assert.equal(ctx.wheelTyped('07',minutes,pad),7);assert.equal(ctx.wheelTyped('75',minutes,pad),-1);
 assert.equal(ctx.wheelTyped('p',['AM','PM'],String),1);assert.equal(ctx.wheelTyped('x',['AM','PM'],String),-1);
});
test('a wheel works like a spin button from the keyboard and labels its value',()=>{
 const h=harness(),el=fakeEl(),changes=[];
 const w=h.ctx.createWheel(el,{label:'Minute',unit:'minutes',values:Array.from({length:60},(_,i)=>i),format:v=>String(v).padStart(2,'0'),loop:true,page:10,onChange:v=>changes.push(v)});
 assert.equal(el.getAttribute('role'),'spinbutton');assert.equal(el.getAttribute('aria-label'),'Minute');assert.equal(el.tabIndex,0);
 assert.equal(el.children[0].children.length,60);
 let prevented=0;const key=k=>el.dispatch('keydown',{key:k,preventDefault(){prevented++}});
 key('ArrowUp');assert.equal(w.value,1);assert.equal(el.scrollTop,40);assert.equal(el.getAttribute('aria-valuetext'),'01 minutes');
 key('ArrowDown');key('ArrowDown');assert.equal(w.value,59,'arrow keys wrap on looping columns');
 key('PageUp');assert.equal(w.value,9);key('Home');assert.equal(w.value,0);key('End');assert.equal(w.value,59);
 h.setNow(20000);key('3');h.setNow(20200);key('5');assert.equal(w.value,35,'two digits typed quickly pick 35');
 h.setNow(30000);key('8');assert.equal(w.value,8,'a pause starts a new number');
 key('x');assert.equal(w.value,8);assert.equal(prevented,9);
 h.flush();assert.equal(w.value,8);assert.equal(el.getAttribute('aria-valuenow'),'8');
 assert.deepEqual(changes,[1,0,59,9,0,59,3,35,8]);
 w.set(20);assert.equal(w.value,20);assert.equal(changes.length,9,'setting a value from code does not echo a change');
 const period=fakeEl(),p=h.ctx.createWheel(period,{label:'AM or PM',values:['AM','PM'],format:String});
 period.dispatch('keydown',{key:'p',preventDefault(){}});assert.equal(p.value,'PM');period.dispatch('keydown',{key:'ArrowUp',preventDefault(){}});assert.equal(p.value,'PM','AM/PM does not wrap');
 assert.equal(period.getAttribute('aria-valuetext'),'PM');
 const hours=fakeEl(),hw=h.ctx.createWheel(hours,{label:'Hour',values:[1,2,3,4,5,6,7,8,9,10,11,12],format:String});
 hw.set(7);hw.setValues(Array.from({length:24},(_,i)=>i),19);assert.equal(hw.value,19);assert.equal(hours.children[0].children.length,24);
});
test('scrolling a wheel follows the row in the middle',()=>{
 const h=harness(),el=fakeEl(),changes=[];
 const w=h.ctx.createWheel(el,{label:'Hour',values:Array.from({length:24},(_,i)=>i),format:String,onChange:v=>changes.push(v)});
 el.scrollTop=163;el.dispatch('scroll');assert.equal(w.value,4);h.flush();assert.equal(el.scrollTop,160,'it settles exactly on the row');
 let prevented=false;el.dispatch('wheel',{deltaY:100,deltaMode:0,preventDefault(){prevented=true}});assert.equal(w.value,5);assert.equal(prevented,true);
 el.dispatch('wheel',{deltaY:20,deltaMode:0,preventDefault(){}});assert.equal(w.value,5,'small trackpad deltas build up');
 el.dispatch('wheel',{deltaY:20,deltaMode:0,preventDefault(){}});assert.equal(w.value,6);
 assert.deepEqual(changes,[4,5,6]);
});

/* ---------- Timer logic ---------- */
test('timer durations validate units, whole numbers and the 24-hour limit',()=>{
 const {ctx}=harness();assert.equal(ctx.durationFromParts(['24','0','0']),86400);assert.equal(ctx.durationFromParts(['0','0','1']),1);
 for(const parts of [['0','0','0'],['0','60','0'],['24','0','1'],['0','1.5','0'],['-1','0','0'],['','5','0']])assert.throws(()=>ctx.durationFromParts(parts));
 assert.equal(ctx.timerText(3661),'1:01:01');assert.equal(ctx.timerText(.1),'00:01');assert.equal(ctx.timerText(-4),'00:00');
 assert.equal(ctx.durationText(5400),'1 hr 30 min');assert.equal(ctx.durationText(45),'45 sec');assert.equal(ctx.durationText(300),'5 min');
});
test('countdown uses elapsed time, keeps paused time stable and clamps at zero',()=>{
 const {ctx}=harness();const running=tmr({remaining_sec:10});
 assert.equal(ctx.timerRemaining(running,2500),7.5);assert.equal(ctx.timerRemaining(running,20000),0);
 assert.equal(ctx.timerRemaining({...running,state:'paused'},20000),10);
});
test('each timer state shows the right text, ring and buttons',()=>{
 const {ctx}=harness();
 let v=ctx.timerView(tmr(),150,true,true);
 assert.equal(v.text,'02:30');assert.equal(v.total,'of 05:00');assert.equal(v.progress,.5);assert.equal(v.status,'Running');
 assert.equal(v.toggleAction,'pause');assert.equal(v.toggleDisabled,false);assert.equal(v.addDisabled,false);assert.equal(v.resetDisabled,false);
 v=ctx.timerView(tmr({state:'paused'}),40,true,true);assert.equal(v.toggleAction,'resume');assert.equal(v.status,'Paused');assert.equal(v.cls,'paused');
 v=ctx.timerView(tmr({state:'idle'}),300,true,true);assert.equal(v.toggleLabel,'Start');assert.equal(v.progress,1);assert.equal(v.resetDisabled,true);
 v=ctx.timerView(tmr({state:'finished'}),0,true,true);assert.equal(v.toggleLabel,'Restart');assert.equal(v.status,"Time's up");assert.equal(v.text,'00:00');assert.equal(v.progress,0);assert.equal(v.addDisabled,false);
 v=ctx.timerView(tmr({state:'expired'}),0,true,true);assert.match(v.status,/while the PC was off/);
 v=ctx.timerView(tmr(),0,true,true);assert.equal(v.status,'Finishing…');assert.equal(v.toggleDisabled,true);assert.equal(v.addDisabled,true);
 v=ctx.timerView(tmr(),100,false,true);assert.equal(v.status,'Reconnecting…');for(const k of ['toggleDisabled','addDisabled','resetDisabled','deleteDisabled'])assert.equal(v[k],true,k);
 v=ctx.timerView(tmr({duration_sec:86400}),86390,true,true);assert.equal(v.addDisabled,true,'cannot pass 24 hours');
 v=ctx.timerView(tmr({state:'paused'}),40,true,false);assert.equal(v.toggleDisabled,true,'no resume without delivery');assert.equal(v.addDisabled,false);
 v=ctx.timerView(tmr(),40,true,false);assert.equal(v.toggleDisabled,false,'pausing is always allowed');
});
test('newer snapshots win and stale ones are ignored',()=>{
 const h=harness();h.ctx.applyTimerSnapshot({...idle,revision:3,sampled_at:5,timers:[tmr()]});
 h.ctx.applyTimerSnapshot({...idle,revision:2,sampled_at:9});assert.equal(vm.runInContext('timerSnap.timers.length',h.ctx),1);
 h.ctx.applyTimerSnapshot({...idle,revision:3,sampled_at:4});assert.equal(vm.runInContext('timerSnap.timers.length',h.ctx),1);
 h.ctx.applyTimerSnapshot({...idle,revision:4,sampled_at:6});assert.equal(vm.runInContext('timerSnap.timers.length',h.ctx),0);
 h.ctx.applyTimerSnapshot({timer:{state:'idle'}});assert.equal(vm.runInContext('timerSnap.revision',h.ctx),4,'an old single-timer payload is ignored');
 assert.equal(h.timerRenders(),2);
});
test('double clicks send one command per timer and other timers stay usable',async()=>{
 const h=harness();h.ctx.applyTimerSnapshot({...idle,timers:[tmr(),tmr({id:'tmr-2'})]});let release;
 h.ctx.api=(url,options)=>{h.requests.push({url,options});return new Promise(resolve=>release=resolve)};
 const first=h.ctx.timerCommand('tmr-1','pause');await h.ctx.timerCommand('tmr-1','pause');assert.equal(h.requests.length,1);
 const v=h.ctx.timerView(tmr(),100,true,true);assert.equal(v.toggleDisabled,true,'a pending timer is locked');
 assert.equal(h.ctx.timerView(tmr({id:'tmr-2'}),100,true,true).toggleDisabled,false);
 release({...idle,revision:1,timers:[tmr({state:'paused'})]});await first;
 assert.equal(h.requests[0].url,'/api/timers/tmr-1/pause');assert.equal(h.requests[0].options.method,'POST');
 h.ctx.api=async(url,options)=>{h.requests.push({url,options});return {...idle,revision:2}};
 await h.ctx.timerCommand('tmr-1','delete');assert.equal(h.requests[1].url,'/api/timers/tmr-1');assert.equal(h.requests[1].options.method,'DELETE');
 await h.ctx.timerCommand('tmr-2','add',{seconds:60});assert.equal(JSON.parse(h.requests[2].options.body).seconds,60);
});
test('a failed command shows the reason and reloads the timers',async()=>{
 const h=harness();h.ctx.applyTimerSnapshot({...idle,timers:[tmr()]});
 h.ctx.api=async(url)=>{if(url.endsWith('/pause'))throw new Error('That timer is gone.');return {...idle,revision:5}};
 await h.ctx.timerCommand('tmr-1','pause');assert.deepEqual(h.messages,['That timer is gone.']);assert.equal(vm.runInContext('timerSnap.revision',h.ctx),5);
});
test('creating a timer posts the opt-in options and returns its id',async()=>{
 const h=harness();h.ctx.applyTimerSnapshot(idle);
 h.ctx.api=async(url,options)=>{h.requests.push({url,options});return {...idle,revision:1,id:'tmr-9',timers:[tmr({id:'tmr-9'})]}};
 const spec={duration_sec:90,label:'Tea',code_red:false,custom_phrase:'',ring_duration_sec:60};
 assert.equal(await h.ctx.createTimer(spec),'tmr-9');assert.deepEqual(JSON.parse(h.requests[0].options.body),spec);
});
test('Dev delivery metadata blocks timer starts, resumes and new timers',async()=>{
 const h=harness();h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false,timers:[tmr({state:'paused'})]});
 await h.ctx.timerCommand('tmr-1','resume');await h.ctx.timerCommand('tmr-1','start');assert.equal(await h.ctx.createTimer({duration_sec:5}),null);
 assert.equal(h.requests.length,0);assert.equal(h.$('browser-ring').disabled,true);assert.equal(h.$('test-sound').disabled,true);
 await h.ctx.timerCommand('tmr-1','reset');assert.equal(h.requests.length,1,'reset only disarms, so it stays allowed');
});
test('Dev pages keep browser audio off even with a saved opt-in',async()=>{
 const h=harness();h.$('browser-ring').checked=true;let contexts=0;
 h.ctx.window={AudioContext:class{constructor(){contexts++}}};h.ctx.clearInterval=()=>{};
 vm.runInContext(section('let ctx = null, sirenTimer = null;','/* ---------- Ringing screen ---------- */'),h.ctx);
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false});
 h.ctx.unlockAudio();h.ctx.startBrowserSiren();assert.equal(contexts,0);assert.equal(h.$('browser-ring').checked,true,'the saved opt-in is kept');
});

/* ---------- Saved alarm rows and delivery metadata ---------- */
function savedAlarmRendering(h,rows){
 const declarations=html.match(/const DAYS = [^;]+;/)[0]+html.match(/const esc = [^\n]+/)[0];
 vm.runInContext(declarations+section('/* ---------- Alarms ---------- */','/* ---------- Edit mode ---------- */'),h.ctx);h.ctx.savedRows=rows;
 vm.runInContext('render=renderAlarmsForTest=render; alarms=savedRows; render();',h.ctx);
}
const futureAlarm={id:'fixture',label:'Saved fixture',time_hhmm:'07:15',target_date:'2099-01-01',weekdays:[],enabled:true,code_red:false,math_mission_difficulty:0,ring_duration_sec:60};
function blockAudio(h){
 let contexts=0;h.$('browser-ring').checked=true;
 h.ctx.window={AudioContext:class{constructor(){contexts++;throw new Error('Audio must remain off')}}};h.ctx.clearInterval=()=>{};
 vm.runInContext(section('let ctx = null, sirenTimer = null;','/* ---------- Ringing screen ---------- */'),h.ctx);
 return ()=>{h.ctx.unlockAudio();h.ctx.startBrowserSiren();assert.equal(contexts,0)};
}
test('loading and failed delivery checks never promise an alarm or create audio',async()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);const assertSilent=blockAudio(h);
 assert.match(h.$('list').innerHTML,/Saved schedule/);assert.match(h.$('next').textContent,/Checking alarm delivery/);
 assert.doesNotMatch(h.$('list').innerHTML,/Rings in|Saved Dev schedule/);assert.equal(h.$('count').textContent,'1 saved');assertSilent();
 h.ctx.api=async()=>{throw new Error('fixture offline')};await h.ctx.loadTimer();
 assert.match(h.$('list').innerHTML,/Alarm delivery status unavailable/);assert.doesNotMatch(h.$('next').textContent,/Next alarm/);assertSilent();
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false});
 assert.match(h.$('list').innerHTML,/Saved Dev schedule/);assert.equal(h.$('next').textContent,'Dev: alarm delivery is off');assertSilent();
});
test('Dev rows say saved schedule instead of ringing, missed or rang',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm,{...futureAlarm,id:'past',target_date:'2000-01-01'},{...futureAlarm,id:'handled',enabled:false,last_triggered_date:'2099-01-01'}]);
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false});
 assert.equal((h.$('list').innerHTML.match(/Saved Dev schedule/g)||[]).length,3);
 assert.doesNotMatch(h.$('list').innerHTML,/Rings in|>Missed<|>Rang</);assert.equal(h.$('count').textContent,'3 saved');
 assert.match(h.$('list').innerHTML,/data-test="fixture" disabled/);
});
test('old and mismatched metadata cannot re-enable a confirmed Dev page',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);const assertSilent=blockAudio(h);
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false,revision:5,sampled_at:10});
 for(const stale of [{...idle,revision:4,sampled_at:100},{...idle,revision:6,sampled_at:101},{...idle,edition:undefined,revision:7,sampled_at:102},{...idle,edition:'Dev',delivery_enabled:true,revision:6,sampled_at:103}]){
  h.ctx.applyTimerSnapshot(stale);assert.equal(h.$('next').textContent,'Dev: alarm delivery is off');
  assert.doesNotMatch(h.$('list').innerHTML,/Rings in/);assert.equal(h.$('browser-ring').disabled,true);assertSilent();
 }
});
test('confirmed Public metadata keeps countdowns, previews and the chosen clock style',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);h.ctx.applyTimerSnapshot(idle);
 assert.match(h.$('list').innerHTML,/Rings in/);assert.match(h.$('next').textContent,/^Next alarm in/);assert.equal(h.$('count').textContent,'1 of 1 on');
 assert.match(h.$('list').innerHTML,/7:15<small>AM<\/small>/);assert.equal(h.$('browser-ring').disabled,false);
 vm.runInContext('clock24=true; render();',h.ctx);assert.match(h.$('list').innerHTML,/07:15<small><\/small>/);
});
test('legacy metadata without an edition leaves delivery unconfirmed',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);const assertSilent=blockAudio(h);
 h.ctx.applyTimerSnapshot({...idle,edition:undefined});
 assert.match(h.$('next').textContent,/status unavailable/);assert.doesNotMatch(h.$('list').innerHTML,/Rings in/);assertSilent();
});
test('regular timer updates do not rebuild alarm rows when delivery is unchanged',()=>{
 const h=harness();h.ctx.applyTimerSnapshot(idle);assert.equal(h.renders(),1);
 h.ctx.applyTimerSnapshot({...idle,sampled_at:2});assert.equal(h.renders(),1);
 h.ctx.applyTimerSnapshot({...idle,sampled_at:3,delivery_enabled:false});assert.equal(h.renders(),2);
});

/* ---------- Page structure ---------- */
test('scripts parse and native pickers are replaced by labelled, themed dialogs',()=>{
 for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
 assert.doesNotMatch(html,/<input[^>]+type="(time|date)"/);
 for(const id of ['time-picker','date-picker'])assert.match(html,new RegExp(`<dialog class="pop" id="${id}" aria-labelledby=`));
 assert.match(html,/id="time-picker-open"[^>]+aria-haspopup="dialog"[^>]+aria-expanded="false"/);
 assert.match(html,/id="date-picker-open"[^>]+aria-haspopup="dialog"/);
 assert.match(html,/role="tablist"[^>]*aria-label="Clock tools"/);assert.match(html,/role="tab" id="tab-timer" aria-controls="panel-timer"/);
 assert.match(html,/id="panel-timer" role="tabpanel"/);assert.match(html,/role="grid"/);assert.match(html,/role="timer"/);
 assert.match(html,/@media \(prefers-reduced-motion:reduce\)\{\*\{animation:none!important;transition:none!important\}\}/);
 assert.match(html,/\["time-picker","date-picker"\]\.forEach\(id => \{ if \(\$\(id\)\.open\)/,'a ringing alarm closes open pickers');
 assert.doesNotMatch(html,/#[0-9a-f]{6}[^;]*;\s*\/\* picker/i);
});
test('new timers are opt-in: Code Red is off and nothing starts on its own',()=>{
 assert.match(html,/<input type="checkbox" id="tm-codered"[^>]*\/>/);assert.doesNotMatch(html,/id="tm-codered"[^>]*checked/);
 assert.match(html,/<button type="submit" class="btn primary block" id="tm-start" disabled>/);
});
