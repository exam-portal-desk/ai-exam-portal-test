/* Notebook ink vs. theme — pure logic (no DOM, no Fabric), shared by editor.js, drawing-tools.js and notebook-export.js and
 * covered by tests/test_notebook_ink_theme.py.
 *
 * The problem this solves: a stroke drawn with the untouched default ink was saved as #000000. In light mode that is dark
 * on a light page; switch to a dark page and it disappears. Recolouring every stroke would be worse (it destroys the
 * colours people picked on purpose), so ink is split in two kinds:
 *
 *   EXPLICIT  — a colour the user chose (swatch / custom colour), or any colour that is not the old default black.
 *               Never changed, on screen or in an export. `customColor: true` marks it; legacy strokes are explicit
 *               unless they are the default black.
 *   ADAPTIVE  — the default ink. Its stored colour (`baseStroke`) is never touched; only the colour it is DRAWN with
 *               follows the page: dark ink on a light page, light ink on a dark page. `themeInk: true` marks it.
 *               Legacy strokes that are the default black (#000000, or the #1a1a1a quick swatch) with no metadata are
 *               treated as adaptive (they can only have come from the default ink); everything else about them is left alone.
 *
 * Saved data therefore always keeps the original colour (see restoreStored): switching light -> dark -> light, saving in
 * either theme, undo/redo and JSON export all see the colour the stroke was created with. The Highlighter is stored as
 * 'multiply' (right on a light page); on a dark page it is drawn with 'screen' so it still shows up, and stored back as
 * 'multiply'.
 */

export const PAPERS = {
  light: { paper: '#fbfbfa', dot: '#d9d9d6', ink: '#1a1a1a', stickyText: '#7c4a03', stickyBg: '#fff3bf' },
  dark:  { paper: '#16161d', dot: '#2b2b36', ink: '#ececf1', stickyText: '#fcd34d', stickyBg: '#3a2f10' },
};

const NAMED = { black: [0, 0, 0], white: [255, 255, 255] };

export function parseColor(value) {
  if (typeof value !== 'string') return null;
  const s = value.trim().toLowerCase();
  if (NAMED[s]) return { r: NAMED[s][0], g: NAMED[s][1], b: NAMED[s][2], a: 1 };
  let m = s.match(/^#([0-9a-f]{3})$/);
  if (m) { const h = m[1]; return { r: parseInt(h[0] + h[0], 16), g: parseInt(h[1] + h[1], 16), b: parseInt(h[2] + h[2], 16), a: 1 }; }
  m = s.match(/^#([0-9a-f]{6})([0-9a-f]{2})?$/);
  if (m) { const n = parseInt(m[1], 16); return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: m[2] ? parseInt(m[2], 16) / 255 : 1 }; }
  m = s.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+%?))?\s*\)$/);
  if (m) { let a = 1; if (m[4] != null) a = m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]); return { r: +m[1], g: +m[2], b: +m[3], a }; }
  return null;
}

export function luminance(c) {
  if (!c) return null;
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
}

export const isLightPaper = bg => { const c = parseColor(bg); if (!c || c.a < 0.05) return true; return luminance(c) > 0.4; };   // unknown / transparent: assume light, never throw

// the app's own default inks: pure black (the old default) and #1a1a1a (the first quick swatch)
const isDefaultInk = c => !!c && ((c.r === 0 && c.g === 0 && c.b === 0) || (c.r === 26 && c.g === 26 && c.b === 26));
const hasOwn = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

/** The colour/composite a stroke was CREATED with (what is stored), whatever it is currently drawn with. */
export const baseStrokeOf = o => (o.baseStroke != null ? o.baseStroke : o.stroke);
export const baseCompositeOf = o => (o.baseComposite != null ? o.baseComposite : o.globalCompositeOperation);

export const isHighlighter = o => !!o && o.objectType === 'drawing' && baseCompositeOf(o) === 'multiply';

/** 'adaptive' | 'explicit' | 'none' (not freehand ink, or a highlighter — those keep their own colour). */
export function inkKind(o) {
  if (!o || o.objectType !== 'drawing' || isHighlighter(o)) return 'none';
  if (o.customColor === true || o.themeInk === false) return 'explicit';
  if (o.themeInk === true) return 'adaptive';
  return isDefaultInk(parseColor(baseStrokeOf(o))) ? 'adaptive' : 'explicit';   // legacy: no metadata
}

