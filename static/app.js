/* STOCKS//LOCAL frontend. Vanilla JS, no build step - CC edits this live.
   Data flows: /api/bot/* (the bot engine, embedded or remote), /api/panels + /api/panel
   (Workbench file bus), /api/chat* (copilot bus). */
'use strict';
// Server-provided settings the UI needs at runtime, filled from /api/meta on
// boot. Declared HERE, above every consumer: as a const further down it sat in
// the temporal dead zone for anything that ran earlier, which was fine only
// because hot mic needs a click. Not a bet worth keeping.
const META = {};
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

/* ---------- shell awareness ----------
   Feature-detect, never shell-detect: /api/shell says what the host can do.
   The page must not know or care WHICH shell it is in beyond that - the same
   frontend runs in Chrome, pywebview, or anything else. */
let shellInfo = { shell: 'browser' };
function extOpen(url) {
  // In a webview an unhandled target=_blank either does nothing or traps you
  // in a chromeless window; route outbound links to the SYSTEM browser there.
  if (shellInfo.shell === 'browser') { window.open(url, '_blank'); return; }
  J('/api/shell/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }) }).catch(() => {});
}
function hookLinks(doc) {
  doc.addEventListener('click', (e) => {
    if (shellInfo.shell === 'browser') return;
    const a = e.target && e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    // Real URL parse, not a string prefix: "http://127.0.0.1:8410@evil.com"
    // serializes with our origin as USERINFO and beats a startsWith check,
    // and so does a port that merely extends ours (:84109).
    let u;
    try { u = new URL(a.href, location.href); } catch { return; }
    if (!/^https?:$/.test(u.protocol) || u.origin === location.origin) return;
    e.preventDefault(); e.stopPropagation();
    extOpen(u.href);
  }, true);
}
hookLinks(document);
(async () => {
  try { shellInfo = await J('/api/shell'); } catch {}
  // The packaged exe is windowless. In the browser lane there is no window and
  // no console to close, so without this button the only way to stop the desk
  // is Task Manager - while it still holds broker keys and a live engine.
  if (shellInfo.can_quit) {
    const b = $('#quitBtn');
    if (b) {
      b.classList.remove('hidden');
      b.onclick = async () => {
        if (!confirm('Stop Market Forge?\n\nThis shuts down the desk and the trading engine. '
          + 'Stops already armed at your broker keep working.')) return;
        const r = await fetch('/api/shell/quit', { method: 'POST' });
        const j = await r.json().catch(() => ({}));
        if (r.status === 409) {
          // The shutdown guard: an entry is working and would land unprotected.
          alert('Not stopping yet:\n\n' + (j.reasons || []).join('\n')
            + '\n\nLeave it running until the fill is protected.');
          return;
        }
        document.body.innerHTML = '<div style="padding:60px;font:16px system-ui;color:#9aa8bb">'
          + 'Market Forge stopped. You can close this tab.</div>';
      };
    }
  }
})();

/* ---------- splash ----------
   Mounted synchronously so there is no flash of desk first; every fetch below
   keeps running underneath it, so the desk is already populated when it lifts
   (a splash that gates loading just trades a logo for a spinner). Any click or
   key skips it. splash_ms in config.json tunes it; 0 disables (cached locally
   so a disabled splash never even mounts on later visits). */
(() => {
  const cached = Number(localStorage.getItem('mfSplashMs') ?? '2500');
  if (!cached) { J('/api/meta').then(m => localStorage.setItem('mfSplashMs', String(m.splash_ms ?? 2500))).catch(() => {}); return; }
  const el = document.createElement('div');
  el.id = 'splash';
  // the mark carries its own wordmark - no duplicate text under it
  el.innerHTML = '<img src="/static/logo.svg" alt="Market Forge">';
  document.body.appendChild(el);
  let gone = false;
  const lift = () => {
    if (gone) return; gone = true;
    el.classList.add('out');
    setTimeout(() => el.remove(), 500);
    removeEventListener('keydown', lift, true);
  };
  el.addEventListener('click', lift);
  addEventListener('keydown', lift, true);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const t0 = performance.now();
  let timer = setTimeout(lift, reduced ? 900 : cached);
  J('/api/meta').then(m => {
    const ms = Number(m.splash_ms ?? 2500);
    localStorage.setItem('mfSplashMs', String(ms));
    if (!ms) { lift(); return; }
    if (reduced) return;               // reduced-motion cut stays short
    clearTimeout(timer);
    timer = setTimeout(lift, Math.max(150, ms - (performance.now() - t0)));
  }).catch(() => {});
})();

/* ---------- tabs ---------- */
document.querySelectorAll('#tabs button').forEach((b) => b.onclick = () => {
  document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('on', x === b));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('on', v.id === 'view-' + b.dataset.tab));
  // admin is a snapshot, not a live poll - refresh it when you open the tab
  if (b.dataset.tab === 'admin') loadAdmin();
  // same for saved pages: read the directory on open, not on a timer
  if (b.dataset.tab === 'saved') loadSavedGrid();
  // the paper book is a separate broker account - fetch on open, then on demand
  if (b.dataset.tab === 'paper') loadPaper();
  // RADAR is sub-tabbed now; refresh whichever pane is showing.
  if (b.dataset.tab === 'radar') {
    const on = document.querySelector('#view-radar .subtabs button.on');
    const sub = on ? on.dataset.sub : 'catalyst';
    if (sub === 'retail') loadReddit();
    else if (sub === 'brief') loadBrief();
    else if (sub === 'scoring') loadScanlog();
    else loadRadar();
  }
  // A hidden element has no scrollHeight, so every scroll-to-bottom done while
  // the COPILOT tab was display:none silently did nothing and it opened at the
  // top. Scroll AFTER the tab is visible and laid out.
  if (b.dataset.tab === 'copilot') {
    requestAnimationFrame(() => {
      const l = $('#chatLog'); if (l) l.scrollTop = l.scrollHeight;
      const t = $('#chatInput'); if (t) t.focus();
    });
  }
});

/* Sub-tabs, SCOPED to their own section.
   This used to select '#subtabs button' and '.subview' globally, which was fine
   while ADMIN was the only group. RADAR is a second one, and a global selector
   would have made clicking Catalyst also switch the admin pane underneath.
   Now each nav only touches subviews inside its own <section>. */
