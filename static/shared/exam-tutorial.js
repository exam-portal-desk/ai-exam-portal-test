/* "How to Navigate Your Exam" — animated walkthrough on the Exam Instructions page.
 *
 * A DEMONSTRATION ONLY: it keeps its own little state object, draws a sample 5-question exam in the page, and never calls fetch,
 * never touches the real exam, answers, marks or submission. The stage ignores the pointer (CSS) so the demo cannot be clicked;
 * only the tutorial's own controls (Previous / Pause / Next / Replay / step dots) are interactive.
 *
 * It mirrors the real exam window (templates/exam_page.html): buttons "Previous", "Mark for Review", "Save & Next", and on the
 * last question "Submit Exam" (or "Ends automatically at time up" when the exam has manual submission turned off, data-manual-
 * submit="0"); palette statuses answered / review / visited / not-visited with the current question ringed; Mark for Review
 * flags the question and moves on (except on the last question); answers are saved when you move; Submit Exam opens a
 * confirmation summary with "Yes, Submit Exam" and "Go Back". Nothing is invented beyond that.
 */
(function () {
  'use strict';
  var root = document.getElementById('xt');
  if (!root) return;

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var manualSubmit = root.getAttribute('data-manual-submit') !== '0';
  var QUESTIONS = [
    { t: 'Which unit is used to measure electric current?', o: ['Volt', 'Ampere', 'Ohm', 'Watt'] },
    { t: 'Which of these is a renewable source of energy?', o: ['Coal', 'Natural gas', 'Solar', 'Diesel'] },
    { t: 'What is the value of 12 × 8?', o: ['86', '92', '96', '108'] },
    { t: 'Which planet is known as the Red Planet?', o: ['Venus', 'Mars', 'Jupiter', 'Saturn'] },
    { t: 'Which gas do plants absorb for photosynthesis?', o: ['Oxygen', 'Nitrogen', 'Hydrogen', 'Carbon dioxide'] }
  ];
  var N = QUESTIONS.length;
  var $ = function (sel) { return root.querySelector(sel); };
  var stage = $('.xt-stage');
  var st = { cur: 0, ans: {}, mark: {}, vis: { 0: 1 } };

  function status(i) {
    if (st.mark[i]) return 'review';
    if (st.ans[i] !== undefined) return 'answered';
    return st.vis[i] ? 'visited' : 'not-visited';
  }
  function counts() {
    var c = { answered: 0, review: 0, visited: 0, 'not-visited': 0 };
    for (var i = 0; i < N; i++) c[status(i)]++;
    return c;
  }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }

  /* ── stage skeleton (labels copied from the real exam page) ── */
  stage.innerHTML =
    '<div class="xt-top"><div class="xt-name"><i class="fas fa-file-alt"></i><span class="t">Sample Exam</span>' +
    '<span class="xt-qc">Q <span data-r="qno">1</span> / ' + N + '</span></div>' +
    '<div class="xt-timer"><i class="fas fa-clock"></i> Time Left: <strong>45:00</strong></div></div>' +
    '<div class="xt-body"><div class="xt-card" data-r="card"><div class="xt-qtext" data-r="qtext"></div><div data-r="opts"></div></div>' +
    '<div class="xt-side"><div class="xt-card"><div class="xt-side-hd"><i class="fas fa-th"></i> Question Navigation</div><div class="xt-side-bd">' +
    '<div class="question-palette" data-r="palette"></div><div class="xt-legend">' +
    '<div class="legend-box"><span class="swatch" style="background:linear-gradient(90deg,#16a34a,#059669)"></span><small>Answered (<span data-r="c-answered">0</span>)</small></div>' +
    '<div class="legend-box"><span class="swatch" style="background:linear-gradient(90deg,#f59e0b,#f97316)"></span><small>Review (<span data-r="c-review">0</span>)</small></div>' +
    '<div class="legend-box"><span class="swatch" style="background:linear-gradient(90deg,#0ea5e9,#0369a1)"></span><small>Visited (<span data-r="c-visited">0</span>)</small></div>' +
    '<div class="legend-box"><span class="swatch" style="background:linear-gradient(90deg,#6c757d,#495057)"></span><small>Not visited (<span data-r="c-not-visited">0</span>)</small></div>' +
    '</div></div></div></div></div>' +
    '<div class="xt-nav" data-r="nav"></div>' +
    '<div class="xt-sco" data-r="sco"><div class="xt-sco-card"><div class="xt-sco-ic"><i class="fas fa-paper-plane"></i></div>' +
    '<div class="xt-sco-title">Submit Exam?</div><div class="xt-sco-desc">Once submitted, you won\'t be able to change any answers.</div>' +
    '<div class="xt-sco-steps"><span class="on">Submit</span><span>Confirm</span><span>Submitting</span><span>Submitted</span></div>' +
    '<div class="xt-sco-stats">' +
    '<div class="xt-sco-stat"><b style="color:var(--success)" data-r="s-answered">0</b><small>Answered</small></div>' +
    '<div class="xt-sco-stat"><b style="color:var(--warning)" data-r="s-review">0</b><small>Review</small></div>' +
    '<div class="xt-sco-stat"><b style="color:var(--info)" data-r="s-visited">0</b><small>Visited</small></div>' +
    '<div class="xt-sco-stat"><b style="color:var(--text-3)" data-r="s-not-visited">0</b><small>Not visited</small></div></div>' +
    '<div class="xt-sco-acts"><span class="xt-sco-btn primary" data-r="sco-yes"><i class="fas fa-check"></i> Yes, Submit Exam</span>' +
    '<span class="xt-sco-btn" data-r="sco-back"><i class="fas fa-arrow-left"></i> Go Back</span></div></div></div>' +
    '<i class="fas fa-arrow-pointer xt-cursor" data-r="cursor"></i>';

  var R = function (n) { return stage.querySelector('[data-r="' + n + '"]'); };

  function render(swap) {
    var q = QUESTIONS[st.cur];
    R('qno').textContent = st.cur + 1;
    R('qtext').textContent = q.t;
    R('opts').innerHTML = q.o.map(function (txt, i) {
      return '<div class="xt-opt' + (st.ans[st.cur] === i ? ' selected' : '') + '" data-opt="' + i + '"><span class="dot"></span><span><strong>' +
        'ABCD'[i] + '.</strong> ' + esc(txt) + '</span></div>';
    }).join('');
    var pal = '';
    for (var i = 0; i < N; i++) pal += '<span class="palette-link ' + status(i) + (i === st.cur ? ' current' : '') + '" data-q="' + i + '">' + (i + 1) + '</span>';
    R('palette').innerHTML = pal;
    var c = counts();
    ['answered', 'review', 'visited', 'not-visited'].forEach(function (k) {
      R('c-' + k).textContent = c[k]; R('s-' + k).textContent = c[k];
    });
    var last = st.cur === N - 1, marked = !!st.mark[st.cur];
    R('nav').innerHTML =
      '<span class="xt-btn prev"' + (st.cur === 0 ? ' disabled' : '') + ' data-act="prev"><i class="fas fa-chevron-left"></i> Previous</span>' +
      '<div class="xt-nav-mid"><span class="xt-btn mark' + (marked ? ' marked' : '') + '" data-act="mark">' +
      (marked ? '<i class="fas fa-flag-checkered"></i> Marked — Unmark' : '<i class="fas fa-flag"></i> Mark for Review') + '</span>' +
      (!last ? '<span class="xt-btn next" data-act="next">Save &amp; Next <i class="fas fa-chevron-right"></i></span>' : '') +
      (last ? (manualSubmit ? '<span class="xt-btn submit" data-act="submit"><i class="fas fa-paper-plane"></i> Submit Exam</span>'
                            : '<span class="xt-nosubmit"><i class="fas fa-lock"></i> Ends automatically at time up</span>') : '') + '</div>';
    if (swap && !reduce) {
      var card = R('card'); card.classList.add('swap'); void card.offsetWidth; card.classList.remove('swap');
    }
  }
  function visit(i) { st.vis[i] = 1; }
  function setState(s) { st = { cur: s.cur, ans: Object.assign({}, s.ans), mark: Object.assign({}, s.mark), vis: Object.assign({}, s.vis) }; }
  function showSco(on) {
    var c = counts(); ['answered', 'review', 'visited', 'not-visited'].forEach(function (k) { R('s-' + k).textContent = c[k]; });
    R('sco').classList.toggle('show', !!on);
  }

  /* ── the lesson: each step = starting state, what the cursor goes to, and what the click does ── */
  var FULL = { cur: 2, ans: { 0: 1 }, mark: { 1: 1 }, vis: { 0: 1, 1: 1, 2: 1 } };
  var STEPS = [
    { title: 'Choose your answer', text: 'Click an option to select it. The question stays in the palette as Visited until it has an answer.',
      setup: { cur: 0, ans: {}, mark: {}, vis: { 0: 1 } }, target: '[data-opt="1"]',
      acts: [function () { st.ans[0] = 1; render(); }] },
    { title: 'Save & Next', text: 'Save & Next stores your answer and opens the next question. Question 1 is now green (Answered) and question 2 is Visited.',
      setup: { cur: 0, ans: { 0: 1 }, mark: {}, vis: { 0: 1 } }, target: '[data-act="next"]',
      acts: [function () { st.cur = 1; visit(1); render(true); }] },
    { title: 'Previous', text: 'Previous takes you back to the question before. Your saved answer is still selected when you return.',
      setup: { cur: 1, ans: { 0: 1 }, mark: {}, vis: { 0: 1, 1: 1 } }, target: '[data-act="prev"]',
      acts: [function () { st.cur = 0; render(true); }] },
    { title: 'Mark for Review', text: 'Mark for Review flags a question to revisit and moves on to the next one. It turns orange (Review) in the palette.',
      setup: { cur: 1, ans: { 0: 1 }, mark: {}, vis: { 0: 1, 1: 1 } }, target: '[data-act="mark"]',
      acts: [function () { st.mark[1] = 1; render(); }, function () { st.cur = 2; visit(2); render(true); }] },
    { title: 'Question Palette', text: 'Click any number in Question Navigation to jump straight to that question. Green = Answered, Orange = Review, Blue = Visited, Grey = Not visited. The ringed number is where you are.',
      setup: FULL, target: '[data-q="4"]',
      acts: [function () { st.cur = 4; visit(4); render(true); }] },
    { title: manualSubmit ? 'Submit Exam' : 'Ending the exam',
      text: manualSubmit
        ? 'On the last question, Submit Exam opens a summary of Answered / Review / Visited / Not visited. "Yes, Submit Exam" finishes the exam; "Go Back" returns to it. (Demo only — nothing is submitted.)'
        : 'This exam has no Submit button: it ends automatically when the timer reaches zero, so keep an eye on the time.',
      setup: { cur: 4, ans: { 0: 1 }, mark: { 1: 1 }, vis: { 0: 1, 1: 1, 2: 1, 4: 1 } }, target: manualSubmit ? '[data-act="submit"]' : '.xt-nosubmit',
      acts: manualSubmit ? [function () { showSco(true); }, function () { /* cursor goes to Go Back */ }, function () { showSco(false); }] : [],
      cursorAfter: [null, '[data-r="sco-back"]', null] }
  ];

  /* ── runner ── */
  var cursor = R('cursor');
  var token = 0, idx = 0, playing = false, wantAuto = !reduce, finished = false;
  var caption = { no: $('.xt-step-no'), title: $('.xt-cap-title'), text: $('.xt-cap-text') };
  var dots = Array.prototype.slice.call(root.querySelectorAll('.xt-dot'));
  var btn = { prev: $('[data-ctl="prev"]'), next: $('[data-ctl="next"]'), play: $('[data-ctl="play"]'), replay: $('[data-ctl="replay"]') };

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function moveTo(sel, instant) {
    var el = sel && stage.querySelector(sel);
    if (!el) { cursor.classList.remove('on'); return; }
    var s = stage.getBoundingClientRect(), r = el.getBoundingClientRect();
    var x = r.left - s.left + r.width * 0.55, y = r.top - s.top + r.height * 0.6;
    if (instant) cursor.style.transition = 'none';
    cursor.style.transform = 'translate(' + x + 'px,' + y + 'px)';
    cursor.classList.add('on');
    if (instant) { void cursor.offsetWidth; cursor.style.transition = ''; }
  }
  function press(sel) {
    var el = sel && stage.querySelector(sel);
    cursor.classList.remove('click'); void cursor.offsetWidth; cursor.classList.add('click');
    if (el) { el.classList.add('xt-press'); setTimeout(function () { el.classList.remove('xt-press'); }, 380); }
  }
  function paintControls() {
    dots.forEach(function (d, i) { d.classList.toggle('on', i === idx); d.classList.toggle('done', i < idx); });
    btn.prev.disabled = idx === 0;
    btn.next.disabled = idx === STEPS.length - 1;
    btn.play.innerHTML = playing ? '<i class="fas fa-pause"></i><span> Pause</span>' : '<i class="fas fa-play"></i><span> Play</span>';
    btn.play.setAttribute('aria-label', playing ? 'Pause tutorial' : 'Play tutorial');
  }
  function paintCaption() {
    var s = STEPS[idx];
    caption.no.textContent = idx + 1; caption.title.textContent = s.title; caption.text.textContent = s.text;
  }

  async function runStep(i, auto) {
    var my = ++token;
    idx = i; finished = false; var s = STEPS[i];
    playing = !!auto; paintCaption(); paintControls();
    setState(s.setup); showSco(false); render(false);
    if (reduce) {                                              // no motion: show the result of the step straight away
      cursor.classList.remove('on');
      s.acts.forEach(function (a) { a(); });
      render(false); if (manualSubmit && i === STEPS.length - 1) showSco(false);
      playing = false; paintControls(); return;
    }
    moveTo(null); await sleep(250); if (my !== token) return;
    moveTo(s.target, true); await sleep(60); if (my !== token) return;
    await sleep(1100); if (my !== token) return;               // let the viewer read the caption first
    for (var k = 0; k < s.acts.length; k++) {
      var tgt = k === 0 ? s.target : (s.cursorAfter && s.cursorAfter[k]) || null;
      if (k > 0 && tgt) { moveTo(tgt); await sleep(1100); if (my !== token) return; }
      else if (k > 0) { await sleep(900); if (my !== token) return; }
      press(tgt); await sleep(260); if (my !== token) return;
      s.acts[k]();
      await sleep(k === 0 ? 1500 : 1100); if (my !== token) return;
    }
    cursor.classList.remove('on');
    if (auto && i < STEPS.length - 1) { await sleep(900); if (my !== token) return; runStep(i + 1, true); }
    else { playing = false; finished = i === STEPS.length - 1; paintControls(); }
  }

  function stop() { token++; playing = false; paintControls(); }
  btn.next.addEventListener('click', function () { wantAuto = false; runStep(Math.min(idx + 1, STEPS.length - 1), false); });
  btn.prev.addEventListener('click', function () { wantAuto = false; runStep(Math.max(idx - 1, 0), false); });
  btn.replay.addEventListener('click', function () { wantAuto = !reduce; runStep(0, !reduce); });
  btn.play.addEventListener('click', function () {
    if (playing) { wantAuto = false; stop(); return; }
    wantAuto = true; runStep(finished ? 0 : idx, true);
  });
  dots.forEach(function (d, i) { d.addEventListener('click', function () { wantAuto = false; runStep(i, false); }); });

  // start when the section scrolls into view; pause when it leaves or the tab is hidden
  var started = false;
  paintCaption(); setState(STEPS[0].setup); render(false); paintControls();
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && e.intersectionRatio >= 0.35) {
          if (!started) { started = true; if (!reduce) runStep(0, true); }
          else if (wantAuto && !playing && !finished) { runStep(idx, true); }
        } else if (started && playing) { stop(); }
      });
    }, { threshold: [0, 0.35, 0.6] }).observe(root);
  } else if (!reduce) { started = true; runStep(0, true); }
  document.addEventListener('visibilitychange', function () { if (document.hidden && playing) stop(); });
  window.addEventListener('resize', function () { if (!playing) cursor.classList.remove('on'); });
})();
