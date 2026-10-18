/* Notebook shape catalogue + drawing geometry — pure data and maths (no DOM, no Fabric), shared by drawing-tools.js and covered by
 * tests/test_notebook_shapes.py.
 *
 * Every shape here is ONE SVG-style path in its own "natural" box. The canvas turns it into a fabric.Path (objectType 'shape') and
 * the drag stretches it to the dragged box with scaleX/scaleY (strokeUniform keeps the border the chosen width), exactly like the
 * shapes that already existed — so new shapes use the same document model, history, save/load and export as old ones; no new
 * storage format. The picker draws its icons from these same paths, so what you see in the menu is what you get on the page.
 *
 * Coordinates are absolute (M L H V Q C A Z only). Filled shapes with holes (ring, frame, smiley…) wind the hole the opposite way
 * from the outline, so a plain non-zero fill leaves it open in every renderer.
 */

const f = n => Math.round(n * 100) / 100;
const poly = pts => 'M ' + pts.map(p => f(p[0]) + ' ' + f(p[1])).join(' L ') + ' Z';
const ngon = (n, rx, ry, cx, cy, rot = 0) => poly(Array.from({ length: n }, (_, i) => { const a = -Math.PI / 2 + rot + i * 2 * Math.PI / n; return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]; }));
const starPts = (n, rOut, rIn, cx, cy, wobble = 0) => Array.from({ length: n * 2 }, (_, i) => {
  const a = -Math.PI / 2 + i * Math.PI / n; let r = i % 2 ? rIn : rOut;
  if (wobble && !(i % 2)) r *= 1 - wobble * (((i / 2) * 7) % 3) / 2;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
});
const star = (n, rOut, rIn, cx = 50, cy = 50, wobble = 0) => poly(starPts(n, rOut, rIn, cx, cy, wobble));
const circle = (cx, cy, r, ccw = false) => `M ${f(cx - r)} ${f(cy)} A ${r} ${r} 0 1 ${ccw ? 0 : 1} ${f(cx + r)} ${f(cy)} A ${r} ${r} 0 1 ${ccw ? 0 : 1} ${f(cx - r)} ${f(cy)} Z`;
const rrect = (w, h, r) => `M ${r} 0 H ${w - r} A ${r} ${r} 0 0 1 ${w} ${r} V ${h - r} A ${r} ${r} 0 0 1 ${w - r} ${h} H ${r} A ${r} ${r} 0 0 1 0 ${h - r} V ${r} A ${r} ${r} 0 0 1 ${r} 0 Z`;
const sunRays = () => Array.from({ length: 8 }, (_, i) => {
  const a = i * Math.PI / 4, c = Math.cos(a), s = Math.sin(a), px = -s, py = c;
  return poly([[50 + 32 * c + 6 * px, 50 + 32 * s + 6 * py], [50 + 48 * c, 50 + 48 * s], [50 + 32 * c - 6 * px, 50 + 32 * s - 6 * py]]);
}).join(' ');