document.querySelectorAll('nav.subtabs').forEach((nav) => {
  const scope = nav.closest('section') || document;
  nav.querySelectorAll('button').forEach((b) => b.onclick = () => {
    nav.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    scope.querySelectorAll(':scope > .subview').forEach(
      (v) => v.classList.toggle('on', v.id === 'sub-' + b.dataset.sub));
    if (b.dataset.sub === 'retail') loadReddit();
    if (b.dataset.sub === 'brief') loadBrief();
    if (b.dataset.sub === 'scoring') loadScanlog();
  });
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
  // Thin wrapper: the maths lives in chart-core.js, shared with the panel kit.
  // Keeps the old string-returning signature so existing callers are untouched.
  const r = MFChart.render(bars, { w, h, type });
  chartGeom = r.geom;
  return r.svg;
}

/* ---------- overview ---------- */
// Chart symbol precedence: whatever you looked at last (this machine) -> your
// biggest open position -> SPY. Opening on SPY when you are holding something is
// the wrong default: the thing you own is the thing you care about.
let chartSym = localStorage.getItem('mfChartSym') || 'SPY';
let chartPinned = !!localStorage.getItem('mfChartSym');
async function pickDefaultChart() {
  if (chartPinned) return;                       // a real choice always wins
  try {
    const pos = await J('/api/bot/positions').catch(() => []);
    if (!Array.isArray(pos) || !pos.length) return;
    const top = pos.slice().sort((a, b) =>
      Math.abs(Number(b.market_value) || 0) - Math.abs(Number(a.market_value) || 0))[0];
    if (top?.symbol && top.symbol !== chartSym) {
      chartSym = top.symbol;
      $('#chartSym').value = chartSym;
      loadChart();
    }
  } catch {}
}
/* Interactive chart: range switcher, candle/line toggle, and a crosshair that
   reports the real OHLC of the bar under the pointer. The maths mirrors candles()
   exactly - if you change padding or the price-axis width there, change it here. */
const CHART_RANGES = { '1M': 22, '3M': 90, '6M': 132, '1Y': 252 };
let chartRange = '3M', chartType = 'candle', chartBars = [], chartGeom = null;

function wireChartHover() {
  const svg = $('#bigChart').querySelector('svg'), read = $('#chartHover');
  if (!svg || !chartGeom || !chartBars.length) return;
  // No more "must match candles()" - the geometry comes back FROM the renderer.
  MFChart.attachCrosshair(svg, chartBars, chartGeom, (b) => {
    if (!b) { read.classList.remove('on'); return; }
    const up = b.c >= b.o;
    read.innerHTML = `<b>${esc(b.t || '')}</b> \u00b7 O ${fmt$(b.o)} H ${fmt$(b.h)} ` +
      `L ${fmt$(b.l)} <b class="${up ? 'up' : 'down'}">C ${fmt$(b.c)}</b>` +
      (b.v ? ` \u00b7 vol ${Number(b.v).toLocaleString()}` : '');
    read.classList.add('on');
  });
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
$('#chartSym').addEventListener('change', () => {
  chartSym = $('#chartSym').value.toUpperCase().trim() || 'SPY';
  chartPinned = true; localStorage.setItem('mfChartSym', chartSym);
  loadChart();
});
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

/* ---------- paper (the shadow book) ----------
   A SECOND broker account, not a mode switch. The desk can be STOCK_ENV=live and
   this tab still shows paper, because paper.py holds its own connection pinned to
   paper-api with the PK key pair.

   Why the tab exists: the live account is under the $2,000 Reg T minimum, so it
   cannot short. Every short setup the engine finds is unexecutable live. Routing
   plans here means those setups still produce a fill, a stop and a P/L to learn
   from instead of scrolling past. */
async function loadPaper() {
  const link = $('#paperLink');
  try {
    const o = await J('/api/bot/paper/overview');
    if (!o.ok) throw new Error(o.error || 'paper overview failed');

    link.classList.add('hidden');
    const short = o.can_short
      ? `<span class="up">shorting ENABLED</span>`
      : `<span class="warn">shorting OFF</span> (needs $2,000 equity + margin)`;
    $('#paperWhy').innerHTML =
      `This is a separate Alpaca <b>paper</b> account. The desk process is running `
      + `<b>${esc(o.process_env)}</b>; nothing on this tab touches real money.<br>`
      + `${short}. Plans placed through the copilot fill <b>here</b> automatically, `
      + `and the live ticket is only ever <i>staged</i> for you on OVERVIEW.`;

    const longV = o.long_market_value || 0, shortV = Math.abs(o.short_market_value || 0);
    $('#paperStatRow').innerHTML = [
      ['Paper equity', fmt$(o.equity), o.status || ''],
      ['Cash', fmt$(o.cash), ''],
      ['Buying power', fmt$(o.buying_power), o.account_type || ''],
      ['Long exposure', fmt$(longV), ''],
      ['Short exposure', fmt$(shortV), shortV ? 'the lane live cannot run' : 'none open'],
      ['Unprotected', String((o.unprotected || []).length),
        (o.unprotected || []).length ? 'no working exit' : 'all guarded',
        (o.unprotected || []).length ? 'warn' : ''],
    ].map(([l, v, sub, c]) => `<div class="stat"><div class="l">${l}</div><div class="v ${c || ''}">${v}</div><div class="dim">${sub || ''}</div></div>`).join('');

    // Same red bar as the live desk. A naked paper short teaches the wrong
    // lesson silently, so it gets the same treatment rather than a log line.
    if ((o.unprotected || []).length) {
      link.classList.remove('hidden');
      // A banner that only names the problem is what let six of these sit. Each
      // one gets a button. Fractional positions cannot take a trailing stop at
      // all, so say that instead of offering an action that will always fail.
      link.innerHTML = 'UNPROTECTED PAPER POSITIONS &mdash; no working exit:<br>'
        + o.unprotected.map(u => {
          const frac = Math.abs(u.qty) < 1;
          return `<span class="nakedrow">${esc(u.symbol)} <span class="dim">(${u.side}, `
            + `${u.qty}, needs a ${u.needs})</span> `
            + (frac
              ? `<span class="warn">FRACTIONAL &mdash; cannot be trailed, close by hand</span>`
              : `<button class="btn sm" onclick="paperProtect('${esc(u.symbol)}')">Protect</button>`)
            + `</span>`;
        }).join('<br>');
    }

    const pos = o.positions || [];
    $('#paperPosCount').textContent = `(${pos.length})`;
    $('#paperPositions').innerHTML = pos.length
      ? '<table><tr><th>sym</th><th>side</th><th class="r">qty</th><th class="r">entry</th><th class="r">now</th><th class="r">value</th><th class="r">P/L</th></tr>'
        + pos.map(p => `<tr><td><b>${esc(p.symbol)}</b></td>`
          + `<td class="${p.side === 'short' ? 'down' : ''}">${p.side}</td>`
          + `<td class="r">${p.qty}</td><td class="r">${fmt$(p.entry)}</td>`
          + `<td class="r">${fmt$(p.price)}</td><td class="r">${fmt$(p.value)}</td>`
          + `<td class="r ${cls(p.pl)}">${fmt$(p.pl)}</td></tr>`).join('') + '</table>'
      : '<span class="dim">no paper positions</span>';

    const ords = o.orders || [];
    $('#paperOrders').innerHTML = ords.length
      ? '<table><tr><th>sym</th><th>side</th><th>type</th><th class="r">qty</th><th>status</th></tr>'
        + ords.map(x => `<tr><td><b>${esc(x.symbol)}</b></td><td>${esc(x.side)}</td>`
          + `<td>${x.trail_percent ? 'trail ' + (+x.trail_percent).toFixed(0) + '%' : esc(String(x.type || '-').replace(/_/g, ' '))}</td>`
          + `<td class="r">${esc(x.qty)}</td><td class="dim">${esc(x.status)}</td></tr>`).join('') + '</table>'
      : '<span class="dim">no working orders</span>';
  } catch (e) {
    // Report the error we ACTUALLY got. The first version of this hard-coded
    // "check your keys", and when the real fault was app.py's BOT_GET allowlist
    // rejecting the proxy path, it sent Dustin to inspect a .env that was fine.
    // An error message that guesses at the cause is worse than no message.
    const msg = String(e.message || e);
    const keyish = /ALPACA|key|PK\b|secret/i.test(msg);
    const proxyish = /not allowed|404/i.test(msg);
    const unreachable = /unreachable|fetch|network|502/i.test(msg);
    let hint = '';
    if (keyish) hint = 'Check ALPACA_KEY_ID / ALPACA_SECRET_KEY in bot/.env (paper keys start with PK).';
    else if (proxyish) hint = 'The desk is refusing to proxy this path. Add it to BOT_GET in app.py, then restart the desk.';
    else if (unreachable) hint = 'The bot engine is not answering. Is it running?';
    else hint = 'Run <code>python bot\\src\\paper.py</code> to test the account directly.';
    link.classList.remove('hidden');
    link.innerHTML = 'PAPER TAB ERROR: ' + esc(msg) + '<br><span class="dim">' + hint + '</span>';
    $('#paperStatRow').innerHTML = '';
    $('#paperPositions').innerHTML = '<span class="dim">--</span>';
    $('#paperOrders').innerHTML = '<span class="dim">--</span>';
  }
}
document.addEventListener('click', (e) => {
  if (e.target && e.target.id === 'paperRefresh') loadPaper();
});

/* Arm a trailing stop on a naked PAPER position. */
async function paperProtect(symbol) {
  const pct = prompt(`Trailing stop for ${symbol} (percent off the best price):`, '4');
  if (pct === null) return;
  const n = Number(pct);
  if (!(n >= 0.5 && n <= 50)) { alert('Trail must be between 0.5 and 50 percent.'); return; }
  try {
    const r = await J('/api/bot/paper/protect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, trail_pct: n }),
    });
    if (!r.ok) throw new Error(r.error || 'protect failed');
    alert(`${symbol}: ${r.side} trailing stop armed at ${n}% on ${r.qty} shares.`);
  } catch (err) {
    alert(`${symbol}: ${err.message}`);
  }
  loadPaper();
}

async function loadOverview() {
  try {
    // Orders come from the BROKER, not the local sqlite ledger. The ledger only
    // knows what this app submitted and never learns what happened next, so it
    // showed AEVA as pending_new long after it had filled and been protected.
    // Falls back to the ledger if the broker call fails, so the panel degrades
    // to stale-but-something rather than empty.
    const [s, pos, ords] = await Promise.all([
      J('/api/bot/status'), J('/api/bot/positions').catch(() => []),
      J('/api/bot/broker/orders?status=all').catch(() => J('/api/bot/orders').catch(() => []))]);
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
    // A 502 from the broker returns {error}, not an array - guard before .length.
    const olist = Array.isArray(ords) ? ords : [];
    // "kind" makes a working exit legible at a glance: a trailing_stop with its
    // width is the single most reassuring row on this screen.
    const kind = o => {
      const t = String(o.type || o.order_type || '').replace(/_/g, ' ');
      if (o.trail_percent) return `trail ${(+o.trail_percent).toFixed(0)}%`;
      return t || '-';
    };
    const fillCol = o => (o.filled_qty != null && +o.filled_qty !== +o.qty)
      ? `${o.filled_qty}/${o.qty}` : (o.qty ?? '-');
    $('#orders').innerHTML = olist.length ? '<table><tr><th>sym</th><th>side</th><th>kind</th>' +
      '<th class="r">qty</th><th>status</th><th class="r">at</th></tr>' +
      olist.slice(0, 10).map(o => {
        const st = String(o.status || '');
        const stc = st === 'filled' ? 'gain' : (/cancel|reject|expired/.test(st) ? 'dim' : '');
        return `<tr><td><b>${esc(o.symbol)}</b></td><td>${esc(o.side)}</td>` +
          `<td class="dim">${esc(kind(o))}</td><td class="r">${esc(String(fillCol(o)))}</td>` +
          `<td class="${stc}">${esc(st)}</td><td class="r dim">` +
          `${esc((o.submitted_at || o.updated_at || o.created_at || '').slice(5, 16).replace('T', ' '))}</td></tr>`;
      }).join('') + '</table>'
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
        // The chip used to report which feed was CONFIGURED, which is not the
        // question. The question is whether the number on screen is current.
        // On the free plan REST cannot return the last 15 minutes at all, so
        // without the live tap running, everything here is >=15 min old.
        const tap = await J('/api/live').catch(() => null);
        const live = tap && tap.connected && (tap.fresh_count || 0) > 0;
        if (rt) {
          fc.textContent = 'data: REAL-TIME (SIP)';
          fc.title = 'Full consolidated tape, real time.';
        } else if (live) {
          fc.textContent = `data: LIVE tap · ${tap.fresh_count} sym`;
          fc.title = `IEX websocket connected: ${tap.fresh_count} symbol(s) with a fresh print.\n`
            + `Everything NOT in the tap still comes from REST, which on the free plan\n`
            + `is blind to the last 15 minutes. IEX is ~2% of volume, so a quiet name\n`
            + `may simply not print.`;
        } else {
          fc.textContent = 'data: IEX · 15-MIN DELAYED';
          fc.title = 'The free plan\'s REST API cannot return the latest 15 minutes.\n'
            + 'Every price on this screen is at least that old.\n\n'
            + 'Fix (free): pip install websocket-client, then run\n'
            + '  python bot/src/stream.py\n'
            + 'to tap real-time IEX for up to 30 symbols.';
        }
        fc.style.color = rt || live ? 'var(--gain)' : 'var(--warn, var(--loss))';
        fc.style.borderColor = rt || live ? 'rgba(74,222,128,.45)'
                                          : 'color-mix(in srgb, var(--warn, var(--loss)) 45%, transparent)';
      }
      window._cfg = cfg;
    }
  } catch (e) { $('#statRow').innerHTML = `<div class="stat"><div class="l">bot</div><div class="v down">unreachable</div><div class="dim">${esc(e.message)}</div></div>`; }
}

