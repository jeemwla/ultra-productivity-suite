'use strict';
// Owner item 10 (2026-11-20): an app with several windows must act like the native taskbar
// button, not open like a folder. The inline window list stays as an option, off by default.
const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'..','ultra_dock','ui','index.html'),'utf8');
const between=(a,b)=>{const i=html.indexOf(a);const j=html.indexOf(b,i);assert.ok(i>=0&&j>i,`source marker ${a}`);return html.slice(i,j);};
const exe='C:\Apps\Chrome\chrome.exe';
const wins=n=>Array.from({length:n},(_,i)=>({hwnd:100+i,title:`Tab ${i} - Google Chrome`,active:false}));
function page(inline){
 const calls=[];let renders=0;
 const ctx=vm.createContext({state:{layout:{inline_window_list:inline}},esc:s=>String(s),img:()=>'<img>',
  invoke:async(name,args)=>{calls.push([name,JSON.parse(JSON.stringify(args??null))]);return true;},
  render:()=>{renders++;},setTimeout:()=>{},setOpen:()=>{},closeAll:()=>{},$:()=>({textContent:''}),open:null});
 vm.runInContext(between('function shortTitle(t, n){','/* Free taskbar space')+'\n'+between('let menuOpening = false;','$("strip").addEventListener("click"')+'\nthis.getPick=()=>pick;this.setPick=v=>{pick=v;};',ctx);
 return {ctx,calls,renders:()=>renders};
}
function click(p,hwnd,expanded){
 const w={dataset:{exe,h:String(hwnd)},title:'',classList:{add(){}},hasAttribute:n=>n==='aria-expanded'&&expanded};
 return p.ctx.handleDockClick({target:{closest:s=>s==='.win'?w:null}});
}
for(const n of [1,2,5]){
 test(`an app with ${n} window(s) is one native-style button by default`,async()=>{
  const p=page(false);const out=p.ctx.appButton(exe,wins(n));
  assert.ok(!out.includes('class="pick'),'no inline window list');
  assert.ok(!out.includes('aria-expanded'),'the button does not expand like a folder');
  assert.ok(out.includes('data-h="100"'),'the button targets the most recently used window');
  await click(p,100,out.includes('aria-expanded'));
  assert.deepEqual(p.calls,[['app_button_click',{hwnd:100}]],'a click brings up or minimizes that window, like the native button');
  assert.equal(p.ctx.getPick(),null);
 });
}
test('the inline window list is still available as a setting',async()=>{
 const p=page(true);
 assert.ok(p.ctx.appButton(exe,wins(5)).includes('aria-expanded="false"'));
 p.ctx.setPick(exe);
 const out=p.ctx.appButton(exe,wins(5));
 assert.equal((out.match(/class="wt /g)||[]).length,5,'all five windows are listed when the option is on');
 assert.ok(!page(true).ctx.appButton(exe,wins(1)).includes('aria-expanded'),'one window never expands');
});
