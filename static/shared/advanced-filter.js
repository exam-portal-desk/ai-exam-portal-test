/* static/shared/advanced-filter.js
 * The one Advanced Filter panel (Manage Exams, Results History): a "Filters" button opening a panel of conditions, each a
 * field + operator + value, combined with Match ALL (AND) or Match ANY (OR), plus applied-filter chips.
 *
 * It is a front end only: the server validates and evaluates the conditions (app/services/advanced_filter.py). Controls are
 * the project's own: themed dropdowns (static/admin/select-enhance.js -> enhanceSelect) and the themed calendar
 * (static/shared/date-picker.js -> createDatePicker). Pages that use this must load select-enhance.css/.js,
 * date-picker.css/.js and advanced-filter.css first.
 *
 * AdvancedFilter.create({
 *   mount,            // element that receives the Filters button + panel
 *   summaryEl,        // element that receives the applied-filter chips
 *   fields,           // [{ key, label, kind: text|number|date|time|enum|entity, values: [[value,label]] | options: () => [[value,label]] }]
 *   dateFormat,       // strftime-style display format for dates, e.g. '%d-%m-%Y' (value sent is always YYYY-MM-DD)
 *   maxConditions,    // default 12
 *   onApply(json),    // called with the JSON string to send as `adv` ('' when nothing applies)
 * }) -> { json(), clear() }
 */
