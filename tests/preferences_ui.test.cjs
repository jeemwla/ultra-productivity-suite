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
test('section-number shortcuts never hijack selects or editable fields',()=>{
 let handler,visits=[];
 ctx.document={addEventListener(_type,fn){handler=fn;}};ctx.$=()=>({hidden:false});ctx.go=p=>visits.push(p);
 vm.runInContext(html.slice(html.indexOf('// keyboard: 1-9'),html.indexOf('// swipe between')),ctx);
 for(const tag of ['input','textarea','select','[contenteditable=true]'])handler({key:'1',target:{closest:selector=>selector.split(',').includes(tag)}});
 assert.deepEqual(visits,[]);
 handler({key:'1',target:{closest:()=>false}});assert.deepEqual(visits,['overview']);
});
test('settings cannot save an uninitialized default draft; failed loading offers a read-only retry',async()=>{
 const boxes={};const $=id=>boxes[id]??={checked:false,value:'',disabled:false,hidden:false,textContent:'',addEventListener(){}};
 let fail=true,calls=[];
 const state=vm.createContext({$,snap:null,page:'settings',go(){},document:{querySelectorAll:()=>[]},api:async(p,o)=>{calls.push([p,o]);if(fail)throw Error('offline');return{...state.defaultPreferences(),revision:'123',storage_status:'ok',router_vpn:true};}});
 vm.runInContext(pages+pure+html.slice(html.indexOf('let prefs=defaultPreferences()'),html.indexOf('/* ---------- navigation actions ---------- */')),state);
 assert.equal($('settings-fields').disabled,true);assert.equal($('settings-save').disabled,true);
 await state.loadPreferences();assert.equal($('settings-fields').disabled,true);assert.match($('settings-save').textContent,/Retry loading/);
 fail=false;await $('settings-save').onclick();assert.equal($('settings-fields').disabled,false);assert.equal($('extra-vpn').checked,true);
 assert.ok(calls.every(([,options])=>options===undefined),'retry must GET stored state rather than POST defaults');
});
