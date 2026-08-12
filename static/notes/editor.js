const root = document.getElementById('noteEditor');
const notebookId = root.dataset.notebookId;
const isPublicView = root.dataset.readOnly === 'true';
const apiBase = root.dataset.apiBase || '/api/notes/notebooks';
const canvas = new fabric.Canvas('notesCanvas', { selection: true, preserveObjectStacking: true });
window.__notesCanvas = canvas;
canvas.wrapperEl.style.setProperty('position', 'relative', 'important'); canvas.wrapperEl.style.setProperty('inset', 'auto', 'important');
let activePageId = document.querySelector('.page-item.active')?.dataset.pageId;
let pageCache = [];
let dirty = false, loading = false, saving = false, saveTimer, pan = false, lastPan, deletedIds = [], history = [], historyIndex = -1, activeTool = 'select', autoSaveEnabled = false;
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
const NOTES_PROPS = ['objectId', 'objectType', 'themeText', 'themeSticky', 'assetId', 'shapeTextId', 'shapeTextFor', 'minHeight', 'customColor', 'customBg', 'strokeOnly'];
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
function textSelectionOffsets(element) { const selection = window.getSelection(); if (!selection?.rangeCount || !element.contains(selection.anchorNode)) return { start: 0, end: 0 }; const offsetAt = (node, offset) => { const range = document.createRange(); range.selectNodeContents(element); range.setEnd(node, offset); return range.toString().length; }; const start = offsetAt(selection.anchorNode, selection.anchorOffset); const end = offsetAt(selection.focusNode, selection.focusOffset); return { start: Math.min(start, end), end: Math.max(start, end) }; }
function placeCaretAtPoint(element, clientX, clientY) { let range = null; if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(clientX, clientY); else if (document.caretPositionFromPoint) { const pos = document.caretPositionFromPoint(clientX, clientY); if (pos && pos.offsetNode) { range = document.createRange(); range.setStart(pos.offsetNode, pos.offset); range.collapse(true); } } if (!range || !element.contains(range.startContainer)) return false; const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); return true; }
const BULLET_RE = /^([•◦▪–✔]\s|\d+\.\s)/;
function setCaretOffset(element, offset) { const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT); let count = 0, node = walker.nextNode(); const range = document.createRange(); while (node) { if (count + node.length >= offset) { range.setStart(node, offset - count); range.collapse(true); break; } count += node.length; node = walker.nextNode(); } if (!node) { range.selectNodeContents(element); range.collapse(false); } const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }
function selectOffsetRange(element, start, end) { const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT); let count = 0, node = walker.nextNode(), started = false; const range = document.createRange(); while (node) { const len = node.length; if (!started && count + len >= start) { range.setStart(node, start - count); started = true; } if (count + len >= end) { range.setEnd(node, end - count); break; } count += len; node = walker.nextNode(); } if (!started) range.setStart(element, 0); if (!node) range.setEnd(element, element.childNodes.length); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }
/* Bullet/numbered-list continuation: Enter on a bulleted line repeats the bullet (auto-
   incrementing "N. " numbers); Backspace right after a bullet on an otherwise-empty line
   removes just the bullet instead of merging into the previous line. Plain-text architecture
   (see toggleLinePrefix in drawing-tools.js), so this operates on line text, not a list model. */
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
function finishNativeTextEdit() { const edit = nativeTextEditor; if (!edit) return; const { object, element } = edit; const offsets = textSelectionOffsets(element); object.selectionStart = offsets.start; object.selectionEnd = offsets.end; object.set('text', element.innerText.replace(/\n$/, '')); object.opacity = 1; object.dirty = true; object._clearCache && object._clearCache(); object.initDimensions(); clampTextHeight(object); object.setCoords(); element.remove(); nativeTextEditor = null; canvas.requestRenderAll(); markDirty(); }
function repositionNativeTextEditor() { if (!nativeTextEditor) return; const { object, element } = nativeTextEditor; const bounds = object.getBoundingRect(false, true); const zoom = canvas.getZoom(); element.style.left = `${bounds.left}px`; element.style.top = `${bounds.top}px`; element.style.fontSize = `${(object.fontSize || 18) * zoom}px`; }
function beginNativeTextEdit(object, point) { if (currentMode === 'read') return; if (!object || !['i-text', 'textbox'].includes(object.type)) return; if (nativeTextEditor?.object === object) { nativeTextEditor.element.focus(); if (point) placeCaretAtPoint(nativeTextEditor.element, point.x, point.y); return; } finishNativeTextEdit(); const bounds = object.getBoundingRect(false, true); const wrapper = canvas.wrapperEl; const zoom = canvas.getZoom(); const element = document.createElement('div'); element.className = 'native-text-editor'; element.contentEditable = 'true'; element.spellcheck = true; element.textContent = object.text || '';
  /* ROOT CAUSE of Ctrl+A / Shift+Arrow / drag-select "selecting the whole canvas" bug:
     Fabric.js sets canvas.wrapperEl.onselectstart = falseFunction to stop native drag-select
     while manipulating canvas objects. selectstart bubbles, so it silently killed selection
     in this overlay too (independent of any CSS user-select value). Stop it right here. */
  element.addEventListener('selectstart', e => e.stopPropagation()); element.style.left = `${bounds.left}px`; element.style.top = `${bounds.top}px`; element.style.width = `${Math.max(36, bounds.width - 4)}px`; element.style.minHeight = `${Math.max(28, bounds.height)}px`; element.style.fontFamily = object.fontFamily || 'DM Sans'; element.style.fontSize = `${(object.fontSize || 18) * zoom}px`; element.style.fontWeight = object.fontWeight || 'normal'; element.style.fontStyle = object.fontStyle || 'normal'; element.style.textDecoration = object.underline ? 'underline' : 'none'; element.style.lineHeight = object.lineHeight || 1.2; element.style.textAlign = object.textAlign || 'left'; element.style.color = object.fill || token('--text-1'); element.style.background = object.backgroundColor || (object.themeSticky ? token('--warning-bg') : 'transparent'); element.style.padding = `${object.padding || 0}px`; object.opacity = 0; canvas.setActiveObject(object); canvas.requestRenderAll(); wrapper.append(element); const sync = () => { object.set('text', element.innerText.replace(/\n$/, '')); const offsets = textSelectionOffsets(element); object.selectionStart = offsets.start; object.selectionEnd = offsets.end; element.style.height = 'auto'; const contentHeight = Math.max(28, element.scrollHeight); element.style.height = `${contentHeight}px`; const growZoom = canvas.getZoom() || 1; const nextHeight = Math.max(object.minHeight || 0, contentHeight / growZoom); if (Math.abs((object.height || 0) - nextHeight) > 0.5) { object.set('height', nextHeight); object.dirty = true; object.setCoords(); canvas.requestRenderAll(); } }; nativeTextEditor = { object, element, sync }; element.addEventListener('keydown', event => handleListContinuation(event, element, sync)); element.addEventListener('input', sync); element.addEventListener('keyup', sync); element.addEventListener('mouseup', sync); element.addEventListener('blur', () => setTimeout(() => { if (nativeTextEditor?.element === element && !element.contains(document.activeElement)) finishNativeTextEdit(); }, 0)); element.focus(); if (!point || !placeCaretAtPoint(element, point.x, point.y)) { const range = document.createRange(); range.selectNodeContents(element); range.collapse(false); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); } sync(); requestAnimationFrame(() => { if (nativeTextEditor?.element !== element) return; if (document.activeElement !== element) element.focus(); if (point) placeCaretAtPoint(element, point.x, point.y); }); }
