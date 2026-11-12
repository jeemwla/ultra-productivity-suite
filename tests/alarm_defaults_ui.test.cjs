const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../code_red_alarm/frontend/index.html'),'utf8');
function harness(){
 const fields={};const $=id=>fields[id]??={value:'',checked:false,hidden:false,textContent:'',classList:{toggle(){}}};
 $('days').children=[];$('days').appendChild=b=>$('days').children.push(b);$('f-time').value='07:00';
 const ctx=vm.createContext({$,document:{querySelectorAll:()=>[],createElement:()=>{const attr={};return {dataset:{},setAttribute(k,v){attr[k]=String(v)},getAttribute:k=>attr[k]}}},render(){},editingId:null});
 const days=html.match(/const DAYS = [^;]+;/)[0];
 vm.runInContext(days+html.slice(html.indexOf('const daysEl ='),html.indexOf('/* ---------- Alarms ---------- */')),ctx);
 $('form').reset=()=>{for(const id of ['f-codered','lock','browser-ring'])$(id).checked=/\bchecked\b/.test(html.match(new RegExp('<input[^>]*id="'+id+'"[^>]*>'))[0]);};
 vm.runInContext(html.match(/function endEdit\(\)\{[\s\S]*?\n\}/)[0],ctx);
 return {ctx,$};
}
test('new draft has no repeat days and shows a concrete one-time date',()=>{
 const h=harness();
 assert.equal(vm.runInContext('selectedDays().length',h.ctx),0);h.ctx.syncDate();
 assert.equal(h.$('date-field').hidden,false);assert.match(h.$('f-date').value,/^\d{4}-\d{2}-\d{2}$/);
});
test('canceling an edited recurring Code Red alarm restores the opt-in draft',()=>{
 const h=harness();h.ctx.setDays(['Mon','Fri']);h.$('f-codered').checked=true;h.ctx.endEdit();
 assert.equal(vm.runInContext('selectedDays().length',h.ctx),0);assert.equal(h.$('f-codered').checked,false);assert.equal(h.$('lock').checked,false);assert.equal(h.$('browser-ring').checked,false);
 assert.match(html,/<option value="0" selected>Off<\/option>/);
});
