/* static/shared/ai-explain.js
 * Turns a saved AI Explanation (Markdown + LaTeX) into finished, safe HTML.
 *
 * Same approach as the AI Assistant (static/ai_assistant/chat.js, AiFormatter): every math region is set aside first
 * so the Markdown rules can never touch it, the text is formatted, and the math is put back. What differs is what
 * comes out: the Assistant formats its own [SECTION] replies, an explanation is ordinary Markdown (headings, numbered
 * steps, lists, tables, code).
 *
 * The whole explanation is rendered here, in memory, BEFORE anything is shown: math is turned into finished KaTeX HTML
 * (the engine the Assistant uses, loaded by base.html) while the text is built, then the caller inserts one finished
 * string. There is never a half-drawn formula on the page, and nothing to rebuild or re-typeset afterwards.
 * A formula KaTeX cannot handle is handed to MathJax (also loaded by base.html) instead of being left as raw TeX.
 *
 * Only presentation is added: the wording of the explanation is never changed. (The decorative glyph an older
 * explanation put in front of a heading, e.g. "◈ Understanding the Question", is not shown; the heading gets one clean
 * icon instead.) Text is always HTML-escaped; the only markup in the output is what this file builds.
 */

// The Assistant's definition of "math" (AiFormatter.MATH_RE in chat.js) with ONE deliberate difference: a single-$
// formula may continue onto the next line (models do write "$\Large⏎ a = b ⏎$"), but never across a blank line, so a stray
// dollar sign cannot swallow a paragraph. tests/test_explanation_ui.py fails if the two ever drift apart otherwise.
export const MATH_RE = /\$\$[\s\S]+?\$\$|\$(?:[^\n$]|\n(?!\s*\n))+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\\ce\{(?:[^{}]|\{[^{}]*\})*\}/g;

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const MATH_TOKEN = /\u0000M(\d+)\u0000/g;
const CODE_TOKEN = /\u0001C(\d+)\u0001/g;

