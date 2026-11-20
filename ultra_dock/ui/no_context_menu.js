/* Ultra Dock pages are app surfaces, not web pages: the WebView's browser menu
   (Back, Refresh, Save as, Print) never appears on them. Text fields keep their
   Cut, Copy and Paste menu. Pages that offer their own menu (the strip) add a
   handler of their own; this one only blocks the browser default. */
(function (root) {
  'use strict';
  const TEXT_INPUTS = new Set(['', 'text', 'search', 'url', 'email', 'tel', 'number', 'password']);
  function editable(target) {
    const el = target && typeof target.closest === 'function'
      ? target.closest('input,textarea,[contenteditable]') : null;
    if (!el) return false;
    const tag = String(el.tagName || '').toUpperCase();
    if (tag === 'TEXTAREA') return !el.disabled;
    if (tag === 'INPUT') return !el.disabled && TEXT_INPUTS.has(String(el.type || '').toLowerCase());
    return String(el.getAttribute ? el.getAttribute('contenteditable') : '').toLowerCase() !== 'false';
  }
  function onContextMenu(event) {
    if (!editable(event.target)) event.preventDefault();
  }
  if (root.document && typeof root.document.addEventListener === 'function') {
    root.document.addEventListener('contextmenu', onContextMenu, true);
  }
  if (typeof module === 'object' && module.exports) module.exports = { editable, onContextMenu };
})(typeof window !== 'undefined' ? window : globalThis);
