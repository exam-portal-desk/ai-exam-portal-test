/* Fabric object -> PDF drawing for the notebook export (works with pdf-writer.js).

   Every object takes the most faithful route it safely can:
     VECTOR  pen/pencil/highlighter strokes, lines, arrows, polylines, rectangles, circles/ellipses, polygons, triangles (and every shape-library path)
             become real PDF paths: fill/stroke colour + alpha, opacity, blend mode (the highlighter's multiply), dash patterns, caps/joins,
             rotation/flip/scale (incl. Fabric's strokeUniform) — sharp at any zoom.
     IMAGE   photographs and imported images are embedded as image XObjects: JPEG bytes untouched when they are safe to pass through,
             otherwise decoded once at a bounded resolution (never more than ~300 dpi at the size they are shown), and de-duplicated.
     RASTER  everything that cannot be drawn exactly as vector (text boxes, sticky notes, shadows, clip paths, gradients/patterns,
             filtered images, groups, unknown types) is rendered ALONE — not the page — onto one reusable scratch canvas at an adaptive
             scale under an explicit pixel budget, and placed as a small image.

   Pure helpers (path normalisation, shape geometry, JPEG header parsing, scale planning) are exported for node tests; everything
   that needs the DOM goes through ObjectPainter, which receives `fabric` and a few factories so it never touches a global. */

import { parseColor } from './ink-theme.js';
import { blendModeFor, isSupportedBlend } from './pdf-writer.js';

const KAPPA = 0.5522847498307936;

// ── pure geometry ─────────────────────────────────────────────────────────────────────────────
export function rectSegments(w, h, rx = 0, ry = 0) {
  const x0 = -w / 2, y0 = -h / 2, x1 = w / 2, y1 = h / 2;
  rx = Math.min(Math.max(rx || 0, 0), w / 2); ry = Math.min(Math.max(ry || 0, 0), h / 2);
  if (!rx || !ry) return [['M', x0, y0], ['L', x1, y0], ['L', x1, y1], ['L', x0, y1], ['Z']];
  const kx = rx * KAPPA, ky = ry * KAPPA;
  return [['M', x0 + rx, y0], ['L', x1 - rx, y0], ['C', x1 - rx + kx, y0, x1, y0 + ry - ky, x1, y0 + ry], ['L', x1, y1 - ry], ['C', x1, y1 - ry + ky, x1 - rx + kx, y1, x1 - rx, y1],
    ['L', x0 + rx, y1], ['C', x0 + rx - kx, y1, x0, y1 - ry + ky, x0, y1 - ry], ['L', x0, y0 + ry], ['C', x0, y0 + ry - ky, x0 + rx - kx, y0, x0 + rx, y0], ['Z']];
}
export function ellipseSegments(rx, ry) {
  const kx = rx * KAPPA, ky = ry * KAPPA;
  return [['M', rx, 0], ['C', rx, ky, kx, ry, 0, ry], ['C', -kx, ry, -rx, ky, -rx, 0], ['C', -rx, -ky, -kx, -ry, 0, -ry], ['C', kx, -ry, rx, -ky, rx, 0], ['Z']];
}
export function polySegments(points, closed) {
  if (!points || points.length < 2) return [];
  const segs = points.map((p, i) => [i ? 'L' : 'M', p.x ?? p[0], p.y ?? p[1]]);
  if (closed) segs.push(['Z']);
  return segs;
}

