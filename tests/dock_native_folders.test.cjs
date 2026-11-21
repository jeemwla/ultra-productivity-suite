// Native folder buttons (owner items 4, 9 and 11): an optional setting, off by default.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../ultra_dock/ui/dock.html'),'utf8');

test('the settings page offers native folder buttons as a switch that starts off',()=>{
  assert.match(html,/<button class="sw" role="switch" id="native-folders" aria-checked="false" aria-label="Native folder buttons"><\/button>/);
  assert.ok(html.includes('String(S.config.native_folder_buttons === true)'),'only an explicit true turns the switch on');
});

test('the switch toggles only native_folder_buttons and keeps the rest of the config',()=>{
  const handler=html.match(/\$\("native-folders"\)\.onclick = \(\) => \{([^\n]*)\};/);
  assert.ok(handler,'the switch has a click handler');
  const calls=[];let renders=0;
  const S={config:{keep:['\\a.exe'],inline_window_list:true}};
  new Function('S','change','renderTop',handler[1])(S,c=>calls.push(c),()=>renders++);
  assert.deepEqual(calls,[{keep:['\\a.exe'],inline_window_list:true,native_folder_buttons:true}]);
  S.config=calls[0];
  new Function('S','change','renderTop',handler[1])(S,c=>calls.push(c),()=>renders++);
  assert.equal(calls[1].native_folder_buttons,false);
  assert.equal(renders,2);
});

test('the help text says what changes: one taskbar button per folder and Alt+Tab keeps every window',()=>{
  const row=html.slice(html.indexOf('for="native-folders"'),html.indexOf('id="native-folders"'));
  assert.match(row,/one taskbar button/);
  assert.match(row,/Alt\+Tab keeps every window/);
});
