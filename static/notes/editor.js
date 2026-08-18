const root = document.getElementById('noteEditor');
const notebookId = root.dataset.notebookId;
const isPublicView = root.dataset.readOnly === 'true';
const apiBase = root.dataset.apiBase || '/api/v01/notebooks';
const canvas = new fabric.Canvas('notesCanvas', { selection: true, preserveObjectStacking: true });
window.__notesCanvas = canvas;
canvas.wrapperEl.style.setProperty('position', 'relative', 'important'); canvas.wrapperEl.style.setProperty('inset', 'auto', 'important');
/* ROOT CAUSE of "can't resize a tall text box's width — the handle is off the visible page":
   Fabric's stock ml/mr (width-only) resize handles sit at the object's true vertical center,
   which for a Textbox that's grown very tall from wrapped content can be far below where the
   user is actually looking/typing — and once past PAGE_HEIGHT, off the fixed-size canvas
   entirely, where nothing can ever be clicked. Clamping how far ml/mr may sit from the object's
   TOP edge keeps width-resizing reachable near the top of any text box no matter how tall it
   grows, while every short/normal box (the vast majority) renders at the exact same position as
   stock Fabric, since the clamp only engages once height exceeds twice the clamp distance.
   Corner/height handles (tl/tr/bl/br/mt/mb) and every other object type are untouched — Textbox
   and IText each own a private controls object (not shared with fabric.Object.prototype), so
   this can't leak into shapes/images/paths. */
const WIDTH_HANDLE_MAX_DIST_FROM_TOP = 140;
function clampedWidthHandlePosition(dim, finalMatrix, fabricObject, currentControl) {
  const control = currentControl || this;
  const height = Math.max(dim.y, 1);
  const yFraction = height > WIDTH_HANDLE_MAX_DIST_FROM_TOP * 2 ? (-0.5 + WIDTH_HANDLE_MAX_DIST_FROM_TOP / height) : control.y;
  const point = new fabric.Point(control.x * dim.x + control.offsetX, yFraction * dim.y + control.offsetY);
  return fabric.util.transformPoint(point, finalMatrix);
}
[fabric.Textbox, fabric.IText].forEach(TextClass => {
  ['ml', 'mr'].forEach(key => { const control = TextClass.prototype.controls[key]; if (control) control.positionHandler = clampedWidthHandlePosition; });
});
let activePageId = document.querySelector('.page-item.active')?.dataset.pageId;
let pageCache = [];
let dirty = false, loading = false, saving = false, saveTimer, pan = false, lastPan, deletedIds = [], history = [], historyIndex = -1, activeTool = 'select', autoSaveEnabled = true;
// Must mirror MAX_OBJECTS_PER_PAGE in app/utils/notes_validation.py. NOT a page content limit —
// a page can hold any number of objects; this only bounds a single save request's payload, so
// save() below splits a big page into several sequential chunked PUTs to the SAME page. Never
// creates or switches pages — page creation stays 100% manual (the "+" button only).
const SAVE_CHUNK_SIZE = 500;
/* Text/Sticky Note editing uses a real contenteditable DOM overlay (beginNativeTextEdit below),
   not Fabric's own IText/Textbox editing — see beginNativeTextEdit's comment for why. This is the
   one piece of state tracking whichever overlay is currently open (at most one at a time). */
let nativeTextEditor = null;
/* ── Read Mode / Full Screen / Toolbar visibility state (independent of each other) ── */
let currentMode = 'edit';          // 'edit' | 'read'
let isFullscreen = false;          // browser fullscreen (or fallback) active?
let toolbarVisible = true;         // object-toolbar visibility preference
let previousActiveTool = 'select'; // remembered tool when entering Read Mode
window.__notesReadOnly = false;    // read by drawing-tools.js to suppress pen/eraser

/* ── Image / page caching ──────────────────────────────────────────────────
   Root cause of the "images reload on every page switch" issue: loadPage()
   used to always hit the network AND fully re-enliven every object (incl.
   images) from scratch, and the server always hands back a freshly-signed
   URL for every image, which changes the URL string every time — so the
   browser's own HTTP cache could never kick in either.

   Two caches fix this without touching the save/undo/redo/export paths:

   1. pageObjectsCache: pageId -> already-enlivened live fabric objects.
      Revisiting a page already seen this session just re-adds the SAME
      object instances to the canvas — no network call, no re-enlivening,
      no new image element creation at all.

   2. assetUrlCache: asset_id -> {url, expiresAt}. When a page DOES need to
      be fetched (first visit, or the page cache entry aged out), any image
      whose asset we've already resolved this session reuses that same URL
      string instead of the server's freshly re-signed one, so the browser
      can still serve it from its own image cache. TTL is kept safely under
      the server's 1-hour signed-URL lifetime (see notes_storage_service.py)
      so we never hand out a URL we can't be reasonably sure is still valid.
*/
// Custom fabric properties this app relies on — shared by save (objectRecord), undo/redo
// (snapshot), and copy/paste/export so every serialize/enliven round-trip stays identical.
const NOTES_PROPS = ['objectId', 'objectType', 'themeText', 'themeSticky', 'assetId', 'shapeTextId', 'shapeTextFor', 'minHeight', 'customColor', 'customBg', 'strokeOnly', 'globalCompositeOperation'];
const pageObjectsCache = new Map();   // pageId -> { objects: fabric.Object[], cachedAt: number }
const assetUrlCache = new Map();      // assetId -> { url: string, expiresAt: number }
const PAGE_CACHE_TTL_MS = 50 * 60 * 1000;  // must stay comfortably under the 1h signed-URL TTL
const ASSET_URL_TTL_MS = 50 * 60 * 1000;
const preloadingPageIds = new Set();

/** Build one fabric-ready object from a saved row, substituting a still-valid
 *  cached URL for its image (if any) instead of the server's freshly-signed
 *  one, and remembering whatever URL we do end up using. Shared by loadPage()
 *  and the background preloader so the two can never drift apart. */
function buildRawFabricEntry(row) {
  const entry = { ...(row.payload?.fabric || {}), objectId: row.id, objectType: row.object_type };
  if (row.object_type === 'image' && row.asset_id && entry.src) {
    const cached = assetUrlCache.get(row.asset_id);
    if (cached && cached.expiresAt > Date.now()) {
      entry.src = cached.url;
    } else {
      assetUrlCache.set(row.asset_id, { url: entry.src, expiresAt: Date.now() + ASSET_URL_TTL_MS });
    }
  }
  return entry;
}

/** Best-effort, non-blocking warm-up of one page's objects so a later
 *  switch to it is an instant cache hit. Never touches the visible canvas —
 *  enlivened objects aren't added to anything until loadPage() actually
 *  needs them. Silently gives up on any failure; a real visit will just
 *  fetch normally. */
async function preloadPage(id) {
  if (!id || pageObjectsCache.has(id) || preloadingPageIds.has(id)) return;
  preloadingPageIds.add(id);
  try {
    const data = await api(`${apiBase}/${notebookId}/pages/${id}/objects`);
    const raw = data.objects.map(buildRawFabricEntry);
    const objects = await new Promise((resolve, reject) => {
      const result = fabric.util.enlivenObjects(raw, resolve);
      if (result && typeof result.then === 'function') result.then(resolve).catch(reject);
    });
    objects.forEach(finalizeLoadedObject);
    if (!pageObjectsCache.has(id)) pageObjectsCache.set(id, { objects, cachedAt: Date.now() });
  } catch (error) {
    /* best-effort only */
  } finally {
    preloadingPageIds.delete(id);
  }
}

/** After a page becomes interactive, warm its immediate neighbors in the
 *  background — at most one previous + one next, never the whole notebook,
 *  and skipped entirely if they're already cached. */
function scheduleAdjacentPreload(id) {
  const index = pageCache.findIndex(page => page.id === id);
  if (index === -1) return;
  const neighborIds = [pageCache[index + 1]?.id, pageCache[index - 1]?.id].filter(Boolean);
  if (!neighborIds.length) return;
  const run = () => neighborIds.forEach(preloadPage);
  if (window.requestIdleCallback) window.requestIdleCallback(run, { timeout: 1500 });
  else setTimeout(run, 300);
}

const fontMetricsPromise = (async () => {
  if (document.fonts) {
    await document.fonts.load('400 22px "DM Sans"');
    await document.fonts.load('600 22px "DM Sans"');
    await document.fonts.ready;
  }
  fabric.util.clearFabricFontCache('DM Sans');
})();

