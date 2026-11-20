const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const ui=path.join(__dirname,'../ultra_dock/ui');
const M=require('../ultra_dock/ui/no_context_menu.js');

const el=(tag,attrs={})=>({tagName:tag,type:attrs.type,disabled:!!attrs.disabled,getAttribute:k=>attrs[k]??null,closest(){return this;}});
const inside=target=>({closest:()=>target});

test('the browser menu is blocked everywhere except editable text',()=>{
  for(const target of [inside(null),inside(el('INPUT',{type:'checkbox'})),inside(el('INPUT',{type:'range'})),
    inside(el('TEXTAREA',{disabled:true})),inside(el('DIV',{contenteditable:'false'})),{}]){
    let prevented=false;M.onContextMenu({target,preventDefault:()=>prevented=true});
    assert.equal(prevented,true);
  }
  for(const target of [inside(el('INPUT',{type:'text'})),inside(el('INPUT',{type:'search'})),inside(el('INPUT',{})),
    inside(el('TEXTAREA')),inside(el('DIV',{contenteditable:''}))]){
    let prevented=false;M.onContextMenu({target,preventDefault:()=>prevented=true});
    assert.equal(prevented,false,'text fields keep Cut, Copy and Paste');
  }
});

test('every Dock page loads the menu blocker before its own scripts',()=>{
  for(const page of ['index.html','dock.html','dock_menu.html','hub.html','starting.html']){
    const html=fs.readFileSync(path.join(ui,page),'utf8');
    const blocker=html.indexOf('<script src="no_context_menu.js"></script>');
    assert.ok(blocker>0,`${page} loads no_context_menu.js`);
    assert.ok(blocker<html.indexOf('<script>'),`${page} blocks the menu before its inline script runs`);
  }
  const listened=[];
  vm.runInNewContext(fs.readFileSync(path.join(ui,'no_context_menu.js'),'utf8'),
    {window:{document:{addEventListener:(name,fn,capture)=>listened.push([name,capture])}}});
  assert.deepEqual(listened,[['contextmenu',true]]);
});

function strip(){
  const html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
  const source=html.slice(html.indexOf('let contextMenuOpening'),html.indexOf('/* Close any open folder'));
  const calls=[],listeners={};
  const c=vm.createContext({calls,Number,view:'strip',closed:0,
    state:{windows:[{hwnd:11,exe:'C:\\A\\a.exe'},{hwnd:12,exe:'C:\\A\\a.exe'},{hwnd:21,exe:'C:\\B\\b.exe'}]},
    layoutItems:()=>[{key:'Work',exes:['C:\\A\\a.exe','C:\\B\\b.exe']}],closeAll:()=>c.closed++,
    document:{addEventListener:(name,fn)=>listeners[name]=fn},
    invoke:async(name,args)=>{calls.push([name,JSON.parse(JSON.stringify(args??null))]);}});
  vm.runInContext(source,c);
  const node=(cls,data,attrs=[])=>({cls,dataset:data,hasAttribute:a=>attrs.includes(a)});
  const click=async(match)=>{let prevented=false;await listeners.contextmenu({preventDefault:()=>prevented=true,
    target:{closest:sel=>match[sel]||null}});return prevented;};
  return {c,calls,listeners,node,click};
}

test('right-click on an app opens its app menu for that window',async()=>{
  const s=strip();
  assert.equal(await s.click({'.win':s.node('win',{exe:'C:\\B\\b.exe',h:'21'})}),true);
  assert.deepEqual(s.calls[0],['show_app_menu',{exe:'C:\\B\\b.exe',hwnd:21,folder:null,folderExes:null}]);
});
test('a button for several windows names none, a window-list row names its window',async()=>{
  const s=strip();
  await s.click({'.win':s.node('win',{exe:'C:\\A\\a.exe',h:'11'},['aria-expanded'])});
  assert.equal(s.calls[0][1].hwnd,null);assert.equal(s.calls[0][1].exe,'C:\\A\\a.exe');
  await s.click({'.wt':s.node('wt',{h:'12'}),'.win':null});
  assert.deepEqual(s.calls[1][1],{exe:'C:\\A\\a.exe',hwnd:12,folder:null,folderExes:null});
});
test('right-click on a folder sends its members, empty space opens the Dock menu',async()=>{
  const s=strip();
  await s.click({'.grp':s.node('grp',{cat:'Work'})});
  assert.deepEqual(s.calls[0],['show_app_menu',{exe:null,hwnd:null,folder:'Work',folderExes:['C:\\A\\a.exe','C:\\B\\b.exe']}]);
  assert.equal(await s.click({}),true);
  assert.deepEqual(s.calls[1],["show_dock_menu",null]);assert.equal(s.c.closed,1);
});
test('a second right-click while a menu is opening is ignored but still blocked',async()=>{
  const s=strip();let release;
  s.c.invoke=(name,args)=>{s.calls.push([name,args]);return new Promise(r=>release=r);};
  const first=s.click({});
  assert.equal(await s.click({}),true);
  assert.equal(s.calls.length,1);release();await first;
  s.c.invoke=async(name,args)=>{s.calls.push([name,args]);};
  await s.click({});assert.equal(s.calls.length,2);
});
test('only the strip view replaces the menu; other index views keep the blocker only',()=>{
  const html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
  assert.match(html,/if \(view === "strip"\) document\.addEventListener\("contextmenu", handleDockContextMenu\);/);
});
