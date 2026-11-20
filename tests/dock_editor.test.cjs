const {test}=require('node:test');
const assert=require('node:assert/strict');
const E=require('../ultra_dock/ui/dock_editor.js');
const fs=require('node:fs'),vm=require('node:vm');
const app=(n,extra={})=>({file:`${n}.exe`,exe:`C:\\Apps\\${n}.exe`,keep_entry:`\\${n}.exe`,name:n,...extra});
const cfg=()=>({categories:{Work:['apps']},order:['Work','Other'],overrides:{},keep:[]});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};};

test('real folder algorithm splits at six and renders singleton remainders as apps',()=>{
  for(let n=0;n<=40;n++){
    const apps=Array.from({length:n},(_,i)=>app(String(i)));
    const board=E.board(cfg(),apps);
    assert.equal(board.flatMap(x=>x.apps).length,n);
    assert.ok(board.every(x=>x.apps.length<=6));
    assert.ok(board.every(x=>x.folder?x.apps.length>=2:x.apps.length===1));
  }
  assert.deepEqual(E.board(cfg(),Array.from({length:7},(_,i)=>app(String(i)))).map(x=>[!!x.folder,x.apps.length]),[[true,4],[true,3]]);
});
test('group writes a named category, order and overrides without mutating input',()=>{
  const original=cfg(),apps=[app('a'),app('b')];
  const result=E.group(original,apps,['a.exe','b.exe'],' Friends ');
  assert.deepEqual(result.categories.Friends,[]);
  assert.deepEqual(result.order,['Work','Friends','Other']);
  assert.deepEqual(result.overrides,{'a.exe':'Friends','b.exe':'Friends'});
  assert.deepEqual(original,cfg());
  assert.equal(E.board(result,apps)[0].category,'Friends');
});
test('adding to an existing folder and moving out preserve the no singleton rule',()=>{
  const apps=[app('a'),app('b'),app('c')];
  let next=E.group(cfg(),apps,['a.exe','b.exe'],'Friends');
  next=E.group(next,apps,['a.exe','b.exe','c.exe'],'Friends',true);
  assert.equal(E.board(next,apps)[0].apps.length,3);
  next=E.moveOut(next,apps[0]);next=E.moveOut(next,apps[1]);
  assert.equal(E.board(next,apps).filter(x=>x.folder).length,0);
  assert.ok(next.keep.includes('\\a.exe'));
});
test('group validation rejects collisions, missing apps, reserved names and pinned apps',()=>{
  const apps=[app('a'),app('b')];
  for(const name of ['','__internal','Other','work','x'.repeat(121),'bad\nname']) assert.throws(()=>E.group(cfg(),apps,['a.exe','b.exe'],name));
  assert.throws(()=>E.group(cfg(),apps,['a.exe'],'New'));
  assert.throws(()=>E.group(cfg(),apps,['a.exe','missing.exe'],'New'));
  assert.throws(()=>E.group(cfg(),[app('a',{pinned:true}),app('b')],['a.exe','b.exe'],'New'));
});
test('keep rules and repeated windows cannot create phantom folders',()=>{
  const apps=[app('a'),app('a'),app('b')];
  const next=cfg();next.keep=['\\a.exe'];
  assert.equal(E.board(next,apps).length,2);
  assert.ok(E.board(next,apps).every(x=>!x.folder));
  assert.deepEqual(E.group(next,[app('a'),app('b')],['a.exe','b.exe'],'New').keep,[]);
  next.keep=['Apps'];
  assert.throws(()=>E.group(next,[app('a'),app('b'),app('c')],['a.exe','b.exe'],'New'),/shared Keep/);
});
test('dwell waits 450 ms, does not restart over the same target, and cancels on leave',()=>{
  let queued,delay,cancels=0;const ready=[];
  const d=E.dwell(x=>ready.push(x),()=>{},(fn,ms)=>{queued=fn;delay=ms;return 1;},()=>{cancels++});
  d.enter('a');assert.equal(delay,450);const first=queued;
  d.enter('a');assert.equal(queued,first);assert.deepEqual(ready,[]);
  queued();assert.deepEqual(ready,['a']);
  d.enter('b');assert.notEqual(queued,first);d.reset();assert.ok(cancels>=3);
});

