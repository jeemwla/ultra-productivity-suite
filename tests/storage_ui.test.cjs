// Exercise the actual Storage renderer offline. This is not a browser/layout test.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../pc_sentinel/frontend/index.html'),'utf8');
const source=html.slice(html.indexOf('function renderStorage(){'),html.indexOf('/* ---------- network + vpn'));
function render(drives){
 const boxes={drives:{dataset:{},querySelectorAll:()=>[],innerHTML:''},vols:{innerHTML:''}};
 const temps={};boxes.drives.querySelector=sel=>{const k=sel.match(/"(\d+)"/)[1];return temps[k]??={textContent:''};};boxes.temps=temps;
 const context=vm.createContext({snap:{hardware:{drives,groups:[]}},$:id=>boxes[id],setText:(el,v)=>{if(el)el.textContent=v;},setHtml:(el,v)=>{el.innerHTML=v;},n0:v=>String(Math.round(v)),n1:v=>Number(v).toFixed(1),esc:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
 vm.runInContext(source,context);context.renderStorage();return boxes;
}
test('physical capacity and volume capacity/free are separately labeled',()=>{
 const boxes=render([{name:'Example 1TB',kind:'SSD',health:'Healthy',id:'0',size_bytes:1e12,life_left:99,temp:40,volumes:[{mount:'C:',filesystem:'NTFS',size_bytes:990e9,free_bytes:0,used_bytes:990e9}]}]);
 assert.match(boxes.drives.innerHTML,/931\.3 GiB device capacity/);
 assert.match(boxes.vols.innerHTML,/0\.0 GiB free/);
 assert.match(boxes.vols.innerHTML,/922\.0 GiB volume total/);
 assert.match(boxes.vols.innerHTML,/aria-valuenow="100"/);
 assert.match(boxes.drives.innerHTML,/Advanced drive details/);
});
test('unknown values remain unavailable and device labels are escaped',()=>{
 const boxes=render([{name:'<script>bad</script>',kind:'SSD',health:'Unknown',size_gb:1000,volumes:[{mount:'X:',size_bytes:null,free_bytes:null,used_bytes:null}]}]);
 assert.match(boxes.drives.innerHTML,/&lt;script>/);
 assert.match(boxes.drives.innerHTML,/pill idle/);
 assert.match(boxes.vols.innerHTML,/Unavailable free/);
 assert.doesNotMatch(boxes.vols.innerHTML,/aria-valuenow/);
});
test('empty storage never invents volumes or readings',()=>{
 const boxes=render([]);
 assert.match(boxes.drives.innerHTML,/No drives reported/);
 assert.match(boxes.vols.innerHTML,/No volumes reported/);
});
test('drive temperatures update in place without rebuilding the cards',()=>{
 const d={name:'Example',kind:'SSD',health:'Healthy',id:'0',size_bytes:1e12,life_left:99,temp:40,volumes:[]};
 const boxes=render([d]);assert.equal(boxes.temps['0'].textContent,'40°C');
 assert.match(boxes.drives.innerHTML,/role="meter" aria-label="Example rated endurance remaining"[^>]*aria-valuenow="99"/);
 const sig=boxes.drives.dataset.sig,sig2=JSON.stringify([{...d,temp:null}]);assert.equal(sig,sig2,'temperature is left out of the rebuild signature');
});