/* ---------- radar ---------- */
/* ---------- brief: what changed ---------- */
const SEV_ORDER = { high: 0, medium: 1, low: 2 };
async function loadBrief() {
  const box = $('#briefBody'); if (!box) return;
  const d = await J('/api/bot/changed').catch(() => null);
  if (!d) { box.innerHTML = '<span class="dim">engine unreachable</span>'; return; }
  $('#briefTs').textContent = d.ts ? `as of ${String(d.ts).slice(5, 16).replace('T', ' ')}` : '';
  if (!d.ts) {
    box.innerHTML = '<span class="dim">No brief yet. Hit "Brief me", or turn on '
      + 'the schedule with job=brief.</span>';
    return;
  }
  if (d.quiet) {
    // Deliberately says nothing rather than manufacturing an update. A brief
    // that fires every 5 minutes saying "no change" trains you to ignore it.
    box.innerHTML = '<div class="brief-quiet">Nothing has changed since the last '
      + 'brief. No regime shift, no position change, no new catalysts over your '
      + 'score threshold.</div>';
    return;
  }
  const ch = [...(d.changes || [])].sort(
    (a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  box.innerHTML =
    (d.text ? `<div class="brief-text">${esc(d.text)}</div>` : '')
    + ch.map(c => `<div class="brief-row ${esc(c.severity)}">`
      + `<b>${esc(String(c.kind).toUpperCase())}</b> ${esc(c.text)}</div>`).join('')
    + (d.facts && d.facts.live_note
      ? `<div class="dim" style="margin-top:8px">${esc(d.facts.live_note)}</div>` : '');
}
document.addEventListener('click', async (e) => {
  if (!e.target || e.target.id !== 'briefRun') return;
  e.target.disabled = true; e.target.textContent = 'Thinking...';
  try {
    await J('/api/bot/run/changed', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch (err) { notify('brief failed: ' + err.message, 'err'); }
  e.target.disabled = false; e.target.textContent = 'Brief me';
  loadBrief();
});

/* ---------- scoring log: what got REJECTED and why ---------- */
async function loadScanlog() {
  const box = $('#scanlogBody'); if (!box) return;
  const d = await J('/api/bot/scanlog').catch(() => null);
  const rows = (d && d.rows) || [];
  $('#scanlogTs').textContent = d && d.finished
    ? `scan ${String(d.finished).slice(5, 16).replace('T', ' ')}` : '';
  $('#scanlogCount').textContent = d && d.rows
    ? `(${d.alerted} alerted · ${d.skipped} skipped)` : '';
  if (!rows.length) {
    box.innerHTML = `<span class="dim">${esc((d && d.note) || 'no scan log yet')}</span>`;
    return;
  }
  const cell = (r) => {
    const bits = [];
    if (r.pct != null) bits.push(`${Number(r.pct).toFixed(1)}%`);
    if (r.price != null) bits.push(fmt$(r.price));
    if (r.dollar_volume) bits.push(`$${(r.dollar_volume / 1e6).toFixed(1)}M adv`);
    return bits.join(' · ');
  };
  // Alerted first, then the rejects - the rejects are the point of this view.
  const ordered = [...rows].sort((a, b) =>
    (a.decision === 'alerted' ? 0 : 1) - (b.decision === 'alerted' ? 0 : 1));
  box.innerHTML = '<table><tr><th>sym</th><th></th><th>numbers</th><th>reason</th></tr>'
    + ordered.map(r => `<tr class="log-${esc(r.decision)}">`
      + `<td><b>${esc(r.symbol)}</b></td>`
      + `<td>${r.decision === 'alerted'
        ? `<span class="up">alerted${r.score != null ? ' ' + r.score : ''}</span>`
        : '<span class="dim">skipped</span>'}</td>`
      + `<td class="dim">${esc(cell(r))}</td>`
      + `<td class="dim">${esc(r.reason || '')}</td></tr>`).join('')
    + '</table>';
}
document.addEventListener('click', (e) => {
  if (e.target && e.target.id === 'scanlogRefresh') loadScanlog();
});

/* Supply chip for a radar card.
   A catalyst is DEMAND; share count is SUPPLY. "micro" next to a +45% move is
   the single most useful thing on the card, and its absence used to be silent.
   Says OUTSTANDING, not float, because that is what SEC gives - float subtracts
   insider/restricted shares and needs a paid source. */
const SUPPLY_HINT = {
  micro: 'tiny supply - a real catalyst moves this violently, and it squeezes',
  small: 'small supply - catalysts have real leverage here',
  mid: 'moderate supply',
  large: 'big supply - a headline moves this less',
  mega: 'enormous supply - a headline is a rounding error',
  not_a_filer: 'No SEC company filing. Usually an ETF or ETP, including '
    + 'leveraged single-stock funds. This is a PRODUCT tracking something else, '
    + 'not a company - so the move you are looking at may be 2x or 3x someone '
    + 'else\'s news, with decay and an expense ratio attached.',
  unknown: 'SEC filer, but no share count came back (lookup failed)',
};
function supplyChip(r) {
  const c = r.supply_class || 'unknown';
  if (c === 'not_a_filer') {
    return `<div class="supply notafiler" title="${esc(SUPPLY_HINT.not_a_filer)}">`
      + `<b>ETF / ETP?</b><span class="dim"> not an SEC filer</span></div>`;
  }
  if (c === 'unknown' && r.shares_millions == null) {
    return `<div class="supply unknown" title="${esc(SUPPLY_HINT.unknown)}">supply: unknown</div>`;
  }
  const m = r.shares_millions;
  const txt = m >= 1000 ? `${(m / 1000).toFixed(1)}B` : `${m}M`;
  return `<div class="supply ${esc(c)}" title="${esc(SUPPLY_HINT[c] || '')} · shares OUTSTANDING (not free float) as of ${esc(r.shares_as_of || '?')}">`
    + `<b>${esc(c.toUpperCase())}</b> ${txt} shares`
    + `<span class="dim"> outstanding</span></div>`;
}

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
        ${supplyChip(r)}
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
  try {
    // fetch does NOT throw on 4xx/5xx, so this used to toast "complete" for a
    // failed scan. Check the status AND the payload, and surface the real error.
    const res = await fetch('/api/bot/run/radar', { method: 'POST' });
    const r = await res.json().catch(() => ({}));
    await loadRadar();
    if (!res.ok || r.ok === false) {
      const why = r.error || `HTTP ${res.status}`;
      notify(`radar re-scan FAILED: ${why}`, 'err');
      console.error('[radar]', r.stdout || why);
    } else {
      const n = (r.stdout || '').match(/(\d+)\s+alert/i);
      notify(`radar re-scan complete${n ? ` · ${n[1]} alert(s)` : ''}`, 'ok');
    }
  } catch (e) { notify(`radar re-scan failed: ${e.message}`, 'err'); }
  $('#radarRefresh').textContent = 'Re-scan';
};
window.chartTo = (sym) => {
  chartSym = sym; $('#chartSym').value = sym;
  chartPinned = true; localStorage.setItem('mfChartSym', sym);
  document.querySelector('[data-tab=overview]').click(); loadChart();
};

/* ---------- retail (reddit) ---------- */
/* ---------- scheduled scans ----------
   In-process timer, not a Windows task: a scan with no desk running has nowhere
   to write. Every run lands in journal.jsonl, which is the whole point - the ask
   was "I can see where it's logged". */
async function loadSchedule() {
  const d = await J('/api/schedule').catch(() => null);
  if (!d) return;
  const on = $('#schedOn'), min = $('#schedMin'), rth = $('#schedRth'), job = $('#schedJob');
  if (on) on.checked = !!d.enabled;
  if (min) min.value = d.every_min || 30;
  if (rth) rth.checked = d.market_hours_only !== false;
  if (job) job.value = d.job || 'radar';
  const st = $('#schedState');
  if (st) {
    st.innerHTML = d.enabled
      ? `<b class="up">ON</b> · ${esc(d.job || 'radar')} every ${d.every_min}m`
        + (d.last_run ? ` · last ${esc(String(d.last_run).slice(5, 16).replace('T', ' '))} (${esc(d.last_result || '')})` : ' · no run yet')
        + (d.market_hours_only && !d.market_open_now ? ' · <b class="warn">market closed, skipping</b>' : '')
      : '<b class="dim">OFF</b>';
  }
  const runs = $('#schedRuns');
  if (runs) {
    runs.innerHTML = (d.runs || []).length
      ? '<table><tr><th>when</th><th>job</th><th>result</th></tr>'
        + d.runs.map(r => `<tr><td class="mono">${esc(String(r.ts).slice(5, 16).replace('T', ' '))}</td>`
          + `<td>${esc(r.job)}</td><td class="dim">${esc(r.result)}</td></tr>`).join('')
        + '</table>'
      : '<span class="dim">no scheduled runs yet this session</span>';
  }
}
async function saveSchedule() {
  const body = {
    enabled: $('#schedOn').checked,
    every_min: Number($('#schedMin').value) || 30,
    market_hours_only: $('#schedRth').checked,
    // The "changed" job existed with no way to pick it - built and unreachable.
    job: ($('#schedJob') || {}).value || 'radar',
  };
  const r = await J('/api/schedule', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .catch((e) => ({ ok: false, error: e.message }));
  notify(r.ok ? (body.enabled ? `${body.job} every ${body.every_min}m - logged to the journal`
                              : 'scheduled runs off')
              : `schedule failed: ${r.error}`, r.ok ? 'ok' : 'err');
  loadSchedule();
}
document.addEventListener('click', (e) => {
  if (!e.target) return;
  if (e.target.id === 'schedBtn') { $('#schedBox').classList.toggle('hidden'); loadSchedule(); }
  if (e.target.id === 'schedSave') saveSchedule();
  if (e.target.id === 'retailRefresh') loadReddit();
});

const ago = (s) => s == null ? '' :
  s < 60 ? `${Math.round(s)}s ago` :
  s < 3600 ? `${Math.round(s / 60)}m ago` :
  s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;

async function loadReddit() {
  try {
    const d = await J('/api/bot/reddit');
    const now = Date.now() / 1000;

    // Freshness, per sub. The round-robin refreshes ONE sub per pass, so a
    // single "updated" time lies: one sub can be an hour stale while another is
    // seconds old. Mark anything past its cache window.
    const fetched = d.sub_fetched || {};
    const win = d.cache_secs || 600;
    $('#redditSubs').innerHTML = Object.keys(fetched).length
      ? Object.entries(fetched).map(([s, t]) => {
          const age = t ? now - t : null;
          const stale = age == null || age > win;
          return `<span class="subchip ${stale ? 'stale' : 'fresh'}" `
            + `title="${age == null ? 'never fetched' : ago(age)}">r/${esc(s)}`
            + `<b>${age == null ? 'never' : ago(age)}</b></span>`;
        }).join('')
      : (d.subs || []).map(s => 'r/' + s).join(' · ');
    $('#redditUpdated').textContent = d.generated
      ? `scan ${ago(now - d.generated)}` : '';

    const list = d.trending || [];
    $('#redditCount').textContent = `(${list.length})`;
    // Live price, same call the catalyst radar uses, so a buzz name shows what
    // it has done SINCE it started trending rather than a stale snapshot.
    const syms = [...new Set(list.map(t => t.symbol))].slice(0, 16).join(',');
    const spark = syms ? await J(`/api/bot/spark?symbols=${syms}`).catch(() => ({})) : {};
    const maxW = Math.max(1, ...list.map(t => t.weight || 0));

    // "% since buzz" was +0.0% on EVERY card, always. Both numbers come from the
    // same delayed REST source moments apart: reddit.py stamps `price` from
    // get_latest_price(), and /api/spark's `last` is the same call. On the free
    // plan that value cannot move inside the 120s payload cache, so the delta is
    // structurally zero and the chip was decoration pretending to be data.
    // Only show it when the two prices came from DIFFERENT sources - i.e. when
    // the live tap has a fresh print for that symbol.
    const tap = await J('/api/live').catch(() => null);
    const livePrices = (tap && tap.prices) || {};

    $('#reddit').innerHTML = list.length ? list.map((t, i) => {
      const tapped = livePrices[t.symbol];
      const live = (tapped && tapped.fresh) ? tapped.price : null;
      const since = live != null && t.price ? ((live - t.price) / t.price * 100) : null;
      const heat = Math.round(((t.weight || 0) / maxW) * 100);
      const subs = [...new Set((t.posts || []).map(p => p.sub))].slice(0, 3);
      return `<div class="card ${i === 0 ? 'signal' : ''}">
        <div class="head"><span class="sym">${esc(t.symbol)}</span>
          ${since != null
            ? `<span class="pct ${since >= 0 ? 'up' : 'down'} ${pctScale(since)}">${arrow(since)}${since >= 0 ? '+' : ''}${since.toFixed(1)}%</span>`
            : ''}
          ${t.price != null ? `<span class="dim">buzz @ ${fmt$(t.price)}</span>` : ''}
          <span class="score ${heat >= 70 ? 'hi' : heat >= 40 ? 'mid' : ''}">${t.mentions}</span></div>
        ${live != null
          ? `<div class="livechip"><span class="p"></span>now ${fmt$(live)}${since != null ? ` <b class="${cls(since)}">${since >= 0 ? '+' : ''}${since.toFixed(1)}% since buzz</b>` : ''}</div>`
          : `<div class="livechip dim" title="No fresh print in the live tap. The buzz price and any 'now' price would both come from the same 15-minute-delayed call, so a % between them is always 0 and means nothing."><span class="p off"></span>no live print &mdash; delayed only</div>`}
        <div class="heatbar" title="mention weight ${t.weight}"><i style="width:${heat}%"></i></div>
        <div class="dim" style="color:var(--accent);font-size:10px;text-transform:uppercase">
          ${t.mentions} mention${t.mentions === 1 ? '' : 's'} · ${subs.map(s => 'r/' + esc(s)).join(' ')}</div>
        ${(t.posts || []).slice(0, 2).map(p => `<a href="${esc(p.url)}" target="_blank">r/${esc(p.sub)} #${p.rank ?? '?'} · ${esc(p.title)}</a>`).join('')}
        <div class="foot"><span class="dim">rank ${i + 1} of ${list.length}</span>
          <button class="btn sm right" onclick="openTicket('${esc(t.symbol)}','buy')">Trade</button>
          <button class="btn sm" style="margin-left:6px" onclick="chartTo('${esc(t.symbol)}')">Chart</button></div></div>`;
    }).join('')
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
      `<button class="wb-savepage" title="save just this tile as its own page">⇩ page</button>` +
      `<button class="wb-del" title="remove this tile from the board (recoverable in _trash)">✕</button>` +
      `<button class="wb-max" title="expand to full screen (Esc to close)">⤢</button></div>`;
    // Remove a tile from the live board without opening the folder. Copied to
    // _trash first, so this is undoable from disk.
    card.querySelector('.wb-del').onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Remove "${p.title || p.name}" from the board?\n\nA copy goes to saved-workbenches/_trash/ so it can be recovered.`)) return;
      const r = await J('/api/panels/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ panel: p.name }),
      }).catch(() => ({}));
      notify(r.ok ? `panel removed: ${p.name} (in _trash)` : `remove failed: ${r.error || '?'}`,
             r.ok ? 'ok' : 'err');
      if (r.ok) loadPanels(true);
    };
    card.querySelector('.wb-max').onclick = (e) => {
      e.stopPropagation();
      const on = card.classList.toggle('maximized');
      document.body.classList.toggle('has-max', on);
      e.target.textContent = on ? '✕' : '⤢';
    };
    // Save ONE tile as its own page. A deep-dive dossier deserves its own
    // surface instead of being one card among four on a shared board.
    card.querySelector('.wb-savepage').onclick = async (e) => {
      e.stopPropagation();
      const btn = e.target;
      const name = prompt('Save this tile as its own page.\nPage name:', p.title || p.name.replace(/\.html$/, ''));
      if (name === null) return;
      btn.disabled = true;
      try {
        const r = await J('/api/workbench/save-panel', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ panel: p.name, name: name.trim() }),
        });
        btn.textContent = r.ok ? '✓ saved' : 'failed';
        notify(r.ok ? `page saved: ${r.name}` : `save failed: ${r.error || '?'}`, r.ok ? 'ok' : 'err');
        if (r.ok) { loadSavedList(); loadSavedGrid(); }
      } catch (err) {
        btn.textContent = 'failed';
        notify(`save failed: ${err.message}`, 'err');
      }
      setTimeout(() => { btn.textContent = '⇩ page'; btn.disabled = false; }, 2200);
    };
    const fr = document.createElement('iframe');
    fr.sandbox = 'allow-scripts allow-same-origin allow-popups';
    fr.src = `/api/panel?name=${encodeURIComponent(p.name)}&v=${p.mtime}`;
    // an iframe is its own document and does NOT inherit [data-theme]; it also
    // needs the external-link hook or a panel's news links die inside a shell
    fr.onload = () => { try {
      fr.contentDocument.documentElement.dataset.theme = document.documentElement.dataset.theme;
      hookLinks(fr.contentDocument);
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

/* ---------- SAVED pages: tile browser + reader overlay ----------
   Clicking a tile READS the page in an overlay. It deliberately does not touch
   the live workbench - "open the Ariel report" should never cost you the board
   you are working on. Loading onto the workbench is a separate, explicit click. */
let savedCache = [];
function svFmt(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000), now = Date.now() / 1000;
  const mins = Math.round((now - ts) / 60);
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return d.toISOString().slice(0, 10);
}
async function loadSavedGrid() {
  const grid = $('#savedGrid');
  if (!grid) return;
  const d = await J('/api/workbench/saved').catch(() => ({ saved: [] }));
  savedCache = d.saved || [];
  const showAuto = $('#svShowAuto')?.checked;
  const rows = savedCache.filter((s) => showAuto || !s.auto);
  $('#svCount').textContent = `${rows.length} page(s).` +
    (savedCache.length - rows.length ? ` ${savedCache.length - rows.length} autosave(s) hidden.` : '');
  if (!rows.length) {
    grid.innerHTML = '<div class="panel dim">No saved pages yet. On the WORKBENCH, ' +
      'hit <code>⇩ page</code> on any tile to save it as its own page.</div>';
    return;
  }
  grid.innerHTML = rows.map((s) => {
    const titles = (s.titles || []).map((t) => `<span>· ${esc(t)}</span>`).join('') ||
      '<span class="dim">· (empty)</span>';
    return `<div class="sv-tile${s.auto ? ' auto' : ''}" data-board="${esc(s.name)}">
      <button class="sv-del" data-del="${esc(s.name)}" title="delete this page (recoverable in _trash)">✕</button>
      <div class="sv-tile-h">${esc(s.name)}</div>
      <div class="sv-tile-titles">${titles}</div>
      <div class="sv-tile-f">
        <span class="sv-chip n">${s.panels} panel${s.panels === 1 ? '' : 's'}</span>
        <span class="sv-chip">${esc(svFmt(s.ts))}</span>
        ${s.auto ? '<span class="sv-chip">auto</span>' : ''}
      </div></div>`;
  }).join('');
  grid.querySelectorAll('.sv-tile').forEach((t) => {
    t.onclick = () => openSavedReader(t.dataset.board);
  });
  grid.querySelectorAll('.sv-del').forEach((b) => {
    b.onclick = async (e) => {
      e.stopPropagation();               // do not open the reader
      const name = b.dataset.del;
      if (!confirm(`Delete saved page "${name}"?\n\nIt moves to saved-workbenches/_trash/ and can be recovered from the folder.`)) return;
      const r = await J('/api/workbench/delete-saved', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }).catch(() => ({}));
      notify(r.ok ? `page deleted: ${name} (in _trash)` : `delete failed: ${r.error || '?'}`,
             r.ok ? 'ok' : 'err');
      if (r.ok) { loadSavedGrid(); loadSavedList(); }
    };
  });
}
function openSavedReader(board) {
  const s = savedCache.find((x) => x.name === board);
  if (!s) return;
  const body = $('#svReaderBody');
  body.className = 'sv-reader-body' + ((s.files || []).length > 1 ? ' multi' : '');
  body.innerHTML = '';
  for (const f of (s.files || [])) {
    const fr = document.createElement('iframe');
    fr.sandbox = 'allow-scripts allow-same-origin allow-popups';
    fr.src = `/api/saved/panel?board=${encodeURIComponent(board)}&name=${encodeURIComponent(f.name)}&v=${f.mtime}`;
    fr.onload = () => { try {
      fr.contentDocument.documentElement.dataset.theme = document.documentElement.dataset.theme;
      hookLinks(fr.contentDocument);   // saved pages carry links too
    } catch {} };
    body.appendChild(fr);
  }
  if (!(s.files || []).length) body.innerHTML = '<div class="panel dim">This page has no panels.</div>';
  $('#svReaderTitle').textContent = board;
  $('#svReaderMeta').textContent = `${s.panels} panel(s) · ${svFmt(s.ts)}`;
  $('#svReaderLoad').dataset.board = board;
  $('#svReader').classList.add('on');
  document.body.classList.add('has-reader');
}
function closeSavedReader() {
  $('#svReader')?.classList.remove('on');
  document.body.classList.remove('has-reader');
  const b = $('#svReaderBody'); if (b) b.innerHTML = '';   // stop iframe timers
}
$('#svReaderClose') && ($('#svReaderClose').onclick = closeSavedReader);
$('#svRefresh') && ($('#svRefresh').onclick = loadSavedGrid);
$('#svShowAuto') && ($('#svShowAuto').onchange = loadSavedGrid);
$('#svReaderLoad') && ($('#svReaderLoad').onclick = async (e) => {
  const board = e.target.dataset.board;
  if (!board) return;
  if (!confirm(`Load "${board}" onto the live workbench?\n\nThe current board is autosaved first.`)) return;
  const r = await J('/api/workbench/load', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: board }),
  }).catch(() => ({}));
  if (r.ok) {
    notify(`board loaded: ${board} · previous autosaved as ${r.previous_saved_as}`, 'ok');
    closeSavedReader(); loadPanels(true); loadSavedList(); loadSavedGrid();
    tabTo('workbench');
  } else notify('board load failed', 'err');
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('#svReader')?.classList.contains('on')) closeSavedReader();
});

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

