const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ui=name=>fs.readFileSync(path.join(__dirname,'../ultra_dock/ui',name),'utf8');
const rust=fs.readFileSync(path.join(__dirname,'../ultra_dock/src-tauri/src/dock_layout.rs'),'utf8');

test('every Dock window loads the shared design tokens, and the windows with controls load the shared controls',()=>{
  for(const page of ['hub.html','dock.html','dock_menu.html','starting.html','index.html'])
    assert.match(ui(page),/<link rel="stylesheet" href="tokens\.css" ?\/?>/,`${page} loads tokens.css`);
  for(const page of ['hub.html','dock.html','dock_menu.html','starting.html'])
    assert.match(ui(page),/<link rel="stylesheet" href="suite\.css" ?\/?>/,`${page} loads suite.css`);
  assert.doesNotMatch(ui('index.html'),/suite\.css/,'the strip keeps its own Windows taskbar controls');
});

test('the token file defines each scale and a light theme, and zeroes motion for reduced motion',()=>{
  const t=ui('tokens.css');
  for(const name of ['--s-1','--s-8','--t-1','--t-6','--r-1','--r-5','--bg','--ink-3','--accent','--sh-1','--sh-3','--d-1','--d-3','--ease'])
    assert.match(t,new RegExp(`${name}:`),name);
  assert.match(t,/@media \(prefers-color-scheme: light\)\{:root\{/);
  assert.match(t,/@media \(prefers-reduced-motion: reduce\)\{\s*:root\{--d-1:0ms;--d-2:0ms;--d-3:0ms\}/);
  assert.match(t,/transition-duration:\.01ms!important/);
  // A color-scheme on the strip's root could give the transparent taskbar window an opaque canvas
  assert.doesNotMatch(t.replace(/\/\*[\s\S]*?\*\//g,''),/(^|[;{\s])color-scheme\s*:/);
});

test('page styles use the token values instead of new fixed radii and font sizes',()=>{
  for(const page of ['hub.html','dock_menu.html','starting.html']){
    const css=ui(page).match(/<style>([\s\S]*?)<\/style>/)[1].replace(/\/\*[\s\S]*?\*\//g,'');
    assert.doesNotMatch(css,/border-radius:\s*\d+px/,`${page} radius comes from --r-*`);
    assert.doesNotMatch(css,/font-size:\s*\d+(\.\d+)?px/,`${page} sizes come from --t-*`);
  }
});

test('strip button widths still match the widths the Rust layout plans with',()=>{
  const html=ui('index.html');
  const num=name=>Number(rust.match(new RegExp(`pub const ${name}: f64 = ([\\d.]+);`))[1]);
  // Every strip button is drawn at the measured native width, falling back to the nominal folder width.
  const fallback=`var\\(--app-button-width,${num('FOLDER_W')}px\\)`;
  assert.match(html,new RegExp(`\\.grp\\{position:relative;height:40px;width:${fallback};`),'folder button width');
  assert.match(html,new RegExp(`\\.win\\{position:relative;width:${fallback};`),'app button width');
  assert.match(html,new RegExp(`\\.more\\{width:${fallback};`),'menu button width');
  // The planner works in nominal widths over a room converted by the narrowest nominal button.
  assert.ok(num('APP_W')<=num('FOLDER_W')&&num('MENU_W')<=num('FOLDER_W'));
  assert.ok(rust.includes('pub fn nominal_room(avail: f64, button_width_css: Option<f64>) -> f64'));
});

test('opaque windows paint their first frame in the system theme, and the strip does not',()=>{
  for(const page of ['hub.html','dock.html','dock_menu.html','starting.html'])
    assert.match(ui(page),/<meta name="color-scheme" content="dark light" ?\/?>/,page);
  assert.doesNotMatch(ui('index.html'),/<meta name="color-scheme"/);
});

test('Windows contrast themes keep switch, pill and running bar state visible',()=>{
  assert.match(ui('suite.css'),/@media \(forced-colors: active\)\{[\s\S]*\.sw\[aria-checked=true\]\{background:Highlight/);
  assert.match(ui('suite.css'),/\.sw,\.sw::after,\.pill::before\{forced-color-adjust:none\}/);
  assert.match(ui('dock_menu.html'),/@media \(forced-colors: active\)\{[\s\S]*\[aria-checked=true\] \.switch\{background:Highlight/);
  assert.match(ui('index.html'),/@media \(forced-colors: active\)\{[^\n]*\.win\.active \.runbar\{background:Highlight/);
  assert.match(ui('tokens.css'),/@media \(prefers-contrast: more\)\{/);
});

test('hub cards are named regions and their buttons say which app they act on',()=>{
  const hub=ui('hub.html');
  for(const app of ['sentinel','alarm','dock','insights']){
    assert.match(hub,new RegExp(`id="c-${app}" aria-labelledby="h-${app}"`),app);
    assert.match(hub,new RegExp(`<h2 id="h-${app}">`),app);
  }
  assert.equal((hub.match(/aria-describedby="h-(sentinel|alarm|dock|insights)"/g)||[]).length,5);
});

test('the strip contrast-theme block comes after the badge and bar rules it overrides',()=>{
  const html=ui('index.html');
  const forced=html.indexOf('@media (forced-colors: active)');
  assert.ok(forced>html.indexOf('.win .wn{'),'after the window count badge');
  assert.ok(forced>html.indexOf('.grp .runbar{'),'after the running bar');
  assert.match(ui('hub.html'),/@media \(forced-colors: active\)\{\.navitem \.dot\{forced-color-adjust:none/);
});