function configureNormalText(text) { text.set({ width: Math.max(220, text.width || 0), editable: false, lockUniScaling: false, lockScalingFlip: true, borderColor: token('--accent'), cornerColor: token('--accent'), transparentCorners: false }); text.setControlsVisibility({ tl: true, tr: true, bl: true, br: true, mt: true, mb: true, ml: true, mr: true }); }
function configureStickyNote(text) { text.set({ width: Math.max(220, text.width || 0), editable: false, lockUniScaling: false, lockScalingFlip: true, borderColor: token('--accent'), cornerColor: token('--accent'), transparentCorners: false }); text.setControlsVisibility({ tl: true, tr: true, bl: true, br: true, mt: true, mb: true, ml: true, mr: true }); }
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

  if (next === 'read') {
    finishNativeTextEdit();
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

document.getElementById('readModeBtn')?.addEventListener('click', () => setMode(currentMode === 'edit' ? 'read' : 'edit'));
document.getElementById('fullscreenBtn')?.addEventListener('click', toggleFullscreen);
document.getElementById('toolbarToggleBtn')?.addEventListener('click', () => { if (currentMode === 'read') return; setToolbarVisible(!toolbarVisible); });

/* Export whole notebook as PDF — renders each page to a PNG (tightly cropped to its actual
   content) on a private off-screen StaticCanvas, never touching the live canvas/objects, then
   posts the images to the server to be assembled into one multi-page PDF by the existing
   ReportLab-based pdf_service (build_notebook_pdf) — reusing that service rather than
   re-implementing PDF generation, while getting pixel-accurate layout fidelity for free since
   Fabric itself does the rendering. Every page is re-serialized via toObject(NOTES_PROPS) +
   enlivenObjects (the same round-trip already used by save/undo), so this can't corrupt or
   leak live object instances into another canvas. */
async function getPageObjectsForExport(pageId) {
  if (pageId === activePageId) return canvas.getObjects().map(o => o.toObject(NOTES_PROPS));
  const cached = pageObjectsCache.get(pageId);
  if (cached) return cached.objects.map(o => o.toObject(NOTES_PROPS));
  const data = await api(`${apiBase}/${notebookId}/pages/${pageId}/objects`);
  return data.objects.map(buildRawFabricEntry);
}
async function exportNotebookPdf() {
  if (!pageCache.length) { toast('No pages to export.', 'error'); return; }
  const btn = document.getElementById('exportPdfBtn');
  const originalIcon = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
  const offEl = document.createElement('canvas');
  const offCanvas = new fabric.StaticCanvas(offEl, { renderOnAddRemove: false });
  try {
    const rendered = [];
    for (const page of pageCache) {
      const raw = await getPageObjectsForExport(page.id);
      const objects = await new Promise((resolve, reject) => {
        const result = fabric.util.enlivenObjects(raw, resolve);
        if (result && typeof result.then === 'function') result.then(resolve).catch(reject);
      });
      let bounds = { left: 0, top: 0, width: 900, height: 650 };
      if (objects.length) {
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        objects.forEach(o => { const r = o.getBoundingRect(true, true); minX = Math.min(minX, r.left); minY = Math.min(minY, r.top); maxX = Math.max(maxX, r.left + r.width); maxY = Math.max(maxY, r.top + r.height); });
        const pad = 40;
        bounds = { left: minX - pad, top: minY - pad, width: Math.max(200, (maxX - minX) + pad * 2), height: Math.max(150, (maxY - minY) + pad * 2) };
      }
      offCanvas.clear();
      offCanvas.setDimensions({ width: bounds.width, height: bounds.height });
      objects.forEach(o => { o.set({ left: (o.left || 0) - bounds.left, top: (o.top || 0) - bounds.top }); o.setCoords(); offCanvas.add(o); });
      offCanvas.renderAll();
      const image = offCanvas.toDataURL({ format: 'png', multiplier: 2 });
      rendered.push({ title: page.title, image });
    }
    // Submitted as a real HTML form POST (into a hidden iframe) instead of fetch+Blob+
    // createObjectURL — that path made Chrome open the PDF through a blob: URL, whose
    // download could fail with "network error". A native form submission lets the browser
    // handle the response as a direct file download itself, driven by the server's
    // Content-Disposition header, with no blob: URL involved at any point.
    let frame = document.getElementById('notesPdfExportFrame');
    if (!frame) { frame = document.createElement('iframe'); frame.name = frame.id = 'notesPdfExportFrame'; frame.style.display = 'none'; document.body.appendChild(frame); }
    const form = document.createElement('form');
    form.method = 'POST'; form.action = `${apiBase}/${notebookId}/export-pdf`; form.target = 'notesPdfExportFrame'; form.style.display = 'none';
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = 'pages'; input.value = JSON.stringify(rendered);
    form.appendChild(input);
    // A successful download (Content-Disposition: attachment) never navigates the iframe, so
    // no 'load' fires for it; an error response (JSON) does navigate it, and — being
    // same-origin — its body is readable here, letting a real failure surface instead of
    // always claiming success.
    let settled = false;
    const finish = (ok, message) => { if (settled) return; settled = true; toast(ok ? 'Notebook exported as PDF.' : (message || 'Unable to export this notebook.'), ok ? 'success' : 'error'); };
    frame.onload = () => {
      let text = ''; try { text = frame.contentDocument?.body?.innerText || ''; } catch (e) {}
      if (text.trim()) { let message; try { message = JSON.parse(text).message; } catch (e) {} finish(false, message); }
    };
    document.body.append(form); form.submit(); form.remove();
    setTimeout(() => finish(true), 1200);
  } catch (error) {
    toast(error.message || 'Unable to export this notebook.', 'error');
  } finally {
    offCanvas.dispose();
    if (btn) { btn.disabled = false; btn.innerHTML = originalIcon; }
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
  if (o.type === 'i-text' || o.type === 'textbox') o.editable = false;
  if (o.objectType === 'rich_text') configureNormalText(o);
  if (o.objectType === 'sticky_note') configureStickyNote(o);
  clampTextHeight(o);
}
function setTool(name) { activeTool=name; window.__notesEraserActive = false; document.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === name)); canvas.isDrawingMode = name === 'draw' && currentMode === 'edit'; canvas.selection = name === 'select' && currentMode === 'edit'; const cursor = name === 'select' ? 'default' : name === 'draw' ? 'crosshair' : 'text'; canvas.defaultCursor = cursor; canvas.hoverCursor = cursor; canvas.freeDrawingCursor = cursor; }
function addObject(object, type) { if (currentMode === 'read') return; object.objectId = object.objectId || uid(); object.objectType = type; canvas.add(object).setActiveObject(object); canvas.requestRenderAll(); markDirty(); }
function objectRecord(object) { return { id: object.objectId || (object.objectId = uid()), object_type: object.objectType || 'shape', asset_id: object.assetId || null, transform: { left: object.left || 0, top: object.top || 0, scaleX: object.scaleX || 1, scaleY: object.scaleY || 1, angle: object.angle || 0 }, payload: { fabric: object.toObject(NOTES_PROPS) } }; }

