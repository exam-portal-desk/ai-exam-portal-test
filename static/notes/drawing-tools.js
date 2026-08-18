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
  window.__notesShapeColor = window.__notesShapeColor || getComputedStyle(document.documentElement).getPropertyValue('--accent-subtle').trim();
  window.__notesStickyBgColor = window.__notesStickyBgColor || getComputedStyle(document.documentElement).getPropertyValue('--warning-bg').trim();
  /* Ink tool state — 'pen' | 'pencil' | 'highlighter' | 'eraser'. Exposed on window (like the
     other __notes* flags already used across editor.js/drawing-tools.js) so editor.js's single
     path:created handler can read it to tag a freshly-drawn stroke — no second listener needed.
     Each ink tool remembers its own stroke width independently, so switching tools never makes
     you re-pick a width (a highlighter needs to stay wide even after a thin pen stroke). */
  window.__notesInkTool = window.__notesInkTool || 'pen';
  window.__notesInkSizes = window.__notesInkSizes || { pen: 6, pencil: 3, highlighter: 22 };
  const hexToRgba = (hex, alpha) => { const v = hex.replace('#', ''); const full = v.length === 3 ? v.split('').map(c => c + c).join('') : v; const n = parseInt(full, 16); return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`; };

  const color = document.createElement('input'); color.type = 'color'; color.value = '#000000'; color.title = 'Text / ink color'; color.style.cssText = 'width:28px;height:28px;padding:2px;border:1px solid var(--border);border-radius:7px;background:var(--surface)'; insert(color, 'color');
  const highlightColor = document.createElement('input'); highlightColor.type = 'color'; window.__notesHighlightColor = window.__notesHighlightColor || '#ffff00'; highlightColor.value = window.__notesHighlightColor; highlightColor.title = 'Highlight color (text highlight & Highlighter pen)'; highlightColor.style.cssText = 'width:28px;height:28px;padding:2px;border:1px solid var(--border);border-radius:7px;background:var(--surface)'; highlightColor.addEventListener('input', () => { window.__notesHighlightColor = highlightColor.value; if (canvas.freeDrawingBrush && window.__notesInkTool === 'highlighter') canvas.freeDrawingBrush.color = hexToRgba(highlightColor.value, .35); }); insert(highlightColor, 'color');
  /* Quick ink-color swatches for Pen/Pencil — sets the same `color` input used everywhere
     else (recoloring a selected drawing, text color, live brush color), so there's exactly
     one source of truth; a swatch click just drives that existing input. */
  const INK_SWATCHES = ['#1a1a1a', '#c0392b', '#e67e22', '#2980b9', '#27ae60', '#8e44ad'];
  const inkPalette = document.createElement('div'); inkPalette.title = 'Quick ink colors'; inkPalette.style.cssText = 'display:flex;gap:3px;align-items:center';
  INK_SWATCHES.forEach(hex => { const swatch = document.createElement('button'); swatch.type = 'button'; swatch.title = hex; swatch.setAttribute('aria-label', `Ink color ${hex}`); swatch.style.cssText = `width:16px;height:16px;border-radius:50%;border:1px solid var(--border);background:${hex};padding:0;cursor:pointer`; swatch.addEventListener('mousedown', event => event.preventDefault()); swatch.addEventListener('click', () => { if (window.__notesReadOnly) return; color.value = hex; color.dispatchEvent(new Event('input')); }); inkPalette.appendChild(swatch); }); insert(inkPalette, 'color');
  const fillColor = document.createElement('input'); fillColor.type = 'color'; fillColor.value = window.__notesShapeColor.startsWith('#') ? window.__notesShapeColor : '#4a86e8'; fillColor.title = 'Fill / background color (shapes, sticky note background)'; fillColor.style.cssText = 'width:28px;height:28px;padding:2px;border:1px solid var(--border);border-radius:7px;background:var(--surface)'; insert(fillColor, 'shape');
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
    'Block Arrow': () => new fabric.Polygon([{ x: 0, y: 25 }, { x: 100, y: 25 }, { x: 100, y: 0 }, { x: 170, y: 45 }, { x: 100, y: 90 }, { x: 100, y: 65 }, { x: 0, y: 65 }]),
    Callout: () => new fabric.Path('M 0 0 L 170 0 L 170 95 L 58 95 L 25 125 L 38 95 L 0 95 z'),
    'Rounded Callout': () => new fabric.Path('M 20 0 L 150 0 Q 170 0 170 20 L 170 70 Q 170 90 150 90 L 60 90 L 35 115 L 42 90 L 20 90 Q 0 90 0 70 L 0 20 Q 0 0 20 0 z'),
    Line: () => new fabric.Line([0, 0, 170, 0]),
    Arrow: () => new fabric.Path('M 0 20 L 140 20 M 105 0 L 140 20 L 105 40'),
    'Double Arrow': () => new fabric.Path('M 0 20 L 170 20 M 30 0 L 0 20 L 30 40 M 140 0 L 170 20 L 140 40'),
    'Curved Arrow': () => new fabric.Path('M 10 60 Q 90 -10 165 35 M 145 20 L 165 35 L 148 52'),
  };
  // Line-type shapes are drawn by direction+length (angle/scale from the drag vector), not a
  // bounding-box stretch — a fixed-orientation template can't otherwise become a real diagonal
  // line/arrow. They're also stroke-only (no fill swatch), unlike closed/fillable shapes.
  const LINE_SHAPES = new Set(['Line', 'Arrow', 'Double Arrow', 'Curved Arrow']);
  /* Native <select> options can't hold real <i> icons, so each shape gets a
     recognizable Unicode glyph as its visible label; the option value stays
     the real shape name so shapes[picker.value] lookups are unaffected. */
  const shapeGlyphs = { Rectangle: '▭', 'Rounded Rectangle': '▢', Circle: '●', Ellipse: '⬭', Triangle: '▲', 'Right Triangle': '◺', Diamond: '◆', Parallelogram: '▱', Trapezoid: '⏢', Pentagon: '⬠', Hexagon: '⬡', Octagon: '⯃', Star: '★', 'Star 6': '✡', Cross: '✚', Heart: '♥', Semicircle: '◗', Cylinder: '⬭', Cube: '⬛', Cloud: '☁', 'Block Arrow': '➤', Line: '─', Arrow: '→', 'Double Arrow': '↔', 'Curved Arrow': '↝', Callout: '💬', 'Rounded Callout': '🗨' };
  const picker = document.createElement('select'); picker.title = 'Shape library'; Object.keys(shapes).forEach(name => { const option = new Option(shapeGlyphs[name] || name, name); option.title = name; picker.add(option); }); picker.style.cssText = 'height:28px;max-width:56px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(picker, 'shape');

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
    widthRow.hidden = window.__notesInkTool === 'eraser';
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
    canvas.freeDrawingBrush.color = kind === 'highlighter' ? hexToRgba(window.__notesHighlightColor || '#ffff00', .35) : kind === 'pencil' ? hexToRgba(color.value, .82) : color.value;
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
    text.setSelectionStyles({ textBackgroundColor: highlightColor.value }, start, end);
    if (editorState) document.execCommand('backColor', false, highlightColor.value);
    text.initDimensions(); canvas.fire('object:modified', { target: text }); canvas.requestRenderAll();
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
  const finalizeShape = (shape, centerX, centerY, keepOrigin = false) => {
    const shapeId = shape.objectId || id();
    shape.set(keepOrigin ? { left: centerX, top: centerY, objectId: shapeId, objectType: 'shape' } : { left: centerX, top: centerY, originX: 'center', originY: 'center', objectId: shapeId, objectType: 'shape' });
    shape.setCoords();
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
  canvas.on('object:scaling', event => syncShapeLabel(event.target)); canvas.on('object:modified', event => syncShapeLabel(event.target));
  canvas.on('mouse:dblclick', event => { if (window.__notesReadOnly) return; const shape = event.target; if (!shape?.shapeTextId) return; const label = canvas.getObjects().find(object => object.objectId === shape.shapeTextId); if (!label) return; canvas.setActiveObject(label); window.__notesBeginNativeTextEdit?.(label); });
  canvas.on('mouse:down', event => {
    if (window.__notesReadOnly) return;
    if (erasing) { eraseAt(canvas.getPointer(event.e)); return; }
    if (placingShape) {
      const pointer = canvas.getPointer(event.e);
      const shape = shapes[picker.value]();
      const isLine = LINE_SHAPES.has(picker.value);
      shape.strokeOnly = isLine;
      const defaultShapeColor = window.__notesShapeColor || getComputedStyle(document.documentElement).getPropertyValue('--accent-subtle').trim();
      shape.set({ left: pointer.x, top: pointer.y, fill: isLine ? null : defaultShapeColor, stroke: defaultShapeColor, strokeWidth: isLine ? 3 : 2 });
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
        shapeDraft.shape.set({ left: shapeDraft.startX, top: shapeDraft.startY, scaleX: length / shapeDraft.naturalWidth, scaleY: 1, angle });
      } else {
        const left = Math.min(pointer.x, shapeDraft.startX), top = Math.min(pointer.y, shapeDraft.startY);
        const width = Math.abs(pointer.x - shapeDraft.startX), height = Math.abs(pointer.y - shapeDraft.startY);
        shapeDraft.shape.set({ left, top, scaleX: Math.max(width, 4) / shapeDraft.naturalWidth, scaleY: Math.max(height, 4) / shapeDraft.naturalHeight });
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
        if (isClick) shape.set({ scaleX: 100 / shapeDraft.naturalWidth, scaleY: 1, angle: 0 });
        finalizeShape(shape, shape.left, shape.top, true);
      } else {
        const centerX = isClick ? startX : Math.min(pointer.x, startX) + draggedWidth / 2;
        const centerY = isClick ? startY : Math.min(pointer.y, startY) + draggedHeight / 2;
        if (isClick) shape.set({ scaleX: 1, scaleY: 1 });
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
  button('Bold selected text', 'fas fa-bold', () => { const text = selectedText(); if (text) updateText({ fontWeight: selectionHasFormat(text, 'fontWeight', 'bold') ? 'normal' : 'bold' }); }, 'format');
  button('Italic selected text', 'fas fa-italic', () => { const text = selectedText(); if (text) updateText({ fontStyle: selectionHasFormat(text, 'fontStyle', 'italic') ? 'normal' : 'italic' }); }, 'format');
  button('Underline selected text', 'fas fa-underline', () => { const text = selectedText(); if (text) updateText({ underline: !selectionHasFormat(text, 'underline', true) }); }, 'format');
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
  /* Unified color system: the existing text/ink swatch keeps controlling text color and pen
     ink, and now also recolors a selected freehand drawing or a stroke-only shape (line/arrow).
     The fill/background swatch controls shape fill and sticky note background. Both apply
     immediately to an existing selected object and otherwise only change the DEFAULT used for
     newly created content — never retroactively recoloring what's already on the canvas. */
  color.addEventListener('input', () => {
    if (window.__notesReadOnly) return;
    const active = canvas.getActiveObject();
    if (active && active.objectType === 'drawing') {
      // Recoloring a translucent (Pencil) stroke must keep its existing translucency instead of
      // swapping in a fully-opaque color — the alpha now lives in the stroke color itself (see
      // activateBrush), so it has to be read back off the CURRENT stroke and reapplied, same as
      // it was already implicitly preserved via object.opacity before this fix.
      const currentAlpha = /^rgba\(/.test(String(active.stroke || '')) ? parseFloat(String(active.stroke).split(',')[3]) : NaN;
      active.set({ stroke: currentAlpha >= 0 && currentAlpha < 1 ? hexToRgba(color.value, currentAlpha) : color.value });
      active.dirty = true; canvas.requestRenderAll(); canvas.fire('object:modified', { target: active });
    }
    else if (active && active.objectType === 'shape' && active.strokeOnly) { active.set({ stroke: color.value }); canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
    else { window.__notesApplyTextColor?.(color.value); }
    /* Update the live brush's color directly rather than rebuilding it via activateBrush() —
       keeps pen/pencil color changeable at any time. Highlighter reads its own color input
       (see highlightColor above) and must never be overwritten by this one. Pencil keeps its
       live-preview alpha baked in here too, for the same reason activateBrush does — otherwise
       switching color mid-session would arm the brush with a fully-opaque color for the next
       stroke, reintroducing the exact live/finished mismatch this fix removes. */
    if (canvas.freeDrawingBrush && window.__notesInkTool !== 'highlighter') canvas.freeDrawingBrush.color = window.__notesInkTool === 'pencil' ? hexToRgba(color.value, .82) : color.value;
  });
  fillColor.addEventListener('input', () => {
    if (window.__notesReadOnly) return;
    window.__notesShapeColor = fillColor.value;
    window.__notesStickyBgColor = fillColor.value;
    const active = canvas.getActiveObject();
    if (!active) return;
    if (active.objectType === 'sticky_note') { window.__notesApplyStickyBackground?.(fillColor.value); }
    else if (active.objectType === 'shape' && !active.strokeOnly) { active.set({ fill: fillColor.value }); canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
  });
}, 300);