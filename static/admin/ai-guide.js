/*
 * AI Configuration guide popup (Admin).
 *
 * Shows the separate guide document (config/AI_MODELS.md) fetched from the server. Nothing here
 * knows the guide's content: it renders the markdown it is given, builds a contents list from the
 * "##" headings, and keeps that list in step with what is visible.
 *
 *  - Two independent scroll panes inside a fixed-height popup (contents | guide): the page behind
 *    the popup never scrolls, and every scroll calculation is relative to the guide pane, not window.
 *  - Active contents item = the section at the top of the guide pane (scroll position based, so it is
 *    correct after clicks, manual scrolling, long sections, deep links and reloads).
 *  - Every code block has a Copy button that copies the RAW source text of that block, nothing else.
 *  - Deep links: #guide and #guide/<section-id> (reload / back / forward aware).
 *
 * All text is inserted with textContent / text nodes, so document text can never inject markup.
 */
(function () {
  'use strict';

  var SCROLL_OFFSET = 14;       // where a section heading lands below the top edge of the guide pane
  var ACTIVE_THRESHOLD = 96;    // a section is "current" once its heading is within this many px of the top
  var LOCK_MS = 140;            // after a click-driven jump ends, ignore scroll-tracking a moment so the clicked item stays lit

  var cfg = null, modalEl = null, modal = null;
  var S = {
    active: false, shown: false, built: false, pendingId: null, pushed: false, ignorePop: false,
    sections: [], byId: {}, scroll: null, navList: null, shell: null, mobileLabel: null, spacer: null,
    input: null, countEl: null, emptyEl: null, activeId: null, lock: false, lockTimer: 0, ticking: false, loadToken: 0, anim: 0
  };

  // ── tiny DOM helpers ───────────────────────────────────────────────────────────────────────────
  function h(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.slice(0, 2) === 'on') node.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? '' : v);
    });
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function slugify(text) {
    return text.toLowerCase().replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '-');
  }
  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  // ── inline markdown: `code`, **bold**, [text](#section) ──────────────────────────────────────────
  function inline(text, parent, ids) {
    text.split(/(`[^`]+`)/).forEach(function (part) {
      if (/^`[^`]+`$/.test(part)) { parent.appendChild(h('code', { text: part.slice(1, -1) })); return; }
      part.split(/(\*\*[^*]+\*\*|\[[^\]]+\]\(#[A-Za-z0-9-]+\))/).forEach(function (seg) {
        var m;
        if (/^\*\*[^*]+\*\*$/.test(seg)) {
          var t = seg.slice(2, -2);
          parent.appendChild(h('strong', { 'class': /^THIS REQUIRES CODE CHANGES:?$/.test(t) ? 'ai-md-code-change' : null, text: t }));
        } else if ((m = /^\[([^\]]+)\]\(#([A-Za-z0-9-]+)\)$/.exec(seg))) {
          if (ids && ids[m[2]]) parent.appendChild(h('a', { 'class': 'ai-md-link', href: '#guide/' + m[2], 'data-goto': m[2], text: m[1] }));
          else parent.appendChild(document.createTextNode(m[1]));
        } else if (seg) {
          parent.appendChild(document.createTextNode(seg));
        }
      });
    });
    return parent;
  }

  // ── JSON highlighting (display only; Copy always uses the raw text) ────────────────────────────────
  function highlightJson(code) {
    var frag = document.createDocumentFragment();
    var re = /("(?:\\.|[^"\\])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b|([{}\[\],:])/g;
    var last = 0, m;
    function span(cls, txt) { return h('span', { 'class': cls, text: txt }); }
    while ((m = re.exec(code))) {
      if (m.index > last) frag.appendChild(document.createTextNode(code.slice(last, m.index)));
      if (m[1] !== undefined) {
        if (m[2]) { frag.appendChild(span('tok-key', m[1])); frag.appendChild(span('tok-p', m[2])); }
        else frag.appendChild(span('tok-str', m[1]));
      } else if (m[3] !== undefined) frag.appendChild(span('tok-num', m[3]));
      else if (m[4] !== undefined) frag.appendChild(span('tok-lit', m[4]));
      else frag.appendChild(span('tok-p', m[5]));
      last = re.lastIndex;
    }
    if (last < code.length) frag.appendChild(document.createTextNode(code.slice(last)));
    return frag;
  }

  // ── copy: RAW source text of the block, with a fallback that works inside the modal ───────────────
  function legacyCopy(text) {
    var ta = h('textarea', { readonly: true, 'aria-hidden': 'true', tabindex: '-1' });
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none;';
    // Must live INSIDE the modal: Bootstrap's focus trap pulls focus back from anything outside it,
    // which makes select()+execCommand('copy') silently fail on a body-level textarea.
    (modalEl || document.body).appendChild(ta);
    var ok = false;
    try { ta.focus({ preventScroll: true }); ta.select(); ta.setSelectionRange(0, text.length); ok = document.execCommand('copy'); } catch (e) { ok = false; }
    ta.parentNode.removeChild(ta);
    return ok;
  }
  function selectNode(node) {
    try { var r = document.createRange(); r.selectNodeContents(node); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); } catch (e) { /* nothing more to try */ }
  }
  function flash(btn, state, label) {
    btn.classList.remove('copied', 'manual');
    if (state) btn.classList.add(state);
    btn.querySelector('.txt').textContent = label;
    clearTimeout(btn._t);
    if (state) btn._t = setTimeout(function () { flash(btn, null, 'Copy'); }, state === 'copied' ? 1800 : 3500);
  }
  function copyRaw(text, btn, pre) {
    function ok() { flash(btn, 'copied', 'Copied ✓'); }
    function manual() { selectNode(pre); flash(btn, 'manual', 'Press Ctrl+C'); }
    function fallback() { (legacyCopy(text) ? ok : manual)(); }
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(ok, fallback);
    } else {
      fallback();
    }
  }
  function codeBlock(lang, label, code) {
    var pre = h('pre', { 'class': 'ai-code-pre', tabindex: '0' });
    var codeEl = h('code');
    if (lang === 'json') codeEl.appendChild(highlightJson(code)); else codeEl.textContent = code;
    pre.appendChild(codeEl);
    var btn = h('button', { 'class': 'ai-code-copy', type: 'button', 'aria-label': 'Copy ' + (label || lang || 'code') + ' to the clipboard' },
      [h('span', { 'class': 'txt', text: 'Copy' })]);
    btn.addEventListener('click', function () { copyRaw(code, btn, pre); });
    btn._raw = code;
    return h('div', { 'class': 'ai-code', 'data-lang': lang || 'text' }, [
      h('div', { 'class': 'ai-code-bar' }, [
        h('span', { 'class': 'ai-code-lang', text: (lang || 'text').toUpperCase() }),
        h('span', { 'class': 'ai-code-label', text: label || '' }),
        btn
      ]),
      pre
    ]);
  }

  // ── markdown blocks ──────────────────────────────────────────────────────────────────────────────
  var BLOCK_START = /^(#{1,4}\s|```|>|\||\s*[-*]\s+|\s*\d+\.\s+|---+\s*$)/;
  function cells(row) { return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function (c) { return c.trim(); }); }

  function render(src) {
    var lines = src.replace(/\r\n?/g, '\n').split('\n');

    // pass 1: heading ids for "##" sections (deduplicated), so [links](#id) can be validated
    var ids = {}, order = [], fenced = false;
    lines.forEach(function (ln) {
      if (/^```/.test(ln)) { fenced = !fenced; return; }
      var m = !fenced && /^##\s+(.*)$/.exec(ln);
      if (m) {
        var base = slugify(m[1].trim()), id = base, n = 2;
        while (ids[id]) id = base + '-' + n++;
        ids[id] = m[1].trim(); order.push(id);
      }
    });

    var doc = h('div', { 'class': 'ai-md-doc' }), title = null, i = 0, h2n = 0, m;
    while (i < lines.length) {
      var line = lines[i];
      if (/^```/.test(line)) {                                                   // fenced code block
        var info = line.slice(3).trim(), lang = info.split(/\s+/)[0] || '', label = info.slice(lang.length).trim(), buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++;
        doc.appendChild(codeBlock(lang, label, buf.join('\n')));
        continue;
      }
      if (!line.trim()) { i++; continue; }
      if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {                                // headings
        var level = m[1].length, text = m[2].trim(); i++;
        if (level === 1) { if (!title) title = text; continue; }
        var hd;
        if (level === 2) {
          hd = inline(text, h('h2', { id: 'aig-' + order[h2n] }), ids); hd.setAttribute('data-section', order[h2n]); h2n++;
        } else if (level === 3 && /^Step\s+\d+\b/i.test(text)) {
          var sm = /^Step\s+(\d+)\.?\s*(.*)$/i.exec(text);
          hd = h('h3', { 'class': 'ai-md-step' }, [h('span', { 'class': 'ai-step-badge', text: sm[1] })]);
          inline(sm[2] || 'Step ' + sm[1], hd, ids);
        } else {
          hd = inline(text, h('h' + level, { 'class': /^Expected result/i.test(text) ? 'ai-md-expected' : (/^Checklist/i.test(text) ? 'ai-md-checklist-h' : null) }), ids);
        }
        doc.appendChild(hd);
        continue;
      }
      if (/^---+\s*$/.test(line)) { doc.appendChild(h('hr')); i++; continue; }
      if (/^>/.test(line)) {                                                     // callout
        var q = [];
        while (i < lines.length && /^>/.test(lines[i])) { q.push(lines[i].replace(/^>\s?/, '')); i++; }
        var kind = /^\*\*(Warning|Careful|Never|Important|THIS REQUIRES CODE CHANGES)/i.test(q[0]) ? ' warn' : (/^\*\*Tip/i.test(q[0]) ? ' tip' : '');
        var box = h('div', { 'class': 'ai-md-callout' + kind }), para = [];
        q.concat(['']).forEach(function (l) {
          if (l.trim()) { para.push(l); return; }
          if (para.length) { box.appendChild(inline(para.join(' '), h('p'), ids)); para = []; }
        });
        doc.appendChild(box);
        continue;
      }
      if (/^\|/.test(line) && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1])) {   // table
        var table = h('table'), tbody = h('tbody'), tr = h('tr');
        cells(line).forEach(function (c) { tr.appendChild(inline(c, h('th'), ids)); });
        table.appendChild(h('thead', {}, [tr]));
        i += 2;
        while (i < lines.length && /^\|/.test(lines[i])) {
          var row = h('tr');
          cells(lines[i]).forEach(function (c) { row.appendChild(inline(c, h('td'), ids)); });
          tbody.appendChild(row); i++;
        }
        table.appendChild(tbody);
        doc.appendChild(h('div', { 'class': 'ai-md-tablewrap' }, [table]));
        continue;
      }
      if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {                // lists (incl. "- [ ]" checklists)
        var ordered = /^\s*\d+\./.test(line);
        var itemRe = ordered ? /^\s*\d+\.\s+(.*)$/ : /^\s*[-*]\s+(.*)$/;
        var list = h(ordered ? 'ol' : 'ul');
        if (ordered) list.setAttribute('start', /^\s*(\d+)\./.exec(line)[1]);
        while (i < lines.length && itemRe.test(lines[i])) {
          var itemText = itemRe.exec(lines[i])[1]; i++;
          while (i < lines.length && lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && !itemRe.test(lines[i])) { itemText += ' ' + lines[i].trim(); i++; }
          var cm = /^\[( |x|X)\]\s+(.*)$/.exec(itemText);
          if (cm) {
            list.classList.add('ai-md-checklist');
            var li = h('li', { 'class': 'ai-md-check' }, [h('input', { type: 'checkbox', checked: cm[1] !== ' ', 'aria-label': cm[2].replace(/[`*]/g, '') })]);
            li.appendChild(inline(cm[2], h('span'), ids));
            list.appendChild(li);
          } else {
            list.appendChild(inline(itemText, h('li'), ids));
          }
        }
        doc.appendChild(list);
        continue;
      }
      var p = [line.trim()]; i++;                                                                // paragraph
      while (i < lines.length && lines[i].trim() && !BLOCK_START.test(lines[i])) { p.push(lines[i].trim()); i++; }
      var ptxt = p.join(' ');
      var pcls = /^\*\*Scenario:\*\*/.test(ptxt) ? 'ai-md-scenario' : (/^\*\*[^*]+\*\*\.?$/.test(ptxt) ? 'ai-md-label' : null);
      doc.appendChild(inline(ptxt, h('p', { 'class': pcls }), ids));
    }

    var sections = [];
    Array.prototype.forEach.call(doc.querySelectorAll('h2[data-section]'), function (el) {
      sections.push({ id: el.getAttribute('data-section'), title: el.textContent, el: el, hay: '' });
    });
    // search index: title + every text node up to the next "##"
    var idx = -1, texts = [];
    Array.prototype.forEach.call(doc.children, function (child) {
      if (child.matches && child.matches('h2[data-section]')) { idx++; texts[idx] = child.textContent; }
      else if (idx >= 0) texts[idx] += ' ' + child.textContent;
    });
    sections.forEach(function (s, n) { s.hay = (texts[n] || s.title).toLowerCase().replace(/\s+/g, ' '); });
    return { title: title, doc: doc, sections: sections, ids: ids };
  }

  // ── contents list, active section, search ────────────────────────────────────────────────────────
  function setActive(id) {
    if (S.activeId === id) return;
    S.activeId = id;
    S.sections.forEach(function (s) {
      var on = s.id === id;
      s.item.classList.toggle('active', on);
      if (on) s.item.setAttribute('aria-current', 'true'); else s.item.removeAttribute('aria-current');
    });
    var cur = S.byId[id];
    if (S.mobileLabel && cur) S.mobileLabel.textContent = cur.title;
    if (cur && !cur.item.hidden) keepNavItemVisible(cur.item);
  }
  function keepNavItemVisible(item) {
    var box = S.navList, br = box.getBoundingClientRect(), ir = item.getBoundingClientRect();
    if (br.height === 0) return;                                             // contents list not on screen (small screens)
    if (ir.top < br.top + 8 || ir.bottom > br.bottom - 8) {
      var target = box.scrollTop + (ir.top - br.top) - (box.clientHeight / 2) + (ir.height / 2);
      box.scrollTo({ top: Math.max(0, target), behavior: reducedMotion() ? 'auto' : 'smooth' });
    }
  }
  function currentFromScroll() {
    var top = S.scroll.getBoundingClientRect().top, cur = S.sections[0];
    for (var n = 0; n < S.sections.length; n++) {
      if (S.sections[n].el.getBoundingClientRect().top - top <= ACTIVE_THRESHOLD) cur = S.sections[n]; else break;
    }
    return cur;
  }
  function updateFromScroll() { S.ticking = false; if (!S.lock && S.sections.length) setActive(currentFromScroll().id); }
  function onScroll() {
    if (S.lock) return;                                                     // a click-driven jump is in progress
    if (!S.ticking) { S.ticking = true; window.requestAnimationFrame(updateFromScroll); }
  }
  function releaseLockSoon() { clearTimeout(S.lockTimer); S.lockTimer = setTimeout(function () { S.lock = false; }, LOCK_MS); }
  function cancelAnim() { if (S.anim) { window.cancelAnimationFrame(S.anim); S.anim = 0; } }
  function userScrolled() {                                                 // wheel / touch / keys / scrollbar: the reader takes over
    cancelAnim(); clearTimeout(S.lockTimer); S.lock = false;
  }
  // Fixed-duration eased scroll of the guide pane (native smooth scrolling has no predictable length on long jumps).
  function scrollPaneTo(top, instant, done) {
    cancelAnim();
    var start = S.scroll.scrollTop, delta = top - start;
    if (instant || reducedMotion() || Math.abs(delta) < 2) { S.scroll.scrollTop = top; done(); return; }
    var dur = Math.min(520, 220 + Math.abs(delta) * 0.06), t0 = window.performance.now();
    function step(now) {
      var p = Math.min(1, (now - t0) / dur), e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      S.scroll.scrollTop = start + delta * e;
      if (p < 1 && S.anim) S.anim = window.requestAnimationFrame(step); else { S.anim = 0; done(); }
    }
    S.anim = window.requestAnimationFrame(step);
  }

  function isGuideHash() { return /^#guide(\/|$)/.test(location.hash); }
  function parseHash() { var m = /^#guide(?:\/([A-Za-z0-9-]+))?$/.exec(location.hash); return m ? { id: m[1] || null } : null; }

  function goTo(id, opts) {
    opts = opts || {};
    var s = S.byId[id];
    if (!s || !S.scroll) return false;
    S.lock = true; clearTimeout(S.lockTimer);                               // the clicked item stays highlighted while we scroll
    setActive(id);
    var top = s.el.getBoundingClientRect().top - S.scroll.getBoundingClientRect().top + S.scroll.scrollTop - SCROLL_OFFSET;
    scrollPaneTo(Math.max(0, top), !!opts.instant, releaseLockSoon);
    if (opts.history !== false && isGuideHash()) history.replaceState(history.state, '', '#guide/' + id);
    if (S.shell) S.shell.classList.remove('nav-open');
    if (!opts.keepFocus) focusPane();
    return true;
  }

  function applySearch() {
    var q = S.input.value.trim().toLowerCase(), terms = q ? q.split(/\s+/) : [], shown = 0;
    S.sections.forEach(function (s) {
      var ok = terms.every(function (t) { return s.hay.indexOf(t) !== -1; });
      s.item.hidden = !ok; if (ok) shown++;
      s.rank = ok && terms.every(function (t) { return s.title.toLowerCase().indexOf(t) !== -1; }) ? 0 : 1;
    });
    // While searching, sections whose TITLE matches come first; with no search the guide's own order is restored.
    S.sections.slice().sort(function (a, b) { return terms.length ? (a.rank - b.rank) || (S.sections.indexOf(a) - S.sections.indexOf(b)) : S.sections.indexOf(a) - S.sections.indexOf(b); })
      .forEach(function (s) { S.navList.insertBefore(s.item, S.emptyEl); });
    S.input.parentNode.classList.toggle('has-text', !!q);
    S.countEl.textContent = terms.length ? shown + ' of ' + S.sections.length + ' sections' : '';
    S.emptyEl.hidden = !(terms.length && shown === 0);
    var cur = S.byId[S.activeId];
    if (cur && !cur.item.hidden) keepNavItemVisible(cur.item);
  }

  // ── build the popup contents ─────────────────────────────────────────────────────────────────────
  function build(markdown) {
    var out = render(markdown);
    S.sections = out.sections; S.byId = {}; S.activeId = null; S.lock = false;
    if (out.title) document.getElementById('aiGuideTitle').textContent = out.title;

    S.input = h('input', { type: 'search', placeholder: 'Search the guide…', 'aria-label': 'Search the guide', autocomplete: 'off', spellcheck: 'false' });
    var clearBtn = h('button', { 'class': 'ai-guide-search-clear', type: 'button', 'aria-label': 'Clear search', text: '×' });
    S.countEl = h('div', { 'class': 'ai-guide-count', 'aria-live': 'polite' });
    S.emptyEl = h('div', { 'class': 'ai-guide-noresults', text: 'No section matches. Try a shorter word, such as "logo" or "key".', hidden: true });
    S.navList = h('nav', { 'class': 'ai-guide-navlist', 'aria-label': 'Guide sections' });
    S.sections.forEach(function (s) {
      s.item = h('a', { 'class': 'ai-guide-navitem', href: '#guide/' + s.id, 'data-goto': s.id, text: s.title });
      S.byId[s.id] = s;
      S.navList.appendChild(s.item);
    });
    S.navList.appendChild(S.emptyEl);

    S.input.addEventListener('input', applySearch);
    S.input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && S.input.value) { e.preventDefault(); e.stopPropagation(); S.input.value = ''; applySearch(); }
      else if (e.key === 'Enter') {
        var first = Array.prototype.map.call(S.navList.querySelectorAll('.ai-guide-navitem:not([hidden])'), function (a) { return S.byId[a.getAttribute('data-goto')]; })[0];
        if (first) { e.preventDefault(); goTo(first.id, { keepFocus: true }); }
      }
    });
    clearBtn.addEventListener('click', function () { S.input.value = ''; applySearch(); S.input.focus(); });

    S.mobileLabel = h('span', { 'class': 'label', text: S.sections.length ? S.sections[0].title : 'Contents' });
    var mobilebar = h('button', { 'class': 'ai-guide-mobilebar', type: 'button', 'aria-expanded': 'false', 'aria-label': 'Show guide sections' },
      [h('i', { 'class': 'fas fa-list', 'aria-hidden': 'true' }), S.mobileLabel, h('span', { 'class': 'hint', text: 'Sections ▾' })]);
    var nav = h('aside', { 'class': 'ai-guide-nav' }, [h('div', { 'class': 'ai-guide-search' }, [S.input, clearBtn, S.countEl]), S.navList]);
    S.spacer = h('div', { 'class': 'ai-guide-endspacer', 'aria-hidden': 'true' });
    out.doc.appendChild(S.spacer);
    S.scroll = h('div', { 'class': 'ai-guide-scroll', tabindex: '-1' }, [out.doc]);
    var main = h('div', { 'class': 'ai-guide-main' }, [S.scroll]);
    S.shell = h('div', { 'class': 'ai-guide-shell' }, [mobilebar, nav, main]);

    mobilebar.addEventListener('click', function () {
      var open = S.shell.classList.toggle('nav-open');
      mobilebar.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    // one delegated handler for every contents item and every in-document link
    S.shell.addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('a[data-goto]') : null;
      if (!a) return;
      e.preventDefault();
      goTo(a.getAttribute('data-goto'));
    });
    S.scroll.addEventListener('scroll', onScroll, { passive: true });
    ['wheel', 'touchstart', 'pointerdown'].forEach(function (ev) { S.scroll.addEventListener(ev, userScrolled, { passive: true }); });
    S.scroll.addEventListener('keydown', userScrolled);

    clear(document.getElementById('aiGuideRoot')).appendChild(S.shell);
    S.built = true;
    finishOpen();
  }

  function focusPane() { try { S.scroll.focus({ preventScroll: true }); } catch (e) { /* not focusable: nothing to do */ } }

  function layoutSpacer() {
    if (S.spacer && S.scroll) S.spacer.style.height = Math.max(0, S.scroll.clientHeight - 220) + 'px';   // lets the last sections reach the top
  }
  function finishOpen() {
    if (!S.built || !S.shown) return;
    layoutSpacer();
    var id = S.pendingId && S.byId[S.pendingId] ? S.pendingId : null;
    if (id) goTo(id, { instant: true, history: false });
    else { S.scroll.scrollTop = 0; setActive(S.sections.length ? S.sections[0].id : null); }
    if (id && isGuideHash()) history.replaceState(history.state, '', '#guide/' + id);
    S.pendingId = null;
    focusPane();
  }

  // ── open / close / history ───────────────────────────────────────────────────────────────────────
  function open(sectionId, fromHistory) {
    if (!modal) return;
    if (S.active) { if (sectionId) goTo(sectionId, { history: !fromHistory }); return; }
    S.active = true; S.built = false; S.shown = false; S.pendingId = sectionId || null;
    document.getElementById('aiGuideTitle').textContent = 'AI Configuration Guide';
    clear(document.getElementById('aiGuideRoot')).appendChild(h('div', { 'class': 'ai-guide-loading', text: 'Loading the guide…' }));
    if (!fromHistory) { history.pushState({ aiGuide: true }, '', '#guide' + (sectionId ? '/' + sectionId : '')); S.pushed = true; }
    else S.pushed = false;
    modal.show();
    var token = ++S.loadToken;
    fetch(cfg.url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return { success: false }; }); })
      .then(function (res) {
        if (token !== S.loadToken || !S.active) return;
        if (!res.success) { clear(document.getElementById('aiGuideRoot')).appendChild(h('div', { 'class': 'ai-guide-error', text: res.message || 'The guide is not available right now.' })); return; }
        build(res.data.markdown);
      })
      .catch(function () {
        if (token !== S.loadToken) return;
        clear(document.getElementById('aiGuideRoot')).appendChild(h('div', { 'class': 'ai-guide-error', text: 'The guide could not be loaded. Check your connection and try again.' }));
      });
  }

  function onNavigate() {                                                    // back / forward / edited URL hash
    if (S.ignorePop) { S.ignorePop = false; return; }
    var target = parseHash();
    if (target) {
      if (!S.active) open(target.id, true);
      else if (target.id) goTo(target.id, { history: false });
    } else if (S.active) {
      S.pushed = false;
      modal.hide();
    }
  }

  function init(options) {
    cfg = options;
    modalEl = document.getElementById(cfg.modalId);
    var btn = document.getElementById(cfg.buttonId);
    if (!modalEl || !btn || !window.bootstrap) return;
    modal = new window.bootstrap.Modal(modalEl);
    btn.addEventListener('click', function () { open(null, false); });
    modalEl.addEventListener('shown.bs.modal', function () { S.shown = true; finishOpen(); });
    modalEl.addEventListener('hidden.bs.modal', function () {
      S.active = false; S.shown = false; S.built = false; S.loadToken++; userScrolled();
      if (S.shell) S.shell.classList.remove('nav-open');
      if (isGuideHash()) {
        if (S.pushed) { S.pushed = false; S.ignorePop = true; history.back(); }          // remove the entry we added
        else history.replaceState(null, '', location.pathname + location.search);
      }
    });
    window.addEventListener('resize', function () {
      if (S.built) layoutSpacer();
      // The Admin sidebar script clears body overflow on every resize (which unlocks ANY open modal).
      // It registered its listener after ours, so re-assert the lock one tick later, after it has run.
      setTimeout(function () { if (S.active) document.body.style.overflow = 'hidden'; }, 0);
    });
    window.addEventListener('popstate', onNavigate);
    window.addEventListener('hashchange', onNavigate);
    var start = parseHash();
    if (start) open(start.id, true);                                         // reload / shared link
  }

  window.AIGuide = { init: init, open: function (id) { open(id || null, false); }, _state: S, _render: render };
})();