/** SVG elliptical arc -> cubic Béziers (endpoint parameterisation, per the SVG implementation notes). */
function arcToCubics(x1, y1, rx, ry, phiDeg, large, sweep, x2, y2) {
  if (!rx || !ry || (x1 === x2 && y1 === y2)) return [['L', x2, y2]];
  rx = Math.abs(rx); ry = Math.abs(ry);
  const phi = (phiDeg % 360) * Math.PI / 180, cos = Math.cos(phi), sin = Math.sin(phi);
  const dx = (x1 - x2) / 2, dy = (y1 - y2) / 2;
  const x1p = cos * dx + sin * dy, y1p = -sin * dx + cos * dy;
  const lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
  if (lambda > 1) { const s = Math.sqrt(lambda); rx *= s; ry *= s; }
  const sign = (large === sweep) ? -1 : 1;
  const num_ = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
  const den = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
  const coef = sign * Math.sqrt(Math.max(0, num_ / den));
  const cxp = coef * (rx * y1p / ry), cyp = coef * (-ry * x1p / rx);
  const cx = cos * cxp - sin * cyp + (x1 + x2) / 2, cy = sin * cxp + cos * cyp + (y1 + y2) / 2;
  const ang = (ux, uy, vx, vy) => { const a = Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy); return a; };
  const theta1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
  let delta = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry);
  if (!sweep && delta > 0) delta -= 2 * Math.PI; else if (sweep && delta < 0) delta += 2 * Math.PI;
  const n = Math.max(1, Math.ceil(Math.abs(delta) / (Math.PI / 2) - 1e-9)), step = delta / n, t = 4 / 3 * Math.tan(step / 4);
  const out = [];
  let a1 = theta1;
  for (let i = 0; i < n; i++) {
    const a2 = a1 + step, c1 = Math.cos(a1), s1 = Math.sin(a1), c2 = Math.cos(a2), s2 = Math.sin(a2);
    const p = (px, py) => [cos * rx * px - sin * ry * py + cx, sin * rx * px + cos * ry * py + cy];
    const [ax, ay] = p(c1 - t * s1, s1 + t * c1), [bx, by] = p(c2 + t * s2, s2 - t * c2), [ex, ey] = p(c2, s2);
    out.push(['C', ax, ay, bx, by, i === n - 1 ? x2 : ex, i === n - 1 ? y2 : ey]);
    a1 = a2;
  }
  return out;
}

/** Any SVG-style command list (Fabric's `path`) -> only M, L, C, Z in ABSOLUTE coordinates, shifted by -offset. */
export function normalizePath(cmds, offset = { x: 0, y: 0 }) {
  const out = []; let cx = 0, cy = 0, sx = 0, sy = 0, lastC = null, lastQ = null;
  const ox = offset.x || 0, oy = offset.y || 0;
  const M = (x, y) => { out.push(['M', x - ox, y - oy]); cx = sx = x; cy = sy = y; };
  const L = (x, y) => { out.push(['L', x - ox, y - oy]); cx = x; cy = y; };
  const C = (x1, y1, x2, y2, x, y) => { out.push(['C', x1 - ox, y1 - oy, x2 - ox, y2 - oy, x - ox, y - oy]); lastC = [x2, y2]; cx = x; cy = y; };
  for (const cmd of cmds || []) {
    const t = cmd[0], rel = t === t.toLowerCase() && t !== 'Z', a = cmd.slice(1).map(Number), T = t.toUpperCase();
    const bx = rel ? cx : 0, by = rel ? cy : 0;
    if (T !== 'C' && T !== 'S') lastC = null;
    if (T !== 'Q' && T !== 'T') lastQ = null;
    if (T === 'M') { M(a[0] + bx, a[1] + by); for (let i = 2; i + 1 < a.length; i += 2) L(a[i] + bx, a[i + 1] + by); }
    else if (T === 'L') { for (let i = 0; i + 1 < a.length; i += 2) { const px = a[i] + (rel ? cx : 0), py = a[i + 1] + (rel ? cy : 0); L(px, py); } }
    else if (T === 'H') L(a[0] + bx, cy);
    else if (T === 'V') L(cx, a[0] + by);
    else if (T === 'C') C(a[0] + bx, a[1] + by, a[2] + bx, a[3] + by, a[4] + bx, a[5] + by);
    else if (T === 'S') { const r = lastC ? [2 * cx - lastC[0], 2 * cy - lastC[1]] : [cx, cy]; C(r[0], r[1], a[0] + bx, a[1] + by, a[2] + bx, a[3] + by); }
    else if (T === 'Q' || T === 'T') {
      let qx, qy, ex, ey;
      if (T === 'Q') { qx = a[0] + bx; qy = a[1] + by; ex = a[2] + bx; ey = a[3] + by; }
      else { const r = lastQ ? [2 * cx - lastQ[0], 2 * cy - lastQ[1]] : [cx, cy]; qx = r[0]; qy = r[1]; ex = a[0] + bx; ey = a[1] + by; }
      C(cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy), ex + 2 / 3 * (qx - ex), ey + 2 / 3 * (qy - ey), ex, ey);
      lastC = null; lastQ = [qx, qy];
    }
    else if (T === 'A') { const ex = a[5] + bx, ey = a[6] + by; arcToCubics(cx, cy, a[0], a[1], a[2], !!a[3], !!a[4], ex, ey).forEach(s => { if (s[0] === 'C') C(s[1], s[2], s[3], s[4], s[5], s[6]); else L(s[1], s[2]); }); }
    else if (T === 'Z') { out.push(['Z']); cx = sx; cy = sy; }
  }
  return out;
}

