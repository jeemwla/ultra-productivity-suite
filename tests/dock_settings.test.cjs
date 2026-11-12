const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/dock.html'),'utf8');
const strip=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');

function helpers(){
  const source=html.slice(html.indexOf('/* ---------- pure helpers'),html.indexOf('/* ---------- saving'));
  const context=vm.createContext({OTHER:'Other'});
  vm.runInContext(source,context);
  return context;
}
const base=()=>({keep:['\\explorer.exe'],order:['Games','Chat'],categories:{Games:['steam'],Chat:['discord']},overrides:{'beeper.exe':'Chat'}});

test('category order always lists every category once with Other present',()=>{
  const h=helpers();
  assert.deepEqual([...h.categoryNames({categories:{A:[],B:[]},order:['B','Ghost']})],['B','A','Other']);
  assert.deepEqual([...h.categoryNames(base())],['Games','Chat','Other']);
});

test('moving, renaming, adding and removing categories keep overrides consistent',()=>{
  const h=helpers();
  assert.deepEqual([...h.moveCategory(base(),'Chat',-1).order],['Chat','Games','Other']);
  assert.deepEqual([...h.moveCategory(base(),'Games',-1).order],['Games','Chat','Other'],'top stays top');
  const r=h.renameCategory(base(),'Chat','Messages');
  assert.equal(r.error,null);assert.deepEqual(r.cfg.categories.Messages,['discord']);assert.equal(r.cfg.overrides['beeper.exe'],'Messages');
  assert.deepEqual([...r.cfg.order],['Games','Messages','Other']);
  assert.match(h.renameCategory(base(),'Chat','games').error,/already/);
  const o=[...h.categoryNames(h.renameCategory(base(),'Other','X').cfg)];
  assert.equal(o.includes('Other')&&!o.includes('X'),true,'Other cannot be renamed');
  const a=h.addCategory(base(),'Work');assert.deepEqual([...a.cfg.order],['Games','Chat','Work','Other'],'new categories go before Other');
  assert.match(h.addCategory(base(),'__more').error,/cannot/);
  const d=h.removeCategory(base(),'Chat');assert.equal('Chat' in d.categories,false);assert.equal('beeper.exe' in d.overrides,false);
  assert.equal(h.categoryNames(h.removeCategory(base(),'Other')).includes('Other'),true,'Other cannot be removed');
});

test('keywords, app folders and the keep list',()=>{
  const h=helpers();
  assert.deepEqual([...h.setKeywords(base(),'Games',['Steam','  riot ','steam','']).categories.Games],['steam','riot']);
  assert.equal(h.setAppCategory(base(),'zoom.exe','Chat').overrides['zoom.exe'],'Chat');
  assert.equal('beeper.exe' in h.setAppCategory(base(),'beeper.exe','').overrides,false,'Automatic removes the override');
  assert.deepEqual([...h.setKeep(base(),'\\Zoom.exe',true).keep],['\\explorer.exe','\\Zoom.exe']);
  assert.deepEqual([...h.setKeep(base(),'\\EXPLORER.EXE',false).keep],[],'matching ignores case');
});

test('app count options run from Automatic through 0 to 30',()=>{
  const o=helpers().capOptions(30);
  assert.equal(o.length,32);assert.deepEqual([...o[0]],['auto','Automatic']);assert.deepEqual([...o[1]],['0','None (all in folders)']);assert.deepEqual([...o[31]],['30','30 apps']);
});

test('the Dock menu opens the settings window instead of Notepad, and the file stays reachable',()=>{
  assert.match(strip,/data-act="edit"[^>]*>Settings</);
  assert.match(strip,/invoke\("open_dock_settings"\)/);
  assert.doesNotMatch(strip,/invoke\("open_config"\)/);
  assert.match(html,/invoke\("open_config"\)/,'Advanced still opens the file');
});
