/* STOCKS//LOCAL frontend. Vanilla JS, no build step - CC edits this live.
   Data flows: /api/bot/* (the bot engine, embedded or remote), /api/panels + /api/panel
   (Workbench file bus), /api/chat* (copilot bus). */
'use strict';
const $ = (s) => document.querySelector(s);
const fmt$ = (v, d = 2) => v == null ? '--' : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const cls = (v) => v > 0 ? 'up' : v < 0 ? 'down' : '';
const arrow = (v) => v > 0 ? '▲ ' : v < 0 ? '▼ ' : '';  // GridPulse rule: never color-only
// magnitude shade scale for % chips: mild < 3%, solid 3-10%, loud > 10%
const pctScale = (v) => { const a = Math.abs(v || 0); const t = a >= 10 ? 3 : a >= 3 ? 2 : 1; return (v >= 0 ? 'g' : 'r') + t; };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const J = async (url, opt) => { const r = await fetch(url, opt); return r.json(); };

/* ---------- theme ----------
   Skins are pure token blocks in style.css, switched by [data-theme] on <html>.
   Order of precedence: this machine's saved pick -> config.json `theme` -> forge.
   Applied BEFORE first paint (see the inline bootstrap in index.html) so there is
   no flash of the wrong skin. Panels are told the theme too, since they render
   inside iframes that do not inherit the attribute. */
const THEMES = [
  ['forge', 'forge'], ['vault', 'vault'], ['tape', 'tape'], ['broadsheet', 'daylight'],
];
function applyTheme(t, persist = true) {
  if (!THEMES.some(([k]) => k === t)) t = 'forge';
  document.documentElement.dataset.theme = t;
  if (persist) localStorage.setItem('mfTheme', t);
  const sel = $('#themePick'); if (sel) sel.value = t;
  // iframes are separate documents: hand them the theme so panels match the app
  document.querySelectorAll('#workbench iframe').forEach((f) => {
    try { f.contentDocument.documentElement.dataset.theme = t; } catch {}
  });
}
function initTheme(cfgTheme) {
  const sel = $('#themePick');
  if (sel && !sel.options.length) {
    sel.innerHTML = THEMES.map(([k, label]) => `<option value="${k}">${label}</option>`).join('');
    sel.onchange = () => applyTheme(sel.value);
  }
  const saved = localStorage.getItem('mfTheme');
  applyTheme(saved || cfgTheme || 'forge', false);
}
initTheme();

/* ---------- tabs ---------- */
document.querySelectorAll('#tabs button').forEach((b) => b.onclick = () => {
  document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('on', x === b));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('on', v.id === 'view-' + b.dataset.tab));
  // admin is a snapshot, not a live poll - refresh it when you open the tab
  if (b.dataset.tab === 'admin') loadAdmin();
});

/* admin sub-tabs: the whole inventory on one screen was a wall */
document.querySelectorAll('#subtabs button').forEach((b) => b.onclick = () => {
  document.querySelectorAll('#subtabs button').forEach((x) => x.classList.toggle('on', x === b));
  document.querySelectorAll('.subview').forEach((v) => v.classList.toggle('on', v.id === 'sub-' + b.dataset.sub));
});
/* jump straight to an admin sub-tab (used by the Ctrl+K palette) */
function subTo(sub) {
  const t = document.querySelector('#tabs button[data-tab="admin"]'); if (t) t.click();
  const b = document.querySelector(`#subtabs button[data-sub="${sub}"]`); if (b) b.click();
}

/* Esc closes a maximized panel (and only that - it must not steal Esc otherwise) */
addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const m = document.querySelector('.wb-card.maximized');
  if (m) { m.classList.remove('maximized'); document.body.classList.remove('has-max');
           const b = m.querySelector('.wb-max'); if (b) b.textContent = '⤢'; }
});

/* ---------- clocks: local + NYSE session state (no holiday calendar - close enough) ---------- */
setInterval(() => { $('#clock').textContent = new Date().toLocaleTimeString(); }, 1000);
function marketClock() {
  const et = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const day = et.getDay(), m = et.getHours() * 60 + et.getMinutes();
  const OPEN = 570, CLOSE = 960, PRE = 240, AFT = 1200;
  const left = (until) => { const d = until - m; return `${Math.floor(d / 60)}h ${String(d % 60).padStart(2, '0')}m`; };
  const el = $('#mktClock');
  if (day === 0 || day === 6) { el.textContent = 'MARKETS CLOSED · weekend'; el.className = 'chip mkt'; }
  else if (m >= OPEN && m < CLOSE) { el.textContent = `MARKET OPEN · closes ${left(CLOSE)}`; el.className = 'chip mkt open'; }
  else if (m >= PRE && m < OPEN) { el.textContent = `PRE-MARKET · opens ${left(OPEN)}`; el.className = 'chip mkt pre'; }
  else if (m >= CLOSE && m < AFT) { el.textContent = 'AFTER-HOURS · till 8p ET'; el.className = 'chip mkt pre'; }
  else { el.textContent = 'MARKETS CLOSED'; el.className = 'chip mkt'; }
}
marketClock(); setInterval(marketClock, 30000);