function api(path, options = {}) { return fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options }).then(async r => { const d = await r.json().catch(() => ({})); if (!r.ok || !d.success) throw new Error(d.message || 'Request failed.'); return d; }); }
function status(text, kind = '') { const e = document.getElementById('saveState'); e.className = `save-state ${kind}`; e.innerHTML = `<i class="fas fa-${kind === 'saving' ? 'spinner fa-spin' : kind === 'failed' ? 'exclamation-circle' : 'check-circle'}"></i> ${text}`; }
function toast(text, type = 'success') { const r = document.getElementById('notesToastRegion'); const e = document.createElement('div'); e.className = `notes-toast ${type}`; e.innerHTML = `<i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'}"></i><span></span>`; e.querySelector('span').textContent = text; r.appendChild(e); setTimeout(() => e.remove(), 4500); }
function uid() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`; }
function token(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
async function ensureTextMetrics() { await fontMetricsPromise; canvas.getObjects().forEach(object => { if (object.type === 'i-text' || object.type === 'textbox') object.initDimensions(); }); canvas.requestRenderAll(); }
const BULLET_RE = /^([•◦▪–✔]\s|\d+\.\s)/;
/* ══════════════════════════════════════════════════════════════════════════════════
   TEXT / STICKY NOTE EDITING — real contenteditable DOM overlay, not Fabric's own
   IText/Textbox editing.

   ROOT CAUSE this works around: Fabric's native `enterEditing()` draws its own caret by
   hand on the canvas — it measures every glyph itself (fabric.charWidthsCache), lays out
   __charBounds/__lineWidths from those measurements, and fillRect()s a line at the
   computed offset on every blink. That measurement/layout bookkeeping is what
   occasionally drifts out of sync with the text actually on screen (the caret rendering
   mid-character, or a keystroke landing at a different index than where the caret was
   drawn) — a real Fabric internal bug, not a zoom/DPI/rendering-scale issue, and
   sensitive enough to exact browser/font-rasterizer behavior that it can reproduce
   reliably in one browser/machine and not another.

   The fix is to never let Fabric draw the caret at all: every text object is created
   with `editable: false` (see configureNormalText/configureStickyNote/createEditableText
   below — enterEditing() is never called anywhere in this app), and while a Text Box or
   Sticky Note is being edited, the Fabric-rendered text is hidden (object.opacity = 0)
   behind a real `<div contenteditable="true">` (`.native-text-editor` in editor.css)
   positioned exactly over it. The user types into that div — the BROWSER's own text
   engine owns caret placement and measurement, which is correct by construction, not
   something this app has to compute. On blur/finish, the div's plain text is copied back
   into object.text, the div is removed, and the Fabric-rendered text (opacity: 1) takes
   over again for display. Formatting (Bold/Italic/color/highlight/lists) still goes
   through Fabric's own per-character `styles` map via setSelectionStyles — only the live
   caret/typing surface changed. */
function textSelectionOffsets(element) { const selection = window.getSelection(); if (!selection?.rangeCount || !element.contains(selection.anchorNode)) return { start: 0, end: 0 }; const offsetAt = (node, offset) => { const range = document.createRange(); range.selectNodeContents(element); range.setEnd(node, offset); return range.toString().length; }; const start = offsetAt(selection.anchorNode, selection.anchorOffset); const end = offsetAt(selection.focusNode, selection.focusOffset); return { start: Math.min(start, end), end: Math.max(start, end) }; }
function placeCaretAtPoint(element, clientX, clientY) { let range = null; if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(clientX, clientY); else if (document.caretPositionFromPoint) { const pos = document.caretPositionFromPoint(clientX, clientY); if (pos && pos.offsetNode) { range = document.createRange(); range.setStart(pos.offsetNode, pos.offset); range.collapse(true); } } if (!range || !element.contains(range.startContainer)) return false; const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); return true; }
function setCaretOffset(element, offset) { const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT); let count = 0, node = walker.nextNode(); const range = document.createRange(); while (node) { if (count + node.length >= offset) { range.setStart(node, offset - count); range.collapse(true); break; } count += node.length; node = walker.nextNode(); } if (!node) { range.selectNodeContents(element); range.collapse(false); } const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }
function selectOffsetRange(element, start, end) { const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT); let count = 0, node = walker.nextNode(), started = false; const range = document.createRange(); while (node) { const len = node.length; if (!started && count + len >= start) { range.setStart(node, start - count); started = true; } if (count + len >= end) { range.setEnd(node, end - count); break; } count += len; node = walker.nextNode(); } if (!started) range.setStart(element, 0); if (!node) range.setEnd(element, element.childNodes.length); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }
/* Bullet/numbered-list continuation: Enter on a bulleted line repeats the bullet (auto-
   incrementing "N. " numbers); Backspace right after a bullet on an otherwise-empty line
   removes just the bullet instead of merging into the previous line. Plain-text architecture
   (see toggleLinePrefix in drawing-tools.js), so this operates on line text, not a list model.
   Operates on the live contenteditable overlay via document.execCommand, so undo/redo inside
   the overlay (native browser Ctrl+Z while editing) stays consistent with the edit. */
function handleListContinuation(event, element, sync) {
  if (event.key !== 'Enter' && event.key !== 'Backspace') return false;
  const offsets = textSelectionOffsets(element);
  if (offsets.start !== offsets.end) return false;
  const text = element.innerText.replace(/\n$/, '');
  const lineStart = text.lastIndexOf('\n', Math.max(0, offsets.start - 1)) + 1;
  const lineEndIdx = text.indexOf('\n', offsets.start);
  const lineEnd = lineEndIdx === -1 ? text.length : lineEndIdx;
  const line = text.slice(lineStart, lineEnd);
  const m = line.match(BULLET_RE);
  if (!m || offsets.start < lineStart + m[0].length) return false;
  const prefix = m[0];
  if (event.key === 'Enter') {
    event.preventDefault();
    if (line === prefix) { selectOffsetRange(element, lineStart, lineStart + prefix.length); document.execCommand('delete'); }
    else { const next = /^\d+\./.test(prefix) ? `${parseInt(prefix, 10) + 1}. ` : prefix; document.execCommand('insertText', false, '\n' + next); }
    sync(); return true;
  }
  if (event.key === 'Backspace' && offsets.start === lineStart + prefix.length) {
    event.preventDefault();
    selectOffsetRange(element, lineStart, lineStart + prefix.length);
    document.execCommand('delete');
    sync(); return true;
  }
  return false;
}
function finishNativeTextEdit() {
  const edit = nativeTextEditor;
  if (!edit) return;
  const { object, element } = edit;
  const offsets = textSelectionOffsets(element);
  object.selectionStart = offsets.start; object.selectionEnd = offsets.end;
  object.set('text', element.innerText.replace(/\n$/, ''));
  object.opacity = 1;
  object.dirty = true; object._clearCache && object._clearCache();
  object.initDimensions();
  clampTextHeight(object); constrainObjectToPage(object); object.setCoords();
  element.remove();
  nativeTextEditor = null;
  canvas.requestRenderAll();
  markDirty();
}
function repositionNativeTextEditor() {
  if (!nativeTextEditor) return;
  const { object, element } = nativeTextEditor;
  const bounds = object.getBoundingRect(false, true);
  const zoom = canvas.getZoom();
  element.style.left = `${bounds.left}px`; element.style.top = `${bounds.top}px`;
  element.style.fontSize = `${(object.fontSize || 18) * zoom}px`;
}
/* ROOT CAUSE of "formatting reverts to default while editing, correct again after clicking
   away": the overlay's initial content was always built with `element.textContent = object.text`
   — plain text only. Normal-mode rendering (opacity:1, Fabric's own _renderText/_renderTextLine)
   reads every character's effective style from Fabric's per-character `styles` map
   (setSelectionStyles' storage, read back via getSelectionStyles), but the DOM overlay has no
   notion of that map at all — textContent carries only the plain string, so on open the overlay
   always showed the object's base font/color/weight for every character, never any per-character
   overrides. (The live-preview code in drawing-tools.js's previewOverlayFormat only patches the
   DOM *forward* from a formatting button click during the SAME session — it never reconstructs
   the overlay's own starting markup, so a fresh open/re-open was never covered by it.)
   Fix: mirror Fabric's own effective-style-per-character resolution (getSelectionStyles(0, len,
   true), which already merges base + per-char overrides exactly like _renderTextLine does) into
   equivalent inline-styled <span> runs when the overlay is first populated, so its initial visual
   state matches normal-mode rendering exactly. Purely additive to how the DOM is *built*; every
   other piece already reads that DOM by walking its text nodes (TreeWalker/Range), which works
   identically regardless of how many inline <span> wrappers surround the same characters — caret
   placement, selection-offset math, and sync() are untouched. */
function buildEditableOverlayMarkup(object) {
  const text = object.text || '';
  if (!text) return '';
  const zoom = canvas.getZoom();
  const baseWeight = object.fontWeight || 'normal';
  const baseFontStyle = object.fontStyle || 'normal';
  const baseUnderline = !!object.underline;
  const baseLinethrough = !!object.linethrough;
  const baseFill = object.fill;
  const baseFontSize = object.fontSize;
  const baseFontFamily = object.fontFamily;
  let perChar = [];
  try { perChar = object.getSelectionStyles(0, text.length, true) || []; } catch (e) { perChar = []; }
  const escapeHtml = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const sameRun = (a, b) => a.fontWeight === b.fontWeight && a.fontStyle === b.fontStyle && !!a.underline === !!b.underline && !!a.linethrough === !!b.linethrough && a.fill === b.fill && a.fontSize === b.fontSize && a.fontFamily === b.fontFamily && a.textBackgroundColor === b.textBackgroundColor;
  let html = '', i = 0;
  while (i < text.length) {
    if (text[i] === '\n') { html += '\n'; i++; continue; }
    const style = perChar[i] || {};
    let j = i + 1;
    while (j < text.length && text[j] !== '\n' && sameRun(perChar[j] || {}, style)) j++;
    const runText = escapeHtml(text.slice(i, j));
    const css = [];
    if (typeof style.fontWeight !== 'undefined' && style.fontWeight !== baseWeight) css.push(`font-weight:${style.fontWeight}`);
    if (typeof style.fontStyle !== 'undefined' && style.fontStyle !== baseFontStyle) css.push(`font-style:${style.fontStyle}`);
    if (!!style.underline !== baseUnderline || !!style.linethrough !== baseLinethrough) {
      const decos = [style.underline && 'underline', style.linethrough && 'line-through'].filter(Boolean);
      css.push(`text-decoration:${decos.length ? decos.join(' ') : 'none'}`);
    }
    if (typeof style.fill !== 'undefined' && style.fill !== baseFill) css.push(`color:${style.fill}`);
    if (typeof style.fontSize !== 'undefined' && style.fontSize !== baseFontSize) css.push(`font-size:${style.fontSize * zoom}px`);
    if (typeof style.fontFamily !== 'undefined' && style.fontFamily !== baseFontFamily) css.push(`font-family:${style.fontFamily}`);
    if (style.textBackgroundColor) css.push(`background-color:${style.textBackgroundColor}`);
    html += css.length ? `<span style="${css.join(';')}">${runText}</span>` : runText;
    i = j;
  }
  return html;
}
function beginNativeTextEdit(object, point) {
  if (currentMode === 'read' || !object || !['i-text', 'textbox'].includes(object.type)) return;
  if (nativeTextEditor?.object === object) { nativeTextEditor.element.focus(); if (point) placeCaretAtPoint(nativeTextEditor.element, point.x, point.y); return; }
  finishNativeTextEdit();
  const bounds = object.getBoundingRect(false, true);
  const wrapper = canvas.wrapperEl;
  const zoom = canvas.getZoom();
  const element = document.createElement('div');
  element.className = 'native-text-editor'; element.contentEditable = 'true'; element.spellcheck = true; element.innerHTML = buildEditableOverlayMarkup(object);
  /* ROOT CAUSE of Ctrl+A / Shift+Arrow / drag-select "selecting the whole canvas" bug:
     Fabric.js sets canvas.wrapperEl.onselectstart = falseFunction to stop native drag-select
     while manipulating canvas objects. selectstart bubbles, so it silently killed selection
     in this overlay too (independent of any CSS user-select value). Stop it right here. */
  element.addEventListener('selectstart', e => e.stopPropagation());
  element.style.left = `${bounds.left}px`; element.style.top = `${bounds.top}px`; element.style.width = `${Math.max(36, bounds.width - 4)}px`; element.style.minHeight = `${Math.max(28, bounds.height)}px`;
  element.style.fontFamily = object.fontFamily || 'DM Sans'; element.style.fontSize = `${(object.fontSize || 18) * zoom}px`; element.style.fontWeight = object.fontWeight || 'normal'; element.style.fontStyle = object.fontStyle || 'normal'; element.style.textDecoration = object.underline ? 'underline' : 'none'; element.style.lineHeight = object.lineHeight || 1.2; element.style.textAlign = object.textAlign || 'left'; element.style.color = object.fill || token('--text-1'); element.style.background = object.backgroundColor || (object.themeSticky ? token('--warning-bg') : 'transparent'); element.style.padding = `${object.padding || 0}px`;
  object.opacity = 0;
  canvas.setActiveObject(object);
  canvas.requestRenderAll();
  wrapper.append(element);
  const sync = () => {
    object.set('text', element.innerText.replace(/\n$/, ''));
    const offsets = textSelectionOffsets(element);
    object.selectionStart = offsets.start; object.selectionEnd = offsets.end;
    element.style.height = 'auto';
    const contentHeight = Math.max(28, element.scrollHeight);
    element.style.height = `${contentHeight}px`;
    const growZoom = canvas.getZoom() || 1;
    const nextHeight = Math.max(object.minHeight || 0, contentHeight / growZoom);
    if (Math.abs((object.height || 0) - nextHeight) > 0.5) { object.set('height', nextHeight); object.dirty = true; object.setCoords(); }
    // A Textbox/Sticky Note growing taller mid-edit could otherwise push past PAGE_HEIGHT with
    // nothing repositioning it until finishNativeTextEdit — same fixed-page clamp trusted for
    // drag/resize/paste, applied live so the box's own handles stay reachable while still typing.
    constrainObjectToPage(object);
    canvas.requestRenderAll();
  };
  nativeTextEditor = { object, element, sync };
  element.addEventListener('keydown', event => handleListContinuation(event, element, sync));
  element.addEventListener('input', sync);
  element.addEventListener('keyup', sync);
  element.addEventListener('mouseup', sync);
  element.addEventListener('blur', () => setTimeout(() => {
    if (nativeTextEditor?.element !== element || element.contains(document.activeElement)) return;
    // A <select>/<input type=color> formatting control (font size, font family, heading,
    // bullet style, ink/highlight color) can't be made focus-preserving the way toolbar
    // buttons are (mousedown.preventDefault doesn't stop a native picker/dropdown from taking
    // focus) — without this check, opening one would silently end the edit session (and with
    // it the live selection range) right before the format change handler runs, so Bold/
    // Underline/Font size/Color/Highlight would land on the WHOLE box instead of the
    // selection. Losing focus to a toolbar control just keeps editing (and the selection)
    // alive instead; only losing focus to something outside both actually finishes it.
    if (document.activeElement?.closest('.object-toolbar')) return;
    finishNativeTextEdit();
  }, 0));
  element.focus();
  if (!point || !placeCaretAtPoint(element, point.x, point.y)) { const range = document.createRange(); range.selectNodeContents(element); range.collapse(false); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }
  sync();
  requestAnimationFrame(() => {
    if (nativeTextEditor?.element !== element) return;
    if (document.activeElement !== element) element.focus();
    if (point) placeCaretAtPoint(element, point.x, point.y);
  });
}
// ROOT CAUSE of "huge textboxes turn blurry": Fabric caches every object as an offscreen bitmap
// by default and redraws that bitmap (not the live text) on every frame. When an object's cached
// bitmap would need more than fabric.maxCacheSideLimit (4096px) on a side, or more than
// fabric.perfLimitSizeTotal (~2.1M px²) total — both easily reached by a tall wrapped textbox,
// especially multiplied by retina devicePixelRatio — Fabric silently renders that cache at a
// LOWER resolution and stretches it back up to the object's real size, which is exactly what
// blur is. Text doesn't benefit from caching the way shapes/paths do (redrawing text directly
// each frame is already cheap), so disabling it here removes the blur at its source instead of
// working around the symptom, and is scoped to text objects only — shapes/images/drawings keep
// their normal caching, so this doesn't touch general canvas performance.
// editable:false on every text object — see the big comment above this section for why Fabric's
// own IText/Textbox editing must never engage.
// ROOT CAUSE of formatted text (bold/italic/color/highlight/font on part of the text) looking
// right in the edit overlay but rendering with dropped letters/spaces or overlapping words once
// back in normal view: Fabric batches consecutive same-style characters into a single
// ctx.fillText(...) call per run for performance, but it lays runs out using WIDTHS it measured
// per individual character, while the browser's own text shaper can render that multi-character
// string slightly narrower/wider than the sum of its single-char measurements (kerning) — so the
// next run starts exactly where Fabric expects but the previous run's glyphs don't actually end
// there, and whichever run paints second overwrites the mismatch. Fabric only measures/renders
// one character at a time (sidestepping the mismatch entirely) when `charSpacing` is non-zero —
// 1 is a thousandth of an em (~0.02px at normal sizes), imperceptible, but enough to take that
// code path on every text object, not just the ones a user happens to give mixed formatting.
function configureNormalText(text) { text.set({ width: Math.max(220, text.width || 0), editable: false, objectCaching: false, charSpacing: text.charSpacing || 1, lockUniScaling: false, lockScalingFlip: true, borderColor: token('--accent'), cornerColor: token('--accent'), transparentCorners: false }); text.setControlsVisibility({ tl: true, tr: true, bl: true, br: true, mt: true, mb: true, ml: true, mr: true }); }
function configureStickyNote(text) { text.set({ width: Math.max(220, text.width || 0), editable: false, objectCaching: false, charSpacing: text.charSpacing || 1, lockUniScaling: false, lockScalingFlip: true, borderColor: token('--accent'), cornerColor: token('--accent'), transparentCorners: false }); text.setControlsVisibility({ tl: true, tr: true, bl: true, br: true, mt: true, mb: true, ml: true, mr: true }); }
async function createEditableText(options, type) { if (currentMode === 'read') return null; await fontMetricsPromise; fabric.util.clearFabricFontCache('DM Sans'); const TextModel = type === 'rich_text' || type === 'sticky_note' ? fabric.Textbox : fabric.IText; const text = new TextModel('', { fontFamily: window.__notesDefaultFont || 'DM Sans', fontWeight: 400, fontStyle: 'normal', lineHeight: 1.2, editable: false, ...options, fill: window.__notesTextColor || options.fill }); if (type === 'rich_text') configureNormalText(text); if (type === 'sticky_note') configureStickyNote(text); addObject(text, type); text.initDimensions(); canvas.calcOffset(); beginNativeTextEdit(text); return text; }
function syncNativeTextOverlay() { if (!nativeTextEditor) return; const { object, element } = nativeTextEditor; element.style.fontSize = `${(object.fontSize || 18) * canvas.getZoom()}px`; element.style.fontFamily = object.fontFamily || 'DM Sans'; element.style.fontWeight = object.fontWeight || 'normal'; element.style.fontStyle = object.fontStyle || 'normal'; element.style.textDecoration = object.underline ? 'underline' : 'none'; element.style.textAlign = object.textAlign || 'left'; element.style.color = object.fill || token('--text-1'); }
function applyTextColor(color) { if (currentMode === 'read') return; window.__notesTextColor = color; const object = nativeTextEditor?.object || canvas.getActiveObject(); if (!object || !['i-text', 'textbox'].includes(object.type)) return; const { start, end } = nativeTextEditor ? textSelectionOffsets(nativeTextEditor.element) : { start: object.selectionStart || 0, end: object.selectionEnd || 0 }; if (start !== end) { object.setSelectionStyles({ fill: color }, start, end); if (nativeTextEditor) document.execCommand('foreColor', false, color); } else object.set({ fill: color }); object.customColor = true; syncNativeTextOverlay(); object.initDimensions(); canvas.fire('object:modified', { target: object }); canvas.requestRenderAll(); markDirty(); }
function applyStickyBackground(color) { if (currentMode === 'read') return; window.__notesStickyBgColor = color; const object = canvas.getActiveObject(); if (!object || object.objectType !== 'sticky_note') return; object.set({ backgroundColor: color }); object.customBg = true; object.initDimensions(); if (nativeTextEditor?.object === object) nativeTextEditor.element.style.background = color; canvas.fire('object:modified', { target: object }); canvas.requestRenderAll(); markDirty(); }
window.__notesBeginNativeTextEdit = beginNativeTextEdit; window.__notesApplyTextColor = applyTextColor; window.__notesSyncActiveTextOverlay = syncNativeTextOverlay; window.__notesApplyStickyBackground = applyStickyBackground;
window.__notesGetNativeEditor = () => nativeTextEditor; window.__notesTextSelectionOffsets = textSelectionOffsets; window.__notesSetCaretOffset = setCaretOffset;
function applyCanvasTheme() { canvas.getObjects().forEach(o => { if (o.themeText && !o.customColor) o.set('fill', token('--text-1')); if (o.themeSticky) { if (!o.customColor) o.set('fill', token('--warning-text')); if (!o.customBg) o.set('backgroundColor', token('--warning-bg')); } }); canvas.requestRenderAll(); }

/* ════════════════════════════════════════════════════════════════
   READ MODE / FULL SCREEN / TOOLBAR TOGGLE — controller
   Kept as a self-contained block: does not touch save/load/page
   logic, only gates interaction and toggles UI/layout state.
   ════════════════════════════════════════════════════════════════ */

function applyToolbarVisibility() {
  const toolbar = document.querySelector('.object-toolbar');
  if (!toolbar) return;
  toolbar.hidden = !(currentMode === 'edit' && toolbarVisible);
}

function refreshLayout() {
  requestAnimationFrame(() => {
    resize();
    repositionNativeTextEditor();
  });
}

function updateUndoRedoUI() {
  const canUndo = currentMode === 'edit' && historyIndex > 0;
  const canRedo = currentMode === 'edit' && historyIndex < history.length - 1;
  const undoBtn = document.getElementById('undoBtn');
  const redoBtn = document.getElementById('redoBtn');
  if (undoBtn) undoBtn.disabled = !canUndo;
  if (redoBtn) redoBtn.disabled = !canRedo;
}

function flashHistoryButton(button) {
  if (!button) return;
  button.classList.add('active');
  setTimeout(() => button.classList.remove('active'), 180);
}

function updateReadModeUI() {
  const btn = document.getElementById('readModeBtn');
  const badge = document.getElementById('readOnlyBadge');
  const toolbarBtn = document.getElementById('toolbarToggleBtn');
  const title = document.getElementById('notebookTitle');
  const reading = currentMode === 'read';
  if (btn) {
    btn.innerHTML = reading ? '<i class="fas fa-pen"></i>' : '<i class="fas fa-book-reader"></i>';
    btn.title = reading ? 'Exit Read Mode' : 'Read Mode';
    btn.classList.toggle('active', reading);
  }
  if (badge) badge.hidden = !reading;
  if (toolbarBtn) toolbarBtn.disabled = reading;
  if (title) { if (reading) title.setAttribute('readonly', 'readonly'); else title.removeAttribute('readonly'); }
  root.classList.toggle('read-mode', reading);
  updateUndoRedoUI();
}

function updateToolbarToggleUI() {
  const btn = document.getElementById('toolbarToggleBtn');
  if (!btn) return;
  btn.innerHTML = toolbarVisible ? '<i class="fas fa-eye-slash"></i>' : '<i class="fas fa-eye"></i>';
  btn.title = toolbarVisible ? 'Hide Toolbar' : 'Show Toolbar';
  btn.classList.toggle('active', !toolbarVisible);
}

function updateFullscreenUI() {
  const btn = document.getElementById('fullscreenBtn');
  if (!btn) return;
  btn.innerHTML = isFullscreen ? '<i class="fas fa-compress"></i>' : '<i class="fas fa-expand"></i>';
  btn.title = isFullscreen ? 'Exit Full Screen' : 'Full Screen';
  btn.classList.toggle('active', isFullscreen);
}

async function setMode(next) {
  if (isPublicView) return; // Public Notebooks are permanently in Read Mode — never toggled here.
  if (next !== 'edit' && next !== 'read') return;
  if (next === currentMode) return;

  finishNativeTextEdit();
  if (next === 'read') {
    canvas.discardActiveObject();
    if (dirty) { clearTimeout(saveTimer); await save(); }
    previousActiveTool = activeTool;
    window.__notesReadOnly = true;
    canvas.isDrawingMode = false;
    canvas.selection = false;
    canvas.skipTargetFind = true;
    canvas.defaultCursor = 'default';
    canvas.hoverCursor = 'default';
    canvas.freeDrawingCursor = 'default';
    document.querySelectorAll('[data-tool]').forEach(b => b.classList.remove('active'));
    window.__notesDeactivateInkTools?.();
  } else {
    window.__notesReadOnly = false;
    canvas.skipTargetFind = false;
    setTool(previousActiveTool || 'select');
  }

  currentMode = next;
  applyToolbarVisibility();
  updateReadModeUI();
  canvas.requestRenderAll();
  refreshLayout();
}

/* Public Notebook viewer entry point. Reuses the exact same Read Mode state
   machine as the owner-editor's manual toggle (currentMode/window.__notesReadOnly/
   canvas flags) instead of a second read-only implementation — the difference
   is only that this runs once at load and the toggle button is then disabled,
   so a public viewer can never flip back into Edit Mode. */
function initPublicReadOnlyView() {
  currentMode = 'read';
  window.__notesReadOnly = true;
  canvas.isDrawingMode = false;
  canvas.selection = false;
  canvas.skipTargetFind = true;
  canvas.defaultCursor = 'default';
  canvas.hoverCursor = 'default';
  canvas.freeDrawingCursor = 'default';
  const readModeBtn = document.getElementById('readModeBtn');
  if (readModeBtn) {
    readModeBtn.disabled = true;
    readModeBtn.title = 'This Public Notebook is read-only';
  }
}

function setToolbarVisible(visible) {
  if (isPublicView) return;
  toolbarVisible = !!visible;
  applyToolbarVisibility();
  updateToolbarToggleUI();
  refreshLayout();
}

function applyFallbackFullscreen(active) {
  root.classList.toggle('fs-fallback', active);
  root.classList.toggle('is-fullscreen', active);
  isFullscreen = active;
  updateFullscreenUI();
  refreshLayout();
}

async function enterFullscreen() {
  const requester = root.requestFullscreen || root.webkitRequestFullscreen;
  if (requester) {
    try { await requester.call(root); }
    catch (error) { toast('Full screen was blocked by the browser. Showing a windowed full screen view instead.', 'error'); applyFallbackFullscreen(true); }
  } else {
    applyFallbackFullscreen(true);
  }
}

function exitFullscreen() {
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    (document.exitFullscreen || document.webkitExitFullscreen)?.call(document);
  } else if (root.classList.contains('fs-fallback')) {
    applyFallbackFullscreen(false);
  }
}

function toggleFullscreen() { if (isFullscreen) exitFullscreen(); else enterFullscreen(); }

function handleFullscreenChange() {
  if (root.classList.contains('fs-fallback')) return; // fallback mode manages its own state
  const nativeActive = !!(document.fullscreenElement || document.webkitFullscreenElement);
  isFullscreen = nativeActive;
  root.classList.toggle('is-fullscreen', nativeActive);
  updateFullscreenUI();
  refreshLayout();
}

document.addEventListener('fullscreenchange', handleFullscreenChange);
document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
document.addEventListener('keydown', event => { if (event.key === 'Escape' && root.classList.contains('fs-fallback')) applyFallbackFullscreen(false); });

/* On-button export progress — shared by the PDF and JSON export buttons below.
   A tick loop that only ever moves the shown percentage up by exactly 1 point at a time, and
   never past `target`. `advance()` raises `target` as real work actually completes (bytes
   received, pages rendered, server round-trip settled), so the bar is always chasing genuine
   progress instead of running to 100% on an independent clock: if real progress stalls, the
   shown percentage stalls with it instead of faking movement. `complete()` raises the target to
   100 and resolves once the tick loop has visibly caught up to it, so callers can await the
   on-screen "100%" before triggering the download/toast. */
function createExportProgress(btn, label) {
  let pct = 0, target = 0, timer = null, doneResolvers = [];
  const render = () => { btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${label} ${pct}%`; };
  function tick() {
    if (pct >= target) return;
    pct += 1; render();
    if (pct >= 100) { doneResolvers.forEach(resolve => resolve()); doneResolvers = []; }
  }
  return {
    start() { pct = 0; target = 0; render(); timer = setInterval(tick, 15); },
    advance(value) { target = Math.max(target, Math.min(99, Math.round(value))); },
    complete() { target = 100; return pct >= 100 ? Promise.resolve() : new Promise(resolve => doneResolvers.push(resolve)); },
    stop() { if (timer) clearInterval(timer); timer = null; },
  };
}