// ── math ────────────────────────────────────────────────────────────────────────────────────────────────────────
function splitMath(text) {
  const items = [];
  const out = String(text).replace(MATH_RE, (m) => {
    let tex = m, display = false;
    if (m.startsWith('$$') || m.startsWith('\\[')) { tex = m.slice(2, -2); display = true; }
    else if (m.startsWith('\\(')) { tex = m.slice(2, -2); }
    else if (m.startsWith('$')) { tex = m.slice(1, -1); }
    items.push({ tex: tex.trim(), display });          // a bare \ce{...} keeps its text as the formula
    return `\u0000M${items.length - 1}\u0000`;
  });
  // An opening $$ or \[ with no closer left over means the text stops in the middle of a formula (a generation that was
  // cut off). Raw, half-written TeX is never shown: the unfinished tail is replaced by a short note.
  const open = out.search(/\$\$|\\\[/);
  if (open < 0) return { text: out, items };
  items.push({ cut: true, display: true, tex: '' });
  return { text: out.slice(0, open) + `\u0000M${items.length - 1}\u0000`, items };
}

const CUT_NOTE = '<span class="aix-cut"><i class="fas fa-scissors" aria-hidden="true"></i> The explanation ends here: it was cut off while it was being written.</span>';

// The model decides formula sizes formula by formula (\Large on one, nothing on the next, \normalsize on a third), so the same
// explanation used to show big and small equations side by side. Sizes are the page's job: every font-size command is
// removed and ONE size is set in CSS (.aix .katex). An inline formula with a fraction, sum, integral, ... is set in display
// style, so it is as large and legible as the same fraction written as \dfrac (which the prompt asks for) would be.
const SIZE_COMMANDS = /\\(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)(?![A-Za-z])\s*/g;
const NEEDS_DISPLAY_STYLE = /\\(?:frac|dfrac|sum|int|prod|lim|binom|begin\{[pbvBV]?matrix\})/;

export function normalizeTex(tex, display) {
  let out = String(tex).replace(SIZE_COMMANDS, '').trim();
  if (!display && NEEDS_DISPLAY_STYLE.test(out) && !/^\\displaystyle\b/.test(out)) out = '\\displaystyle ' + out;
  return out;
}

function mathHtml(item, ctx) {
  if (item.cut) return CUT_NOTE;
  const tex = normalizeTex(item.tex, item.display);
  if (ctx.katex) {
    try {
      const html = ctx.katex.renderToString(tex, { displayMode: item.display, throwOnError: true, strict: 'ignore', trust: false });
      return item.display ? `<span class="aix-eq">${html}</span>` : html;
    } catch (e) { /* not something KaTeX understands: MathJax gets it below */ }
  }
  ctx.fallbacks++;
  const src = item.display ? `\\[${tex}\\]` : `\\(${tex}\\)`;
  return `<span class="aix-mj${item.display ? ' aix-mj--display' : ''}" data-src="${esc(tex)}">${esc(src)}</span>`;
}

// ── inline Markdown ─────────────────────────────────────────────────────────────────────────────────────────────
function inline(text, ctx) {
  let s = esc(text);
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0001C${codes.length - 1}\u0001`; });
  // (no regex look-behind anywhere in this file: Safari before 16.4 cannot even parse it)
  s = s.replace(/\*\*(\S(?:.*?\S)?)\*\*/g, '<strong>$1</strong>').replace(/__(\S(?:.*?\S)?)__/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?!\*)/g, '$1<em>$2</em>');
  s = s.replace(/(^|[\s(])_([^\s_](?:[^_\n]*?[^\s_])?)_(?=$|[\s).,;:!?])/g, '$1<em>$2</em>');
  s = s.replace(CODE_TOKEN, (_, i) => `<code>${codes[+i]}</code>`);
  return s.replace(MATH_TOKEN, (_, i) => mathHtml(ctx.items[+i], ctx));
}

// ── headings: which kind of section is this? ────────────────────────────────────────────────────────────────────
const KINDS = [                                       // order matters: the first match wins
  ['wrong',      /(why|how).*(wrong|incorrect)|mistake|misconception/i, 'fa-circle-xmark'],
  ['answer',     /^(final|correct)\s+answer|^answer\b|^result\b|^conclusion\b/i, 'fa-circle-check'],
  ['given',      /^(given|known|data|knowns)\b/i, 'fa-list-ul'],
  ['concept',    /key\s+concept|^concept\b|principle|takeaway|to\s+remember|formula/i, 'fa-lightbulb'],
  ['tip',        /quick\s+tip|^tip\b|shortcut|trick/i, 'fa-bolt'],
  ['note',       /^(note|important|warning|caution)\b/i, 'fa-circle-info'],
  ['solution',   /solution|^steps?\b|working|derivation|calculation|method|approach/i, 'fa-list-ol'],
  ['understand', /understanding|^(the\s+)?question\b|analysis|overview|^setup\b/i, 'fa-magnifying-glass'],
];

function headingInfo(raw) {
  // a decorative glyph in front of the words (◈ ⊘ ◉ ✦ ➤ …) is dropped from the display, never from the stored text
  const label = String(raw).replace(/^[^\p{L}\p{N}\u0000(\[$\\]+/u, '').replace(/[:：]\s*$/, '').trim() || String(raw).trim();
  const plain = label.replace(/\u0000M\d+\u0000/g, '').replace(/[*_`]/g, '').trim();
  for (const [kind, re, icon] of KINDS) if (re.test(plain)) return { label, kind, icon };
  return { label, kind: 'plain', icon: '' };
}

// ── blocks ──────────────────────────────────────────────────────────────────────────────────────────────────────
const R = {
  fence: /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/,
  heading: /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/,
  hr: /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/,
  li: /^(\s*)([-*+•]|\d+[.)])\s+(.*)$/,
  quote: /^\s{0,3}>\s?(.*)$/,
  tableSep: /^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$/,
  mathOnly: /^\s*\u0000M(\d+)\u0000\s*$/,
};
const indentOf = (l) => l.match(/^\s*/)[0].replace(/\t/g, '    ').length;
const isTableStart = (lines, i) => i + 1 < lines.length && lines[i].includes('|') && R.tableSep.test(lines[i + 1]) && lines[i + 1].includes('-');
const isBlockStart = (lines, i, ctx) => {
  const l = lines[i];
  return R.fence.test(l) || R.heading.test(l) || R.hr.test(l) || R.li.test(l) || R.quote.test(l) || isTableStart(lines, i)
    || (R.mathOnly.test(l) && ctx.items[+R.mathOnly.exec(l)[1]].display);
};

const CALLOUT = [                                     // a paragraph that opens with one of these labels stands out a little
  ['answer', /^(?:\*\*|__)?\s*(?:final\s+answer|correct\s+answer|answer)\s*(?:\*\*|__)?\s*[:：]/i],
  ['warning', /^(?:\*\*|__)?\s*(?:warning|caution)\s*(?:\*\*|__)?\s*[:：]/i],
  ['note', /^(?:\*\*|__)?\s*(?:note|important|remember|tip)\s*(?:\*\*|__)?\s*[:：]/i],
];

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => c.trim());
}