// Conversations are grouped by DAY. chatDay '' means today (the live one).
// A new day therefore opens an empty chat by itself - nothing gets deleted.
let chatDay = '';
function renderDays(d) {
  const box = $('#chatDays'); if (!box) return;
  const today = d.today, days = d.days || [];
  const label = (x) => {
    if (x === today) return 'Today';
    const y = new Date(Date.parse(today + 'T00:00:00') - 86400000).toISOString().slice(0, 10);
    if (x === y) return 'Yesterday';
    return new Date(x + 'T00:00:00').toLocaleDateString(undefined,
      { month: 'short', day: 'numeric' });
  };
  const cur = chatDay || today;
  box.innerHTML = days.map(s =>
    `<button class="daybtn ${s.day === cur ? 'on' : ''}" data-day="${s.day}">` +
    `<b>${label(s.day)} <span style="float:right;opacity:.55;font-weight:400">${s.n}</span></b>` +
    `<span>${esc(s.preview || '...')}</span></button>`).join('') ||
    '<div class="dim" style="padding:8px 11px;font-size:12.5px">no history yet</div>';
  box.querySelectorAll('.daybtn').forEach(b => b.onclick = () => {
    chatDay = b.dataset.day === today ? '' : b.dataset.day;
    chatCount = -1; chatInit = false;   // force a full re-render + scroll to end
    loadChat();
  });
}

