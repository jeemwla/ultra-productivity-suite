const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../pc_sentinel/frontend/index.html'),'utf8');
const source=html.slice(html.indexOf('const hm ='),html.indexOf('/* ---------- alerts ---------- */'));
function render(pack,groups=[]){
 const boxes={};const ctx=vm.createContext({pack,snap:{hardware:{groups}},bhist:null,bright:null,document:{activeElement:null},
  $:id=>id==='bt-br'?null:boxes[id]??={innerHTML:'',textContent:''},n0:Math.round,n1:v=>Number(v).toFixed(1),esc:String});
 vm.runInContext(source,ctx);ctx.renderBattery();return boxes['bt-body'].innerHTML;
}
test('desktop without a reported battery never claims charged or invents a charge reading',()=>{
 const result=render({has_battery:false});assert.match(result,/No battery reported/);assert.match(result,/expected on desktops/);assert.doesNotMatch(result,/>charged</);assert.doesNotMatch(result,/>0<small/);
});
test('missing battery state and power stay unavailable while real zero watts remains zero',()=>{
 const missing=render({has_battery:true,rate_w:null});assert.match(missing,/State unavailable/);assert.doesNotMatch(missing,/0 W/);
 const zero=render({has_battery:true,rate_w:0,state:'Plugged in'});assert.match(zero,/0.0 W/);assert.match(zero,/Plugged in/);
});