export function multiplyMatrix(a, b) {
  return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]];
}

/** Size-aware raster scale: as sharp as `max` allows while w*h*scale² stays inside the pixel budget (and one canvas side inside `maxSide`). */
export function rasterScaleFor(width, height, { budget = 6_000_000, max = 4, min = 1, maxSide = 8192 } = {}) {
  const w = Math.max(width, 1), h = Math.max(height, 1);
  const byBudget = Math.sqrt(budget / (w * h)), bySide = maxSide / Math.max(w, h);
  return Math.max(Math.min(min, 1), Math.min(max, byBudget, bySide));
}

/** Fraction of an image's own resolution worth keeping when it is shown `displayScale` canvas-px per source px (300 dpi = 3.125 px per page px). */
export function imageKeepFraction(displayScale, { targetPxPerPagePx = 3.125 } = {}) {
  if (!(displayScale > 0)) return 1;
  return Math.min(1, targetPxPerPagePx * displayScale);
}

/** Header facts of a JPEG, or null when it is not one. Used to decide whether its bytes can go into the PDF untouched. */
export function parseJpeg(b) {
  if (!b || b.length < 4 || b[0] !== 0xFF || b[1] !== 0xD8) return null;
  const info = { orientation: 1, adobeTransform: null, width: 0, height: 0, components: 0, bits: 0, sof: 0 };
  let i = 2;
  while (i + 3 < b.length) {
    if (b[i] !== 0xFF) { i++; continue; }
    const marker = b[i + 1];
    if (marker === 0xFF) { i++; continue; }
    if (marker === 0xD8 || marker === 0x01 || (marker >= 0xD0 && marker <= 0xD7)) { i += 2; continue; }
    if (marker === 0xD9 || marker === 0xDA) break;
    const len = (b[i + 2] << 8) | b[i + 3];
    if (marker >= 0xC0 && marker <= 0xCF && marker !== 0xC4 && marker !== 0xC8 && marker !== 0xCC) {
      info.sof = marker; info.bits = b[i + 4]; info.height = (b[i + 5] << 8) | b[i + 6]; info.width = (b[i + 7] << 8) | b[i + 8]; info.components = b[i + 9];
      break;
    }
    if (marker === 0xE1 && b[i + 4] === 0x45 && b[i + 5] === 0x78 && b[i + 6] === 0x69 && b[i + 7] === 0x66) {           // "Exif"
      const t = i + 10, le = b[t] === 0x49;
      const u16 = p => le ? (b[p] | (b[p + 1] << 8)) : ((b[p] << 8) | b[p + 1]);
      const u32 = p => le ? (b[p] | (b[p + 1] << 8) | (b[p + 2] << 16) | (b[p + 3] << 24)) >>> 0 : ((b[p] << 24) | (b[p + 1] << 16) | (b[p + 2] << 8) | b[p + 3]) >>> 0;
      const ifd = t + u32(t + 4);
      if (ifd + 2 < b.length) {
        const n = u16(ifd);
        for (let k = 0; k < n && ifd + 2 + k * 12 + 12 <= b.length; k++) if (u16(ifd + 2 + k * 12) === 0x0112) info.orientation = u16(ifd + 2 + k * 12 + 8) || 1;
      }
    }
    if (marker === 0xEE && b[i + 4] === 0x41 && b[i + 5] === 0x64 && b[i + 6] === 0x6F && b[i + 7] === 0x62 && b[i + 8] === 0x65) info.adobeTransform = b[i + 15];   // "Adobe"
    i += 2 + len;
  }
  return info.width && info.height ? info : null;
}