/* ---------- candlestick SVG (no libs; gridlines + price axis + volume) ---------- */
function candles(bars, w = 880, h = 340, type = 'candle') {
  if (!bars?.length) return '<div class="dim">no bars</div>';
  const PR = 56;                      // right gutter for the price axis
  const VH = 44, GAP = 14;            // volume strip height + gap
  const CH = h - VH - GAP - 18;       // candle area height
  const n = bars.length, lo = Math.min(...bars.map(b => b.l)), hi = Math.max(...bars.map(b => b.h));
  const rng = (hi - lo) || 1, cw = (w - PR - 6) / n, bw = Math.max(2, cw - 3);
  const maxV = Math.max(...bars.map(b => b.v || 0), 1);
  const y = (p) => 6 + CH * (1 - (p - lo) / rng);
  const fmtP = (p) => p >= 1000 ? p.toFixed(0) : p >= 100 ? p.toFixed(1) : p.toFixed(2);
  let s = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" font-family="Inter,system-ui,sans-serif">`;
  // horizontal gridlines + price labels (5 ticks)
  for (let i = 0; i <= 4; i++) {
    const p = hi - (rng * i) / 4, gy = y(p);
    s += `<line x1="0" y1="${gy}" x2="${w - PR}" y2="${gy}" stroke="var(--border)" stroke-width="1" opacity=".6"/>`;
    s += `<text x="${w - PR + 6}" y="${gy + 4}" fill="var(--dim)" font-size="11.5">${fmtP(p)}</text>`;
  }
  // line mode: one stroke over the closes, tinted by the period's direction
  if (type === 'line') {
    const pts = bars.map((b, i) => `${3 + i * cw + bw / 2},${y(b.c)}`).join(' ');
    const rising = bars.at(-1).c >= bars[0].c;
    s += `<polyline points="${pts}" fill="none" stroke="${rising ? 'var(--success)' : 'var(--danger)'}" ` +
         `stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }
  // candles + volume
  bars.forEach((b, i) => {
    const x = 3 + i * cw, cx = x + bw / 2, up = b.c >= b.o, col = up ? 'var(--success)' : 'var(--danger)';
    if (type !== 'line') {
      s += `<line x1="${cx}" y1="${y(b.h)}" x2="${cx}" y2="${y(b.l)}" stroke="${col}" stroke-width="1.2"/>`;
      const top = y(Math.max(b.o, b.c)), bh2 = Math.max(1.5, Math.abs(y(b.o) - y(b.c)));
      s += `<rect x="${x}" y="${top}" width="${bw}" height="${bh2}" rx="1" fill="${col}" opacity="${up ? .95 : .85}">` +
           `<title>${b.t}  O ${fmtP(b.o)}  H ${fmtP(b.h)}  L ${fmtP(b.l)}  C ${fmtP(b.c)}${b.v ? '  V ' + (b.v / 1e6).toFixed(1) + 'M' : ''}</title></rect>`;
    }
    if (b.v) {
      const vh = Math.max(1, (b.v / maxV) * VH);
      s += `<rect x="${x}" y="${h - 16 - vh}" width="${bw}" height="${vh}" fill="${col}" opacity=".35"/>`;
    }
  });
  // last-price dashed line + tag
  const last = bars[n - 1], ly = y(last.c);
  s += `<line x1="0" y1="${ly}" x2="${w - PR}" y2="${ly}" stroke="var(--primary)" stroke-width="1" stroke-dasharray="5 4" opacity=".8"/>`;
  s += `<rect x="${w - PR + 2}" y="${ly - 10}" width="${PR - 4}" height="19" rx="5" fill="var(--primary)"/>`;
  s += `<text x="${w - PR / 2}" y="${ly + 4}" text-anchor="middle" fill="#08111f" font-size="11.5" font-weight="700">${fmtP(last.c)}</text>`;
  // x date labels (~6)
  const step = Math.max(1, Math.round(n / 6));
  for (let i = 0; i < n; i += step) {
    s += `<text x="${3 + i * cw}" y="${h - 3}" fill="var(--dim)" font-size="10.5">${(bars[i].t || '').slice(5)}</text>`;
  }
  return s + '</svg>';
}

/* ---------- overview ---------- */
let chartSym = 'SPY';
/* Interactive chart: range switcher, candle/line toggle, and a crosshair that
   reports the real OHLC of the bar under the pointer. The maths mirrors candles()
   exactly - if you change padding or the price-axis width there, change it here. */
const CHART_RANGES = { '1M': 22, '3M': 90, '6M': 132, '1Y': 252 };
let chartRange = '3M', chartType = 'candle', chartBars = [];

function wireChartHover() {
  const box = $('#bigChart'), svg = box.querySelector('svg'), read = $('#chartHover');
  if (!svg || !chartBars.length) return;
  const W = 880, PR = 56, n = chartBars.length, cw = (W - PR - 6) / n;  // must match candles()
  let cross = svg.querySelector('.xhair');
  if (!cross) {
    cross = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    cross.setAttribute('class', 'xhair'); cross.setAttribute('y1', 0);
    cross.setAttribute('y2', 340); cross.setAttribute('stroke', 'var(--dim)');
    cross.setAttribute('stroke-dasharray', '3 3'); cross.setAttribute('opacity', '0');
    svg.appendChild(cross);
  }
  const move = (e) => {
    const r = svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.floor((px - 3) / cw)));
    const b = chartBars[i], x = 3 + i * cw + cw / 2;
    cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('opacity', '.65');
    const up = b.c >= b.o;
    read.innerHTML = `<b>${esc(b.t || '')}</b> · O ${fmt$(b.o)} H ${fmt$(b.h)} ` +
      `L ${fmt$(b.l)} <b class="${up ? 'up' : 'down'}">C ${fmt$(b.c)}</b>` +
      (b.v ? ` · vol ${Number(b.v).toLocaleString()}` : '');
    read.classList.add('on');
  };
  svg.onpointermove = move;
  svg.onpointerleave = () => { cross.setAttribute('opacity', '0'); read.classList.remove('on'); };
}

async function loadChart() {
  const sym = chartSym;
  $('#bigChart').innerHTML = '<span class="dim">loading ' + esc(sym) + '...</span>';
  try {
    const lim = CHART_RANGES[chartRange] || 90;
    const d = await J(`/api/bot/bars?symbol=${sym}&limit=${lim}`);
    if (d.error) throw new Error(d.error);
    chartBars = d.bars || [];
    const last = chartBars.at(-1), prev = chartBars.at(-2);
    const pct = prev ? ((last.c - prev.c) / prev.c * 100) : 0;
    $('#chartMeta').innerHTML = `${fmt$(last.c)} <span class="${cls(pct)}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span> · ${chartBars.length} bars`;
    $('#bigChart').innerHTML = candles(chartBars, 880, 340, chartType);
    wireChartHover();
    const nw = await J(`/api/bot/news?symbol=${sym}&limit=6`);
    $('#chartNews').innerHTML = (nw.news || []).map(n =>
      `<a href="${esc(n.url)}" target="_blank">▸ [${esc(n.source || 'news')}] ${esc(n.headline)}</a>`).join('') || '';
  } catch (e) { $('#bigChart').innerHTML = `<span class="dim">chart error: ${esc(e.message)}</span>`; }
}
$('#chartSym').addEventListener('change', () => { chartSym = $('#chartSym').value.toUpperCase().trim() || 'SPY'; loadChart(); });
$('#chartRanges').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.r) {
    chartRange = b.dataset.r;
    $('#chartRanges').querySelectorAll('[data-r]').forEach(x => x.classList.toggle('on', x === b));
  } else if (b.dataset.t) {
    chartType = chartType === 'candle' ? 'line' : 'candle';
    b.textContent = chartType === 'candle' ? '▮' : '∿';
  }
  loadChart();
});

async function loadOverview() {
  try {
    const [s, pos, ords] = await Promise.all([
      J('/api/bot/status'), J('/api/bot/positions').catch(() => []), J('/api/bot/orders').catch(() => [])]);
    if (s.error) throw new Error(s.error);
    const env = String(s.env || '?');
    const badge = $('#envBadge'); badge.textContent = env.toUpperCase();
    badge.className = 'badge ' + (env === 'live' ? 'live' : 'paper');
    // The two desks look identical at a glance, so put the account in the TAB
    // title and hang a red rail down the page when real money is on the line.
    const live = env === 'live';
    document.title = `${live ? '● LIVE' : 'paper'} · Market Forge :${location.port || '80'}`;
    document.body.classList.toggle('live-mode', live);
    $('#ksDot').classList.toggle('tripped', s.kill_switch === 'tripped');
    $('#equityTicker').textContent = fmt$(s.equity);
    $('#statRow').innerHTML = [
      ['Equity', fmt$(s.equity), ''], ['Cash', fmt$(s.cash), ''],
      // account positions vs bot-tracked trades are two different counts - show both
      ['Positions', fmt$(s.positions_value), `${pos.length} open${s.open_positions ? ` · ${s.open_positions} from bot` : ''}`],
      ['Day P/L', arrow(s.day_pl_pct) + (s.day_pl_pct >= 0 ? '+' : '') + (s.day_pl_pct * 100).toFixed(2) + '%', '', cls(s.day_pl_pct)],
      ['Drawdown', (s.drawdown_pct * 100).toFixed(1) + '%', '', s.drawdown_pct > 0.05 ? 'warn' : ''],
      ['Bankroll', fmt$(s.bankroll, 0), `${fmt$(s.committed)} used`],
    ].map(([l, v, sub, c]) => `<div class="stat"><div class="l">${l}</div><div class="v ${c || ''}">${v}</div><div class="dim">${sub || ''}</div></div>`).join('');
    $('#posCount').textContent = `(${pos.length || 0})`;
    $('#positions').innerHTML = pos.length ? '<table><tr><th>sym</th><th class="r">qty</th><th class="r">entry</th><th class="r">now</th><th class="r">value</th><th class="r">P/L</th><th></th></tr>' +
      pos.map(p => `<tr><td><b>${esc(p.symbol)}</b></td><td class="r">${p.qty}</td><td class="r">${fmt$(p.avg_entry)}</td><td class="r">${fmt$(p.price)}</td><td class="r">${fmt$(p.market_value)}</td><td class="r ${cls(p.unrealized_pl)}">${fmt$(p.unrealized_pl)}</td><td class="r"><button class="btn sm" onclick="openTicket('${esc(p.symbol)}','sell')">Sell</button></td></tr>`).join('') + '</table>'
      : '<span class="dim">no open positions</span>';
    $('#orders').innerHTML = ords.length ? '<table><tr><th>sym</th><th>side</th><th class="r">qty</th><th>status</th><th class="r">at</th></tr>' +
      ords.slice(0, 10).map(o => `<tr><td><b>${esc(o.symbol)}</b></td><td>${esc(o.side)}</td><td class="r">${o.qty ?? '-'}</td><td>${esc(o.status)}</td><td class="r dim">${esc((o.updated_at || o.created_at || '').slice(5, 16).replace('T', ' '))}</td></tr>`).join('') + '</table>'
      : '<span class="dim">no orders yet</span>';
    const cfg = await J('/api/bot/config').catch(() => null);
    if (cfg?.radar_auto) {
      const a = cfg.radar_auto, on = a.execute && (cfg.env !== 'live' || a.live_enabled);
      const mode = String(cfg.mode || '').toLowerCase();
      $('#autoChip').textContent = on
        ? `auto: ARMED · ${fmt$(a.notional, 0)}/trade · ${a.exit} ${(a.trail_pct * 100).toFixed(0)}%`
        : (mode === 'research' ? 'research mode · no trading' : 'auto: OFF · manual only');
      // data feed: SIP = paid real-time consolidated tape, IEX = free partial/delayed
      const feed = String(cfg.data_feed || '').toLowerCase();
      const fc = $('#feedChip');
      if (feed) {
        const rt = feed === 'sip';
        fc.textContent = rt ? 'data: REAL-TIME (SIP)' : `data: ${feed.toUpperCase()} (free)`;
        fc.style.color = rt ? 'var(--gain)' : '';
        fc.style.borderColor = rt ? 'rgba(74,222,128,.45)' : '';
        fc.title = rt ? 'Full consolidated tape, real time'
                      : 'Free IEX feed: a slice of total volume. Subscribe to Alpaca real-time and rerun setup.py to switch.';
      }
      window._cfg = cfg;
    }
  } catch (e) { $('#statRow').innerHTML = `<div class="stat"><div class="l">bot</div><div class="v down">unreachable</div><div class="dim">${esc(e.message)}</div></div>`; }
}