function parseBlocks(lines, ctx) {
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    let m = R.fence.exec(line);
    if (m) {
      const body = []; i++;
      while (i < lines.length && !(lines[i].trim().startsWith(m[1]) && /^\s*[`~]+\s*$/.test(lines[i]))) { body.push(lines[i]); i++; }
      i++;
      blocks.push({ t: 'code', text: body.join('\n') });
      continue;
    }
    if ((m = R.heading.exec(line))) { blocks.push({ t: 'heading', level: m[1].length, text: m[2] }); i++; continue; }
    if (R.hr.test(line)) { blocks.push({ t: 'hr' }); i++; continue; }

    if ((m = R.mathOnly.exec(line)) && ctx.items[+m[1]].display) { blocks.push({ t: 'math', html: mathHtml(ctx.items[+m[1]], ctx) }); i++; continue; }

    if (isTableStart(lines, i)) {
      const head = splitRow(lines[i]);
      const align = splitRow(lines[i + 1]).map((c) => (/^:-+:$/.test(c) ? 'center' : /-:$/.test(c) ? 'right' : ''));
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) { rows.push(splitRow(lines[i])); i++; }
      blocks.push({ t: 'table', head, align, rows });
      continue;
    }

    if (R.quote.test(line)) {
      const inner = [];
      while (i < lines.length && (m = R.quote.exec(lines[i]))) { inner.push(m[1]); i++; }
      blocks.push({ t: 'quote', blocks: parseBlocks(inner, ctx) });
      continue;
    }

    if ((m = R.li.exec(line))) {
      const base = m[1].replace(/\t/g, '    ').length, ordered = /\d/.test(m[2]);
      const items = [];
      while (i < lines.length) {
        let next = i; while (next < lines.length && !lines[next].trim()) next++;        // blank lines between items do not end a list
        const mi = R.li.exec(lines[next] || '');
        if (!mi || Math.abs(mi[1].replace(/\t/g, '    ').length - base) > 1 || /\d/.test(mi[2]) !== ordered) break;
        i = next;
        const contentIndent = base + mi[2].length + 1;
        const body = [mi[3]];
        const num = ordered ? parseInt(mi[2], 10) : null;
        i++;
        while (i < lines.length) {
          const l = lines[i];
          if (!l.trim()) {                             // blank line: the item goes on only if what follows still belongs to it
            let j = i; while (j < lines.length && !lines[j].trim()) j++;
            if (j < lines.length && (indentOf(lines[j]) > base + 1 || (R.mathOnly.test(lines[j]) && !R.li.test(lines[j])))) { body.push(''); i = j; continue; }
            break;
          }
          const ind = indentOf(l);
          if (ind > base + 1) { body.push(l.slice(Math.min(ind, contentIndent))); i++; continue; }   // nested content
          if (R.mathOnly.test(l)) { body.push(l.trim()); i++; continue; }                            // an equation under a step belongs to it
          if (isBlockStart(lines, i, ctx)) break;                                                    // the next item, a heading, ...
          body.push(l.trim()); i++;                                                                  // the same paragraph, wrapped
        }
        items.push({ n: num, blocks: parseBlocks(body, ctx) });
      }
      blocks.push({ t: 'list', ordered, items });
      continue;
    }

    const para = [line.trim()]; i++;
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines, i, ctx)) { para.push(lines[i].trim()); i++; }
    blocks.push({ t: 'para', lines: para });
  }
  return blocks;
}

// ── HTML ────────────────────────────────────────────────────────────────────────────────────────────────────────
const BOLD_STEP = /^\*\*\s*((?:step\s*)?\d+\s*[.):-].*?)\s*:?\s*\*\*\s*:?$/i;    // "**1. Calculate the areas:**" written instead of a list item

function paragraphHtml(lines, ctx) {
  if (lines.length === 1) {
    const step = BOLD_STEP.exec(lines[0].trim());
    if (step) return `<h5 class="aix-h3 aix-h3--step">${inline(step[1], ctx)}</h5>`;
  }
  const text = lines.join('\n');
  const html = lines.map((l) => inline(l, ctx)).join('<br>');
  for (const [kind, re] of CALLOUT) if (re.test(text)) return `<p class="aix-callout aix-callout--${kind}">${html}</p>`;
  return `<p>${html}</p>`;
}

function itemHtml(blocks, ctx) {
  // the item's own text is written straight into it (no paragraph box); whatever follows (an equation, a nested list) goes after
  if (blocks.length && blocks[0].t === 'para') return blocks[0].lines.map((l) => inline(l, ctx)).join('<br>') + blocksHtml(blocks.slice(1), ctx);
  return blocksHtml(blocks, ctx);
}

function blockHtml(b, ctx) {
  switch (b.t) {
    case 'para': return paragraphHtml(b.lines, ctx);
    case 'math': return b.html;
    case 'hr': return '<hr class="aix-hr">';
    case 'code': return `<pre class="aix-code"><code>${esc(b.text)}</code></pre>`;
    case 'quote': return `<blockquote class="aix-quote">${blocksHtml(b.blocks, ctx)}</blockquote>`;
    case 'list': {
      const tag = b.ordered ? 'ol' : 'ul';
      const lis = b.items.map((it, k) => `<li${b.ordered ? ` data-n="${it.n ?? k + 1}"` : ''}>${itemHtml(it.blocks, ctx)}</li>`).join('');
      return `<${tag} class="${b.ordered ? 'aix-steps' : 'aix-list'}">${lis}</${tag}>`;
    }
    case 'table': {
      const cell = (tag, c, k) => `<${tag}${b.align[k] ? ` style="text-align:${b.align[k]}"` : ''}>${inline(c, ctx)}</${tag}>`;
      return `<div class="aix-table-wrap"><table class="aix-table"><thead><tr>${b.head.map((c, k) => cell('th', c, k)).join('')}</tr></thead>` +
        `<tbody>${b.rows.map((r) => `<tr>${r.map((c, k) => cell('td', c, k)).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    case 'heading': {                                                    // (a ### heading; ## opens a section, see below)
      const info = headingInfo(b.text);
      const step = /^step\s*\d+|^\d+\s*[:.)]/i.test(info.label.replace(/\u0000M\d+\u0000/g, ''));
      return `<h5 class="aix-h3${step ? ' aix-h3--step' : ''}">${inline(info.label, ctx)}</h5>`;
    }
    default: return '';
  }
}

function blocksHtml(blocks, ctx) { return blocks.map((b) => blockHtml(b, ctx)).join(''); }

function documentHtml(blocks, ctx) {
  let html = '', body = '', open = null;
  const close = () => { if (open) html += `<section class="aix-sec aix-sec--${open.kind}">${open.head}<div class="aix-body">${body}</div></section>`; else if (body) html += `<div class="aix-pre">${body}</div>`; body = ''; open = null; };
  for (const b of blocks) {
    if (b.t === 'heading' && b.level <= 2) {
      close();
      const info = headingInfo(b.text);
      open = { kind: info.kind, head: `<h4 class="aix-h">${info.icon ? `<i class="fas ${info.icon} aix-ico" aria-hidden="true"></i>` : ''}<span>${inline(info.label, ctx)}</span></h4>` };
    } else body += blockHtml(b, ctx);
  }
  close();
  return html;
}

/**
 * markdown -> { html, fallbacks }.  `fallbacks` = formulas left for MathJax (see typesetFallbacks).
 * `katex` defaults to the page's KaTeX; without it every formula goes to MathJax.
 */
export function renderExplanation(markdown, options = {}) {
  const katex = options.katex !== undefined ? options.katex : (typeof window !== 'undefined' ? window.katex : undefined);
  const { text, items } = splitMath(String(markdown || '').replace(/\r\n?/g, '\n'));
  const ctx = { items, katex, fallbacks: 0 };
  const html = `<div class="aix">${documentHtml(parseBlocks(text.split('\n'), ctx), ctx)}</div>`;
  return { html, fallbacks: ctx.fallbacks };
}

/**
 * An inline formula never wraps, so on a narrow screen one that is wider than its line would be cut off by the card.
 * Call once the explanation is visible: only such a formula (rare) gets its own line with its own sideways scroll.
 */
export function fitWide(root) {
  root.querySelectorAll('.katex').forEach((k) => {
    if (k.closest('.katex-display')) return;
    const host = k.closest('p, li, h4, h5, .aix-callout');
    if (host && k.offsetWidth > host.clientWidth + 1) k.classList.add('aix-wide');
  });
}

/** Let MathJax finish any formula KaTeX could not (rare). Resolves when done; the formulas stay hidden until then. */
export function typesetFallbacks(root) {
  const els = Array.from((root || document).querySelectorAll('.aix-mj:not([data-mj])'));
  if (!els.length) return Promise.resolve(0);
  els.forEach((el) => { el.dataset.mj = '1'; });
  const done = () => els.forEach((el) => {
    if (el.querySelector('mjx-merror')) {              // neither engine can draw it (broken TeX from the model): a muted code chip, not an error message
      const code = document.createElement('code');
      code.className = 'aix-badmath'; code.title = 'This formula could not be displayed'; code.textContent = el.dataset.src || '';
      el.textContent = ''; el.appendChild(code);
    }
    el.classList.add('aix-mj-ready');
  });
  const run = typeof window !== 'undefined' && typeof window.mathJaxTypeset === 'function' ? window.mathJaxTypeset(els) : null;
  return Promise.resolve(run).then(done, done).then(() => els.length);
}

if (typeof window !== 'undefined') window.AiExplain = { render: renderExplanation, typesetFallbacks, fitWide, normalizeTex, MATH_RE };