/* Export the notebook as JSON — same owner/public export route this button always used (see
   apiBase declaration at the top of this file; same JSON shape/endpoint pattern notebooks.js
   uses for the "My Notes" list's Export JSON action), just fetched instead of navigated to so
   the download can be held back until the response is actually fully in hand, with real
   progress along the way when the server reports a Content-Length. */
async function exportNotebookJson() {
  const btn = document.getElementById('exportJsonBtn');
  if (!btn || btn.disabled) return;
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Exporting JSON');
  progress.start();
  try {
    const exportUrl = `${apiBase}/${notebookId}/export`;
    const res = await fetch(exportUrl, { credentials: 'same-origin' });
    if (!res.ok) {
      let message; try { message = (await res.json()).message; } catch (e) {}
      throw new Error(message || 'Unable to export this notebook.');
    }
    // Body is only read here to drive real byte progress off Content-Length — the bytes
    // themselves aren't kept. The actual download is triggered below through the same
    // endpoint's Content-Disposition header instead of a client-side Blob, because a blob:
    // URL carries no Content-Disposition of its own: it made the browser open/display the
    // JSON in a tab rather than download it, the exact failure mode the PDF export's own
    // hidden-iframe handoff (further down this file) already exists to avoid.
    const total = Number(res.headers.get('Content-Length')) || 0;
    const reader = res.body?.getReader();
    let received = 0;
    if (reader) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        received += value.length;
        if (total > 0) progress.advance((received / total) * 99);
      }
    } else {
      await res.arrayBuffer();
    }
    await progress.complete();
    let jsonFrame = document.getElementById('notesJsonExportFrame');
    if (!jsonFrame) { jsonFrame = document.createElement('iframe'); jsonFrame.name = jsonFrame.id = 'notesJsonExportFrame'; jsonFrame.style.display = 'none'; document.body.appendChild(jsonFrame); }
    // Cache-bust so re-exporting immediately after a previous one still reassigns a distinct
    // src — browsers don't reload an iframe whose src is set to the exact same string it
    // already has. The server ignores unknown query params, so this doesn't affect the export.
    jsonFrame.src = `${exportUrl}${exportUrl.includes('?') ? '&' : '?'}_=${Date.now()}`;
    toast('Notebook exported as JSON.');
  } catch (error) {
    toast(error.message || 'Unable to export this notebook.', 'error');
  } finally {
    progress.stop();
    btn.disabled = false;
    btn.classList.remove('exporting');
    btn.innerHTML = originalHTML;
  }
}
document.getElementById('exportJsonBtn')?.addEventListener('click', exportNotebookJson);