function pointerPage(){
  const html=fs.readFileSync(require('node:path').join(__dirname,'../ultra_dock/ui/dock.html'),'utf8');
  const handlers={},groups=[],captured=new Set(),keys={};
  const board={addEventListener:(name,fn)=>handlers[name]=fn,contains:()=>true,
    setPointerCapture:id=>captured.add(id),hasPointerCapture:id=>captured.has(id),releasePointerCapture:id=>captured.delete(id)};
  const tile={dataset:{folder:'Other'},querySelector:()=>null,querySelectorAll:()=>[{dataset:{file:'b.exe'}}]};
  tile.classList={add:()=>{},remove:()=>{}};
  const button={dataset:{file:'a.exe'},disabled:false,focus:()=>{}};
  const c=vm.createContext({$:id=>id==='app-board'?board:{value:'New',textContent:''},CSS:{escape:x=>x},editing:true,dragging:null,preview:null,
    document:{elementFromPoint:()=>({closest:()=>tile})},window:{addEventListener:(name,fn)=>keys[name]=fn},
    traceEditor:()=>{},hover:{reset:()=>{c.preview=null;}},applyGroup:(...args)=>groups.push(args)});
  const start=html.indexOf('let dragGesture=');
  vm.runInContext(html.slice(start,html.indexOf('/* ---------- rendering')),c);
  const event=(x=0,y=0,id=1)=>({preventDefault:()=>{},button:0,pointerId:id,clientX:x,clientY:y,target:{closest:()=>button}});
  return {c,handlers,groups,captured,tile,button,keys,event};
}
test('captured pointer drag groups on release without a hover delay',()=>{
  const p=pointerPage();p.handlers.pointerdown(p.event());assert.ok(p.captured.has(1));
  p.handlers.pointermove(p.event(20,20));assert.equal(p.groups.length,0);
  p.handlers.pointerup(p.event(20,20));assert.equal(p.groups.length,1);assert.equal(p.captured.size,0);
  assert.deepEqual(Array.from(p.groups[0][0]),['a.exe','b.exe']);assert.equal(p.groups[0][1],'Other');assert.equal(p.groups[0][2],true);
  assert.equal(vm.runInContext('suppressAppClick',p.c),true);
  p.handlers.pointerdown({...p.event(),target:{closest:()=>null}});
  assert.equal(vm.runInContext('suppressAppClick',p.c),false,'the next deliberate click must work on its first attempt');
});
test('clicks, pinned handles, self drops and cancelled gestures do not group',()=>{
  const p=pointerPage();p.handlers.pointerdown(p.event());p.handlers.pointerup(p.event(2,2));assert.equal(p.groups.length,0);
  p.button.disabled=true;p.handlers.pointerdown(p.event());assert.equal(p.captured.size,0);p.button.disabled=false;
  p.handlers.pointerdown(p.event());p.tile.querySelector=()=>({});p.handlers.pointermove(p.event(20,20));p.handlers.pointerup(p.event(20,20));assert.equal(p.groups.length,0);
  p.tile.querySelector=()=>null;p.handlers.pointerdown(p.event());p.handlers.pointermove(p.event(20,20));p.handlers.pointercancel();p.handlers.pointerup(p.event(20,20));assert.equal(p.groups.length,0);
  p.handlers.pointerdown(p.event());p.handlers.pointermove(p.event(20,20));p.keys.keydown({key:'Escape'});p.handlers.pointerup(p.event(20,20));assert.equal(p.groups.length,0);
});
test('another pointer cannot move or finish a captured app gesture',()=>{
  const p=pointerPage();p.handlers.pointerdown(p.event());p.handlers.pointermove(p.event(20,20,2));p.handlers.pointerup(p.event(20,20,2));assert.equal(p.groups.length,0);assert.ok(p.captured.has(1));
  p.handlers.lostpointercapture();assert.equal(p.captured.size,0);
});