async function loadChat() {
  const d = await J('/api/chat' + (chatDay ? `?day=${chatDay}` : '')).catch(() => null);
  if (!d) return;
  renderDays(d);
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

/* ---------- scan settings ----------
   What each radar looks at, and how often. Lives ON the radar tabs, because
   that is where you are standing when you wonder why nothing is showing - and
   the usual answer is a floor set higher than the day is moving.
   Applying rewrites bot/.env and restarts the engine, so it refuses while an
   entry is working, same as stopping the desk. */
const SCAN_FIELDS = {
  catalyst: [
    ['RADAR_SCAN_HOURS', 'Scan hours (ET)', 'text', '10,12,14,16',
      'When the radar runs, weekdays. Comma separated, 0-23.'],
    ['RADAR_MIN_MOVE_PCT', 'Min move %', 'number', '5',
      'How big a move has to be before it is looked at. Raise on wild days, lower on quiet ones.'],
    ['RADAR_TOP_N', 'Movers per scan', 'number', '20',
      'How many names come back from the screener each pass.'],
    ['RADAR_MIN_PRICE_CENTS', 'Price floor (cents)', 'number', '300',
      'Skip anything under this. The sub-$3 tier is mostly halts and spikes.'],
    ['RADAR_LLM_MIN_SCORE', 'Alert score floor', 'number', '60',
      'Only push alerts at or above this 0-100 catalyst score.'],
  ],
  retail: [
    ['RADAR_REDDIT_SUBS', 'Subreddits', 'text', 'wallstreetbets,swingtrading,stocks',
      'Comma separated, no r/ prefix needed.'],
    ['RADAR_REDDIT_CACHE_SECS', 'Refresh (seconds)', 'number', '600',
      'One sub refreshes per cycle, round-robin, so the full sweep takes this times the number of subs.'],
    ['RADAR_REDDIT_ENABLED', 'Enabled', 'bool', 'true',
      'Turn the retail layer off entirely.'],
  ],
};

async function renderScan(which) {
  const box = $(which === 'catalyst' ? '#scanCatalyst' : '#scanRetail');
  if (!box) return;
  const cfg = await J('/api/bot/config').catch(() => ({}));
  const cur = {
    RADAR_SCAN_HOURS: (cfg.radar_scan_hours || []).join(',') || '10,12,14,16',
    RADAR_MIN_MOVE_PCT: cfg.radar_min_move_pct,
    RADAR_TOP_N: cfg.radar_top_n,
    RADAR_MIN_PRICE_CENTS: cfg.radar_min_price_cents,
    RADAR_LLM_MIN_SCORE: cfg.radar_llm_min_score,
    RADAR_REDDIT_SUBS: (cfg.reddit_subs || []).join(','),
    RADAR_REDDIT_CACHE_SECS: cfg.reddit_cache_secs,
    RADAR_REDDIT_ENABLED: cfg.reddit_enabled,
  };
  box.innerHTML = `<div class="scan-grid">` + SCAN_FIELDS[which].map(([k, label, type, dflt, help]) => {
    const v = cur[k] ?? dflt;
    const input = type === 'bool'
      ? `<select data-k="${k}"><option value="true"${String(v) === 'true' ? ' selected' : ''}>on</option>` +
        `<option value="false"${String(v) === 'false' ? ' selected' : ''}>off</option></select>`
      : `<input data-k="${k}" type="${type}" value="${esc(String(v))}" ${type === 'number' ? 'step="any"' : ''}>`;
    return `<label class="scan-f"><span class="scan-l">${esc(label)}</span>${input}
      <span class="scan-h">${esc(help)}</span></label>`;
  }).join('') + `</div>
    <div class="scan-act"><span class="scan-msg dim"></span>
      <button class="btn" data-reload>Reload</button>
      <button class="btn side on" data-apply>Apply + restart engine</button></div>`;

  box.querySelector('[data-reload]').onclick = () => renderScan(which);
  box.querySelector('[data-apply]').onclick = async () => {
    const msg = box.querySelector('.scan-msg');
    const settings = {};
    box.querySelectorAll('[data-k]').forEach(el => settings[el.dataset.k] = el.value);
    msg.textContent = 'applying...'; msg.style.color = '';
    const r = await fetch('/api/scan-settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings })
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      // 409 means an entry is working. Say so plainly rather than "failed".
      msg.textContent = (j.reasons || j.errors || [j.error || 'failed']).join(' · ');
      msg.style.color = 'var(--loss)';
      return;
    }
    msg.textContent = ''; notify(j.note || 'scan settings applied', 'ok');
    setTimeout(() => renderScan(which), 2500);   // let the engine come back up
  };
}

function toggleScan(which) {
  const box = $(which === 'catalyst' ? '#scanCatalyst' : '#scanRetail');
  if (!box) return;
  const showing = !box.classList.contains('hidden');
  box.classList.toggle('hidden', showing);
  if (!showing) renderScan(which);
}

/* ---------- staged trade ----------
   The copilot WRITES staged-trade.json; it never places the order. This panel
   is the whole stage-and-confirm contract made visible: it proposes with its
   reasoning attached, and the trade does not exist until you click.
   Before this existed the copilot improvised a trade button inside a Workbench
   panel, which was a correct read of a missing feature. */
async function loadStaged() {
  const panel = $('#stagedPanel'), box = $('#staged');
  if (!panel || !box) return;
  const d = await J('/api/staged').catch(() => null);
  const t = d && d.staged;
  if (!t) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');
  const side = String(t.side || 'buy').toLowerCase();
  const size = t.notional != null ? fmt$(t.notional, 0) : `${t.qty} sh`;
  const trail = t.trail_pct != null ? `${(+t.trail_pct).toFixed(0)}%` : 'none';
  const mins = Math.round((t.age_s || 0) / 60);
  box.innerHTML = `
    <div class="stg-head">
      <span class="stg-side ${side}">${esc(side.toUpperCase())}</span>
      <b class="mono stg-sym">${esc(t.symbol)}</b>
      <span class="dim mono">${esc(size)} · trail ${esc(trail)}</span>
      <span class="right dim">staged ${mins}m ago</span>
    </div>
    ${t.why ? `<div class="stg-why">${esc(t.why)}</div>` : ''}
    ${(t.advisories || []).map(a => `<div class="stg-adv ${esc(a.severity)}">`
      + `${a.severity === 'danger' ? '!! ' : a.severity === 'caution' ? '! ' : ''}`
      + `${esc(a.message)}</div>`).join('')}
    ${(t.advisories || []).length
      ? `<div class="dim stg-adv-foot">Notices only. Nothing here blocks the
         trade &mdash; it is staged and ready for your review.</div>`
      : ''}
    ${t.expired
      ? `<div class="stg-stale">This was staged ${mins} minutes ago and has expired.
         Prices have moved; ask for a fresh read before acting.</div>`
      : ''}
    <div class="stg-act">
      <button class="btn" onclick="dismissStaged()">Dismiss</button>
      ${t.expired
        ? `<button class="btn" onclick="chartTo('${esc(t.symbol)}')">Chart</button>`
        : `<button class="btn side on" onclick="confirmStaged()">Confirm ${esc(side)} ${esc(t.symbol)}</button>`}
    </div>`;
  window.__staged = t;
}

async function dismissStaged() {
  await fetch('/api/staged/clear', { method: 'POST' }).catch(() => {});
  loadStaged();
}

async function confirmStaged() {
  const t = window.__staged;
  if (!t) return;
  // Re-open the normal ticket rather than firing straight from the card. The
  // ticket is where size and the trail get confirmed against a CURRENT quote,
  // and one extra deliberate click before real money is the right price.
  openTicket(t.symbol, String(t.side || 'buy').toLowerCase());
  const n = $('#tkNotional'), q = $('#tkQty'), tr = $('#tkTrail');
  if (n && t.notional != null) n.value = t.notional;
  if (q && t.qty != null) q.value = t.qty;
  if (tr && t.trail_pct != null) tr.value = t.trail_pct;
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
  loadNaked(); loadStaged(); loadOverview();
};

/* ---------- ADMIN: read-only inventory ----------
   "What is running, on what model, from which files, and what has it cost."
   No create/edit controls anywhere on this tab, on purpose. */
const dur = (s) => s == null ? '' : s > 86400 ? `${(s / 86400).toFixed(1)}d`
  : s > 3600 ? `${(s / 3600).toFixed(1)}h` : s > 60 ? `${Math.round(s / 60)}m` : `${Math.round(s)}s`;
const kb = (b) => b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB' : b >= 1000 ? Math.round(b / 1000) + ' KB' : b + ' B';
const tok = (n) => n == null ? '--' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M'
  : n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n);

