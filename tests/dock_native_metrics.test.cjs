const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/index.html'),'utf8');
const css=html.slice(html.indexOf('<style>'),html.indexOf('</style>'));
const rule=sel=>{for(const part of css.split('}')){const [head,body]=part.split('{');if(body!==undefined&&head.split(/[\n}]/).pop().trim()===sel)return body;}return '';};

// Owner item 6: the waffle glyph must fill the same box as a native app icon.
test('the waffle glyph fills the icon box, like a native app icon',()=>{
  const svg=html.match(/const CHEV = '(<svg[^']*<\/svg>)'/)[1];
  const [x,y,w,h]=svg.match(/viewBox="([^"]+)"/)[1].split(/\s+/).map(Number);
  const rects=[...svg.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)].map(m=>m.slice(1).map(Number));
  const left=Math.min(...rects.map(r=>r[0])),top=Math.min(...rects.map(r=>r[1]));
  const right=Math.max(...rects.map(r=>r[0]+r[2])),bottom=Math.max(...rects.map(r=>r[1]+r[3]));
  assert.deepEqual([left,top,right,bottom],[x,y,x+w,y+h],'no padding inside the viewBox');
  assert.match(rule('.more svg'),/width:var\(--app-icon-size/);
});
// Owner item 6: a folder pill is the size of one native icon.
test('a folder tile is exactly one native icon in size',()=>{
  const folder=rule('.folder');
  assert.match(folder,/width:var\(--app-icon-size,24px\)/);assert.match(folder,/height:var\(--app-icon-size,24px\)/);
  assert.doesNotMatch(folder,/\*4\/3/);
});
// Owner item 7: Dock buttons use the measured native button pitch.
test('app, folder and waffle buttons all use the native button width',()=>{
  for(const sel of ['.win','.grp','.more']) assert.match(rule(sel),/width:var\(--app-button-width,44px\)/,sel);
});
test('render applies the measured icon size and button width',()=>{
  const body=html.slice(html.indexOf('function render(){'),html.indexOf('const activeElement',html.indexOf('function render(){')));
  assert.ok(body.includes('setProperty("--app-icon-size",`${state.layout?.icon_size || 24}px`)'));
  assert.ok(body.includes('setProperty("--app-button-width",`${state.layout?.button_width || 44}px`)'));
});
