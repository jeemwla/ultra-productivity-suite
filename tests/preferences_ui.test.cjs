const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../pc_sentinel/frontend/index.html'),'utf8');
const pages=html.slice(html.indexOf('const PAGES ='),html.indexOf('$("navs").innerHTML'));
const pure=html.slice(html.indexOf('function defaultPreferences'),html.indexOf('let prefs=defaultPreferences()'));
const ctx=vm.createContext({});vm.runInContext(pages+pure,ctx);
test('every page combination keeps Settings reachable and Battery obeys its explicit mode',()=>{
 for(let bits=0;bits<512;bits++)for(const battery of [null,true,false])for(const hasBattery of [true,false]){
   const value=ctx.defaultPreferences();Object.keys(value.pages).filter(p=>p!=='battery').forEach((p,i)=>value.pages[p]=!!(bits&(1<<i)));value.pages.battery=battery;
   assert.equal(ctx.pageVisible('settings',value,hasBattery),true);
   assert.equal(Boolean(ctx.pageVisible('battery',value,hasBattery)),battery===null?hasBattery:battery);
   assert.equal(Boolean(ctx.pageVisible('speed',value,hasBattery)),value.pages.speed);
 }
});
test('revision comparison rejects stale socket or GET responses without losing precision',()=>{
 assert.equal(ctx.freshPreferences({revision:'1794441000000000002'},{revision:'1794441000000000001'}),false);
 assert.equal(ctx.freshPreferences({revision:'1794441000000000001'},{revision:'1794441000000000002'}),true);
 assert.equal(ctx.freshPreferences({revision:'2'},{revision:'malformed'}),false);
});
test('public router defaults are off and default page objects do not share mutations',()=>{
 const a=ctx.defaultPreferences(),b=ctx.defaultPreferences();a.pages.power=false;
 assert.equal(b.pages.power,true);assert.equal(b.router_vpn,false);assert.equal(b.router_ping,false);assert.equal(b.pages.battery,null);
});