/* ---------- radar ---------- */
async function loadRadar() {
  try {
    const radar = await J('/api/bot/radar');
    const list = [...(radar || [])].sort((a, b) => (b.score || 0) - (a.score || 0));
    $('#radarCount').textContent = `(${list.length})`;
    const newest = list.map(r => r.ts || '').sort().at(-1) || '';
    $('#lastScan').textContent = newest ? `last scan ${newest.slice(5, 16).replace('T', ' ')} UTC` : '';
    const syms = [...new Set(list.map(r => r.symbol))].slice(0, 16).join(',');
    const spark = syms ? await J(`/api/bot/spark?symbols=${syms}`).catch(() => ({})) : {};
    $('#radar').innerHTML = list.length ? list.map(r => {
      const up = (r.pct || 0) >= 0, sp = spark[r.symbol], now = sp?.last;
      const since = now != null && r.price ? ((now - r.price) / r.price * 100) : null;
      let host = ''; try { host = r.url ? new URL(r.url).hostname.replace(/^www\./, '') : ''; } catch {}
      return `<div class="card ${r.verdict === 'signal' ? 'signal' : ''}">
        <div class="head"><span class="sym">${esc(r.symbol)}</span>
          <span class="pct ${up ? 'up' : 'down'} ${pctScale(r.pct)}">${r.pct != null ? arrow(r.pct) + (up ? '+' : '') + Number(r.pct).toFixed(1) + '%' : '--'}</span>
          <span class="dim">alert @ ${fmt$(r.price)}</span>
          <span class="score ${(r.score ?? 0) >= 70 ? 'hi' : (r.score ?? 0) >= 40 ? 'mid' : ''}">${r.score ?? '--'}</span></div>
        ${now != null ? `<div class="livechip"><span class="p"></span>now ${fmt$(now)} ${since != null ? `<b class="${cls(since)}">${since >= 0 ? '+' : ''}${since.toFixed(1)}% since alert</b>` : ''}</div>` : ''}
        ${r.catalyst_type ? `<div class="dim" style="color:var(--accent);font-size:10px;text-transform:uppercase">${esc(r.catalyst_type)}</div>` : ''}
        ${r.why ? `<div class="why">${esc(r.why)}</div>` : ''}
        ${r.headline ? `<a href="${esc(r.url || '#')}" target="_blank">${host ? `<img src="https://www.google.com/s2/favicons?domain=${host}&sz=32" width="12" height="12" style="vertical-align:-1px"> ` : ''}${esc(r.headline)}</a>` : ''}
        <div class="foot"><span class="dim">${esc((r.ts || '').slice(5, 16).replace('T', ' '))}</span>
          <button class="btn sm right" onclick="openTicket('${esc(r.symbol)}','buy')">Trade</button>
          <button class="btn sm" style="margin-left:6px" onclick="chartTo('${esc(r.symbol)}')">Chart</button></div></div>`;
    }).join('') : '<span class="dim">no catalysts flagged - radar scans 10a/12p/2p/4p ET weekdays</span>';
  } catch (e) { $('#radar').innerHTML = `<span class="dim">radar error: ${esc(e.message)}</span>`; }
}
$('#radarRefresh').onclick = async () => {
  $('#radarRefresh').textContent = 'Scanning...';
  try { await fetch('/api/bot/run/radar', { method: 'POST' }); await loadRadar(); notify('radar re-scan complete', 'ok'); }
  catch { notify('radar re-scan failed', 'err'); }
  $('#radarRefresh').textContent = 'Re-scan';
};
window.chartTo = (sym) => { chartSym = sym; $('#chartSym').value = sym; document.querySelector('[data-tab=overview]').click(); loadChart(); };