/** Can these JPEG bytes be embedded as-is (DCTDecode) and look identical to what the browser draws? */
export function jpegPassthroughOk(info, naturalWidth, naturalHeight) {
  if (!info) return false;
  if (![0xC0, 0xC1, 0xC2].includes(info.sof) || info.bits !== 8) return false;      // baseline / extended / progressive Huffman only
  if (info.components !== 1 && info.components !== 3) return false;                 // CMYK etc. go through the canvas
  if (info.orientation !== 1) return false;                                         // the browser applies EXIF rotation, a PDF viewer would not
  if (info.components === 3 && info.adobeTransform === 0) return false;             // RGB stored without YCbCr: needs a ColorTransform override
  if (naturalWidth && naturalHeight && (info.width !== naturalWidth || info.height !== naturalHeight)) return false;
  return true;
}

export function dashArrayOf(arr) {
  if (!Array.isArray(arr) || !arr.length) return null;
  const list = arr.length % 2 ? arr.concat(arr) : arr.slice();
  return list.every(v => !(v > 0)) ? null : list.map(v => Math.max(0, +v || 0));
}

const colorKey = new Map();

// ── the painter ───────────────────────────────────────────────────────────────────────────────
export class ObjectPainter {
  /**
   * @param {object} deps
   * @param {object} deps.writer      PdfWriter
   * @param {object} deps.fabric      the Fabric namespace
   * @param {Function} deps.createCanvas   () => HTMLCanvasElement
   * @param {Function} [deps.fetchBytes]   (src, signal) => Promise<Uint8Array|null>
   * @param {AbortSignal} [deps.signal]
   * @param {object} [deps.limits]    { rasterBudgetPx, maxRasterScale, maxImagePx, maxImageSide }
   */
  constructor({ writer, fabric, createCanvas, fetchBytes, signal, limits = {} }) {
    this.writer = writer; this.fabric = fabric; this.createCanvas = createCanvas; this.signal = signal;
    this.fetchBytes = fetchBytes || (async (src, sig) => {
      const res = await fetch(src, { credentials: 'same-origin', cache: 'force-cache', signal: sig });
      if (!res.ok) return null;
      const len = Number(res.headers.get('Content-Length')) || 0;
      if (len > 48 * 1024 * 1024) return null;
      return new Uint8Array(await res.arrayBuffer());
    });
    this.limits = { rasterBudgetPx: 6_000_000, maxRasterScale: 4, maxImagePx: 12_000_000, maxImageSide: 4096, ...limits };
    this.images = new Map();                   // key -> { ref }  (de-duplicated image XObjects)
    this.scratch = null; this.scratchCanvas = null; this.colorCtx = null;
    this.stats = { vector: 0, image: 0, raster: 0, rasterPx: 0, imageReused: 0, passthrough: 0, failed: [] };
  }

  throwIfAborted() { if (this.signal?.aborted) { const e = new Error('Export cancelled.'); e.name = 'AbortError'; throw e; } }

  /** The scratch canvas is created once and reused for every raster object, then released by dispose(). */
  _scratch(width, height) {
    if (!this.scratch) {
      this.scratchCanvas = this.createCanvas();
      this.scratch = new this.fabric.StaticCanvas(this.scratchCanvas, { renderOnAddRemove: false, enableRetinaScaling: false, skipOffscreen: false });
    }
    this.scratch.setDimensions({ width, height });
    return this.scratch;
  }
  dispose() {
    try { this.scratch?.clear(); this.scratch?.dispose?.(); } catch (e) { /* already gone */ }
    if (this.scratchCanvas) { this.scratchCanvas.width = 0; this.scratchCanvas.height = 0; }
    this.scratch = this.scratchCanvas = null;
    if (this.colorCtx) { this.colorCtx.canvas.width = 0; this.colorCtx = null; }
    this.images.clear();
  }

