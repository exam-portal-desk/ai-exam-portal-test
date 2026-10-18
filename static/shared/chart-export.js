/* static/shared/chart-export.js
   Client-side PNG export for Chart.js charts. Nothing is sent to the server: callers render each chart on an offscreen canvas
   with a light palette (ChartExport.offscreen), then ChartExport.compose wraps one or more of them in a titled, footed page
   and ChartExport.download saves it.

   ChartExport.offscreen(Chart, config, width, height) -> { chart, canvas, w, h, dispose() }
   ChartExport.compose(parts, title, subtitle, width, extra?)  parts: [{ canvas, w, h, caption? }]  -> canvas
                                                      extra: { filters: [{label, value}], cards: [{label, value, sub}] }
   ChartExport.ready()                                 resolves once web fonts are loaded (await it before composing)
   ChartExport.download(canvas, filename) -> Promise
   ChartExport.palette                                 colours that read well on white
   ChartExport.slug(text) / ChartExport.stamp()        file-name helpers */
(function (global) {
  var SCALE = 2;

  var palette = { text: '#1c1b18', tick: '#4b4a45', grid: '#e4e2da', tipBg: '#ffffff', tipText: '#1c1b18' };

  function offscreen(Chart, config, width, height) {
    var holder = document.createElement('div');
    holder.style.cssText = 'position:fixed;left:-10000px;top:0;pointer-events:none';
    var cv = document.createElement('canvas');
    cv.width = width * SCALE; cv.height = height * SCALE;
    cv.style.width = width + 'px'; cv.style.height = height + 'px';
    holder.appendChild(cv);
    document.body.appendChild(holder);
    config.options = config.options || {};
    config.options.responsive = false;
    config.options.animation = false;
    config.options.devicePixelRatio = SCALE;
    var chart = new Chart(cv, config);
    chart.resize(width, height);
    return {
      chart: chart, canvas: chart.canvas, w: width, h: height,
      dispose: function () { try { chart.destroy(); } catch (e) {} holder.remove(); }
    };
  }

  /* Wraps text to a pixel width on a scratch context (long filter values must never be cut off). */
  function wrap(g, text, maxW) {
    var words = String(text).split(/\s+/), lines = [], line = '';
    words.forEach(function (w) {
      var test = line ? line + ' ' + w : w;
      if (g.measureText(test).width <= maxW || !line) { line = test; } else { lines.push(line); line = w; }
    });
    if (line) lines.push(line);
    return lines;
  }

  /* extra (optional): { filters: [{label, value}], cards: [{label, value, sub}] }  -> a "Filters applied" box and a grid of
     the dashboard's metric cards are drawn between the header and the charts. */
  function compose(parts, title, subtitle, width, extra) {
    extra = extra || {};
    var pad = 28, inner = width - pad * 2, footH = 36;
    var scratch = document.createElement('canvas').getContext('2d');

    scratch.font = '400 13px system-ui, sans-serif';
    var subLines = wrap(scratch, subtitle || '', inner);
    var headH = 52 + subLines.length * 18 + 8;

    var filters = extra.filters || [], filterRows = [], filterH = 0;
    if (filters.length) {
      scratch.font = '400 13px system-ui, sans-serif';
      filters.forEach(function (f) {
        var lines = wrap(scratch, f.value, inner - 40 - 150);
        filterRows.push({ label: f.label, lines: lines });
        filterH += lines.length * 18 + 6;
      });
      filterH += 44;
    }

    var cards = extra.cards || [], cols = Math.min(4, cards.length || 1), gap = 12, cardW = (inner - gap * (cols - 1)) / cols, cardH = 74;
    var cardRows = Math.ceil(cards.length / cols), cardsH = cards.length ? cardRows * (cardH + gap) + 6 : 0;

    var bodyH = parts.reduce(function (a, p) { return a + p.h * inner / p.w + (p.caption ? 30 : 0) + 14; }, 0);
    var total = headH + (filterH ? filterH + 12 : 0) + cardsH + bodyH + footH;
    var c = document.createElement('canvas');
    c.width = width * SCALE; c.height = Math.ceil(total * SCALE);
    var g = c.getContext('2d');
    g.scale(SCALE, SCALE);
    g.fillStyle = '#ffffff'; g.fillRect(0, 0, width, total);
    g.textBaseline = 'alphabetic';
    g.fillStyle = '#1c1b18'; g.font = '700 22px system-ui, sans-serif';
    g.fillText(title, pad, 38, inner);
    g.fillStyle = '#4b4a45'; g.font = '400 13px system-ui, sans-serif';
    subLines.forEach(function (l, i) { g.fillText(l, pad, 60 + i * 18); });
    var y = headH;

    if (filterH) {
      g.fillStyle = '#f6f5f0'; g.strokeStyle = '#e4e2da'; g.lineWidth = 1;
      g.beginPath(); g.rect(pad, y, inner, filterH); g.fill(); g.stroke();
      g.fillStyle = '#76756e'; g.font = '700 11px system-ui, sans-serif';
      g.fillText('FILTERS APPLIED', pad + 14, y + 22);
      var fy = y + 44;
      filterRows.forEach(function (f) {
        g.fillStyle = '#4b4a45'; g.font = '600 13px system-ui, sans-serif'; g.fillText(f.label, pad + 14, fy);
        g.fillStyle = '#1c1b18'; g.font = '400 13px system-ui, sans-serif';
        f.lines.forEach(function (l, i) { g.fillText(l, pad + 14 + 150, fy + i * 18); });
        fy += f.lines.length * 18 + 6;
      });
      y += filterH + 12;
    }

    cards.forEach(function (cd, i) {
      var cx = pad + (i % cols) * (cardW + gap), cy = y + Math.floor(i / cols) * (cardH + gap);
      g.fillStyle = '#ffffff'; g.strokeStyle = '#e4e2da'; g.beginPath(); g.rect(cx, cy, cardW, cardH); g.fill(); g.stroke();
      g.fillStyle = '#3b5bdb'; g.fillRect(cx, cy, cardW, 3);
      g.fillStyle = '#76756e'; g.font = '700 10px system-ui, sans-serif'; g.fillText(String(cd.label).toUpperCase(), cx + 12, cy + 22, cardW - 24);
      g.fillStyle = '#1c1b18'; g.font = '800 22px system-ui, sans-serif'; g.fillText(String(cd.value), cx + 12, cy + 48, cardW - 24);
      g.fillStyle = '#4b4a45'; g.font = '400 11px system-ui, sans-serif'; g.fillText(String(cd.sub || ''), cx + 12, cy + 65, cardW - 24);
    });
    if (cards.length) y += cardsH;

    parts.forEach(function (p) {
      if (p.caption) { g.fillStyle = '#1c1b18'; g.font = '600 15px system-ui, sans-serif'; g.fillText(p.caption, pad, y + 18); y += 30; }
      var h = p.h * inner / p.w;
      g.drawImage(p.canvas, pad, y, inner, h);
      y += h + 14;
    });
    g.fillStyle = '#76756e'; g.font = '400 11px system-ui, sans-serif';
    g.fillText('Smart AI Exam Portal · generated ' + new Date().toLocaleString(), pad, total - 14);
    return c;
  }

  function download(canvas, filename) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (!blob) { reject(new Error('Could not create the image.')); return; }
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url; a.download = filename;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        resolve();
      }, 'image/png');
    });
  }

  global.ChartExport = {
    palette: palette, offscreen: offscreen, compose: compose, download: download,
    ready: function () { return (document.fonts && document.fonts.ready) ? document.fonts.ready.catch(function () {}) : Promise.resolve(); },
    slug: function (s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); },
    stamp: function () { return new Date().toISOString().slice(0, 10); }
  };
})(window);