/* ---------- retail (reddit) ---------- */
async function loadReddit() {
  try {
    const d = await J('/api/bot/reddit');
    $('#redditSubs').textContent = (d.subs || []).map(s => 'r/' + s).join(' · ');
    $('#reddit').innerHTML = (d.trending || []).length ? d.trending.map(t => `<div class="card">
      <div class="head"><span class="sym">${esc(t.symbol)}</span>
        ${t.price != null ? `<span class="dim">${fmt$(t.price)}</span>` : ''}
        <span class="score" style="margin-left:auto;color:var(--accent)">${t.mentions} hot</span></div>
      ${(t.posts || []).slice(0, 2).map(p => `<a href="${esc(p.url)}" target="_blank">r/${esc(p.sub)} #${p.rank ?? '?'} · ${esc(p.title)}</a>`).join('')}
      <div class="foot"><button class="btn sm right" onclick="openTicket('${esc(t.symbol)}','buy')">Trade</button>
        <button class="btn sm" style="margin-left:6px" onclick="chartTo('${esc(t.symbol)}')">Chart</button></div></div>`).join('')
      : '<span class="dim">reddit buzz warming up (round-robin, ~10 min per sub)</span>';
  } catch (e) { $('#reddit').innerHTML = `<span class="dim">reddit error: ${esc(e.message)}</span>`; }
}

/* ---------- workbench (CC's live canvas; resizable, size-hinted, savable) ---------- */
let panelState = '';
const wbSizes = JSON.parse(localStorage.getItem('wbSizes') || '{}');  // {name: {w, h}} - survives restarts
function watchResize(card, name) {
  // Per-card timer: a single shared one meant that resizing two panels in quick
  // succession only ever persisted the last one.
  let timer = null;
  new ResizeObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      // Never persist the MAXIMIZED size - it is the viewport, and restoring it
      // later would leave the card permanently full-screen-sized in the grid.
      if (card.classList.contains('maximized')) return;
      wbSizes[name] = { w: card.offsetWidth, h: card.offsetHeight };
      localStorage.setItem('wbSizes', JSON.stringify(wbSizes));
    }, 400);
  }).observe(card);
}
async function loadPanels(force = false) {
  const d = await J('/api/panels').catch(() => ({ panels: [] }));
  const sig = JSON.stringify(d.panels);
  $('#wbCount').textContent = `${d.panels.length} panel(s) live.`;
  if (!force && sig === panelState) return;
  // real activity events from the file diff (skip the very first load)
  if (panelState) {
    try {
      const prev = new Map(JSON.parse(panelState).map(p => [p.name, p.mtime]));
      for (const p of d.panels) {
        if (!prev.has(p.name)) notify(`panel created: ${p.title || p.name}`, 'ok');
        else if (prev.get(p.name) !== p.mtime) notify(`panel updated: ${p.title || p.name}`, 'info', false);
        prev.delete(p.name);
      }
      for (const [name] of prev) notify(`panel removed: ${name}`, 'info', false);
    } catch {}
  }
  panelState = sig;
  const wb = $('#workbench');
  wb.innerHTML = '';
  for (const p of d.panels) {
    const card = document.createElement('div');
    card.className = 'wb-card ' + (p.size && p.size !== 'normal' ? p.size : '');
    const saved = wbSizes[p.name];
    if (saved?.w) { card.style.width = saved.w + 'px'; card.style.flex = 'none'; }
    if (saved?.h) card.style.height = saved.h + 'px';
    // ⤢ maximizes a card to the whole viewport. A long "full page" board should be
    // READ full-bleed, not squinted at through a 400px letterbox with two scrollbars.
    card.innerHTML = `<div class="wb-t">${esc(p.title)}` +
      `<span class="right dim">${esc(p.name)}</span>` +
      `<button class="wb-max" title="expand to full screen (Esc to close)">⤢</button></div>`;
    card.querySelector('.wb-max').onclick = (e) => {
      e.stopPropagation();
      const on = card.classList.toggle('maximized');
      document.body.classList.toggle('has-max', on);
      e.target.textContent = on ? '✕' : '⤢';
    };
    const fr = document.createElement('iframe');
    fr.sandbox = 'allow-scripts allow-same-origin allow-popups';
    fr.src = `/api/panel?name=${encodeURIComponent(p.name)}&v=${p.mtime}`;
    // an iframe is its own document and does NOT inherit [data-theme]
    fr.onload = () => { try {
      fr.contentDocument.documentElement.dataset.theme = document.documentElement.dataset.theme;
    } catch {} };
    card.appendChild(fr); wb.appendChild(card);
    watchResize(card, p.name);
  }
  if (!d.panels.length) wb.innerHTML = '<div class="panel dim">Empty board. Ask the copilot: ' +
    '<code>build me tomorrow\'s plan as one full-width board</code></div>';
}

/* saved boards */
async function loadSavedList() {
  const d = await J('/api/workbench/saved').catch(() => ({ saved: [] }));
  const sel = $('#wbSavedList');
  const cur = sel.value;
  sel.innerHTML = '<option value="">saved boards...</option>' +
    (d.saved || []).map(s => `<option value="${esc(s.name)}">${esc(s.name)} (${s.panels})</option>`).join('');
  sel.value = cur;
}
$('#wbSave').onclick = async () => {
  const name = $('#wbSaveName').value.trim() || `board-${new Date().toISOString().slice(0, 16).replace(/[T:]/g, '-')}`;
  const r = await J('/api/workbench/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
  $('#wbSave').textContent = r.ok ? `Saved ✓ (${r.panels})` : 'failed';
  notify(r.ok ? `board saved: ${r.name} (${r.panels} panels)` : 'board save failed', r.ok ? 'ok' : 'err');
  setTimeout(() => { $('#wbSave').textContent = 'Save board'; }, 2500);
  $('#wbSaveName').value = ''; loadSavedList();
};
$('#wbLoad').onclick = async () => {
  const name = $('#wbSavedList').value;
  if (!name) return;
  const r = await J('/api/workbench/load', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
  if (r.ok) {
    $('#wbLoad').textContent = 'Loaded ✓'; setTimeout(() => { $('#wbLoad').textContent = 'Load'; }, 2500);
    notify(`board loaded: ${name} · previous autosaved as ${r.previous_saved_as}`, 'ok');
    loadPanels(true); loadSavedList();
  } else notify('board load failed', 'err');
};

/* ---------- rules ---------- */
function mdToHtml(md) { /* tiny renderer: headings, bold, code, tables, lists */
  let h = esc(md);
  h = h.replace(/^### (.*)$/gm, '<h3>$1</h3>').replace(/^## (.*)$/gm, '<h2>$1</h2>').replace(/^# (.*)$/gm, '<h1>$1</h1>');
  h = h.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  h = h.replace(/^\|(.+)\|$/gm, (m) => '<tr>' + m.slice(1, -1).split('|').map(c => `<td>${c.trim()}</td>`).join('') + '</tr>');
  h = h.replace(/(<tr>.*<\/tr>\n?)+/g, (m) => `<table>${m}</table>`);
  h = h.replace(/^- (.*)$/gm, '<li>$1</li>').replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`);
  return h.replace(/\n{2,}/g, '<br>');
}
async function loadRules() {
  const cfg = window._cfg || await J('/api/bot/config').catch(() => null);
  if (cfg) {
    const a = cfg.radar_auto || {};
    const rows = [
      ['env', String(cfg.env).toUpperCase()], ['bankroll', fmt$(cfg.bankroll, 0)],
      ['auto armed', a.execute && a.live_enabled ? 'YES' : 'no', 1], ['$/trade', fmt$(a.notional, 0), 1],
      ['entries/day', a.max_per_day, 1], ['max exposure', fmt$(a.max_exposure, 0), 1],
      ['exit', `${a.exit} ${(a.trail_pct * 100 || 0).toFixed(0)}%`, 1], ['min score', a.min_score, 1],
      ['price floor', fmt$(a.min_price, 0), 1], ['radar min move', cfg.radar_min_move_pct + '%'],
      ['reddit subs', (cfg.reddit_subs || []).join(', ')], ['llm min score', cfg.radar_llm_min_score],
    ];
    // THE AGENT'S PLAN: what the machine actually does, then its live knobs -
    // every knob is clickable and opens a tweak conversation with the copilot.
    $('#botConfig').innerHTML =
      `<div class="pipeline dim" style="font-size:13.5px;line-height:1.7;margin-bottom:12px">
        <b style="color:var(--fg)">The agent's pipeline:</b>
        Alpaca movers screener → <b>verify</b> every % against the live tape + real prior close
        (fakes get dropped) → LLM triage (signal vs noise, score 0-100) with news headlines +
        Reddit hot-page buzz folded in → <b>gates</b> (score, price floor, caps, kill-switch) →
        whole-share entry with a GTC trailing stop armed at fill.
        Scans 10a/12p/2p/4p ET weekdays · Reddit round-robin ~10 min · news + bars on demand.
      </div>
      <div class="kv">` + rows.map(([l, v, hot]) =>
      `<div class="k ${hot ? 'hot' : ''}" style="cursor:pointer" title="click to discuss/tweak with the copilot" data-l="${esc(l)}" data-v="${esc(String(v ?? '--'))}">` +
      `<div class="l">${l}</div><div class="v">${esc(String(v ?? '--'))}</div></div>`).join('') + '</div>' +
      '<p class="dim">Click any knob to open a tweak conversation. Applying = config change + engine restart ' +
      '(edit bot/.env, then restart the desk).</p>';
    document.querySelectorAll('#botConfig .k').forEach(el => el.onclick = () =>
      palPrefill(`I want to look at the "${el.dataset.l}" knob (currently ${el.dataset.v}). Explain what it controls, the tradeoff of moving it, suggest a value for my style (check memory.md), and tell me exactly how to apply it.`));
  }
  const md = await (await fetch('/api/rules')).text();
  $('#rulesDoc').innerHTML = mdToHtml(md);
}

/* ---------- copilot chat + voice ---------- */
let chatCount = 0;
let bridgeThinking = false, bridgeSince = 0, bridgeStep = '', bridgeSteps = 0, workingMarks = new Set();
async function loadBridge() {
  try {
    const b = await J('/api/bridge');
    const c = $('#bridgeChip');
    const was = bridgeThinking;
    bridgeThinking = b.status === 'thinking';
    bridgeSince = b.since || 0;
    bridgeStep = b.step || ''; bridgeSteps = b.steps || 0;
    if (!b.enabled) { c.textContent = 'bridge: off'; c.style.color = ''; return; }
    $('#stopBtn').classList.toggle('hidden', !bridgeThinking);
    if (bridgeThinking) {
      const el = Math.max(0, Math.round(Date.now() / 1000 - bridgeSince));
      c.textContent = `bridge: working ${el}s${bridgeStep ? ' · ' + bridgeStep : '...'}`;
      c.style.color = 'var(--accent)';
      const mark = Math.floor(el / 45);   // spoken heartbeat while it builds
      if (mark > 0 && !workingMarks.has(mark) && $('#ttsToggle').checked) {
        workingMarks.add(mark);
        speakText('Still working on it.');
      }
      renderTyping(el);
      renderDock();
    } else {
      if (was) notify(`bridge replied · ${(b.last_ms / 1000).toFixed(0)}s · ${bridgeSteps || 'no'} tool step${bridgeSteps === 1 ? '' : 's'}`, 'ok', false);
      workingMarks.clear(); renderTyping(0);
      c.textContent = `bridge: LIVE · ${b.model}${b.turns ? ` · ${(b.last_ms / 1000).toFixed(1)}s last` : ''}`;
      c.style.color = 'var(--gain)';
      renderDock();
    }
  } catch { $('#bridgeChip').textContent = 'bridge: ?'; }
}
function renderTyping(elapsed) {
  let t = document.getElementById('typingMsg');
  if (!elapsed) { t?.remove(); return; }
  if (!t) {
    t = document.createElement('div');
      t.id = 'typingMsg'; t.className = 'turn assistant typing';
    $('#chatLog').appendChild(t);
  }
  const lg = $('#chatLog');
  const stick = lg.scrollHeight - lg.scrollTop - lg.clientHeight < 160;
  t.innerHTML = `<div class="who">◆</div><div class="body">` +
    `<div class="stamp">copilot · step ${bridgeSteps || 0}</div>` +
    `${bridgeStep ? esc(bridgeStep) : 'thinking'}<span class="dots"></span> ${elapsed}s` +
    `<span class="stopmini" onclick="stopAll()" title="abort this turn">⏹ stop</span></div>`;
  if (stick) lg.scrollTop = lg.scrollHeight;
}
let chatInit = false;      // refresh guard: NEVER read the backlog aloud on page load
const spokenKeys = new Set();  // identity of every message already accounted for

// Diff by IDENTITY, not by array index. The list is re-sorted by ts on every
// poll, and the interactive CC session appends to chat-outbox.jsonl out of band.
// A message landing with a not-strictly-newest ts inserts into the MIDDLE, which
// shifts an old already-spoken reply into the tail slot - and an index diff
// (`msgs.slice(chatCount)`) then reads that old message aloud on every send.
const msgKey = m => `${m.ts}|${m.role}|${String(m.text).length}|${String(m.text).slice(0, 64)}`;

async function loadChat() {
  const d = await J('/api/chat').catch(() => null);
  if (!d) return;
  const msgs = [...d.inbox, ...d.outbox].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  if (chatInit && msgs.length === chatCount) return;
  chatCount = msgs.length;
  // Stick to the bottom, but only if you were ALREADY there. Yanking the view
  // down while someone is scrolled up reading an old answer is infuriating.
  const log = $('#chatLog');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
  const me = (window.__mfUser || 'You').trim().slice(0, 2).toUpperCase();
  $('#chatLog').innerHTML = msgs.map(m => {
    const mine = m.role === 'user';
    return `<div class="turn ${mine ? 'user' : 'assistant'}">` +
      `<div class="who">${mine ? esc(me) : '◆'}</div>` +
      `<div class="body"><div class="stamp">${mine ? 'you' : 'copilot'} · ` +
      `${esc(String(m.ts).slice(5, 16).replace('T', ' '))}</div>${esc(m.text)}</div></div>`;
  }).join('');
  // after layout, not during: scrollHeight is stale until the browser reflows
  if (atBottom || !chatInit) requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });

  // first paint: mark everything as seen, say nothing
  if (!chatInit) {
    chatInit = true;
    msgs.forEach(m => spokenKeys.add(msgKey(m)));
    return;
  }
  const fresh = msgs.filter(m => !spokenKeys.has(msgKey(m)));
  msgs.forEach(m => spokenKeys.add(msgKey(m)));
  const speakable = fresh.filter(m => m.role === 'assistant' && !String(m.text).startsWith('(stopped'));
  if ($('#ttsToggle').checked && speakable.length) {
    speakText(speakable.map(m => m.text).join('. '));
  }
}

