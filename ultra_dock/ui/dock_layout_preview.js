/* Pure port of dock_layout.rs base_items, before space-dependent compaction.
 * Input categories and app order are already resolved by the caller. Cap is
 * null (Automatic) or a nonnegative integer. App objects retain their identity.
 */
(function(root) {
'use strict';
const MAX_PER_FOLDER = 6;

function itemsFor(category, apps) {
  if (!apps.length) return [];
  if (apps.length === 1) return [{category, folder:false, apps:apps.slice()}];
  const count = Math.ceil(apps.length / MAX_PER_FOLDER);
  const size = Math.floor(apps.length / count), extra = apps.length % count;
  let offset = 0;
  return Array.from({length:count}, (_, i) => {
    const length = size + Number(i < extra);
    const item = {category, folder:true, apps:apps.slice(offset, offset + length)};
    offset += length;
    if (category === '__more') item.label = i === 0 ? 'More apps' : `More apps ${i + 1}`;
    return item;
  });
}

function plan(groups, cap = null) {
  if (cap === null) return groups.flatMap(group => itemsFor(group.category, group.apps));
  const flat = groups.flatMap(group => group.apps.map(app => ({category:group.category, app})));
  let loose = Math.min(cap, flat.length);
  // Do not leave a lone app behind the cap in a one-app folder.
  if (flat.length - loose === 1 && loose > 0) loose--;
  // A singleton at a category cut pulls its last loose sibling into its folder.
  if (loose > 0 && loose < flat.length && flat[loose - 1].category === flat[loose].category
      && flat.slice(loose).filter(item => item.category === flat[loose].category).length === 1) loose--;

  const result = flat.slice(0, loose).map(({category, app}) => ({category, folder:false, apps:[app]}));
  const rest = flat.slice(loose);
  if (rest.length === 1) {
    result.push({category:rest[0].category, folder:false, apps:[rest[0].app]});
    return result;
  }
  // Rust coalesces remainder categories in first-occurrence order. Map also
  // handles unknown names such as "__proto__" without treating them as keys.
  const categories = new Map();
  for (const {category, app} of rest) {
    if (!categories.has(category)) categories.set(category, []);
    categories.get(category).push(app);
  }
  const pool = [], folders = [];
  for (const [category, apps] of categories) {
    if (apps.length === 1) pool.push(apps[0]);
    else folders.push({category, apps});
  }
  // Append before chunking so the last category remains balanced and <= 6.
  if (pool.length === 1 && folders.length) folders[folders.length - 1].apps.push(pool.pop());
  for (const {category, apps} of folders) result.push(...itemsFor(category, apps));
  if (pool.length) result.push(...itemsFor('__more', pool));
  return result;
}

root.DockLayoutPreview = {plan};
if (typeof module !== 'undefined' && module.exports) module.exports = root.DockLayoutPreview;
})(typeof globalThis !== 'undefined' ? globalThis : this);
