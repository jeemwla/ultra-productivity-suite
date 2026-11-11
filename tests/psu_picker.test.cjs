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