document.getElementById('readModeBtn')?.addEventListener('click', () => setMode(currentMode === 'edit' ? 'read' : 'edit'));
document.getElementById('fullscreenBtn')?.addEventListener('click', toggleFullscreen);
document.getElementById('toolbarToggleBtn')?.addEventListener('click', () => { if (currentMode === 'read') return; setToolbarVisible(!toolbarVisible); });

/* Export whole notebook as PDF — renders each page's objects (only the objects — see below) to
   a PNG on a private off-screen StaticCanvas, never touching the live canvas/objects, then posts
   the images to the server to be assembled into one multi-page PDF by the existing ReportLab-
   based pdf_service (build_notebook_pdf) — reusing that service rather than re-implementing PDF
   generation, while getting pixel-accurate content fidelity for free since Fabric itself does
   the rendering. Every page is re-serialized via toObject(NOTES_PROPS) + enlivenObjects (the
   same round-trip already used by save/undo), so this can't corrupt or leak live object
   instances into another canvas.
   Every page's export canvas is exactly PAGE_WIDTH x PAGE_HEIGHT (declared further up this
   file) — the notebook's one canonical, fixed page size. This is a plain capture, not a fit: the
   editor itself now enforces that fixed geometry (see constrainObjectToPage), so a page's
   content can never actually exceed these bounds by the time it gets here — export doesn't need
   to inspect content, compute a bounding box, or invent any page size of its own; it just
   mirrors the one source of truth. Objects are exported at their real, unshifted notebook
   coordinates, so every PDF page ends up the same fixed size with content at its true notebook
   position/scale.
   The dot-grid page background is deliberately NOT drawn into this raster — it's reproduced
   once server-side as a reusable PDF vector resource (pdf_service.build_notebook_pdf) instead,
   both because it's the "proper" way to represent a repeating vector pattern in a PDF, and
   because baking a full-page dot pattern into every page's raster was the dominant cost behind
   an oversized export: a mostly-transparent content-only PNG compresses far better than one
   with a fine dot pattern covering the whole page. Only that live theme's colors need to travel
   to the server (gridTheme below), so the PDF background still matches what was on screen. */
// ROOT CAUSE of PDF export failing with "Tainted canvases may not be exported" whenever a page
// has images: the export canvas below calls toDataURL(), which the browser refuses the instant
// the canvas has ever drawn a cross-origin image — and this app's cloud storage backend hands
// out direct signed URLs on the STORAGE PROVIDER'S OWN domain (not this app's), which is exactly
// that cross-origin case, regardless of what CORS policy that bucket does or doesn't have.
// Swapping each image's `src` to this app's own same-origin asset-file endpoint (see
// asset_file_api in app/routes/notes.py — it streams the real bytes from whichever storage
// backend is configured) removes the cross-origin condition entirely before the export canvas
// ever loads the image, so it can never taint. Scoped to export prep only — the live canvas
// keeps loading images from their normal (fast, freshly-signed) URLs everywhere else.
function withExportSafeImageSrc(raw) {
  return raw.map(entry => (entry.type === 'image' && entry.assetId) ? { ...entry, src: `/api/v01/assets/${entry.assetId}/file` } : entry);
}
async function getPageObjectsForExport(pageId) {
  if (pageId === activePageId) return withExportSafeImageSrc(canvas.getObjects().map(o => o.toObject(NOTES_PROPS)));
  const cached = pageObjectsCache.get(pageId);
  if (cached) return withExportSafeImageSrc(cached.objects.map(o => o.toObject(NOTES_PROPS)));
  const data = await api(`${apiBase}/${notebookId}/pages/${pageId}/objects`);
  return withExportSafeImageSrc(data.objects.map(buildRawFabricEntry));
}
async function exportNotebookPdf() {
  if (!pageCache.length) { toast('No pages to export.', 'error'); return; }
  const btn = document.getElementById('exportPdfBtn');
  if (!btn || btn.disabled) return;
  const originalIcon = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Exporting PDF');
  progress.start();
  const offEl = document.createElement('canvas');
  const offCanvas = new fabric.StaticCanvas(offEl, { renderOnAddRemove: false });
  // Raw --bg/--border custom-property text (may be '#rrggbb' or 'rgba(...)' depending on the
  // active theme — see theme.css) forwarded as-is; pdf_service parses either form server-side.
  const shellStyle = getComputedStyle(document.getElementById('canvasShell'));
  const gridTheme = {
    bg: shellStyle.getPropertyValue('--bg').trim(),
    dot: shellStyle.getPropertyValue('--border').trim(),
  };
  try {
    const rendered = [];
    const totalPages = pageCache.length;
    for (let i = 0; i < totalPages; i++) {
      const page = pageCache[i];
      const raw = await getPageObjectsForExport(page.id);
      const objects = await new Promise((resolve, reject) => {
        const result = fabric.util.enlivenObjects(raw, resolve);
        if (result && typeof result.then === 'function') result.then(resolve).catch(reject);
      });
      offCanvas.clear();
      offCanvas.setDimensions({ width: PAGE_WIDTH, height: PAGE_HEIGHT });
      // constrainObjectToPage here (not just live in the editor) is what keeps a page saved
      // before this fixed-page architecture existed from exporting with clipped content the
      // very first time it's exported without having been opened in the editor since — same
      // safety net as object:added, applied here because this off-screen canvas is separate
      // from the live one and never fires that event.
      objects.forEach(o => { constrainObjectToPage(o); offCanvas.add(o); });
      offCanvas.renderAll();
      const image = offCanvas.toDataURL({ format: 'png', multiplier: 2 });
      rendered.push({ title: page.title, image });
      // Re-rasterizing every page's objects is the dominant, genuinely measurable cost of this
      // export, so it drives the first 90% of the bar in direct proportion to pages actually
      // rendered — not an arbitrary schedule.
      progress.advance(((i + 1) / totalPages) * 90);
    }
    // Submitted as a real HTML form POST (into a hidden iframe) instead of fetch+Blob+
    // createObjectURL — that path made Chrome open the PDF through a blob: URL, whose
    // download could fail with "network error". A native form submission lets the browser
    // handle the response as a direct file download itself, driven by the server's
    // Content-Disposition header, with no blob: URL involved at any point.
    let frame = document.getElementById('notesPdfExportFrame');
    if (!frame) { frame = document.createElement('iframe'); frame.name = frame.id = 'notesPdfExportFrame'; frame.style.display = 'none'; document.body.appendChild(frame); }
    const form = document.createElement('form');
    // Always the owner-path endpoint (not `apiBase`, which is /api/v01/library for a public
    // viewer) — there is exactly one export-pdf route/service; export_notebook_pdf_api accepts
    // either an owned or a currently-public notebook, so the public viewer reuses it as-is
    // instead of a second export implementation.
    form.method = 'POST'; form.action = `/api/v01/notebooks/${notebookId}/export-pdf`; form.target = 'notesPdfExportFrame'; form.style.display = 'none';
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = 'pages'; input.value = JSON.stringify(rendered);
    form.appendChild(input);
    const themeInput = document.createElement('input');
    themeInput.type = 'hidden'; themeInput.name = 'gridTheme'; themeInput.value = JSON.stringify(gridTheme);
    form.appendChild(themeInput);
    // A successful download (Content-Disposition: attachment) never navigates the iframe, so
    // no 'load' fires for it; an error response (JSON) does navigate it, and — being
    // same-origin — its body is readable here, letting a real failure surface instead of
    // always claiming success. This is the only completion signal this download mechanism
    // gives back, so it's also what the progress bar's last stretch (91-99%) is paced against
    // below, instead of racing to 100% on its own.
    let settled = false;
    let resolveSettle;
    const settlePromise = new Promise(resolve => { resolveSettle = resolve; });
    const settle = (ok, message) => { if (settled) return; settled = true; resolveSettle({ ok, message }); };
    frame.onload = () => {
      let text = ''; try { text = frame.contentDocument?.body?.innerText || ''; } catch (e) {}
      if (text.trim()) { let message; try { message = JSON.parse(text).message; } catch (e) {} settle(false, message); }
    };
    document.body.append(form); form.submit(); form.remove();
    const waitStart = Date.now();
    const waitMs = 1200;
    const waitTimer = setInterval(() => {
      progress.advance(90 + Math.min(1, (Date.now() - waitStart) / waitMs) * 9);
    }, 80);
    setTimeout(() => settle(true), waitMs);
    const result = await settlePromise;
    clearInterval(waitTimer);
    if (!result.ok) throw new Error(result.message || 'Unable to export this notebook.');
    await progress.complete();
    toast('Notebook exported as PDF.');
  } catch (error) {
    toast(error.message || 'Unable to export this notebook.', 'error');
  } finally {
    offCanvas.dispose();
    progress.stop();
    btn.disabled = false;
    btn.classList.remove('exporting');
    btn.innerHTML = originalIcon;
  }
}
document.getElementById('exportPdfBtn')?.addEventListener('click', exportNotebookPdf);

/* Pages sidebar — collapsible, independent of Mode/Screen/Toolbar state. Never destroys or
   recreates the Fabric canvas; just frees horizontal space and lets the existing resize()
   logic grow the canvas into it. Zoom/pan and object positions are untouched. */
let pagesCollapsed = false;
function setPagesCollapsed(collapsed) {
  pagesCollapsed = !!collapsed;
  const rail = document.getElementById('pageRail');
  const btn = document.getElementById('pagesCollapseBtn');
  rail?.classList.toggle('collapsed', pagesCollapsed);
  if (btn) { btn.title = pagesCollapsed ? 'Show Pages' : 'Collapse Pages'; btn.setAttribute('aria-label', btn.title); }
  refreshLayout();
}
document.getElementById('pagesCollapseBtn')?.addEventListener('click', () => setPagesCollapsed(!pagesCollapsed));

/* ════════════════════════════════════════════════════════════════ */

function markDirty() { if (loading) return; dirty = true; clearTimeout(saveTimer); if (autoSaveEnabled) { status('Saving…', 'saving'); saveTimer = setTimeout(save, 1400); } else status('Unsaved changes', 'saving'); snapshot(); }
function snapshot() { if (loading) return; const state = JSON.stringify(canvas.toJSON(NOTES_PROPS)); if (history[historyIndex] === state) { updateUndoRedoUI(); return; } history = history.slice(0, historyIndex + 1); history.push(state); historyIndex = history.length - 1; if (history.length > 40) { history.shift(); historyIndex -= 1; } updateUndoRedoUI(); }
/* Shared by loadPage() and restoreHistory() so a page load and an Undo/Redo
   reconstruct objects through the exact same finalization — this is the one
   place that decides editable/lockScaling/control-visibility/min-height for
   a freshly-enlivened object, so the two paths can never drift apart. */
