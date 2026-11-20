const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = path.join(__dirname, '../ultra_dock/ui/dock_layout_preview.js');
function plan(groups, cap = null) {
  assert.ok(fs.existsSync(source), 'the shared Rust-policy preview module must exist');
  return require(source).plan(groups, cap);
}
function group(category, count) {
  return {category, apps: Array.from({length:count}, (_, i) => ({file:`${category}-${i}.exe`}))};
}
const shape = items => items.map(item => [item.category, item.folder, item.apps.length]);
const singles = items => items.filter(item => !item.folder).length;

test('cap five with four eligible apps leaves all four separate, including two Other apps', () => {
  const groups = [group('Chat', 1), group('Work', 1), group('Other', 2)];
  assert.deepEqual(shape(plan(groups, 5)), [
    ['Chat', false, 1], ['Work', false, 1], ['Other', false, 1], ['Other', false, 1],
  ]);
});

test('Automatic keeps category order, skips empties and balances eight and thirteen apps', () => {
  const groups = [group('Empty', 0), group('Solo', 1), group('Eight', 8), group('Thirteen', 13)];
  assert.deepEqual(shape(plan(groups)), [
    ['Solo', false, 1], ['Eight', true, 4], ['Eight', true, 4],
    ['Thirteen', true, 5], ['Thirteen', true, 4], ['Thirteen', true, 4],
  ]);
  assert.deepEqual(plan(groups), plan(groups, null));
});

test('caps zero, one, N-1, N and N+1 follow Rust boundary behavior', () => {
  const groups = [group('A', 4), group('B', 2)];
  const fixtures = [
    [0, [['A', true, 4], ['B', true, 2]]],
    [1, [['A', false, 1], ['A', true, 3], ['B', true, 2]]],
    [5, [...Array.from({length:4}, () => ['A', false, 1]), ['B', true, 2]]],
    [6, [...Array.from({length:4}, () => ['A', false, 1]), ['B', false, 1], ['B', false, 1]]],
    [7, [...Array.from({length:4}, () => ['A', false, 1]), ['B', false, 1], ['B', false, 1]]],
  ];
  for (const [cap, expected] of fixtures) assert.deepEqual(shape(plan(groups, cap)), expected, `cap ${cap}`);
});

test('a cap cutting a category with one remainder pulls its last separate app back', () => {
  const groups = ['Browsers', 'Chat', 'Games', 'Music'].map(name => group(name, 3));
  assert.deepEqual(shape(plan(groups, 5)), [
    ...Array.from({length:3}, () => ['Browsers', false, 1]), ['Chat', false, 1],
    ['Chat', true, 2], ['Games', true, 3], ['Music', true, 3],
  ]);
  assert.equal(singles(plan(groups, 4)), 4);
  assert.equal(singles(plan(groups, 10)), 10);
});

test('numeric caps pool cross-category singletons under More apps, Automatic does not', () => {
  const groups = [group('A', 2), group('B', 1), group('C', 1), group('D', 1)];
  const items = plan(groups, 2);
  assert.deepEqual(shape(items), [['A', false, 1], ['A', false, 1], ['__more', true, 3]]);
  assert.deepEqual(items[2].apps, groups.slice(1).flatMap(g => g.apps));
  assert.deepEqual(shape(plan(groups)), [['A', true, 2], ['B', false, 1], ['C', false, 1], ['D', false, 1]]);
});

test('a lone singleton joins the last category folder before balanced chunking', () => {
  const groups = [group('First', 2), group('Last', 6), group('Unknown', 1)];
  const items = plan(groups, 0);
  assert.deepEqual(shape(items), [['First', true, 2], ['Last', true, 4], ['Last', true, 3]]);
  assert.strictEqual(items[2].apps.at(-1), groups[2].apps[0]);
});

test('pool order follows first category occurrence, including singletons before folders', () => {
  const groups = [group('SoloFirst', 1), group('Folder', 2), group('SoloLast', 1)];
  const items = plan(groups, 0);
  assert.deepEqual(shape(items), [['Folder', true, 2], ['__more', true, 2]]);
  assert.deepEqual(items[1].apps, [groups[0].apps[0], groups[2].apps[0]]);
});

