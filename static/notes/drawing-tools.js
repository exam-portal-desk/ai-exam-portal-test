/* Dedicated drawing and shape controls for the Notes Fabric canvas. */
setTimeout(() => {
  const canvas = window.__notesCanvas;
  const toolbar = document.querySelector('.object-toolbar');
  if (!canvas || !toolbar) return;

  const id = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  const insert = element => { toolbar.insertBefore(element, toolbar.lastElementChild); return element; };
  /* Every toolbar button created here is routed through this wrapper, so a single
     `window.__notesReadOnly` check (set by editor.js when Read Mode is active)
     is enough to block pen/eraser/format/shape actions — no per-button changes needed. */
  const button = (label, icon, handler) => { const item = document.createElement('button'); item.type = 'button'; item.title = label; item.setAttribute('aria-label', label); item.innerHTML = `<i class="${icon}"></i>`; item.addEventListener('click', (...args) => { if (window.__notesReadOnly) return; handler(...args); }); return insert(item); };
  /* Defaults for NEW content only — changing these never recolors/resizes existing objects. */
  window.__notesDefaultSize = window.__notesDefaultSize || 18;
  window.__notesShapeColor = window.__notesShapeColor || getComputedStyle(document.documentElement).getPropertyValue('--accent-subtle').trim();
  window.__notesStickyBgColor = window.__notesStickyBgColor || getComputedStyle(document.documentElement).getPropertyValue('--warning-bg').trim();

  const color = document.createElement('input'); color.type = 'color'; color.value = document.documentElement.getAttribute('data-theme') === 'light' ? '#000000' : '#ffffff'; color.title = 'Text / ink color'; color.style.cssText = 'width:28px;height:28px;padding:2px;border:1px solid var(--border);border-radius:7px;background:var(--surface)'; insert(color);
  const fillColor = document.createElement('input'); fillColor.type = 'color'; fillColor.value = window.__notesShapeColor.startsWith('#') ? window.__notesShapeColor : '#4a86e8'; fillColor.title = 'Fill / background color (shapes, sticky note background)'; fillColor.style.cssText = 'width:28px;height:28px;padding:2px;border:1px solid var(--border);border-radius:7px;background:var(--surface)'; insert(fillColor);
  const eraserSize = document.createElement('select'); eraserSize.title = 'Eraser size'; [['Small', 10], ['Medium', 22], ['Large', 38]].forEach(([name, value]) => eraserSize.add(new Option(name, value))); eraserSize.style.cssText = 'height:28px;max-width:76px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(eraserSize);
  const shapes = {
    Rectangle: () => new fabric.Rect({ width: 170, height: 105 }), 'Rounded Rectangle': () => new fabric.Rect({ width: 170, height: 105, rx: 16, ry: 16 }), Circle: () => new fabric.Circle({ radius: 60 }), Ellipse: () => new fabric.Ellipse({ rx: 90, ry: 55 }), Triangle: () => new fabric.Triangle({ width: 140, height: 115 }), Diamond: () => new fabric.Polygon([{ x: 60, y: 0 }, { x: 120, y: 60 }, { x: 60, y: 120 }, { x: 0, y: 60 }]), Pentagon: () => new fabric.Polygon([{ x: -55, y: 40 }, { x: 0, y: -60 }, { x: 55, y: 40 }, { x: 35, y: 70 }, { x: -35, y: 70 }]), Hexagon: () => new fabric.Polygon([{ x: -60, y: 0 }, { x: -30, y: -52 }, { x: 30, y: -52 }, { x: 60, y: 0 }, { x: 30, y: 52 }, { x: -30, y: 52 }]), Star: () => new fabric.Polygon(Array.from({ length: 10 }, (_, index) => { const angle = -Math.PI / 2 + index * Math.PI / 5, radius = index % 2 ? 30 : 65; return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius }; })), Line: () => new fabric.Line([0, 0, 170, 0]), Arrow: () => new fabric.Path('M 0 20 L 140 20 M 105 0 L 140 20 L 105 40'), 'Double Arrow': () => new fabric.Path('M 0 20 L 170 20 M 30 0 L 0 20 L 30 40 M 140 0 L 170 20 L 140 40'), Callout: () => new fabric.Path('M 0 0 L 170 0 L 170 95 L 58 95 L 25 125 L 38 95 L 0 95 z')
  };
  /* Native <select> options can't hold real <i> icons, so each shape gets a
     recognizable Unicode glyph as its visible label; the option value stays
     the real shape name so shapes[picker.value] lookups are unaffected. */
  const shapeGlyphs = { Rectangle: '▭', 'Rounded Rectangle': '▢', Circle: '●', Ellipse: '⬭', Triangle: '▲', Diamond: '◆', Pentagon: '⬠', Hexagon: '⬡', Star: '★', Line: '─', Arrow: '→', 'Double Arrow': '↔', Callout: '💬' };
  const picker = document.createElement('select'); picker.title = 'Shape library'; Object.keys(shapes).forEach(name => { const option = new Option(shapeGlyphs[name] || name, name); option.title = name; picker.add(option); }); picker.style.cssText = 'height:28px;max-width:56px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(picker);

  /* Integer size control — free typing, +/-1 steps, valid range only. Drives font size for
     text, stroke width for freehand/pen drawings; internal property stays correct per type. */
  const SIZE_MIN = 1, SIZE_MAX = 300;
  const clampSize = value => { const n = Math.round(Number(value)); return Number.isFinite(n) ? Math.min(SIZE_MAX, Math.max(SIZE_MIN, n)) : window.__notesDefaultSize || 18; };
  const sizeStepper = document.createElement('div'); sizeStepper.className = 'notes-size-stepper'; sizeStepper.title = 'Size';
  const sizeMinus = document.createElement('button'); sizeMinus.type = 'button'; sizeMinus.textContent = '−'; sizeMinus.setAttribute('aria-label', 'Decrease size');
  const sizeInput = document.createElement('input'); sizeInput.type = 'number'; sizeInput.min = String(SIZE_MIN); sizeInput.max = String(SIZE_MAX); sizeInput.step = '1'; sizeInput.value = String(window.__notesDefaultSize || 18); sizeInput.setAttribute('aria-label', 'Size in pixels');
  const sizePlus = document.createElement('button'); sizePlus.type = 'button'; sizePlus.textContent = '+'; sizePlus.setAttribute('aria-label', 'Increase size');
  const sizeUnit = document.createElement('span'); sizeUnit.className = 'notes-size-unit'; sizeUnit.textContent = 'px';
  sizeStepper.append(sizeMinus, sizeInput, sizePlus, sizeUnit); insert(sizeStepper);
  const applySize = next => {
    if (window.__notesReadOnly) { sizeInput.value = String(window.__notesDefaultSize || 18); return; }
    const value = clampSize(next);
    sizeInput.value = String(value);
    window.__notesDefaultSize = value;
    const active = canvas.getActiveObject();
    if (active && ['i-text', 'textbox'].includes(active.type)) { updateText({ fontSize: value }); }
    else if (active && active.objectType === 'drawing' && 'strokeWidth' in active) { active.set({ strokeWidth: value }); active.dirty = true; canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
    if (canvas.freeDrawingBrush) canvas.freeDrawingBrush.width = value;
  };
  sizeMinus.addEventListener('click', () => applySize((Number(sizeInput.value) || window.__notesDefaultSize || 18) - 1));
  sizePlus.addEventListener('click', () => applySize((Number(sizeInput.value) || window.__notesDefaultSize || 18) + 1));
  sizeInput.addEventListener('change', () => applySize(sizeInput.value));
  sizeInput.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); applySize(sizeInput.value); } });
  /* Predefined pen thickness presets, alongside the free-form stepper above —
     picking one just calls the same applySize() so custom sizing still works. */
  const penThickness = document.createElement('select'); penThickness.title = 'Pen thickness'; [['Thin', 4], ['Medium', 10], ['Thick', 20]].forEach(([name, value]) => penThickness.add(new Option(name, value))); penThickness.style.cssText = 'height:28px;max-width:90px;background:var(--surface);color:var(--text-1);border:1px solid var(--border);border-radius:7px'; insert(penThickness);
  penThickness.addEventListener('change', () => applySize(penThickness.value));
  const syncSizeDisplay = () => {
    const active = canvas.getActiveObject();
    if (!active) return;
    if (['i-text', 'textbox'].includes(active.type) && active.fontSize) { sizeInput.value = String(Math.round(active.fontSize)); window.__notesDefaultSize = Math.round(active.fontSize); }
    else if (active.objectType === 'drawing' && active.strokeWidth) { sizeInput.value = String(Math.round(active.strokeWidth)); window.__notesDefaultSize = Math.round(active.strokeWidth); }
  };
  canvas.on('selection:created', syncSizeDisplay); canvas.on('selection:updated', syncSizeDisplay);
  let erasing = false;
  const penCursor = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24'%3E%3Cpath d='M14.7 3.3a1.5 1.5 0 0 1 2.1 0l3.9 3.9a1.5 1.5 0 0 1 0 2.1L9.5 20.5 3 22l1.5-6.5z' fill='%23ffffff' stroke='%235a6472' stroke-width='1.6' stroke-linejoin='round'/%3E%3Cpath d='M12.9 5.1l4.9 4.9' stroke='%235a6472' stroke-width='1.6'/%3E%3C/svg%3E\") 3 21, crosshair";
  const eraserCursor = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24'%3E%3Cpath d='M4 16 13 5l7 7-9 9H4z' fill='%23ffffff' stroke='%235a6472' stroke-width='2'/%3E%3Cpath d='M3 21h12' stroke='%235a6472' stroke-width='2'/%3E%3C/svg%3E\") 4 20, crosshair";
  const activatePen = () => { erasing = false; window.__notesEraserActive = false; canvas.selection = false; canvas.isDrawingMode = true; canvas.freeDrawingCursor = penCursor; canvas.defaultCursor = penCursor; canvas.hoverCursor = penCursor; canvas.freeDrawingBrush = new fabric.PencilBrush(canvas); canvas.freeDrawingBrush.color = color.value; canvas.freeDrawingBrush.width = window.__notesDefaultSize || 4; };
  const eraseAt = pointer => {
    const radius = Number(eraserSize.value) / 2;
    canvas.getObjects().filter(object => object.objectType === 'drawing').filter(object => {
      const box = object.getBoundingRect(true, true); const nearestX = Math.max(box.left, Math.min(pointer.x, box.left + box.width)); const nearestY = Math.max(box.top, Math.min(pointer.y, box.top + box.height)); return (pointer.x - nearestX) ** 2 + (pointer.y - nearestY) ** 2 <= radius ** 2;
    }).forEach(object => canvas.remove(object));
    canvas.requestRenderAll();
  };
  const activateEraser = () => { erasing = true; window.__notesEraserActive = true; canvas.isDrawingMode = false; canvas.selection = false; canvas.defaultCursor = eraserCursor; canvas.hoverCursor = eraserCursor; };
  const applyHighlight = () => {
    const text = canvas.getActiveObject();
    if (!text || !['i-text', 'textbox'].includes(text.type) || text.selectionStart === text.selectionEnd) return;
    text.setSelectionStyles({ textBackgroundColor: color.value }, text.selectionStart, text.selectionEnd); text.initDimensions(); canvas.fire('object:modified', { target: text }); canvas.requestRenderAll();
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
  const finalizeShape = (shape, centerX, centerY) => {
    const shapeId = shape.objectId || id();
    shape.set({ left: centerX, top: centerY, originX: 'center', originY: 'center', objectId: shapeId, objectType: 'shape' });
    shape.setCoords();
    const label = new fabric.Textbox('', { left: shape.left, top: shape.top, originX: 'center', originY: 'center', width: Math.max(80, (shape.width || 160) * Math.abs(shape.scaleX || 1) * .78), fontFamily: 'DM Sans', fontSize: 18, lineHeight: 1.2, textAlign: 'center', fill: getComputedStyle(document.documentElement).getPropertyValue('--text-1').trim(), objectId: id(), objectType: 'shape', shapeTextFor: shapeId });
    shape.shapeTextId = label.objectId; canvas.add(label); canvas.setActiveObject(shape); canvas.requestRenderAll();
  };
  const syncShapeLabel = shape => { if (!shape?.shapeTextId) return; const label = canvas.getObjects().find(object => object.objectId === shape.shapeTextId); if (!label) return; label.set({ left: shape.left, top: shape.top, width: Math.max(80, (shape.width || 160) * Math.abs(shape.scaleX || 1) * .78), scaleX: 1, scaleY: 1, angle: shape.angle }); label.initDimensions(); label.setCoords(); };
  const selectedText = () => { const object = canvas.getActiveObject(); return object && ['i-text', 'textbox'].includes(object.type) ? object : null; };
  const updateText = changes => { if (window.__notesReadOnly) return; const text = selectedText(); if (!text) return; const hasSelection = text.selectionStart !== text.selectionEnd; if (hasSelection) text.setSelectionStyles(changes, text.selectionStart, text.selectionEnd); else text.set(changes); text.initDimensions(); window.__notesSyncActiveTextOverlay?.(); canvas.fire('object:modified', { target: text }); canvas.requestRenderAll(); };
  canvas.on('object:scaling', event => syncShapeLabel(event.target)); canvas.on('object:modified', event => syncShapeLabel(event.target));
  canvas.on('mouse:dblclick', event => { if (window.__notesReadOnly) return; const shape = event.target; if (!shape?.shapeTextId) return; const label = canvas.getObjects().find(object => object.objectId === shape.shapeTextId); if (!label) return; canvas.setActiveObject(label); window.__notesBeginNativeTextEdit?.(label); });
  canvas.on('mouse:down', event => {
    if (window.__notesReadOnly) return;
    if (erasing) { eraseAt(canvas.getPointer(event.e)); return; }
    if (placingShape) {
      const pointer = canvas.getPointer(event.e);
      const shape = shapes[picker.value]();
      const defaultShapeColor = window.__notesShapeColor || getComputedStyle(document.documentElement).getPropertyValue('--accent-subtle').trim();
      shape.set({ left: pointer.x, top: pointer.y, originX: 'left', originY: 'top', fill: defaultShapeColor, stroke: defaultShapeColor, strokeWidth: 2 });
      canvas.add(shape);
      shapeDraft = { shape, startX: pointer.x, startY: pointer.y, naturalWidth: Math.max(shape.width || 1, 1), naturalHeight: Math.max(shape.height || 1, 1) };
      canvas.requestRenderAll();
    }
  });
  canvas.on('mouse:move', event => {
    if (window.__notesReadOnly) return;
    if (erasing && event.e.buttons) { eraseAt(canvas.getPointer(event.e)); return; }
    if (placingShape && shapeDraft) {
      const pointer = canvas.getPointer(event.e);
      const left = Math.min(pointer.x, shapeDraft.startX), top = Math.min(pointer.y, shapeDraft.startY);
      const width = Math.abs(pointer.x - shapeDraft.startX), height = Math.abs(pointer.y - shapeDraft.startY);
      shapeDraft.shape.set({ left, top, scaleX: Math.max(width, 4) / shapeDraft.naturalWidth, scaleY: Math.max(height, 4) / shapeDraft.naturalHeight });
      shapeDraft.shape.setCoords();
      canvas.requestRenderAll();
    }
  });
  canvas.on('mouse:up', event => {
    if (window.__notesReadOnly) return;
    if (erasing) { canvas.selection = true; return; }
    if (placingShape && shapeDraft) {
      const { shape, startX, startY } = shapeDraft;
      const pointer = canvas.getPointer(event.e);
      const draggedWidth = Math.abs(pointer.x - startX), draggedHeight = Math.abs(pointer.y - startY);
      const isClick = draggedWidth < 6 && draggedHeight < 6;
      const centerX = isClick ? startX : Math.min(pointer.x, startX) + draggedWidth / 2;
      const centerY = isClick ? startY : Math.min(pointer.y, startY) + draggedHeight / 2;
      if (isClick) shape.set({ scaleX: 1, scaleY: 1 });
      finalizeShape(shape, centerX, centerY);
      exitShapePlacement();
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !placingShape) return;
    if (shapeDraft) canvas.remove(shapeDraft.shape);
    exitShapePlacement();
    canvas.requestRenderAll();
  });
  button('Pen', 'fas fa-pen', activatePen); button('Eraser', 'fas fa-eraser', activateEraser); button('Highlight selected text', 'fas fa-highlighter', applyHighlight); button('Bold selected text', 'fas fa-bold', () => { const text = selectedText(); if (text) updateText({ fontWeight: text.fontWeight === 'bold' ? 'normal' : 'bold' }); }); button('Italic selected text', 'fas fa-italic', () => { const text = selectedText(); if (text) updateText({ fontStyle: text.fontStyle === 'italic' ? 'normal' : 'italic' }); }); button('Align left', 'fas fa-align-left', () => updateText({ textAlign: 'left' })); button('Center text', 'fas fa-align-center', () => updateText({ textAlign: 'center' })); button('Align right', 'fas fa-align-right', () => updateText({ textAlign: 'right' })); button('Justify text', 'fas fa-align-justify', () => updateText({ textAlign: 'justify' })); shapeButton = button('Draw shape (click-drag on canvas)', 'far fa-square', beginShapePlacement);
  /* Unified color system: the existing text/ink swatch keeps controlling text color and pen
     ink, and now also recolors a selected freehand drawing or a stroke-only shape (line/arrow).
     The fill/background swatch controls shape fill and sticky note background. Both apply
     immediately to an existing selected object and otherwise only change the DEFAULT used for
     newly created content — never retroactively recoloring what's already on the canvas. */
  const STROKE_ONLY_TYPES = ['line', 'path', 'polyline'];
  color.addEventListener('input', () => {
    if (window.__notesReadOnly) return;
    const active = canvas.getActiveObject();
    if (active && active.objectType === 'drawing') { active.set({ stroke: color.value }); active.dirty = true; canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
    else if (active && active.objectType === 'shape' && STROKE_ONLY_TYPES.includes(active.type)) { active.set({ stroke: color.value }); canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
    else { window.__notesApplyTextColor?.(color.value); }
    /* Update the live brush's color directly rather than rebuilding it via
       activatePen() — keeps pen color changeable at any time, independent of
       theme or which object (if any) happens to be selected. */
    if (canvas.freeDrawingBrush) canvas.freeDrawingBrush.color = color.value;
  });
  fillColor.addEventListener('input', () => {
    if (window.__notesReadOnly) return;
    window.__notesShapeColor = fillColor.value;
    window.__notesStickyBgColor = fillColor.value;
    const active = canvas.getActiveObject();
    if (!active) return;
    if (active.objectType === 'sticky_note') { window.__notesApplyStickyBackground?.(fillColor.value); }
    else if (active.objectType === 'shape' && !['i-text', 'textbox'].includes(active.type) && !STROKE_ONLY_TYPES.includes(active.type)) { active.set({ fill: fillColor.value }); canvas.requestRenderAll(); canvas.fire('object:modified', { target: active }); }
  });
}, 300);