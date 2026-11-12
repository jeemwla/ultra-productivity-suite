const {test}=require('node:test');
const assert=require('node:assert/strict');
const L=require('../pc_sentinel/frontend/edit_layout.js');
const all=()=>true, noBattery=id=>id!=='battery';
function overlaps(placed){const seen=new Set();for(const [id,p] of Object.entries(placed))for(let r=p.y;r<p.y+p.h;r++)for(let c=p.x;c<p.x+p.w;c++){const k=c+','+r;if(seen.has(k))return id;seen.add(k);}return null;}
function inBounds(placed,cols){return Object.values(placed).every(p=>p.x>=0&&p.x+p.w<=cols&&p.y>=0&&p.w>=1&&p.h>=1);}

test('default layout fills full rows on a desktop with no overlaps',()=>{
 const p=L.flow(L.defaults(),12,noBattery);
 assert.deepEqual([p.cpu,p.pw,p.apps,p.quick,p.cool].map(q=>[q.x,q.y,q.w]),[[0,0,3],[9,0,3],[0,2,8],[8,2,4],[8,7,4]]);
 assert.equal(p.battery,undefined,'battery card only where a battery is shown');
 for(const cols of [12,6,2])assert.equal(overlaps(L.flow(L.defaults(),cols,all)),null);
});
test('dropping near a slot picks that slot, and neighbours move out of the way',()=>{
 const d=L.defaults();
 const i=L.nearestIndex(d,'cool',0,0,12,noBattery);assert.equal(i,0);
 const moved=L.moveVisible(d,'cool',i,noBattery),p=L.flow(moved,12,noBattery);
 assert.deepEqual([p.cool.x,p.cool.y],[0,0]);assert.ok(p.cpu.x>=4||p.cpu.y>0,'the first tile slid aside');assert.equal(overlaps(p),null);
 // a near drop (off by a fraction of a cell, rounded by the controller) still lands in the intended slot
 const near=L.nearestIndex(d,'cpu',9,1,12,noBattery);const q=L.flow(L.moveVisible(d,'cpu',near,noBattery),12,noBattery);
 assert.deepEqual([q.cpu.x,q.cpu.y],[9,0]);
});
test('thousands of random drags and resizes keep every layout valid in every column count',()=>{
 let seed=7;const rnd=n=>{seed=(seed*1103515245+12345)&0x7fffffff;return seed%n;};
 const ids=L.CARDS.map(c=>c.id);let count=0;
 for(const cols of [12,6,2])for(const vis of [all,noBattery]){
  let lay=L.defaults();
  for(let step=0;step<800;step++){
   const id=ids[rnd(ids.length)];if(!vis(id))continue;
   if(rnd(3)===0)lay=L.resize(lay,id,rnd(cols+3)-1,rnd(14)-1,cols);
   else lay=L.moveVisible(lay,id,L.nearestIndex(lay,id,rnd(cols),rnd(20),cols,vis),vis);
   const p=L.flow(lay,cols,vis);assert.equal(overlaps(p),null);assert.ok(inBounds(p,cols));
   assert.deepEqual([...lay.order].sort(),[...ids].sort(),'no card is lost or duplicated');count++;
  }
 }
 assert.ok(count>3000,String(count));
});
test('resize snaps to whole cells and respects each card\'s limits',()=>{
 let lay=L.resize(L.defaults(),'apps',2.6,99,12);assert.deepEqual(L.sizeOf(lay,'apps',12),[4,12]);
 lay=L.resize(lay,'cpu',40,0,12);assert.deepEqual(L.sizeOf(lay,'cpu',12),[12,2]);
 lay=L.resize(lay,'cpu',0,3,2);assert.deepEqual(L.sizeOf(lay,'cpu',2),[1,3]);
 assert.deepEqual(L.sizeOf(lay,'cpu',12),[12,2],'sizes are kept per column count');
});
test('keyboard moves step through the order and jump rows',()=>{
 const d=L.defaults();
 assert.equal(L.keyMove(d,'gpu','ArrowLeft',12,noBattery).order[0],'gpu');
 assert.equal(L.keyMove(d,'cpu','ArrowLeft',12,noBattery).order[0],'cpu','already first stays first');
 const down=L.keyMove(d,'cpu','ArrowDown',12,noBattery),p=L.flow(down,12,noBattery);assert.ok(p.cpu.y>=2,'moved below the first row');
 const up=L.keyMove(down,'cpu','ArrowUp',12,noBattery);assert.equal(L.flow(up,12,noBattery).cpu.y,0);
});
test('saved layouts are repaired: unknown cards dropped, new ones added, bad sizes clamped',()=>{
 const n=L.normalize({v:1,order:['net','ghost','net','apps'],sizes:{'12':{apps:[99,0],cpu:['x',2],ghost:[3,3]},'6':'bad'}});
 assert.deepEqual([...n.order].sort(),L.CARDS.map(c=>c.id).sort());assert.deepEqual(n.order.slice(0,1),['cpu']);
 assert.ok(n.order.indexOf('net')<n.order.indexOf('apps'),'saved order is kept');
 assert.deepEqual(n.sizes['12'],{apps:[12,3]});assert.equal(n.sizes['6'],undefined);
 for(const bad of [null,'x',{v:2,order:['apps']},{v:1,order:'apps'}])assert.deepEqual(L.normalize(bad),L.defaults());
});
test('hidden cards keep their place when others move',()=>{
 const d=L.defaults();const before=d.order.indexOf('battery');
 const moved=L.moveVisible(d,'cool',0,noBattery);assert.equal(moved.order.indexOf('battery'),before);
});
test('column count follows the page breakpoints',()=>{
 assert.equal(L.colsForWidth(1600),12);assert.equal(L.colsForWidth(1200),6);assert.equal(L.colsForWidth(760),2);
});