/* ---------- naked positions ----------
   A position with no working sell order. Polled with the overview; arming is a
   REAL order, so it always costs a deliberate click and a confirm. */
async function loadNaked() {
  const el = $('#nakedBanner'); if (!el) return;
  const d = await J('/api/bot/unprotected').catch(() => null);
  const rows = d && d.positions || [];
  if (!rows.length) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  el.classList.remove('hidden');
  el.innerHTML = `<h4>⚠ ${rows.length} position${rows.length === 1 ? '' : 's'} with NO exit armed</h4>` +
    rows.map(r => `<div class="row"><b>${esc(r.symbol)}</b> ${r.qty} sh ` +
      `<span class="dim">entry ${fmt$(r.avg_entry)} · now ${fmt$(r.price)}</span> ` +
      `<span class="${cls(Number(r.unrealized_pl))}">${fmt$(r.unrealized_pl)}</span>` +
      `<button class="btn sm right" onclick="protectPos('${esc(r.symbol)}')">Arm 10% trail</button></div>`).join('');
}
window.protectPos = async (symbol, trail = 10) => {
  if (!confirm(`Arm a ${trail}% trailing stop on ${symbol}?\n\n` +
               `This places a REAL GTC sell order with your broker.`)) return;
  const r = await J('/api/bot/protect', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, trail_pct: trail }),
  }).catch((e) => ({ ok: false, error: String(e) }));
  notify(r.ok ? `${symbol}: ${trail}% trail armed (${r.qty} sh)` : `protect failed: ${r.error}`,
         r.ok ? 'ok' : 'err');
  loadNaked(); loadOverview();
};

/* ---------- ADMIN: read-only inventory ----------
   "What is running, on what model, from which files, and what has it cost."
   No create/edit controls anywhere on this tab, on purpose. */
const dur = (s) => s == null ? '' : s > 86400 ? `${(s / 86400).toFixed(1)}d`
  : s > 3600 ? `${(s / 3600).toFixed(1)}h` : s > 60 ? `${Math.round(s / 60)}m` : `${Math.round(s)}s`;
const kb = (b) => b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB' : b >= 1000 ? Math.round(b / 1000) + ' KB' : b + ' B';
const tok = (n) => n == null ? '--' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M'
  : n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n);

