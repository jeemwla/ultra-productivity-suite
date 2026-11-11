const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');
test('a failed Dock render retries and never counts as a successful frame',async()=>{
 const source=html.slice(html.indexOf('async function poll(){'),html.indexOf('/* ---------------- Update dot'));
 const context=vm.createContext({JSON,renderCalls:0,reports:0,invoke:async()=>({organize:true,windows:[{hwnd:42,category:'Tools',active:false,title:'Example'}]}),
  render:()=>{context.renderCalls++;if(context.renderCalls===1)throw new Error('fixture render failure');},report:()=>context.reports++});
 vm.runInContext("let state={},sig='';\n"+source,context);
 await context.poll();assert.equal(context.renderCalls,1);assert.equal(context.reports,0);assert.equal(vm.runInContext('sig',context),'');
 await context.poll();assert.equal(context.renderCalls,2);assert.notEqual(vm.runInContext('sig',context),'');
 await context.poll();assert.equal(context.renderCalls,2);assert.equal(context.reports,1);
});
