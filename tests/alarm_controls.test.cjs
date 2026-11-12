const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../code_red_alarm/frontend/index.html'),'utf8');
const source=html.slice(html.indexOf('/* ---------- Time picker ---------- */'),html.indexOf('/* ---------- Day picker ---------- */'));

function harness(){
 const fields=new Map(),presets=[],requests=[],messages=[];let now=10000,focused=null;
 function element(){
  const attrs={},listeners={};
  return {value:'',textContent:'',children:[],dataset:{},disabled:false,open:false,
   style:{setProperty(k,v){this[k]=v}},setAttribute(k,v){attrs[k]=String(v)},getAttribute:k=>attrs[k],
   appendChild(node){this.children.push(node)},addEventListener(k,fn){listeners[k]=fn},
   dispatch(k,event){listeners[k]?.(event)},focus(){focused=this},scrollIntoView(){},
   showModal(){this.open=true},close(){this.open=false;listeners.close?.()}};
 }
 const $=id=>{if(!fields.has(id))fields.set(id,element());return fields.get(id)};
 [300,600,1500,2700].forEach(seconds=>{const e=element();e.dataset.seconds=String(seconds);presets.push(e)});
 $('f-time').value='07:00';$('timer-hours').value='0';$('timer-minutes').value='5';$('timer-seconds').value='0';
 const ctx=vm.createContext({$,document:{createElement:element,querySelectorAll:s=>s==='.timer-preset'?presets:[],addEventListener(){}},performance:{now:()=>now},setInterval(){},syncDate(){},render(){},stopBrowserSiren(){},toast:m=>messages.push(m),
  fmt12:value=>{const [h,m]=value.split(':').map(Number);return {t:`${(h+11)%12+1}:${String(m).padStart(2,'0')}`,ap:h<12?'AM':'PM'}},
  api:async(url,options)=>{requests.push({url,options});return {run_id:'run-1',state:'running',duration_sec:300,remaining_sec:300,revision:1,sampled_at:1}}});
 vm.runInContext(source,ctx);
 return {ctx,$,presets,requests,messages,setNow:n=>now=n,getFocused:()=>focused};
}
const idle={run_id:'',state:'idle',duration_sec:300,remaining_sec:300,revision:0,sampled_at:1,delivery_enabled:true,edition:'Public'};

