/* Shared by the settings window and Node tests. No native calls here. */
(function(root){
'use strict';
const clone = value => JSON.parse(JSON.stringify(value));
const key = app => app.file.toLowerCase();
function kept(cfg, app){ return app.pinned || (cfg.keep || []).some(k => app.exe.toLowerCase().includes(k.toLowerCase())); }
function category(cfg, app){
  const override = cfg.overrides?.[key(app)];
  if (override) return override;
  let best = 'Other', score = 0;
  for (const [name, words] of Object.entries(cfg.categories || {})) for (const word of words) {
    if (word.length > score && app.exe.toLowerCase().includes(word.toLowerCase())) { best = name; score = word.length; }
  }
  return best;
}
function board(cfg, apps){
  const unique = [...new Map(apps.map(a => [key(a), a])).values()];
  const singles = [], groups = new Map();
  for (const app of unique) {
    if (kept(cfg, app)) { singles.push({apps:[app]}); continue; }
    const cat = category(cfg, app);
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat).push(app);
  }
  const order = [...new Set([...(cfg.order || []), ...groups.keys()])];
  for (const cat of order) {
    const members = groups.get(cat) || [];
    const count=Math.ceil(members.length/6), size=count ? Math.floor(members.length/count) : 0;
    let offset=0;
    for (let i=0; i<count; i++) {
      const n=size+Number(i<members.length%count), chunk=members.slice(offset,offset+n); offset+=n;
      singles.push({category:cat, folder:chunk.length>1, apps:chunk});
    }
  }
  return singles;
}
function group(cfg, apps, files, name, existing=false){
  const selected = [...new Set(files.map(f => f.toLowerCase()))];
  const members = apps.filter(a => selected.includes(key(a)));
  if (selected.length < 2 || members.length !== selected.length) throw Error('Choose at least two open apps.');
  if (members.some(a => a.pinned)) throw Error('Windows-pinned apps cannot be moved.');
  name = String(name || '').trim();
  if (!name || name.length>120 || /[\x00-\x1f]/.test(name) || name.startsWith('__')) throw Error('Enter a folder name (up to 120 characters).');
  const names = [...Object.keys(cfg.categories || {}), 'Other'];
  const match = names.find(n => n.toLowerCase() === name.toLowerCase());
  if (match && !existing) throw Error('That category already exists. Drop onto its folder to add apps.');
  if (existing && !match) throw Error('That folder no longer exists.');
  const next = clone(cfg);
  if (!match) {
    if (Object.keys(next.categories || {}).length >= 40) throw Error('At most 40 categories.');
    next.categories = {...next.categories, [name]:[]};
    next.order = [...new Set([...(next.order || []).filter(n=>n!=='Other'), name, 'Other'])];
  } else name = match;
  next.overrides = {...next.overrides};
  for (const app of members) next.overrides[key(app)] = name;
  // Explicit grouping also removes keep rules for these apps. Warn by refusing broad rules.
  const rules = (next.keep || []).filter(rule => members.some(a=>a.exe.toLowerCase().includes(rule.toLowerCase())));
  if (apps.some(a => !selected.includes(key(a)) && rules.some(rule=>a.exe.toLowerCase().includes(rule.toLowerCase())))) throw Error('Remove the shared Keep rule before grouping these apps.');
  next.keep = (next.keep || []).filter(rule=>!rules.includes(rule));
  return next;
}
function moveOut(cfg, app){
  if (app.pinned) return cfg;
  const next = clone(cfg), entry = app.keep_entry || '\\'+app.file;
  next.keep = [...new Set([...(next.keep || []), entry])];
  return next;
}
function dwell(ready, clear, schedule=setTimeout, cancel=clearTimeout){
  let timer, target;
  return { enter(value){ if (value===target) return; this.reset(); target=value; timer=schedule(()=>ready(value),450); },
    reset(){ cancel(timer); target=null; clear(); } };
}
// Only one request may write at a time. Old acknowledgements never replace newer edits.
function saver({read, write, send, state, report}){
  let revision=0, pending=false, running=null;
  async function drain(){
    state(true, true);
    try {
      while (pending) {
        pending=false;
        const at=revision, snapshot=clone(read());
        try {
          const saved=await (async()=>send(snapshot))();
          if (at===revision) { revision++; write(saved); report('Saved',false); }
        } catch(error) { pending=true; report(String(error || 'Could not save'),true); break; }
      }
    } finally { running=null; state(pending,false); }
  }
  return {
    changed(){ revision++; pending=true; state(true,!!running); },
    flush(){ if (running) return running; if (!pending) return Promise.resolve(); running=drain(); return running; },
    get revision(){return revision;}
  };
}
root.DockEditor={board,group,moveOut,dwell,saver,category};
if (typeof module!=='undefined') module.exports=root.DockEditor;
})(typeof globalThis!=='undefined'?globalThis:this);
