// Page helpers for summaries, Code Red typing, history and timer lengths. No browser, audio, server or live data.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../code_red_alarm/frontend/index.html'),'utf8');
const section=(from,to)=>html.slice(html.indexOf(from),html.indexOf(to));

function fakeEl(){
 const attrs={};
 return {children:[],style:{},dataset:{},hidden:false,disabled:false,value:'',textContent:'',checked:false,innerHTML:'',
  classList:{add(){},remove(){},toggle(){},contains:()=>false},setAttribute(k,v){attrs[k]=String(v)},getAttribute:k=>attrs[k],
  addEventListener(){},focus(){}};
}
function page(now=new Date(2026,10,12,7,5)){
 const fields=new Map(),$=id=>{if(!fields.has(id))fields.set(id,fakeEl());return fields.get(id)};
 const ctx=vm.createContext({$,document:{createElement:fakeEl,querySelectorAll:()=>[],addEventListener(){},documentElement:{dataset:{}}},
  performance:{now:()=>0},setTimeout:()=>0,clearTimeout(){},requestAnimationFrame:()=>0,cancelAnimationFrame(){},matchMedia:()=>({matches:false}),
  Intl,Date:class extends Date{constructor(...a){a.length?super(...a):super(now)}static now(){return +now}},Math,JSON,Number,String,Array,Set,Object,
  store:{get:(k,d)=>d,set(){}},stopBrowserSiren(){},toast(){},renderTimers(){},api:async()=>({})});
 const declarations=html.match(/const DAYS = [^;]+;/)[0]+html.match(/const esc = [^\n]+/)[0];
 vm.runInContext(section('/* ---------- Time format ---------- */','/* ---------- Popovers ---------- */'),ctx);
 vm.runInContext(declarations+section('/* ---------- Alarms ---------- */','/* ---------- Edit mode ---------- */'),ctx);
 vm.runInContext('clock24=false; globalThis.inText=inText;',ctx);
 return {ctx,$,now};
}

test('repeat days read the way people say them',()=>{
 const {ctx}=page();
 assert.equal(ctx.repeatText(['Mon','Tue','Wed','Thu','Fri']),'Weekdays');
 assert.equal(ctx.repeatText(['Sun','Sat']),'Weekends','order of the stored list does not matter');
 assert.equal(ctx.repeatText(['Mon','Tue','Wed','Thu','Fri','Sat','Sun']),'Every day');
 assert.equal(ctx.repeatText(['Wed','Mon']),'Mon, Wed');
});

test('the next-alarm summary names the alarm, the day and the time',()=>{
 const {ctx,$}=page();
 vm.runInContext(`deliveryEnabled=true; alarms=[
  {id:'a',label:'Gym',enabled:true,time_hhmm:'05:45',weekdays:['Fri'],next_ring_local:'2026-11-13T05:45:00',ring_duration_sec:600},
  {id:'b',label:'Standup',enabled:true,time_hhmm:'09:00',weekdays:['Thu'],next_ring_local:'2026-11-12T09:00:00',ring_duration_sec:600}]; render();`,ctx);
 assert.equal($('next').textContent,'Next alarm in 1h 55m · Standup, today 9:00 AM');
 const at=new Date(2026,10,12,7,5);
 assert.equal(ctx.inText(new Date(2026,10,12,7,47),at),'in 42 min');
 assert.equal(ctx.inText(new Date(2026,10,12,10,5),at),'in 3h');
 assert.equal(ctx.inText(new Date(2026,10,19,4,0),at),'in 6 days');
 assert.equal(ctx.dayWord(new Date(2026,10,13,1),at),'tomorrow');assert.equal(ctx.dayWord(new Date(2026,10,11,23),at),'yesterday');
 assert.match(ctx.dayWord(new Date(2026,11,25),at),/Dec/);
});

