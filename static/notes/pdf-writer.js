/* Minimal streaming PDF writer for the notebook export (pure JS, no DOM, no third-party code).

   WHY THIS EXISTS: the export used to rasterise every page to ONE 2400x1600 PNG and hand it to jsPDF. jsPDF keeps every page's
   content and every image in memory until save(), has no blend modes (the highlighter's multiply), and cannot embed JPEG bytes
   untouched. This writer appends each finished page/image to a list of Blob parts as soon as it is produced — a Blob is not
   held on the JS heap (browsers keep large Blobs in their own, disk-backed storage) — so peak memory follows ONE page, not the
   whole document. It writes classic PDF 1.5: Catalog, Pages, Helvetica (header/footer only), per-page content streams,
   Flate/DCT image XObjects with optional soft masks, ExtGState (opacity + blend mode) and one shared tiling pattern.

   `deflate` is injected so node tests can use zlib; in the browser it defaults to CompressionStream('deflate'), whose
   output (zlib format) is exactly what /FlateDecode expects. Without either, streams are written uncompressed (still valid). */

const ENC = new TextEncoder();

const WIN_ANSI_HIGH = { 0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87, 0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8A, 0x2039: 0x8B, 0x0152: 0x8C, 0x017D: 0x8E,
  0x2018: 0x91, 0x2019: 0x92, 0x201C: 0x93, 0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02DC: 0x98, 0x2122: 0x99, 0x0161: 0x9A, 0x203A: 0x9B, 0x0153: 0x9C, 0x017E: 0x9E, 0x0178: 0x9F };

/** True when every character is representable in the standard fonts' WinAnsi encoding. */
export function isWinAnsi(text) {
  for (const ch of String(text)) { const c = ch.codePointAt(0); if (!(c === 9 || (c >= 32 && c < 127) || (c >= 160 && c <= 255) || WIN_ANSI_HIGH[c])) return false; }
  return true;
}

/** A PDF literal string (ASCII only: high bytes as octal escapes). Unrepresentable characters become '?'. */
export function pdfString(text) {
  let out = '(';
  for (const ch of String(text)) {
    let c = ch.codePointAt(0);
    if (WIN_ANSI_HIGH[c]) c = WIN_ANSI_HIGH[c]; else if (c > 255 || (c < 32 && c !== 9)) c = 63;
    if (c === 40 || c === 41 || c === 92) out += '\\' + String.fromCharCode(c);
    else if (c >= 32 && c < 127) out += String.fromCharCode(c);
    else out += '\\' + c.toString(8).padStart(3, '0');
  }
  return out + ')';
}

// Helvetica advance widths (1/1000 em) for ASCII 32..126 — only used to right-align the footer.
const HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015,
  667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333,
  556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584];
export function helveticaWidth(text, size) {
  let w = 0;
  for (const ch of String(text)) { const c = ch.codePointAt(0); w += (c >= 32 && c <= 126) ? HELV[c - 32] : 556; }
  return w * size / 1000;
}

/** Number formatting that can never produce an exponent or NaN (both are invalid in a content stream). */
export function num(v) {
  if (!Number.isFinite(v)) return '0';
  if (v > 1e9) v = 1e9; else if (v < -1e9) v = -1e9;
  const r = Math.round(v * 10000) / 10000;
  return Object.is(r, -0) ? '0' : String(r);
}

const BLEND = { 'source-over': null, multiply: 'Multiply', screen: 'Screen', overlay: 'Overlay', darken: 'Darken', lighten: 'Lighten', 'color-dodge': 'ColorDodge', 'color-burn': 'ColorBurn',
  'hard-light': 'HardLight', 'soft-light': 'SoftLight', difference: 'Difference', exclusion: 'Exclusion', hue: 'Hue', saturation: 'Saturation', color: 'Color', luminosity: 'Luminosity' };
