/* MARKET FORGE - CHART CORE. One renderer, two consumers.
   ==========================================================================
   The app (static/app.js, Overview tab) and the panel kit (static/panel-kit.js,
   inside panel iframes) both draw price charts. They used to have SEPARATE
   implementations of the same axis/gridline/crosshair maths, and the crosshair
   carried a comment reading "must match candles()" - a design smell admitting
   itself. Change the gutter width in one and the crosshair silently pointed at
   the wrong bar in the other.

   Now there is one function. It returns the SVG *and the geometry it used*, so
   a caller can map a pointer position back to a bar without re-deriving
   anything. That is the whole point: the geometry is data, not a convention
   two files agree to honour.

   Colours are CSS variables, so a chart follows whatever skin is active.
   Bars arrive in DOLLARS (that is what /api/bot/bars returns; the radar rows
   are cents - do not confuse them).

   MFChart.render(bars, opts) -> { svg, geom }
   MFChart.xToIndex(geom, clientX, svgRect) -> bar index under the pointer
   MFChart.attachCrosshair(svgEl, bars, geom, onHover)
   ========================================================================== */
(function (root) {
  'use strict';

  const fmtP = (p) => p == null || isNaN(p) ? '--'
    : p >= 1000 ? p.toFixed(0) : p >= 100 ? p.toFixed(1) : p.toFixed(2);

  /** Render a price chart. Returns the markup AND the geometry used to place it. */
  function render(bars, opts = {}) {
    const o = Object.assign({
      w: 880, h: 340, type: 'candle', volume: true,
      priceAxis: true, lastPrice: true, dateLabels: true,
    }, opts);
    if (!bars || !bars.length) return { svg: '<div class="dim">no bars</div>', geom: null };

    const PR = o.priceAxis ? 56 : 6;          // right gutter for the price axis
    const VH = o.volume ? 44 : 0, GAP = o.volume ? 14 : 0;
    const CH = o.h - VH - GAP - 18;           // plot area height
    const n = bars.length;
    const lo = Math.min(...bars.map(b => b.l ?? b.c));
    const hi = Math.max(...bars.map(b => b.h ?? b.c));
    const rng = (hi - lo) || 1;
    const cw = (o.w - PR - 6) / n;            // column width
    const bw = Math.max(2, Math.min(cw - 3, 26));
    const maxV = Math.max(...bars.map(b => b.v || 0), 1);
    const y = (p) => 6 + CH * (1 - (p - lo) / rng);
    // Geometry is returned so callers never re-derive it. x(i) is the CENTRE of
    // a column, which is what a crosshair should snap to.
    const geom = { w: o.w, h: o.h, PR, CH, n, lo, hi, cw, bw,
                   x: (i) => 3 + i * cw + bw / 2, y };

    let s = `<svg viewBox="0 0 ${o.w} ${o.h}" preserveAspectRatio="none" class="mf-svg">`;

    if (o.priceAxis) {
      for (let i = 0; i <= 4; i++) {
        const p = hi - (rng * i) / 4, gy = y(p);
        s += `<line x1="0" y1="${gy}" x2="${o.w - PR}" y2="${gy}" stroke="var(--border)" opacity=".6"/>`;
        s += `<text x="${o.w - PR + 6}" y="${gy + 4}" fill="var(--dim)" font-size="11.5">${fmtP(p)}</text>`;
      }
    }

    if (o.type === 'line') {
      const pts = bars.map((b, i) => `${geom.x(i)},${y(b.c)}`).join(' ');
      const rising = bars[n - 1].c >= bars[0].c;
      s += `<polyline points="${pts}" fill="none" stroke="${rising ? 'var(--success)' : 'var(--danger)'}"` +
           ` stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    }

    bars.forEach((b, i) => {
      const x = 3 + i * cw, cx = geom.x(i);
      const up = b.c >= (b.o ?? b.c);
      const col = up ? 'var(--success)' : 'var(--danger)';
      if (o.type !== 'line' && b.o != null) {
        s += `<line x1="${cx}" y1="${y(b.h)}" x2="${cx}" y2="${y(b.l)}" stroke="${col}" stroke-width="1.2"/>`;
        const top = y(Math.max(b.o, b.c));
        const bh = Math.max(1.5, Math.abs(y(b.o) - y(b.c)));
        s += `<rect x="${x}" y="${top}" width="${bw}" height="${bh}" rx="1" fill="${col}" opacity="${up ? .95 : .85}"/>`;
      }
      if (o.volume && b.v) {
        const vh = Math.max(1, (b.v / maxV) * VH);
        s += `<rect x="${x}" y="${o.h - 16 - vh}" width="${bw}" height="${vh}" fill="${col}" opacity=".35"/>`;
      }
    });

    if (o.lastPrice) {
      const ly = y(bars[n - 1].c);
      s += `<line x1="0" y1="${ly}" x2="${o.w - PR}" y2="${ly}" stroke="var(--primary)"` +
           ` stroke-dasharray="5 4" opacity=".8"/>`;
      if (o.priceAxis) {
        s += `<rect x="${o.w - PR + 2}" y="${ly - 10}" width="${PR - 4}" height="19" rx="5" fill="var(--primary)"/>`;
        s += `<text x="${o.w - PR / 2}" y="${ly + 4}" text-anchor="middle" fill="#08111f"` +
             ` font-size="11.5" font-weight="700">${fmtP(bars[n - 1].c)}</text>`;
      }
    }

    if (o.dateLabels) {
      const step = Math.max(1, Math.round(n / 6));
      for (let i = 0; i < n; i += step) {
        s += `<text x="${3 + i * cw}" y="${o.h - 3}" fill="var(--dim)" font-size="10.5">` +
             `${String(bars[i].t || '').slice(5)}</text>`;
      }
    }

    // crosshair line lives in the markup so callers only have to move it
    s += `<line class="xhair" x1="0" y1="0" x2="0" y2="${o.h - 16}" stroke="var(--dim)"` +
         ` stroke-dasharray="3 3" opacity="0"/>`;
    return { svg: s + '</svg>', geom };
  }

  /** Which bar is under this pointer? Uses the geometry render() handed back. */
  function xToIndex(geom, clientX, rect) {
    if (!geom || !rect || !rect.width) return 0;
    const px = ((clientX - rect.left) / rect.width) * geom.w;
    const i = Math.floor((px - 3) / geom.cw);
    return Math.max(0, Math.min(geom.n - 1, i));
  }

  /** Wire the crosshair. onHover(bar, index) gets called as the pointer moves. */
  function attachCrosshair(svgEl, bars, geom, onHover) {
    if (!svgEl || !geom) return;
    const line = svgEl.querySelector('.xhair');
    svgEl.addEventListener('pointermove', (e) => {
      const i = xToIndex(geom, e.clientX, svgEl.getBoundingClientRect());
      const x = geom.x(i);
      if (line) { line.setAttribute('x1', x); line.setAttribute('x2', x); line.setAttribute('opacity', '.65'); }
      if (onHover) onHover(bars[i], i);
    });
    svgEl.addEventListener('pointerleave', () => {
      if (line) line.setAttribute('opacity', '0');
      if (onHover) onHover(null, -1);
    });
  }

  root.MFChart = { render, xToIndex, attachCrosshair, fmtP };
})(typeof window !== 'undefined' ? window : this);