test('alarm cards show snoozes, weekday names and a reschedule button for a missed alarm',()=>{
 const {ctx,$}=page();
 vm.runInContext(`deliveryEnabled=true; alarms=[
  {id:'snz',label:'Nap',enabled:false,time_hhmm:'07:00',target_date:'2026-11-12',weekdays:[],last_triggered_date:'2026-11-12',snooze_limit:3,snooze_minutes:9,snoozed_until:'2026-11-12T07:09:00',next_ring_local:'2026-11-12T07:09:00',ring_duration_sec:600},
  {id:'late',label:'Dentist',enabled:true,time_hhmm:'08:00',target_date:'2026-11-10',weekdays:[],next_ring_local:null,ring_duration_sec:600},
  {id:'wk',label:'Work',enabled:true,time_hhmm:'06:30',weekdays:['Mon','Tue','Wed','Thu','Fri'],next_ring_local:'2026-11-13T06:30:00',ring_duration_sec:600}]; render();`,ctx);
 const list=$('list').innerHTML;
 assert.match(list,/Snoozed until 7:09 AM/);assert.match(list,/Snooze 9 min/);
 assert.doesNotMatch(list,/class="alarm off[^"]*" data-id="snz"/,'a snoozed alarm does not look switched off');
 assert.match(list,/data-move="late"/);assert.doesNotMatch(list,/data-test="late"/);assert.match(list,/>Missed</);
 assert.match(list,/>Weekdays</);assert.match(list,/role="switch"/);
 assert.equal($('next').textContent,'Next alarm in 4 min · Nap, today 7:09 AM','a snooze counts as the next alarm');
 $('list').innerHTML='sentinel';vm.runInContext('render()',ctx);
 assert.equal($('list').innerHTML,'sentinel','an unchanged list is not rebuilt');
});

test('undo and reschedule save every field of the original alarm',()=>{
 const {ctx}=page();
 const body=ctx.alarmBody({label:'Gym',time_hhmm:'05:45',weekdays:['Tue'],recurring:1,enabled:1,code_red:1,custom_phrase:'I am up',ring_duration_sec:900,math_mission_difficulty:2,snooze_limit:3,snooze_minutes:5,volume_percent:80,wake_up_check_min:5});
 assert.deepEqual({...body},{label:'Gym',time_hhmm:'05:45',target_date:null,weekdays:['Tue'],recurring:true,enabled:true,code_red:true,custom_phrase:'I am up',volume_percent:80,ring_duration_sec:900,wake_up_check_min:5,math_mission_difficulty:2,snooze_limit:0,snooze_minutes:5});
 assert.equal(ctx.alarmBody({label:'x',time_hhmm:'07:00',ring_duration_sec:60,snooze_limit:2},{target_date:'2026-11-13',enabled:true}).target_date,'2026-11-13');
});

test('Code Red typing feedback matches the engine rules',()=>{
 const {ctx}=page();
 assert.equal(ctx.normalizePhrase('  I’m  up — now '),"I'm up - now");
 let p=ctx.phraseProgress("I'm out of bed",'I’m out');assert.deepEqual({...p},{good:7,total:14,wrong:false,done:false});
 p=ctx.phraseProgress("I'm out of bed",'I’m out ');assert.equal(p.good,8,'a space typed between words counts');assert.equal(p.wrong,false);
 p=ctx.phraseProgress("I'm out of bed","i'm");assert.equal(p.good,0);assert.equal(p.wrong,true,'case still matters');
 p=ctx.phraseProgress("I'm out of bed","I'm  out of bed ");assert.equal(p.done,true);
 assert.equal(ctx.phraseProgress('',' ').done,false);
});

test('history rows use plain words and hide bookkeeping events',()=>{
 const {ctx}=page();const now=new Date(2026,10,12,7,5);
 assert.equal(ctx.historyRow({kind:'due',ts:'2026-11-12T07:00:00'},now),null);
 let r=ctx.historyRow({kind:'dismissed',label:'Gym',detail:'after 42 s',ts:'2026-11-12T05:46:00'},now);
 assert.deepEqual([r.label,r.name,r.detail,r.when],['Stopped','Gym','after 42 s','Today 5:46 AM']);
 assert.equal(ctx.historyRow({kind:'dismissed',label:'Gym',detail:'after 300 s',ts:'2026-11-12T05:46:00'},now).detail,'after 5 min');
 r=ctx.historyRow({kind:'missed',label:'Gym',detail:'x',ts:'2026-11-11T05:45:00'},now);assert.equal(r.tone,'bad');assert.match(r.when,/^Yesterday/);
 assert.equal(ctx.historyRow({kind:'clock_changed',ts:'2026-11-12T01:00:00'},now).name,'PC clock');
 assert.equal(ctx.historyRow({kind:'audio_started',label:'Gym',detail:'Speakers',ts:'2026-11-12T05:45:00'},now).detail,'');
});