test('all 24-hour times round-trip through the scrolling 12-hour picker',()=>{
 const {ctx}=harness();
 for(let h=0;h<24;h++)for(let m=0;m<60;m++){
  const value=`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
  assert.equal(ctx.pickerValue(ctx.pickerParts(value)),value);
 }
});
test('picker scroll choices apply explicitly and cancel preserves the draft',()=>{
 const h=harness();h.$('f-time').value='21:37';h.$('time-picker-open').onclick();
 assert.equal(h.$('picker-hours').children[8].getAttribute('aria-selected'),'true');
 assert.equal(h.$('picker-minutes').children[37].getAttribute('aria-selected'),'true');
 h.$('picker-hours').children[10].onclick();h.$('picker-period').children[0].onclick();
 assert.equal(h.$('f-time').value,'21:37');h.$('time-picker-cancel').onclick();
 assert.equal(h.$('f-time').value,'21:37');assert.equal(h.getFocused(),h.$('time-picker-open'));
 h.$('time-picker-open').onclick();h.$('picker-hours').children[11].onclick();h.$('picker-period').children[0].onclick();h.$('time-picker-apply').onclick();
 assert.equal(h.$('f-time').value,'00:37');assert.equal(h.$('time-picker-value').textContent,'12:37 AM');
 assert.equal(h.$('time-picker').open,false);
});
test('time lists support arrow, page and boundary keys with active descendants',()=>{
 const h=harness();h.$('time-picker-open').onclick();let prevented=0;
 for(const key of ['End','ArrowDown','Home','PageDown'])h.$('picker-minutes').dispatch('keydown',{key,preventDefault(){prevented++}});
 assert.equal(prevented,4);assert.equal(h.$('picker-minutes').getAttribute('aria-activedescendant'),'picker-minutes-5');
 assert.equal(h.$('picker-minutes').children.filter(n=>n.getAttribute('aria-selected')==='true').length,1);
 h.$('time-picker-apply').onclick();assert.equal(h.$('f-time').value,'07:05');
});
test('custom timer duration validates units, whole numbers and the 24-hour limit',()=>{
 const {ctx}=harness();assert.equal(ctx.durationFromParts(['24','0','0']),86400);assert.equal(ctx.durationFromParts(['0','0','1']),1);
 for(const parts of [['0','0','0'],['0','60','0'],['24','0','1'],['0','1.5','0'],['-1','0','0'],['','5','0']])assert.throws(()=>ctx.durationFromParts(parts));
 assert.equal(ctx.timerText(3661),'1:01:01');assert.equal(ctx.timerText(.1),'00:01');assert.equal(ctx.timerText(-4),'00:00');
});
test('countdown uses elapsed time, keeps paused time stable and clamps at zero',()=>{
 const {ctx}=harness();const running={remaining_sec:10,state:'running'};
 assert.equal(ctx.timerRemaining(running,2500),7.5);assert.equal(ctx.timerRemaining(running,20000),0);
 assert.equal(ctx.timerRemaining({...running,state:'paused'},20000),10);
});
test('newer server snapshots win and regular idle updates preserve custom duration',()=>{
 const h=harness();h.ctx.applyTimerSnapshot(idle);h.$('timer-minutes').value='25';
 h.ctx.applyTimerSnapshot({...idle,sampled_at:2});assert.equal(h.$('timer-minutes').value,'25');
 h.ctx.applyTimerSnapshot({...idle,run_id:'new',revision:2,state:'running',remaining_sec:80,duration_sec:90,sampled_at:4});
 h.ctx.applyTimerSnapshot({...idle,revision:1});assert.equal(h.$('timer-toggle').textContent,'Pause');assert.equal(h.$('timer-minutes').disabled,true);
 h.ctx.applyTimerSnapshot({...idle,run_id:'new',revision:2,state:'running',remaining_sec:90,duration_sec:90,sampled_at:3});
 assert.equal(h.$('timer-readout').textContent,'01:20');h.setNow(13000);h.ctx.renderTimer();assert.equal(h.$('timer-readout').textContent,'01:17');
});
test('timer is opt-in and duplicate pending clicks send only one start',async()=>{
 const h=harness();h.ctx.renderTimer();assert.equal(h.$('timer-toggle').disabled,true);assert.equal(h.requests.length,0);
 h.ctx.applyTimerSnapshot(idle);let release;
 h.ctx.api=(url,options)=>{h.requests.push({url,options});return new Promise(resolve=>release=resolve)};
 const first=h.ctx.timerCommand('start');await h.ctx.timerCommand('start');assert.equal(h.requests.length,1);
 assert.equal(JSON.parse(h.requests[0].options.body).duration_sec,300);
 release({...idle,run_id:'new',state:'running',revision:1});await first;
 assert.equal(h.$('timer-toggle').textContent,'Pause');assert.equal(h.$('timer-hours').disabled,true);
});
test('stale connection disables mutations and a preset after completion is retained',async()=>{
 const h=harness();h.ctx.applyTimerSnapshot({...idle,state:'finished',run_id:'old',revision:2});
 h.ctx.api=async()=>({...idle,run_id:'old',revision:3,sampled_at:3});
 await h.presets[2].onclick();assert.equal(h.$('timer-minutes').value,'25');assert.equal(h.$('timer-readout').textContent,'25:00');
 h.setNow(21000);h.ctx.renderTimer();assert.equal(h.$('timer-toggle').disabled,true);assert.equal(h.$('timer-status').textContent,'Reconnecting…');
});
test('inline scripts parse and the native time popup is replaced by a labeled dialog',()=>{
 for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
 assert.doesNotMatch(html,/<input[^>]+type="time"/);assert.match(html,/<dialog[^>]+id="time-picker"[^>]+aria-labelledby=/);
 assert.match(html,/role="timer"/);assert.match(html,/if \(\$\("time-picker"\)\.open\) \$\("time-picker"\)\.close\(\)/);
});
test('Dev delivery metadata blocks timer starts and browser audio even with a saved opt-in',async()=>{
 const h=harness();h.$('browser-ring').checked=true;
 const audioSource=html.slice(html.indexOf('let ctx = null, sirenTimer = null;'),html.indexOf('/* ---------- Ringing screen ---------- */'));
 let contexts=0;
 h.ctx.window={AudioContext:class{constructor(){contexts++}}};
 h.ctx.clearInterval=()=>{};
 vm.runInContext(audioSource,h.ctx);
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false});
 assert.equal(h.$('timer-toggle').disabled,true);assert.equal(h.$('browser-ring').disabled,true);
 assert.equal(h.$('browser-ring').checked,true,'the saved opt-in is retained');
 await h.ctx.timerCommand('start');h.ctx.unlockAudio();h.ctx.startBrowserSiren();
 assert.equal(h.requests.length,0);assert.equal(contexts,0);
});

function savedAlarmRendering(h,rows){
 const declarations=html.match(/const DAYS = [^;]+;/)[0]+html.match(/const esc = [^\n]+/)[0];
 const block=html.slice(html.indexOf('/* ---------- Alarms ---------- */'),html.indexOf('/* ---------- Edit mode ---------- */'));
 vm.runInContext(declarations+block,h.ctx);h.ctx.savedRows=rows;
 vm.runInContext('alarms=savedRows; render();',h.ctx);
}
const futureAlarm={id:'fixture',label:'Saved fixture',time_hhmm:'07:15',target_date:'2099-01-01',weekdays:[],enabled:true,code_red:false,math_mission_difficulty:0,ring_duration_sec:60};
function blockAudio(h){
 let contexts=0;h.$('browser-ring').checked=true;
 h.ctx.window={AudioContext:class{constructor(){contexts++;throw new Error('Audio must remain off')}}};h.ctx.clearInterval=()=>{};
 vm.runInContext(html.slice(html.indexOf('let ctx = null, sirenTimer = null;'),html.indexOf('/* ---------- Ringing screen ---------- */')),h.ctx);
 return ()=>{h.ctx.unlockAudio();h.ctx.startBrowserSiren();assert.equal(contexts,0)};
}
test('initial loading and failed delivery metadata never promise an alarm or create audio',async()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);const assertSilent=blockAudio(h);
 assert.match(h.$('list').innerHTML,/Saved schedule/);assert.match(h.$('next').textContent,/Checking alarm delivery/);
 assert.doesNotMatch(h.$('list').innerHTML,/Rings in|Saved Dev schedule/);assert.equal(h.$('count').textContent,'1 saved');assertSilent();
 h.ctx.api=async()=>{throw new Error('fixture offline')};await h.ctx.loadTimer();
 assert.match(h.$('list').innerHTML,/Alarm delivery status unavailable/);assert.doesNotMatch(h.$('next').textContent,/Next alarm/);assertSilent();
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false});
 assert.match(h.$('list').innerHTML,/Saved Dev schedule/);assert.equal(h.$('next').textContent,'Dev: alarm delivery is off');assertSilent();
 await h.ctx.loadTimer();assert.match(h.$('list').innerHTML,/Saved Dev schedule/);assert.equal(h.$('next').textContent,'Dev: alarm delivery is off');assertSilent();
});
test('Dev rows identify saved schedules instead of ringing, missed or handled occurrences',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm,{...futureAlarm,id:'past',target_date:'2000-01-01'},{...futureAlarm,id:'handled',enabled:false,last_triggered_date:'2099-01-01'}]);
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false});
 assert.equal((h.$('list').innerHTML.match(/Saved Dev schedule/g)||[]).length,3);
 assert.equal((h.$('list').innerHTML.match(/Dev: alarm delivery is off/g)||[]).length,3);
 assert.doesNotMatch(h.$('list').innerHTML,/Rings in|>Missed<|>Rang</);assert.equal(h.$('count').textContent,'3 saved');
 assert.match(h.$('list').innerHTML,/data-test="fixture" disabled/);
});
test('old and mismatched metadata cannot re-enable a confirmed Dev page',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);const assertSilent=blockAudio(h);
 h.ctx.applyTimerSnapshot({...idle,edition:'Dev',delivery_enabled:false,revision:5,sampled_at:10});
 for(const stale of [
  {...idle,revision:4,sampled_at:100},
  {...idle,revision:6,sampled_at:101},
  {...idle,edition:undefined,revision:7,sampled_at:102},
  {...idle,edition:'Dev',delivery_enabled:true,revision:6,sampled_at:103}
 ]){
  h.ctx.applyTimerSnapshot(stale);assert.equal(h.$('next').textContent,'Dev: alarm delivery is off');
  assert.match(h.$('list').innerHTML,/Saved Dev schedule/);assert.doesNotMatch(h.$('list').innerHTML,/Rings in/);
  assert.equal(h.$('browser-ring').disabled,true);assert.equal(h.$('browser-ring').checked,true);assertSilent();
 }
});
test('confirmed Public metadata preserves countdowns and enabled previews',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);h.ctx.applyTimerSnapshot(idle);
 assert.match(h.$('list').innerHTML,/Rings in/);assert.match(h.$('next').textContent,/^Next alarm in/);
 assert.equal(h.$('count').textContent,'1 of 1 on');assert.doesNotMatch(h.$('list').innerHTML,/Saved Dev schedule|delivery is off|data-test="fixture" disabled/);
 assert.equal(h.$('browser-ring').disabled,false);
});
test('legacy metadata without an explicit edition leaves delivery unconfirmed',()=>{
 const h=harness();savedAlarmRendering(h,[futureAlarm]);const assertSilent=blockAudio(h);
 h.ctx.applyTimerSnapshot({...idle,edition:undefined});
 assert.match(h.$('next').textContent,/status unavailable/);assert.doesNotMatch(h.$('list').innerHTML,/Rings in/);assertSilent();
});
test('regular timer ticks preserve alarm row controls when delivery metadata is unchanged',()=>{
 const h=harness();let renders=0;h.ctx.render=()=>renders++;
 h.ctx.applyTimerSnapshot(idle);assert.equal(renders,1);
 h.ctx.applyTimerSnapshot({...idle,sampled_at:2});assert.equal(renders,1);
 h.ctx.applyTimerSnapshot({...idle,sampled_at:3,delivery_enabled:false});assert.equal(renders,2);
});