/** `hex`/`rgb` ink colour with the alpha of `like` (Pencil is translucent; Pen is opaque). */
export function withAlphaOf(inkColor, like) {
  const ink = parseColor(inkColor), ref = parseColor(like);
  if (!ink) return inkColor;
  const a = ref ? ref.a : 1;
  return a >= 1 ? `rgb(${ink.r}, ${ink.g}, ${ink.b})` : `rgba(${ink.r}, ${ink.g}, ${ink.b}, ${Math.round(a * 100) / 100})`;
}

export function displayStroke(o, paperIsLight, inkOnDark) {
  const base = baseStrokeOf(o);
  if (inkKind(o) !== 'adaptive' || paperIsLight) return base;
  return withAlphaOf(inkOnDark, base);
}

export const displayComposite = (o, paperIsLight) => (isHighlighter(o) && !paperIsLight ? 'screen' : baseCompositeOf(o));

/** Live objects: tag legacy ink once and set the colour it is drawn with. Never changes `baseStroke`. Returns true if it changed. */
export function adaptLiveObject(o, paperIsLight, inkOnDark) {
  if (!o || o.objectType !== 'drawing') return false;
  let changed = false;
  if (isHighlighter(o)) {
    if (o.baseComposite == null) { o.baseComposite = o.globalCompositeOperation; }
    const want = displayComposite(o, paperIsLight);
    if (o.globalCompositeOperation !== want) { o.globalCompositeOperation = want; changed = true; }
  } else if (inkKind(o) === 'adaptive') {
    if (o.baseStroke == null) o.baseStroke = o.stroke;
    o.themeInk = true;
    const want = displayStroke(o, paperIsLight, inkOnDark);
    if (o.stroke !== want) { o.stroke = want; changed = true; }
  }
  if (changed) o.dirty = true;
  return changed;
}

/** What gets written to saved data / undo history / JSON export for a plain object: the original colour, never the drawn one. */
export function restoreStored(plain, source) {
  if (!plain || !source) return plain;
  if (source.themeInk === true && source.baseStroke != null) plain.stroke = source.baseStroke;
  if (source.baseComposite != null) plain.globalCompositeOperation = source.baseComposite;
  return plain;
}

/* ── export: deterministic, independent of the app theme ── */

const walk = (objects, fn) => (objects || []).forEach(o => { fn(o); if (Array.isArray(o.objects)) walk(o.objects, fn); });

/** 'light' | 'dark' paper for a whole notebook, from the colours people explicitly chose (default ink/text adapt to either). */
export function chooseExportPaper(pages) {
  let light = 0, dark = 0;
  const tally = color => {
    const l = luminance(parseColor(color));
    if (l == null) return;
    if (l > 0.75) light++; else if (l < 0.2) dark++;
  };
  (pages || []).forEach(objs => walk(objs, o => {
    if (o.objectType === 'drawing' && !isHighlighter(o) && inkKind(o) === 'explicit') tally(baseStrokeOf(o));
    else if (o.objectType === 'rich_text' && o.customColor === true) tally(o.fill);
  }));
  return light > dark ? 'dark' : 'light';
}

/** Copy of raw page objects with every adaptive colour resolved for `paper`; explicit colours are returned untouched. */
export function resolveForExport(objects, paper) {
  const p = PAPERS[paper] || PAPERS.light, onLight = paper !== 'dark';
  const fix = o => {
    const c = Object.assign({}, o);
    if (Array.isArray(o.objects)) c.objects = o.objects.map(fix);
    if (c.objectType === 'drawing') {
      if (isHighlighter(c)) c.globalCompositeOperation = displayComposite(c, onLight);
      else if (inkKind(c) === 'adaptive') c.stroke = displayStroke(c, onLight, p.ink);
    }
    if (c.themeText && !c.customColor && c.fill != null) c.fill = p.ink;
    if (c.themeSticky) {
      if (!c.customColor) c.fill = p.stickyText;
      if (!c.customBg) c.backgroundColor = p.stickyBg;
    }
    return c;
  };
  return (objects || []).map(fix);
}
