/* static/shared/date-picker.js
 * Reusable themed calendar date-picker — same visual behavior and
 * interaction pattern as the custom picker built into
 * templates/admin/exams.html (readonly text input + popup calendar,
 * plain "YYYY-MM-DD" kept in data-iso while the input displays a
 * formatted string), factored into a standalone, multi-instance-safe
 * module so other pages (e.g. Admin User Analytics' From/To fields)
 * can reuse the same picker instead of native <input type="date">
 * or a page-specific reimplementation.
 *
 * createDatePicker(inputEl, { displayFormat, onChange(iso) })
 * returns { open(), close(), setValue(isoString) }
 */
(function (global) {
  var MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  var DAY_NAMES = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function formatDisplayDate(fmt, y, m, d) {
    var dow = new Date(y, m, d).getDay();
    var tokens = {
      '%Y': String(y), '%y': String(y).slice(-2),
      '%m': pad2(m + 1), '%d': pad2(d),
      '%B': MONTH_NAMES[m], '%b': MONTH_NAMES[m].slice(0, 3),
      '%A': DAY_NAMES[dow], '%a': DAY_NAMES[dow].slice(0, 3),
    };
    var out = fmt;
    Object.keys(tokens).forEach(function (t) { out = out.split(t).join(tokens[t]); });
    return out;
  }

  function createDatePicker(input, opts) {
    opts = opts || {};
    var displayFmt = opts.displayFormat || '%d %B %Y';
    var state = { year: 0, month: 0, selectedDay: null };
    var pop = null;

    function build() {
      pop = document.createElement('div');
      pop.className = 'dt-pop';
      pop.innerHTML =
        '<div class="dt-cal">' +
          '<div class="dt-cal-hdr">' +
            '<button type="button" class="dt-cal-nav" data-nav="-1"><i class="fas fa-chevron-left"></i></button>' +
            '<span class="dt-cal-title"></span>' +
            '<button type="button" class="dt-cal-nav" data-nav="1"><i class="fas fa-chevron-right"></i></button>' +
          '</div>' +
          '<div class="dt-cal-week"><span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span></div>' +
          '<div class="dt-cal-grid"></div>' +
          '<div class="dt-cal-ftr"><button type="button" class="dt-link-btn" data-today>Today</button><button type="button" class="dt-link-btn" data-cancel>Cancel</button></div>' +
        '</div>';
      document.body.appendChild(pop);
      pop.querySelector('[data-nav="-1"]').addEventListener('click', function () { shiftMonth(-1); });
      pop.querySelector('[data-nav="1"]').addEventListener('click', function () { shiftMonth(1); });
      pop.querySelector('[data-today]').addEventListener('click', function () {
        var t = new Date();
        selectDay(t.getFullYear(), t.getMonth(), t.getDate());
      });
      pop.querySelector('[data-cancel]').addEventListener('click', close);
    }

    function shiftMonth(delta) {
      state.month += delta;
      if (state.month < 0) { state.month = 11; state.year--; }
      if (state.month > 11) { state.month = 0; state.year++; }
      render();
    }

    function render() {
      pop.querySelector('.dt-cal-title').textContent = MONTH_NAMES[state.month] + ' ' + state.year;
      var grid = pop.querySelector('.dt-cal-grid');
      grid.innerHTML = '';

      var startDow = new Date(state.year, state.month, 1).getDay();
      var daysInMonth = new Date(state.year, state.month + 1, 0).getDate();
      var daysInPrevMonth = new Date(state.year, state.month, 0).getDate();
      var now = new Date();

      var cells = [];
      for (var i = 0; i < startDow; i++) {
        var pm = state.month === 0 ? 11 : state.month - 1;
        var py = state.month === 0 ? state.year - 1 : state.year;
        cells.push({ day: daysInPrevMonth - startDow + 1 + i, other: true, y: py, m: pm });
      }
      for (var d = 1; d <= daysInMonth; d++) cells.push({ day: d, other: false, y: state.year, m: state.month });
      var remain = (7 - (cells.length % 7)) % 7;
      for (var j = 1; j <= remain; j++) {
        var nm = state.month === 11 ? 0 : state.month + 1;
        var ny = state.month === 11 ? state.year + 1 : state.year;
        cells.push({ day: j, other: true, y: ny, m: nm });
      }

      cells.forEach(function (c) {
        var el = document.createElement('div');
        el.className = 'dt-cal-day' + (c.other ? ' other-month' : '');
        if (!c.other && c.y === now.getFullYear() && c.m === now.getMonth() && c.day === now.getDate()) el.classList.add('today');
        if (state.selectedDay && c.y === state.selectedDay.y && c.m === state.selectedDay.m && c.day === state.selectedDay.d) el.classList.add('selected');
        el.textContent = c.day;
        el.addEventListener('click', function () { selectDay(c.y, c.m, c.day); });
        grid.appendChild(el);
      });
    }

    function selectDay(y, m, d) {
      state.selectedDay = { y: y, m: m, d: d };
      state.year = y; state.month = m;
      input.dataset.iso = y + '-' + pad2(m + 1) + '-' + pad2(d);
      input.value = formatDisplayDate(displayFmt, y, m, d);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      if (opts.onChange) opts.onChange(input.dataset.iso);
      close();
    }

    function positionPop() {
      var r = input.getBoundingClientRect();
      var popW = pop.offsetWidth || 288, popH = pop.offsetHeight || 300;
      var top = r.bottom + 6, left = r.left;
      if (left + popW > window.innerWidth - 8) left = window.innerWidth - popW - 8;
      if (left < 8) left = 8;
      if (top + popH > window.innerHeight - 8) top = r.top - popH - 6;
      if (top < 8) top = 8;
      pop.style.top = top + 'px';
      pop.style.left = left + 'px';
    }

    function open() {
      if (!pop) build();
      var v = (input.dataset.iso || input.value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
      var now = new Date();
      if (v) {
        state.year = parseInt(v[1], 10); state.month = parseInt(v[2], 10) - 1;
        state.selectedDay = { y: state.year, m: state.month, d: parseInt(v[3], 10) };
      } else {
        state.year = now.getFullYear(); state.month = now.getMonth();
        state.selectedDay = null;
      }
      render();
      positionPop();
      pop.classList.add('open');
      document.addEventListener('mousedown', outsideHandler);
    }

    function close() {
      if (pop) pop.classList.remove('open');
      document.removeEventListener('mousedown', outsideHandler);
    }

    function outsideHandler(e) {
      if (pop && (pop.contains(e.target) || input.contains(e.target))) return;
      close();
    }

    function setValue(iso) {
      var v = (iso || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (!v) { input.value = ''; input.dataset.iso = ''; return; }
      var y = parseInt(v[1], 10), m = parseInt(v[2], 10) - 1, d = parseInt(v[3], 10);
      input.dataset.iso = iso;
      input.value = formatDisplayDate(displayFmt, y, m, d);
    }

    input.addEventListener('click', open);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });

    return { open: open, close: close, setValue: setValue };
  }

  global.createDatePicker = createDatePicker;
})(window);
