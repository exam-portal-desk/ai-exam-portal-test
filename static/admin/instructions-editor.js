/* Exam Instructions editor (Add Exam, Edit Exam, Bulk Update).
 *
 *   InstructionsEditor.wire(textarea)   toolbar + live preview for one textarea (safe to call twice)
 *
 * The stored value stays the plain-text mini-markup that app/utils/instructions_formatter.py renders (# headings, - / 1. lists
 * with two-space nesting, **bold**, *italic*, __underline__, [text](url), [center]/[right] lines, "---", IMPORTANT: callouts),
 * so nothing is converted on save and every earlier instruction still opens as it was. Edits go through execCommand
 * ('insertText') so the browser's own Undo/Redo keeps working and the caret/selection is placed deliberately afterwards.
 * Needs: adminToast, FloatingPanel (select-enhance.js). Template text: window.INSTR_GENERAL_TEMPLATE.
 */
(function (global) {
  'use strict';

  const LIST_RE = /^(\s*)([-*]|\d+[.)])\s+(.*)$/;
  const HEAD_RE = /^(#{1,3})\s+(.*)$/;
  const ALIGN_RE = /^\[(left|center|right)\]\s*/i;
  const CALLOUT_RE = /^(important|warning|note)\s*:/i;
  const RULE_RE = /^(-{3,}|\*{3,}|_{3,})$/;
  const EMPTY_PREVIEW = '<span class="instr-preview-empty">Nothing to preview yet.</span>';

  const toast = (m, t, s) => { if (global.adminToast) global.adminToast(m, t, s); };
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  /* ── text-editing primitives ── */
  function replace(ta, start, end, text, selA, selB) {
    ta.focus();
    ta.setSelectionRange(start, end);
    let ok = false;
    try { ok = document.execCommand('insertText', false, text); } catch (e) { ok = false; }
    if (!ok) { ta.setRangeText(text, start, end, 'end'); ta.dispatchEvent(new Event('input', { bubbles: true })); }
    const a = selA == null ? start + text.length : selA;
    ta.setSelectionRange(a, selB == null ? a : selB);
  }

  function lineRange(ta) {
    const v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
    const start = v.lastIndexOf('\n', s - 1) + 1;
    let end = v.indexOf('\n', e > s && v[e - 1] === '\n' ? e - 1 : e);
    if (end === -1) end = v.length;
    return { start, end };
  }

  /* Rewrite the selected lines with fn(lines) -> lines; the rewritten block stays selected. */
  function mapLines(ta, fn) {
    const { start, end } = lineRange(ta);
    const lines = ta.value.slice(start, end).split('\n');
    const out = fn(lines);
    if (!out) return false;
    const text = out.join('\n');
    replace(ta, start, end, text, start, start + text.length);
    return true;
  }

  const isBlank = ln => !ln.trim();
  const stripAlign = s => s.replace(ALIGN_RE, '');
  const stripHeading = s => { const m = s.trim().match(HEAD_RE); return m ? m[2] : s.trim(); };
  // lines saved by older editor versions may have the marker buried inside emphasis: "**- item**" -> "- **item**"
  const unburyPrefix = s => s.replace(/^(\s*)(\*\*|__)\s*([-]|\d+[.)]|#{1,3})\s+(.*\S.*?)\2\s*$/, '$1$3 $2$4$2');
  const stripListMark = s => { let t = s, m; while ((m = t.match(LIST_RE))) t = m[3]; return t; };

  /* ── commands ── */
  // A line is  [structure][body]  — structure = indent + list marker | heading hashes, then an optional [align] tag or an
  // IMPORTANT:/WARNING:/NOTE: label. Emphasis (**, *, __) is only ever applied to the body, so it can never swallow a "-",
  // "1." or "##" and turn the line into plain text.
  const PREFIX_RE = /^(?:\s*(?:[-*]|\d+[.)])\s+|#{1,3}\s+)*(?:\[(?:left|center|right)\]\s*|(?:important|warning|note)\s*:\s*)?/i;
  const prefixLen = ln => ln.match(PREFIX_RE)[0].length;

  const WORD = /[\p{L}\p{N}_]/u;
  const EMPH = /[*_]/;
  const countOf = (str, ch) => str.split(ch).length - 1;

  /* Emphasis is a small state machine, not string wrapping. Around the text sit "runs" of marker characters; their state is
       bold      = at least two stars on both sides         (**)
       italic    = an odd number of stars on both sides     (*, or *** for both)
       underline = at least two underscores on both sides   (__)
     Toggling flips one flag and the run is rewritten in ONE canonical order (stars outside, underscores inside), so
     bold -> italic -> underline -> italic ... always unwinds cleanly and never leaves "****a****" or "***__***".
     Returns the new line plus the inner (marker-free) range and the outer range (markers included). */
  function toggleInline(line, a, b, marker, placeholder, floor) {
    let seg = line.slice(a, b);
    const lead = seg.length - seg.trimStart().length;
    seg = seg.trim();
    a += lead; b = a + seg.length;
    if (!seg) {
      const out = line.slice(0, a) + marker + placeholder + marker + line.slice(a);
      return { line: out, a: a + marker.length, b: a + marker.length + placeholder.length, oa: a, ob: a + 2 * marker.length + placeholder.length };
    }
    // markers that are part of the selection itself belong to the runs, provided the text between them is plain
    const lm = seg.match(/^[*_]+/), tm = seg.match(/[*_]+$/);
    if (lm && tm && lm[0].length + tm[0].length < seg.length) {
      const inner = seg.slice(lm[0].length, seg.length - tm[0].length);
      if (!/[*]/.test(inner) && !inner.includes('__')) { a += lm[0].length; b -= tm[0].length; seg = inner; }
    }
    let ls = a; while (ls > floor && EMPH.test(line[ls - 1])) ls--;
    let re = b; while (re < line.length && EMPH.test(line[re])) re++;
    const L = line.slice(ls, a), Rr = line.slice(b, re);
    const stars = Math.min(countOf(L, '*'), countOf(Rr, '*'));
    const unders = Math.min(countOf(L, '_'), countOf(Rr, '_'));
    const sym = countOf(L, '*') === countOf(Rr, '*') && countOf(L, '_') === countOf(Rr, '_') && L.replace(/[*_]/g, '') === '';
    let bold = false, italic = false, under = false;
    if (sym) { bold = stars >= 2; italic = stars % 2 === 1; under = unders >= 2; }
    const keepL = sym ? '' : L, keepR = sym ? '' : Rr;                   // an unbalanced run is left alone and we simply nest inside it
    let turnedOn;
    if (marker === '**') { bold = !bold; turnedOn = bold; } else if (marker === '*') { italic = !italic; turnedOn = italic; } else { under = !under; turnedOn = under; }
    if (turnedOn) {                       // the selection may already hold some of this format inside it: merge it into the one new span
      if (marker === '**') seg = seg.replace(/\*\*/g, '');
      else if (marker === '__') seg = seg.replace(/__/g, '');
      else seg = seg.replace(/(^|[^*])\*(?!\*)/g, '$1');
    }
    const open = '*'.repeat((bold ? 2 : 0) + (italic ? 1 : 0)) + (under ? '__' : '');
    const close = (under ? '__' : '') + '*'.repeat((bold ? 2 : 0) + (italic ? 1 : 0));
    const outL = keepL + open, outR = close + keepR;
    const out = line.slice(0, ls) + outL + seg + outR + line.slice(re);
    return { line: out, a: ls + outL.length, b: ls + outL.length + seg.length, oa: ls + keepL.length, ob: ls + outL.length + seg.length + close.length };
  }

  function wrapInline(ta, marker, placeholder) {
    const v = ta.value;
    let s = ta.selectionStart, e = ta.selectionEnd;
    const { start, end } = lineRange(ta);
    const lines = v.slice(start, end).split('\n');
    // offsets of every line inside the whole text
    let off = start;
    const meta = lines.map(ln => { const o = off; off += ln.length + 1; return { o, pre: prefixLen(ln) }; });

    if (s === e) {                                                       // caret only -> the word under it, else a placeholder
      const m0 = meta[0], rel = s - m0.o, ln = lines[0];
      let a = Math.max(rel, m0.pre), b = a;
      if (rel >= m0.pre) {
        a = rel; b = rel;
        while (a > m0.pre && WORD.test(ln[a - 1])) a--;
        while (b < ln.length && WORD.test(ln[b])) b++;
        if (a === b && (EMPH.test(ln[a - 1] || '') || EMPH.test(ln[a] || ''))) {     // caret sits on/between markers: use the word they wrap
          let r = a; while (r < ln.length && EMPH.test(ln[r])) r++;
          let l = a; while (l > m0.pre && EMPH.test(ln[l - 1])) l--;
          if (r < ln.length && WORD.test(ln[r])) { a = b = r; while (b < ln.length && WORD.test(ln[b])) b++; }
          else if (l > m0.pre && WORD.test(ln[l - 1])) { b = l; a = l; while (a > m0.pre && WORD.test(ln[a - 1])) a--; }
        }
      }
      const r = toggleInline(ln, a, b, marker, placeholder, m0.pre);
      const text = r.line;
      replace(ta, start, end, [text].concat(lines.slice(1)).join('\n'), start + r.a, start + r.b);
      return;
    }

    const out = lines.map((ln, i) => {
      const { o, pre } = meta[i];
      const a = Math.max(s - o, pre, 0), b = Math.min(e - o, ln.length);
      if (!ln.trim() || b <= a || !ln.slice(a, b).trim()) return ln;
      const r = toggleInline(ln, a, b, marker, placeholder, pre);
      meta[i].r = r;
      return r.line;
    });
    // Selection afterwards: one line -> just its text (so the next click toggles that exact text); several lines -> from the first
    // to the last touched line INCLUDING their markers, so the next click sees whole emphasised lines and toggles them off cleanly.
    const touched = meta.filter(m => m.r).length;
    let acc = start, from = null, to = null;
    out.forEach((ln, i) => {
      const r = meta[i].r;
      if (r) { if (from === null) from = acc + (touched === 1 ? r.a : r.oa); to = acc + (touched === 1 ? r.b : r.ob); }
      acc += ln.length + 1;
    });
    replace(ta, start, end, out.join('\n'), from === null ? start : from, to === null ? start : to);
  }

  function headingCmd(ta, level) {
    const hashes = '#'.repeat(level) + ' ';
    mapLines(ta, lines => {
      const live = lines.filter(l => !isBlank(l));
      const same = live.length && live.every(l => (l.trim().match(HEAD_RE) || [])[1] === '#'.repeat(level));
      return lines.map(l => {
        if (isBlank(l)) return l;
        const body = stripListMark(stripHeading(unburyPrefix(l))).trim();
        return same ? body : hashes + body;
      });
    });
  }

  function listCmd(ta, kind) {
    mapLines(ta, lines => {
      const live = lines.filter(l => !isBlank(l));
      const isKind = l => { const m = l.match(LIST_RE); return m && (kind === 'bullet' ? /^[-*]$/.test(m[2]) : /^\d/.test(m[2])); };
      const allKind = live.length && live.every(isKind);
      const counters = {};
      return lines.map(l => {
        if (isBlank(l)) return l;
        l = unburyPrefix(l);
        const m = l.match(LIST_RE);
        const indent = m ? m[1] : '';
        const body = stripAlign(stripListMark(m ? m[3] : stripHeading(l))).trim();
        if (allKind) return body;
        if (kind === 'bullet') return indent + '- ' + body;
        Object.keys(counters).forEach(k => { if (+k > indent.length) delete counters[k]; });
        counters[indent.length] = (counters[indent.length] || 0) + 1;
        return indent + counters[indent.length] + '. ' + body;
      });
    });
  }

  function indentCmd(ta, dir) {
    let touched = false;
    mapLines(ta, lines => lines.map(l => {
      if (isBlank(l) || !LIST_RE.test(l)) return l;
      if (dir > 0) { touched = true; return '  ' + l; }
      const next = l.replace(/^(\t| {1,2})/, '');
      if (next !== l) touched = true;
      return next;
    }));
    if (!touched) toast(dir > 0 ? 'Indent applies to list items.' : 'Nothing to outdent.', 'warning',
      dir > 0 ? 'Make the line a bullet or numbered item first.' : undefined);
  }

  function alignCmd(ta, dir) {
    let skipped = 0, done = 0;
    mapLines(ta, lines => lines.map(l => {
      if (isBlank(l)) return l;
      const t = l.trim();
      if (LIST_RE.test(l) || RULE_RE.test(t) || CALLOUT_RE.test(t)) { skipped++; return l; }
      done++;
      const h = t.match(/^(#{1,3}\s+)(.*)$/);
      const tag = dir === 'left' ? '' : '[' + dir + '] ';
      return h ? h[1] + tag + stripAlign(h[2]) : tag + stripAlign(t);
    }));
    if (skipped && !done) toast('Alignment applies to headings and paragraphs.', 'warning', 'List items and notes keep their normal alignment.');
  }

  function insertBlock(ta, text, selectText) {
    const v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
    const before = v.slice(0, s), after = v.slice(e);
    const lead = !before ? '' : before.endsWith('\n\n') ? '' : before.endsWith('\n') ? '\n' : '\n\n';
    const tail = !after ? '' : after.startsWith('\n\n') ? '' : after.startsWith('\n') ? '\n' : '\n\n';
    const full = lead + text + tail;
    const at = s + lead.length;
    replace(ta, s, e, full, selectText ? at + text.indexOf(selectText) : at + text.length,
      selectText ? at + text.indexOf(selectText) + selectText.length : null);
  }

  /* ── dialogs (FloatingPanel, the project's shared popup) ── */
  function openDialog(anchor, title, bodyHtml, buttons, onReady) {
    const panel = document.createElement('div');
    panel.className = 'instr-dialog';
    panel.innerHTML =
      '<div class="instr-dlg-head"><span>' + esc(title) + '</span>' +
      '<button type="button" class="instr-dlg-x" aria-label="Close"><i class="fas fa-times"></i></button></div>' +
      '<div class="instr-dlg-body">' + bodyHtml + '</div>' +
      '<div class="instr-dlg-foot"></div>';
    const foot = panel.querySelector('.instr-dlg-foot');
    buttons.forEach(b => {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'btn btn-sm ' + (b.cls || 'btn-secondary');
      el.innerHTML = (b.icon ? '<i class="fas ' + b.icon + ' me-1"></i>' : '') + esc(b.label);
      el.addEventListener('click', () => b.onClick(panel));
      foot.appendChild(el);
    });
    panel.querySelector('.instr-dlg-x').addEventListener('click', () => global.FloatingPanel.close());
    global.FloatingPanel.open(anchor, panel, {});
    if (onReady) onReady(panel);
    return panel;
  }

  // Escape closes only the popup, not the Add/Edit modal underneath it.
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && global.FloatingPanel && global.FloatingPanel.current() &&
        global.FloatingPanel.current().classList.contains('instr-dialog')) {
      e.stopPropagation(); global.FloatingPanel.close();
    }
  }, true);

  function linkCmd(ta, anchor) {
    const s = ta.selectionStart, e = ta.selectionEnd, picked = ta.value.slice(s, e).replace(/\n/g, ' ').trim();
    openDialog(anchor, 'Insert link',
      '<label class="instr-dlg-label" for="instrLinkText">Link text</label>' +
      '<input id="instrLinkText" class="form-control" type="text" value="' + esc(picked) + '" placeholder="e.g. Exam rules">' +
      '<label class="instr-dlg-label" for="instrLinkUrl">Web address</label>' +
      '<input id="instrLinkUrl" class="form-control" type="url" inputmode="url" placeholder="https://example.com">' +
      '<div class="instr-dlg-err" hidden></div>',
      [
        { label: 'Cancel', onClick: () => global.FloatingPanel.close() },
        { label: 'Insert link', icon: 'fa-link', cls: 'btn-primary', onClick: panel => {
          const text = panel.querySelector('#instrLinkText').value.trim().replace(/[\[\]]/g, '');
          const url = panel.querySelector('#instrLinkUrl').value.trim();
          const err = panel.querySelector('.instr-dlg-err');
          if (!/^(https?:\/\/|mailto:)[^\s()<>"']+$/i.test(url)) {
            err.textContent = 'Enter a full address starting with https://, http:// or mailto:'; err.hidden = false; return;
          }
          global.FloatingPanel.close();
          const md = '[' + (text || url) + '](' + url + ')';
          replace(ta, s, e, md);
        } },
      ],
      panel => panel.querySelector(picked ? '#instrLinkUrl' : '#instrLinkText').focus());
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise((resolve, reject) => {       // plain-http fallback
      const t = document.createElement('textarea');
      t.value = text; t.setAttribute('readonly', ''); t.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(t); t.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      t.remove();
      ok ? resolve() : reject(new Error('copy failed'));
    });
  }

  function templateCmd(ta, anchor) {
    const tpl = global.INSTR_GENERAL_TEMPLATE || '';
    if (!tpl) { toast('The template is not available.', 'danger'); return; }
    openDialog(anchor, 'General exam instructions',
      '<p class="instr-dlg-text">A ready-made set of instructions covering timing, answering, submission, technical issues and conduct.</p>' +
      '<p class="instr-dlg-text">Lines marked <strong>[Edit: …]</strong> are exam-specific (marking, attempts, support contact). Fill them in or delete them.</p>',
      [
        { label: 'Cancel', onClick: () => global.FloatingPanel.close() },
        { label: 'Copy', icon: 'fa-copy', onClick: () => {
          global.FloatingPanel.close();
          copyText(tpl).then(() => toast('Template copied to clipboard.', 'success'),
                             () => toast('Could not copy the template.', 'danger', 'Use Insert instead, or copy from the editor.'));
        } },
        { label: 'Insert', icon: 'fa-file-import', cls: 'btn-primary', onClick: () => {
          global.FloatingPanel.close();
          if (!ta.value.trim()) { replace(ta, 0, ta.value.length, tpl.trim(), 0, 0); ta.scrollTop = 0; toast('Template inserted.', 'success'); return; }
          confirmOverwrite(ta, anchor, tpl);
        } },
      ]);
  }

  // Never replaces existing instructions without asking.
  function confirmOverwrite(ta, anchor, tpl) {
    openDialog(anchor, 'Existing instructions found',
      '<p class="instr-dlg-text">This exam already has instructions. How should the template be added?</p>' +
      '<p class="instr-dlg-text instr-dlg-hint"><i class="fas fa-rotate-left me-1"></i>Replace can be undone with Ctrl+Z while this window is open.</p>',
      [
        { label: 'Cancel', onClick: () => { global.FloatingPanel.close(); ta.focus(); } },
        { label: 'Add below', icon: 'fa-plus', onClick: () => {
          global.FloatingPanel.close();
          const v = ta.value.replace(/\s+$/, '');
          replace(ta, v.length, ta.value.length, '\n\n' + tpl.trim());
          toast('Template added below your instructions.', 'success');
        } },
        { label: 'Replace', icon: 'fa-arrows-rotate', cls: 'btn-danger', onClick: () => {
          global.FloatingPanel.close();
          replace(ta, 0, ta.value.length, tpl.trim(), 0, 0); ta.scrollTop = 0;
          toast('Instructions replaced with the template.', 'success');
        } },
      ]);
  }

  /* ── Auto-Fill Exam Details ──
     Fills the template's "[Edit: …]" placeholders from the exam form that is already on screen (Add Exam, or the Edit Exam
     modal): nothing is fetched or saved, and a placeholder is only replaced when its value is actually known. Matching is by the
     bold label in front of the placeholder ("- **Total duration:** [Edit: …]"), so only those lines are ever touched. Once a
     line is filled it no longer has a placeholder, which is what makes a second click harmless. */
  const FILL_LINE = /^(\s*(?:[-*]|\d+[.)])\s+\*\*([^*]+?):\*\*\s*)\[Edit:[^\]]*\]\s*$/;
  const PLACEHOLDER = /\[Edit:[^\]]*\]/;
  const plural = (n, w) => n + ' ' + w + (Number(n) === 1 ? '' : 's');

  // raw: the form's own values (strings). attempts: null when the form does not carry that value.
  function formatValues(raw) {
    const pos = v => { const n = parseFloat(v); return isFinite(n) && n > 0 ? n : null; };
    const nonneg = v => { if (v === '' || v == null) return null; const n = parseFloat(v); return isFinite(n) && n >= 0 ? n : null; };
    const out = {};
    const name = (raw.name || '').trim();
    if (name) out['Examination name'] = name;
    const d = (raw.date || '').trim(), t = (raw.start_time || '').trim();
    if (d && t) out['Date and start time'] = d + ' at ' + t;
    const dur = pos(raw.duration);
    if (dur) out['Total duration'] = plural(Math.round(dur), 'minute');
    const q = pos(raw.total_questions);
    if (q) out['Number of questions'] = String(Math.round(q));
    if (raw.attempts != null) {
      const a = String(raw.attempts).trim();
      out['Attempts allowed'] = (a === '' || a === '0') ? 'Unlimited' : (isFinite(parseInt(a, 10)) ? String(parseInt(a, 10)) : undefined);
      if (out['Attempts allowed'] === undefined) delete out['Attempts allowed'];
    }
    const p = nonneg(raw.positive_marks);
    if (p !== null && p > 0) out['Marks for a correct answer'] = plural(p, 'mark');
    const n = nonneg(raw.negative_marks);
    if (n !== null) out['Negative marking'] = n === 0 ? 'None' : plural(n, 'mark') + ' deducted for each wrong answer';
    const pc = pos(raw.passing_percentage);
    if (pc) out['Passing criteria'] = pc + '%';
    const mode = (raw.result_mode || '').trim();
    if (mode === 'instant') out['Results'] = 'Instant — visible immediately after submission';
    else if (mode === 'manual') out['Results'] = 'Released by the admin after review';
    else if (mode === 'delayed') { const dl = pos(raw.result_delay); if (dl) out['Results'] = 'Delayed — visible ' + Math.round(dl) + ' min after submission'; }
    return out;
  }

  // -> { text, filled: [labels], left: [labels still showing a placeholder] }
  function fillPlaceholders(text, values) {
    const filled = [], left = [];
    const lines = text.split('\n').map(ln => {
      const m = ln.match(FILL_LINE);
      if (!m) return ln;
      if (Object.prototype.hasOwnProperty.call(values, m[2])) { filled.push(m[2]); return m[1] + values[m[2]]; }
      left.push(m[2]);
      return ln;
    });
    return { text: lines.join('\n'), filled, left };
  }

  function gatherRaw(form) {
    const val = n => { const el = form && form.querySelector('[name="' + n + '"]'); return el ? String(el.value) : ''; };
    const raw = {};
    ['name', 'date', 'start_time', 'duration', 'total_questions', 'positive_marks', 'negative_marks', 'passing_percentage', 'result_mode', 'result_delay']
      .forEach(n => { raw[n] = val(n); });
    // The attempt limit is not a field of the Add/Edit form; the Edit modal carries the exam's saved value (already on the page).
    raw.attempts = form && form.hasAttribute('data-instr-attempts') ? form.getAttribute('data-instr-attempts') : null;
    return raw;
  }

  const TEMPLATE_MARK = /^#\s+General Examination Instructions|\*\*Total duration:\*\*/m;

  function autofillCmd(ta, anchor) {
    const tpl = (global.INSTR_GENERAL_TEMPLATE || '').trim();
    const values = formatValues(gatherRaw(ta.closest('form')));
    const pending = list => list.length ? 'Still to complete: ' + list.map(esc).join(', ') : undefined;
    if (!Object.keys(values).length) { toast('No exam details to fill in yet.', 'warning', 'Enter the exam name, date, duration and marks first.'); return; }
    const current = ta.value;
    if (!current.trim()) {                                                   // empty editor: the template, already filled
      if (!tpl) { toast('The template is not available.', 'danger'); return; }
      const r = fillPlaceholders(tpl, values);
      replace(ta, 0, current.length, r.text, 0, 0); ta.scrollTop = 0;
      toast('Template inserted with ' + plural(r.filled.length, 'exam detail') + ' filled in.', 'success', pending(otherPlaceholders(r.text)));
      return;
    }
    if (PLACEHOLDER.test(current)) {                                         // only the placeholders change; every other character stays
      const r = fillPlaceholders(current, values);
      if (!r.filled.length) { toast('Nothing more to fill in.', 'warning', 'The remaining placeholders need details this form does not have.'); return; }
      const top = ta.scrollTop;
      replace(ta, 0, current.length, r.text, 0, 0); ta.scrollTop = top;
      toast('Filled in ' + plural(r.filled.length, 'exam detail') + '.', 'success', pending(r.left));
      return;
    }
    if (TEMPLATE_MARK.test(current)) { toast('Exam details are already filled in.', 'success', 'Edit the text directly to change them.'); return; }
    if (!tpl) { toast('The template is not available.', 'danger'); return; }
    confirmOverwrite(ta, anchor, fillPlaceholders(tpl, values).text);        // custom text without the template: ask first
  }

  // labels of placeholders that no mapping can fill (shown to the admin as things left to complete)
  function otherPlaceholders(text) {
    const out = [];
    text.split('\n').forEach(ln => { const m = ln.match(FILL_LINE); if (m && out.indexOf(m[2]) < 0) out.push(m[2]); });
    return out;
  }

  /* ── preview (rendered by the same server code students see) ── */
  const timers = new WeakMap();
  function renderPreview(ta, el) {
    clearTimeout(timers.get(ta));
    if (!ta.value.trim()) { el.innerHTML = EMPTY_PREVIEW; return; }
    timers.set(ta, setTimeout(async () => {
      const sent = ta.value;
      try {
        const r = await fetch('/api/v01/admin/exams/preview-instructions', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instructions: sent }),
        });
        const data = await r.json();
        if (ta.value !== sent) return;                 // a newer keystroke owns the preview
        el.innerHTML = data.html || EMPTY_PREVIEW;
      } catch (e) { /* preview is a convenience — a failed fetch leaves the last preview */ }
    }, 300));
  }

  /* ── wiring ── */
  function run(ta, cmd, anchor) {
    switch (cmd) {
      case 'h1': return headingCmd(ta, 1);
      case 'h2': return headingCmd(ta, 2);
      case 'h3': return headingCmd(ta, 3);
      case 'bold': return wrapInline(ta, '**', 'bold text');
      case 'italic': return wrapInline(ta, '*', 'italic text');
      case 'underline': return wrapInline(ta, '__', 'underlined text');
      case 'bullet': return listCmd(ta, 'bullet');
      case 'number': return listCmd(ta, 'number');
      case 'indent': return indentCmd(ta, 1);
      case 'outdent': return indentCmd(ta, -1);
      case 'left': case 'center': case 'right': return alignCmd(ta, cmd);
      case 'link': return linkCmd(ta, anchor);
      case 'rule': return insertBlock(ta, '---');
      case 'warning': return insertBlock(ta, 'IMPORTANT: Your message here', 'Your message here');
      case 'template': return templateCmd(ta, anchor);
      case 'autofill': return autofillCmd(ta, anchor);
    }
  }

  function onKeydown(ta, e) {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && !e.altKey) {
      const k = e.key.toLowerCase();
      const cmd = { b: 'bold', i: 'italic', u: 'underline' }[k];
      if (cmd) { e.preventDefault(); run(ta, cmd, ta); return; }
    }
    const { start, end } = lineRange(ta);
    const line = ta.value.slice(start, end);
    const m = line.match(LIST_RE);
    if (e.key === 'Tab' && !e.altKey && !e.ctrlKey && !e.metaKey && m) {       // Tab only inside a list, so keyboard users can still leave the field
      e.preventDefault(); indentCmd(ta, e.shiftKey ? -1 : 1); return;
    }
    if (e.key === 'Enter' && !e.shiftKey && !mod && m && ta.selectionStart === ta.selectionEnd && ta.selectionStart === end) {
      e.preventDefault();
      if (!m[3].trim()) { replace(ta, start, end, '', start, start); return; }  // Enter on an empty item ends the list
      const mark = /^\d/.test(m[2]) ? (parseInt(m[2], 10) + 1) + m[2].replace(/^\d+/, '') : m[2];
      replace(ta, end, end, '\n' + m[1] + mark + ' ');
    }
  }

  function wire(ta) {
    if (!ta || ta.dataset.instrWired) return;
    const field = ta.closest('.col-12');
    const preview = field && field.querySelector('.instr-preview');
    if (!field || !preview) return;
    ta.dataset.instrWired = '1';
    field.querySelectorAll('[data-instr-cmd]').forEach(btn => {
      btn.addEventListener('mousedown', e => e.preventDefault());       // keep the caret/selection in the textarea
      btn.addEventListener('click', () => run(ta, btn.dataset.instrCmd, btn));
    });
    ta.addEventListener('input', () => renderPreview(ta, preview));
    ta.addEventListener('keydown', e => onKeydown(ta, e));
    renderPreview(ta, preview);
    // A cancelled edit / a form reset must not leave a stale draft or preview behind.
    const form = ta.closest('form');
    if (form) form.addEventListener('reset', () => setTimeout(() => renderPreview(ta, preview), 0));
    const modal = ta.closest('.modal');
    if (modal && modal.id && modal.id.indexOf('editExamModal') === 0) {
      modal.addEventListener('hidden.bs.modal', () => {
        if (ta.value !== ta.defaultValue) { ta.value = ta.defaultValue; renderPreview(ta, preview); }
      });
    }
  }

  global.InstructionsEditor = { wire, render: renderPreview, run, formatValues, fillPlaceholders };
})(window);