test('recent timer lengths keep three custom values, newest first',()=>{
 const {ctx}=page();const presets=[60,300];
 assert.deepEqual([...ctx.rememberDuration([],95,presets)],[95]);
 assert.deepEqual([...ctx.rememberDuration([95,120,7],120,presets)],[120,95,7]);
 assert.deepEqual([...ctx.rememberDuration([1,2,3],4,presets)],[4,1,2]);
 assert.deepEqual([...ctx.rememberDuration([1,2],300,presets)],[1,2],'a preset length is not repeated');
});

test('a running timer shows when it ends; a paused one says so',()=>{
 const {ctx}=page();
 const t={id:'t',label:'Tea',state:'running',duration_sec:300,remaining_sec:300,code_red:false};
 assert.equal(ctx.timerView(t,95,true,true).ends,'Ends 7:06 AM');
 assert.equal(ctx.timerView({...t,state:'paused'},95,true,true).ends,'Paused');
 assert.equal(ctx.timerView({...t,state:'idle'},300,true,true).ends,'');
 assert.equal(ctx.timeAfterSeconds(new Date(2026,10,12,23,59,30),45),'00:00');
});

test('snooze is part of the form, off by default and locked while Code Red is on',()=>{
 assert.match(html,/<select class="in" id="f-snooze"[^>]*>\s*<option value="0" selected>Off<\/option>/);
 const fields={};const $=id=>fields[id]??={value:'9',checked:false,hidden:true,disabled:false,classList:{toggle(){}}};
 const ctx=vm.createContext({$,document:{querySelectorAll:()=>[]}});
 vm.runInContext(html.match(/function syncSnooze\(\)\{[\s\S]*?\n\}/)[0],ctx);
 $('f-codered').checked=true;ctx.syncSnooze();assert.equal($('f-snooze').value,'0');assert.equal($('f-snooze').disabled,true);assert.equal($('snooze-hint').hidden,false);
 $('f-codered').checked=false;ctx.syncSnooze();assert.equal($('f-snooze').disabled,false);assert.equal($('snooze-hint').hidden,true);
});

test('the ringing screen offers snooze only when the alarm allows it',()=>{
 const ctx=vm.createContext({Math});vm.runInContext(html.match(/const snoozeLeft = [^\n]+/)[0].replace('const ','globalThis.'),ctx);
 assert.equal(ctx.snoozeLeft({snooze_limit:3,snoozes_used:1}),2);
 for(const extra of [{code_red:true},{is_test:true},{is_timer:true},{snooze_limit:0}])assert.equal(ctx.snoozeLeft({snooze_limit:3,snoozes_used:0,...extra}),0,JSON.stringify(extra));
 assert.match(html,/setBackgroundInert\(true\)/,'the page behind the ringing screen is inert');
 assert.match(html,/startBrowserSiren\(failed\)/,'a failed speaker helper makes the page ring');
});

test('snooze times round up to the minute and history shows them in the chosen clock style',()=>{
 const {ctx}=page();
 assert.equal(ctx.momentText(new Date(2026,10,12,7,13,20)),'7:14 AM','never earlier than the real moment');
 assert.equal(ctx.momentText(new Date(2026,10,12,7,14,0)),'7:14 AM');
 assert.equal(ctx.momentText(new Date(2026,10,12,23,59,30)),'12:00 AM');
 const r=ctx.historyRow({kind:'snoozed',label:'Gym',detail:'9 min, until 00:42 (1 of 3)',ts:'2026-11-12T00:33:00'},new Date(2026,10,12,7,5));
 assert.equal(r.detail,'9 min, until 12:42 AM (1 of 3)');
 assert.match(html,/Ringing: \$\{ringAlarm\.label\}/,'the window title says which alarm is ringing');
 assert.match(html,/fbk-bg"\)\?\.classList\.remove\("show"\)/,'the feedback box closes when an alarm rings');
});

test('missed-alarm details name the time it was due',()=>{
 const {ctx}=page();
 const r=ctx.historyRow({kind:'missed',label:'Gym',detail:"2026-11-19 05:45: the alarm engine wasn't running (PC off)",ts:'2026-11-19T14:19:00'},new Date(2026,10,19,14,20));
 assert.equal(r.detail,"was due 5:45 AM; the alarm engine wasn't running (PC off)");
 assert.match(html,/ and \$\{more\} more/,'the missed banner says when more alarms were missed than it names');
});