async function loadAdmin() {
  const d = await J('/api/admin').catch(() => null);
  if (!d) { $('#adminLanes').innerHTML = '<span class="dim">admin unavailable</span>'; return; }
  const r = d.runtime || {}, u = d.usage || {};

  $('#adminRuntime').innerHTML = [
    ['Uptime', dur(r.uptime_s), r.started || ''],
    ['Mode', r.embedded ? 'embedded' : 'split', r.embedded ? 'engine runs in-process' : r.bot_base],
    ['Python', r.python || '--', r.platform || ''],
    ['Copilot spend', u.cost_usd != null ? fmt$(u.cost_usd, 2) : '--',
      `${u.turns || 0} bridge turn${u.turns === 1 ? '' : 's'}`],
    ['Tokens', tok(u.tokens_total), u.cached ? `${tok(u.cached)} cached` : ''],
    ['Panels', String(d.counts?.panels ?? 0), `${d.counts?.boards ?? 0} saved board(s)`],
  ].map(([l, v, s]) => `<div class="stat"><div class="l">${l}</div><div class="v">${esc(v)}</div>` +
    `<div class="dim">${esc(s)}</div></div>`).join('');

  $('#adminLanes').innerHTML = '<table class="admin"><tr><th>lane</th><th>model</th><th>runtime</th>' +
    '<th>state</th><th class="r">turns</th><th class="r">last</th></tr>' +
    (d.lanes || []).map(l => {
      const st = l.enabled === false ? 'off' : (l.status || '');
      const cl = /off|not found|external/.test(String(st)) ? 'dim' : 'up';
      return `<tr><td><b>${esc(l.name)}</b><div class="dim">${esc(l.note || '')}</div></td>` +
        `<td class="mono">${esc(l.model)}</td>` +
        `<td class="dim mono">${esc(l.runtime)}${l.binary ? `<div class="faint">${esc(l.binary)}</div>` : ''}</td>` +
        `<td class="${cl}">${esc(st)}</td>` +
        `<td class="r mono">${l.turns ?? '--'}</td>` +
        `<td class="r mono">${l.last_ms ? (l.last_ms / 1000).toFixed(0) + 's' : '--'}</td></tr>`;
    }).join('') + '</table>';

  $('#adminFiles').innerHTML = '<table class="admin"><tr><th>file</th><th>what it does</th>' +
    '<th class="r">size</th><th class="r">changed</th></tr>' +
    (d.files || []).map(f => `<tr><td class="mono ${f.exists ? '' : 'dim'}">${esc(f.label)}` +
      `${f.exists ? '' : ' <span class="dim">(missing)</span>'}</td>` +
      `<td class="dim">${esc(f.what)}</td>` +
      `<td class="r mono">${f.exists ? kb(f.bytes) : '--'}</td>` +
      `<td class="r dim mono">${esc(f.mtime || '--')}</td></tr>`).join('') + '</table>';

  const t = d.trading || {};
  $('#adminTrading').innerHTML = '<table class="admin">' + [
    ['Account', String(t.env || '--').toUpperCase(), t.env === 'live' ? 'loss' : ''],
    ['Data feed', String(t.feed || '--').toUpperCase(), ''],
    ['Mode', t.mode || 'manual', ''],
    ['Auto-entries', t.auto ? 'ARMED' : 'off', t.auto ? 'loss' : 'up'],
    ['Live auto unlocked', t.live_auto ? 'YES' : 'no', t.live_auto ? 'loss' : 'up'],
    ['Per trade', t.per_trade_cents != null ? fmt$(t.per_trade_cents / 100, 0) : '--', ''],
    ['Max per day', t.max_per_day ?? '--', ''],
    ['Min score', t.min_score ?? '--', ''],
  ].map(([l, v, c]) => `<tr><td class="dim">${l}</td><td class="mono ${c}">${esc(String(v))}</td></tr>`).join('') + '</table>';

  const c = d.counts || {};
  $('#adminCounts').innerHTML = '<table class="admin">' +
    Object.entries(c.journal_kinds || {}).sort((a, b) => b[1] - a[1])
      .map(([k, n]) => `<tr><td class="dim">${esc(k)}</td><td class="r mono">${n}</td></tr>`).join('') +
    `<tr><td class="dim">chat messages</td><td class="r mono">${c.chat ?? 0}</td></tr>` +
    `<tr><td class="dim">journal entries</td><td class="r mono">${c.journal ?? 0}</td></tr></table>`;

  // ---- usage: measured spend, the OpsCanvas AI-Usage framing but local
  const bm = Object.entries(u.by_model || {}).sort((a, b) => b[1].cost_usd - a[1].cost_usd);
  const bd = Object.entries(u.by_day || {}).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 14);
  const today = new Date().toISOString().slice(0, 10);
  $('#usageCards').innerHTML = [
    ['Total', u.cost_usd != null ? fmt$(u.cost_usd, 2) : '$0.00', `${u.turns || 0} turns`],
    ['Today', fmt$((u.by_day || {})[today]?.cost_usd || 0, 2), `${(u.by_day || {})[today]?.turns || 0} turns`],
    ['Tokens', tok(u.tokens_total), `${tok(u.cached)} read from cache`],
    ['Avg / turn', u.turns ? fmt$(u.cost_usd / u.turns, 3) : '--', 'bridge lane only'],
  ].map(([l, v, s]) => `<div class="stat"><div class="l">${l}</div><div class="v">${esc(v)}</div>` +
    `<div class="dim">${esc(s)}</div></div>`).join('');
  const bar = (v, max) => `<div class="ubar"><i style="width:${max ? Math.max(2, (v / max) * 100) : 0}%"></i></div>`;
  const maxM = Math.max(1, ...bm.map(([, x]) => x.cost_usd));
  $('#usageModels').innerHTML = bm.length ? '<table class="admin">' + bm.map(([m, x]) =>
    `<tr><td class="mono">${esc(m)}${bar(x.cost_usd, maxM)}</td><td class="r mono">${fmt$(x.cost_usd, 2)}</td>` +
    `<td class="r dim mono">${tok(x.tokens)}</td><td class="r dim mono">${x.turns}t</td></tr>`).join('') + '</table>'
    : '<span class="dim">no bridge turns logged yet</span>';
  const maxD = Math.max(1, ...bd.map(([, x]) => x.cost_usd));
  $('#usageDays').innerHTML = bd.length ? '<table class="admin">' + bd.map(([day, x]) =>
    `<tr><td class="mono">${esc(day)}${bar(x.cost_usd, maxD)}</td><td class="r mono">${fmt$(x.cost_usd, 2)}</td>` +
    `<td class="r dim mono">${tok(x.tokens)}</td><td class="r dim mono">${x.turns}t</td></tr>`).join('') + '</table>'
    : '<span class="dim">nothing yet</span>';

  const voice = (d.lanes || []).find(l => /voice/i.test(l.name)) || {};
  $('#adminVoice').innerHTML = '<table class="admin">' +
    `<tr><td class="dim">Profile</td><td class="mono">${esc(voice.model || '--')}</td></tr>` +
    `<tr><td class="dim">Server</td><td class="mono">${esc(voice.binary || '--')}</td></tr>` +
    `<tr><td class="dim">State</td><td class="mono">${esc(voice.status || '--')}</td></tr></table>` +
    '<div class="dim hint">Change the voice with <code>voicebox_profile</code> in <code>config.json</code>. ' +
    'Presets need an engine, cloned voices reject one, and the relay negotiates that per profile.</div>';
  $('#themeNote').textContent =
    'Skins are token blocks in style.css. Panels follow the app, so a board built in one skin reads correctly in all of them.';

  $('#adminLog').innerHTML = (d.recent || []).length
    ? '<table class="admin">' + d.recent.map(e =>
        `<tr><td class="dim mono" style="width:130px">${esc(String(e.ts || '').slice(5, 16).replace('T', ' '))}</td>` +
        `<td class="mono" style="width:80px">${esc(e.kind || '')}</td>` +
        `<td>${esc(e.text || '')}</td></tr>`).join('') + '</table>'
    : '<span class="dim">nothing logged yet</span>';
}

/* ---------- voice out: Voicebox (kokoro @ 127.0.0.1:17493) first, browser fallback ---------- */
let vbOk = false, audioEl = null;
async function vbHealth() {
  try {
    const h = await J('/api/tts/health');
    vbOk = !!h.ok;
    const d = $('#vbDot');
    if (d) { d.className = vbOk ? 'ok' : 'bad'; d.title = vbOk ? 'Voicebox connected (kokoro)' : (h.error || h.hint || 'Voicebox unavailable - using browser voice'); }
  } catch { vbOk = false; }
}
/* STOP: kill the in-flight bridge turn AND silence the voice, from anywhere */
window.stopAll = async () => {
  try { audioEl?.pause(); } catch {}
  if ('speechSynthesis' in window) speechSynthesis.cancel();
  speakingNow = false;
  const r = await J('/api/bridge/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => ({}));
  notify(r.stopped ? 'bridge turn stopped' : 'voice silenced', r.stopped ? 'warn' : 'info');
  loadBridge();
};
$('#stopBtn').onclick = window.stopAll;

let speakingNow = false;  // echo guard: hot mic ignores itself while the copilot talks
async function speakText(text) {
  if (!text) return;
  if (vbOk) {
    try {
      const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text.slice(0, 900) }) });
      if (r.ok && (r.headers.get('Content-Type') || '').includes('audio')) {
        const blob = await r.blob();
        audioEl?.pause();
        audioEl = new Audio(URL.createObjectURL(blob));
        speakingNow = true;
        audioEl.onended = audioEl.onerror = () => { setTimeout(() => { speakingNow = false; }, 400); };
        audioEl.play();
        return;
      }
    } catch { /* fall through to browser voice */ }
    vbHealth();
  }
  if ('speechSynthesis' in window) {
    const u = new SpeechSynthesisUtterance(text.slice(0, 600)); u.rate = 1.05;
    speakingNow = true;
    u.onend = u.onerror = () => { setTimeout(() => { speakingNow = false; }, 400); };
    speechSynthesis.speak(u);
  }
}
async function sendChat() {
  const t = $('#chatInput').value.trim();
  if (!t) return;
  $('#chatInput').value = '';
  await J('/api/chat/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) });
  loadChat();
}
$('#chatSend').onclick = sendChat;
// Enter sends, Shift+Enter is a newline (the convention everyone already knows).
// The box grows with the text instead of scrolling a one-line input.
const growInput = () => {
  const ta = $('#chatInput');
  ta.style.height = 'auto';
  ta.style.height = Math.min(220, ta.scrollHeight) + 'px';
};
$('#chatInput').addEventListener('input', growInput);
$('#chatInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); setTimeout(growInput, 0); }
});