async function save() { if (isPublicView) return; if (!dirty || saving || !activePageId) return; saving = true; status('Saving…', 'saving'); try { const objects = canvas.getObjects().map(objectRecord); await api(`${apiBase}/${notebookId}/pages/${activePageId}/objects`, { method: 'PUT', body: JSON.stringify({ objects, deleted_ids: deletedIds }) }); dirty = false; deletedIds = []; status('Saved'); } catch (error) { status('Save failed', 'failed'); toast(error.message, 'error'); } finally { saving = false; } }
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
    deletedIds = [];
    cached.objects.forEach(o => canvas.add(o));
    canvas.requestRenderAll();
    history = []; historyIndex = -1; loading = false; snapshot(); dirty = false; status('Saved');
    scheduleAdjacentPreload(id);
    return;
  }

  loading = true;
  document.getElementById('canvasLoading').hidden = false;
  canvas.clear();
  deletedIds = [];
  try {
    const data = await api(`${apiBase}/${notebookId}/pages/${id}/objects`);
    const raw = data.objects.map(buildRawFabricEntry);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Loading this page took too long. Some saved content may be corrupted.')), 10000);
      const finish = objects => {
        clearTimeout(timer);
        objects.forEach(o => { finalizeLoadedObject(o); canvas.add(o); });
        canvas.requestRenderAll();
        pageObjectsCache.set(id, { objects, cachedAt: Date.now() });
        resolve();
      };
      const result = fabric.util.enlivenObjects(raw, finish);
      if (result && typeof result.then === 'function') result.then(finish).catch(reject);
    });
    history = []; historyIndex = -1; loading = false; snapshot(); dirty = false; status('Saved');
    scheduleAdjacentPreload(id);
  } catch (error) {
    toast(error.message, 'error'); status('Load failed', 'failed');
  } finally {
    loading = false; document.getElementById('canvasLoading').hidden = true;
  }
}
async function openBlankPage(id) { await save(); if (activePageId && activePageId !== id) { pageObjectsCache.set(activePageId, { objects: canvas.getObjects().slice(), cachedAt: Date.now() }); } activePageId = id; hideEmptyEditor(); loading = true; document.querySelectorAll('.page-item').forEach(p => p.classList.toggle('active', p.dataset.pageId === id)); canvas.clear(); deletedIds = []; history = []; historyIndex = -1; dirty = false; loading = false; document.getElementById('canvasLoading').hidden = true; canvas.requestRenderAll(); status('Saved'); updateUndoRedoUI(); pageObjectsCache.set(id, { objects: [], cachedAt: Date.now() }); }
function showEmptyEditor() { activePageId = null; dirty = false; canvas.clear(); dirty = false; history = []; historyIndex = -1; document.getElementById('canvasLoading').hidden = true; const shell = document.getElementById('canvasShell'); let empty = document.getElementById('emptyPageEditor'); if (!empty) { empty = document.createElement('div'); empty.id = 'emptyPageEditor'; empty.className = 'canvas-empty-state'; empty.innerHTML = '<i class="far fa-file-alt"></i><strong>No pages yet</strong><span>Create a page to begin taking notes.</span>'; shell.append(empty); } empty.hidden = false; status('Saved'); updateUndoRedoUI(); }
function hideEmptyEditor() { document.getElementById('emptyPageEditor')?.setAttribute('hidden', ''); }
function resize() { const shell = document.getElementById('canvasShell'); canvas.setDimensions({ width: Math.max(2400, canvas.getWidth(), shell.clientWidth), height: Math.max(1600, canvas.getHeight(), shell.clientHeight) }); canvas.calcOffset(); canvas.requestRenderAll(); }
function growWorkspaceFor(object) { if (!object || loading) return; const right = (object.left || 0) + ((object.getScaledWidth?.() || object.width || 0) / (object.originX === 'center' ? 2 : 1)) + 480; const bottom = (object.top || 0) + ((object.getScaledHeight?.() || object.height || 0) / (object.originY === 'center' ? 2 : 1)) + 360; const width = Math.max(canvas.getWidth(), right, 2400); const height = Math.max(canvas.getHeight(), bottom, 1600); if (width !== canvas.getWidth() || height !== canvas.getHeight()) { canvas.setDimensions({ width, height }); canvas.calcOffset(); canvas.requestRenderAll(); } }
let pageMenuPortal = null;
function closePageMenus() { pageMenuPortal?.remove(); pageMenuPortal = null; }
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
    action('Rename', 'fas fa-pen', () => { closePageMenus(); startInlineRename(page, titleElement); }),
    action('Delete', 'fas fa-trash-alt', () => { closePageMenus(); root.dataset.pageTarget = page.id; document.getElementById('deletePageMessage').textContent = ''; document.getElementById('deletePageModal').classList.add('open'); }, true)
  );
  menu.dataset.triggerPageId = page.id; menu.addEventListener('click', event => event.stopPropagation()); document.body.append(menu); pageMenuPortal = menu;
  const rect = trigger.getBoundingClientRect(); const margin = 8; const menuWidth = menu.offsetWidth; const menuHeight = menu.offsetHeight;
  const left = Math.max(margin, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - margin));
  const below = rect.bottom + 4; const top = below + menuHeight <= window.innerHeight - margin ? below : Math.max(margin, rect.top - menuHeight - 4);
  menu.style.left = `${left}px`; menu.style.top = `${top}px`;
}
function pageNameTaken(title, excludeId = '') { const normalized = title.trim().toLocaleLowerCase(); return [...document.querySelectorAll('.page-item')].some(item => item.dataset.pageId !== excludeId && item.querySelector('span')?.textContent.trim().toLocaleLowerCase() === normalized); }
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
    const next = input.value.trim();
    if (!save || next === original) { input.replaceWith(titleElement); return; }
    if (!next) { toast('Page name cannot be empty.', 'error'); input.focus(); complete = false; return; }
    if (pageNameTaken(next, page.id)) { toast('A page with this name already exists in this notebook.', 'error'); input.focus(); complete = false; return; }
    try {
      const result = await api(`${apiBase}/${notebookId}/pages/${page.id}`, { method: 'PATCH', body: JSON.stringify({ title: next }) });
      page.title = result.page.title; titleElement.textContent = page.title; input.replaceWith(titleElement);
    } catch (error) { toast(error.message, 'error'); input.focus(); complete = false; }
  };
  input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); finish(true); } if (event.key === 'Escape') { event.preventDefault(); finish(false); } });
  input.addEventListener('blur', () => finish(true));
}
function renderPages(pages, selectedId = null, shouldLoad = true) {
  pageCache = pages; const list = document.getElementById('pageList'); list.innerHTML = '';
  const selected = selectedId || activePageId || pages[0]?.id;
  pages.forEach(page => {
    const row = document.createElement('div'); row.style.cssText = 'position:relative;display:flex;align-items:center;margin-bottom:2px';
    const open = document.createElement('button'); open.type = 'button'; open.className = `page-item ${page.id === selected ? 'active' : ''}`; open.dataset.pageId = page.id;
    const title = document.createElement('span'); title.textContent = page.title; open.appendChild(title);
    open.addEventListener('click', () => { if (activePageId !== page.id) showUnsavedChangesDialog(() => loadPage(page.id)); });
    title.addEventListener('dblclick', event => { if (currentMode === 'read') return; event.preventDefault(); event.stopPropagation(); startInlineRename(page, title); });
    const dots = document.createElement('button'); dots.type = 'button'; dots.title = 'Page actions'; dots.className = 'page-dots-btn'; dots.innerHTML = '<i class="fas fa-ellipsis-h"></i>';
    dots.style.cssText = 'width:26px;height:26px;border:0;border-radius:6px;background:transparent;color:var(--text-3);cursor:pointer;position:absolute;right:4px';
    dots.addEventListener('click', event => { event.stopPropagation(); if (currentMode === 'read') return; const isSameMenu = pageMenuPortal?.dataset.triggerPageId === page.id; closePageMenus(); if (!isSameMenu) openPageMenu(page, title, dots); });
    row.append(open, dots); list.appendChild(row);
  });
  if (shouldLoad && selected && selected !== activePageId) loadPage(selected);
}
async function refreshPages() { const data = await api(`${apiBase}/${notebookId}/pages`); const next = data.pages.some(page => page.id === activePageId) ? activePageId : data.pages[0]?.id || null; renderPages(data.pages, next, false); if (next) await loadPage(next); else showEmptyEditor(); }
function openPageModal(mode, page = null) { if (currentMode === 'read') return; root.dataset.pageMode = mode; root.dataset.pageTarget = page?.id || ''; document.getElementById('pageModalTitle').lastChild.textContent = mode === 'new' ? ' New page' : ' Rename page'; document.getElementById('pageName').value = page?.title || ''; document.getElementById('pageMessage').textContent = ''; document.getElementById('pageModal').classList.add('open'); document.getElementById('pageName').focus(); }
function closeModal(id) { document.getElementById(id)?.classList.remove('open'); }
let pendingNavigation = null;
function showUnsavedChangesDialog(action) { if (!dirty) return action(); pendingNavigation = action; let dialog = document.getElementById('unsavedChangesModal'); if (!dialog) { dialog = document.createElement('div'); dialog.id = 'unsavedChangesModal'; dialog.className = 'modal-bd'; dialog.innerHTML = '<div class="modal-box notes-modal"><div class="modal-hdr"><h5><i class="fas fa-exclamation-circle" style="color:var(--warning-text)"></i> Unsaved Changes</h5></div><div class="modal-bdy"><p class="notes-confirm-text">You have unsaved changes. What would you like to do?</p></div><div class="modal-ftr"><button class="sbtn sbtn-ghost" data-unsaved-cancel>Cancel</button><button class="sbtn sbtn-danger" data-unsaved-discard>Discard Changes</button><button class="sbtn sbtn-primary" data-unsaved-save>Save &amp; Continue</button></div></div>'; document.body.append(dialog); dialog.querySelector('[data-unsaved-cancel]').addEventListener('click', () => { pendingNavigation = null; dialog.classList.remove('open'); }); dialog.querySelector('[data-unsaved-discard]').addEventListener('click', () => { dirty = false; deletedIds = []; const next = pendingNavigation; pendingNavigation = null; dialog.classList.remove('open'); next?.(); }); dialog.querySelector('[data-unsaved-save]').addEventListener('click', async () => { const next = pendingNavigation; await save(); if (dirty) return; pendingNavigation = null; dialog.classList.remove('open'); next?.(); }); } dialog.classList.add('open'); }