/* name -> { d, noFill? (stroke-only), directional? (runs from the drag start to the drag end: flips with the drag direction) } */
export const EXTRA_SHAPES = {
  // ── Shapes
  'Snip Corner Rectangle': { d: 'M 0 0 L 70 0 L 100 30 L 100 70 L 0 70 Z' },
  'Snip Diagonal Rectangle': { d: 'M 25 0 L 100 0 L 100 45 L 75 70 L 0 70 L 0 25 Z' },
  'Round Diagonal Rectangle': { d: 'M 18 0 L 100 0 L 100 52 Q 100 70 82 70 L 0 70 L 0 18 Q 0 0 18 0 Z' },
  Heptagon: { d: ngon(7, 50, 50, 50, 50) },
  Decagon: { d: ngon(10, 50, 50, 50, 50) },
  Dodecagon: { d: ngon(12, 50, 50, 50, 50) },
  Pie: { d: 'M 50 50 L 100 50 A 50 50 0 1 0 50 0 Z' },
  Arc: { d: 'M 0 50 A 50 50 0 0 1 100 50', noFill: true },
  Teardrop: { d: 'M 50 0 L 100 0 L 100 50 A 50 50 0 1 1 50 0 Z' },
  Plaque: { d: 'M 15 0 L 85 0 A 15 15 0 0 0 100 15 L 100 55 A 15 15 0 0 0 85 70 L 15 70 A 15 15 0 0 0 0 55 L 0 15 A 15 15 0 0 0 15 0 Z' },
  Frame: { d: 'M 0 0 H 100 V 70 H 0 Z M 14 14 V 56 H 86 V 14 Z' },
  Donut: { d: circle(50, 50, 50) + ' ' + circle(50, 50, 24, true) },
  'No Symbol': { d: circle(50, 50, 50) + ' ' + circle(50, 50, 38, true) + ' ' + poly([[22, 14], [86, 78], [78, 86], [14, 22]]) },
  Smiley: { d: circle(50, 50, 50) + ' ' + circle(34, 38, 6, true) + ' ' + circle(66, 38, 6, true) + ' M 24 58 Q 50 90 76 58 Q 50 74 24 58 Z' },
  Sun: { d: circle(50, 50, 22) + ' ' + sunRays() },
  Moon: { d: 'M 60 0 A 50 50 0 1 0 60 100 A 38 38 0 1 1 60 0 Z' },
  'Lightning Bolt': { d: poly([[60, 0], [20, 55], [45, 55], [30, 100], [80, 40], [52, 40], [75, 0]]) },
  'Folded Corner': { d: 'M 0 0 H 100 V 55 L 75 80 H 0 Z M 75 80 L 75 55 H 100' },
  'Star 4': { d: star(4, 50, 20) },
  'Star 8': { d: star(8, 50, 30) },
  // ── Arrows (block arrows)
  'Right Arrow': { d: 'M 0 30 H 60 V 10 L 100 50 L 60 90 V 70 H 0 Z' },
  'Left Arrow': { d: 'M 100 30 H 40 V 10 L 0 50 L 40 90 V 70 H 100 Z' },
  'Up Arrow': { d: 'M 30 100 V 40 H 10 L 50 0 L 90 40 H 70 V 100 Z' },
  'Down Arrow': { d: 'M 30 0 V 60 H 10 L 50 100 L 90 60 H 70 V 0 Z' },
  'Left-Right Arrow': { d: 'M 0 50 L 30 20 V 38 H 70 V 20 L 100 50 L 70 80 V 62 H 30 V 80 Z' },
  'Up-Down Arrow': { d: 'M 50 0 L 80 30 H 62 V 70 H 80 L 50 100 L 20 70 H 38 V 30 H 20 Z' },
  'Quad Arrow': { d: 'M 50 0 L 68 20 H 56 V 44 H 80 V 32 L 100 50 L 80 68 V 56 H 56 V 80 H 68 L 50 100 L 32 80 H 44 V 56 H 20 V 68 L 0 50 L 20 32 V 44 H 44 V 20 H 32 Z' },
  'Bent Arrow': { d: 'M 0 100 V 40 Q 0 28 12 28 H 60 V 8 L 100 40 L 60 72 V 52 H 28 V 100 Z' },
  'U-Turn Arrow': { d: 'M 0 100 V 40 Q 0 0 40 0 Q 80 0 80 40 V 56 H 100 L 65 100 L 30 56 H 50 V 40 Q 50 28 40 28 Q 28 28 28 40 V 100 Z' },
  Chevron: { d: 'M 0 0 H 60 L 100 50 L 60 100 H 0 L 40 50 Z' },
  'Home Plate': { d: 'M 0 0 H 70 L 100 50 L 70 100 H 0 Z' },
  'Notched Right Arrow': { d: 'M 0 30 H 60 V 10 L 100 50 L 60 90 V 70 H 0 L 18 50 Z' },
  // ── Callouts
  'Oval Callout': { d: 'M 75 71 L 92 100 L 50 76 A 50 38 0 1 1 75 71 Z' },
  'Cloud Callout': { d: 'M 30 90 Q 10 90 10 70 Q 10 50 30 50 Q 30 25 55 25 Q 75 5 100 25 Q 130 20 140 45 Q 160 50 160 70 Q 160 90 140 90 Z M 50 100 A 7 7 0 1 1 64 100 A 7 7 0 1 1 50 100 Z M 36 112 A 4 4 0 1 1 44 112 A 4 4 0 1 1 36 112 Z' },
  Explosion: { d: star(9, 50, 22, 50, 50, 0.3) },
  'Seal 8': { d: star(8, 50, 40) },
  'Seal 12': { d: star(12, 50, 41) },
  'Seal 16': { d: star(16, 50, 43) },
  'Seal 24': { d: star(24, 50, 45) },
  Wave: { d: 'M 0 15 Q 25 0 50 15 Q 75 30 100 15 V 75 Q 75 90 50 75 Q 25 60 0 75 Z' },
  Banner: { d: 'M 0 0 H 100 V 50 H 0 L 12 25 Z' },
  // ── Flowchart
  Process: { d: 'M 0 0 H 100 V 70 H 0 Z' },
  'Alternate Process': { d: rrect(100, 70, 14) },
  Decision: { d: poly([[50, 0], [100, 35], [50, 70], [0, 35]]) },
  Data: { d: 'M 20 0 H 100 L 80 70 H 0 Z' },
  'Predefined Process': { d: 'M 0 0 H 100 V 70 H 0 Z M 12 0 V 70 M 88 0 V 70' },
  'Internal Storage': { d: 'M 0 0 H 100 V 70 H 0 Z M 14 0 V 70 M 0 14 H 100' },
  Document: { d: 'M 0 0 H 100 V 56 Q 75 38 50 56 Q 25 74 0 56 Z' },
  Terminator: { d: 'M 20 0 H 80 A 20 20 0 0 1 80 40 H 20 A 20 20 0 0 1 20 0 Z' },
  Preparation: { d: 'M 20 0 H 80 L 100 35 L 80 70 H 20 L 0 35 Z' },
  'Manual Input': { d: 'M 0 20 L 100 0 V 70 H 0 Z' },
  'Manual Operation': { d: 'M 0 0 H 100 L 80 70 H 20 Z' },
  Connector: { d: circle(35, 35, 35) },
  'Off-page Connector': { d: 'M 0 0 H 70 V 40 L 35 70 L 0 40 Z' },
  Delay: { d: 'M 0 0 H 60 A 35 35 0 0 1 60 70 H 0 Z' },
  Display: { d: 'M 0 35 L 25 0 H 75 A 25 35 0 0 1 75 70 H 25 Z' },
  'Summing Junction': { d: circle(35, 35, 35) + ' M 10 10 L 60 60 M 60 10 L 10 60' },
  Or: { d: circle(35, 35, 35) + ' M 35 0 V 70 M 0 35 H 70' },
  Collate: { d: 'M 0 0 H 70 L 0 70 H 70 Z' },
  Merge: { d: 'M 0 0 H 80 L 40 70 Z' },
  Extract: { d: 'M 40 0 L 80 70 H 0 Z' },
  Sort: { d: poly([[50, 0], [100, 35], [50, 70], [0, 35]]) + ' M 0 35 H 100' },
  // ── Equation
  Plus: { d: 'M 40 0 H 60 V 40 H 100 V 60 H 60 V 100 H 40 V 60 H 0 V 40 H 40 Z' },
  Minus: { d: 'M 0 0 H 100 V 20 H 0 Z' },
  Multiply: { d: 'M 20 0 L 50 30 L 80 0 L 100 20 L 70 50 L 100 80 L 80 100 L 50 70 L 20 100 L 0 80 L 30 50 L 0 20 Z' },
  Divide: { d: 'M 0 40 H 100 V 60 H 0 Z ' + circle(50, 14, 12) + ' ' + circle(50, 86, 12) },
  Equal: { d: 'M 0 0 H 100 V 20 H 0 Z M 0 40 H 100 V 60 H 0 Z' },
  'Not Equal': { d: 'M 0 10 H 100 V 30 H 0 Z M 0 50 H 100 V 70 H 0 Z M 62 0 L 80 8 L 38 80 L 20 72 Z' },
  'Bracket Pair': { d: 'M 25 0 H 0 V 100 H 25 M 75 0 H 100 V 100 H 75', noFill: true },
  'Brace Pair': { d: 'M 30 0 Q 12 0 12 18 V 38 Q 12 50 0 50 Q 12 50 12 62 V 82 Q 12 100 30 100 M 70 0 Q 88 0 88 18 V 38 Q 88 50 100 50 Q 88 50 88 62 V 82 Q 88 100 70 100', noFill: true },
  'Left Bracket': { d: 'M 30 0 H 0 V 100 H 30', noFill: true },
  'Right Bracket': { d: 'M 0 0 H 30 V 100 H 0', noFill: true },
  'Left Brace': { d: 'M 30 0 Q 10 0 10 20 V 40 Q 10 50 0 50 Q 10 50 10 60 V 80 Q 10 100 30 100', noFill: true },
  'Right Brace': { d: 'M 0 0 Q 20 0 20 20 V 40 Q 20 50 30 50 Q 20 50 20 60 V 80 Q 20 100 0 100', noFill: true },
  // ── Lines (stroke-only; run from where the drag starts to where it ends, in any direction)
  'Elbow Connector': { d: 'M 0 0 H 50 V 100 H 100', noFill: true, directional: true },
  'Curved Connector': { d: 'M 0 0 C 50 0 50 100 100 100', noFill: true, directional: true },
  Curve: { d: 'M 0 100 Q 50 -20 100 100', noFill: true, directional: true },
};

