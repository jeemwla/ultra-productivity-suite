const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../pc_sentinel/frontend/index.html'),'utf8');
const source=html.slice(html.indexOf('function prepareReport('),html.indexOf('\n$("ab-export").insertAdjacentHTML'));
function prepare(input,full=false){const c=vm.createContext({});vm.runInContext(source,c);return c.prepareReport(input,full);}
test('default report omits processes and redacts nested machine, storage and path identifiers',()=>{
 const input={snapshot:{hardware:{device:{hostname:'OWNER-PC',cpu:'Example CPU'},drives:[{serial:'secret-serial',size_bytes:1000}],groups:[{name:'C:\\Users\\Alice\\project',value:0}]},network:{public_ip:'192.0.2.1',mac_address:'aa:bb',note:'owner-pc network'}},apps:{all_apps:[{exe:'C:\\Users\\Alice\\app.exe'}]},displays:{displays:[{model:'Model A',device_path:'private-path'}]}};
 const r=prepare(input),text=JSON.stringify(r);assert.equal(r.apps,undefined);assert.equal(r.snapshot.hardware.device.cpu,'Example CPU');assert.equal(r.snapshot.hardware.groups[0].value,0);assert.equal(r.displays.displays[0].model,'Model A');
 for(const privateText of ['OWNER-PC','Alice','secret-serial','192.0.2.1','aa:bb','private-path'])assert.ok(!text.includes(privateText));
 assert.equal(input.snapshot.hardware.device.hostname,'OWNER-PC','input is not mutated');assert.equal(r.privacy.identifiers_included,false);
});
test('identifier opt-in preserves available details and marks the report',()=>{const r=prepare({snapshot:{hardware:{device:{hostname:'PC'}}},apps:{processes:[{pid:1,exe:'C:\\App.exe'}]}},true);assert.equal(r.snapshot.hardware.device.hostname,'PC');assert.equal(r.apps.processes[0].pid,1);assert.equal(r.privacy.identifiers_included,true);});
test('null snapshots, missing readings and unicode user paths remain safe',()=>{const r=prepare({snapshot:null,notes:['/home/用户/project','C:/Users/ユーザー/file'],value:null});assert.equal(r.snapshot,null);assert.ok(!JSON.stringify(r).includes('ユーザー'));assert.equal(r.value,null);});