// Mirrors the server's EDITABLE allowlist. The server is the one that enforces
// it; this only decides which rows show a button.
const EDITABLE_FILES = new Set(['RULES.md', 'memory.md', 'CLAUDE.md', 'PROMPTS.md', 'config.json']);

async function editFile(name) {
  const d = await J('/api/file?name=' + encodeURIComponent(name)).catch(() => null);
  if (!d || d.error) return notify('could not open ' + name, 'err');
  const wrap = document.createElement('div');
  wrap.className = 'modal';                       // same shell as the trade ticket
  wrap.innerHTML = `<div class="modal-card fileedit">
    <div class="panel-h">EDIT <span class="mono">${esc(name)}</span>
      <button class="btn sm right" data-x>✕</button></div>
    <div class="dim" style="margin:-4px 0 8px">${esc(d.what || '')}</div>
    <textarea class="fileta mono" spellcheck="false"></textarea>
    <div class="dim mono" style="font-size:11px;margin-top:6px">${esc(d.path || '')}</div>
    <div class="tk-row" style="justify-content:flex-end;align-items:center;gap:10px">
      <span data-msg class="dim"></span>
      <button class="btn" data-x>Cancel</button>
      <button class="btn side on" data-save>Save</button>
    </div></div>`;
  document.body.appendChild(wrap);
  const ta = wrap.querySelector('.fileta');
  const msg = wrap.querySelector('[data-msg]');
  ta.value = d.text || '';
  const original = ta.value;
  ta.focus();
  const close = () => wrap.remove();
  wrap.querySelectorAll('[data-x]').forEach(b => b.onclick = close);
  // Backdrop click closes only when nothing has been typed. Losing a rewritten
  // trading plan to a stray click outside the box is not a fair trade.
  wrap.addEventListener('mousedown', e => {
    if (e.target === wrap && ta.value === original) close();
  });
  wrap.querySelector('[data-save]').onclick = async () => {
    msg.textContent = 'saving...'; msg.style.color = '';
    try {
      const r = await fetch('/api/file', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, text: ta.value })
      });
      const j = await r.json().catch(() => ({}));
      // fetch does NOT throw on 4xx/5xx. Checking r.ok is the difference between
      // "invalid JSON, not saved" and the user walking away thinking it saved.
      if (!r.ok || !j.ok) {
        msg.textContent = j.error || ('save failed (' + r.status + ')');
        msg.style.color = 'var(--loss)';
        return;
      }
      notify(j.note || (name + ' saved'), 'ok');
      close();
      loadAdmin();
    } catch (e) {
      msg.textContent = String(e).slice(0, 120);
      msg.style.color = 'var(--loss)';
    }
  };
}

