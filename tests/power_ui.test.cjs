// Exercise actual power presentation code. These checks do not certify layout or sensors.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../pc_sentinel/frontend/index.html'),'utf8');
const source=html.slice(html.indexOf('const validWatts ='),html.indexOf('/* ---------- performance ---------- */'));
function harness(power,device={type:'desktop'},options={}){
 const boxes={},events={};const box=id=>boxes[id]??=( {textContent:'',innerHTML:'',hidden:false,value:'',dataset:{},addEventListener(type,fn){(events[id]??={})[type]=fn;}} );
 let pendingFrame=null,now=0;
 const ctx=vm.createContext({snap:{hardware:{power,device}},$:box,document:{activeElement:null},api:options.api,
  performance:{now:()=>now},requestAnimationFrame:fn=>(pendingFrame=fn,1),auto:String,
  n0:v=>String(Math.round(v)),n1:v=>Number(v).toFixed(1),esc:v=>String(v).replaceAll('<','&lt;').replaceAll('"','&quot;'),tween:(e,v)=>e.textContent=v==null?'–':String(v),setSt:(id,l,t)=>{box(id).textContent=t;}});
 if(options.realTween)vm.runInContext(html.slice(html.indexOf('const TW ='),html.indexOf('function smooth(')),ctx);
 vm.runInContext(source,ctx);return {ctx,boxes,box,
  edit(id,value){const el=box(id);el.value=value;events[id]?.input?.({target:el,currentTarget:el});},
  frame(at){now=at;const fn=pendingFrame;pendingFrame=null;fn?.(at);}};
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
test('unknown and invalid readings never appear as zero power',()=>{
 for(const p of [null,{}, {measured:{CPU:null,GPU:NaN,invalid:-1}}]){
  const h=harness(p);h.ctx.renderPower();assert.equal(h.box('pw-total').textContent,'–');assert.match(h.box('pw-coverage').textContent,/unknown, never zero/);assert.equal(h.box('psu-meter').hidden,true);
 }
});
test('real zero watts survives, partial readings do not imply a total',()=>{
 const h=harness({measured:{'CPU package':0,'Graphics card':null},measured_subtotal_w:0,coverage:{unknown_components:['GPU']}});h.ctx.renderPower();
 assert.equal(h.box('pw-total').textContent,'0');assert.equal(h.box('pw-quality').textContent,'Measured component subtotal');assert.match(h.box('pw-coverage').textContent,/Missing: GPU/);assert.doesNotMatch(h.box('pw-wall').textContent,/≈/);
});
test('model is labeled, wall estimate discloses its assumption, no adequacy diagnosis',()=>{
 const h=harness({measured:{CPU:50,GPU:100},estimated:{Other:30},modeled_dc_w:180,modeled_wall_w:200,wall_model:{efficiency:.9},coverage:{component_readings_complete:true}});
 vm.runInContext('powerContext={rated_watts:1000,model:"Example",storage_status:"ok"}',h.ctx);h.ctx.renderPower();
 assert.equal(h.box('pw-quality').textContent,'Estimated system draw');assert.match(h.box('pw-wall').textContent,/90% efficiency\. Not metered/);assert.match(h.box('psu-peak').textContent,/not current consumption/);assert.doesNotMatch(h.box('psu-st').textContent,/plenty|spare|near its limit/i);
});
test('laptop hides desktop context and retains battery guidance',()=>{
 const h=harness({}, {type:'laptop'});h.ctx.renderPower();assert.equal(h.box('desktop-psu').hidden,true);assert.equal(h.box('laptop-power-note').hidden,false);
});
test('active and unsaved PSU edits survive streaming telemetry',()=>{
 const h=harness({});vm.runInContext('powerContext={rated_watts:1000,model:"Saved model",storage_status:"ok"}',h.ctx);
 h.box('psu-w').value='850';h.box('psu-w').dataset.dirty='true';h.ctx.renderPower();assert.equal(h.box('psu-w').value,'850');assert.equal(h.box('psu-model').value,'Saved model');
});

test('losing power readings cancels a pending numeric animation',()=>{
 const h=harness({measured:{'CPU package':100},measured_subtotal_w:100},{type:'desktop'},{realTween:true});
 h.box('pw-total')._c=40;h.ctx.renderPower();h.frame(100);
 h.ctx.snap.hardware.power={};h.ctx.renderPower();
 assert.equal(h.box('pw-total').textContent,'–');
 h.frame(400);assert.equal(h.box('pw-total').textContent,'–','an older animation must not restore stale watts');
});

test('partial multi-GPU legend and stack include every known reading in the subtotal',()=>{
 const h=harness({measured:{'CPU package':40,'Graphics card':null},measured_subtotal_w:140,
  gpu_readings:[{id:'A',name:'GPU A',watts:100},{id:'B',name:'GPU B',watts:null}],
  coverage:{component_readings_complete:false,unknown_components:['GPU 2 aggregate power']}});
 h.ctx.renderPower();assert.equal(h.box('pw-total').textContent,'140');
 const legendWatts=[...h.box('pw-legend').innerHTML.matchAll(/class="num">([\d.]+) W/g)].map(m=>Number(m[1]));
 assert.equal(legendWatts.reduce((a,b)=>a+b,0),140,'legend must explain the entire displayed component subtotal');
 assert.match(h.box('pw-legend').innerHTML,/GPU A/);
 const stackWidths=[...h.box('pw-stack').innerHTML.matchAll(/width:([\d.]+)%/g)].map(m=>Number(m[1]));
 assert.equal(stackWidths.length,2);assert.ok(Math.abs(stackWidths.reduce((a,b)=>a+b,0)-100)<.001);
 assert.match(h.box('pw-coverage').textContent,/Missing: GPU 2/);assert.doesNotMatch(h.box('pw-quality').textContent,/Estimated system/);
});

test('an older initial GET cannot replace a successful save or clear',async()=>{
 for(const clear of [false,true]){
  const get=deferred(),written={rated_watts:clear?null:1000,model:null,form_factor:null,storage_status:'ok'};
  const h=harness({}, {type:'desktop'},{api:(_p,o)=>o?Promise.resolve(written):get.promise});
  const loading=h.ctx.loadPowerContext();h.edit('psu-w','1000');await h.ctx.savePowerContext(clear);
  get.resolve({rated_watts:750,model:'Older PSU',storage_status:'ok'});await loading;h.ctx.renderPower();
  assert.equal(String(h.box('psu-w').value),clear?'':'1000','late GET must not make saved state look reverted');
  assert.notEqual(h.box('psu-model').value,'Older PSU');
 }
});

test('a stale initial GET failure cannot replace successful save feedback',async()=>{
 const get=deferred();const h=harness({}, {type:'desktop'}, {api:(_p,o)=>o?Promise.resolve({rated_watts:1000,storage_status:'ok'}):get.promise});
 const loading=h.ctx.loadPowerContext();h.edit('psu-w','1000');await h.ctx.savePowerContext(false);
 get.reject(new Error('initial read timed out'));await loading;h.ctx.renderPower();
 assert.equal(String(h.box('psu-w').value),'1000');assert.match(h.box('psu-save-status').textContent,/Saved locally/);
});

test('new PSU drafts entered during a save survive its response and later telemetry',async()=>{
 const post=deferred();let request;
 const h=harness({}, {type:'desktop'}, {api:(_p,o)=>{request=JSON.parse(o.body);return post.promise;}});
 h.edit('psu-w','1000');h.edit('psu-model','Original');h.edit('psu-size','SFX');const saving=h.ctx.savePowerContext(false);
 h.edit('psu-w','850');h.edit('psu-model','New draft');h.edit('psu-size','ATX');
 post.resolve({...request,storage_status:'ok'});await saving;h.ctx.renderPower();
 assert.deepEqual(request,{rated_watts:1000,model:'Original',form_factor:'SFX'});
 assert.equal(h.box('psu-w').value,'850');assert.equal(h.box('psu-model').value,'New draft');assert.equal(h.box('psu-size').value,'ATX');
});

test('failed PSU saves preserve newer edits and show the failure',async()=>{
 const post=deferred();const h=harness({}, {type:'desktop'}, {api:()=>post.promise});
 h.edit('psu-w','1000');const saving=h.ctx.savePowerContext(false);h.edit('psu-w','850');
 post.reject(new Error('local storage unavailable'));await saving;h.ctx.renderPower();
 assert.equal(h.box('psu-w').value,'850');assert.match(h.box('psu-save-status').textContent,/Not saved: local storage unavailable/);
});