function finalizeLoadedObject(o) {
  if (o.objectType === 'rich_text') configureNormalText(o);
  if (o.objectType === 'sticky_note') configureStickyNote(o);
  // Runs AFTER configureNormalText/configureStickyNote so it's the final word for every
  // text-like object, including shape-label textboxes that skip those two. editable is always
  // false (see the native-text-editor overlay comment above beginNativeTextEdit) — Fabric's own
  // IText/Textbox editing must never engage, on freshly-loaded objects either. Also the catch-all
  // for objectCaching:false on any text-like object saved before that fix existed, or on shape
  // labels either function never touches, so old notebooks get crisp text again on their very
  // next load, no re-save needed. charSpacing here is the same fix/reasoning as
  // configureNormalText/configureStickyNote above (per-run text-batching mismatch) — repeated
  // here so it also self-heals shape-label textboxes (which skip those two) and any textbox
  // saved before this fix existed, without needing a re-save.
  if (o.type === 'i-text' || o.type === 'textbox') { o.editable = false; o.objectCaching = false; o.charSpacing = o.charSpacing || 1; }
  // Self-heals any Textbox saved with a leftover scaleX/scaleY (see preserveStickyTextSize) —
  // guarantees the border and the wrapped text it's drawn from always agree on the page's
  // very first paint, not only after the next edit touches the object.
  preserveStickyTextSize(o);
  clampTextHeight(o);
}
function setTool(name) { activeTool=name; window.__notesEraserActive = false; window.__notesDeactivateInkTools?.(); document.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === name)); canvas.isDrawingMode = name === 'draw' && currentMode === 'edit'; canvas.selection = name === 'select' && currentMode === 'edit'; const cursor = name === 'select' ? 'default' : name === 'draw' ? 'crosshair' : 'text'; canvas.defaultCursor = cursor; canvas.hoverCursor = cursor; canvas.freeDrawingCursor = cursor; }
function addObject(object, type) { if (currentMode === 'read') return; object.objectId = object.objectId || uid(); object.objectType = type; canvas.add(object).setActiveObject(object); canvas.requestRenderAll(); markDirty(); }
function objectRecord(object) {
  // Root cause of text overflowing/clipping past its box after a reload: scaling a Textbox as
  // part of a MULTI-object selection scales the group, not the child — 'object:scaling'/
  // 'object:modified' below never fire for that child, so its own scaleX/scaleY can end up
  // non-1 with the wrap still computed for the old (unscaled) width. Normalizing here too, on
  // the way OUT to storage, means a corrupted scale can never be persisted in the first place,
  // on top of finalizeLoadedObject() self-healing it on the way back IN.
  preserveStickyTextSize(object);
  return { id: object.objectId || (object.objectId = uid()), object_type: object.objectType || 'shape', asset_id: object.assetId || null, transform: { left: object.left || 0, top: object.top || 0, scaleX: object.scaleX || 1, scaleY: object.scaleY || 1, angle: object.angle || 0 }, payload: { fabric: object.toObject(NOTES_PROPS) } };
}

/* A page can hold any number of objects — the ONLY thing chunked here is the save
   request itself (see SAVE_CHUNK_SIZE / notes_service.save_page_objects). Every chunk targets
   the SAME activePageId; nothing here ever creates or switches pages, no matter how large the
   page gets. start_index tells the server each chunk's true position so z_index (draw/stacking
   order) stays correct across chunks instead of every chunk restarting from 0. deleted_ids only
   needs to ride along on one chunk — the delete itself doesn't care about ordering. */
async function save() {
  if (isPublicView) return;
  if (!dirty || saving || !activePageId) return;
  saving = true; status('Saving…', 'saving');
  try {
    const objects = canvas.getObjects().map(objectRecord);
    const pageId = activePageId;
    if (!objects.length) {
      await api(`${apiBase}/${notebookId}/pages/${pageId}/objects`, { method: 'PUT', body: JSON.stringify({ objects: [], deleted_ids: deletedIds, start_index: 0 }) });
    } else {
      for (let start = 0; start < objects.length; start += SAVE_CHUNK_SIZE) {
        const chunk = objects.slice(start, start + SAVE_CHUNK_SIZE);
        await api(`${apiBase}/${notebookId}/pages/${pageId}/objects`, { method: 'PUT', body: JSON.stringify({ objects: chunk, deleted_ids: start === 0 ? deletedIds : [], start_index: start }) });
      }
    }
    dirty = false; deletedIds = []; status('Saved');
  } catch (error) { status('Save failed', 'failed'); toast(error.message, 'error'); } finally { saving = false; }
}
function updatePageNav() {
  const label = document.getElementById('pageNavLabel');
  const prevBtn = document.getElementById('prevPageBtn');
  const nextBtn = document.getElementById('nextPageBtn');
  const index = pageCache.findIndex(p => p.id === activePageId);
  if (label) label.textContent = pageCache.length && index !== -1 ? `Page ${index + 1} of ${pageCache.length}` : '';
  if (prevBtn) prevBtn.disabled = index <= 0;
  if (nextBtn) nextBtn.disabled = index === -1 || index >= pageCache.length - 1;
  // Collapsed rail mirrors the same state — see the "2. COLLAPSED PAGE SIDEBAR" UX rework:
  // prev/next/current-page/actions must all stay reachable without expanding the sidebar.
  const collapsedPrev = document.getElementById('collapsedPrevBtn');
  const collapsedNext = document.getElementById('collapsedNextBtn');
  const collapsedNum = document.getElementById('collapsedPageNumber');
  if (collapsedPrev) collapsedPrev.disabled = index <= 0;
  if (collapsedNext) collapsedNext.disabled = index === -1 || index >= pageCache.length - 1;
  if (collapsedNum) {
    const text = index === -1 ? '–' : String(index + 1);
    collapsedNum.textContent = text;
    collapsedNum.title = pageCache.length ? `Page ${index + 1} of ${pageCache.length} — click to jump` : 'No pages';
    collapsedNum.classList.toggle('compact-num', text.length >= 4);
  }
}
function goToAdjacentPage(delta) {
  const index = pageCache.findIndex(p => p.id === activePageId);
  const target = pageCache[index + delta];
  if (!target) return;
  showUnsavedChangesDialog(() => loadPage(target.id));
}
document.getElementById('prevPageBtn')?.addEventListener('click', () => goToAdjacentPage(-1));
document.getElementById('nextPageBtn')?.addEventListener('click', () => goToAdjacentPage(1));
document.getElementById('collapsedPrevBtn')?.addEventListener('click', () => goToAdjacentPage(-1));
document.getElementById('collapsedNextBtn')?.addEventListener('click', () => goToAdjacentPage(1));
document.getElementById('collapsedPageNumber')?.addEventListener('click', event => { event.stopPropagation(); openPageJumpPopover(event.currentTarget); });
document.getElementById('collapsedPageMenuBtn')?.addEventListener('click', event => {
  event.stopPropagation();
  if (currentMode === 'read') return;
  const page = pageCache.find(p => p.id === activePageId);
  if (!page) return;
  const isSameMenu = pageMenuPortal?.dataset.triggerPageId === page.id;
  closePageMenus();
  if (!isSameMenu) openPageMenu(page, null, event.currentTarget);
});
async function loadPage(id) {
  if (!id) { showEmptyEditor(); return; }
  finishNativeTextEdit();
  await save();

  // Snapshot the page we're LEAVING using its current live objects (so any
  // adds/removes/edits made this session are reflected), not the stale
  // copy captured back when that page was first fetched — otherwise a
  // revisit after editing would silently show outdated content.
  if (activePageId && activePageId !== id) {
    pageObjectsCache.set(activePageId, { objects: canvas.getObjects().slice(), cachedAt: Date.now() });
  }

  activePageId = id;
  hideEmptyEditor();
  document.querySelectorAll('.page-item').forEach(p => p.classList.toggle('active', p.dataset.pageId === id));

  // Instant path: this page's objects (images included) are already live,
  // enlivened fabric instances from earlier this session — just re-attach
  // them to the canvas. No network call, no re-fetching images.
  const cached = pageObjectsCache.get(id);
  if (cached && (Date.now() - cached.cachedAt) < PAGE_CACHE_TTL_MS) {
    loading = true;
    canvas.clear();
    updateObjectCount();
    deletedIds = [];
    cached.objects.forEach(o => canvas.add(o));
    applyFixedPageBounds();
    canvas.requestRenderAll();
    history = []; historyIndex = -1; loading = false; snapshot(); dirty = false; status('Saved');
    updatePageNav();
    scheduleAdjacentPreload(id);
    return;
  }

  loading = true;
  document.getElementById('canvasLoading').hidden = false;
  canvas.clear();
  updateObjectCount();
  deletedIds = [];
  try {
    const data = await api(`${apiBase}/${notebookId}/pages/${id}/objects`);
    const raw = data.objects.map(buildRawFabricEntry);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Loading this page took too long. Some saved content may be corrupted.')), 10000);
      const finish = objects => {
        clearTimeout(timer);
        objects.forEach(o => { finalizeLoadedObject(o); canvas.add(o); });
        applyFixedPageBounds();
        canvas.requestRenderAll();
        pageObjectsCache.set(id, { objects, cachedAt: Date.now() });
        resolve();
      };
      const result = fabric.util.enlivenObjects(raw, finish);
      if (result && typeof result.then === 'function') result.then(finish).catch(reject);
    });
    history = []; historyIndex = -1; loading = false; snapshot(); dirty = false; status('Saved');
    updatePageNav();
    scheduleAdjacentPreload(id);
  } catch (error) {
    toast(error.message, 'error'); status('Load failed', 'failed');
  } finally {
    loading = false; document.getElementById('canvasLoading').hidden = true;
  }
}
async function openBlankPage(id) { await save(); if (activePageId && activePageId !== id) { pageObjectsCache.set(activePageId, { objects: canvas.getObjects().slice(), cachedAt: Date.now() }); } activePageId = id; hideEmptyEditor(); loading = true; document.querySelectorAll('.page-item').forEach(p => p.classList.toggle('active', p.dataset.pageId === id)); canvas.clear(); updateObjectCount(); deletedIds = []; history = []; historyIndex = -1; dirty = false; loading = false; applyFixedPageBounds(); document.getElementById('canvasLoading').hidden = true; canvas.requestRenderAll(); status('Saved'); updateUndoRedoUI(); updatePageNav(); pageObjectsCache.set(id, { objects: [], cachedAt: Date.now() }); }
function showEmptyEditor() { activePageId = null; dirty = false; canvas.clear(); updateObjectCount(); dirty = false; history = []; historyIndex = -1; document.getElementById('canvasLoading').hidden = true; const shell = document.getElementById('canvasShell'); let empty = document.getElementById('emptyPageEditor'); if (!empty) { empty = document.createElement('div'); empty.id = 'emptyPageEditor'; empty.className = 'canvas-empty-state'; empty.innerHTML = '<i class="far fa-file-alt"></i><strong>No pages yet</strong><span>Create a page to begin taking notes.</span>'; shell.append(empty); } empty.hidden = false; status('Saved'); updateUndoRedoUI(); updatePageNav(); }
function hideEmptyEditor() { document.getElementById('emptyPageEditor')?.setAttribute('hidden', ''); }
// Canonical notebook page size — the ONE source of truth for every page's physical geometry.
// Every page's canvas is exactly this size, always; nothing grows it, nothing shrinks it. Same
// numeric values the old "floor" used, so already-saved content that was positioned assuming
// this working area doesn't visually jump. Shared by resize()/constrainObjectToPage()/
// applyFixedPageBounds() and mirrored server-side in app/services/pdf_service.py (_CANVAS_PX_W/H)
// so the PDF export never has to invent its own page dimensions — it just reads this one.
const PAGE_WIDTH = 2400, PAGE_HEIGHT = 1600;
function resize() { canvas.setDimensions({ width: PAGE_WIDTH, height: PAGE_HEIGHT }); canvas.calcOffset(); canvas.requestRenderAll(); }
/* Keeps an object's bounding box fully inside the fixed page rectangle [0,PAGE_WIDTH] x
   [0,PAGE_HEIGHT] — nudging its position back in if a drag/resize/paste would put any part of
   it outside, rather than letting it silently extend past the page (the old architecture just
   grew the canvas to whatever content needed; this fixed-page architecture can't do that, so
   content has to stay inside instead). Only ever translates position, never resizes/hides/
   deletes the object, so nothing is cropped or made to disappear — an object already bigger
   than the page in one dimension gets pinned to that edge (best effort; it can't be made to
   fully fit without shrinking it, which this deliberately never does).
   Wired to object:added/object:moving/object:modified below, so it covers live creation,
   dragging, and resizing uniformly — including objects freshly restored from a page saved
   under the old grow-to-fit architecture (canvas.add() during that bulk restore fires
   object:added exactly like live editing does), which is what keeps any such legacy content
   reachable/visible instead of silently clipped by the now-fixed canvas bounds. */