async function loadAdmin() {
  const d = await J('/api/admin').catch(() => null);
  if (!d) { $('#adminLanes').innerHTML = '<span class="dim">admin unavailable</span>'; return; }
  const r = d.runtime || {}, u = d.usage || {};

  // Version lives here, not just in the console banner nobody reads. If a newer
  // release exists the sub-line says so; there is no auto-download.
  const up = r.update || {};
  $('#adminRuntime').innerHTML = [
    ['Version', r.version ? `v${r.version}` : '--',
      up.available ? `v${up.latest} available` : 'up to date'],
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

  // Editable files get an Edit button. Anything not on the server's allowlist
  // (bot/.env, the jsonl logs) stays read-only here on purpose.
  $('#adminFiles').innerHTML = '<table class="admin"><tr><th>file</th><th>what it does</th>' +
    '<th class="r">size</th><th class="r">changed</th><th></th></tr>' +
    (d.files || []).map(f => `<tr><td class="mono ${f.exists ? '' : 'dim'}">${esc(f.label)}` +
      `${f.exists ? '' : ' <span class="dim">(missing)</span>'}</td>` +
      `<td class="dim">${esc(f.what)}</td>` +
      `<td class="r mono">${f.exists ? kb(f.bytes) : '--'}</td>` +
      `<td class="r dim mono">${esc(f.mtime || '--')}</td>` +
      `<td class="r">${EDITABLE_FILES.has(f.label)
        ? `<button class="btn sm" onclick="editFile('${esc(f.label)}')">Edit</button>` : ''}</td>` +
      `</tr>`).join('') + '</table>';

  const t = d.trading || {};
  $('#adminTrading').innerHTML = '<table class="admin">' + [
    ['Account', String(t.env || '--').toUpperCase(), t.env === 'live' ? 'loss' : ''],
    ['Data feed', String(t.feed || '--').toUpperCase(), ''],
    ['Mode', t.mode || 'manual', ''],
    ['Auto-entries', t.auto ? 'ARMED' : 'off', t.auto ? 'loss' : 'up'],
    ['Live auto unlocked', t.live_auto ? 'YES' : 'no', t.live_auto ? 'loss' : 'up'],
    ['Per trade', t.per_trade != null ? fmt$(t.per_trade, 0) : '--', ''],
    ['Max exposure', t.max_exposure != null ? fmt$(t.max_exposure, 0) : '--', ''],
    ['Price floor', t.min_price != null ? fmt$(t.min_price, 0) : '--', ''],
    ['Trail', t.trail_pct != null ? (t.trail_pct * 100).toFixed(0) + '%' : '--', ''],
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
let noVoiceWarned = false;
let audioUnlocked = false, audioUnlockArmed = false;
function armAudioUnlock() {
  if (audioUnlocked || audioUnlockArmed) return;
  audioUnlockArmed = true;
  const unlock = () => {
    audioUnlockArmed = false;
    audioUnlocked = true;
    // Playing a silent buffer inside the gesture is what actually satisfies the
    // policy; after this, later .play() calls are allowed for the session.
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) { const c = new Ctx(); c.resume?.(); const s = c.createBufferSource();
        s.buffer = c.createBuffer(1, 1, 22050); s.connect(c.destination); s.start(0); }
    } catch {}
    notify('voice: sound enabled', 'ok');
    for (const ev of ['pointerdown', 'keydown']) document.removeEventListener(ev, unlock, true);
  };
  for (const ev of ['pointerdown', 'keydown']) document.addEventListener(ev, unlock, true);
}

async function speakText(text) {
  if (!text) return;
  if (vbOk) {
    try {
      // Hard client-side cap: a wedged Voicebox once held this fetch open for
      // minutes and the desk just looked mute. Better a visible fallback.
      const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.slice(0, 900) }),
        signal: AbortSignal.timeout ? AbortSignal.timeout(60000) : undefined });
      if (r.ok && (r.headers.get('Content-Type') || '').includes('audio')) {
        const blob = await r.blob();
        audioEl?.pause();
        audioEl = new Audio(URL.createObjectURL(blob));
        speakingNow = true;
        audioEl.onended = audioEl.onerror = () => { setTimeout(() => { speakingNow = false; }, 400); };
        // play() fails via a REJECTED PROMISE (autoplay policy, device gone) -
        // unhandled, that was a desk that just silently never spoke
        audioEl.play().catch((e) => {
          speakingNow = false;
          if (e && e.name === 'NotAllowedError') {
            // Browser autoplay policy: audio cannot start until the page has
            // had a real user gesture. A hard refresh resets that, which is
            // exactly when this fires. The pywebview shell passes
            // --autoplay-policy=no-user-gesture-required; a plain browser
            // cannot, so unlock on the next click/key instead of just
            // complaining once and staying mute forever.
            armAudioUnlock();
            notify('voice: click anywhere once to enable sound (browser rule)', 'warn');
            return;
          }
          notify(`voice: playback blocked (${e.name || e.message})`, 'warn');
        });
        return;
      }
    } catch { /* fall through to browser voice */ }
    vbHealth();
  }
  // gate on a real browser: WebView2 may EXPOSE speechSynthesis yet render
  // nothing, which reads as "voice randomly broken" instead of an honest miss
  if ('speechSynthesis' in window && shellInfo.shell === 'browser') {
    const u = new SpeechSynthesisUtterance(text.slice(0, 600)); u.rate = 1.05;
    speakingNow = true;
    u.onend = u.onerror = () => { setTimeout(() => { speakingNow = false; }, 400); };
    speechSynthesis.speak(u);
    return;
  }
  // WebView2 has NO Web Speech API: inside a shell with Voicebox down there is
  // no voice at all - say so once instead of being silently mute.
  if (!noVoiceWarned) {
    noVoiceWarned = true;
    notify('voice: Voicebox unreachable and this window has no built-in voice', 'warn');
  }
}
async function sendChat() {
  const t = $('#chatInput').value.trim();
  if (!t) return;
  $('#chatInput').value = '';
  // Sending always lands in TODAY. If you were reading an old day, jump back to
  // the live conversation rather than posting into a view that will not update.
  if (chatDay) { chatDay = ''; chatCount = -1; chatInit = false; }
  await J('/api/chat/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) });
  loadChat();
}
$('#chatSend').onclick = sendChat;
// Enter sends, Shift+Enter is a newline (the convention everyone already knows).
// The box grows with the text instead of scrolling a one-line input.
$('#chatNew').onclick = () => {
  chatDay = ''; chatCount = -1; chatInit = false; loadChat();
  requestAnimationFrame(() => $('#chatInput')?.focus());
};
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
   listening from ANY tab). Echo-guarded: anything heard while the copilot is
   speaking gets discarded.

   TWO ENGINES, picked live:
   1) PREFERRED: MediaRecorder -> POST /api/stt (Voicebox whisper, local).
      MediaRecorder exists in every modern engine, unlike SpeechRecognition -
      WebView2 has NO Web Speech API, which is exactly how the old hot mic died
      the moment the desk left Chrome. Bonus: fully offline, no Google.
   2) FALLBACK: browser SpeechRecognition (Chrome/Edge) when Voicebox is absent.
   Neither available -> buttons disabled with an honest tooltip. */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let sttOk = false;
async function sttHealth() {
  try { sttOk = !!(await J('/api/stt/health')).ok; } catch { sttOk = false; }
  return sttOk;
}

/* --- engine 1: raw-PCM WAV capture + /api/stt -----------------------------
   Why WAV and not MediaRecorder: MediaRecorder's container is engine roulette
   (webm/opus in Chrome and WebView2, mp4 elsewhere) and Voicebox /transcribe
   500s on webm - reproduced 2026-08-06, and exactly the error the first exe
   test hit. Whisper's home format is plain PCM WAV, so we tap the raw samples
   with a ScriptProcessor and build the WAV ourselves. Works identically in
   every engine; bonus: the hot mic gets a real pre-roll (no clipped first
   syllable, which the restart-a-recorder approach could never fix). */
let micStream = null;
async function getMic() {
  if (micStream && micStream.active) return micStream;
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true } });
  return micStream;
}
function dropMic() {
  try { micStream?.getTracks().forEach(t => t.stop()); } catch {}
  micStream = null;
}
function wavEncode(chunks, sampleRate) {
  let n = 0;
  for (const c of chunks) n += c.length;
  const pcm = new Int16Array(n);
  let o = 0;
  for (const c of chunks)
    for (let i = 0; i < c.length; i++) {
      const s = Math.max(-1, Math.min(1, c[i]));
      pcm[o++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const tag = (off, str) => { for (let i = 0; i < str.length; i++) v.setUint8(off + i, str.charCodeAt(i)); };
  tag(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); tag(8, 'WAVE');
  tag(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  tag(36, 'data'); v.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: 'audio/wav' });
}
function startWav(stream) {
  // ScriptProcessor is deprecated-but-everywhere (incl. WebView2); AudioWorklet
  // needs a module file and buys nothing for a local app.
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const rec = { ctx, chunks: [], rate: ctx.sampleRate };
  rec.proc = ctx.createScriptProcessor(4096, 1, 1);
  rec.proc.onaudioprocess = (e) => rec.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  ctx.createMediaStreamSource(stream).connect(rec.proc);
  rec.proc.connect(ctx.destination);   // unconnected processors never pump
  return rec;
}
function stopWav(rec) {
  try { rec.proc.disconnect(); } catch {}
  try { rec.ctx.close(); } catch {}
  return wavEncode(rec.chunks, rec.rate);
}
async function transcribe(blob) {
  try {
    const r = await fetch('/api/stt', { method: 'POST',
      headers: { 'Content-Type': 'audio/wav' }, body: blob });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) throw new Error(d.error || 'transcribe failed');
    return (d.text || '').trim();
  } catch (e) {
    sttOk = false;   // un-latch: next click re-probes health and can fall back to SR
    throw e;
  }
}

