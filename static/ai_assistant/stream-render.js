/*
 * static/ai_assistant/stream-render.js — how a reply LOOKS while it is still arriving.
 *
 * Nothing here formats anything itself. The final message is drawn by the Assistant's existing renderer
 * (AiFormatter.format + KaTeX). This module only decides WHAT PART of the text is safe to draw yet, and updates
 * the visible message without rebuilding it:
 *
 *   raw chunks -> buffer -> safeBoundary() -> the SAME format() -> sync into the DOM -> KaTeX on what changed
 *
 *   - safeBoundary(): the longest prefix that contains no half-written construct. An unclosed  $  $$  \(  \[
 *     \ce{  **  __  ` or ``` , a half-typed [SECTION] header or list marker, and a half-typed word all stay in the
 *     buffer until they are complete. Text is never altered, dropped or rewritten: the buffer keeps everything.
 *   - syncHtml(): blocks that are already on screen (with their rendered formulas) are left untouched; only the
 *     block that is still growing is updated, and new blocks are appended.
 *   - StreamView: batches updates (one paint per ~80 ms, aligned to frames) so text does not arrive as hundreds
 *     of tiny pieces, and finishes with the exact final text.
 *
 * Nothing in this file says anything about "thinking": a model that has a separate reasoning stream simply sends
 * events this module never sees.
 */

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

// ── 1. What is safe to show? ─────────────────────────────────────────────────────────────────────────────────────

/**
 * Length of the longest prefix of `text` that can be drawn without showing a half-written construct.
 * The math rules mirror AiFormatter.MATH_RE exactly (so "closed" here means "the renderer will treat it as math"):
 *   $$ ... $$     $ ... $  (one line, no inner $)     \[ ... \]     \( ... \)     \ce{ ... }
 * Growing the text never makes the result smaller (checked by tests/js/stream_safe_boundary.mjs).
 */
export function safeBoundary(text) {
  const n = text.length;
  let i = 0;
  let open = -1;          // where the first still-open construct starts
  let mathEnd = -1;       // where the most recent complete math expression ended

  while (i < n) {
    const c = text[i];

    if (c === '`') {
      if (text.startsWith('```', i)) {                                   // fenced block
        const end = text.indexOf('```', i + 3);
        if (end === -1) { open = i; break; }
        i = end + 3; continue;
      }
      const end = text.indexOf('`', i + 1);                              // inline code, same line only
      const nl = text.indexOf('\n', i + 1);
      if (end !== -1 && (nl === -1 || end < nl)) { i = end + 1; continue; }
      if (nl !== -1) { i += 1; continue; }                               // the line ended: a plain backtick
      open = i; break;
    }

    if (c === '$') {
      if (text[i + 1] === '$') {                                         // display math
        const end = text.indexOf('$$', i + 2);
        if (end === -1) { open = i; break; }
        i = end + 2; mathEnd = i; continue;
      }
      if (i === n - 1) { open = i; break; }                              // a lone "$" at the end may still become "$$"
      let j = i + 1;                                                     // inline math: needs a closing $ on the same line
      while (j < n && text[j] !== '\n' && text[j] !== '$') j++;
      if (j === n) { open = i; break; }                                  // the line is still running: it may close
      if (text[j] === '$') { i = j + 1; mathEnd = i; continue; }
      i += 1; continue;                                                  // newline first: a literal dollar sign
    }

    if (c === '\\') {
      const d = text[i + 1];
      if (d === undefined) { open = i; break; }                          // a lone "\" at the end
      if (d === '(' || d === '[') {
        const end = text.indexOf(d === '(' ? '\\)' : '\\]', i + 2);
        if (end === -1) { open = i; break; }
        i = end + 2; mathEnd = i; continue;
      }
      if (d === 'c') {
        const rest = text.slice(i, i + 4);
        if (rest.length < 4 && '\\ce{'.startsWith(rest)) { open = i; break; }   // "\c" / "\ce" so far
        if (rest === '\\ce{') {
          let depth = 1, k = i + 4;
          while (k < n && depth > 0) { if (text[k] === '{') depth++; else if (text[k] === '}') depth--; k++; }
          if (depth > 0) { open = i; break; }
          i = k; mathEnd = i; continue;
        }
      }
      i += 2; continue;                                                  // any other escape is ordinary text
    }

    if ((c === '*' || c === '_') && text[i + 1] === c) {                 // ** bold **  /  __ bold __
      const end = text.indexOf(c + c, i + 2);
      const nl = text.indexOf('\n', i + 2);
      if (end !== -1 && (nl === -1 || end < nl)) { i = end + 2; continue; }
      if (nl !== -1) { i += 2; continue; }
      open = i; break;
    }

    i += 1;
  }

  // a single trailing * or _ may be the first half of ** / __
  if (open === -1 && (text[n - 1] === '*' || text[n - 1] === '_') && text[n - 2] !== text[n - 1]) open = n - 1;

  let limit = open === -1 ? n : open;

  // a half-typed line start: "[FINAL ANSW", or a bare list marker "2." / "-"
  const head = text.slice(0, limit);
  const ls = head.lastIndexOf('\n') + 1;
  const tail = head.slice(ls);
  if (/^\s*\[[^\]]*$/.test(tail) || /^\s*(?:step\s*)?\d+[.)]?\s*$/i.test(tail) || /^\s*[-•]\s*$/.test(tail)) {
    return ls;
  }

  // never show half a word — unless a finished formula reaches past the last space: then it is part of that word
  // and must not be cut in half
  if (open === -1 && !/\s$/.test(text)) {
    const cut = Math.max(text.lastIndexOf(' '), text.lastIndexOf('\n'), text.lastIndexOf('\t')) + 1;
    if (mathEnd <= cut) limit = cut;
  }
  return limit;
}