  /** CSS colour -> {r,g,b,a}; null = invisible; undefined = something we cannot express (gradients, unknown names). */
  resolveColor(value) {
    if (value == null || value === '' || value === 'transparent' || value === 'none') return null;
    if (typeof value !== 'string') return undefined;
    const direct = parseColor(value);
    if (direct) return direct.a <= 0 ? null : direct;
    if (!colorKey.has(value)) {
      try {
        if (!this.colorCtx) { const c = this.createCanvas(); c.width = c.height = 1; this.colorCtx = c.getContext('2d'); }
        this.colorCtx.fillStyle = '#010203'; this.colorCtx.fillStyle = value;
        const normalised = this.colorCtx.fillStyle;
        colorKey.set(value, normalised === '#010203' && value.toLowerCase() !== '#010203' ? undefined : parseColor(normalised) || undefined);
      } catch (e) { colorKey.set(value, undefined); }
    }
    return colorKey.get(value);
  }

  /** Paint one Fabric object into `page` (whose space is canvas pixels, y down). Never throws for a single bad object: falls back, then records. */
  async drawObject(page, obj) {
    this.throwIfAborted();
    if (!obj || obj.visible === false) return;
    try {
      const how = await this._route(page, obj);
      if (how) this.stats[how]++;
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      try { await this._raster(page, obj); this.stats.raster++; }
      catch (error2) { this.stats.failed.push({ type: obj.type, id: obj.objectId || null, message: String(error2?.message || error2).slice(0, 160) }); }
    }
  }

  async _route(page, obj) {
    const type = obj.type;
    if (type === 'image') return this._image(page, obj);
    if (type === 'text' || type === 'i-text' || type === 'textbox') {
      const hasBox = obj.backgroundColor || obj.shadow || obj.stroke && obj.strokeWidth > 0;
      if (!hasBox && !String(obj.text || '').trim()) return null;                  // an empty caption draws nothing: skip the pointless bitmap
      await this._raster(page, obj); return 'raster';
    }
    if (!this._vectorEligible(obj)) { await this._raster(page, obj); return 'raster'; }
    const done = this._vector(page, obj);
    if (!done) { await this._raster(page, obj); return 'raster'; }
    return 'vector';
  }

  _vectorEligible(o) {
    if (['group', 'activeSelection'].includes(o.type)) return false;
    if (o.clipPath || o.shadow || o.skewX || o.skewY) return false;
    if (!isSupportedBlend(o.globalCompositeOperation)) return false;
    if (this.resolveColor(o.fill) === undefined || this.resolveColor(o.stroke) === undefined) return false;
    return ['path', 'rect', 'circle', 'ellipse', 'polygon', 'polyline', 'line', 'triangle'].includes(o.type);
  }

  _segments(o) {
    switch (o.type) {
      case 'path': return normalizePath(o.path, o.pathOffset || { x: 0, y: 0 });
      case 'rect': return rectSegments(o.width, o.height, o.rx, o.ry);
      case 'circle': { const full = ((o.endAngle ?? 360) - (o.startAngle ?? 0)) >= 360 - 1e-6; return full ? ellipseSegments(o.radius, o.radius) : null; }
      case 'ellipse': return ellipseSegments(o.rx, o.ry);
      case 'polygon': case 'polyline': { const off = o.pathOffset || { x: 0, y: 0 }; return polySegments((o.points || []).map(p => ({ x: p.x - off.x, y: p.y - off.y })), o.type === 'polygon'); }
      case 'line': { const p = o.calcLinePoints(); return [['M', p.x1, p.y1], ['L', p.x2, p.y2]]; }
      case 'triangle': return polySegments([[-o.width / 2, o.height / 2], [0, -o.height / 2], [o.width / 2, o.height / 2]], true);
      default: return null;
    }
  }

  _writeSegments(page, segs, T) {
    for (const s of segs) {
      if (s[0] === 'M') page.moveTo(...T(s[1], s[2]));
      else if (s[0] === 'L') page.lineTo(...T(s[1], s[2]));
      else if (s[0] === 'C') page.curveTo(...T(s[1], s[2]), ...T(s[3], s[4]), ...T(s[5], s[6]));
      else page.closePath();
    }
  }

