// Test the actual page helper, with no browser, audio, server, or live data.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname,'../code_red_alarm/frontend/index.html'),'utf8');
const source = html.slice(html.indexOf('function nextRing('),html.indexOf('/* A one-time alarm'));
const dateKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const context = vm.createContext({Date,Number,Object,DAYS:['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],dateKey,engineNow:()=>new Date('2026-11-11T06:00:00')});
vm.runInContext(source,context);
const next=context.nextRing, now=new Date('2026-11-11T06:00:00');
test('backend occurrence, including far-future date, is authoritative',()=>{
 const alarm={enabled:true,time_hhmm:'07:15',target_date:'2026-11-12',next_ring_local:'2028-02-29T07:15:00'};
 assert.equal(+next(alarm,now),+new Date('2028-02-29T07:15:00'));
 assert.equal(next({...alarm,next_ring_local:null},now),null);
 assert.equal(next({...alarm,next_ring_local:'bad'},now),null);
 assert.equal(next({...alarm,enabled:false},now),null);
});
test('older-engine fallback supports dates far beyond eight days',()=>{
 const alarm={enabled:true,time_hhmm:'07:15',weekdays:[]};
 for(let offset=9;offset<550;offset++){
  const target=new Date(2026,10,11+offset,7,15);
  const value={...alarm,target_date:dateKey(target)};
  assert.equal(+next(value,now),+target);
  assert.equal(next({...value,last_triggered_date:value.target_date},now),null);
 }
});
test('daily and weekday fallback skip already-triggered dates',()=>{
 const alarm={enabled:true,time_hhmm:'07:15',weekdays:[]};
 assert.equal(+next(alarm,now),+new Date('2026-11-11T07:15:00'));
 assert.equal(+next({...alarm,last_triggered_date:'2026-11-11'},now),+new Date('2026-11-12T07:15:00'));
 assert.equal(+next({...alarm,weekdays:['Fri']},now),+new Date('2026-11-13T07:15:00'));
});