/* Dictation: click = record, click again = stop -> transcribe -> DRAFT.
   It does NOT send. Speaking a thought and sending it are two decisions, and
   this desk places real orders, so the text lands in the composer and waits for
   you. Dictate again and it appends, so you can build a message in pieces.
   Auto-send is what HOT MIC is for - that mode is an explicit opt-in to a
   conversation. */
let ptt = null, pttBusy = false;
async function pttToggle() {
  if (ptt) {
    const rec = ptt; ptt = null;
    $('#micBtn').classList.remove('rec');
    const blob = stopWav(rec);
    if (!hotOn && !hotProc) dropMic();            // last consumer: mic light off
    if (rec.chunks.length * 4096 / rec.rate < 0.35) return;   // click-click, not speech
    const box = $('#chatInput');
    box.placeholder = 'transcribing...';
    try {
      const text = await transcribe(blob);
      if (text) {
        const had = box.value.trim();
        box.value = had ? had + ' ' + text : text;   // append, never clobber
        box.focus();
        box.setSelectionRange(box.value.length, box.value.length);
        box.dispatchEvent(new Event('input'));       // let the composer autosize
      }
    } catch (e) { notify('voice: ' + e.message, 'err'); }
    box.placeholder = 'Ask anything about the market, or tell me what to build...';
    return;
  }
  if (pttBusy) return;                            // getMic() still awaiting
  pttBusy = true;
  try {
    const stream = await getMic();
    ptt = startWav(stream);
    $('#micBtn').classList.add('rec');
  } catch (e) {
    ptt = null; notify('mic: ' + e.message, 'err');
    if (!hotOn && !hotProc) dropMic();
  } finally { pttBusy = false; }
}

/* hot mic: voice-activity chunking on the same sample tap. A ring buffer keeps
   ~350ms of pre-roll so the first syllable survives; ~1.2s of silence closes
   the utterance; anything heard while the copilot is speaking is discarded. */
let hotOn = false, hotProc = null, hotCtx = null, hotWarned = false;
async function hotStart() {
  const stream = await getMic();
  if (!hotOn) {                                    // toggled off mid-await
    if (!ptt) dropMic();
    return;
  }
  if (hotProc) { try { hotProc.disconnect(); } catch {} }   // never two VAD taps
  hotCtx = new (window.AudioContext || window.webkitAudioContext)();
  const rate = hotCtx.sampleRate;
  // SILENCE_MS is the endpointing hold: how long you have to stop talking before
  // the utterance is considered finished and sent. 1200ms cut people off
  // mid-thought and shipped the tail of a sentence as a second message, which is
  // where the stray words came from. 1800 is a natural pause without feeling
  // laggy. Tune with "voice_endpoint_ms" in config.json.
  const SILENCE_MS = Math.max(600, +(META.voice_endpoint_ms || 1800));
  const THRESH = 0.015, MIN_MS = 450, MAX_MS = 25000, PRE = 4;
  let ring = [], talking = false, buf = [], lastVoice = 0, started = 0, echo = false;
  hotProc = hotCtx.createScriptProcessor(4096, 1, 1);
  hotProc.onaudioprocess = (e) => {
    if (!hotOn) return;
    const data = new Float32Array(e.inputBuffer.getChannelData(0));
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length), now = performance.now();
    if (!talking) {
      ring.push(data);
      if (ring.length > PRE) ring.shift();
      if (rms > THRESH && !speakingNow) {
        talking = true; echo = false; started = now; lastVoice = now;
        buf = ring.slice(); ring = [];             // pre-roll seeds the utterance
      }
      return;
    }
    buf.push(data);
    if (rms > THRESH) lastVoice = now;
    if (speakingNow) echo = true;                  // TTS lit up mid-utterance
    if (now - lastVoice > SILENCE_MS || now - started > MAX_MS) {
      talking = false;
      const utter = buf, ms = now - started;
      buf = [];
      if (echo || ms < MIN_MS) return;
      transcribe(wavEncode(utter, rate)).then((text) => {
        if (text.length > 2)
          J('/api/chat/send', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }) }).then(loadChat);
      }).catch(() => {                             // fail quiet, but say it ONCE
        if (!hotWarned) { hotWarned = true; notify('hot mic: transcription failing - is Voicebox up?', 'warn'); }
      });
    }
  };
  hotCtx.createMediaStreamSource(stream).connect(hotProc);
  hotProc.connect(hotCtx.destination);
}
function hotStop() {
  try { hotProc?.disconnect(); } catch {}
  hotProc = null;
  try { hotCtx?.close(); } catch {}
  hotCtx = null;
  if (!ptt) dropMic();   // PTT may still be mid-recording on the shared stream
}

/* --- engine 2: legacy SpeechRecognition (Chrome/Edge, needs Google) ------- */
let srPtt = null, srHot = null;
function srPttToggle() {
  if (srPtt) { srPtt.stop(); return; }
  srPtt = new SR(); srPtt.lang = 'en-US'; srPtt.interimResults = true;
  srPtt.onresult = (e) => { $('#chatInput').value = [...e.results].map(r => r[0].transcript).join(''); };
  srPtt.onend = () => { $('#micBtn').classList.remove('rec'); srPtt = null; if ($('#chatInput').value.trim()) sendChat(); };
  srPtt.start(); $('#micBtn').classList.add('rec');
}
function srHotStart() {
  srHot = new SR(); srHot.lang = 'en-US'; srHot.continuous = true; srHot.interimResults = false;
  srHot.onresult = (e) => {
    if (speakingNow) return;
    const text = [...e.results].slice(e.resultIndex).map(r => r[0].transcript).join(' ').trim();
    if (text.length > 2)
      J('/api/chat/send', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }) }).then(loadChat);
  };
  srHot.onend = () => { if (hotOn) { try { srHot.start(); } catch {} } };  // Chrome kills long sessions - relight
  srHot.onerror = (e) => { if (e.error === 'not-allowed') hotToggle(false); };
  srHot.start();
}
function srHotStop() { try { srHot?.stop(); } catch {} srHot = null; }

/* --- wiring: pick an engine at click time -------------------------------- */
const hasAudio = !!(navigator.mediaDevices
  && (window.AudioContext || window.webkitAudioContext));
$('#micBtn').onclick = async () => {
  if (ptt || srPtt) { ptt ? pttToggle() : srPtt.stop(); return; }
  if (hasAudio && (sttOk || await sttHealth())) return pttToggle();
  if (SR) return srPttToggle();
  notify('voice input needs Voicebox running, or Chrome/Edge', 'warn');
};
async function hotToggle(next = !hotOn) {
  hotOn = next;
  $('#hotMic').classList.toggle('on', hotOn);
  $('#hotMic').textContent = hotOn ? '🎙 hot mic ON' : '🎙 hot mic';
  if (!hotOn) { hotStop(); srHotStop(); return; }
  hotWarned = false;
  try {
    if (hasAudio && (sttOk || await sttHealth())) return await hotStart();
    if (SR) return srHotStart();
    notify('voice input needs Voicebox running, or Chrome/Edge', 'warn');
    hotToggle(false);
  } catch (e) { notify('mic: ' + e.message, 'err'); hotToggle(false); }
}
$('#hotMic').onclick = () => hotToggle();
$('#radarSettings').onclick = () => toggleScan('catalyst');
$('#retailSettings').onclick = () => toggleScan('retail');
sttHealth().then(() => {
  if (!sttOk && !SR && !hasAudio) {
    $('#micBtn').title = 'voice needs Voicebox, or Chrome/Edge';
    $('#micBtn').disabled = true; $('#hotMic').disabled = true;
  }
});

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
      // The staged card used to sit there after the trade was placed, so the
      // desk showed a live proposal for something already done and you had to
      // notice and hit Dismiss. If this order IS the staged one, retire it.
      const st = window.__staged;
      if (st && String(st.symbol).toUpperCase() === String(tk.symbol).toUpperCase()
          && String(st.side || 'buy').toLowerCase() === String(tk.side).toLowerCase()) {
        fetch('/api/staged/clear', { method: 'POST' })
          .catch(() => {})
          .finally(() => { window.__staged = null; loadStaged(); });
      }
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
  { k: 'go: saved pages', ico: '▦', run: () => tabTo('saved') },
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
  Object.assign(META, m);
  window._user = m.user || 'trader';
  window.__mfUser = m.user || 'You';        // avatar initials in the chat
  initTheme(m.theme);                        // config default, if nothing saved locally
  setTimeout(() => { if ($('#ttsToggle').checked) speakText(`Welcome back, ${window._user}.`); }, 1200);
}).catch(() => {});
loadOverview(); loadChart(); pickDefaultChart(); loadRadar(); loadReddit(); loadPanels(true); loadRules(); loadChat(); loadNaked(); loadStaged();
setInterval(loadOverview, 15000);
setInterval(loadNaked, 20000);   // an unarmed position must surface fast
// The copilot writes staged-trade.json mid-turn, so this needs the same cadence
// as panels. Without it the card only appeared on a page refresh, which made a
// staged trade look like it had silently failed.
setInterval(loadStaged, 2500);
setInterval(loadRadar, 30000);
setInterval(loadReddit, 60000);
setInterval(loadChat, 2500);
setInterval(loadPanels, 2500);
loadBridge(); setInterval(loadBridge, 2500);