function constrainObjectToPage(object) {
  if (!object) return;
  object.setCoords();
  const box = object.getBoundingRect(true, true);
  let dx = 0, dy = 0;
  if (box.left < 0) dx = -box.left;
  else if (box.left + box.width > PAGE_WIDTH) dx = PAGE_WIDTH - (box.left + box.width);
  if (box.top < 0) dy = -box.top;
  else if (box.top + box.height > PAGE_HEIGHT) dy = PAGE_HEIGHT - (box.top + box.height);
  if (dx || dy) { object.set({ left: (object.left || 0) + dx, top: (object.top || 0) + dy }); object.setCoords(); }
}
// Resets the canvas to the fixed page size after a page loads/switches — with PAGE_WIDTH/HEIGHT
// truly fixed this is mostly a no-op safety net (canvas.setDimensions is idempotent), but keeps
// this an explicit step rather than an assumption. The legacy-content safety net described above
// (constrainObjectToPage via object:added) already ran while these objects were being restored,
// so nothing further is needed here.
function applyFixedPageBounds() { canvas.setDimensions({ width: PAGE_WIDTH, height: PAGE_HEIGHT }); canvas.calcOffset(); }
/* Floating page-menu / rename-prompt / jump-popover — a single shared "portal" so only one
   is ever open at once, all positioned by the same routine. Appended to `root`, never
   document.body: during native Fullscreen only root's own subtree is painted, so a
   body-appended popup would silently render nowhere — appending inside root is what makes
   the 3-dot menu (and rename/jump) work identically in normal mode, fullscreen, and the
   fs-fallback mode (whose elevated z-index also lives on root, so descendants share it). */
let pageMenuPortal = null;
function closePageMenus() { pageMenuPortal?.remove(); pageMenuPortal = null; }
function placeFloatingPanel(panel, trigger) {
  root.append(panel);
  const rect = trigger.getBoundingClientRect(); const margin = 8;
  const panelWidth = panel.offsetWidth, panelHeight = panel.offsetHeight;
  const left = Math.max(margin, Math.min(rect.right - panelWidth, window.innerWidth - panelWidth - margin));
  const below = rect.bottom + 4; const top = below + panelHeight <= window.innerHeight - margin ? below : Math.max(margin, rect.top - panelHeight - 4);
  panel.style.left = `${left}px`; panel.style.top = `${top}px`;
  pageMenuPortal = panel;
}
function openPageMenu(page, titleElement, trigger) {
  if (currentMode === 'read') return;
  closePageMenus();
  const menu = document.createElement('div');
  menu.dataset.pageMenu = '1';
  menu.setAttribute('role', 'menu');
  menu.style.cssText = 'position:fixed;z-index:1100;min-width:132px;padding:4px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow-lg)';
  const action = (label, icon, callback, danger = false) => {
    const button = document.createElement('button'); button.type = 'button'; button.setAttribute('role', 'menuitem'); button.innerHTML = `<i class="${icon}"></i> ${label}`;
    button.style.cssText = `display:block;width:100%;padding:7px 8px;border:0;border-radius:6px;background:transparent;color:${danger ? 'var(--danger-text)' : 'var(--text-2)'};font:inherit;font-size:.72rem;text-align:left;cursor:pointer`;
    button.addEventListener('mouseenter', () => { button.style.background = 'var(--surface-2)'; }); button.addEventListener('mouseleave', () => { button.style.background = 'transparent'; }); button.addEventListener('click', callback);
    return button;
  };
  menu.append(
    // titleElement is null when opened from the collapsed rail (no visible row to swap an
    // <input> into) — falls back to a small floating rename prompt instead.
    action('Rename', 'fas fa-pen', () => { closePageMenus(); if (titleElement) startInlineRename(page, titleElement); else openRenamePrompt(page, trigger); }),
    action('Delete', 'fas fa-trash-alt', () => { closePageMenus(); root.dataset.pageTarget = page.id; document.getElementById('deletePageMessage').textContent = ''; document.getElementById('deletePageModal').classList.add('open'); }, true)
  );
  menu.dataset.triggerPageId = page.id; menu.addEventListener('click', event => event.stopPropagation());
  placeFloatingPanel(menu, trigger);
}
function openRenamePrompt(page, trigger) {
  if (currentMode === 'read') return;
  closePageMenus();
  const panel = document.createElement('div');
  panel.dataset.pageMenu = '1';
  panel.style.cssText = 'position:fixed;z-index:1100;padding:8px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow-lg);display:flex;gap:6px;align-items:center';
  const input = document.createElement('input'); input.value = page.title; input.maxLength = 160;
  input.style.cssText = 'width:150px;background:var(--bg-raised);border:1px solid var(--border-focus);border-radius:5px;color:var(--text-1);font:inherit;font-size:.76rem;padding:.3rem .4rem;outline:0';
  const confirm = document.createElement('button'); confirm.type = 'button'; confirm.title = 'Save'; confirm.innerHTML = '<i class="fas fa-check"></i>';
  confirm.style.cssText = 'width:26px;height:26px;border:0;border-radius:6px;background:var(--accent-subtle);color:var(--accent);cursor:pointer;flex-shrink:0';
  panel.append(input, confirm); panel.addEventListener('click', event => event.stopPropagation());
  const submit = async () => { if (await commitPageRename(page, input.value)) closePageMenus(); else input.focus(); };
  confirm.addEventListener('click', submit);
  input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); submit(); } if (event.key === 'Escape') { event.preventDefault(); closePageMenus(); } });
  placeFloatingPanel(panel, trigger);
  input.focus(); input.select();
}
/* Compact jump-to-page popover — the collapsed rail's page-number control opens this
   instead of expanding the sidebar, so 100s/1000s-of-pages notebooks stay fast to navigate. */
function openPageJumpPopover(trigger) {
  closePageMenus();
  if (!pageCache.length) return;
  const panel = document.createElement('div');
  panel.dataset.pageMenu = '1';
  panel.style.cssText = 'position:fixed;z-index:1100;padding:8px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow-lg);display:flex;flex-direction:column;gap:6px;min-width:120px';
  const label = document.createElement('span'); label.textContent = `Go to page (1–${pageCache.length})`; label.style.cssText = 'font-size:.65rem;color:var(--text-3)';
  const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:6px';
  const input = document.createElement('input'); input.type = 'number'; input.min = '1'; input.max = String(pageCache.length);
  const currentIndex = pageCache.findIndex(p => p.id === activePageId);
  input.value = String(currentIndex === -1 ? 1 : currentIndex + 1);
  input.style.cssText = 'width:64px;background:var(--bg-raised);border:1px solid var(--border-focus);border-radius:5px;color:var(--text-1);font:inherit;font-size:.76rem;padding:.3rem .4rem;outline:0';
  const go = document.createElement('button'); go.type = 'button'; go.textContent = 'Go';
  go.style.cssText = 'padding:.3rem .6rem;border:0;border-radius:6px;background:var(--accent-subtle);color:var(--accent);cursor:pointer;font:600 .72rem inherit';
  row.append(input, go); panel.append(label, row); panel.addEventListener('click', event => event.stopPropagation());
  const submit = () => { const target = pageCache[Number(input.value) - 1]; closePageMenus(); if (target) showUnsavedChangesDialog(() => loadPage(target.id)); };
  go.addEventListener('click', submit);
  input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); submit(); } if (event.key === 'Escape') { event.preventDefault(); closePageMenus(); } });
  placeFloatingPanel(panel, trigger);
  input.focus(); input.select();
}
function pageNameTaken(title, excludeId = '') { const normalized = title.trim().toLocaleLowerCase(); return pageCache.some(page => page.id !== excludeId && page.title.trim().toLocaleLowerCase() === normalized); }
/* Single validated save path for a page rename, shared by the expanded list's inline edit
   (startInlineRename) and the collapsed rail's floating prompt (openRenamePrompt) so the two
   can never drift apart. */
async function commitPageRename(page, nextRaw) {
  const next = nextRaw.trim();
  if (next === page.title) return true;
  if (!next) { toast('Page name cannot be empty.', 'error'); return false; }
  if (pageNameTaken(next, page.id)) { toast('A page with this name already exists in this notebook.', 'error'); return false; }
  try {
    const result = await api(`${apiBase}/${notebookId}/pages/${page.id}`, { method: 'PATCH', body: JSON.stringify({ title: next }) });
    page.title = result.page.title;
    pageCache = pageCache.map(p => p.id === page.id ? page : p);
    renderPages(pageCache, activePageId, false);
    return true;
  } catch (error) { toast(error.message, 'error'); return false; }
}
async function startInlineRename(page, titleElement) {
  if (currentMode === 'read') return;
  const original = page.title;
  const input = document.createElement('input');
  input.className = 'page-inline-name'; input.value = original; input.maxLength = 160;
  input.style.cssText = 'width:100%;min-width:0;background:var(--bg-raised);border:1px solid var(--border-focus);border-radius:5px;color:var(--text-1);font:inherit;font-size:.76rem;padding:.25rem .35rem;outline:0';
  titleElement.replaceWith(input); input.focus(); input.select();
  let complete = false;
  const finish = async save => {
    if (complete) return; complete = true;
    if (!save || input.value.trim() === original) { input.replaceWith(titleElement); return; }
    const ok = await commitPageRename(page, input.value);
    if (!ok) { complete = false; input.focus(); }
  };
  input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); finish(true); } if (event.key === 'Escape') { event.preventDefault(); finish(false); } });
  input.addEventListener('blur', () => finish(true));
}
function renderPages(pages, selectedId = null, shouldLoad = true) {
  pageCache = pages; const list = document.getElementById('pageList'); list.innerHTML = '';
  const selected = selectedId || activePageId || pages[0]?.id;
  pages.forEach((page, index) => {
    const row = document.createElement('div'); row.style.cssText = 'position:relative;display:flex;align-items:center;margin-bottom:2px';
    const open = document.createElement('button'); open.type = 'button'; open.className = `page-item ${page.id === selected ? 'active' : ''}`; open.dataset.pageId = page.id;
    const num = document.createElement('span'); num.className = 'page-item-num'; num.textContent = String(index + 1);
    const title = document.createElement('span'); title.textContent = page.title; open.append(num, title);
    open.addEventListener('click', () => { if (activePageId !== page.id) showUnsavedChangesDialog(() => loadPage(page.id)); });
    title.addEventListener('dblclick', event => { if (currentMode === 'read') return; event.preventDefault(); event.stopPropagation(); startInlineRename(page, title); });
    const dots = document.createElement('button'); dots.type = 'button'; dots.title = 'Page actions'; dots.className = 'page-dots-btn'; dots.innerHTML = '<i class="fas fa-ellipsis-h"></i>';
    dots.style.cssText = 'width:26px;height:26px;border:0;border-radius:6px;background:transparent;color:var(--text-3);cursor:pointer;position:absolute;right:4px';
    dots.addEventListener('click', event => { event.stopPropagation(); if (currentMode === 'read') return; const isSameMenu = pageMenuPortal?.dataset.triggerPageId === page.id; closePageMenus(); if (!isSameMenu) openPageMenu(page, title, dots); });
    row.append(open, dots); list.appendChild(row);
  });
  if (shouldLoad && selected && selected !== activePageId) loadPage(selected);
  updatePageNav();
}
async function refreshPages() { const data = await api(`${apiBase}/${notebookId}/pages`); const next = data.pages.some(page => page.id === activePageId) ? activePageId : data.pages[0]?.id || null; renderPages(data.pages, next, false); if (next) await loadPage(next); else showEmptyEditor(); }
function closeModal(id) { document.getElementById(id)?.classList.remove('open'); }
let pendingNavigation = null;
function showUnsavedChangesDialog(action) { if (!dirty) return action(); pendingNavigation = action; let dialog = document.getElementById('unsavedChangesModal'); if (!dialog) { dialog = document.createElement('div'); dialog.id = 'unsavedChangesModal'; dialog.className = 'modal-bd'; dialog.innerHTML = '<div class="modal-box notes-modal"><div class="modal-hdr"><h5><i class="fas fa-exclamation-circle" style="color:var(--warning-text)"></i> Unsaved Changes</h5></div><div class="modal-bdy"><p class="notes-confirm-text">You have unsaved changes. What would you like to do?</p></div><div class="modal-ftr"><button class="sbtn sbtn-ghost" data-unsaved-cancel>Cancel</button><button class="sbtn sbtn-danger" data-unsaved-discard>Discard Changes</button><button class="sbtn sbtn-primary" data-unsaved-save>Save &amp; Continue</button></div></div>'; root.append(dialog); dialog.querySelector('[data-unsaved-cancel]').addEventListener('click', () => { pendingNavigation = null; dialog.classList.remove('open'); }); dialog.querySelector('[data-unsaved-discard]').addEventListener('click', () => { dirty = false; deletedIds = []; const next = pendingNavigation; pendingNavigation = null; dialog.classList.remove('open'); next?.(); }); dialog.querySelector('[data-unsaved-save]').addEventListener('click', async () => { const next = pendingNavigation; await save(); if (dirty) return; pendingNavigation = null; dialog.classList.remove('open'); next?.(); }); } dialog.classList.add('open'); }