/* The shape menu. Names that are not in EXTRA_SHAPES are the shapes drawing-tools.js already defined (rectangle, circle, heart, …). */
export const SHAPE_MENU = [
  { id: 'shapes', name: 'Shapes', icon: 'far fa-square', items: ['Rectangle', 'Rounded Rectangle', 'Snip Corner Rectangle', 'Snip Diagonal Rectangle', 'Round Diagonal Rectangle', 'Circle', 'Ellipse',
    'Triangle', 'Right Triangle', 'Diamond', 'Parallelogram', 'Trapezoid', 'Pentagon', 'Hexagon', 'Heptagon', 'Octagon', 'Decagon', 'Dodecagon', 'Pie', 'Arc',
    'Teardrop', 'Plaque', 'Frame', 'Donut', 'No Symbol', 'Cross', 'Heart', 'Smiley', 'Sun', 'Moon', 'Lightning Bolt', 'Folded Corner', 'Cloud', 'Semicircle',
    'Cylinder', 'Cube', 'Star 4', 'Star', 'Star 6', 'Star 8'] },
  { id: 'arrows', name: 'Arrows', icon: 'fas fa-arrow-right', items: ['Right Arrow', 'Left Arrow', 'Up Arrow', 'Down Arrow', 'Left-Right Arrow', 'Up-Down Arrow', 'Quad Arrow',
    'Bent Arrow', 'U-Turn Arrow', 'Chevron', 'Home Plate', 'Notched Right Arrow', 'Block Arrow'] },
  { id: 'callouts', name: 'Callouts', icon: 'far fa-comment', items: ['Callout', 'Rounded Callout', 'Oval Callout', 'Cloud Callout', 'Explosion', 'Seal 8', 'Seal 12', 'Seal 16', 'Seal 24', 'Wave', 'Banner'] },
  { id: 'flowchart', name: 'Flowchart', icon: 'fas fa-diagram-project', items: ['Process', 'Alternate Process', 'Decision', 'Data', 'Predefined Process', 'Internal Storage', 'Document',
    'Terminator', 'Preparation', 'Manual Input', 'Manual Operation', 'Connector', 'Off-page Connector', 'Delay', 'Display', 'Summing Junction', 'Or', 'Collate', 'Merge', 'Extract', 'Sort'] },
  { id: 'equation', name: 'Equation', icon: 'fas fa-plus-minus', items: ['Plus', 'Minus', 'Multiply', 'Divide', 'Equal', 'Not Equal', 'Bracket Pair', 'Brace Pair', 'Left Bracket', 'Right Bracket', 'Left Brace', 'Right Brace', 'X-Y Axis'] },
];