/* voice in: push-to-talk (#micBtn, COPILOT tab) + HOT MIC (#hotMic, topbar - always
   listening from ANY tab; the whole app is one page so tabs don't matter). Echo-guarded:
   anything heard while the copilot is speaking gets discarded. Chrome caps continuous
   sessions, so hot mic auto-restarts on end. */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR) {
  const rec = new SR(); rec.lang = 'en-US'; rec.interimResults = true;
  let on = false;
  rec.onresult = (e) => { $('#chatInput').value = [...e.results].map(r => r[0].transcript).join(''); };
  rec.onend = () => { $('#micBtn').classList.remove('rec'); on = false; if ($('#chatInput').value.trim()) sendChat(); };
  $('#micBtn').onclick = () => { on ? rec.stop() : (rec.start(), $('#micBtn').classList.add('rec'), on = true); };

  const hot = new SR(); hot.lang = 'en-US'; hot.continuous = true; hot.interimResults = false;
  let hotOn = false;
  hot.onresult = (e) => {
    if (speakingNow) return;                       // that's our own voice - drop it
    const text = [...e.results].slice(e.resultIndex).map(r => r[0].transcript).join(' ').trim();
    if (text.length > 2) {
      J('/api/chat/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text } ) }).then(loadChat);
    }
  };
  hot.onend = () => { if (hotOn) { try { hot.start(); } catch {} } };   // Chrome kills long sessions - relight
  hot.onerror = (e) => { if (e.error === 'not-allowed') { hotOn = false; $('#hotMic').classList.remove('on'); } };
  $('#hotMic').onclick = () => {
    hotOn = !hotOn;
    $('#hotMic').classList.toggle('on', hotOn);
    $('#hotMic').textContent = hotOn ? '🎙 hot mic ON' : '🎙 hot mic';
    try { hotOn ? hot.start() : hot.stop(); } catch {}
  };
} else { $('#micBtn').title = 'voice needs Chrome/Edge'; $('#micBtn').disabled = true; $('#hotMic').disabled = true; }

/* ---------- trade ticket ---------- */
let tk = { symbol: '', side: 'buy' };
window.openTicket = (sym, side = 'buy') => {
  tk = { symbol: sym.toUpperCase(), side };
  $('#tkSym').textContent = tk.symbol;
  document.querySelectorAll('.btn.side').forEach(b => b.classList.toggle('on', b.dataset.side === side));
  $('#tkResult').textContent = ''; $('#tkResult').className = 'tk-result mono';
  $('#tkConfirm').value = ''; $('#tkGo').disabled = true;
  const env = ($('#envBadge').textContent || 'live').toLowerCase();
  $('#tkEnv').textContent = env.toUpperCase(); $('#tkConfirm').placeholder = `type "${env}" to arm`;
  $('#ticket').classList.remove('hidden');
};
document.querySelectorAll('.btn.side').forEach(b => b.onclick = () => {
  tk.side = b.dataset.side;
  document.querySelectorAll('.btn.side').forEach(x => x.classList.toggle('on', x === b));
});
$('#tkClose').onclick = () => $('#ticket').classList.add('hidden');
$('#tkConfirm').addEventListener('input', () => {
  $('#tkGo').disabled = $('#tkConfirm').value.trim().toLowerCase() !== ($('#envBadge').textContent || '').toLowerCase();
});
$('#tkGo').onclick = async () => {
  $('#tkGo').disabled = true; $('#tkGo').textContent = 'Placing...';
  const body = { symbol: tk.symbol, side: tk.side, confirm: $('#tkConfirm').value.trim() };
  const qty = Number($('#tkQty').value), notional = Number($('#tkNotional').value), trail = Number($('#tkTrail').value);
  if (qty >= 1) body.qty = qty; else body.notional = notional;
  if (tk.side === 'buy' && trail > 0) body.exit_trail_pct = trail;
  try {
    const r = await J('/api/bot/order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const el = $('#tkResult');
    if (r.ok) {
      el.className = 'tk-result mono ok';
      el.textContent = `✓ ${r.status || 'submitted'} · ${tk.side} ${r.qty ?? ''} ${tk.symbol}` +
        (r.trail ? (r.trail.armed ? `\n✓ trailing stop ARMED: ${r.trail.trail_percent}% off the high (qty ${r.trail.qty})` : `\n⚠ ${r.trail.error}`) : '');
      notify(`order ${r.status || 'submitted'}: ${tk.side} ${tk.symbol}${r.trail?.armed ? ' + trail armed' : ''}`, 'ok');
      if (r.trail && !r.trail.armed) notify(`trail NOT armed on ${tk.symbol} - set a stop manually`, 'warn');
      loadOverview();
    } else { el.className = 'tk-result mono err'; el.textContent = '✗ ' + (r.error || 'failed'); notify(`order failed: ${tk.symbol}`, 'err'); }
  } catch (e) { $('#tkResult').className = 'tk-result mono err'; $('#tkResult').textContent = '✗ ' + e.message; }
  $('#tkGo').textContent = 'Submit'; $('#tkGo').disabled = false;
};

/* ---------- event bus: toasts (top-right, fleeting) + dock (bottom-right, memory) ---------- */
const events = [];
function notify(text, kind = 'info', toast = true) {
  events.unshift({ ts: new Date().toTimeString().slice(0, 8), text, kind });
  if (events.length > 30) events.pop();
  renderDock();
  if (!toast) return;
  const t = document.createElement('div');
  t.className = 'toast ' + kind;
  t.textContent = (kind === 'ok' ? '✓ ' : kind === 'err' ? '✗ ' : '') + text;
  $('#toasts').appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 400); }, 4200);
}
function renderDock() {
  const latest = events[0];
  $('#dockPill').innerHTML = bridgeThinking
    ? `<span class="working">●</span> bridge working${bridgeStep ? ' · ' + esc(bridgeStep) : '...'}`
    : (latest ? `${latest.kind === 'ok' ? '✓' : '·'} ${esc(latest.text).slice(0, 56)}` : 'quiet');
  const list = $('#dockList');
  if (!list.classList.contains('hidden'))
    list.innerHTML = events.map(e => `<div class="ev ${e.kind}"><span class="dim">${e.ts}</span> ${esc(e.text)}</div>`).join('')
      || '<div class="ev dim">nothing yet</div>';
}
$('#dockPill').onclick = () => { $('#dockList').classList.toggle('hidden'); renderDock(); };

/* ---------- command palette (Ctrl+K): commands first, copilot for everything else ---------- */
const tabTo = (name) => document.querySelector(`[data-tab=${name}]`)?.click();
async function palChat(text) {
  await J('/api/chat/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
  tabTo('copilot'); loadChat();
  notify('sent to copilot', 'info', false);
}
function palPrefill(text) {   // deliberate-send variant: loads the chat box, YOU hit Enter
  tabTo('copilot');
  $('#chatInput').value = text;
  $('#chatInput').focus();
  notify('drafted - press Enter to send', 'info', false);
}
async function clearBoard() {
  const r = await J('/api/workbench/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  notify(r.ok ? `board cleared (${r.cleared} panel${r.cleared === 1 ? '' : 's'} autosaved)` : 'clear failed', r.ok ? 'ok' : 'err');
  loadPanels(true); loadSavedList();
}
const PAL_STATIC = [
  { k: 'go: overview', ico: '▦', run: () => tabTo('overview') },
  { k: 'go: catalyst radar', ico: '▦', run: () => tabTo('radar') },
  { k: 'go: retail radar (reddit)', ico: '▦', run: () => tabTo('retail') },
  { k: 'go: workbench', ico: '▦', run: () => tabTo('workbench') },
  { k: 'go: copilot', ico: '▦', run: () => tabTo('copilot') },
  { k: 'go: journal', ico: '▦', run: () => tabTo('journal') },
  // rules moved under Admin, so the palette has to open the tab AND the sub-tab
  { k: 'go: rules', ico: '▦', run: () => subTo('rules') },
  { k: 'go: usage / cost', ico: '$', run: () => subTo('usage') },
  { k: 'go: admin', ico: '▦', run: () => subTo('status') },
  { k: 'change skin / theme', ico: '◧', run: () => subTo('appearance') },
  { k: 're-scan radar', ico: '⟳', run: () => { tabTo('radar'); $('#radarRefresh').click(); } },
  { k: 'save board', ico: '💾', run: () => { tabTo('workbench'); $('#wbSaveName').focus(); } },
  { k: 'clear board (autosaves first)', ico: '🧹', run: clearBoard },
  { k: 'hot mic toggle', ico: '🎙', run: () => $('#hotMic').click() },
  { k: 'stop - abort bridge turn + silence voice', ico: '⏹', run: () => stopAll() },
  { k: 'memory drawer', ico: '🧠', run: () => $('#memBtn').click() },
  { k: 'morning brief', ico: '☀', run: () => palChat('Build the morning brief as ONE full-width board: market state, my account and positions, top radar signals with your read, reddit overlap, and the plan for today.') },
  { k: 'show earnings movers', ico: '📈', run: () => palChat('Build one full-width board of today\'s earnings-driven movers from the radar: name, move, your read, and levels worth watching.') },
];
function palCandidates(q) {
  const out = [];
  const ql = q.trim().toLowerCase();
  const symM = ql.match(/^(chart|trade|scan)\s+([a-z.]{1,6})$/);
  if (symM) {
    const sym = symM[2].toUpperCase();
    if (symM[1] === 'chart' || symM[1] === 'scan') out.push({ k: `chart ${sym}`, ico: '📊', run: () => chartTo(sym) });
    if (symM[1] === 'trade') out.push({ k: `trade ${sym}`, ico: '$', run: () => openTicket(sym, 'buy') });
  }
  for (const c of PAL_STATIC) if (!ql || ql.split(/\s+/).every(t => c.k.includes(t))) out.push(c);
  for (const o of $('#wbSavedList').options)
    if (o.value && (!ql || o.value.toLowerCase().includes(ql.replace(/^load\s*/, ''))))
      out.push({ k: `load board: ${o.value}`, ico: '⧉', run: async () => { $('#wbSavedList').value = o.value; $('#wbLoad').click(); tabTo('workbench'); } });
  if (ql) out.push({ k: `ask copilot: "${q.trim()}"`, ico: '➤', ask: true, run: () => palChat(q.trim()) });
  return out.slice(0, 9);
}
let palSel = 0;
function palRender() {
  const items = palCandidates($('#palInput').value);
  palSel = Math.min(palSel, Math.max(0, items.length - 1));
  $('#palList').innerHTML = items.map((c, i) =>
    `<div class="pal-item ${i === palSel ? 'sel' : ''} ${c.ask ? 'ask' : ''}" data-i="${i}"><span class="ico">${c.ico}</span>${esc(c.k)}${i === palSel ? '<span class="hintk">enter</span>' : ''}</div>`).join('');
  document.querySelectorAll('.pal-item').forEach(el => el.onclick = () => { palRun(items[+el.dataset.i]); });
  return items;
}
function palOpen() { $('#palette').classList.remove('hidden'); $('#palInput').value = ''; palSel = 0; palRender(); $('#palInput').focus(); }
function palClose() { $('#palette').classList.add('hidden'); }
function palRun(item) { if (item) { palClose(); item.run(); } }
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#palette').classList.contains('hidden') ? palOpen() : palClose(); }
  else if (e.key === 'Escape' && !$('#palette').classList.contains('hidden')) palClose();
});
$('#palInput').addEventListener('input', () => { palSel = 0; palRender(); });
$('#palInput').addEventListener('keydown', (e) => {
  const items = palCandidates($('#palInput').value);
  if (e.key === 'ArrowDown') { e.preventDefault(); palSel = (palSel + 1) % items.length; palRender(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); palSel = (palSel - 1 + items.length) % items.length; palRender(); }
  else if (e.key === 'Enter') { e.preventDefault(); palRun(items[palSel]); }
});
$('#palette').addEventListener('click', (e) => { if (e.target.id === 'palette') palClose(); });