function clampTextHeight(object) { if (!['sticky_note', 'rich_text'].includes(object?.objectType)) return; if (object.minHeight && object.height < object.minHeight) object.set('height', object.minHeight); }
function preserveStickyTextSize(object) { if (!['sticky_note', 'rich_text'].includes(object?.objectType) || object.type !== 'textbox') return; const scaleX = Math.abs(object.scaleX || 1), scaleY = Math.abs(object.scaleY || 1); if (scaleX === 1 && scaleY === 1) return; const requestedWidth = Math.max(140, object.width * scaleX); const requestedHeight = Math.max(40, object.height * scaleY); object.set({ width: requestedWidth, scaleX: 1, scaleY: 1 }); object.initDimensions(); if (scaleY !== 1) object.minHeight = Math.max(requestedHeight, object.height); clampTextHeight(object); object.setCoords(); }
function updateObjectCount() { const el = document.getElementById('objectCount'); if (el) el.innerHTML = `<i class="fas fa-shapes"></i> Objects: ${canvas.getObjects().length}`; }
canvas.on('object:added', event => { constrainObjectToPage(event.target); markDirty(); updateObjectCount(); }); canvas.on('object:moving', event => constrainObjectToPage(event.target)); canvas.on('object:scaling', event => preserveStickyTextSize(event.target)); canvas.on('object:modified', event => { preserveStickyTextSize(event.target); constrainObjectToPage(event.target); markDirty(); }); canvas.on('object:removed', e => { if (!loading && e.target?.objectId) deletedIds.push(e.target.objectId); markDirty(); updateObjectCount(); });
// A Textbox/Sticky Note growing taller mid-edit (constrainObjectToPage) and finishing an edit
// (clampTextHeight/constrainObjectToPage/markDirty) are handled directly inside the
// native-text-editor overlay's sync()/finishNativeTextEdit() (see beginNativeTextEdit above) —
// there's no Fabric 'text:changed'/'text:editing:exited' event to hook here since Fabric's own
// text editing is never entered.
canvas.on('path:created', e => {
  e.path.objectId = uid(); e.path.objectType = 'drawing';
  // Pen/Pencil/Highlighter all draw through the same fabric.PencilBrush (see drawing-tools.js
  // activateBrush) — only the resulting stroke's look differs, tagged here from the ink tool
  // that was active at draw time. Highlighter's and Pencil's translucency both live in their
  // rgba stroke color already (set on the brush itself, so the live in-progress stroke and the
  // finished path always render identically — no post-hoc opacity step here for Pencil anymore,
  // since applying one on top of the already-translucent stroke color would double-dim it).
  // 'multiply' on top lets ink beneath show through like a real marker instead of being
  // tinted/occluded.
  const inkTool = window.__notesInkTool;
  if (inkTool === 'highlighter') e.path.set({ globalCompositeOperation: 'multiply' });
}); canvas.on('mouse:dblclick', event => { if (currentMode === 'read') return; if (['i-text', 'textbox'].includes(event.target?.type)) { event.e.preventDefault(); beginNativeTextEdit(event.target, { x: event.e.clientX, y: event.e.clientY }); } });
canvas.on('mouse:down', async e => {
  // Recovers from the "editing kept alive through a toolbar control" state in beginNativeTextEdit
  // (blur to a <select>/color-input formatting control doesn't itself finish the edit): a canvas
  // click is "elsewhere entirely" — nothing else ends this edit session on its own once the
  // overlay itself is no longer the focused element.
  if (nativeTextEditor && document.activeElement !== nativeTextEditor.element) finishNativeTextEdit();
  if (e.e.altKey) { pan = true; lastPan = e.e; canvas.selection = false; return; } if (currentMode === 'read') return; if (e.target && ['i-text', 'textbox'].includes(e.target.type)) { e.e.preventDefault(); } if (!e.target && !window.__notesEraserActive && !window.__notesShapeToolActive && !canvas.isDrawingMode && activeTool !== 'image' && activeTool !== 'rect') { const p = canvas.getPointer(e.e); await createEditableText({ left: p.x, top: p.y, fontSize: window.__notesDefaultSize || 22, fill: token('--text-1'), themeText: true }, 'rich_text'); } }); canvas.on('mouse:move', e => { lastPointerCanvas = canvas.getPointer(e.e); if (!pan) return; const v = canvas.viewportTransform; v[4] += e.e.clientX - lastPan.clientX; v[5] += e.e.clientY - lastPan.clientY; lastPan = e.e; canvas.requestRenderAll(); }); canvas.on('mouse:up', () => { pan = false; canvas.selection = currentMode === 'edit'; });
// Tracked purely so object Ctrl+V (below) can paste near where the user's pointer actually is
// instead of always offsetting from the original object — cheap (just a coordinate pair, no
// render triggered) and touches nothing about existing pan/select/draw mouse handling above.
let lastPointerCanvas = null, pointerOverCanvas = false;
canvas.wrapperEl.addEventListener('mouseenter', () => { pointerOverCanvas = true; });
canvas.wrapperEl.addEventListener('mouseleave', () => { pointerOverCanvas = false; });
/* ROOT CAUSE of "the visible caret and the actual click/typing position disagree" in BOTH Text
   Box and Sticky Note (both are fabric.Textbox instances sharing this exact same pointer path):
   Fabric caches the canvas element's on-page position once, in canvas._offset (canvas.calcOffset(),
   built from getBoundingClientRect), and reuses that cached value for every click-to-text-index
   calculation (getSelectionStartFromPointer -> canvas.getPointer -> subtracts _offset) from then
   on. This app only refreshes it on window 'resize' (see resize()/applyFixedPageBounds()) — but
   the notebook page is a fixed 2400x1600 canvas inside a scrollable #canvasShell (see editor.css),
   so routine scrolling, or the page-rail sidebar collapsing/expanding, moves the canvas within the
   viewport constantly without ever firing 'resize'. Every click/drag afterwards is then off by
   however far the canvas has shifted since the last refresh: Fabric places the caret at whatever
   index that stale math produces (correctly, for the wrong click point), which is exactly the
   "caret overlaps a character" / "typing lands somewhere else" symptom — not a font, zoom, or
   rendering bug. A capture-phase listener on the wrapper fires before Fabric's own bubble-phase
   mousedown handler on the canvas element itself, so recalculating right here guarantees Fabric
   always reads a fresh offset at the exact moment it turns a click into a text index. */