// ── 2. Update the DOM without rebuilding it ──────────────────────────────────────────────────────────────────────

/**
 * Make `mount` show exactly `html` (the output of the normal formatter) while touching as little as possible.
 * Every top-level section and every block inside it remembers the markup it was made from (`_sig`); a block whose
 * markup did not change is not touched at all, so its rendered formulas stay. Returns the elements that are new or
 * changed — the only ones that need math rendering.
 */
export function syncHtml(mount, html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const touched = [];
  syncChildren(mount, tpl.content, touched, true);
  return touched;
}

function syncChildren(oldParent, newParent, touched, isTop) {
  const olds = Array.from(oldParent.children);
  const news = Array.from(newParent.children);
  news.forEach((n, i) => {
    const o = olds[i];
    const sig = n.outerHTML;
    if (!o) {
      stamp(n, sig); n.setAttribute('data-new', '');                      // data-new: only drives a short fade-in
      oldParent.appendChild(n); touched.push(n);
      return;
    }
    if (o._sig === sig) return;                                            // unchanged: leave it (and its formulas) alone
    if (isTop && o.classList.contains('msg-section') && n.classList.contains('msg-section') && syncSection(o, n, touched)) {
      o._sig = sig;
      return;
    }
    if (!isTop && o.tagName === n.tagName && o.className === n.className) {
      o.innerHTML = n.innerHTML; o._sig = sig; touched.push(o);            // the block that is still growing
      return;
    }
    stamp(n, sig); oldParent.replaceChild(n, o); touched.push(n);
  });
  for (let k = olds.length - 1; k >= news.length; k--) olds[k].remove();
}

// Remember the markup a node was made from — and, for a whole section, of every block inside it — so that a later
// update can tell "unchanged" (leave it, and its rendered formulas, alone) from "changed".
function stamp(node, sig) {
  node._sig = sig;
  node.querySelectorAll(':scope > .section-body > *').forEach((c) => { c._sig = c.outerHTML; });
}