function clampTextHeight(object) { if (!['sticky_note', 'rich_text'].includes(object?.objectType)) return; if (object.minHeight && object.height < object.minHeight) object.set('height', object.minHeight); }
function preserveStickyTextSize(object) { if (!['sticky_note', 'rich_text'].includes(object?.objectType) || object.type !== 'textbox') return; const scaleX = Math.abs(object.scaleX || 1), scaleY = Math.abs(object.scaleY || 1); if (scaleX === 1 && scaleY === 1) return; const requestedWidth = Math.max(140, object.width * scaleX); const requestedHeight = Math.max(40, object.height * scaleY); object.set({ width: requestedWidth, scaleX: 1, scaleY: 1 }); object.initDimensions(); if (scaleY !== 1) object.minHeight = Math.max(requestedHeight, object.height); clampTextHeight(object); object.setCoords(); }
canvas.on('object:added', event => { growWorkspaceFor(event.target); markDirty(); }); canvas.on('object:scaling', event => preserveStickyTextSize(event.target)); canvas.on('object:modified', event => { preserveStickyTextSize(event.target); growWorkspaceFor(event.target); markDirty(); }); canvas.on('object:removed', e => { if (!loading && e.target?.objectId) deletedIds.push(e.target.objectId); markDirty(); }); canvas.on('path:created', e => { e.path.objectId = uid(); e.path.objectType = 'drawing'; }); canvas.on('mouse:dblclick', event => { if (currentMode === 'read') return; if (['i-text', 'textbox'].includes(event.target?.type)) { event.e.preventDefault(); beginNativeTextEdit(event.target, { x: event.e.clientX, y: event.e.clientY }); } });
canvas.on('mouse:down', async e => { if (e.e.altKey) { pan = true; lastPan = e.e; canvas.selection = false; return; } if (currentMode === 'read') return; if (e.target && ['i-text', 'textbox'].includes(e.target.type)) { e.e.preventDefault(); } if (!e.target && !window.__notesEraserActive && !window.__notesShapeToolActive && !canvas.isDrawingMode && activeTool !== 'image' && activeTool !== 'rect') { const p = canvas.getPointer(e.e); await createEditableText({ left: p.x, top: p.y, fontSize: window.__notesDefaultSize || 22, fill: token('--text-1'), themeText: true }, 'rich_text'); } }); canvas.on('mouse:move', e => { if (!pan) return; const v = canvas.viewportTransform; v[4] += e.e.clientX - lastPan.clientX; v[5] += e.e.clientY - lastPan.clientY; lastPan = e.e; canvas.requestRenderAll(); }); canvas.on('mouse:up', () => { pan = false; canvas.selection = currentMode === 'edit'; });
canvas.on('mouse:wheel', e => { const shell = document.getElementById('canvasShell'); if (e.e.ctrlKey) { let z = canvas.getZoom() * (0.999 ** e.e.deltaY); z = Math.min(3, Math.max(.25, z)); canvas.zoomToPoint({ x: e.e.offsetX, y: e.e.offsetY }, z); canvas.zoomToPoint({ x: e.e.offsetX, y: e.e.offsetY }, z); repositionNativeTextEditor(); document.getElementById('zoomValue').textContent = `${Math.round(z * 100)}%`; e.e.preventDefault(); e.e.stopPropagation(); return; } if (e.e.shiftKey) { shell.scrollLeft += e.e.deltaY; e.e.preventDefault(); } });
document.addEventListener('keydown', event => {
  if (currentMode === 'read') return;
  if (document.activeElement && ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
  if (nativeTextEditor) {
    if (nativeTextEditor.element.contains(document.activeElement)) {
      event.stopPropagation();
    }
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
async function pasteClipboard() {
  if (currentMode === 'read' || !clipboard.length) return;
  pasteOffset += 20;
  const idMap = new Map(clipboard.map(o => [o.objectId, uid()]));
  const clones = await Promise.all(clipboard.map(cloneObject));
  clones.forEach((clone, i) => {
    const original = clipboard[i];
    clone.set({ objectId: idMap.get(original.objectId), left: (original.left || 0) + pasteOffset, top: (original.top || 0) + pasteOffset });
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
  const autoSaveLabel = document.createElement('label'); autoSaveLabel.className = 'notes-auto-save'; autoSaveLabel.innerHTML = 'Auto Save <input type="checkbox" aria-label="Auto Save"><span></span>'; const autoSaveToggle = autoSaveLabel.querySelector('input'); autoSaveToggle.checked = false; autoSaveToggle.addEventListener('change', () => { if (currentMode === 'read') { autoSaveToggle.checked = autoSaveEnabled; return; } autoSaveEnabled = autoSaveToggle.checked; if (autoSaveEnabled && dirty) { clearTimeout(saveTimer); saveTimer = setTimeout(save, 1400); } }); document.querySelector('[data-group="other"]').append(autoSaveLabel);
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
  if (document.activeElement && ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
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
    objects.forEach(o => { finalizeLoadedObject(o); canvas.add(o); });
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
let pageModalSaving = false;
function setPageFormSaving(isSaving) { const button = document.querySelector('#pageForm button[type="submit"]'); if (!button) return; button.disabled = isSaving; button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Saving...' : 'Save page'; }
document.getElementById('newPageBtn').addEventListener('click', () => { if (currentMode === 'read') return; openPageModal('new'); });
document.querySelectorAll('[data-close-page-modal]').forEach(button => button.addEventListener('click', () => { if (!pageModalSaving) closeModal('pageModal'); }));
let deletePageSaving = false;
function setDeletePageSaving(isSaving) {
  const button = document.getElementById('confirmDeletePage');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Deleting...' : '<i class="fas fa-trash-alt"></i> Delete page';
}
document.querySelectorAll('[data-close-delete-page]').forEach(button => button.addEventListener('click', () => { if (!deletePageSaving) closeModal('deletePageModal'); }));
document.getElementById('pageForm').addEventListener('submit', async event => {
  event.preventDefault(); if (pageModalSaving || currentMode === 'read') return;
  const title = document.getElementById('pageName').value.trim(); const target = root.dataset.pageMode === 'new' ? '' : root.dataset.pageTarget; const message = document.getElementById('pageMessage');
  if (!title) { message.textContent = 'Page name cannot be empty.'; return; }
  if (pageNameTaken(title, target)) { message.textContent = 'A page with this name already exists in this notebook.'; return; }
  pageModalSaving = true; message.textContent = ''; setPageFormSaving(true);
  try {
    let selected = activePageId;
    if (root.dataset.pageMode === 'new') {
      const result = await api(`${apiBase}/${notebookId}/pages`, { method: 'POST', body: JSON.stringify({ title }) });
      selected = result.page.id; pageCache = [...pageCache, result.page]; closeModal('pageModal'); renderPages(pageCache, selected, false); await openBlankPage(selected);
    } else {
      const result = await api(`${apiBase}/${notebookId}/pages/${root.dataset.pageTarget}`, { method: 'PATCH', body: JSON.stringify({ title }) });
      pageCache = pageCache.map(page => page.id === result.page.id ? result.page : page); closeModal('pageModal'); renderPages(pageCache, selected, false);
    }
  } catch (error) { message.textContent = error.message; }
  finally { pageModalSaving = false; setPageFormSaving(false); }
});
document.getElementById('confirmDeletePage').addEventListener('click', async () => { if (currentMode === 'read' || deletePageSaving) return; const target = root.dataset.pageTarget; const remaining = pageCache.filter(page => page.id !== target); document.getElementById('deletePageMessage').textContent = ''; deletePageSaving = true; setDeletePageSaving(true); try { await api(`${apiBase}/${notebookId}/pages/${target}`, { method: 'DELETE' }); pageObjectsCache.delete(target); closeModal('deletePageModal'); renderPages(remaining, activePageId === target ? remaining[0]?.id || null : activePageId, false); if (activePageId === target) { if (remaining[0]) await loadPage(remaining[0].id); else showEmptyEditor(); } toast('Page deleted.'); } catch (error) { document.getElementById('deletePageMessage').textContent = error.message; } finally { deletePageSaving = false; setDeletePageSaving(false); } });
document.addEventListener('click', closePageMenus); document.addEventListener('click', event => { const link = event.target.closest('a[href]'); if (!link || event.defaultPrevented || link.target || link.href === location.href) return; event.preventDefault(); showUnsavedChangesDialog(() => { location.href = link.href; }); }, true); document.addEventListener('keydown', event => { if (event.key === 'Escape') closePageMenus(); }); window.addEventListener('scroll', closePageMenus, true); new MutationObserver(applyCanvasTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] }); window.addEventListener('resize', () => { closePageMenus(); resize(); }); window.addEventListener('beforeunload', event => { if (!dirty) return; event.preventDefault(); event.returnValue = ''; });
if (isPublicView) initPublicReadOnlyView();
applyToolbarVisibility(); updateReadModeUI(); updateFullscreenUI(); updateToolbarToggleUI();
resize(); fontMetricsPromise.then(refreshPages).catch(error => { toast(error.message, 'error'); showEmptyEditor(); });