test('editor uses platform evidence for unknown titles while manual folders win',()=>{
  const next={categories:{Games:[],Chat:['discord']},overrides:{},keep:[],order:['Games','Chat','Other']};
  const game=app('discordquest',{game_install:true});
  assert.equal(E.category(next,game),'Games');
  next.overrides['discordquest.exe']='Custom';assert.equal(E.category(next,game),'Custom');
  delete next.overrides['discordquest.exe'];delete next.categories.Games;
  assert.equal(E.category(next,game),'Chat');
});

test('settings board follows the taskbar app count and excludes pins from that count',()=>{
  const next={categories:{AI:['chatgpt'],Create:['obs'],Other:[]},order:['AI','Create','Other'],keep:[],overrides:{}};
  const apps=[app('chatgpt'),app('obs'),app('photos'),app('ulanzideck'),app('opera',{pinned:true})];
  const five=E.board(next,apps,5);assert.equal(five.length,5);assert.ok(five.every(item=>!item.folder));
  const auto=E.board(next,apps,null);assert.equal(auto.length,4);assert.deepEqual(auto.find(item=>item.folder).apps.map(a=>a.file),['photos.exe','ulanzideck.exe']);
});

test('creating a folder saves its membership before switching to Automatic',async()=>{
  const html=fs.readFileSync(require('node:path').join(__dirname,'../ultra_dock/ui/dock.html'),'utf8');
  const calls=[],message={textContent:''},apps=[app('a'),app('b')];
  const c=vm.createContext({DockEditor:E,S:{config:cfg(),apps,cap:5},dirty:false,selectedApps:new Set(),
    change:config=>{c.S.config=config;calls.push('change');},save:async()=>calls.push('save'),
    queueCap:async action=>{calls.push(action);c.S.cap=null;},renderApps:()=>{},renderCats:()=>{},renderKeep:()=>{},
    $:()=>message,status:()=>{}});
  vm.runInContext(html.slice(html.indexOf('async function applyGroup'),html.indexOf('$("edit-mode").onclick')),c);
  await c.applyGroup(['a.exe','b.exe'],'Friends',false);
  assert.deepEqual(calls,['change','save','auto']);assert.equal(c.S.cap,null);assert.match(message.textContent,/Grouped in Friends/);
  calls.length=0;c.S.cap=5;c.dirty=true;await c.applyGroup(['a.exe','b.exe'],'Friends2',false);
  assert.deepEqual(calls,['change','save']);assert.equal(c.S.cap,5,'failed or pending membership saves must not change the app count');
});
test('delayed save acknowledgements cannot overwrite edits and writes are serialized',async()=>{
  let current={value:1};const requests=[],writes=[],states=[];
  const s=E.saver({read:()=>current,write:x=>{writes.push(x);current=x;},send:x=>{const d=deferred();requests.push({x,...d});return d.promise;},state:(...x)=>states.push(x),report:()=>{}});
  s.changed();const running=s.flush();
  current={value:2};s.changed();assert.equal(s.flush(),running);
  assert.equal(requests.length,1,'later request cannot complete before the first write');
  requests[0].resolve({value:1,cleaned:true});for(let i=0;i<5 && requests.length<2;i++)await Promise.resolve();
  assert.deepEqual(current,{value:2});assert.equal(writes.length,0);
  assert.equal(requests.length,2);assert.deepEqual(requests[1].x,{value:2});
  requests[1].resolve({value:2,cleaned:true});await running;
  assert.deepEqual(current,{value:2,cleaned:true});assert.deepEqual(states.at(-1),[false,false]);
});
test('save failure retains dirty edits, surfaces error and retries latest snapshot',async()=>{
  let current={value:1},fails=true;const reports=[],states=[];
  const s=E.saver({read:()=>current,write:x=>current=x,send:async x=>{if(fails)throw Error('disk full');return x;},state:(...x)=>states.push(x),report:(...x)=>reports.push(x)});
  s.changed();await s.flush();assert.deepEqual(states.at(-1),[true,false]);assert.match(reports[0][0],/disk full/);assert.equal(reports[0][1],true);
  current={value:3};s.changed();fails=false;await s.flush();assert.deepEqual(current,{value:3});assert.deepEqual(states.at(-1),[false,false]);
});