function syncSection(o, n, touched) {
  const label = (el) => { const l = el.querySelector(':scope > .section-label'); return l ? l.outerHTML : ''; };
  if (label(o) !== label(n)) return false;                                 // a different kind of section: replace it
  const oBody = o.querySelector(':scope > .section-body');
  const nBody = n.querySelector(':scope > .section-body');
  if (!oBody || !nBody) return false;
  syncChildren(oBody, nBody, touched, false);
  return true;
}

// ── 3. The streaming view ───────────────────────────────────────────────────────────────────────────────────────

export class StreamView {
  /**
   * mount()      -> the element the reply is drawn into; called once, when there is something safe to show
   * format(text) -> HTML for `text` (the Assistant's normal formatter)
   * renderMath(el) -> render formulas inside `el` (the Assistant's normal renderer)
   * clean(text)  -> optional: text to draw from the raw buffer (e.g. remove a model's <think> block)
   * hasBody(text)-> optional: false while the safe text has nothing to draw yet (only headers), so nothing stray shows
   * onPaint()    -> called after each visible update (used to follow the bottom of the chat)
   */
  constructor({ mount, format, renderMath, clean = (t) => t, hasBody = (t) => t.trim().length > 0, onPaint = () => {}, minInterval = 80 }) {
    this.o = { mount, format, renderMath, clean, hasBody, onPaint, minInterval };
    this.raw = '';
    this.shown = 0;            // characters of the cleaned text drawn so far; never decreases
    this.el = null;
    this.lastHtml = '';
    this.lastRender = 0;
    this.timer = 0;
    this.raf = 0;
    this.closed = false;
  }

  get hasContent() { return this.el !== null; }

  push(text) {
    if (this.closed || !text) return;
    this.raw += text;
    this._schedule();
  }

  _schedule() {
    if (this.timer || this.raf) return;
    const wait = Math.max(0, this.o.minInterval - (now() - this.lastRender));
    this.timer = setTimeout(() => {
      this.timer = 0;
      this.raf = requestAnimationFrame(() => { this.raf = 0; this._render(false); });
    }, wait);
  }

  _stopTimers() {
    if (this.timer) { clearTimeout(this.timer); this.timer = 0; }
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
  }

  _render(final) {
    const text = this.o.clean(this.raw);
    const limit = final ? text.length : safeBoundary(text);
    if (!final && limit <= this.shown) return;                  // nothing new is safe to show yet
    this.shown = Math.max(this.shown, limit);
    const safe = text.slice(0, this.shown);
    if (!this.o.hasBody(safe)) return;                          // e.g. only a section header so far: wait for its first line
    this.lastRender = now();
    if (!this.el) { this.el = this.o.mount(); this.el.innerHTML = ''; }
    const html = this.o.format(safe);
    if (html === this.lastHtml) return;
    const touched = syncHtml(this.el, html);
    this.lastHtml = html;
    for (const node of touched) this.o.renderMath(node);
    this.o.onPaint();
  }

  /** Stop early (error, interruption): show what is safe right now, drop the unfinished tail, take no more text. */
  flush() {
    this._stopTimers();
    if (!this.closed) this._render(false);
    this.closed = true;
  }

  /**
   * The stream is complete: draw the exact final text through the normal formatter. Blocks that already match are
   * kept as they are; if anything ever failed to line up, the message is simply drawn from scratch.
   */
  finish(finalText) {
    this._stopTimers();
    this.closed = true;
    this.raw = finalText;
    this._render(true);
    if (!this.el) return;
    const want = document.createElement('template');
    want.innerHTML = this.o.format(finalText);
    const sigs = Array.from(want.content.children).map((c) => c.outerHTML);
    const have = Array.from(this.el.children).map((c) => c._sig);
    if (sigs.length !== have.length || sigs.some((s, i) => s !== have[i])) {
      this.el.innerHTML = this.o.format(finalText);
      this.o.renderMath(this.el);
    }
    this.el.querySelectorAll('[data-new]').forEach((e) => e.removeAttribute('data-new'));
  }
}