  _vector(page, o) {
    const segs = this._segments(o);
    if (!segs || !segs.length) return segs ? true : false;
    const m = o.calcTransformMatrix(), opacity = o.opacity ?? 1;
    const fill = o.type === 'line' ? null : this.resolveColor(o.fill);
    const stroke = this.resolveColor(o.stroke), strokeWidth = stroke && o.strokeWidth > 0 ? o.strokeWidth : 0;
    if (!fill && !strokeWidth) return true;
    const BM = blendModeFor(o.globalCompositeOperation);
    const uniform = !!o.strokeUniform && !!strokeWidth;
    page.save();
    page.gstate({ ca: (fill ? fill.a : 1) * opacity, CA: (stroke ? stroke.a : 1) * opacity, BM });
    const T = uniform ? (x, y) => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]] : (x, y) => [x, y];
    if (!uniform) page.cm(...m);
    if (fill) page.fillRGB(fill.r, fill.g, fill.b);
    if (strokeWidth) {
      page.strokeRGB(stroke.r, stroke.g, stroke.b); page.lineWidth(strokeWidth);
      page.lineCap(o.strokeLineCap || 'butt'); page.lineJoin(o.strokeLineJoin || 'miter'); page.miterLimit(o.strokeMiterLimit ?? 4);
      page.dash(dashArrayOf(o.strokeDashArray), o.strokeDashOffset || 0);
    }
    const strokeFirst = o.paintFirst === 'stroke' && fill && strokeWidth;
    if (strokeFirst) {
      this._writeSegments(page, segs, T); page.paint('S');
      this._writeSegments(page, segs, T); page.paint('f');
    } else {
      this._writeSegments(page, segs, T);
      page.paint(fill && strokeWidth ? 'B' : fill ? 'f' : 'S');
    }
    page.restore();
    return true;
  }

  // ── adaptive raster fallback (one object at a time, on one reusable canvas) ───────────────────
  async _raster(page, o) {
    this.throwIfAborted();
    const bb = o.getBoundingRect(true, true);
    const sh = o.shadow, shadowPad = sh ? Math.ceil((sh.blur || 0) * 2 + Math.max(Math.abs(sh.offsetX || 0), Math.abs(sh.offsetY || 0))) : 0;
    const pad = 2 + Math.ceil(o.strokeWidth || 0) + shadowPad;
    const left = bb.left - pad, top = bb.top - pad, w = bb.width + pad * 2, h = bb.height + pad * 2;
    if (!(w > 0 && h > 0)) return;
    const scale = rasterScaleFor(w, h, { budget: this.limits.rasterBudgetPx, max: this.limits.maxRasterScale });
    const cw = Math.max(1, Math.ceil(w * scale)), ch = Math.max(1, Math.ceil(h * scale));
    const canvas = this._scratch(cw, ch), wasCaching = o.objectCaching;
    o.objectCaching = false;
    canvas.setViewportTransform([scale, 0, 0, scale, -left * scale, -top * scale]);
    canvas.add(o); canvas.renderAll(); canvas.remove(o);
    o.objectCaching = wasCaching;
    const ctx = canvas.getContext();
    const pixels = ctx.getImageData(0, 0, cw, ch);
    const encoded = this._splitRgba(pixels.data, cw, ch);
    canvas.clear();
    if (!encoded) return;                                                                 // nothing visible
    const ref = await this.writer.addImage({ width: encoded.w, height: encoded.h, data: encoded.rgb, mask: encoded.alpha ? { width: encoded.w, height: encoded.h, data: encoded.alpha } : null });
    this.stats.rasterPx += encoded.w * encoded.h;
    page.save();
    page.gstate({ ca: 1, CA: 1, BM: blendModeFor(o.globalCompositeOperation) });
    page.cm(encoded.w / scale, 0, 0, -encoded.h / scale, left + encoded.x / scale, top + encoded.y / scale + encoded.h / scale);
    page.image(ref);
    page.restore();
  }

  /**
   * RGBA bytes -> { rgb, alpha|null, x, y, w, h } (separate colour and soft-mask planes, as PDF wants them); null when the bitmap is
   * entirely transparent. With `trim` the transparent margin is cut away first — a text box is mostly empty canvas, and compressing
   * and embedding that emptiness was the dominant cost of the raster fallback.
   */
  _splitRgba(rgba, width, height, trim = true) {
    let x0 = 0, y0 = 0, x1 = width, y1 = height;
    if (trim) {
      let top = -1, bottom = -1, left = width, right = -1;
      for (let y = 0; y < height; y++) {
        const row = y * width * 4 + 3;
        let first = -1, last = -1;
        for (let x = 0; x < width; x++) if (rgba[row + x * 4] !== 0) { first = x; break; }
        if (first < 0) continue;
        for (let x = width - 1; x >= first; x--) if (rgba[row + x * 4] !== 0) { last = x; break; }
        if (top < 0) top = y;
        bottom = y; if (first < left) left = first; if (last > right) right = last;
      }
      if (top < 0) return null;
      x0 = left; y0 = top; x1 = right + 1; y1 = bottom + 1;
    }
    const w = x1 - x0, h = y1 - y0, n = w * h, rgb = new Uint8Array(n * 3), alpha = new Uint8Array(n);
    let min = 255, max = 0, q = 0, i = 0;
    for (let y = y0; y < y1; y++) {
      let p = (y * width + x0) * 4;
      for (let x = 0; x < w; x++, p += 4, q += 3, i++) {
        const a = rgba[p + 3];
        rgb[q] = rgba[p]; rgb[q + 1] = rgba[p + 1]; rgb[q + 2] = rgba[p + 2]; alpha[i] = a;
        if (a < min) min = a; if (a > max) max = a;
      }
    }
    if (max === 0) return null;
    return { rgb, alpha: min === 255 ? null : alpha, x: x0, y: y0, w, h };
  }

  // ── images ────────────────────────────────────────────────────────────────────────────────────
  async _image(page, o) {
    if ((o.filters && o.filters.length) || o.clipPath || o.shadow || o.skewX || o.skewY || !isSupportedBlend(o.globalCompositeOperation)) { await this._raster(page, o); return 'raster'; }
    const el = o.getElement && o.getElement();
    if (!el) throw new Error('image not loaded');
    const natW = el.naturalWidth || el.width, natH = el.naturalHeight || el.height;
    const cropX = o.cropX || 0, cropY = o.cropY || 0, sw = o.width, sh = o.height;
    if (!(natW > 0 && natH > 0 && sw > 0 && sh > 0)) throw new Error('image has no size');
    const m = o.calcTransformMatrix();
    const sx = Math.hypot(m[0], m[1]), sy = Math.hypot(m[2], m[3]);
    const keep = imageKeepFraction(Math.max(sx, sy));
    const whole = cropX === 0 && cropY === 0 && Math.abs(sw - natW) <= 1 && Math.abs(sh - natH) <= 1;
    const src = (typeof o.getSrc === 'function' ? o.getSrc() : el.src) || '';
    const tier = keep >= 0.5 ? 'full' : Math.ceil(keep * 8) / 8;
    const key = `${src}|${cropX},${cropY},${sw},${sh}|${tier}`;
    let ref = this.images.get(key);
    if (ref) this.stats.imageReused++;
    else {
      // One look at the file's own bytes: a JPEG within ~2x of the resolution it is shown at goes in untouched (original quality, no decode);
      // anything else is decoded once at a bounded resolution — a JPEG source is re-encoded as JPEG (photos stay small), the rest stays lossless.
      const probe = src && !src.startsWith('data:image/svg') ? await this._probe(src) : null;
      const isJpeg = !!probe?.info;
      if (whole && keep >= 0.5 && probe && jpegPassthroughOk(probe.info, natW, natH)) {
        ref = await this.writer.addImage({ width: probe.info.width, height: probe.info.height, data: probe.bytes, filter: 'DCTDecode', colorSpace: probe.info.components === 1 ? 'DeviceGray' : 'DeviceRGB' });
        this.stats.passthrough++;
      } else ref = await this._decodedImage(el, cropX, cropY, sw, sh, Math.min(keep, 1), isJpeg);
      if (probe) probe.bytes = null;
      this.images.set(key, ref);
    }
    const opacity = o.opacity ?? 1;
    page.save();
    page.gstate({ ca: opacity, CA: opacity, BM: blendModeFor(o.globalCompositeOperation) });
    page.cm(...m); page.cm(sw, 0, 0, -sh, -sw / 2, sh / 2);
    page.image(ref);
    page.restore();
    const stroke = this.resolveColor(o.stroke);
    if (stroke && o.strokeWidth > 0) this._vector(page, Object.assign(Object.create(Object.getPrototypeOf(o)), o, { type: 'rect', fill: null, rx: 0, ry: 0 }));
    return 'image';
  }

  /** Fetch a source once and read its JPEG header (the de-duplicated image map means a repeated image is only probed once). */
  async _probe(src) {
    let bytes = null;
    try { bytes = await this.fetchBytes(src, this.signal); } catch (e) { if (e?.name === 'AbortError') throw e; }
    if (!bytes) return null;
    return { bytes, info: parseJpeg(bytes) };
  }

  async _decodedImage(el, cropX, cropY, sw, sh, keep, asJpeg) {
    const { maxImagePx, maxImageSide } = this.limits;
    const k = Math.min(keep, maxImageSide / Math.max(sw, sh), Math.sqrt(maxImagePx / (sw * sh)));
    const tw = Math.max(1, Math.round(sw * k)), th = Math.max(1, Math.round(sh * k));
    const canvas = this.createCanvas(); canvas.width = tw; canvas.height = th;
    try {
      const ctx = canvas.getContext('2d', { willReadFrequently: !asJpeg });
      ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(el, cropX, cropY, sw, sh, 0, 0, tw, th);
      if (asJpeg) {
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.92));
        if (blob) return await this.writer.addImage({ width: tw, height: th, data: new Uint8Array(await blob.arrayBuffer()), filter: 'DCTDecode', colorSpace: 'DeviceRGB' });
      }
      const pixels = ctx.getImageData(0, 0, tw, th);
      const enc = this._splitRgba(pixels.data, tw, th, false) || { rgb: new Uint8Array(tw * th * 3), alpha: null };
      return await this.writer.addImage({ width: tw, height: th, data: enc.rgb, mask: enc.alpha ? { width: tw, height: th, data: enc.alpha } : null });
    } finally { canvas.width = 0; canvas.height = 0; }
  }

  /** A short piece of text the standard fonts cannot spell (page titles in other scripts) as a sharp bitmap, in PDF page space (y up). */
  async drawTextBitmap(page, text, { x, y, size = 11, bold = true, color = '#2c3e50', family = 'DM Sans, sans-serif' } = {}) {
    const scale = 4, canvas = this.createCanvas(), ctx = canvas.getContext('2d');
    const font = `${bold ? '700 ' : ''}${size * scale}px ${family}`;
    ctx.font = font;
    const width = Math.ceil(ctx.measureText(text).width) + 4 * scale, height = Math.ceil(size * 1.5 * scale);
    canvas.width = Math.min(width, 16000); canvas.height = height;
    ctx.font = font; ctx.fillStyle = color; ctx.textBaseline = 'alphabetic';
    ctx.fillText(text, 2 * scale, size * 1.15 * scale);
    const cw = canvas.width, ch = canvas.height;
    const enc = this._splitRgba(ctx.getImageData(0, 0, cw, ch).data, cw, ch);
    canvas.width = 0;
    if (!enc) return;
    const ref = await this.writer.addImage({ width: enc.w, height: enc.h, data: enc.rgb, mask: enc.alpha ? { width: enc.w, height: enc.h, data: enc.alpha } : null });
    // PDF page space here is y-up and an image's first row is its top, so no flip: anchor the trimmed bitmap's bottom-left corner
    const bottom = y - size * 0.35 + (ch - enc.y - enc.h) / scale;
    page.save(); page.cm(enc.w / scale, 0, 0, enc.h / scale, x - 2 + enc.x / scale, bottom); page.image(ref); page.restore();
  }
}