/* The line menu (names that are not in EXTRA_SHAPES are existing line/arrow tools; 'Polyline' and 'Scribble' are interactions, not paths). */
export const LINE_MENU = ['Line', 'Arrow', 'Double Arrow', 'Curved Arrow', 'Elbow Connector', 'Curved Connector', 'Curve', 'Polyline', 'Scribble'];

/* ── line formatting ── */
export const WEIGHTS = [1, 2, 3, 4, 6, 8, 12];
export const DASH_STYLES = [
  { id: 'solid', label: 'Solid' }, { id: 'dotted', label: 'Dotted' }, { id: 'dashed', label: 'Dashed' },
  { id: 'dash-dot', label: 'Dash dot' }, { id: 'long-dash', label: 'Long dash' }, { id: 'long-dash-dot', label: 'Long dash dot' },
];
export const OPACITIES = [1, 0.8, 0.6, 0.4, 0.2];
export function dashArray(style, width) {
  const u = Math.max(Number(width) || 1, 2);
  switch (style) {
    case 'dotted': return [0.01, u * 2];
    case 'dashed': return [u * 3, u * 2];
    case 'dash-dot': return [u * 4, u * 2, 0.01, u * 2];
    case 'long-dash': return [u * 6, u * 3];
    case 'long-dash-dot': return [u * 6, u * 2.5, 0.01, u * 2.5];
    default: return null;                                      // solid
  }
}
export const roundCapFor = style => style === 'dotted' || style === 'dash-dot' || style === 'long-dash-dot';