(function (global) {
  var OPS = {
    text:   [['contains', 'contains words'], ['not_contains', 'does not contain'], ['equals', 'is exactly'], ['starts_with', 'starts with'], ['ends_with', 'ends with']],
    number: [['gt', 'is greater than'], ['lt', 'is less than'], ['eq', 'is equal to'], ['between', 'is between']],
    date:   [['on', 'is on'], ['before', 'is before'], ['after', 'is after'], ['between', 'is between']],
    time:   [['at', 'is at'], ['before', 'is before'], ['after', 'is after'], ['between', 'is between']],
    enum:   [['is', 'is'], ['is_not', 'is not']],
    entity: [['is', 'is'], ['is_not', 'is not']],
  };

  function el(tag, cls, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'text') e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); });
    return e;
  }
  function copy(o) { return JSON.parse(JSON.stringify(o)); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function create(opts) {
    var fields = opts.fields, max = opts.maxConditions || 12;
    var applied = { match: 'all', conditions: [] }, draft = copy(applied);
    var byKey = {}; fields.forEach(function (f) { byKey[f.key] = f; });

    function options(f) { return f.options ? f.options() : (f.values || []); }
    function firstValue(f) { var o = options(f)[0]; return o ? String(o[0]) : ''; }
    function newCondition() {
      var f = fields[0];
      return { field: f.key, op: OPS[f.kind][0][0], value: (f.kind === 'enum' || f.kind === 'entity') ? firstValue(f) : '', value2: '' };
    }

    /* ── DOM ──────────────────────────────────────────────────────────── */
    var wrap = el('div', 'adv-wrap');
    var btn = el('button', 'adv-btn', { type: 'button', 'aria-haspopup': 'dialog', 'aria-expanded': 'false' });
    btn.innerHTML = '<i class="fas fa-sliders"></i> Filters <span class="adv-count" style="display:none">0</span>';
    var count = btn.querySelector('.adv-count');
    var panel = el('div', 'adv-panel', { role: 'dialog', 'aria-label': 'Advanced filter' });
    panel.innerHTML =
      '<div class="adv-head"><span class="adv-title"><i class="fas fa-filter"></i> Advanced filter</span>' +
      '<button type="button" class="adv-close" aria-label="Close filters"><i class="fas fa-times"></i></button>' +
      '<div class="adv-match" role="group" aria-label="How conditions combine">' +
      '<button type="button" data-match="all" class="active" title="A row must satisfy every condition (AND)">Match ALL</button>' +
      '<button type="button" data-match="any" title="A row may satisfy any one condition (OR)">Match ANY</button></div></div>' +
      '<div class="adv-rows"></div><div class="adv-error" role="alert"></div>' +
      '<div class="adv-foot"><button type="button" class="adv-act" data-a="add"><i class="fas fa-plus"></i> Add filter</button>' +
      '<div class="adv-foot-right"><button type="button" class="adv-act" data-a="clear">Clear all</button>' +
      '<button type="button" class="adv-act" data-a="reset" title="Discard unapplied edits">Reset</button>' +
      '<button type="button" class="adv-act primary" data-a="apply">Apply</button></div></div>';
    wrap.append(btn, panel);
    opts.mount.appendChild(wrap);
    var rowsEl = panel.querySelector('.adv-rows'), errEl = panel.querySelector('.adv-error');

    /* ── controls built from the project's shared components ──────────── */
    function selectControl(items, value, onChange, aria) {
      var box = el('div', 'adv-ctl');
      var sel = el('select', '', { 'aria-label': aria });
      if (!items.length) sel.appendChild(el('option', '', { value: '', text: 'Nothing to choose from' }));
      items.forEach(function (it) { sel.appendChild(el('option', '', { value: String(it[0]), text: it[1] })); });
      box.appendChild(sel);
      sel.value = String(value);
      if (sel.value !== String(value) && items.length) sel.value = String(items[0][0]);
      global.enhanceSelect(sel, { block: true });
      sel.addEventListener('change', function () { onChange(sel.value); });
      onChange(sel.value, true);
      return box;
    }
    function textControl(kind, cond, which) {
      var inp = el('input', 'adv-in', { 'aria-label': which === 'value2' ? 'Upper value' : 'Value' });
      if (kind === 'text') { inp.type = 'text'; inp.maxLength = 200; inp.placeholder = 'e.g. mock test'; }
      else { inp.type = 'text'; inp.inputMode = 'numeric'; inp.placeholder = 'number'; inp.pattern = '[0-9]*'; }
      inp.value = cond[which] == null ? '' : cond[which];
      inp.addEventListener('input', function () { if (kind === 'number') inp.value = inp.value.replace(/[^0-9]/g, ''); cond[which] = inp.value; });
      return inp;
    }
    function dateControl(cond, which) {
      var box = el('div', 'dt-field adv-date');
      var inp = el('input', 'adv-in dt-date-input', { type: 'text', readonly: 'readonly', placeholder: 'Select date', autocomplete: 'off', 'aria-label': which === 'value2' ? 'Upper date' : 'Date' });
      box.append(inp, el('i', 'fas fa-calendar-days dt-field-icon'));
      var picker = global.createDatePicker(inp, { displayFormat: opts.dateFormat || '%d-%m-%Y', onChange: function (iso) { cond[which] = iso || ''; } });
      if (cond[which]) picker.setValue(cond[which]);
      return box;
    }
    function timeControl(cond, which) {
      var box = el('div', 'adv-time');
      var cur = (cond[which] || '').split(':'), h = cur[0] || '', m = cur[1] || '';
      var hours = [], mins = [];
      for (var i = 0; i < 24; i++) hours.push([pad(i), pad(i)]);
      for (var j = 0; j < 60; j += 5) mins.push([pad(j), pad(j)]);
      function sync() { cond[which] = (h !== '' && m !== '') ? h + ':' + m : ''; }
      box.appendChild(selectControl(hours, h || '09', function (v) { h = v; sync(); }, 'Hour'));
      box.appendChild(el('span', 'adv-and', { text: ':' }));
      box.appendChild(selectControl(mins, m || '00', function (v) { m = v; sync(); }, 'Minute'));
      return box;
    }
    function valueControl(f, cond, which) {
      if (f.kind === 'enum' || f.kind === 'entity') {
        var items = options(f).map(function (o) { return [String(o[0]), o[1]]; });
        if (cond[which] == null || cond[which] === '' || !items.some(function (o) { return o[0] === String(cond[which]); })) cond[which] = items.length ? items[0][0] : '';
        return selectControl(items, cond[which], function (v) { cond[which] = v; }, 'Value');
      }
      if (f.kind === 'date') return dateControl(cond, which);
      if (f.kind === 'time') return timeControl(cond, which);
      return textControl(f.kind, cond, which);
    }

    function renderRows() {
      rowsEl.replaceChildren();
      if (!draft.conditions.length) rowsEl.appendChild(el('div', 'adv-empty', { text: 'No conditions yet. Use “Add filter” to start.' }));
      draft.conditions.forEach(function (cond, idx) {
        var f = byKey[cond.field], row = el('div', 'adv-row');
        row.appendChild(selectControl(fields.map(function (x) { return [x.key, x.label]; }), cond.field, function (v, initial) {
          if (initial || v === cond.field) return;
          var nf = byKey[v];
          cond.field = v; cond.op = OPS[nf.kind][0][0];
          cond.value = (nf.kind === 'enum' || nf.kind === 'entity') ? firstValue(nf) : ''; cond.value2 = '';
          renderRows();
        }, 'Field'));
        row.appendChild(selectControl(OPS[f.kind], cond.op, function (v, initial) {
          if (initial || v === cond.op) return;
          cond.op = v; renderRows();
        }, 'Operator'));
        var val = el('div', 'adv-val');
        val.appendChild(valueControl(f, cond, 'value'));
        if (cond.op === 'between') { val.appendChild(el('span', 'adv-and', { text: 'and' })); val.appendChild(valueControl(f, cond, 'value2')); }
        row.appendChild(val);
        var rm = el('button', 'adv-rm', { type: 'button', title: 'Remove this condition', 'aria-label': 'Remove condition' });
        rm.innerHTML = '<i class="fas fa-times"></i>';
        rm.addEventListener('click', function (e) { e.stopPropagation(); draft.conditions.splice(idx, 1); renderRows(); });
        row.appendChild(rm);
        rowsEl.appendChild(row);
      });
      panel.querySelectorAll('.adv-match button').forEach(function (b) { b.classList.toggle('active', b.dataset.match === draft.match); });
      place();
    }

    /* ── summary chips ────────────────────────────────────────────────── */
    function describe(cond) {
      var f = byKey[cond.field];
      var op = (OPS[f.kind].filter(function (o) { return o[0] === cond.op; })[0] || ['', ''])[1];
      var v = cond.value;
      if (f.kind === 'enum' || f.kind === 'entity') v = (options(f).filter(function (o) { return String(o[0]) === String(cond.value); })[0] || [0, cond.value])[1];
      else if (f.kind === 'text') v = '“' + v + '”';
      var day = function (iso) { var p = String(iso).split('-'); return p.length === 3 ? new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : iso; };
      if (f.kind === 'date') { v = day(v); if (cond.op === 'between') return f.label + ' ' + op + ' ' + v + ' – ' + day(cond.value2); }
      if (cond.op === 'between') v = v + ' – ' + cond.value2;
      return f.label + ' ' + op + ' ' + v;
    }
    function validate(conds) {
      for (var i = 0; i < conds.length; i++) {
        var c = conds[i], f = byKey[c.field];
        if (c.value === '' || c.value == null) return 'Fill in a value for “' + f.label + '”.';
        if (c.op === 'between' && (c.value2 === '' || c.value2 == null)) return 'Fill in both values for “' + f.label + '”.';
      }
      return '';
    }
    function renderSummary() {
      var n = applied.conditions.length;
      count.style.display = n ? '' : 'none'; count.textContent = n; btn.classList.toggle('has-filters', n > 0);
      var sum = opts.summaryEl; if (!sum) return;
      sum.replaceChildren(); sum.classList.toggle('show', n > 0);
      if (!n) return;
      sum.appendChild(el('span', '', { text: n === 1 ? 'Filtering by:' : 'Matching ' + (applied.match === 'all' ? 'ALL' : 'ANY') + ' of:' }));
      applied.conditions.forEach(function (c, i) {
        var chip = el('span', 'adv-chip'), x = el('button', '', { type: 'button', title: 'Remove', 'aria-label': 'Remove filter: ' + describe(c) });
        chip.appendChild(el('span', '', { text: describe(c) }));
        x.innerHTML = '<i class="fas fa-times" style="font-size:.6rem"></i>';
        x.addEventListener('click', function () { applied.conditions.splice(i, 1); commit(); });
        chip.appendChild(x); sum.appendChild(chip);
      });
      var clr = el('button', 'adv-link', { type: 'button', text: 'Clear all' });
      clr.addEventListener('click', function () { applied = { match: 'all', conditions: [] }; commit(); });
      sum.appendChild(clr);
    }
    function json() { return applied.conditions.length ? JSON.stringify(applied) : ''; }
    function commit() {
      draft = copy(applied);
      renderSummary(); renderRows();
      if (opts.onApply) opts.onApply(json());
    }

    /* ── open / close ─────────────────────────────────────────────────── */
    /* Placement. The panel is position:fixed and placed from the trigger's own rectangle, so no parent's overflow / clipping
       can crop it and it never depends on where the trigger sits in its toolbar: on desktop it opens under the trigger (aligned
       to the trigger's left or right edge, whichever keeps it on screen), flips above when there is more room there, and its
       height is limited to the room available (the condition list scrolls inside it, the footer stays visible). On a phone it
       is a full-screen sheet with the page behind it locked. */
    var phone = window.matchMedia('(max-width: 767.98px)');
    function place() {
      if (!panel.classList.contains('open')) return;
      var root = document.documentElement;
      if (phone.matches) {
        panel.classList.add('sheet'); panel.removeAttribute('style'); root.classList.add('adv-lock'); return;
      }
      panel.classList.remove('sheet'); root.classList.remove('adv-lock');
      var r = btn.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight, m = 8;
      var w = Math.min(720, vw - m * 2);
      var left = r.left < vw / 2 ? r.left : r.right - w;
      left = Math.max(m, Math.min(left, vw - w - m));
      var below = vh - r.bottom - 6 - m, above = r.top - 6 - m, want = panel.scrollHeight;
      var openUp = below < Math.min(want, 360) && above > below;
      var room = Math.max(160, openUp ? above : below);
      panel.style.position = 'fixed'; panel.style.width = w + 'px'; panel.style.left = left + 'px'; panel.style.right = 'auto';
      panel.style.maxHeight = room + 'px';
      var h = Math.min(want, room);
      panel.style.top = (openUp ? Math.max(m, r.top - 6 - h) : r.bottom + 6) + 'px';
    }
    function setOpen(open) {
      panel.classList.toggle('open', open); btn.classList.toggle('open', open); btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { draft = copy(applied); errEl.textContent = ''; renderRows(); place(); }
      else { document.documentElement.classList.remove('adv-lock'); panel.classList.remove('sheet'); panel.removeAttribute('style'); }
    }
    window.addEventListener('resize', place);
    window.addEventListener('orientationchange', place);
    window.addEventListener('scroll', function () { if (!phone.matches) place(); }, { passive: true, capture: true });
    panel.querySelector('.adv-close').addEventListener('click', function () { setOpen(false); btn.focus(); });
    btn.addEventListener('click', function (e) { e.stopPropagation(); setOpen(!panel.classList.contains('open')); });
    // A click outside closes the panel; the shared dropdown menus live inside it and the calendar popup is a body-level .dt-pop.
    document.addEventListener('click', function (e) {
      if (!panel.classList.contains('open')) return;
      // The click path is read from the event itself: a control the click just removed (a condition's X re-renders the
      // rows) is already detached from the page by the time this runs, so panel.contains(e.target) would be false and
      // the panel would wrongly close.
      var path = e.composedPath ? e.composedPath() : [];
      if (path.indexOf(panel) !== -1 || path.indexOf(btn) !== -1) return;
      var t = e.target;
      if (panel.contains(t) || btn.contains(t) || (t.closest && (t.closest('.dt-pop') || t.closest('.sel-enh')))) return;
      setOpen(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && panel.classList.contains('open') && !document.querySelector('.sel-enh.open, .dt-pop.open')) setOpen(false);
    });
    panel.querySelectorAll('.adv-match button').forEach(function (b) { b.addEventListener('click', function () { draft.match = b.dataset.match; renderRows(); }); });
    panel.querySelectorAll('.adv-act').forEach(function (b) {
      b.addEventListener('click', function () {
        var a = b.dataset.a;
        if (a === 'add') {
          if (draft.conditions.length >= max) { errEl.textContent = 'At most ' + max + ' conditions.'; return; }
          draft.conditions.push(newCondition()); renderRows();
        } else if (a === 'reset') { draft = copy(applied); errEl.textContent = ''; renderRows(); }
        else if (a === 'clear') { applied = { match: 'all', conditions: [] }; commit(); setOpen(false); }
        else {
          var err = validate(draft.conditions); errEl.textContent = err;
          if (err) return;
          applied = copy(draft); commit(); setOpen(false);
        }
      });
    });

    return { json: json, clear: function () { applied = { match: 'all', conditions: [] }; commit(); } };
  }

  global.AdvancedFilter = { create: create };
})(window);