canvas.wrapperEl.addEventListener('mousedown', () => canvas.calcOffset(), true);
canvas.wrapperEl.addEventListener('touchstart', () => canvas.calcOffset(), true);
canvas.on('mouse:wheel', e => { const shell = document.getElementById('canvasShell'); if (e.e.ctrlKey) { let z = canvas.getZoom() * (0.999 ** e.e.deltaY); z = Math.min(3, Math.max(.25, z)); canvas.zoomToPoint({ x: e.e.offsetX, y: e.e.offsetY }, z); canvas.zoomToPoint({ x: e.e.offsetX, y: e.e.offsetY }, z); repositionNativeTextEditor(); document.getElementById('zoomValue').textContent = `${Math.round(z * 100)}%`; e.e.preventDefault(); e.e.stopPropagation(); return; } if (e.e.shiftKey) { shell.scrollLeft += e.e.deltaY; e.e.preventDefault(); } });
document.addEventListener('keydown', event => {
  if (currentMode === 'read') return;
  if (document.activeElement && ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
  // The native-text-editor overlay (a contenteditable DIV, not an INPUT/TEXTAREA — the check
  // above can't catch it) already routes Delete/Backspace to native text editing on its own; this
  // must not also try to delete the underlying Fabric object out from under it.
  if (nativeTextEditor) {
    if (nativeTextEditor.element.contains(document.activeElement)) { event.stopPropagation(); }
    return;
  }
  const active = canvas.getActiveObject();
  if (!active) return;
  const isText = ['i-text', 'textbox'].includes(active.type);
  if ((event.key === 'Delete' || event.key === 'Backspace') && !isText) { event.preventDefault(); canvas.getActiveObjects().forEach(object => { const pairedId = object.shapeTextId || object.shapeTextFor; const paired = pairedId && canvas.getObjects().find(candidate => candidate.objectId === pairedId || candidate.objectId === object.shapeTextFor); canvas.remove(object); if (paired && paired !== object) canvas.remove(paired); }); canvas.discardActiveObject(); canvas.requestRenderAll(); }
}, true);
/* Canvas Undo/Redo shortcuts. Deliberately its own listener rather than
   folded into the block above: that one only runs once an object is
   selected (it returns early on `if (!active) return;`), but Undo/Redo
   must work from a plain, nothing-selected canvas too. Any focus inside a
   form field or the native text-edit overlay is left to native browser
   undo — see the isTypingContext check — so in-progress text edits are
   never accidentally swallowed into a canvas-level Undo. */
function isTypingContext(target) {
  return !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
}
document.addEventListener('keydown', event => {
  if (currentMode === 'read') return;
  const key = event.key.toLowerCase();
  const isUndo = (event.ctrlKey || event.metaKey) && !event.shiftKey && key === 'z';
  const isRedo = (event.ctrlKey || event.metaKey) && (key === 'y' || (event.shiftKey && key === 'z'));
  if (!isUndo && !isRedo) return;
  if (nativeTextEditor || isTypingContext(document.activeElement)) return;
  event.preventDefault();
  if (isUndo) performUndo(); else performRedo();
}, true);
document.addEventListener('keydown', event => {
  if (event.key !== 'PageUp' && event.key !== 'PageDown') return;
  if (nativeTextEditor || isTypingContext(document.activeElement)) return;
  event.preventDefault();
  goToAdjacentPage(event.key === 'PageUp' ? -1 : 1);
});
/* Copy/Paste for canvas objects — Ctrl+C/Ctrl+V. Uses an in-memory clipboard (not the OS
   clipboard, which the existing 'paste' listener above already owns for image files) so the
   two features can never collide. Clones via fabric's own object.clone() — for images this
   reuses the already-loaded element instantly instead of re-fetching the asset URL (which
   was silently slow/unreliable and made image paste look broken); every other object type
   clones just as fast. A pasted object is added via canvas.add(), which already triggers the
   existing object:added -> markDirty()/snapshot() handling, so persistence and undo "just work". */
let clipboard = [], pasteOffset = 0;
function copySelection() {
  const selected = canvas.getActiveObjects();
  if (!selected.length) return;
  const set = new Set(selected);
  selected.forEach(o => {
    if (o.shapeTextId) { const label = canvas.getObjects().find(c => c.objectId === o.shapeTextId); if (label) set.add(label); }
    if (o.shapeTextFor) { const shape = canvas.getObjects().find(c => c.objectId === o.shapeTextFor); if (shape) set.add(shape); }
  });
  clipboard = [...set];
  pasteOffset = 0;
}
function cloneObject(o) { return new Promise(resolve => o.clone(resolve, NOTES_PROPS)); }
// Center (in canvas/object coordinates) of whatever portion of the canvas is actually scrolled
// into view right now — the fallback paste anchor when the pointer isn't over the canvas.
function visibleCanvasCenter() {
  const shell = document.getElementById('canvasShell');
  const vpt = canvas.viewportTransform, zoom = canvas.getZoom();
  return { x: (shell.scrollLeft + shell.clientWidth / 2 - vpt[4]) / zoom, y: (shell.scrollTop + shell.clientHeight / 2 - vpt[5]) / zoom };
}
async function pasteClipboard() {
  if (currentMode === 'read' || !clipboard.length) return;
  pasteOffset += 20;
  // Paste position: near the pointer if it's actually over the canvas right now, otherwise the
  // center of whatever's currently scrolled into view — either way the paste lands somewhere the
  // user can already see, instead of always offsetting a fixed 20px from the ORIGINAL object
  // (which could be far outside the current viewport, or even off the edge of the page, forcing
  // an unwanted scroll/jump to find the pasted copy).
  const anchor = (pointerOverCanvas && lastPointerCanvas) ? lastPointerCanvas : visibleCanvasCenter();
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  clipboard.forEach(o => { const r = o.getBoundingRect(true, true); minX = Math.min(minX, r.left); minY = Math.min(minY, r.top); maxX = Math.max(maxX, r.left + r.width); maxY = Math.max(maxY, r.top + r.height); });
  // Preserves the copied objects' positions RELATIVE to each other (so a multi-object copy still
  // pastes as one cohesive group) while moving the whole group's center to the paste anchor;
  // pasteOffset keeps repeated Ctrl+V presses (pointer not moving in between) from stacking every
  // copy exactly on top of the last one, same as the previous fixed-diagonal behavior did.
  const dx = anchor.x - (minX + maxX) / 2 + pasteOffset, dy = anchor.y - (minY + maxY) / 2 + pasteOffset;
  const idMap = new Map(clipboard.map(o => [o.objectId, uid()]));
  const clones = await Promise.all(clipboard.map(cloneObject));
  clones.forEach((clone, i) => {
    const original = clipboard[i];
    clone.set({ objectId: idMap.get(original.objectId), left: (original.left || 0) + dx, top: (original.top || 0) + dy });
    if (clone.shapeTextId && idMap.has(clone.shapeTextId)) clone.set('shapeTextId', idMap.get(clone.shapeTextId));
    if (clone.shapeTextFor && idMap.has(clone.shapeTextFor)) clone.set('shapeTextFor', idMap.get(clone.shapeTextFor));
  });
  clones.forEach(clone => { finalizeLoadedObject(clone); canvas.add(clone); });
  canvas.discardActiveObject();
  // A single pasted object is selected immediately. For multiple, deliberately not
  // auto-wrapped in a fabric.ActiveSelection: constructing one from objects already
  // individually canvas.add()'d corrupts their absolute left/top (confirmed via testing —
  // a real Fabric.js quirk, not something to build around here). Positions/pairing are
  // already correct post-paste; the user can still drag-select them together manually.
  if (clones.length === 1) canvas.setActiveObject(clones[0]);
  canvas.requestRenderAll();
}
document.addEventListener('keydown', event => {
  if (currentMode === 'read') return;
  const key = event.key.toLowerCase();
  const isCopy = (event.ctrlKey || event.metaKey) && key === 'c';
  const isPaste = (event.ctrlKey || event.metaKey) && key === 'v';
  if (!isCopy && !isPaste) return;
  if (nativeTextEditor || isTypingContext(document.activeElement)) return;
  event.preventDefault();
  if (isCopy) copySelection(); else pasteClipboard();
}, true);
if (!isPublicView) {
  const manualSaveButton = document.createElement('button'); manualSaveButton.type = 'button'; manualSaveButton.id = 'manualSaveBtn'; manualSaveButton.title = 'Save changes'; manualSaveButton.innerHTML = '<i class="fas fa-save"></i>'; manualSaveButton.addEventListener('click', () => { if (currentMode === 'read') return; save(); }); document.querySelector('[data-group="other"]').append(manualSaveButton);
  const autoSaveLabel = document.createElement('label'); autoSaveLabel.className = 'notes-auto-save'; autoSaveLabel.innerHTML = 'Auto Save <input type="checkbox" aria-label="Auto Save"><span></span>'; const autoSaveToggle = autoSaveLabel.querySelector('input'); autoSaveToggle.checked = autoSaveEnabled; autoSaveToggle.addEventListener('change', () => { if (currentMode === 'read') { autoSaveToggle.checked = autoSaveEnabled; return; } autoSaveEnabled = autoSaveToggle.checked; if (autoSaveEnabled && dirty) { clearTimeout(saveTimer); saveTimer = setTimeout(save, 1400); } }); document.querySelector('[data-group="other"]').append(autoSaveLabel);
}
const paletteColors = ['#000000', '#434343', '#666666', '#999999', '#b7b7b7', '#d9d9d9', '#efefef', '#ffffff', '#980000', '#ff0000', '#ff9900', '#ffff00', '#00ff00', '#00ffff', '#4a86e8', '#0000ff', '#9900ff', '#ff00ff', '#e06666', '#f6b26b', '#93c47d', '#76a5af'];
const palette = document.createElement('div'); palette.className = 'notes-color-palette'; palette.title = 'Text color presets'; palette.style.cssText = 'display:flex;flex-wrap:wrap;gap:3px;max-width:150px;padding:2px 4px;align-items:center';
paletteColors.forEach(hex => { const swatch = document.createElement('button'); swatch.type = 'button'; swatch.title = hex; swatch.setAttribute('aria-label', `Text color ${hex}`); swatch.style.cssText = `width:15px;height:15px;border-radius:3px;border:1px solid var(--border);background:${hex};padding:0;cursor:pointer`; swatch.addEventListener('click', () => applyTextColor(hex)); palette.appendChild(swatch); });
document.querySelector('[data-group="color"]').append(palette);
document.querySelectorAll('[data-tool]').forEach(b => b.addEventListener('click', async () => { if (currentMode === 'read') return; const t = b.dataset.tool; setTool(t); if (t === 'text') { activeTool='text'; } if (t === 'sticky') await createEditableText({ left: 180, top: 150, fontSize: window.__notesDefaultSize || 18, fill: token('--warning-text'), backgroundColor: window.__notesStickyBgColor || token('--warning-bg'), padding: 14, themeSticky: true }, 'sticky_note'); if (t === 'image') document.getElementById('imageInput').click(); }));
document.getElementById('deleteObjectBtn').addEventListener('click', () => { if (currentMode === 'read') return; const selected = canvas.getActiveObjects(); if (!selected.length) return; selected.forEach(object => { const pairedId = object.shapeTextId || object.shapeTextFor; const paired = pairedId && canvas.getObjects().find(candidate => candidate.objectId === pairedId || candidate.objectId === object.shapeTextFor); canvas.remove(object); if (paired && paired !== object) canvas.remove(paired); }); canvas.discardActiveObject(); canvas.requestRenderAll(); });
async function uploadImageFile(file) {
  const data = new FormData(); data.append('image', file);
  status('Uploading image…', 'saving');
  try {
    const response = await fetch(`${apiBase}/${notebookId}/assets`, { method: 'POST', credentials: 'same-origin', body: data });
    const out = await response.json();
    if (!response.ok || !out.success) throw new Error(out.message || 'Upload failed.');
    fabric.Image.fromURL(out.url, image => { image.assetId = out.asset.id; image.scaleToWidth(Math.min(320, image.width)); addObject(image, 'image'); });
  } catch (error) { toast(error.message, 'error'); status('Saved'); }
}
document.getElementById('imageInput').addEventListener('change', async e => { if (currentMode === 'read') { e.target.value = ''; return; } for (const file of [...e.target.files]) await uploadImageFile(file); e.target.value = ''; });
document.addEventListener('paste', event => {
  if (currentMode === 'read' || isPublicView) return;
  // Guards against hijacking a paste meant for a real text field elsewhere on the page (e.g. the
  // notebook title input). The native-text-editor overlay is a DIV, not an INPUT/TEXTAREA, so
  // pasting an image while editing Text Box/Sticky Note content still reaches here — matching
  // known-good behavior; a clipboard that happens to carry both text and an image while editing
  // is an edge case, not a regression.
  const active = document.activeElement;
  if (active && ['INPUT', 'TEXTAREA'].includes(active.tagName)) return;
  const images = [...(event.clipboardData?.items || [])].filter(item => item.type.startsWith('image/'));
  if (!images.length) return;
  event.preventDefault();
  images.forEach(item => { const file = item.getAsFile(); if (file) uploadImageFile(file); });
});
function performUndo() {
  if (currentMode === 'read' || historyIndex <= 0) return;
  restoreHistory(historyIndex - 1);
  flashHistoryButton(document.getElementById('undoBtn'));
}
function performRedo() {
  if (currentMode === 'read' || historyIndex >= history.length - 1) return;
  restoreHistory(historyIndex + 1);
  flashHistoryButton(document.getElementById('redoBtn'));
}
document.getElementById('undoBtn').addEventListener('click', performUndo);
document.getElementById('redoBtn').addEventListener('click', performRedo);
function restoreHistory(index) {
  if (currentMode === 'read' || index < 0 || index >= history.length || index === historyIndex) return;
  finishNativeTextEdit();
  canvas.discardActiveObject();
  loading = true;
  const raw = (JSON.parse(history[index]).objects) || [];
  const finish = objects => {
    canvas.clear();
    updateObjectCount();
    objects.forEach(o => { finalizeLoadedObject(o); canvas.add(o); });
    applyFixedPageBounds();
    canvas.requestRenderAll();
    loading = false;
    historyIndex = index;
    dirty = true;
    clearTimeout(saveTimer);
    if (autoSaveEnabled) { status('Saving…', 'saving'); saveTimer = setTimeout(save, 1400); } else status('Unsaved changes', 'saving');
    updateUndoRedoUI();
  };
  const result = fabric.util.enlivenObjects(raw, finish);
  if (result && typeof result.then === 'function') result.then(finish);
}
function changeZoom(delta) { const z = Math.min(3, Math.max(.25, canvas.getZoom() + delta)); canvas.setZoom(z); document.getElementById('zoomValue').textContent = `${Math.round(z * 100)}%`; repositionNativeTextEditor(); } document.getElementById('zoomInBtn').addEventListener('click', () => changeZoom(.1)); document.getElementById('zoomOutBtn').addEventListener('click', () => changeZoom(-.1));
document.getElementById('notebookTitle').addEventListener('change', async e => { if (currentMode === 'read') return; try { await api(`${apiBase}/${notebookId}`, { method: 'PATCH', body: JSON.stringify({ title: e.target.value }) }); toast('Notebook renamed.'); } catch (error) { toast(error.message, 'error'); } });
/* Instant page creation — no title prompt. A blank POST body makes the server assign the
   next "Untitled" name (see notes_service.create_page); the student renames it later via
   double-click or the page-actions menu, both already inline (startInlineRename). */
let creatingPage = false;
async function createPageInstant() {
  if (currentMode === 'read' || creatingPage) return;
  const btn = document.getElementById('newPageBtn');
  creatingPage = true; if (btn) btn.disabled = true;
  try {
    const result = await api(`${apiBase}/${notebookId}/pages`, { method: 'POST', body: JSON.stringify({}) });
    pageCache = [...pageCache, result.page];
    renderPages(pageCache, result.page.id, false);
    await openBlankPage(result.page.id);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    creatingPage = false; if (btn) btn.disabled = false;
  }
}
document.getElementById('newPageBtn').addEventListener('click', createPageInstant);
document.getElementById('collapsedNewPageBtn')?.addEventListener('click', createPageInstant);
let deletePageSaving = false;
function setDeletePageSaving(isSaving) {
  const button = document.getElementById('confirmDeletePage');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Deleting...' : '<i class="fas fa-trash-alt"></i> Delete page';
}
document.querySelectorAll('[data-close-delete-page]').forEach(button => button.addEventListener('click', () => { if (!deletePageSaving) closeModal('deletePageModal'); }));
document.getElementById('confirmDeletePage').addEventListener('click', async () => { if (currentMode === 'read' || deletePageSaving) return; const target = root.dataset.pageTarget; const remaining = pageCache.filter(page => page.id !== target); document.getElementById('deletePageMessage').textContent = ''; deletePageSaving = true; setDeletePageSaving(true); try { await api(`${apiBase}/${notebookId}/pages/${target}`, { method: 'DELETE' }); pageObjectsCache.delete(target); closeModal('deletePageModal'); renderPages(remaining, activePageId === target ? remaining[0]?.id || null : activePageId, false); if (activePageId === target) { if (remaining[0]) await loadPage(remaining[0].id); else showEmptyEditor(); } toast('Page deleted.'); } catch (error) { document.getElementById('deletePageMessage').textContent = error.message; } finally { deletePageSaving = false; setDeletePageSaving(false); } });
// Clear Page — removes every object on the CURRENT page only, through the same canvas.remove()
// path a normal single-object delete already uses, so the existing object:removed listener
// (deletedIds tracking + markDirty()) persists the clear via the normal autosave flow with no
// separate save/delete code needed here.
document.getElementById('clearPageBtn')?.addEventListener('click', () => { if (currentMode === 'read' || !activePageId || !canvas.getObjects().length) return; document.getElementById('clearPageModal').classList.add('open'); });
document.querySelectorAll('[data-close-clear-page]').forEach(button => button.addEventListener('click', () => closeModal('clearPageModal')));
document.getElementById('confirmClearPage').addEventListener('click', () => { if (currentMode === 'read' || !activePageId) return; canvas.discardActiveObject(); canvas.remove(...canvas.getObjects()); canvas.requestRenderAll(); closeModal('clearPageModal'); toast('Page cleared.'); });
document.addEventListener('click', closePageMenus); document.addEventListener('click', event => { const link = event.target.closest('a[href]'); if (!link || event.defaultPrevented || link.target || link.href === location.href) return; event.preventDefault(); showUnsavedChangesDialog(() => { location.href = link.href; }); }, true); document.addEventListener('keydown', event => { if (event.key === 'Escape') closePageMenus(); }); window.addEventListener('scroll', closePageMenus, true); new MutationObserver(applyCanvasTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] }); window.addEventListener('resize', () => { closePageMenus(); resize(); }); window.addEventListener('beforeunload', event => { if (!dirty) return; event.preventDefault(); event.returnValue = ''; });
if (isPublicView) initPublicReadOnlyView();
applyToolbarVisibility(); updateReadModeUI(); updateFullscreenUI(); updateToolbarToggleUI();
resize(); fontMetricsPromise.then(refreshPages).catch(error => { toast(error.message, 'error'); showEmptyEditor(); });