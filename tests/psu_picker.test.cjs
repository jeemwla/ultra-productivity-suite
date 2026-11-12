const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const picker=require('../pc_sentinel/frontend/psu_picker.js');
const records=JSON.parse(fs.readFileSync(path.join(__dirname,'../pc_sentinel/backend/psu_catalog.json'),'utf8')).records;
test('every explicit variant survives the real brand/wattage/form-factor cascade',()=>{
 let count=0;
 for(const r of records)for(const watt of r.wattage.variants_w){
   const filtered=picker.narrow(records,{brand:r.brand,watt:String(watt),size:r.form_factor});
   assert.ok(filtered.some(x=>x.id===r.id));
   const d=picker.draft(r,String(watt));assert.equal(d.rated_watts,watt);assert.equal(d.form_factor,r.form_factor);
   assert.ok(d.model.length<=120);count++;
 }
 assert.ok(count>2000);
});
test('every range stays a series and clears any previously entered exact rating',()=>{
 let count=0;
 for(const r of records.filter(r=>r.wattage.range)){
   const span=r.wattage.range,key=`range:${span.min_w}:${span.max_w}`;
   assert.equal(picker.wattOptions([r]).length,1);
   assert.equal(picker.wattOptions([r])[0][0],key);
   assert.equal(picker.draft(r,key).rated_watts,'');
   assert.equal(picker.matchesWatt(r,String(span.min_w)),false);count++;
 }
 assert.ok(count>200);
});
test('brand and size filtering never leaks another brand or form factor',()=>{
 const corsair=picker.narrow(records,{brand:'Corsair',watt:'1000',size:'SFX'});
 assert.ok(corsair.some(r=>r.series==='SF'));
 assert.ok(corsair.every(r=>r.brand==='Corsair'&&r.form_factor==='SFX'&&r.wattage.variants_w.includes(1000)));
 assert.deepEqual(picker.narrow(records,{brand:'no such brand'}),[]);
 assert.throws(()=>picker.draft(corsair[0],'9999'));
});
test('ambiguous series labels include the year, ATX generation and platform',()=>{
 const lian=records.filter(r=>r.brand==='Lian Li'&&r.series==='SP'&&r.wattage.variants_w.includes(850));
 assert.ok(lian.length>=2);
 assert.equal(new Set(lian.map(picker.modelLabel)).size,lian.length);
});
const ids=hits=>hits.map(h=>h.record.id);
test('smart search finds the same units in any word order',()=>{
 const a=picker.search(records,'corsair 1000 sfx',50),b=picker.search(records,'sfx corsair 1000',50),c=picker.search(records,'1000 SFX Corsair',50);
 assert.ok(a.length>0);
 assert.deepEqual(new Set(ids(a)),new Set(ids(b)));assert.deepEqual(new Set(ids(a)),new Set(ids(c)));
 assert.ok(a.every(h=>h.record.brand==='Corsair'&&h.record.form_factor.startsWith('SFX')));
 assert.ok(a.some(h=>h.record.series==='SF'&&h.record.wattage.variants_w.includes(1000)));
 assert.equal(a[0].record.form_factor,'SFX','exact SFX ranks above SFX-L');
 assert.ok(a.filter(h=>h.record.wattage.variants_w.includes(1000)).every(h=>h.watt==='1000'),'the typed wattage is preselected');
});
test('smart search tolerates partial words, one-letter typos and a W suffix',()=>{
 const base=new Set(ids(picker.search(records,'corsair 1000 sfx',50)));
 assert.deepEqual(new Set(ids(picker.search(records,'corsiar 1000 sfx',50))),base,'swapped letters');
 assert.deepEqual(new Set(ids(picker.search(records,'corsair 1000W sfx',50))),base,'1000W equals 1000');
 assert.ok(picker.search(records,'cors',50).every(h=>/corsair/i.test(h.record.brand)),'prefix');
 assert.ok(picker.search(records,'lian li sfx',50).every(h=>h.record.brand==='Lian Li'&&h.record.form_factor.startsWith('SFX')));
});
test('a number inside a series range finds the series but keeps it a range',()=>{
 const r=records.find(x=>x.wattage.range&&x.wattage.range.max_w-x.wattage.range.min_w>=100);
 const mid=Math.round((r.wattage.range.min_w+r.wattage.range.max_w)/2/10)*10;
 const hit=picker.search(records,`${r.brand} ${mid}`,500).find(h=>h.record.id===r.id);
 assert.ok(hit,'range series found');assert.ok(hit.watt.startsWith('range:'),'no invented exact model');
});
test('every word must match, and nonsense returns nothing',()=>{
 assert.deepEqual(picker.search(records,'zzzzqq',10),[]);
 assert.deepEqual(picker.search(records,'',10),[]);
 assert.deepEqual(picker.search(records,'corsair 99999',10),[]);
 assert.ok(picker.search(records,'corsair',10).length===10,'results are capped');
});
test('a single digit such as the 3 in "atx 3" matches ATX 3.x',()=>{
 const hits=picker.search(records,'seasonic 1000 atx 3',50);
 assert.ok(hits.length>0);
 assert.ok(hits.every(h=>h.record.brand==='Seasonic'&&h.record.atx_version==='ATX 3.x'));
});