test('More apps pools also split eight and thirteen apps into balanced folders', () => {
  for (const [count, sizes] of [[8, [4, 4]], [13, [5, 4, 4]]]) {
    const groups = Array.from({length:count}, (_, i) => group(`Unknown${i}`, 1));
    const items = plan(groups, 0);
    assert.deepEqual(shape(items), sizes.map(size => ['__more', true, size]));
    assert.deepEqual(items.flatMap(item => item.apps), groups.flatMap(g => g.apps));
  }
});

test('empty inputs and a single app never create empty or one-app folders', () => {
  for (const cap of [null, 0, 1, 30]) {
    assert.deepEqual(plan([], cap), []);
    assert.deepEqual(plan([group('Empty', 0)], cap), []);
    assert.deepEqual(shape(plan([group('Only', 1)], cap)), [['Only', false, 1]]);
  }
});

test('unknown and prototype-like category names remain exact, case-sensitive identities', () => {
  const groups = ['__proto__', 'constructor', 'Other', 'other', '未知'].map(name => group(name, 2));
  for (const cap of [null, 0]) {
    assert.deepEqual(shape(plan(groups, cap)), groups.map(g => [g.category, true, 2]));
  }
});

test('numeric caps merge repeated category entries; Automatic retains input grouping', () => {
  const groups = [group('Repeat', 1), group('Middle', 2), group('Repeat', 1)];
  assert.deepEqual(shape(plan(groups)), [['Repeat', false, 1], ['Middle', true, 2], ['Repeat', false, 1]]);
  assert.deepEqual(shape(plan(groups, 0)), [['Repeat', true, 2], ['Middle', true, 2]]);
  assert.deepEqual(plan(groups, 0)[0].apps, [groups[0].apps[0], groups[2].apps[0]]);
});

test('category-cut singleton counting spans all remaining entries of that category', () => {
  const groups = [group('Repeat', 2), group('Middle', 2), group('Repeat', 1)];
  assert.deepEqual(shape(plan(groups, 1)), [['Repeat', false, 1], ['Repeat', true, 2], ['Middle', true, 2]]);
});

test('manual group assignments and app identities are passed through without reclassification or mutation', () => {
  const apps = [Object.freeze({file:'browser.exe', category:'Other'}), Object.freeze({file:'game.exe', pinned:true})];
  const groups = Object.freeze([Object.freeze({category:'My manual folder', apps:Object.freeze(apps)})]);
  const automatic = plan(groups);
  assert.equal(automatic[0].category, 'My manual folder');
  assert.equal(automatic[0].folder, true);
  assert.notStrictEqual(automatic[0].apps, groups[0].apps);
  automatic[0].apps.forEach((app, i) => assert.strictEqual(app, apps[i]));
  assert.deepEqual(shape(plan(groups, 2)), [['My manual folder', false, 1], ['My manual folder', false, 1]]);
  assert.deepEqual(groups[0].apps, apps);
});

test('all caps zero through thirty preserve every supplied app and folder bounds', () => {
  const groups = [group('A', 13), group('B', 8), group('C', 1), group('D', 1), group('E', 3)];
  const original = groups.flatMap(g => g.apps);
  for (const cap of [null, ...Array.from({length:31}, (_, i) => i)]) {
    const items = plan(groups, cap), output = items.flatMap(item => item.apps);
    assert.equal(output.length, original.length);
    for (const app of original) assert.equal(output.filter(item => item === app).length, 1);
    assert.ok(items.every(item => item.folder ? item.apps.length >= 2 && item.apps.length <= 6 : item.apps.length === 1));
    if (cap !== null) assert.ok(singles(items) <= cap);
  }
});

test('browser global and CommonJS expose the same pure plan API without DOM dependencies', () => {
  plan([]);
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(source, 'utf8'), context);
  assert.equal(typeof context.DockLayoutPreview.plan, 'function');
  const groups = [group('A', 3), group('Other', 2)];
  assert.equal(JSON.stringify(context.DockLayoutPreview.plan(groups, 5)), JSON.stringify(plan(groups, 5)));
});
