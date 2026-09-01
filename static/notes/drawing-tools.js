/* Dedicated drawing and shape controls for the Notes Fabric canvas. */
setTimeout(() => {
  const canvas = window.__notesCanvas;
  const toolbar = document.querySelector('.object-toolbar');
  if (!canvas || !toolbar) return;

  const id = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  const insert = (element, group) => { (document.querySelector(`[data-group="${group}"]`) || toolbar).appendChild(element); return element; };
  /* Every toolbar button created here is routed through this wrapper, so a single
     `window.__notesReadOnly` check (set by editor.js when Read Mode is active)
     is enough to block pen/eraser/format/shape actions — no per-button changes needed.
     mousedown is prevented so clicking a button never steals focus/selection away from
     an actively-open text editor (root cause of Highlight/Bold/etc silently no-op'ing). */
  const button = (label, icon, handler, group = 'format') => { const item = document.createElement('button'); item.type = 'button'; item.title = label; item.setAttribute('aria-label', label); item.innerHTML = `<i class="${icon}"></i>`; item.addEventListener('mousedown', event => event.preventDefault()); item.addEventListener('click', (...args) => { if (window.__notesReadOnly) return; handler(...args); }); return insert(item, group); };
  /* Defaults for NEW content only — changing these never recolors/resizes existing objects.
     Text size and ink stroke width are intentionally separate state (window.__notesDefaultSize
     vs window.__notesInkSizes below) so adjusting one never affects the other. */
  window.__notesDefaultSize = window.__notesDefaultSize || 18;
  window.__notesDefaultFont = window.__notesDefaultFont || 'DM Sans';
  // ROOT CAUSE of "the default shape color looks inconsistent/wrong, and doesn't match the
  // toolbar swatch": --accent-subtle (see theme.css) is a near-transparent UI tint meant for
  // hover/badge backgrounds — e.g. rgba(76,110,245,0.12), 12% opacity — never meant to be
  // painted as an opaque shape fill. A freshly-drawn shape came out barely-visible pale blue
  // while the (native <input type=color>, hex-only) swatch fell back to a hardcoded #4a86e8
  // because it can't represent an rgba() string at all, so the two never agreed. A plain,
  // opaque, professional default (the same blue already used elsewhere in this file/palette)
  // fixes both at once: the shape now renders exactly what the swatch shows.
  window.__notesShapeColor = window.__notesShapeColor || '#4a86e8';
  window.__notesStickyBgColor = window.__notesStickyBgColor || getComputedStyle(document.documentElement).getPropertyValue('--warning-bg').trim();
  /* Ink tool state — 'pen' | 'pencil' | 'highlighter' | 'eraser'. Exposed on window (like the
     other __notes* flags already used across editor.js/drawing-tools.js) so editor.js's single
     path:created handler can read it to tag a freshly-drawn stroke — no second listener needed.
     Each ink tool remembers its own stroke width independently, so switching tools never makes
     you re-pick a width (a highlighter needs to stay wide even after a thin pen stroke). */
  window.__notesInkTool = window.__notesInkTool || 'pen';
  window.__notesInkSizes = window.__notesInkSizes || { pen: 6, pencil: 3, highlighter: 22 };
  const hexToRgba = (hex, alpha) => { const v = hex.replace('#', ''); const full = v.length === 3 ? v.split('').map(c => c + c).join('') : v; const n = parseInt(full, 16); return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`; };

  /* ══════════════════════════════════════════════════════════════════════════════════
     COLOR PICKER SYSTEM — one generic popover builder (Google Sheets-style: a large preset
     grid, the native custom-RGB picker, and a typeable hex code field) shared by all three
     color controls in this toolbar: Text/Ink color, Highlight color, and Shape/Sticky Fill
     color. Only the UI/plumbing (grid rendering, popover open/close, hex parsing) is shared —
     each control still owns its own color STATE and read/write logic exactly as the separate
     native <input type=color> elements did before (window.__notesInkColor /
     __notesHighlightColor / __notesShapeColor stay three separate variables with three
     separate meanings), and every apply path below still ends in the exact same object
     mutation + canvas.fire('object:modified', ...) (or window.__notesApplyTextColor's own
     markDirty()) the old inputs used, so serialization/autosave/undo participate identically
     to before. Nothing here is a new data model — it's a toolbar-UI replacement. */
  const hexToRgbTriplet = hex => { const v = hex.replace('#', ''); const full = v.length === 3 ? v.split('').map(c => c + c).join('') : v; const n = parseInt(full, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
  const rgbTripletToHex = (r, g, b) => '#' + [r, g, b].map(v => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('');
  const mixHex = (hex, target, amount) => { const [r1, g1, b1] = hexToRgbTriplet(hex), [r2, g2, b2] = hexToRgbTriplet(target); return rgbTripletToHex(r1 + (r2 - r1) * amount, g1 + (g2 - g1) * amount, b1 + (b2 - b1) * amount); };
  /* Preset grid data: 10 columns (Grayscale, Reds, Oranges, Yellows, Greens, Cyan/Teal, Blues,
     Purples, Magentas, Browns — left to right), 7 rows each (3 light tints, the pure/base hue,
     3 darker shades), generated by mixing each base toward white/black rather than 70
     hand-picked hex codes, so the ramp is always visually even and every color control in the
     toolbar offers the exact same palette. Gray is a straight black->white ramp (mixing a hue
     toward itself doesn't apply). Computed once and cached: these never change at runtime. */
  const PRESET_FAMILIES = [
    { name: 'Gray', base: '#757575' },
    { name: 'Red', base: '#ea4335' },
    { name: 'Orange', base: '#f4511e' },
    { name: 'Yellow', base: '#fbbc04' },
    { name: 'Green', base: '#34a853' },
    { name: 'Teal', base: '#00acc1' },
    { name: 'Blue', base: '#4a86e8' },
    { name: 'Purple', base: '#8e24aa' },
    { name: 'Magenta', base: '#d81b60' },
    { name: 'Brown', base: '#795548' },
  ];
  let presetGridCache = null;
  const buildPresetGrid = () => {
    if (presetGridCache) return presetGridCache;
    presetGridCache = PRESET_FAMILIES.map(({ name, base }) => {
      if (name === 'Gray') return ['#000000', '#434343', '#666666', '#999999', '#b7b7b7', '#d9d9d9', '#ffffff'];
      return [
        mixHex(base, '#ffffff', 0.75), mixHex(base, '#ffffff', 0.5), mixHex(base, '#ffffff', 0.25),
        base,
        mixHex(base, '#000000', 0.2), mixHex(base, '#000000', 0.4), mixHex(base, '#000000', 0.6),
      ];
    });
    return presetGridCache;
  };
  const HEX_INPUT_RE = /^#?([0-9a-f]{6}|[0-9a-f]{3})$/i;
  const normalizeTypedHex = raw => { const m = HEX_INPUT_RE.exec(String(raw || '').trim()); if (!m) return null; let h = m[1]; if (h.length === 3) h = h.split('').map(c => c + c).join(''); return '#' + h.toLowerCase(); };
  // Shared "no fill / transparent" swatch look — a diagonal line through the swatch, the same
  // convention PowerPoint/Google Slides use for their own "No Fill" option, so it reads as
  // "none" at a glance rather than being mistaken for an actual (e.g. white) color choice.
  const NO_FILL_SWATCH_BG = 'linear-gradient(to top right, transparent calc(50% - 1px), var(--danger) calc(50% - 1px), var(--danger) calc(50% + 1px), transparent calc(50% + 1px)), var(--surface)';
  /* One popover builder for all three color controls below. `getColor`/`applyColor` are the
     only per-control pieces (each control's own read/write logic); this function only ever
     renders UI and wires generic interactions (preset click, native custom picker, typed hex
     code) that call back into them. `key` distinguishes which control's popover is currently
     open via the app's one shared floating-panel portal (placeFloatingPanel/closePageMenus,
     exposed by editor.js as window.__notes*) — reused as-is for positioning and outside-click/
     Escape/scroll dismissal, so opening one control's popover always cleanly closes any other
     and no new popover-lifecycle code is needed here at all.
     `getColor` may return `null` to mean "no fill / transparent" (only meaningful when
     `noFillSupport` is passed, i.e. the Shape Fill control) — every place a color is read here
     treats null as its own state, never coerced to a real hex, so it round-trips exactly
     through sync/open/preset-selection instead of silently becoming some fallback color. */
  const createColorControl = ({ key, title, ariaLabel, group, heading, getColor, applyColor, fallback, noFillSupport }) => {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.title = title; btn.setAttribute('aria-label', ariaLabel);
    btn.style.cssText = `width:28px;height:28px;padding:0;border:1px solid var(--border);border-radius:7px;cursor:pointer;background:${fallback}`;
    btn.addEventListener('mousedown', event => event.preventDefault());
    insert(btn, group);
    const sync = () => { const value = getColor(); btn.style.background = value === null ? NO_FILL_SWATCH_BG : value; };
    const open = () => {
      if (window.__notesReadOnly) return;
      const current = window.__notesGetActiveFloatingPanel?.();
      const alreadyOpen = current?.dataset.notesColorPopover === key;
      window.__notesCloseFloatingPanels?.();
      if (alreadyOpen) return;
      const rawColor = getColor();
      const isNoFill = noFillSupport && rawColor === null;
      const activeHex = isNoFill ? null : (toHexColor(rawColor) || fallback);
      const panel = document.createElement('div');
      panel.dataset.notesColorPopover = key;
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-label', heading);
      panel.style.cssText = 'position:fixed;z-index:1100;width:242px;padding:12px;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-lg);font-size:.72rem';
      // Only stopPropagation on click (so interacting inside the popover doesn't bubble to the
      // document-level outside-click listener and immediately close it) — NOT mousedown:
      // nothing here needs to preserve focus on some other element, and preventDefault on
      // mousedown would silently block the native <input type=color> below from ever opening
      // its OS color picker.
      panel.addEventListener('click', event => event.stopPropagation());

      const headingEl = document.createElement('div');
      headingEl.textContent = heading;
      headingEl.style.cssText = 'font-weight:700;color:var(--text-1);margin-bottom:8px;font-size:.74rem';
      panel.append(headingEl);

      if (noFillSupport) {
        const noFillBtn = document.createElement('button');
        noFillBtn.type = 'button';
        noFillBtn.setAttribute('aria-label', 'No fill (transparent)');
        noFillBtn.style.cssText = `display:flex;align-items:center;gap:8px;width:100%;height:30px;padding:0 8px;margin-bottom:10px;border-radius:7px;cursor:pointer;background:var(--bg-raised);border:1px solid ${isNoFill ? 'var(--accent)' : 'var(--border)'};color:var(--text-1);font:600 .72rem inherit`;
        const swatchDot = document.createElement('span');
        swatchDot.style.cssText = `width:16px;height:16px;border-radius:4px;flex-shrink:0;background:${NO_FILL_SWATCH_BG};border:1px solid var(--border)`;
        const swatchLabel = document.createElement('span');
        swatchLabel.textContent = 'No Fill';
        noFillBtn.append(swatchDot, swatchLabel);
        noFillBtn.addEventListener('mousedown', event => event.preventDefault());
        noFillBtn.addEventListener('click', () => { applyColor(null); sync(); window.__notesCloseFloatingPanels?.(); });
        panel.append(noFillBtn);
      }

      const grid = document.createElement('div');
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(10,1fr);gap:3px;margin-bottom:10px';
      buildPresetGrid().forEach(column => column.forEach(hex => {
        const isSelected = !isNoFill && activeHex && hex.toLowerCase() === activeHex.toLowerCase();
        const swatch = document.createElement('button');
        swatch.type = 'button'; swatch.title = hex; swatch.setAttribute('aria-label', `Color ${hex}`);
        swatch.style.cssText = `width:20px;height:20px;padding:0;border-radius:4px;cursor:pointer;background:${hex};border:1px solid ${hex.toLowerCase() === '#ffffff' ? 'var(--border)' : 'transparent'};box-shadow:${isSelected ? '0 0 0 2px var(--surface),0 0 0 3.5px var(--accent)' : 'none'};transition:transform .08s ease`;
        swatch.addEventListener('mouseenter', () => { swatch.style.transform = 'scale(1.15)'; });
        swatch.addEventListener('mouseleave', () => { swatch.style.transform = 'scale(1)'; });
        swatch.addEventListener('mousedown', event => event.preventDefault());
        swatch.addEventListener('click', () => { applyColor(hex); sync(); window.__notesCloseFloatingPanels?.(); });
        grid.append(swatch);
      }));
      panel.append(grid);

      const divider = document.createElement('div');
      divider.style.cssText = 'height:1px;background:var(--border);margin:2px 0 10px';
      panel.append(divider);

      const customRow = document.createElement('div');
      customRow.style.cssText = 'display:flex;align-items:center;gap:6px';
      const customLabel = document.createElement('span');
      customLabel.textContent = 'Custom';
      customLabel.style.cssText = 'color:var(--text-3);font-weight:600;letter-spacing:.03em;text-transform:uppercase;font-size:.62rem';
      // A native color input can't represent "no fill" (it's hex-only) — while no-fill is
      // active this just starts the custom picker from the fallback default; picking an actual
      // color here (or typing a hex below) is itself how the user leaves the no-fill state.
      const customStartHex = activeHex || fallback;
      const customInput = document.createElement('input');
      customInput.type = 'color'; customInput.value = customStartHex; customInput.title = 'Pick an exact custom color';
      customInput.style.cssText = 'width:30px;height:30px;padding:2px;border:1px solid var(--border);border-radius:7px;background:var(--bg-raised);cursor:pointer;flex-shrink:0';
      // Typeable hex code field — the new bit this popover adds beyond the old plain color
      // input: type/paste a code (with or without '#', 3- or 6-digit) and it applies live as
      // soon as it's a complete valid hex, exactly like the preset/native-picker paths.
      const hexInput = document.createElement('input');
      hexInput.type = 'text'; hexInput.spellcheck = false; hexInput.autocomplete = 'off';
      hexInput.value = isNoFill ? '' : customStartHex.toUpperCase(); hexInput.placeholder = isNoFill ? 'No fill' : '#RRGGBB'; hexInput.maxLength = 7;
      hexInput.title = 'Type an exact hex color code';
      hexInput.style.cssText = 'flex:1;min-width:0;height:30px;padding:0 8px;border:1px solid var(--border);border-radius:7px;background:var(--bg-raised);color:var(--text-1);font:600 .74rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.02em;outline:0';
      const applyTypedHex = () => {
        const normalized = normalizeTypedHex(hexInput.value);
        if (!normalized) { hexInput.style.borderColor = 'var(--danger-border)'; return; }
        hexInput.style.borderColor = 'var(--border)'; hexInput.value = normalized.toUpperCase();
        customInput.value = normalized; applyColor(normalized); sync();
      };
      hexInput.addEventListener('input', () => {
        const normalized = normalizeTypedHex(hexInput.value);
        hexInput.style.borderColor = normalized ? 'var(--border)' : 'var(--danger-border)';
        if (normalized) { customInput.value = normalized; applyColor(normalized); sync(); }
      });
      hexInput.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); applyTypedHex(); } });
      hexInput.addEventListener('blur', applyTypedHex);
      customInput.addEventListener('input', () => { hexInput.value = customInput.value.toUpperCase(); hexInput.style.borderColor = 'var(--border)'; applyColor(customInput.value); sync(); });
      customRow.append(customLabel, customInput, hexInput);
      panel.append(customRow);

      window.__notesPlaceFloatingPanel?.(panel, btn);
    };
    // stopPropagation first (matching the existing page-actions "..." button in editor.js, the
    // other consumer of this same portal) — without it, this click would bubble to the
    // document-level `closePageMenus` outside-click listener and close the popover the instant
    // it opens.
    btn.addEventListener('click', event => { event.stopPropagation(); open(); });
    sync();
    return { button: btn, sync };
  };

  /* ── Text / Ink color ── multi-purpose, same as the old plain <input type=color> it
     replaces: recolors the current text selection, a selected freehand drawing's stroke, or a
     selected stroke-only shape's (line/arrow) stroke; otherwise it's just the default used for
     new text and the live Pen/Pencil brush color. window.__notesInkColor is the new single
     source of truth for "this control's current value" (what color.value implicitly held
     before it was a real DOM element). */
  window.__notesInkColor = window.__notesInkColor || '#000000';
  // A freehand stroke (Pen/Pencil/Highlighter path) or a stroke-only shape (Line/Arrow) is
  // "ink-recolorable" — its visible color is its stroke, changeable after the fact via this
  // control. Shared by both the single- and multi-selection paths below so they can never
  // drift apart on what counts as recolorable.
  const isInkRecolorable = object => !!object && (object.objectType === 'drawing' || (object.objectType === 'shape' && object.strokeOnly));
  const getActiveInkColor = () => {
    const editorState = window.__notesGetNativeEditor?.();
    const activeObj = canvas.getActiveObject();
    const textObj = editorState?.object || (activeObj && ['i-text', 'textbox'].includes(activeObj.type) ? activeObj : null);
    if (textObj) {
      const { start, end } = editorState ? window.__notesTextSelectionOffsets(editorState.element) : { start: textObj.selectionStart || 0, end: textObj.selectionEnd || 0 };
      const styles = selectionRangeStyles(textObj, start, end);
      if (styles[0]?.fill) return styles[0].fill;
    }
    // A multi-selection (Fabric ActiveSelection) has no objectType of its own — only reflect
    // its members' actual color when EVERY selected object is ink-recolorable (a mixed
    // selection falls through to the same text/default behavior a single non-ink object
    // already gets, rather than guessing which member "wins").
    if (activeObj?.type === 'activeSelection') {
      const members = activeObj.getObjects ? activeObj.getObjects() : [];
      if (members.length && members.every(isInkRecolorable)) return members[0].stroke || window.__notesInkColor || '#000000';
      return window.__notesInkColor || '#000000';
    }
    if (isInkRecolorable(activeObj)) return activeObj.stroke || window.__notesInkColor || '#000000';
    return window.__notesInkColor || '#000000';
  };
  // Recolors exactly ONE ink-recolorable object in place — same stroke-only mutation either
  // way, never touches geometry/points, so the original freehand path or line/arrow shape is
  // preserved exactly. Pulled out so both the single-object and multi-selection branches of
  // applyInkColor below share the identical translucency-preserving logic.
  const recolorInkObject = (object, hex) => {
    const currentAlpha = object.objectType === 'drawing' && /^rgba\(/.test(String(object.stroke || '')) ? parseFloat(String(object.stroke).split(',')[3]) : NaN;
    object.set({ stroke: currentAlpha >= 0 && currentAlpha < 1 ? hexToRgba(hex, currentAlpha) : hex });
    if (object.objectType === 'drawing') object.dirty = true;
  };
  const applyInkColor = hex => {
    if (window.__notesReadOnly) return;
    window.__notesInkColor = hex;
    const active = canvas.getActiveObject();
    if (active?.type === 'activeSelection') {
      const members = active.getObjects ? active.getObjects() : [];
      // ROOT CAUSE of "a mixed selection (shape + ink + text) makes Pen/Ink color do nothing, or
      // wrongly recolor text": this used to require EVERY member to be ink-recolorable before
      // touching any of them, else it fell straight through to the text-color handler — so a
      // Shape+Ink+Text selection (never "every member" ink) always skipped ink entirely and
      // recolored text instead, even though ink was clearly what was selected too. Filtering to
      // just the ink-recolorable members and only falling back to text when there are NONE keeps
      // this control's existing dual role (ink color, or text color when nothing ink-like is
      // selected) while making it correctly ignore shape/text/image members that happen to be in
      // the same selection rather than either seizing all of them or none of them.
      const relevant = members.filter(isInkRecolorable);
      if (relevant.length) {
        // ROOT CAUSE of multi-selected ink getting visually corrupted (strokes shifted/
        // duplicated-looking) on recolor: firing 'object:modified' PER MEMBER used to run
        // editor.js's handler — including constrainObjectToPage(member) — once for each still-
        // grouped object. While an object is part of a live Fabric ActiveSelection, its own
        // left/top are stored relative to the SELECTION's center, not the canvas — but
        // constrainObjectToPage reads an ABSOLUTE bounding box (getBoundingRect(true, true))
        // and, whenever it looked "out of page bounds" under that mismatch, wrote the resulting
        // absolute-space delta straight onto the member's group-relative left/top
        // (object.set({ left: object.left + dx, ... })) — corrupting that one stroke's position
        // relative to its siblings still in the group. Recoloring itself (recolorInkObject)
        // never touches geometry — only firing the event on still-grouped members did.
        // Firing it ONCE on the selection itself instead avoids the mismatch entirely: a
        // color-only change never actually moves anything, so constrainObjectToPage on the
        // group is a no-op, and markDirty()/autosave/undo still see exactly one modification
        // (a cleaner history entry than one per member, as a side benefit).
        relevant.forEach(member => recolorInkObject(member, hex));
        canvas.requestRenderAll();
        canvas.fire('object:modified', { target: active });
      } else {
        // No ink-recolorable member at all (e.g. a Shape+Text selection) — the same combined
        // "Text / ink color" button falls back to its other role, itself now selection-aware
        // (see applyTextColor in editor.js) so it only ever touches actual text members, never
        // the shape.
        window.__notesApplyTextColor?.(hex);
      }
    } else if (isInkRecolorable(active)) {
      recolorInkObject(active, hex);
      canvas.requestRenderAll();
      canvas.fire('object:modified', { target: active });
    } else {
      window.__notesApplyTextColor?.(hex);
    }
    // Keeps pen/pencil color changeable at any time, live. Highlighter reads its own color
    // (see Highlight color below) and must never be overwritten by this one. Pencil keeps its
    // live-preview alpha baked in here too (see activateBrush) — otherwise switching color
    // mid-session would arm the brush with a fully-opaque color for the next stroke.
    if (canvas.freeDrawingBrush && window.__notesInkTool !== 'highlighter') canvas.freeDrawingBrush.color = window.__notesInkTool === 'pencil' ? hexToRgba(hex, .82) : hex;
  };
  const inkColorControl = createColorControl({
    key: 'ink', title: 'Text / ink color', ariaLabel: 'Text / ink color', group: 'color',
    heading: 'Text / ink color', getColor: getActiveInkColor, applyColor: applyInkColor, fallback: '#000000',
  });
  canvas.on('selection:created', inkColorControl.sync);
  canvas.on('selection:updated', inkColorControl.sync);
  canvas.on('selection:cleared', inkColorControl.sync);

  /* ── Highlight color ── distinct, pre-existing behavior kept as-is: picking a color here
     only sets the DEFAULT (and the live Highlighter pen's color) — it does not itself
     highlight anything. Applying a highlight to selected text stays the separate "Highlight
     selected text" button (applyHighlight, further down), which now reads
     window.__notesHighlightColor instead of a <input type=color>'s .value. */
  window.__notesHighlightColor = window.__notesHighlightColor || '#ffff00';
  const getActiveHighlightColor = () => {
    const editorState = window.__notesGetNativeEditor?.();
    const activeObj = canvas.getActiveObject();
    const textObj = editorState?.object || (activeObj && ['i-text', 'textbox'].includes(activeObj.type) ? activeObj : null);
    if (textObj) {
      const { start, end } = editorState ? window.__notesTextSelectionOffsets(editorState.element) : { start: textObj.selectionStart || 0, end: textObj.selectionEnd || 0 };
      if (start !== end) {
        const styles = selectionRangeStyles(textObj, start, end);
        if (styles[0]?.textBackgroundColor) return styles[0].textBackgroundColor;
      }
    }
    return window.__notesHighlightColor || '#ffff00';
  };
  const applyHighlightColor = hex => {
    if (window.__notesReadOnly) return;
    window.__notesHighlightColor = hex;
    if (canvas.freeDrawingBrush && window.__notesInkTool === 'highlighter') canvas.freeDrawingBrush.color = hexToRgba(hex, .35);
  };
  const highlightColorControl = createColorControl({
    key: 'highlight', title: 'Highlight color (text highlight & Highlighter pen)', ariaLabel: 'Highlight color', group: 'color',
    heading: 'Highlight color', getColor: getActiveHighlightColor, applyColor: applyHighlightColor, fallback: '#ffff00',
  });
  canvas.on('selection:created', highlightColorControl.sync);
  canvas.on('selection:updated', highlightColorControl.sync);
  canvas.on('selection:cleared', highlightColorControl.sync);

  /* Quick ink-color swatches for Pen/Pencil — one click straight to applyInkColor, same shared
     apply path the popover's own preset grid uses; no separate source of truth. */
  const INK_SWATCHES = ['#1a1a1a', '#c0392b', '#e67e22', '#2980b9', '#27ae60', '#8e44ad'];
  const inkPalette = document.createElement('div'); inkPalette.title = 'Quick ink colors'; inkPalette.style.cssText = 'display:flex;gap:3px;align-items:center';
  INK_SWATCHES.forEach(hex => { const swatch = document.createElement('button'); swatch.type = 'button'; swatch.title = hex; swatch.setAttribute('aria-label', `Ink color ${hex}`); swatch.style.cssText = `width:16px;height:16px;border-radius:50%;border:1px solid var(--border);background:${hex};padding:0;cursor:pointer`; swatch.addEventListener('mousedown', event => event.preventDefault()); swatch.addEventListener('click', () => { if (window.__notesReadOnly) return; applyInkColor(hex); inkColorControl.sync(); }); inkPalette.appendChild(swatch); }); insert(inkPalette, 'color');

  /* ── Shape / Sticky Note fill color ── reads active.fill/backgroundColor on selection change
     (fixes "the toolbar color and the actual selected shape color do not always match" at its
     source) and never snaps a custom color to the nearest preset — the popover just shows
     whichever preset (if any) exactly matches. */
  // window.__notesShapeColor is intentionally read/written WITHOUT a `|| fallback` anywhere
  // below (only the top-of-file init line has one) — after that line it's always either a real
  // hex string or explicitly `null` (No Fill chosen as the default for new shapes), and `||`
  // would treat null as "unset" and silently coerce it back to a color, defeating No Fill the
  // moment nothing/a non-shape is selected.
  const getActiveShapeColor = () => {
    const active = canvas.getActiveObject();
    if (active?.objectType === 'sticky_note') return active.backgroundColor || window.__notesStickyBgColor || '#4a86e8';
    // A shape's own fill is only ever null (no fill) or a real color — both are meaningful and
    // must be shown exactly as they are; only fall back to the shared default when the shape
    // has no fill value recorded at all (shouldn't normally happen, but stays safe either way).
    if (active?.objectType === 'shape' && !active.strokeOnly) return 'fill' in active ? active.fill : window.__notesShapeColor;
    return window.__notesShapeColor;
  };
  const applyShapeFillColor = hex => {
    if (window.__notesReadOnly) return;
    window.__notesShapeColor = hex;
    if (hex !== null) window.__notesStickyBgColor = hex; // a sticky note's own background never goes "no fill"
    const active = canvas.getActiveObject();
    if (!active) return;
    // Same "filter the ActiveSelection down to just the relevant members" treatment as
    // applyInkColor/applyTextColor above — a Shape+Ink+Text (or Shape+Sticky) selection only
    // ever has its fillable members' fill touched here, never a freehand stroke or text color.
    if (active.type === 'activeSelection') {
      const members = active.getObjects ? active.getObjects() : [];
      const relevant = members.filter(m => (m.objectType === 'shape' && !m.strokeOnly) || m.objectType === 'sticky_note');
      if (!relevant.length) return;
      relevant.forEach(m => {
        if (m.objectType === 'sticky_note') { if (hex !== null) { m.set({ backgroundColor: hex }); m.customBg = true; m.initDimensions(); } }
        else m.set({ fill: hex });
      });
      canvas.requestRenderAll();
      canvas.fire('object:modified', { target: active });
      return;
    }
    if (active.objectType === 'sticky_note') { if (hex !== null) window.__notesApplyStickyBackground?.(hex); }
    else if (active.objectType === 'shape' && !active.strokeOnly) { active.set({ fill: hex }); canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
  };
  const fillColorControl = createColorControl({
    key: 'fill', title: 'Fill / background color (shapes, sticky note background)', ariaLabel: 'Fill / background color', group: 'shape',
    heading: 'Fill color', getColor: getActiveShapeColor, applyColor: applyShapeFillColor, fallback: '#4a86e8', noFillSupport: true,
  });
  canvas.on('selection:created', fillColorControl.sync);
  canvas.on('selection:updated', fillColorControl.sync);
  canvas.on('selection:cleared', fillColorControl.sync);

  /* ── Shape border / stroke color ── a dedicated, always-available control, independent of
     Fill. ROOT CAUSE of two related reports this fixes at once:
     (1) "No Fill shapes have no way to choose their own border color" — a shape's stroke was
     previously only ever set ONCE, at creation time (derived from whatever the fill default
     happened to be that moment); nothing afterward ever touched active.stroke for a normal
     (non-strokeOnly) shape, so a shape already in No-Fill mode had its border color permanently
     frozen with no UI path to change it, short of temporarily filling it, recoloring THAT, then
     switching back to No Fill (exactly the workaround the report describes).
     (2) "some shapes (e.g. arrows) don't respond to color changes" — Line/Arrow/Double
     Arrow/Curved Arrow/X-Y Axis are created strokeOnly:true, and applyShapeFillColor above
     explicitly skips strokeOnly shapes (`!active.strokeOnly`) since they have no fill concept —
     so the obviously-labeled "Fill / background color" swatch silently no-ops on them. Their
     stroke WAS technically recolorable, but only through the separate "Text / ink color"
     popover (isInkRecolorable below) — correct in effect, but not remotely discoverable as "how
     do I change this arrow's color", which is exactly what was being reported as broken.
     This control works identically for EVERY shape objectType (filled or strokeOnly, fresh or
     legacy) by only ever touching `stroke` — never `fill` — so it can't regress anything the
     Fill control or the ink-color popover already handle; it just closes the gap where neither
     of those reached. `!active.shapeTextFor` excludes a shape's own caption label (also
     objectType:'shape') from being mistaken for the shape body it's attached to. */
  const getActiveShapeBorderColor = () => {
    const active = canvas.getActiveObject();
    if (active?.objectType === 'shape' && !active.shapeTextFor) return active.stroke || window.__notesShapeBorderColor || '#4a86e8';
    return window.__notesShapeBorderColor || '#4a86e8';
  };
  const applyShapeBorderColor = hex => {
    if (window.__notesReadOnly || hex === null) return;
    window.__notesShapeBorderColor = hex;
    const active = canvas.getActiveObject();
    if (!active) return;
    // Same selection-filtering treatment as the other color controls above — only the shape
    // members (excluding each shape's own caption label) get their stroke touched.
    if (active.type === 'activeSelection') {
      const members = active.getObjects ? active.getObjects() : [];
      const relevant = members.filter(m => m.objectType === 'shape' && !m.shapeTextFor);
      if (!relevant.length) return;
      relevant.forEach(m => m.set({ stroke: hex }));
      canvas.requestRenderAll();
      canvas.fire('object:modified', { target: active });
      return;
    }
    if (active.objectType !== 'shape' || active.shapeTextFor) return;
    active.set({ stroke: hex });
    canvas.requestRenderAll();
    canvas.fire('object:modified', { target: active });
  };
  const borderColorControl = createColorControl({
    key: 'shapeBorder', title: 'Shape border / stroke color', ariaLabel: 'Shape border color', group: 'shape',
    heading: 'Border color', getColor: getActiveShapeBorderColor, applyColor: applyShapeBorderColor, fallback: '#4a86e8',
  });
  canvas.on('selection:created', borderColorControl.sync);
  canvas.on('selection:updated', borderColorControl.sync);
  canvas.on('selection:cleared', borderColorControl.sync);
  const eraserSize = document.createElement('select'); eraserSize.title = 'Eraser size'; [['Small', 10], ['Medium', 22], ['Large', 38]].forEach(([name, value]) => eraserSize.add(new Option(name, value))); eraserSize.style.cssText = 'height:28px;max-width:76px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(eraserSize, 'pen');
  // Regular n-gon helper (radius r, first vertex pointing up) — used for Pentagon/Octagon/Star so
  // vertices are mathematically correct rather than hand-picked (Pentagon was previously lopsided).
  const ngon = (n, r, rotate = 0) => Array.from({ length: n }, (_, i) => { const a = -Math.PI / 2 + rotate + i * (2 * Math.PI / n); return { x: Math.cos(a) * r, y: Math.sin(a) * r }; });
  const star = (points, rOuter, rInner) => Array.from({ length: points * 2 }, (_, i) => { const a = -Math.PI / 2 + i * Math.PI / points, r = i % 2 ? rInner : rOuter; return { x: Math.cos(a) * r, y: Math.sin(a) * r }; });
  const shapes = {
    Rectangle: () => new fabric.Rect({ width: 170, height: 105 }),
    'Rounded Rectangle': () => new fabric.Rect({ width: 170, height: 105, rx: 16, ry: 16 }),
    Circle: () => new fabric.Circle({ radius: 60 }),
    Ellipse: () => new fabric.Ellipse({ rx: 90, ry: 55 }),
    Triangle: () => new fabric.Triangle({ width: 140, height: 115 }),
    'Right Triangle': () => new fabric.Polygon([{ x: 0, y: 0 }, { x: 0, y: 115 }, { x: 140, y: 115 }]),
    Diamond: () => new fabric.Polygon([{ x: 60, y: 0 }, { x: 120, y: 60 }, { x: 60, y: 120 }, { x: 0, y: 60 }]),
    Parallelogram: () => new fabric.Polygon([{ x: 40, y: 0 }, { x: 170, y: 0 }, { x: 130, y: 100 }, { x: 0, y: 100 }]),
    Trapezoid: () => new fabric.Polygon([{ x: 40, y: 0 }, { x: 130, y: 0 }, { x: 170, y: 100 }, { x: 0, y: 100 }]),
    Pentagon: () => new fabric.Polygon(ngon(5, 62)),
    Hexagon: () => new fabric.Polygon([{ x: -60, y: 0 }, { x: -30, y: -52 }, { x: 30, y: -52 }, { x: 60, y: 0 }, { x: 30, y: 52 }, { x: -30, y: 52 }]),
    Octagon: () => new fabric.Polygon(ngon(8, 65, Math.PI / 8)),
    Star: () => new fabric.Polygon(star(5, 65, 30)),
    'Star 6': () => new fabric.Polygon(star(6, 60, 28)),
    Cross: () => new fabric.Polygon([{ x: 40, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 40 }, { x: 140, y: 40 }, { x: 140, y: 100 }, { x: 100, y: 100 }, { x: 100, y: 140 }, { x: 40, y: 140 }, { x: 40, y: 100 }, { x: 0, y: 100 }, { x: 0, y: 40 }, { x: 40, y: 40 }]),
    Heart: () => new fabric.Path('M 85 30 C 70 -10 0 0 0 45 C 0 80 40 105 85 140 C 130 105 170 80 170 45 C 170 0 100 -10 85 30 z'),
    Semicircle: () => new fabric.Path('M 0 60 A 60 60 0 0 1 120 60 z'),
    Cylinder: () => new fabric.Path('M 0 20 A 60 20 0 1 0 120 20 A 60 20 0 1 0 0 20 M 0 20 L 0 100 A 60 20 0 0 0 120 100 L 120 20'),
    Cube: () => new fabric.Path('M 0 30 L 90 0 L 170 30 L 170 110 L 90 140 L 0 110 z M 0 30 L 90 60 L 170 30 M 90 60 L 90 140'),
    Cloud: () => new fabric.Path('M 30 90 Q 10 90 10 70 Q 10 50 30 50 Q 30 25 55 25 Q 75 5 100 25 Q 130 20 140 45 Q 160 50 160 70 Q 160 90 140 90 Z'),
    // A fabric.Path (not Polygon) — closed with a trailing Z — so it can go through the same
    // _setPath-based rebuild every other arrow-aware shape below uses for independent head sizing;
    // the exact initial points here barely matter since finalizeShape's very first drag/click
    // immediately rebuilds this via buildBlockArrowPath anyway (see ARROW_KINDS/BOXED family).
    'Block Arrow': () => new fabric.Path('M 0 25 L 100 25 L 100 0 L 170 45 L 100 90 L 100 65 L 0 65 z'),
    Callout: () => new fabric.Path('M 0 0 L 170 0 L 170 95 L 58 95 L 25 125 L 38 95 L 0 95 z'),
    'Rounded Callout': () => new fabric.Path('M 20 0 L 150 0 Q 170 0 170 20 L 170 70 Q 170 90 150 90 L 60 90 L 35 115 L 42 90 L 20 90 Q 0 90 0 70 L 0 20 Q 0 0 20 0 z'),
    Line: () => new fabric.Line([0, 0, 170, 0]),
    Arrow: () => new fabric.Path('M 0 20 L 140 20 M 105 0 L 140 20 L 105 40'),
    'Double Arrow': () => new fabric.Path('M 0 20 L 170 20 M 30 0 L 0 20 L 30 40 M 140 0 L 170 20 L 140 40'),
    'Curved Arrow': () => new fabric.Path('M 10 60 Q 90 -10 165 35 M 145 20 L 165 35 L 148 52'),
    // Simple X-Y coordinate axes — deliberately just the two perpendicular arrows and nothing
    // else: no axis numbers, tick marks, or "X"/"Y" labels baked in (the user adds their own
    // via the Text tool if/where they want them — see the task this shape was added for). Drawn
    // in a 160x160 box so it drags out with independent width/height like any other closed
    // shape (NOT direction+length like Line/Arrow below — an axis has two perpendicular
    // extents, not one), which is why it's intentionally left out of LINE_SHAPES even though it
    // shares their stroke-only, no-fill nature (see NO_FILL_SHAPES just below instead).
    'X-Y Axis': () => new fabric.Path('M 10 150 L 150 150 M 138 142 L 150 150 L 138 158 M 10 150 L 10 10 M 2 22 L 10 10 L 18 22'),
  };
  // Line-type shapes are drawn by direction+length (angle/scale from the drag vector), not a
  // bounding-box stretch — a fixed-orientation template can't otherwise become a real diagonal
  // line/arrow. They're also stroke-only (no fill swatch), unlike closed/fillable shapes.
  const LINE_SHAPES = new Set(['Line', 'Arrow', 'Double Arrow', 'Curved Arrow']);
  // Shapes that are stroke-only (no fill swatch, same as LINE_SHAPES) but still use normal
  // bounding-box drag sizing rather than direction+length — currently just the X-Y axis, whose
  // two arms need to size independently along width/height, not rotate/scale as one rigid line.
  const NO_FILL_SHAPES = new Set(['X-Y Axis']);
  /* Each shape gets a recognizable Unicode glyph — used both as the picker button's own face
     (below) and each grid cell's icon inside the picker popover. */
  // 'X-Y Axis' deliberately does NOT use '└' — that box-drawing corner glyph reads as the LETTER
  // "L" at a glance, not as a coordinate system, which is exactly the mix-up reported. Two thin
  // arrow glyphs pointing up and right reads unambiguously as "a pair of axes" instead.
  const shapeGlyphs = { Rectangle: '▭', 'Rounded Rectangle': '▢', Circle: '●', Ellipse: '⬭', Triangle: '▲', 'Right Triangle': '◺', Diamond: '◆', Parallelogram: '▱', Trapezoid: '⏢', Pentagon: '⬠', Hexagon: '⬡', Octagon: '⯃', Star: '★', 'Star 6': '✡', Cross: '✚', Heart: '♥', Semicircle: '◗', Cylinder: '⬭', Cube: '⬛', Cloud: '☁', 'Block Arrow': '➤', Line: '─', Arrow: '→', 'Double Arrow': '↔', 'Curved Arrow': '↝', Callout: '💬', 'Rounded Callout': '🗨', 'X-Y Axis': '↑→' };
  /* ══════════════════════════════════════════════════════════════════════════════════
     ARROW-SHAPE REGISTRY — reusable by design: to give a FUTURE shape independent-arrowhead-size
     support, add one path-builder function to the matching family's BUILDERS map, and one entry
     to that family's KINDS map (shape display name -> the builder's key). Nothing else in this
     file needs to change — every call site (creation drag/click below, interactive resize, the
     arrowhead-size/on-off controls) drives entirely off `shape.arrowKind`/`shape.arrowFamily` +
     these registries, never off a specific shape name.
     Two families, because shapes need arrowheads in two structurally different ways:
     - LINE family (Arrow, Double Arrow, Curved Arrow): one direction + one length, builder
       signature (length, headSize, headsOn) => path string, body runs 0..length along local x.
     - BOXED family (X-Y Axis, Block Arrow): independent width + height (dragged out like a
       rectangle, not a single direction+length), builder signature
       (width, height, headSize, headsOn) => path string.
     ROOT CAUSE of "arrowheads scale up/down with the whole shape, forcing a long arrow into a
     huge arrowhead (or a big Block Arrow into a huge triangular tip)": every one of these was a
     fabric.Path/Polygon template with the arrowhead's geometry baked into the SAME points as the
     body, then stretched via scaleX/scaleY for the whole thing — the arrowhead's own points
     scaled right along with the body because there was never a separate "arrowhead size" concept.
     Fixing this properly means the arrowhead can't be a scaled TEMPLATE — the path's actual point
     data is regenerated on every resize, with the arrowhead's own size held fixed (headSize,
     independent of length/width/height) instead of scaling.
     `headsOn` (shape.arrowHeadsEnabled) is the "sometimes I just want a plain line/axis, no
     arrowhead" control — every builder below accepts it and, when false, emits just the body
     (line/curve/two axis arms/a plain rectangle for Block Arrow) with the wing segments omitted
     entirely, rather than drawing a zero-size or hidden head. */
  const ARROWHEAD_SPREAD_RATIO = 20 / 35; // matches the original hand-authored Arrow's proportions
  const buildStraightArrowPath = (length, headSize, double, headsOn) => {
    const line = `M 0 0 L ${length} 0`;
    if (!headsOn) return line;
    const back = Math.max(4, Math.min(headSize, length - 4));
    const spread = back * ARROWHEAD_SPREAD_RATIO;
    const c = spread; // vertical center — keeps the arrowhead's wings symmetric around the line
    const body = `M 0 ${c} L ${length} ${c}`;
    if (double) return `${body} M ${back} ${c - spread} L 0 ${c} L ${back} ${c + spread} M ${length - back} ${c - spread} L ${length} ${c} L ${length - back} ${c + spread}`;
    return `${body} M ${length - back} ${c - spread} L ${length} ${c} L ${length - back} ${c + spread}`;
  };
  /* Curved Arrow: a quadratic-bezier body from (0,0) to (length,0), bowed upward by an amount
     proportional to length (capped so a very long arrow doesn't get an absurdly tall arc), with a
     FIXED-size arrowhead at the end. The head's two wings are derived from the curve's own
     tangent direction AT the endpoint — the derivative of a quadratic bezier at t=1 is exactly
     2*(end-control) — so the head always sits flush with the curve no matter how the bow
     height/length change, while its wing length is headSize alone, never the curve's scale. */
  const CURVE_BOW_RATIO = 0.22, CURVE_BOW_MAX = 140, ARROWHEAD_WING_ANGLE = 28 * Math.PI / 180;
  const rotateVec = (x, y, angle) => ({ x: x * Math.cos(angle) - y * Math.sin(angle), y: x * Math.sin(angle) + y * Math.cos(angle) });
  const buildCurvedArrowPath = (length, headSize, headsOn) => {
    const bow = Math.min(length * CURVE_BOW_RATIO, CURVE_BOW_MAX);
    const controlX = length / 2, controlY = -bow, endX = length, endY = 0;
    const curve = `M 0 0 Q ${controlX} ${controlY} ${endX} ${endY}`;
    if (!headsOn) return curve;
    const tangentX = endX - controlX, tangentY = endY - controlY;
    const mag = Math.max(Math.hypot(tangentX, tangentY), 0.001);
    const ux = tangentX / mag, uy = tangentY / mag;
    const wing = Math.max(4, headSize);
    const w1 = rotateVec(-ux, -uy, ARROWHEAD_WING_ANGLE), w2 = rotateVec(-ux, -uy, -ARROWHEAD_WING_ANGLE);
    return `${curve} M ${endX + w1.x * wing} ${endY + w1.y * wing} L ${endX} ${endY} L ${endX + w2.x * wing} ${endY + w2.y * wing}`;
  };
  const LINE_ARROW_BUILDERS = {
    single: (length, headSize, headsOn) => buildStraightArrowPath(length, headSize, false, headsOn),
    double: (length, headSize, headsOn) => buildStraightArrowPath(length, headSize, true, headsOn),
    curved: buildCurvedArrowPath,
  };
  const LINE_ARROW_KINDS = { Arrow: 'single', 'Double Arrow': 'double', 'Curved Arrow': 'curved' };
  /* X-Y Axis: two perpendicular arms from a shared origin (bottom-left, local (0,height)) — one
     running right to (width,height), one running up to (0,0) — each with its own FIXED-size
     arrowhead, independent of the other arm's length AND of each other, exactly mirroring the
     line-family's "body scales, head doesn't" contract but for two independent extents instead
     of one. headsOn:false draws two plain perpendicular lines — "just a simple L/corner", the
     explicit no-arrowhead use case this whole toggle exists for. */
  const buildAxisPath = (width, height, headSize, headsOn) => {
    const originY = height;
    const lines = `M 0 ${originY} L ${width} ${originY} M 0 ${originY} L 0 0`;
    if (!headsOn) return lines;
    const hBack = Math.max(3, Math.min(headSize, width - 3)), hSpread = hBack * ARROWHEAD_SPREAD_RATIO;
    const vBack = Math.max(3, Math.min(headSize, height - 3)), vSpread = vBack * ARROWHEAD_SPREAD_RATIO;
    const hHead = `M ${width - hBack} ${originY - hSpread} L ${width} ${originY} L ${width - hBack} ${originY + hSpread}`;
    const vHead = `M ${-vSpread} ${vBack} L 0 0 L ${vSpread} ${vBack}`;
    return `${lines} ${hHead} ${vHead}`;
  };
  /* Block Arrow: a filled shaft (the middle ~44% of the height, matching the shape's original
     hand-authored proportions) running the full width minus a FIXED-length triangular head at the
     right end — same "body scales, head doesn't" contract, just applied to a closed filled
     polygon-as-path instead of a stroke-only line. headsOn:false collapses it to a plain filled
     rectangle spanning the whole width — a "bar" with no arrow tip. */
  const buildBlockArrowPath = (width, height, headSize, headsOn) => {
    const shaftTop = height * 0.278, shaftBottom = height * 0.722;
    if (!headsOn) return `M 0 ${shaftTop} L ${width} ${shaftTop} L ${width} ${shaftBottom} L 0 ${shaftBottom} z`;
    const headLen = Math.max(8, Math.min(headSize, width - 8));
    const shaftLen = width - headLen;
    return `M 0 ${shaftTop} L ${shaftLen} ${shaftTop} L ${shaftLen} 0 L ${width} ${height / 2} L ${shaftLen} ${height} L ${shaftLen} ${shaftBottom} L 0 ${shaftBottom} z`;
  };
  const BOXED_ARROW_BUILDERS = { axis: buildAxisPath, blockArrow: buildBlockArrowPath };
  const BOXED_ARROW_KINDS = { 'X-Y Axis': 'axis', 'Block Arrow': 'blockArrow' };
  // Two separate remembered defaults, not one shared value — a straight line-arrow's head and a
  // chunky Block Arrow's tip live at very different natural scales (a 22px head suits a thin
  // arrow; the same 22px would make a freshly-drawn Block Arrow look like a thin nub instead of
  // the bold tip its original hand-tuned design had), so each family keeps its own "last used"
  // size instead of fighting over one number.
  window.__notesArrowHeadSize = window.__notesArrowHeadSize || 22;
  window.__notesBoxedArrowHeadSize = window.__notesBoxedArrowHeadSize || 42;
  window.__notesArrowHeadsEnabled = window.__notesArrowHeadsEnabled !== false;
  const buildArrowPathData = (shape, length, height, headSize, headsOn) => shape.arrowFamily === 'boxed'
    ? (BOXED_ARROW_BUILDERS[shape.arrowKind] || BOXED_ARROW_BUILDERS.axis)(length, height, headSize, headsOn)
    : (LINE_ARROW_BUILDERS[shape.arrowKind] || LINE_ARROW_BUILDERS.single)(length, headSize, headsOn);
  /* ROOT CAUSE #1 of "changing arrowhead size teleports the shape elsewhere on the canvas":
     Fabric's own fabric.Path.prototype._setPath (via the shared Polyline._setPositionDimensions)
     computes a BRAND NEW left/top from the new path's bounding box and origin whenever the
     options object passed as its 2nd argument doesn't already include `left`/`top` — a plain
     `shape._setPath(d)` call (no options) always hits that branch, silently relocating the object
     every single time the path is rebuilt. Passing `{ left, top }` (falsy-safe: 0 is a valid
     canvas coordinate, so a plain `anchor ||` fallback would wrongly treat left:0/top:0 as "no
     anchor given") explicitly is what actually prevents that overwrite — Fabric only recomputes
     when the key is `undefined`.
     ROOT CAUSE #2 of "the resized arrowhead doesn't visually update — or only shows up a couple
     of seconds later, after some unrelated interaction, and only sometimes": fabric.Object's
     default objectCaching renders each object to an offscreen bitmap once and reuses it on every
     later frame unless `dirty` is explicitly true — that flag is what tells Fabric "the cached
     bitmap no longer matches this object's actual geometry, re-rasterize it". _setPath rewrites
     the path's POINTS directly, bypassing the normal .set() property setters that would usually
     flip this flag on their own, so without setting it here the canvas keeps painting the stale
     pre-rebuild bitmap — requestRenderAll() still runs every frame, but this ONE object silently
     opts out of actually redrawing until something unrelated happens to dirty it later (a
     selection change, another edit elsewhere) — exactly the "works eventually, at random" symptom,
     and since it depends on incidental later interactions rather than the rebuild itself, it can
     easily look like it "only works for one shape kind" purely by coincidence of what the user
     happened to click next.
     Centralized here so every call site (initial placement drag/click, later interactive resize,
     and the arrowhead controls) rebuilds through the exact same position- and cache-correct
     path, for BOTH families — a bug fixed in only one of them would still bite in the others. */
  const rebuildArrowPath = (shape, length, height, headSize, headsOn, anchor) => {
    const left = anchor ? anchor.left : shape.left, top = anchor ? anchor.top : shape.top;
    shape._setPath(buildArrowPathData(shape, length, height, headSize, headsOn), { left, top });
    shape.set({ scaleX: 1, scaleY: 1 });
    shape.dirty = true;
  };
  /* Settles an arrow-aware shape's geometry back to path-encoded truth: reads the CURRENT visual
     size (width/height * scaleX/scaleY, however that scale got there — a placement drag or a
     later handle resize), rebuilds the path at that exact size with the shape's own fixed
     arrowHeadSize/arrowHeadsEnabled, then resets scaleX/scaleY to 1 so the path itself is the
     source of truth again (no compounding scale left over for the next resize to build on). Only
     ever called at the END of a gesture (object:modified below) — never mid-drag
     (object:scaling) — so Fabric's own live-resize transform math is never interfered with while
     a drag is actually in progress; a brief WYSIWYG-style stretch of the arrowhead DURING the
     drag preview, settling to the true fixed size the instant the mouse is released, is an
     accepted/expected trade-off here, not a bug. */
  const settleArrowGeometry = shape => {
    if (!shape || !shape.arrowKind) return;
    const headSize = shape.arrowHeadSize || (shape.arrowFamily === 'boxed' ? window.__notesBoxedArrowHeadSize : window.__notesArrowHeadSize);
    const headsOn = shape.arrowHeadsEnabled !== false;
    if (shape.arrowFamily === 'boxed') {
      const width = Math.max((shape.width || 1) * Math.abs(shape.scaleX || 1), 8);
      const height = Math.max((shape.height || 1) * Math.abs(shape.scaleY || 1), 8);
      rebuildArrowPath(shape, width, height, headSize, headsOn);
    } else {
      const length = Math.max((shape.width || 1) * Math.abs(shape.scaleX || 1), 8);
      rebuildArrowPath(shape, length, 0, headSize, headsOn);
    }
    shape.setCoords();
  };
  /* ══════════════════════════════════════════════════════════════════════════════════
     SHAPE PICKER — a categorized icon-grid popover (same floating-panel portal as the color
     controls above) replacing the old plain <select> of every shape name in one long flat list.
     `selectedShapeName` replaces `picker.value` as the single source of truth for "which shape
     the Draw-shape button will place next" — every former `picker.value` read below now reads
     this instead. Picking a shape here only updates that selection and the trigger button's own
     face; it deliberately does NOT auto-arm placement mode — the separate "Draw shape" button
     (shapeButton, wired further below) still owns that step exactly as before, so the two-step
     "pick shape, then click Draw Shape, then drag on canvas" flow is unchanged from today. */
  const SHAPE_CATEGORIES = [
    { name: 'Basic Shapes', shapes: ['Rectangle', 'Rounded Rectangle', 'Circle', 'Ellipse', 'Triangle', 'Right Triangle', 'Diamond', 'Parallelogram', 'Trapezoid'] },
    { name: 'Polygons & Stars', shapes: ['Pentagon', 'Hexagon', 'Octagon', 'Star', 'Star 6', 'Cross', 'Heart'] },
    { name: '3D & Special', shapes: ['Semicircle', 'Cylinder', 'Cube', 'Cloud'] },
    { name: 'Lines & Arrows', shapes: ['Line', 'Arrow', 'Double Arrow', 'Curved Arrow', 'Block Arrow'] },
    { name: 'Callouts', shapes: ['Callout', 'Rounded Callout'] },
    { name: 'Diagrams', shapes: ['X-Y Axis'] },
  ];
  let selectedShapeName = 'Rectangle';
  const shapePickerBtn = document.createElement('button');
  shapePickerBtn.type = 'button'; shapePickerBtn.title = 'Shape library'; shapePickerBtn.setAttribute('aria-label', 'Shape library');
  shapePickerBtn.style.cssText = 'width:28px;height:28px;padding:0;border:1px solid var(--border);border-radius:7px;cursor:pointer;background:var(--surface);color:var(--text-1);font-size:15px;line-height:1;display:flex;align-items:center;justify-content:center';
  shapePickerBtn.textContent = shapeGlyphs[selectedShapeName];
  shapePickerBtn.addEventListener('mousedown', event => event.preventDefault());
  insert(shapePickerBtn, 'shape');
  const openShapePicker = () => {
    if (window.__notesReadOnly) return;
    const current = window.__notesGetActiveFloatingPanel?.();
    const alreadyOpen = current?.dataset.notesShapePicker === '1';
    window.__notesCloseFloatingPanels?.();
    if (alreadyOpen) return;
    const panel = document.createElement('div');
    panel.dataset.notesShapePicker = '1';
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Shape library');
    panel.style.cssText = 'position:fixed;z-index:1100;width:288px;max-height:400px;overflow-y:auto;padding:10px 12px;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-lg);font-size:.72rem';
    panel.addEventListener('click', event => event.stopPropagation());
    SHAPE_CATEGORIES.forEach((category, i) => {
      const heading = document.createElement('div');
      heading.textContent = category.name;
      heading.style.cssText = `font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.04em;font-size:.62rem;margin:${i === 0 ? 0 : 12}px 0 6px`;
      panel.append(heading);
      const grid = document.createElement('div');
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(7,1fr);gap:4px';
      category.shapes.forEach(name => {
        const isSelected = name === selectedShapeName;
        const cell = document.createElement('button');
        cell.type = 'button'; cell.title = name; cell.setAttribute('aria-label', name);
        cell.style.cssText = `width:100%;aspect-ratio:1;display:flex;align-items:center;justify-content:center;font-size:16px;border-radius:7px;cursor:pointer;background:${isSelected ? 'var(--accent-subtle)' : 'transparent'};border:1px solid ${isSelected ? 'var(--accent)' : 'transparent'};color:var(--text-1);transition:background .08s ease`;
        cell.textContent = shapeGlyphs[name] || name[0];
        cell.addEventListener('mousedown', event => event.preventDefault());
        cell.addEventListener('mouseenter', () => { if (name !== selectedShapeName) cell.style.background = 'var(--surface-2)'; });
        cell.addEventListener('mouseleave', () => { if (name !== selectedShapeName) cell.style.background = 'transparent'; });
        cell.addEventListener('click', () => {
          selectedShapeName = name;
          shapePickerBtn.textContent = shapeGlyphs[name] || name[0];
          window.__notesCloseFloatingPanels?.();
        });
        grid.append(cell);
      });
      panel.append(grid);
    });
    window.__notesPlaceFloatingPanel?.(panel, shapePickerBtn);
  };
  shapePickerBtn.addEventListener('click', event => { event.stopPropagation(); openShapePicker(); });
  /* Arrowhead size + on/off — a small stepper (same look as the text-size stepper) plus a toggle
     button, placed right next to the shape picker. Works identically for BOTH arrow families
     (line: Arrow/Double Arrow/Curved Arrow; boxed: X-Y Axis/Block Arrow) purely by branching on
     `shape.arrowFamily` — see the registry comment above. Changing the size updates the
     family-appropriate DEFAULT used for the next newly-placed shape of that kind, and — the
     "independently adjustable after the fact" half of the requirement — also live-resizes the
     currently selected shape's head(s) (via the same rebuildArrowPath/_setPath machinery
     settleArrowGeometry uses) while holding its current body size fixed, so body and head are
     each editable without disturbing the other. The toggle does the same for "has an arrowhead at
     all" — off collapses Arrow/Double/Curved to a plain line or curve, X-Y Axis to a plain right
     angle ("just a simple L", no arrowhead), and Block Arrow to a plain bar. Both controls read
     the CURRENT active object's own arrowHeadSize/arrowHeadsEnabled when one is selected, falling
     back to the shared per-family default otherwise. */
  const ARROWHEAD_MIN = 6, ARROWHEAD_MAX = 90;
  const activeHeadDefault = () => canvas.getActiveObject()?.arrowFamily === 'boxed' ? window.__notesBoxedArrowHeadSize : window.__notesArrowHeadSize;
  const clampHeadSize = value => { const n = Math.round(Number(value)); return Number.isFinite(n) ? Math.min(ARROWHEAD_MAX, Math.max(ARROWHEAD_MIN, n)) : activeHeadDefault(); };
  const arrowHeadStepper = document.createElement('div'); arrowHeadStepper.className = 'notes-size-stepper'; arrowHeadStepper.title = 'Arrowhead size';
  const headMinus = document.createElement('button'); headMinus.type = 'button'; headMinus.textContent = '−'; headMinus.setAttribute('aria-label', 'Decrease arrowhead size');
  const headInput = document.createElement('input'); headInput.type = 'number'; headInput.min = String(ARROWHEAD_MIN); headInput.max = String(ARROWHEAD_MAX); headInput.step = '1'; headInput.value = String(window.__notesArrowHeadSize); headInput.setAttribute('aria-label', 'Arrowhead size in pixels');
  const headPlus = document.createElement('button'); headPlus.type = 'button'; headPlus.textContent = '+'; headPlus.setAttribute('aria-label', 'Increase arrowhead size');
  const headUnit = document.createElement('span'); headUnit.className = 'notes-size-unit'; headUnit.textContent = 'px';
  arrowHeadStepper.append(headMinus, headInput, headPlus, headUnit); insert(arrowHeadStepper, 'shape');
  const headsToggleBtn = button('Toggle arrowheads on/off (a shape with them off is just a plain line/right angle/bar)', 'fas fa-location-arrow', () => applyHeadsEnabled(!(canvas.getActiveObject()?.arrowKind ? canvas.getActiveObject().arrowHeadsEnabled !== false : window.__notesArrowHeadsEnabled)), 'shape');
  const rebuildActiveArrow = (active, headSize, headsOn) => {
    if (active.arrowFamily === 'boxed') {
      const width = Math.max((active.width || 1) * Math.abs(active.scaleX || 1), 8);
      const height = Math.max((active.height || 1) * Math.abs(active.scaleY || 1), 8);
      rebuildArrowPath(active, width, height, headSize, headsOn);
    } else {
      const length = Math.max((active.width || 1) * Math.abs(active.scaleX || 1), 8);
      rebuildArrowPath(active, length, 0, headSize, headsOn);
    }
    active.set({ arrowHeadSize: headSize, arrowHeadsEnabled: headsOn });
    active.setCoords();
    syncShapeLabel(active);
    canvas.requestRenderAll();
    canvas.fire('object:modified', { target: active });
  };
  const applyHeadSize = next => {
    if (window.__notesReadOnly) { headInput.value = String(activeHeadDefault()); return; }
    const value = clampHeadSize(next);
    headInput.value = String(value);
    const active = canvas.getActiveObject();
    if (active?.arrowFamily === 'boxed') window.__notesBoxedArrowHeadSize = value; else window.__notesArrowHeadSize = value;
    if (active && active.arrowKind) rebuildActiveArrow(active, value, active.arrowHeadsEnabled !== false);
  };
  const applyHeadsEnabled = enabled => {
    if (window.__notesReadOnly) return;
    window.__notesArrowHeadsEnabled = enabled;
    headsToggleBtn.classList.toggle('active', enabled);
    const active = canvas.getActiveObject();
    if (active && active.arrowKind) rebuildActiveArrow(active, active.arrowHeadSize || activeHeadDefault(), enabled);
  };
  headMinus.addEventListener('mousedown', event => event.preventDefault());
  headPlus.addEventListener('mousedown', event => event.preventDefault());
  headMinus.addEventListener('click', () => applyHeadSize((Number(headInput.value) || activeHeadDefault()) - 2));
  headPlus.addEventListener('click', () => applyHeadSize((Number(headInput.value) || activeHeadDefault()) + 2));
  headInput.addEventListener('change', () => applyHeadSize(headInput.value));
  headInput.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); applyHeadSize(headInput.value); } });
  const syncArrowHeadDisplay = () => {
    const active = canvas.getActiveObject();
    const relevant = !!(active && active.arrowKind);
    headInput.value = String(relevant ? (active.arrowHeadSize || activeHeadDefault()) : activeHeadDefault());
    headsToggleBtn.classList.toggle('active', relevant ? active.arrowHeadsEnabled !== false : window.__notesArrowHeadsEnabled);
  };
  canvas.on('selection:created', syncArrowHeadDisplay); canvas.on('selection:updated', syncArrowHeadDisplay); canvas.on('selection:cleared', syncArrowHeadDisplay);
  syncArrowHeadDisplay();

  /* Font family — common word-processor fonts, applied like Bold/Italic via updateText(). */
  const FONTS = ['DM Sans', 'Arial', 'Helvetica', 'Calibri', 'Cambria', 'Candara', 'Constantia', 'Corbel',
    'Georgia', 'Garamond', 'Times New Roman', 'Verdana', 'Tahoma', 'Trebuchet MS', 'Segoe UI',
    'Century Gothic', 'Book Antiqua', 'Palatino Linotype', 'Courier New', 'Lucida Console',
    'Lucida Sans Unicode', 'Impact', 'Comic Sans MS', 'Franklin Gothic Medium', 'Rockwell',
    'Bookman Old Style', 'Gill Sans', 'Copperplate', 'Papyrus', 'Arial Black'];
  const fontSelect = document.createElement('select'); fontSelect.title = 'Font family';
  FONTS.forEach(name => { const option = new Option(name, name); option.style.fontFamily = name; fontSelect.add(option); });
  fontSelect.style.cssText = 'height:28px;max-width:120px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(fontSelect, 'font');
  fontSelect.addEventListener('change', () => { window.__notesDefaultFont = fontSelect.value; updateText({ fontFamily: fontSelect.value }); });

  /* Integer size control — free typing, +/-1 steps, valid range only. TEXT SIZE ONLY —
     ink stroke width is a fully separate control/state below (window.__notesInkSizes), so
     changing one never affects the other. */
  const SIZE_MIN = 1, SIZE_MAX = 300;
  const clampSize = value => { const n = Math.round(Number(value)); return Number.isFinite(n) ? Math.min(SIZE_MAX, Math.max(SIZE_MIN, n)) : window.__notesDefaultSize || 18; };
  const sizeStepper = document.createElement('div'); sizeStepper.className = 'notes-size-stepper'; sizeStepper.title = 'Text size';
  const sizeMinus = document.createElement('button'); sizeMinus.type = 'button'; sizeMinus.textContent = '−'; sizeMinus.setAttribute('aria-label', 'Decrease text size');
  const sizeInput = document.createElement('input'); sizeInput.type = 'number'; sizeInput.min = String(SIZE_MIN); sizeInput.max = String(SIZE_MAX); sizeInput.step = '1'; sizeInput.value = String(window.__notesDefaultSize || 18); sizeInput.setAttribute('aria-label', 'Text size in pixels');
  const sizePlus = document.createElement('button'); sizePlus.type = 'button'; sizePlus.textContent = '+'; sizePlus.setAttribute('aria-label', 'Increase text size');
  const sizeUnit = document.createElement('span'); sizeUnit.className = 'notes-size-unit'; sizeUnit.textContent = 'px';
  sizeStepper.append(sizeMinus, sizeInput, sizePlus, sizeUnit); insert(sizeStepper, 'font');
  const applySize = next => {
    if (window.__notesReadOnly) { sizeInput.value = String(window.__notesDefaultSize || 18); return; }
    const value = clampSize(next);
    sizeInput.value = String(value);
    window.__notesDefaultSize = value;
    const active = canvas.getActiveObject();
    if (active && ['i-text', 'textbox'].includes(active.type)) updateText({ fontSize: value });
  };
  sizeMinus.addEventListener('click', () => applySize((Number(sizeInput.value) || window.__notesDefaultSize || 18) - 1));
  sizePlus.addEventListener('click', () => applySize((Number(sizeInput.value) || window.__notesDefaultSize || 18) + 1));
  sizeInput.addEventListener('change', () => applySize(sizeInput.value));
  sizeInput.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); applySize(sizeInput.value); } });
  /* Heading presets — reuses the same character-style mechanism as Bold/Italic (no
     block/structural model exists in this plain-text canvas architecture). */
  const headingSelect = document.createElement('select'); headingSelect.title = 'Heading style';
  const HEADING_SIZES = { h1: 28, h2: 22, h3: 18 };
  [['Normal', ''], ['Heading 1', 'h1'], ['Heading 2', 'h2'], ['Heading 3', 'h3']].forEach(([name, value]) => headingSelect.add(new Option(name, value)));
  headingSelect.style.cssText = 'height:28px;max-width:104px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(headingSelect, 'format');
  headingSelect.addEventListener('change', () => { const level = headingSelect.value; updateText({ fontSize: HEADING_SIZES[level] || window.__notesDefaultSize || 18, fontWeight: level ? 'bold' : 'normal' }); headingSelect.value = ''; });
  /* STROKE WIDTH — quick preset buttons, one row per ink tool (a highlighter needs a much
     wider range than a pencil), fully separate from text size (window.__notesDefaultSize). */
  const WIDTH_PRESETS = { pen: [1, 2, 6, 12, 22], pencil: [1, 2, 4, 7], highlighter: [10, 18, 26, 36] };
  const widthRow = document.createElement('div'); widthRow.className = 'notes-width-row'; widthRow.title = 'Stroke width'; insert(widthRow, 'pen');
  const currentInkSize = () => window.__notesInkSizes[window.__notesInkTool] ?? 6;
  const setInkSize = value => {
    if (window.__notesReadOnly) return;
    window.__notesInkSizes[window.__notesInkTool] = value;
    if (canvas.freeDrawingBrush) canvas.freeDrawingBrush.width = value;
    refreshWidthButtons();
    const active = canvas.getActiveObject();
    if (active && active.objectType === 'drawing' && 'strokeWidth' in active) { active.set({ strokeWidth: value }); active.dirty = true; canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
  };
  function refreshWidthButtons() {
    const presets = WIDTH_PRESETS[window.__notesInkTool] || WIDTH_PRESETS.pen;
    widthRow.innerHTML = '';
    // ROOT CAUSE of "the toolbar jumps when the Eraser is selected": `widthRow.hidden = true` sets
    // display:none, which pulls the whole control OUT of the toolbar's flex layout — every group
    // after it (shape/other) then slides left/up to fill the gap. The Eraser has its own size
    // control (eraserSize, the Small/Medium/Large <select> above) so the pen/pencil width presets
    // genuinely aren't applicable here — but "not applicable" only needs to mean *inert*, not
    // *gone*: visibility:hidden keeps the row's box (and therefore every later group's position)
    // exactly where it always is, while still making the now-irrelevant preset dots unclickable.
    widthRow.style.visibility = window.__notesInkTool === 'eraser' ? 'hidden' : '';
    widthRow.style.pointerEvents = window.__notesInkTool === 'eraser' ? 'none' : '';
    const active = currentInkSize();
    presets.forEach(value => {
      const dotBtn = document.createElement('button'); dotBtn.type = 'button'; dotBtn.title = `${value}px`; dotBtn.className = 'notes-width-btn';
      dotBtn.classList.toggle('active', value === active);
      const dot = document.createElement('span'); dot.className = 'notes-width-dot'; const size = Math.max(4, Math.min(18, value)); dot.style.width = `${size}px`; dot.style.height = `${size}px`;
      dotBtn.appendChild(dot); dotBtn.addEventListener('mousedown', event => event.preventDefault()); dotBtn.addEventListener('click', () => { if (window.__notesReadOnly) return; setInkSize(value); });
      widthRow.appendChild(dotBtn);
    });
  }
  const syncSizeDisplay = () => {
    const active = canvas.getActiveObject();
    if (!active) return;
    if (['i-text', 'textbox'].includes(active.type) && active.fontSize) { sizeInput.value = String(Math.round(active.fontSize)); window.__notesDefaultSize = Math.round(active.fontSize); if (active.fontFamily) { fontSelect.value = active.fontFamily; window.__notesDefaultFont = active.fontFamily; } }
  };
  canvas.on('selection:created', syncSizeDisplay); canvas.on('selection:updated', syncSizeDisplay);
  /* Toolbar-reflects-selection (font size/family, Bold/Italic/Underline, text color): the
     ROOT CAUSE this fixes is that syncSizeDisplay above only ever reads the OBJECT-level
     defaults (active.fontSize/fontFamily), and nothing at all updated Bold/Italic/Underline's
     'active' highlight or the color swatch — so the toolbar always showed the box's base style
     (or whatever was last typed elsewhere), never the actual formatting of the current selected
     RANGE, and never changed as the caret moved between differently-formatted runs.
     text.getSelectionStyles(from, to, true) (the same primitive selectionHasFormat/
     buildEditableOverlayMarkup already use) returns the MERGED base+override style per character,
     so reading it here is guaranteed to agree with what's actually rendered. A collapsed caret
     (no selection) reflects the character immediately before it — the same "what would typing
     continue as" convention word processors use; an empty box falls back to the object's own
     base style. A non-uniform (mixed-format) selection shows the FIRST character's value for
     size/family/color (better than the old hardcoded-default bug) and only lights up Bold/
     Italic/Underline when the ENTIRE selection shares that style, matching how
     selectionHasFormat's own toggle direction is already decided. */
  const toHexColor = value => {
    if (!value) return null;
    if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)) return value.length === 4 ? '#' + [...value.slice(1)].map(c => c + c).join('') : value;
    const m = String(value).match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
    if (!m) return null;
    return '#' + [m[1], m[2], m[3]].map(n => Number(n).toString(16).padStart(2, '0')).join('');
  };
  const baseTextStyle = text => ({ fontWeight: text.fontWeight || 'normal', fontStyle: text.fontStyle || 'normal', underline: !!text.underline, fill: text.fill, fontSize: text.fontSize, fontFamily: text.fontFamily });
  const effectiveStyleAt = (text, index) => {
    const base = baseTextStyle(text);
    const len = (text.text || '').length;
    if (!len) return base;
    const clamped = Math.max(0, Math.min(index, len - 1));
    try { const styles = text.getSelectionStyles(clamped, clamped + 1, true); return styles[0] ? { ...base, ...styles[0] } : base; } catch (e) { return base; }
  };
  const selectionRangeStyles = (text, start, end) => {
    const len = (text.text || '').length;
    if (!len) return [effectiveStyleAt(text, 0)];
    if (start === end) return [effectiveStyleAt(text, Math.max(0, start - 1))];
    const from = Math.max(0, Math.min(start, len - 1)), to = Math.max(from + 1, Math.min(end, len));
    try { const styles = text.getSelectionStyles(from, to, true); return styles.length ? styles : [effectiveStyleAt(text, from)]; } catch (e) { return [effectiveStyleAt(text, from)]; }
  };
  let formatButtons = {};
  const syncFormatDisplay = () => {
    const editorState = window.__notesGetNativeEditor?.();
    const text = editorState?.object || canvas.getActiveObject();
    if (!text || !['i-text', 'textbox'].includes(text.type)) return;
    const { start, end } = editorState ? window.__notesTextSelectionOffsets(editorState.element) : { start: text.selectionStart || 0, end: text.selectionEnd || 0 };
    const styles = selectionRangeStyles(text, start, end);
    const first = styles[0];
    formatButtons.bold?.classList.toggle('active', styles.every(s => s.fontWeight === 'bold'));
    formatButtons.italic?.classList.toggle('active', styles.every(s => s.fontStyle === 'italic'));
    formatButtons.underline?.classList.toggle('active', styles.every(s => !!s.underline));
    if (first.fontSize) { sizeInput.value = String(Math.round(first.fontSize)); }
    if (first.fontFamily) { fontSelect.value = first.fontFamily; }
    // Text/ink color swatch reflects the same selection via getActiveInkColor (defined below,
    // reachable here through the closure) — keeps a single source of truth for "what color does
    // this selection actually have" instead of computing it twice.
    inkColorControl.sync();
  };
  window.__notesSyncFormatDisplay = syncFormatDisplay;
  canvas.on('selection:created', syncFormatDisplay); canvas.on('selection:updated', syncFormatDisplay);
  canvas.on('selection:cleared', () => { formatButtons.bold?.classList.remove('active'); formatButtons.italic?.classList.remove('active'); formatButtons.underline?.classList.remove('active'); });
  let erasing = false;
  /* Small, precise crosshair cursor for every ink tool (Pen/Pencil/Highlighter) — a large pen
     icon obstructs handwriting and can't show the exact draw point. Hotspot "10 10" is the
     exact center of the diamond, matching the true Fabric.js drawing coordinate. */
  const inkCursor = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20'%3E%3Ccircle cx='10' cy='10' r='8' fill='white' fill-opacity='.35'/%3E%3Cline x1='10' y1='2' x2='10' y2='6.5' stroke='%235a6472' stroke-width='1.3' stroke-linecap='round'/%3E%3Cline x1='10' y1='13.5' x2='10' y2='18' stroke='%235a6472' stroke-width='1.3' stroke-linecap='round'/%3E%3Cline x1='2' y1='10' x2='6.5' y2='10' stroke='%235a6472' stroke-width='1.3' stroke-linecap='round'/%3E%3Cline x1='13.5' y1='10' x2='18' y2='10' stroke='%235a6472' stroke-width='1.3' stroke-linecap='round'/%3E%3Cpath d='M10 7.3 L12.7 10 L10 12.7 L7.3 10 Z' fill='%23ffffff' stroke='%235a6472' stroke-width='1.1'/%3E%3C/svg%3E\") 10 10, crosshair";
  const eraserCursor = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24'%3E%3Cpath d='M4 16 13 5l7 7-9 9H4z' fill='%23ffffff' stroke='%235a6472' stroke-width='2'/%3E%3Cpath d='M3 21h12' stroke='%235a6472' stroke-width='2'/%3E%3C/svg%3E\") 4 20, crosshair";
  let inkButtons = {};
  const setActiveInkButton = kind => { Object.entries(inkButtons).forEach(([k, btn]) => btn.classList.toggle('active', k === kind)); };
  /* Pen / Pencil / Highlighter share one freehand-brush code path — only the resulting
     stroke's color/width/blend differ (tagged at creation time in editor.js's path:created
     handler via window.__notesInkTool, so eraser/save/undo logic needs zero special-casing:
     every stroke is still just an objectType:'drawing' fabric.Path). */
  const activateBrush = kind => {
    erasing = false; window.__notesEraserActive = false; window.__notesInkTool = kind;
    canvas.selection = false; canvas.isDrawingMode = true;
    canvas.freeDrawingCursor = inkCursor; canvas.defaultCursor = inkCursor; canvas.hoverCursor = inkCursor;
    canvas.freeDrawingBrush = new fabric.PencilBrush(canvas);
    /* ROOT CAUSE of "Pencil looks dark while drawing, then turns lighter the instant you lift
       the pen": the live in-progress stroke is painted straight from this brush color — PencilBrush
       has no separate "opacity" of its own — while the finished stroke used to get its lighter look
       from a fabric.Object opacity applied only after path:created (see editor.js), a moment the
       live preview never went through. Baking the same alpha into the brush's own color (exactly
       how Highlighter already gets its live translucency below) makes the live preview and the
       finished stroke identical from the very first pixel, with no post-hoc opacity step needed. */
    canvas.freeDrawingBrush.color = kind === 'highlighter' ? hexToRgba(window.__notesHighlightColor || '#ffff00', .35) : kind === 'pencil' ? hexToRgba(window.__notesInkColor || '#000000', .82) : (window.__notesInkColor || '#000000');
    canvas.freeDrawingBrush.width = currentInkSize();
    setActiveInkButton(kind);
    refreshWidthButtons();
  };
  const eraseAt = pointer => {
    const radius = Number(eraserSize.value) / 2;
    canvas.getObjects().filter(object => object.objectType === 'drawing').filter(object => {
      const box = object.getBoundingRect(true, true); const nearestX = Math.max(box.left, Math.min(pointer.x, box.left + box.width)); const nearestY = Math.max(box.top, Math.min(pointer.y, box.top + box.height)); return (pointer.x - nearestX) ** 2 + (pointer.y - nearestY) ** 2 <= radius ** 2;
    }).forEach(object => canvas.remove(object));
    canvas.requestRenderAll();
  };
  const activateEraser = () => { erasing = true; window.__notesEraserActive = true; window.__notesInkTool = 'eraser'; canvas.isDrawingMode = false; canvas.selection = false; canvas.defaultCursor = eraserCursor; canvas.hoverCursor = eraserCursor; setActiveInkButton('eraser'); refreshWidthButtons(); };
  // Called by editor.js's setTool() when switching to Select/Text/Sticky/Image — otherwise
  // the Pen/Eraser highlighting (and the eraser's own armed state) would stay stuck "active"
  // even though the canvas is no longer in drawing/erasing mode.
  window.__notesDeactivateInkTools = () => { erasing = false; setActiveInkButton(null); };
  /* Text Highlight (background on the SELECTED characters only) — distinct from the freehand
     Highlighter ink tool above, which draws translucent strokes on the canvas itself. Reads the
     selection the same live way applyTextColor() does (window.__notesGetNativeEditor's overlay,
     not a possibly-stale object.selectionStart) so it stays correctly scoped even right after
     picking a color from the swatch/picker mid-edit. */
  const applyHighlight = () => {
    const editorState = window.__notesGetNativeEditor?.();
    const text = editorState?.object || canvas.getActiveObject();
    if (!text || !['i-text', 'textbox'].includes(text.type)) return;
    const { start, end } = editorState ? window.__notesTextSelectionOffsets(editorState.element) : { start: text.selectionStart || 0, end: text.selectionEnd || 0 };
    if (start === end) return;
    const hex = window.__notesHighlightColor || '#ffff00';
    text.setSelectionStyles({ textBackgroundColor: hex }, start, end);
    if (editorState) document.execCommand('backColor', false, hex);
    text.initDimensions(); canvas.fire('object:modified', { target: text }); canvas.requestRenderAll();
    highlightColorControl.sync();
  };
  /* Bullet / numbered lists — this is a plain-text canvas (no block/structural text
     model), so "list" means toggling a line-prefix, applied to EVERY line the current
     selection spans, on the live contenteditable overlay, pushed back via its own sync(). */
  const LIST_PREFIX_RE = /^([•◦▪–✔]\s|\d+\.\s)/;
  const toggleLinePrefix = prefix => {
    const editorState = window.__notesGetNativeEditor?.();
    if (!editorState) return;
    const { element, sync } = editorState;
    const text = element.innerText.replace(/\n$/, '');
    const offsets = window.__notesTextSelectionOffsets(element);
    let end = Math.max(offsets.start, offsets.end);
    if (end > offsets.start && text[end - 1] === '\n') end -= 1;
    const lineStart = text.lastIndexOf('\n', Math.max(0, offsets.start - 1)) + 1;
    const blockEndIdx = text.indexOf('\n', end);
    const blockEnd = blockEndIdx === -1 ? text.length : blockEndIdx;
    const lines = text.slice(lineStart, blockEnd).split('\n');
    const isNumbered = /^\d+\.\s/.test(prefix);
    const allPrefixed = lines.every(l => l.startsWith(prefix) || (isNumbered && LIST_PREFIX_RE.test(l)));
    const nextLines = lines.map((l, i) => {
      const stripped = l.replace(LIST_PREFIX_RE, '');
      return allPrefixed ? stripped : (isNumbered ? `${i + 1}. ${stripped}` : prefix + stripped);
    });
    const nextBlock = nextLines.join('\n');
    const oldBlock = lines.join('\n');
    element.innerText = text.slice(0, lineStart) + nextBlock + text.slice(blockEnd);
    window.__notesSetCaretOffset(element, Math.max(lineStart, offsets.end + (nextBlock.length - oldBlock.length)));
    element.focus(); sync();
  };
  /* Google Docs-style shape insertion: the toolbar button arms placement
     mode (crosshair, no selection) instead of dropping a shape immediately;
     the user then drags out the shape's bounding box on the canvas, and the
     tool automatically returns to normal/select mode once it's placed. */
  let placingShape = false, shapeDraft = null, shapeButton = null;
  const exitShapePlacement = () => {
    placingShape = false; shapeDraft = null; window.__notesShapeToolActive = false;
    canvas.selection = true; canvas.defaultCursor = 'default'; canvas.hoverCursor = 'move';
    shapeButton?.classList.remove('active');
  };
  const beginShapePlacement = () => {
    erasing = false; window.__notesEraserActive = false; canvas.isDrawingMode = false; canvas.selection = false;
    placingShape = true; window.__notesShapeToolActive = true;
    canvas.defaultCursor = 'crosshair'; canvas.hoverCursor = 'crosshair';
    shapeButton?.classList.add('active');
  };
  /* ROOT CAUSE of "text/ink goes behind a shape and becomes hidden": every object's stacking
     order is purely creation-order (Fabric appends canvas.add() to the TOP of the draw stack,
     unconditionally) — there's no overlap awareness anywhere. A freshly-drawn shape is added at
     mouse:down (see the placingShape branch in the mouse:down handler below) and, by the time
     it's finalized here, may now visually cover pre-existing text/sticky-note/ink content that
     was on the canvas first, purely because the shape happens to be newer. Fixing this generally
     ("all text/ink always on top") would break normal layering (e.g. a shape deliberately drawn
     over an old, no-longer-relevant note) — so instead, this only nudges THIS ONE newly
     finalized shape to sit just behind whatever specific text/sticky/ink objects it now overlaps,
     the moment its real final position/size is known (dragging/resizing afterward doesn't
     re-run this — matches "moving/resizing must PRESERVE correct layering", not continuously
     recompute it). canvas.moveTo only ever changes array position (which IS the source of
     truth for both rendering order and the z_index persisted on save — see objectRecord/save()
     below), so this participates in serialization/undo through the exact same existing paths,
     no special-casing needed. 'drawing' (freehand pen/pencil/highlighter strokes) was originally
     missing from this list — a shape drawn over existing ink would bury it completely, which is
     the concrete case behind the "text/ink hidden behind shape" regression report. */
  const sendBehindOverlappingText = shape => {
    shape.setCoords();
    const shapeBox = shape.getBoundingRect(true, true);
    const overlaps = (a, b) => a.left < b.left + b.width && b.left < a.left + a.width && a.top < b.top + b.height && b.top < a.top + a.height;
    const objects = canvas.getObjects();
    const overlappingIndexes = objects.reduce((indexes, object, index) => {
      if (object === shape || !['rich_text', 'sticky_note', 'drawing'].includes(object.objectType)) return indexes;
      object.setCoords();
      if (overlaps(shapeBox, object.getBoundingRect(true, true))) indexes.push(index);
      return indexes;
    }, []);
    if (!overlappingIndexes.length) return;
    canvas.moveTo(shape, Math.min(...overlappingIndexes));
  };
  /* Real handwriting is virtually never a single fabric.Path — lifting the mouse/pen between
     letters or words (exactly how PencilBrush works) ends one path and starts a new one, so a
     handwritten phrase is a cluster of separate 'drawing' objects. Selecting "the whole phrase"
     (a rubber-band drag across all of them, or shift-click) groups them into a Fabric
     ActiveSelection for the drag — and while grouped, each member's own left/top are relative to
     the SELECTION's center, not the canvas (see objectRecord's absolute-position fix elsewhere in
     this codebase for the same phenomenon). getBoundingRect(true, true) does not correct for this
     either — confirmed empirically during that earlier fix. Used both for a lone object (where it
     just degrades to the plain box) and for each member of an ActiveSelection. */
  const absoluteBoundingRect = object => {
    object.setCoords();
    const box = object.getBoundingRect(true, true);
    if (!object.group) return box;
    const dx = box.left - object.left, dy = box.top - object.top;
    const origin = fabric.util.transformPoint({ x: object.left, y: object.top }, object.group.calcTransformMatrix());
    return { left: origin.x + dx, top: origin.y + dy, width: box.width, height: box.height };
  };
  const boxesOverlap = (a, b) => a.left < b.left + b.width && b.left < a.left + a.width && a.top < b.top + b.height && b.top < a.top + a.height;
  /* Mirror of sendBehindOverlappingText, for the reverse situation: the content already exists,
     the shape already exists, and the user then drags/resizes the text/sticky/ink object so it
     now overlaps a shape that happens to sit ABOVE it in the stacking order (e.g. the shape was
     simply created after the content, in an unrelated spot, then the content was moved onto it
     later — sendBehindOverlappingText never runs again for that shape once it's finalized, so
     nothing had ever revisited this case). Without this, z-order stays frozen at whatever it was
     the moment each object was created, so dragging older content onto a newer shape buries it
     — reproducible with plain text just as much as ink; it was never actually type-specific, only
     ordering-specific, which is why moving text onto a shape created AFTER that text (the
     originally-tested case) looked fine while the reverse order didn't. Only nudges the moved
     object above the SPECIFIC shape(s) it now overlaps — not to the absolute top of the canvas —
     so unrelated layering is left alone, same "smallest necessary reorder" contract as
     sendBehindOverlappingText above. Operates on ONE plain (non-selection) object at a time —
     multi-stroke handwriting dragged as a group is fanned out into individual calls to this by
     bringAboveOverlappingShapes below, each using its own true canvas-absolute box via
     absoluteBoundingRect so a still-grouped member's position is never misread. */
  const bringOneAboveOverlappingShapes = object => {
    if (!object || !['rich_text', 'sticky_note', 'drawing'].includes(object.objectType)) return;
    const box = absoluteBoundingRect(object);
    const objects = canvas.getObjects();
    const objectIndex = objects.indexOf(object);
    const overlappingShapeIndexes = objects.reduce((indexes, other, index) => {
      if (index <= objectIndex || other === object || other.objectType !== 'shape' || other.shapeTextFor) return indexes;
      if (boxesOverlap(box, absoluteBoundingRect(other))) indexes.push(index);
      return indexes;
    }, []);
    if (!overlappingShapeIndexes.length) return;
    canvas.moveTo(object, Math.max(...overlappingShapeIndexes));
  };
  /* Entry point wired to 'object:modified' below. A plain drag/resize fires this with the object
     itself as target — handled directly. Dragging a multi-object selection (the "select the whole
     handwritten phrase, then drag" case above) fires it with the ActiveSelection as target, whose
     own objectType is undefined, so without this branch it would silently no-op for every member —
     exactly the gap reported. Each member is evaluated independently (its own overlap, its own
     reorder), same "smallest necessary reorder" contract as the single-object case; members are
     re-read from canvas.getObjects() fresh on every call so an earlier member's own moveTo in this
     same pass can never leave a later member's index calculation stale. */
  const bringAboveOverlappingShapes = target => {
    if (!target) return;
    const members = target.type === 'activeSelection' && typeof target.getObjects === 'function' ? target.getObjects() : [target];
    members.forEach(bringOneAboveOverlappingShapes);
  };
  const finalizeShape = (shape, centerX, centerY, keepOrigin = false) => {
    const shapeId = shape.objectId || id();
    shape.set(keepOrigin ? { left: centerX, top: centerY, objectId: shapeId, objectType: 'shape' } : { left: centerX, top: centerY, originX: 'center', originY: 'center', objectId: shapeId, objectType: 'shape' });
    shape.setCoords();
    sendBehindOverlappingText(shape);
    const label = new fabric.Textbox('', { left: shape.left, top: shape.top, originX: 'center', originY: 'center', width: Math.max(80, (shape.width || 160) * Math.abs(shape.scaleX || 1) * .78), fontFamily: 'DM Sans', fontSize: 18, lineHeight: 1.2, textAlign: 'center', fill: getComputedStyle(document.documentElement).getPropertyValue('--text-1').trim(), editable: false, objectCaching: false, charSpacing: 1, objectId: id(), objectType: 'shape', shapeTextFor: shapeId });
    shape.shapeTextId = label.objectId; canvas.add(label); canvas.setActiveObject(shape); canvas.requestRenderAll();
  };
  const syncShapeLabel = shape => { if (!shape?.shapeTextId) return; const label = canvas.getObjects().find(object => object.objectId === shape.shapeTextId); if (!label) return; label.set({ left: shape.left, top: shape.top, width: Math.max(80, (shape.width || 160) * Math.abs(shape.scaleX || 1) * .78), scaleX: 1, scaleY: 1, angle: shape.angle }); label.initDimensions(); label.setCoords(); };
  const selectedText = () => { const object = canvas.getActiveObject(); return object && ['i-text', 'textbox'].includes(object.type) ? object : null; };
  /* fontSize/fontFamily have no document.execCommand equivalent that maps to real CSS pixel
     sizes/font names (the legacy 'fontSize'/'fontName' commands only support HTML's 1-7 relative
     scale), so previewOverlayFormat wraps the current DOM selection in a styled <span> by hand
     instead — same end result as execCommand's own approach (wrap the selected text nodes),
     just for the two properties execCommand can't express directly. Re-reads the selection fresh
     each call (not cached) so it composes correctly when chained after a bold/italic/underline/
     color execCommand already wrapped the same text in <b>/<font> tags moments earlier. */
  const wrapSelectionWithStyle = styleProps => {
    const selection = window.getSelection();
    if (!selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (range.collapsed) return;
    const span = document.createElement('span');
    Object.assign(span.style, styleProps);
    try {
      range.surroundContents(span);
    } catch (e) {
      // surroundContents throws if the range's boundaries fall inside different elements
      // (e.g. it partially overlaps a <b>/<font> tag from a just-applied format) — extractContents
      // handles arbitrarily-structured ranges, so fall back to it for that case.
      span.appendChild(range.extractContents());
      range.insertNode(span);
    }
    const restored = document.createRange();
    restored.selectNodeContents(span);
    selection.removeAllRanges();
    selection.addRange(restored);
  };
  /* Live preview during active editing: the contenteditable overlay has no notion of Fabric's
     per-character `styles` map, so without this a range-scoped Bold/Italic/Underline is
     invisible until the user finishes editing — easy to mistake for "nothing happened" (or,
     worse, to then act as if the whole box needs formatting instead). execCommand only ever
     touches the DOM text NODES' presentation (never the extracted plain-text sync() reads),
     mirroring the same technique applyTextColor() already uses for foreColor. */
  const previewOverlayFormat = changes => {
    if (!window.__notesGetNativeEditor?.()) return;
    if ('fontWeight' in changes) document.execCommand('bold');
    if ('fontStyle' in changes) document.execCommand('italic');
    if ('underline' in changes) document.execCommand('underline');
    if ('fontSize' in changes) wrapSelectionWithStyle({ fontSize: `${changes.fontSize * canvas.getZoom()}px` });
    if ('fontFamily' in changes) wrapSelectionWithStyle({ fontFamily: changes.fontFamily });
  };
  /* Applies a formatting change to exactly the current selection (per-character `styles` map
     via setSelectionStyles), or to the whole object when nothing is selected (legacy
     convention, unchanged). text.selectionStart/selectionEnd are kept live by the
     native-text-editor overlay's own sync() (see beginNativeTextEdit in editor.js) on every
     input/keyup/mouseup, so they're still correct here even right after a toolbar control
     briefly steals DOM focus away from the overlay. */
  const updateText = changes => {
    if (window.__notesReadOnly) return;
    const text = selectedText();
    if (!text) return;
    const hasSelection = text.selectionStart !== text.selectionEnd;
    if (hasSelection) { text.setSelectionStyles(changes, text.selectionStart, text.selectionEnd); previewOverlayFormat(changes); }
    else text.set(changes);
    text.initDimensions();
    window.__notesSyncActiveTextOverlay?.();
    canvas.fire('object:modified', { target: text });
    canvas.requestRenderAll();
    syncFormatDisplay();
  };
  /* Bold/Italic/Underline need to know the CURRENT state at the selection/caret to decide which
     way to toggle — reading only the object-level property (the old bug) was wrong whenever the
     caret sat inside an already-styled run that overrides the object default. */
  const selectionHasFormat = (text, prop, value) => {
    const start = text.selectionStart || 0, end = text.selectionEnd || 0;
    if (start === end) return text[prop] === value;
    const styles = text.getSelectionStyles(start, end, true);
    return styles.length > 0 && styles.every(s => s[prop] === value);
  };
  canvas.on('object:scaling', event => syncShapeLabel(event.target)); canvas.on('object:modified', event => { settleArrowGeometry(event.target); syncShapeLabel(event.target); bringAboveOverlappingShapes(event.target); });
  canvas.on('mouse:dblclick', event => { if (window.__notesReadOnly) return; const shape = event.target; if (!shape?.shapeTextId) return; const label = canvas.getObjects().find(object => object.objectId === shape.shapeTextId); if (!label) return; canvas.setActiveObject(label); window.__notesBeginNativeTextEdit?.(label); });
  canvas.on('mouse:down', event => {
    if (window.__notesReadOnly) return;
    if (erasing) { eraseAt(canvas.getPointer(event.e)); return; }
    if (placingShape) {
      const pointer = canvas.getPointer(event.e);
      const shape = shapes[selectedShapeName]();
      const isLine = LINE_SHAPES.has(selectedShapeName);
      // noFill (LINE_SHAPES or NO_FILL_SHAPES) drives color semantics — no fill, stroke-only
      // recoloring via the ink/text color control; isLine alone (LINE_SHAPES only) drives
      // PLACEMENT/scaling — direction+length vs a normal independent-width/height box drag. An
      // X-Y axis is noFill but not isLine: no fill swatch, yet still boxes out like Rectangle.
      const noFill = isLine || NO_FILL_SHAPES.has(selectedShapeName);
      shape.strokeOnly = noFill;
      const lineArrowKind = LINE_ARROW_KINDS[selectedShapeName], boxedArrowKind = BOXED_ARROW_KINDS[selectedShapeName];
      if (lineArrowKind) { shape.arrowKind = lineArrowKind; shape.arrowFamily = 'line'; shape.arrowHeadSize = window.__notesArrowHeadSize; shape.arrowHeadsEnabled = window.__notesArrowHeadsEnabled; }
      else if (boxedArrowKind) { shape.arrowKind = boxedArrowKind; shape.arrowFamily = 'boxed'; shape.arrowHeadSize = window.__notesBoxedArrowHeadSize; shape.arrowHeadsEnabled = window.__notesArrowHeadsEnabled; }
      // window.__notesShapeColor may be null (user's default is currently "No Fill") — valid
      // for a closed shape's fill, but a line/arrow's (or any shape's) STROKE must never be
      // null or the shape becomes fully invisible, so the stroke always falls back to a real
      // color even while the fill default is "no fill".
      const fillDefault = window.__notesShapeColor;
      // A border/stroke color independent of fill (see the dedicated Border color control below)
      // means the stroke should default to whatever the user last picked THERE, not silently
      // re-derive from the fill default every time a new shape is drawn.
      const strokeDefault = window.__notesShapeBorderColor || (fillDefault === null ? '#4a86e8' : fillDefault);
      // strokeUniform: true is the ROOT CAUSE fix for "a No-Fill (or any) shape's border gets
      // visibly thicker/distorted when resized non-uniformly" — Fabric scales the stroke's own
      // geometry right along with scaleX/scaleY by default, so a wide-but-short resize (differing
      // scaleX vs scaleY) stretches the border unevenly instead of keeping a constant pixel width.
      // strokeUniform disables that coupling, rendering the stroke at its literal strokeWidth
      // regardless of the shape's current scale/aspect ratio. Applies to every shape (not just
      // No-Fill ones) since the distortion isn't fill-state-specific, only most visible on a
      // stroke-only shape where the border IS the whole visual.
      shape.set({ left: pointer.x, top: pointer.y, fill: noFill ? null : fillDefault, stroke: strokeDefault, strokeWidth: isLine ? 3 : 2, strokeUniform: true });
      if (isLine) shape.set({ originX: 'left', originY: 'center' });
      else shape.set({ originX: 'left', originY: 'top' });
      canvas.add(shape);
      shapeDraft = { shape, isLine, startX: pointer.x, startY: pointer.y, naturalWidth: Math.max(shape.width || 1, 1), naturalHeight: Math.max(shape.height || 1, 1) };
      canvas.requestRenderAll();
    }
  });
  canvas.on('mouse:move', event => {
    if (window.__notesReadOnly) return;
    if (erasing && event.e.buttons) { eraseAt(canvas.getPointer(event.e)); return; }
    if (placingShape && shapeDraft) {
      const pointer = canvas.getPointer(event.e);
      if (shapeDraft.isLine) {
        // Line-type shapes: point along the actual drag direction/length, not a bbox stretch.
        const dx = pointer.x - shapeDraft.startX, dy = pointer.y - shapeDraft.startY;
        const length = Math.max(Math.hypot(dx, dy), 4);
        const angle = Math.atan2(dy, dx) * 180 / Math.PI;
        if (shapeDraft.shape.arrowKind) {
          // Arrow/Double Arrow/Curved Arrow: rebuild the actual path at this length (fixed
          // arrowHeadSize) instead of stretching a template via scaleX — see the registry
          // comment above for why a scale-based approach can't keep the arrowhead a constant size.
          rebuildArrowPath(shapeDraft.shape, length, 0, shapeDraft.shape.arrowHeadSize, shapeDraft.shape.arrowHeadsEnabled !== false, { left: shapeDraft.startX, top: shapeDraft.startY });
          shapeDraft.shape.set({ angle });
        } else {
          shapeDraft.shape.set({ left: shapeDraft.startX, top: shapeDraft.startY, scaleX: length / shapeDraft.naturalWidth, scaleY: 1, angle });
        }
      } else {
        const left = Math.min(pointer.x, shapeDraft.startX), top = Math.min(pointer.y, shapeDraft.startY);
        const width = Math.abs(pointer.x - shapeDraft.startX), height = Math.abs(pointer.y - shapeDraft.startY);
        if (shapeDraft.shape.arrowFamily === 'boxed') {
          // X-Y Axis / Block Arrow: same "rebuild the real geometry, don't scale a template"
          // treatment as the line-arrow branch above, just driven by independent width+height
          // instead of one direction+length.
          rebuildArrowPath(shapeDraft.shape, Math.max(width, 8), Math.max(height, 8), shapeDraft.shape.arrowHeadSize, shapeDraft.shape.arrowHeadsEnabled !== false, { left, top });
        } else {
          shapeDraft.shape.set({ left, top, scaleX: Math.max(width, 4) / shapeDraft.naturalWidth, scaleY: Math.max(height, 4) / shapeDraft.naturalHeight });
        }
      }
      shapeDraft.shape.setCoords();
      canvas.requestRenderAll();
    }
  });
  canvas.on('mouse:up', event => {
    if (window.__notesReadOnly) return;
    if (erasing) { canvas.selection = true; return; }
    if (placingShape && shapeDraft) {
      const { shape, startX, startY, isLine } = shapeDraft;
      const pointer = canvas.getPointer(event.e);
      const draggedWidth = Math.abs(pointer.x - startX), draggedHeight = Math.abs(pointer.y - startY);
      const isClick = draggedWidth < 6 && draggedHeight < 6;
      if (isLine) {
        if (isClick) {
          if (shape.arrowKind) { rebuildArrowPath(shape, 100, 0, shape.arrowHeadSize, shape.arrowHeadsEnabled !== false); shape.set({ angle: 0 }); }
          else shape.set({ scaleX: 100 / shapeDraft.naturalWidth, scaleY: 1, angle: 0 });
        }
        finalizeShape(shape, shape.left, shape.top, true);
      } else {
        const centerX = isClick ? startX : Math.min(pointer.x, startX) + draggedWidth / 2;
        const centerY = isClick ? startY : Math.min(pointer.y, startY) + draggedHeight / 2;
        if (isClick) {
          // A plain click (no drag) places the shape at a sensible default size — for
          // X-Y Axis/Block Arrow that's a real path rebuild (matching how every other
          // arrow-aware shape's click-to-place default works), not just resetting scale to 1,
          // since their static factory template is only ever a placeholder overwritten on first
          // use anyway (see the `shapes` entries' own comments).
          if (shape.arrowFamily === 'boxed') { const [w, h] = shape.arrowKind === 'blockArrow' ? [170, 90] : [160, 160]; rebuildArrowPath(shape, w, h, shape.arrowHeadSize, shape.arrowHeadsEnabled !== false); }
          else shape.set({ scaleX: 1, scaleY: 1 });
        }
        finalizeShape(shape, centerX, centerY);
      }
      exitShapePlacement();
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !placingShape) return;
    if (shapeDraft) canvas.remove(shapeDraft.shape);
    exitShapePlacement();
    canvas.requestRenderAll();
  });
  formatButtons = {
    bold: button('Bold selected text', 'fas fa-bold', () => { const text = selectedText(); if (text) updateText({ fontWeight: selectionHasFormat(text, 'fontWeight', 'bold') ? 'normal' : 'bold' }); }, 'format'),
    italic: button('Italic selected text', 'fas fa-italic', () => { const text = selectedText(); if (text) updateText({ fontStyle: selectionHasFormat(text, 'fontStyle', 'italic') ? 'normal' : 'italic' }); }, 'format'),
    underline: button('Underline selected text', 'fas fa-underline', () => { const text = selectedText(); if (text) updateText({ underline: !selectionHasFormat(text, 'underline', true) }); }, 'format'),
  };
  const bulletStyle = document.createElement('select'); bulletStyle.title = 'Bullet style';
  [['•', '• '], ['◦', '◦ '], ['▪', '▪ '], ['–', '– '], ['✔', '✔ ']].forEach(([label, value]) => bulletStyle.add(new Option(label, value)));
  bulletStyle.style.cssText = 'height:28px;max-width:44px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(bulletStyle, 'format');
  button('Bullet list', 'fas fa-list-ul', () => toggleLinePrefix(bulletStyle.value), 'format');
  button('Numbered list', 'fas fa-list-ol', () => toggleLinePrefix('1. '), 'format');
  button('Align left', 'fas fa-align-left', () => updateText({ textAlign: 'left' }), 'format');
  button('Center text', 'fas fa-align-center', () => updateText({ textAlign: 'center' }), 'format');
  button('Align right', 'fas fa-align-right', () => updateText({ textAlign: 'right' }), 'format');
  button('Justify text', 'fas fa-align-justify', () => updateText({ textAlign: 'justify' }), 'format');
  button('Highlight selected text', 'fas fa-highlighter', applyHighlight, 'color');
  inkButtons = {
    pen: button('Pen', 'fas fa-pen', () => activateBrush('pen'), 'pen'),
    pencil: button('Pencil', 'fas fa-pencil-alt', () => activateBrush('pencil'), 'pen'),
    // Deliberately a different icon from "Highlight selected text" below (fa-highlighter) —
    // the two are easy to conflate (same word, same yellow-marker concept) but do very
    // different things: this draws a translucent freehand stroke on the canvas; that one
    // colors the background of a text selection. A shared icon was making it easy to reach
    // for this tool while actually meaning the other, coloring far more than intended.
    highlighter: button('Highlighter pen (draws over handwriting/drawings)', 'fas fa-marker', () => activateBrush('highlighter'), 'pen'),
    eraser: button('Eraser', 'fas fa-eraser', activateEraser, 'pen'),
  };
  // Select is the true active tool at load (see the static "select" data-tool button in
  // editor.html) — no ink tool is actually engaged yet, so none of these show as active
  // until the user picks one. Only the width row needs an initial render.
  refreshWidthButtons();
  shapeButton = button('Draw shape (click-drag on canvas)', 'far fa-square', beginShapePlacement, 'shape');
  // Text/ink, Highlight, and Fill/background colors' own apply paths (applyInkColor,
  // applyHighlightColor, applyShapeFillColor) and their selection-sync (…Control.sync) are all
  // wired up above, right where each swatch button/popover is built — see the "COLOR PICKER
  // SYSTEM" block near the top of this file.
}, 300);