/* ── drag geometry (one definition for every shape tool; page coordinates, so zoom/scroll never matter) ──
   Shift  -> square / circle (equal sides); lines snap to 15° steps.
   Alt    -> draw from the centre: the press point is the middle, not a corner.                                              */
export function constrainBox(start, p, { shift = false, alt = false } = {}) {
  let dx = p.x - start.x, dy = p.y - start.y;
  if (shift) { const s = Math.max(Math.abs(dx), Math.abs(dy)); dx = (dx < 0 ? -1 : 1) * s; dy = (dy < 0 ? -1 : 1) * s; }
  const x0 = alt ? start.x - dx : start.x, y0 = alt ? start.y - dy : start.y, x1 = start.x + dx, y1 = start.y + dy;
  return { left: Math.min(x0, x1), top: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0), flipX: x1 < x0, flipY: y1 < y0,
    begin: { x: x0, y: y0 }, end: { x: x1, y: y1 } };
}
export function constrainLine(start, p, { shift = false, alt = false } = {}) {
  let dx = p.x - start.x, dy = p.y - start.y;
  if (shift) { const len = Math.hypot(dx, dy), step = Math.PI / 12, a = Math.round(Math.atan2(dy, dx) / step) * step; dx = len * Math.cos(a); dy = len * Math.sin(a); }
  const x1 = alt ? start.x - dx : start.x, y1 = alt ? start.y - dy : start.y, x2 = start.x + dx, y2 = start.y + dy;
  return { x1, y1, x2, y2, length: Math.hypot(x2 - x1, y2 - y1), angle: Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI };
}

/* ── path inspection (icons and tests) ── */
export function pathBounds(d) {
  const tokens = d.match(/[MLHVQCAZ]|-?\d*\.?\d+(?:e-?\d+)?/gi) || [];
  let i = 0, cmd = '', x = 0, y = 0, minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const add = (px, py) => { minX = Math.min(minX, px); maxX = Math.max(maxX, px); minY = Math.min(minY, py); maxY = Math.max(maxY, py); };
  const num = () => parseFloat(tokens[i++]);
  while (i < tokens.length) {
    if (/[A-Za-z]/.test(tokens[i])) cmd = tokens[i++];
    if (cmd === 'Z') continue;
    if (cmd === 'M' || cmd === 'L') { x = num(); y = num(); add(x, y); }
    else if (cmd === 'H') { x = num(); add(x, y); }
    else if (cmd === 'V') { y = num(); add(x, y); }
    else if (cmd === 'Q') { const cx = num(), cy = num(); x = num(); y = num(); add(cx, cy); add(x, y); }
    else if (cmd === 'C') { const a = num(), b = num(), c = num(), e = num(); x = num(); y = num(); add(a, b); add(c, e); add(x, y); }
    else if (cmd === 'A') { const rx = num(), ry = num(); num(); const large = num(), sweep = num(); const nx = num(), ny = num();
      const mx = (x + nx) / 2, my = (y + ny) / 2; add(nx, ny); add(mx - rx * 0, my - ry * 0);
      if (Math.abs(nx - x) < 1e-6 && Math.abs(ny - y) < 1e-6) { x = nx; y = ny; continue; }
      // a circle/ellipse arc: include its full extent when it is a large arc (good enough for icon/test bounds)
      if (large) { add(mx - rx, my); add(mx + rx, my); add(mx, my - ry); add(mx, my + ry); }
      x = nx; y = ny; void sweep; }
    else break;
  }
  return { minX, minY, maxX, maxY, width: maxX - minX, height: maxY - minY };
}

const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
/** Small inline SVG (currentColor, so it follows the theme) for a path in the menu. */
export function iconSvg(d, { noFill = false, size = 22, view = null } = {}) {
  const b = view || pathBounds(d), pad = Math.max(b.width, b.height) * 0.06 + 1;
  const vb = [b.minX - pad, b.minY - pad, b.width + 2 * pad, b.height + 2 * pad].map(f).join(' ');
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + vb + '" width="' + size + '" height="' + size + '" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">' +
    '<path d="' + esc(d) + '" fill="' + (noFill ? 'none' : 'currentColor') + '" fill-opacity="' + (noFill ? 0 : 0.13) + '" stroke="currentColor" stroke-width="' + f(Math.max(b.width, b.height) / 14) +
    '" stroke-linejoin="round" stroke-linecap="round"/></svg>';
}
export const allShapeNames = () => SHAPE_MENU.flatMap(c => c.items);
export { rrect, circle, poly };
