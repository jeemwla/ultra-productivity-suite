const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/hub.html'),'utf8');
const between=(a,b)=>html.slice(html.indexOf(a),html.indexOf(b));

function clock(nowIso){
 // a Date whose "now" is fixed, so "today" and "tomorrow" do not depend on when the test runs
 const Real=Date,fixed=new Real(nowIso).getTime();
 return class extends Real{constructor(...a){super(...(a.length?a:[fixed]));}static now(){return fixed;}};
}

test('alarm wording says today, tomorrow, a weekday or a date',()=>{
 const ctx=vm.createContext({Date:clock('2026-11-12T01:30:00'),Math});
 vm.runInContext(between('function whenText(iso)','async function alarmCard()'),ctx);
 assert.match(ctx.whenText('2026-11-12T06:30:00'),/^today at 6:30/);
 assert.match(ctx.whenText('2026-11-13T06:30:00'),/^tomorrow at 6:30/);
 assert.match(ctx.whenText('2026-11-14T08:15:00'),/^Saturday at 8:15/);
 assert.match(ctx.whenText('2026-11-30T07:00:00'),/^Nov 30 at 7:00/);
 assert.equal(ctx.whenText('not a date'),'');
 assert.equal(ctx.inText(5*3600+60*30),'in 5 h 30 min');
 assert.equal(ctx.inText(600),'in 10 min');
 assert.equal(ctx.inText(null),'');
});

test('sensor picking prefers Tctl, GPU Core and CPU Total, and copes with missing readings',()=>{
 const ctx=vm.createContext({});
 vm.runInContext(between('function pickSensor(','async function sentinelCard()'),ctx);
 const groups=[
  {kind:'cpu',sensors:[{name:'Core #1',type:'Temperature',value:55},{name:'Core (Tctl/Tdie)',type:'Temperature',value:62.4},
   {name:'CPU Core #1',type:'Load',value:10},{name:'CPU Total',type:'Load',value:37.9}]},
  {kind:'gpu',sensors:[{name:'GPU Hot Spot',type:'Temperature',value:60},{name:'GPU Core',type:'Temperature',value:44}]},
  {kind:'storage',sensors:[{name:'Temperature',type:'Temperature',value:40}]}];
 assert.equal(ctx.pickSensor(groups,'cpu','Temperature',[/Tctl|Tdie/i,/Package/i]),62.4);
 assert.equal(ctx.pickSensor(groups,'gpu','Temperature',[/GPU Core/i]),44);
 assert.equal(ctx.pickSensor(groups,'cpu','Load',[/CPU Total/i]),37.9);
 assert.equal(ctx.pickSensor(groups,'gpu','Load',[/GPU Core/i]),null);
 assert.equal(ctx.pickSensor([{kind:'cpu',sensors:[{name:'Tctl',type:'Temperature',value:null}]}],'cpu','Temperature',[/Tctl/]),null);
 assert.equal(ctx.pickSensor([],'cpu','Temperature',[/Tctl/]),null);
});

test('hidden cards stay hidden even though cards use flex layout',()=>{
 assert.match(html,/\[hidden\]\{display:none!important\}/);
});
