const {test}=require('node:test');
const assert=require('node:assert/strict');
const BV=require('../pc_sentinel/frontend/battery_view.js');
const render=(pack,hist=null,bright=null,level=null)=>BV.html(pack,hist,bright,level);
test('desktop without a reported battery never claims charged or invents a charge reading',()=>{
 const result=render({has_battery:false});assert.match(result,/No battery reported/);assert.match(result,/expected on desktops/);assert.doesNotMatch(result,/>charged</);assert.doesNotMatch(result,/>0<small/);
});
test('missing battery state and power stay unavailable while real zero watts remains zero',()=>{
 const missing=render({has_battery:true,rate_w:null});assert.match(missing,/State unavailable/);assert.doesNotMatch(missing,/0 W/);
 const zero=render({has_battery:true,rate_w:0,state:'Plugged in'});assert.match(zero,/0.0 W/);assert.match(zero,/Plugged in/);
});
test('a battery with nothing reported says "not reported" for every value and invents none',()=>{
 const out=render({has_battery:true});
 for(const label of ['Health','Charge cycles','Power','Time left','Wear','Remaining energy','Average daily use','Charger'])assert.match(out,new RegExp(label));
 assert.ok((out.match(/not reported/g)||[]).length>=7,'each missing value is marked');
 assert.doesNotMatch(out,/NaN|undefined|null/);assert.doesNotMatch(out,/\d+(\.\d)? W</);
});
test('charging shows watts into the battery, time to full and capacity in watt-hours',()=>{
 const out=render({has_battery:true,charging:true,plugged_in:true,state:'Charging',charge_pct:50,rate_w:45,voltage_v:16.8,minutes_to_full:46,
  design_mwh:76000,full_mwh:68400,remaining_mwh:34200,health_pct:90,wear_pct:10,cycles:150,
  daily_use:{pct_per_day:28.3,on_battery_hours_per_day:4.3,days:3,drain_samples:3}});
 assert.match(out,/Charging at/);assert.match(out,/45\.0 W/);assert.match(out,/into the battery/);assert.match(out,/0h 46m/);
 assert.match(out,/68\.4 Wh now, 76\.0 Wh when new/);assert.match(out,/>150</);assert.match(out,/28\.3% <small>per day/);assert.match(out,/4\.3 h a day on battery · last 3\.0 days/);
});
test('on battery shows draw and time left; a missing time left is not reported',()=>{
 const out=render({has_battery:true,charging:false,plugged_in:false,state:'On battery',rate_w:-17.1,minutes_left:120});
 assert.match(out,/Using/);assert.match(out,/17\.1 W/);assert.match(out,/2h 00m/);assert.match(out,/Unplugged/);
 const unknown=render({has_battery:true,charging:false,plugged_in:false,rate_w:-5});
 assert.match(unknown,/Time left<\/div><div class="big num"><span class="nr">not reported/);
});
test('maker and state text is escaped',()=>{
 const out=render({has_battery:true,maker:'<img src=x>',state:'<b>x</b>'});
 assert.doesNotMatch(out,/<img src=x>|<b>x<\/b>/);assert.match(out,/&lt;img/);
});
test('health history needs two points and draws a labelled chart',()=>{
 assert.match(render({has_battery:true},{history:[{health_pct:90}]}),/enough capacity history/);
 const out=render({has_battery:true},{history:[{start:'2026-10-01T00:00:00Z',health_pct:92.5},{end:'2026-11-05T00:00:00Z',health_pct:90}]});
 assert.match(out,/aria-label="Battery health from 92\.5% to 90\.0%"/);assert.match(out,/2026-10-01 · 92\.5%/);
});