/* ---------- ticker tape (GridPulse signature) ---------- */
const TAPE_SYMS = ['SPY', 'QQQ', 'NVDA', 'AAPL', 'AMD', 'TSLA', 'MSFT'];
async function loadTape() {
  try {
    const d = await J(`/api/bot/spark?symbols=${TAPE_SYMS.join(',')}`);
    const ticks = TAPE_SYMS.map(sym => {
      const s = d[sym];
      if (!s?.last || !s.closes?.length) return '';
      const prev = s.closes.at(-1) === s.last && s.closes.length > 1 ? s.closes.at(-2) : s.closes.at(-1);
      const pct = prev ? (s.last - prev) / prev * 100 : 0;
      // heat tint: the tape doubles as the sentiment strip (deeper tint = bigger move)
      const heat = Math.min(0.30, Math.abs(pct) * 0.10);
      const tint = heat > 0.015 ? `background: rgba(${pct >= 0 ? '34,197,94' : '239,68,68'},${heat.toFixed(2)});` : '';
      return `<span class="tick" style="${tint}"><span class="sym">${sym}</span><span>${fmt$(s.last)}</span>` +
        `<span class="chg ${cls(pct)}">${arrow(pct)}${Math.abs(pct).toFixed(2)}%</span></span>`;
    }).join('');
    if (ticks) $('#tapeTrack').innerHTML = ticks + ticks; /* duplicated for the seamless loop */
  } catch { /* tape is decoration; fail quiet */ }
}
/* tape pause: hover pauses, click pins the pause (persists across restarts) */
if (localStorage.getItem('tapePaused') === '1') $('#tape').classList.add('paused');
$('#tape').onclick = () => {
  const p = $('#tape').classList.toggle('paused');
  localStorage.setItem('tapePaused', p ? '1' : '0');
};

/* ---------- memory drawer (memory.md - the copilot's standing orders) ---------- */
let memLines = [];
async function loadMemory() {
  const txt = await (await fetch('/api/memory')).text().catch(() => '');
  memLines = txt.split('\n');
  const items = memLines.map((l, i) => ({ l, i })).filter(x => x.l.trim().startsWith('- '));
  $('#memList').innerHTML = items.length ? items.map(x =>
    `<div class="mem-item"><span>${esc(x.l.trim().slice(2))}</span><span class="x" data-i="${x.i}" title="remove">✕</span></div>`).join('')
    : '<div class="dim">no entries yet - add your first rule below</div>';
  $('#memBtn').classList.toggle('has', items.length > 0);
  document.querySelectorAll('.mem-item .x').forEach(el => el.onclick = async () => {
    memLines.splice(+el.dataset.i, 1);
    await saveMemory();
  });
}
async function saveMemory() {
  await J('/api/memory', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: memLines.join('\n') }) });
  loadMemory();
}
$('#memBtn').onclick = () => { $('#memDrawer').classList.toggle('hidden'); loadMemory(); };
$('#memClose').onclick = () => $('#memDrawer').classList.add('hidden');
$('#memAdd').onclick = async () => {
  const t = $('#memInput').value.trim();
  if (!t) return;
  memLines.push('- ' + t);
  $('#memInput').value = '';
  await saveMemory();
  notify('memory updated', 'ok', false);
};
$('#memInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#memAdd').click(); });

/* ---------- journal: the decision log + replay ---------- */
const JICONS = { order: '$', scan: '⟳', bridge: '✦', board: '⧉', chat: '💬', note: '✎' };
async function loadJournal(day = '') {
  const d = await J('/api/journal' + (day ? `?day=${day}` : '')).catch(() => null);
  if (!d) return;
  const sel = $('#jDay');
  const today = new Date().toISOString().slice(0, 10);
  const days = [...new Set([today, ...(d.days || [])])];
  sel.innerHTML = days.map(x => `<option value="${x}" ${x === d.day ? 'selected' : ''}>${x === today ? x + ' (today)' : x}</option>`).join('');
  $('#jTimeline').innerHTML = (d.entries || []).length ? d.entries.map(e =>
    `<div class="jrow"><span class="jt">${esc(String(e.ts).slice(11, 16))}</span>` +
    `<span class="jk ${esc(e.kind)}">${JICONS[e.kind] || '·'} ${esc(e.kind)}</span>` +
    `<span>${esc(e.text)}</span></div>`).join('')
    : '<div class="dim" style="padding:14px">nothing logged this day - the journal records from today forward (chats, scans, builds, orders)</div>';
}
$('#jDay').addEventListener('change', () => loadJournal($('#jDay').value));
$('#jReplay').onclick = () => {
  const day = $('#jDay').value || new Date().toISOString().slice(0, 10);
  palChat(`REPLAY ${day}: read journal.jsonl (entries for ${day}) plus the chat logs, cross-check /api/bot/orders, and reconstruct the day like git history for my decisions - what fired, what I took, what I skipped and why, how it ended. Reply with a 3-sentence spoken summary AND build ONE size:full story-board panel with the full timeline, decisions, outcomes, and one process lesson.`);
  notify(`replay of ${day} sent to copilot`, 'info');
};

/* ---------- pollers ---------- */
loadTape(); setInterval(loadTape, 60000);
vbHealth(); setInterval(vbHealth, 60000);
loadSavedList(); setInterval(loadSavedList, 30000);
loadMemory(); loadJournal(); setInterval(() => { if (document.querySelector('#view-journal.on')) loadJournal($('#jDay').value); }, 20000);
J('/api/meta').then(m => {
  window._user = m.user || 'trader';
  window.__mfUser = m.user || 'You';        // avatar initials in the chat
  initTheme(m.theme);                        // config default, if nothing saved locally
  setTimeout(() => { if ($('#ttsToggle').checked) speakText(`Welcome back, ${window._user}.`); }, 1200);
}).catch(() => {});
loadOverview(); loadChart(); loadRadar(); loadReddit(); loadPanels(true); loadRules(); loadChat(); loadNaked();
setInterval(loadOverview, 15000);
setInterval(loadNaked, 20000);   // an unarmed position must surface fast
setInterval(loadRadar, 30000);
setInterval(loadReddit, 60000);
setInterval(loadChat, 2500);
setInterval(loadPanels, 2500);
loadBridge(); setInterval(loadBridge, 2500);
