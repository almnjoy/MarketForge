/* MARKET FORGE PANEL KIT
   Drop this into any panels/*.html and get interactive, themed, live components
   without writing chart maths:

     <link rel="stylesheet" href="/static/panel.css">
     <script src="/static/panel-kit.js"></script>
     <div id="c"></div>
     <script>MF.quote('#c', 'AAPL')</script>

   Everything reads the SAME endpoints the dashboard uses, so a panel is never
   stale relative to the app. All colors come from CSS variables, so a panel
   automatically follows whatever theme the user picked.

   MF.chart(el, {symbol, range, type})  interactive chart + range switcher
   MF.quote(el, symbol)                 chart + the full stat grid (last/open/
                                        day range/prev close/volume/52w)
   MF.stats(el, symbol)                 just the stat grid
   MF.table(el, rows, cols)             a themed table
   MF.money(n) / MF.pct(n) / MF.num(n)  formatters that match the app
*/
(function (global) {
  'use strict';

  const RANGES = { '1M': 22, '3M': 66, '6M': 132, '1Y': 252, 'ALL': 2000 };

  const money = (v, d = 2) => v == null || isNaN(v) ? '--'
    : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const num = (v) => v == null || isNaN(v) ? '--' : Number(v).toLocaleString();
  const pct = (v, d = 2) => v == null || isNaN(v) ? '--'
    : (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
  const el = (sel) => typeof sel === 'string' ? document.querySelector(sel) : sel;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  async function bars(symbol, limit) {
    const r = await fetch(`/api/bot/bars?symbol=${encodeURIComponent(symbol)}&limit=${limit}`);
    if (!r.ok) throw new Error('bars ' + r.status);
    const d = await r.json();
    // /api/bot/bars returns DOLLARS (unlike the radar rows, which are cents).
    // Verified against the live endpoint - do not "helpfully" divide by 100 here.
    return (Array.isArray(d) ? d : d.bars || []).map(b => ({
      t: String(b.t || '').slice(0, 10),
      o: +b.o, h: +b.h, l: +b.l, c: +b.c, v: b.v || 0,
    })).filter(b => b.c);
  }

  /* ---------- chart: line or candle, crosshair, hover readout ---------- */
  function draw(host, data, opt) {
    const W = host.clientWidth || 900, H = opt.height || 260;
    const PR = 62, PB = 20, iw = W - PR, ih = H - PB;
    if (!data.length) { host.innerHTML = '<div class="mf-empty">no bars</div>'; return; }
    const his = Math.max(...data.map(b => b.h)), los = Math.min(...data.map(b => b.l));
    const rng = (his - los) || 1, pad = rng * 0.08;
    const hi = his + pad, lo = los - pad, span = hi - lo;
    const x = i => (i / Math.max(1, data.length - 1)) * (iw - 8) + 4;
    const y = p => ih - ((p - lo) / span) * (ih - 10) - 5;
    const up = data[data.length - 1].c >= data[0].c;
    const stroke = up ? 'var(--gain, #4ade80)' : 'var(--loss, #f87171)';

    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" class="mf-svg">`;
    for (let i = 0; i <= 4; i++) {
      const p = hi - (span * i) / 4, gy = y(p);
      s += `<line x1="0" y1="${gy}" x2="${iw}" y2="${gy}" stroke="var(--border)" opacity=".55"/>`;
      s += `<text x="${iw + 6}" y="${gy + 4}" fill="var(--dim)" font-size="11">${money(p)}</text>`;
    }
    if (opt.type === 'candle') {
      const cw = iw / data.length, bw = Math.max(1, Math.min(9, cw * 0.62));
      data.forEach((b, i) => {
        const cx = x(i), c = b.c >= b.o ? 'var(--gain,#4ade80)' : 'var(--loss,#f87171)';
        s += `<line x1="${cx}" y1="${y(b.h)}" x2="${cx}" y2="${y(b.l)}" stroke="${c}"/>`;
        s += `<rect x="${cx - bw / 2}" y="${Math.min(y(b.o), y(b.c))}" width="${bw}" ` +
             `height="${Math.max(1, Math.abs(y(b.o) - y(b.c)))}" fill="${c}"/>`;
      });
    } else {
      const pts = data.map((b, i) => `${x(i)},${y(b.c)}`).join(' ');
      s += `<polyline points="${pts}" fill="none" stroke="${stroke}" stroke-width="2"
             stroke-linejoin="round" stroke-linecap="round"/>`;
    }
    const step = Math.max(1, Math.round(data.length / 6));
    for (let i = 0; i < data.length; i += step)
      s += `<text x="${x(i)}" y="${H - 4}" fill="var(--dim)" font-size="10.5">${data[i].t.slice(5)}</text>`;
    s += `<line class="mf-cross" x1="0" y1="0" x2="0" y2="${ih}" stroke="var(--dim)" stroke-dasharray="3 3" opacity="0"/>`;
    s += '</svg>';
    host.innerHTML = s + `<div class="mf-read"><b>${data.length} bars</b> · ${opt.range || ''}` +
      `<span class="mf-hover"></span><span class="mf-lohi">lo ${money(los)} · hi ${money(his)}</span></div>`;

    // crosshair + hover readout. Pointer maths mirrors the x() scale above.
    const svg = host.querySelector('svg'), cross = host.querySelector('.mf-cross'),
          read = host.querySelector('.mf-hover');
    svg.addEventListener('pointermove', (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(data.length - 1, Math.round(((px - 4) / (iw - 8)) * (data.length - 1))));
      const b = data[i];
      cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('opacity', '.7');
      read.innerHTML = ` · <b>${b.t}</b> O ${money(b.o)} H ${money(b.h)} L ${money(b.l)} ` +
        `<b>C ${money(b.c)}</b> V ${num(b.v)}`;
    });
    svg.addEventListener('pointerleave', () => { cross.setAttribute('opacity', '0'); read.textContent = ''; });
  }

  async function chart(target, opt = {}) {
    const host = el(target); if (!host) return;
    const o = Object.assign({ symbol: 'SPY', range: '3M', type: 'line', height: 260, ranges: true }, opt);
    host.classList.add('mf-chart');
    host.innerHTML = '<div class="mf-empty">loading…</div>';
    let head = '';
    if (o.ranges) head = `<div class="mf-ranges">` +
      Object.keys(RANGES).map(r => `<button data-r="${r}"${r === o.range ? ' class="on"' : ''}>${r}</button>`).join('') +
      `</div>`;
    const wrap = document.createElement('div');
    wrap.innerHTML = head + '<div class="mf-plot"></div>';
    host.innerHTML = ''; host.appendChild(wrap);
    const plot = wrap.querySelector('.mf-plot');
    const cache = {};                 // range -> bars, so a redraw never refetches
    let curRange = o.range, lastW = 0, busy = false;

    const render = async (range, quiet = false) => {
      if (busy) return;               // overlapping renders fight over innerHTML
      busy = true; curRange = range;
      try {
        if (!cache[range]) {
          if (!quiet) plot.innerHTML = '<div class="mf-empty">loading…</div>';
          cache[range] = await bars(o.symbol, RANGES[range] || 66);
        }
        draw(plot, cache[range], { ...o, range });
        lastW = host.clientWidth;     // remember the width we drew AT
      } catch (e) {
        plot.innerHTML = `<div class="mf-empty">chart error: ${esc(e.message)}</div>`;
      } finally { busy = false; }
    };

    wrap.querySelectorAll('.mf-ranges button').forEach(b => b.onclick = () => {
      wrap.querySelectorAll('.mf-ranges button').forEach(x => x.classList.toggle('on', x === b));
      render(b.dataset.r);
    });
    await render(o.range);

    // Redraw on resize so a maximized panel uses the space - but ONLY when the
    // width really changed. Drawing changes the element's height, which fires the
    // observer again; without this guard that is an infinite render loop that
    // pins the panel on "loading..." and re-fetches bars several times a second.
    let t;
    new ResizeObserver(() => {
      clearTimeout(t);
      t = setTimeout(() => {
        const w = host.clientWidth;
        if (!w || Math.abs(w - lastW) < 12) return;   // ignore our own echo
        render(curRange, true);                        // quiet: no loading flash
      }, 200);
    }).observe(host);
  }

  /* ---------- stat grid: the numbers people actually look up ---------- */
  async function stats(target, symbol) {
    const host = el(target); if (!host) return;
    host.classList.add('mf-stats');
    host.innerHTML = '<div class="mf-empty">loading…</div>';
    try {
      const d = await bars(symbol, 260);
      if (!d.length) { host.innerHTML = '<div class="mf-empty">no data</div>'; return; }
      const last = d[d.length - 1], prev = d[d.length - 2] || last;
      const chg = last.c - prev.c, chgPct = (chg / prev.c) * 100;
      const yr = d.slice(-252);
      const hi52 = Math.max(...yr.map(b => b.h)), lo52 = Math.min(...yr.map(b => b.l));
      const cells = [
        ['LAST', money(last.c)], ['OPEN', money(last.o)],
        ['DAY LOW', money(last.l)], ['DAY HIGH', money(last.h)],
        ['PREV CLOSE', money(prev.c)], ['VOLUME', num(last.v)],
        ['CHANGE', pct(chgPct), chg >= 0 ? 'up' : 'down'],
        ['52W LOW', money(lo52)], ['52W HIGH', money(hi52)],
        ['BARS', num(d.length)],
      ];
      host.innerHTML = cells.map(([l, v, c]) =>
        `<div class="mf-cell"><div class="mf-l">${l}</div><div class="mf-v ${c || ''}">${v}</div></div>`).join('');
    } catch (e) { host.innerHTML = `<div class="mf-empty">stats error: ${esc(e.message)}</div>`; }
  }

  /* ---------- quote: the whole card in one call ---------- */
  async function quote(target, symbol, opt = {}) {
    const host = el(target); if (!host) return;
    host.classList.add('mf-quote');
    host.innerHTML = `<div class="mf-head"><span class="mf-sym">${esc(symbol)}</span>
        <span class="mf-px"></span></div><div class="mf-c"></div><div class="mf-s"></div>`;
    await chart(host.querySelector('.mf-c'), Object.assign({ symbol }, opt));
    await stats(host.querySelector('.mf-s'), symbol);
    try {
      const d = await bars(symbol, 3), last = d[d.length - 1], prev = d[d.length - 2] || last;
      const chg = last.c - prev.c, p = (chg / prev.c) * 100;
      host.querySelector('.mf-px').innerHTML =
        `${money(last.c)} <b class="${chg >= 0 ? 'up' : 'down'}">${chg >= 0 ? '+' : ''}${money(chg)} (${pct(p)})</b>`;
    } catch {}
  }

  function table(target, rows, cols) {
    const host = el(target); if (!host) return;
    host.innerHTML = '<table class="mf-table"><tr>' + cols.map(c => `<th>${esc(c.label || c)}</th>`).join('') +
      '</tr>' + rows.map(r => '<tr>' + cols.map(c => {
        const k = c.key || c, v = r[k];
        return `<td class="${c.cls ? c.cls(v, r) : ''}">${c.fmt ? c.fmt(v, r) : esc(v)}</td>`;
      }).join('') + '</tr>').join('') + '</table>';
  }

  global.MF = { chart, quote, stats, table, bars, money, pct, num, RANGES };
})(window);
