/* static/shared/chart-export.js
   Client-side PNG export for Chart.js charts. Nothing is sent to the server: callers render each chart on an offscreen canvas
   with a light palette (ChartExport.offscreen), then ChartExport.compose wraps one or more of them in a titled, footed page
   and ChartExport.download saves it.

   ChartExport.offscreen(Chart, config, width, height) -> { chart, canvas, w, h, dispose() }
   ChartExport.compose(parts, title, subtitle, width)  parts: [{ canvas, w, h, caption? }]  -> canvas
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

  function compose(parts, title, subtitle, width) {
    var pad = 28, headH = 78, footH = 36;
    var inner = width - pad * 2;
    var bodyH = parts.reduce(function (a, p) { return a + p.h * inner / p.w + (p.caption ? 30 : 0) + 14; }, 0);
    var c = document.createElement('canvas');
    c.width = width * SCALE; c.height = Math.ceil((headH + bodyH + footH) * SCALE);
    var g = c.getContext('2d');
    g.scale(SCALE, SCALE);
    g.fillStyle = '#ffffff'; g.fillRect(0, 0, width, headH + bodyH + footH);
    g.textBaseline = 'alphabetic';
    g.fillStyle = '#1c1b18'; g.font = '700 22px system-ui, sans-serif';
    g.fillText(title, pad, 38);
    g.fillStyle = '#4b4a45'; g.font = '400 13px system-ui, sans-serif';
    g.fillText(subtitle, pad, 60, inner);
    var y = headH;
    parts.forEach(function (p) {
      if (p.caption) { g.fillStyle = '#1c1b18'; g.font = '600 15px system-ui, sans-serif'; g.fillText(p.caption, pad, y + 18); y += 30; }
      var h = p.h * inner / p.w;
      g.drawImage(p.canvas, pad, y, inner, h);
      y += h + 14;
    });
    g.fillStyle = '#76756e'; g.font = '400 11px system-ui, sans-serif';
    g.fillText('Smart AI Exam Portal · generated ' + new Date().toLocaleString(), pad, headH + bodyH + footH - 14);
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
    slug: function (s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); },
    stamp: function () { return new Date().toISOString().slice(0, 10); }
  };
})(window);