export const blendModeFor = op => (op && Object.prototype.hasOwnProperty.call(BLEND, op)) ? BLEND[op] : null;
export const isSupportedBlend = op => !op || Object.prototype.hasOwnProperty.call(BLEND, op);

async function defaultDeflate(bytes) {
  if (typeof CompressionStream === 'undefined') return null;
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export class PageBuilder {
  constructor(writer, width, height) {
    this.writer = writer; this.width = width; this.height = height;
    this.ops = []; this.gstates = new Map(); this.images = new Set(); this.fonts = new Set(); this.patterns = new Set();
  }
  raw(s) { this.ops.push(s); return this; }
  save() { return this.raw('q'); }
  restore() { return this.raw('Q'); }
  cm(a, b, c, d, e, f) { return this.raw(`${num(a)} ${num(b)} ${num(c)} ${num(d)} ${num(e)} ${num(f)} cm`); }
  fillRGB(r, g, b) { return this.raw(`${num(r / 255)} ${num(g / 255)} ${num(b / 255)} rg`); }
  strokeRGB(r, g, b) { return this.raw(`${num(r / 255)} ${num(g / 255)} ${num(b / 255)} RG`); }
  lineWidth(w) { return this.raw(`${num(w)} w`); }
  lineCap(kind) { return this.raw(`${kind === 'round' ? 1 : kind === 'square' ? 2 : 0} J`); }
  lineJoin(kind) { return this.raw(`${kind === 'round' ? 1 : kind === 'bevel' ? 2 : 0} j`); }
  miterLimit(v) { return this.raw(`${num(Math.max(1, v || 10))} M`); }
  dash(array, phase = 0) { return this.raw(array && array.length ? `[${array.map(num).join(' ')}] ${num(phase)} d` : '[] 0 d'); }
  moveTo(x, y) { return this.raw(`${num(x)} ${num(y)} m`); }
  lineTo(x, y) { return this.raw(`${num(x)} ${num(y)} l`); }
  curveTo(x1, y1, x2, y2, x3, y3) { return this.raw(`${num(x1)} ${num(y1)} ${num(x2)} ${num(y2)} ${num(x3)} ${num(y3)} c`); }
  rect(x, y, w, h) { return this.raw(`${num(x)} ${num(y)} ${num(w)} ${num(h)} re`); }
  closePath() { return this.raw('h'); }
  /** 'S' stroke, 'f' fill (non-zero, like canvas), 'B' fill+stroke, 'n' no-op end of path. */
  paint(op) { return this.raw(op); }
  /** Opacity (fill `ca`, stroke `CA`) and optional blend mode as a named ExtGState. */
  gstate({ ca = 1, CA = 1, BM = null } = {}) {
    const clamp = v => Math.min(1, Math.max(0, Number.isFinite(v) ? v : 1));
    const key = `${num(clamp(ca))}|${num(clamp(CA))}|${BM || ''}`;
    let name = this.gstates.get(key)?.name;
    if (!name) { name = `G${this.gstates.size}`; this.gstates.set(key, { name, ca: clamp(ca), CA: clamp(CA), BM }); }
    return this.raw(`/${name} gs`);
  }
  /** Paint an image XObject (unit square) with the current transform. */
  image(ref) { this.images.add(ref); return this.raw(`/I${ref} Do`); }
  fillPattern(name) { this.patterns.add(name); return this.raw(`/Pattern cs /${name} scn`); }
  /** Standard-font text; `font` is 'F1' (Helvetica) or 'F2' (Helvetica-Bold). Text must be WinAnsi (see isWinAnsi). */
  text(str, x, y, { font = 'F1', size = 10, color = [0, 0, 0], align = 'left' } = {}) {
    this.fonts.add(font);
    const width = align === 'right' ? helveticaWidth(str, size) : 0;
    return this.raw(`BT /${font} ${num(size)} Tf ${num(color[0] / 255)} ${num(color[1] / 255)} ${num(color[2] / 255)} rg ${num(x - width)} ${num(y)} Td ${pdfString(str)} Tj ET`);
  }
}

export class PdfWriter {
  /**
   * @param {{deflate?: (bytes: Uint8Array) => Promise<Uint8Array|null>|Uint8Array|null, title?: string}} [options]
   */
  constructor({ deflate = defaultDeflate, title = '' } = {}) {
    this.deflate = deflate; this.title = title;
    this.parts = []; this.offset = 0; this.xref = new Map(); this.pageRefs = []; this.nextNum = 5; this.patternDefs = new Map(); this.closed = false;
    this.stats = { pages: 0, images: 0, imageBytes: 0, streamBytes: 0 };
    this._push(Uint8Array.of(0x25, 0x50, 0x44, 0x46, 0x2D, 0x31, 0x2E, 0x35, 0x0A, 0x25, 0xE2, 0xE3, 0xCF, 0xD3, 0x0A));
    this._object(3, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
    this._object(4, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');
  }

  _push(piece) {
    const bytes = typeof piece === 'string' ? ENC.encode(piece) : piece;
    this.parts.push(bytes); this.offset += bytes.byteLength;
    if (this.parts.length > 48) this.parts = [new Blob(this.parts)];   // fold into one Blob reference: keeps the JS heap flat
  }
  _begin(num_) { this.xref.set(num_, this.offset); this._push(`${num_} 0 obj\n`); }
  _object(num_, body) { this._begin(num_); this._push(`${body}\nendobj\n`); }
  async _stream(num_, dict, bytes, { compress = true } = {}) {
    let data = bytes, filter = '';
    if (compress && bytes.byteLength > 64 && this.deflate) {
      const packed = await this.deflate(bytes);
      if (packed && packed.byteLength < bytes.byteLength) { data = packed; filter = ' /Filter /FlateDecode'; }
    }
    this._begin(num_);
    this._push(`<< ${dict}${filter} /Length ${data.byteLength} >>\nstream\n`);
    this._push(data); this._push('\nendstream\nendobj\n');
    this.stats.streamBytes += data.byteLength;
    return num_;
  }
  _alloc() { return this.nextNum++; }

  /**
   * Define the shared dot-grid tiling pattern once (a coloured pattern: every page stamps it with `fillPattern`). `origin` is the
   * page-space (PDF, y-up) position of one tile's lower-left corner.
   */
  async definePattern(name, { tile, dot, origin, color, alpha = 1, background = null }) {
    if (this.patternDefs.has(name)) return;
    const num_ = this._alloc();
    const [r, g, b] = color.map(v => v / 255);
    const d = dot, o = (tile - d) / 2;
    const content = `${num(r)} ${num(g)} ${num(b)} rg ${num(o)} ${num(o)} ${num(d)} ${num(d)} re f`;
    const gs = alpha < 1 ? ` /Resources << /ExtGState << /GA << /ca ${num(alpha)} >> >> >>` : ' /Resources << >>';
    await this._stream(num_, `/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 ${num(tile)} ${num(tile)}] /XStep ${num(tile)} /YStep ${num(tile)}${gs} /Matrix [1 0 0 1 ${num(origin[0])} ${num(origin[1])}]`,
      ENC.encode(alpha < 1 ? `/GA gs ${content}` : content));
    this.patternDefs.set(name, num_);
  }

  /**
   * Add an image XObject once and return its object number. `image` = { width, height, data, filter: 'DCTDecode'|'FlateDecode'|'none',
   * colorSpace: 'DeviceRGB'|'DeviceGray', mask?: { width, height, data } } — `data` is already encoded for `filter`
   * (raw samples when 'none'/'FlateDecode' raw -> compressed here).
   */
  async addImage({ width, height, data, filter = 'none', colorSpace = 'DeviceRGB', mask = null }) {
    let smaskRef = null;
    if (mask) smaskRef = await this.addImage({ width: mask.width, height: mask.height, data: mask.data, filter: 'none', colorSpace: 'DeviceGray' });
    const ref = this._alloc();
    const base = `/Type /XObject /Subtype /Image /Width ${width} /Height ${height} /ColorSpace /${colorSpace} /BitsPerComponent 8${smaskRef ? ` /SMask ${smaskRef} 0 R` : ''}`;
    if (filter === 'DCTDecode') await this._stream(ref, `${base} /Filter /DCTDecode`, data, { compress: false });
    else await this._stream(ref, base, data, { compress: true });
    this.stats.images++; this.stats.imageBytes += data.byteLength;
    return ref;
  }

  beginPage(width, height) { return new PageBuilder(this, width, height); }

  /** Write a finished page (content stream + page dictionary). The builder can be discarded afterwards. */
  async endPage(page) {
    const content = ENC.encode(page.ops.join('\n') + '\n');
    const contentRef = this._alloc(), pageRef = this._alloc();
    await this._stream(contentRef, '', content);
    const res = [];
    if (page.gstates.size) res.push('/ExtGState << ' + [...page.gstates.values()].map(g => `/${g.name} << /Type /ExtGState /ca ${num(g.ca)} /CA ${num(g.CA)}${g.BM ? ` /BM /${g.BM}` : ''} >>`).join(' ') + ' >>');
    if (page.images.size) res.push('/XObject << ' + [...page.images].map(r => `/I${r} ${r} 0 R`).join(' ') + ' >>');
    if (page.fonts.size) res.push('/Font << ' + [...page.fonts].map(f => `/${f} ${f === 'F2' ? 4 : 3} 0 R`).join(' ') + ' >>');
    if (page.patterns.size) res.push('/Pattern << ' + [...page.patterns].map(p => `/${p} ${this.patternDefs.get(p)} 0 R`).join(' ') + ' >>');
    this._object(pageRef, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${num(page.width)} ${num(page.height)}] /Resources << ${res.join(' ')} >> /Contents ${contentRef} 0 R >>`);
    this.pageRefs.push(pageRef); this.stats.pages++;
    page.ops = []; page.gstates.clear(); page.images.clear();
  }

  /** Finish the document and return it as a Blob (or, with `asParts`, the raw part list — used by node tests). */
  finish({ asParts = false } = {}) {
    if (!this.pageRefs.length) throw new Error('No pages to export.');
    const infoRef = this._alloc();
    const created = new Date();
    const pad = n => String(n).padStart(2, '0');
    const stamp = `D:${created.getUTCFullYear()}${pad(created.getUTCMonth() + 1)}${pad(created.getUTCDate())}${pad(created.getUTCHours())}${pad(created.getUTCMinutes())}${pad(created.getUTCSeconds())}Z`;
    this._object(1, '<< /Type /Catalog /Pages 2 0 R >>');
    this._object(2, `<< /Type /Pages /Count ${this.pageRefs.length} /Kids [${this.pageRefs.map(r => `${r} 0 R`).join(' ')}] >>`);
    this._object(infoRef, `<< /Title ${pdfString(this.title)} /Producer (Sarvadwar notebook export) /CreationDate (${stamp}) >>`);
    const xrefAt = this.offset, size = this.nextNum;
    let table = `xref\n0 ${size}\n0000000000 65535 f \n`;
    for (let i = 1; i < size; i++) table += `${String(this.xref.get(i) ?? 0).padStart(10, '0')} 00000 ${this.xref.has(i) ? 'n' : 'f'} \n`;
    this._push(table + `trailer\n<< /Size ${size} /Root 1 0 R /Info ${infoRef} 0 R >>\nstartxref\n${xrefAt}\n%%EOF\n`);
    this.closed = true;
    const parts = this.parts; this.parts = [];
    return asParts ? parts : new Blob(parts, { type: 'application/pdf' });
  }

  /** Drop everything written so far (cancel / failure). */
  discard() { this.parts = []; this.xref.clear(); this.pageRefs = []; this.closed = true; }
}
