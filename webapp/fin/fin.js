/* Nargis Moliya — owner-only finance Mini App. Vanilla JS, SVG charts, no external libraries. */
(function () {
  'use strict';

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { try { tg.ready(); tg.expand(); } catch (e) { /* ignore */ } }

  // Telegram puts the signed initData in the URL hash (#tgWebAppData=...) as well as in tg.initData.
  const initData = (tg && tg.initData) || new URLSearchParams(location.hash.slice(1)).get('tgWebAppData') || '';

  const MONTHS = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentyabr', 'oktyabr', 'noyabr', 'dekabr'];
  const WEEKDAYS = ['Yak', 'Du', 'Se', 'Chor', 'Pay', 'Ju', 'Sha'];
  const PALETTE = ['#4f7cff', '#ff5a5f', '#1fb978', '#f5a524', '#9b6bff', '#17b3c8', '#e0478a', '#8a97a8'];

  const $ = (id) => document.getElementById(id);
  const state = { kind: 'month', month: null, today: null, dash: null, txs: [], pending: 0, openTx: null };

  /* ── helpers ── */
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n) => String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const short = (n) => {
    const a = Math.abs(n);
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace('.0', '') + ' mln';
    if (a >= 1e3) return Math.round(n / 1e3) + ' ming';
    return String(Math.round(n));
  };
  const parseDate = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
  const dm = (iso) => { const d = parseDate(iso); return `${d.getDate()} ${MONTHS[d.getMonth()].slice(0, 3)}`; };
  const monthLabel = (ym) => { const [y, m] = ym.split('-').map(Number); return `${MONTHS[m - 1]} ${y}`; };
  const ymOf = (iso) => iso.slice(0, 7);
  const shiftMonth = (ym, delta) => { const [y, m] = ym.split('-').map(Number); const d = new Date(y, m - 1 + delta, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };

  function haptic(kind) { try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (e) { /* ignore */ } }
  function toast(text) {
    const el = $('toast'); el.textContent = text; el.classList.add('show');
    clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('show'), 2600);
  }

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({}, opts, {
      headers: Object.assign({ 'Authorization': 'tma ' + initData, 'Content-Type': 'application/json' }, (opts && opts.headers) || {}),
    }));
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || !data || data.ok === false) {
      const err = new Error((data && data.error) || ('HTTP ' + res.status));
      err.status = res.status; throw err;
    }
    return data;
  }

  /* ── theme ── */
  function applyTheme() {
    const dark = tg && tg.colorScheme ? tg.colorScheme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    try { if (tg) { tg.setHeaderColor(dark ? '#0d121c' : '#f3f5f9'); tg.setBackgroundColor(dark ? '#0d121c' : '#f3f5f9'); } } catch (e) { /* ignore */ }
  }
  applyTheme();
  if (tg && tg.onEvent) tg.onEvent('themeChanged', applyTheme);

  /* ── loading / messages ── */
  function showMessage(title, text) {
    $('skeleton').classList.add('hidden'); $('content').classList.add('hidden');
    const m = $('msg'); m.classList.remove('hidden');
    m.innerHTML = `<b>${esc(title)}</b><span class="muted">${esc(text)}</span>`;
  }

  async function load() {
    if (!initData) { showMessage('Faqat Telegram ichida', 'Bu panelni bot menyusidagi "Moliya" tugmasi orqali oching.'); return; }
    $('refresh').classList.add('spin');
    $('msg').classList.add('hidden');
    if (!state.dash) { $('skeleton').classList.remove('hidden'); }
    const monthParam = state.kind === 'month' && state.month && state.month !== (state.today || '').slice(0, 7) ? '&month=' + state.month : '';
    try {
      const [d, t] = await Promise.all([
        api(`/api/fin/dashboard?kind=${state.kind}${monthParam}`),
        api(`/api/fin/transactions?month=${state.month || ''}`),
      ]);
      state.dash = d.dashboard; state.today = d.dashboard.today;
      if (!state.month) state.month = ymOf(d.dashboard.today);
      state.txs = t.transactions; state.pending = t.pendingCount;
      render();
    } catch (err) {
      if (err.status === 401 || err.status === 403) showMessage('Kirish taqiqlangan', 'Bu panel faqat Asadbek ustoz uchun. Telegram ichidan qayta oching.');
      else showMessage('Yuklab bo\'lmadi', 'Internetni tekshirib, yangilash tugmasini bosing.');
    } finally { $('refresh').classList.remove('spin'); }
  }

  /* ── rendering ── */
  function countUp(el, to, signed) {
    const from = Number(el.dataset.v || 0); el.dataset.v = to;
    const t0 = performance.now(), dur = 600;
    const step = (t) => {
      const p = Math.min((t - t0) / dur, 1), e = 1 - Math.pow(1 - p, 3), v = from + (to - from) * e;
      el.textContent = (signed ? (v >= 0 ? '+' : '−') : '') + fmt(Math.abs(v));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function render() {
    const d = state.dash;
    $('skeleton').classList.add('hidden'); $('content').classList.remove('hidden');
    $('range').textContent = d.kind === 'month' ? monthLabel(ymOf(d.start)) : `${dm(d.start)} – ${dm(d.end)}`;
    document.querySelectorAll('#seg button').forEach((b) => b.classList.toggle('active', b.dataset.kind === state.kind));
    $('monthnav').classList.toggle('hidden', state.kind !== 'month');
    $('monthLabel').textContent = monthLabel(state.month || ymOf(d.start));
    $('nextMonth').disabled = (state.month || '') >= ymOf(state.today);

    countUp($('kIncome'), d.income); countUp($('kExpense'), d.expense); countUp($('kNet'), d.net, true); countUp($('kAvg'), d.avgDailyExpense);

    // budget
    const b = $('budget');
    if (d.budgetLimit && d.budgetPct != null) {
      b.classList.remove('hidden');
      $('budgetPct').textContent = d.budgetPct + '%';
      requestAnimationFrame(() => { $('budgetFill').style.width = Math.min(d.budgetPct, 100) + '%'; });
      $('budgetNote').textContent = `Limit ${fmt(d.budgetLimit)} so'm · qoldi ${fmt(Math.max(d.budgetLimit - d.expense, 0))} so'm`;
    } else b.classList.add('hidden');

    // other currencies
    const usd = $('usd'), cur = Object.entries(d.otherCurrencies || {});
    if (cur.length) {
      usd.classList.remove('hidden');
      usd.innerHTML = '<h2>Boshqa valyutalar</h2>' + cur.map(([c, v]) => `<div class="row"><span>${esc(c)}</span><b>+${fmt(v.income)} / −${fmt(v.expense)}</b></div>`).join('');
    } else usd.classList.add('hidden');

    renderBars(d); renderDonut(d); renderLine(d); renderInsights(d); renderTxs();
  }

  function renderBars(d) {
    const W = 340, H = 170, pl = 30, pb = 20, pt = 8, n = d.daily.length;
    const max = Math.max(1, ...d.daily.map((x) => Math.max(x.income, x.expense)));
    const iw = (W - pl) / n, bw = Math.max(Math.min(iw * 0.34, 12), 2);
    const y = (v) => pt + (H - pt - pb) * (1 - v / max);
    let g = '';
    for (let i = 0; i <= 3; i++) { const v = max * i / 3; g += `<line class="grid" x1="${pl}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="0" y="${y(v) + 3}">${short(v)}</text>`; }
    const every = n <= 8 ? 1 : n <= 16 ? 2 : 4;
    const cols = d.daily.map((x, i) => {
      const cx = pl + iw * i + iw / 2, dt = parseDate(x.date);
      const label = i % every === 0 ? `<text x="${cx}" y="${H - 4}" text-anchor="middle">${n <= 8 ? WEEKDAYS[dt.getDay()] + ' ' : ''}${dt.getDate()}</text>` : '';
      const hi = y(x.income), he = y(x.expense), base = y(0);
      return `<g class="col" data-i="${i}"><rect class="hit" x="${pl + iw * i}" y="${pt}" width="${iw}" height="${H - pt - pb}" fill="transparent"/>
        <rect class="bar-in grow" x="${cx - bw - 0.5}" y="${hi}" width="${bw}" height="${base - hi}" rx="2" style="animation-delay:${i * 12}ms"/>
        <rect class="bar-ex grow" x="${cx + 0.5}" y="${he}" width="${bw}" height="${base - he}" rx="2" style="animation-delay:${i * 12 + 60}ms"/>${label}</g>`;
    }).join('');
    $('barsChart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kunlik daromad va xarajat">${g}${cols}</svg>`;
    $('barsTip').textContent = 'Ustunni bosing';
    $('barsChart').querySelectorAll('g.col').forEach((el) => el.addEventListener('click', () => {
      $('barsChart').querySelectorAll('g.col').forEach((c) => c.classList.remove('sel')); el.classList.add('sel');
      const x = d.daily[Number(el.dataset.i)];
      $('barsTip').textContent = `${dm(x.date)} · daromad ${fmt(x.income)} · xarajat ${fmt(x.expense)}`;
      try { tg && tg.HapticFeedback && tg.HapticFeedback.selectionChanged(); } catch (e) { /* ignore */ }
    }));
  }

  const plain = (name) => { const p = String(name).split(' '); return p.length > 1 && !/[\p{L}\p{N}]/u.test(p[0]) ? p.slice(1).join(' ') : name; };

  function renderDonut(d) {
    const cats = d.expenseCategories;
    if (!cats.length) { $('donutWrap').innerHTML = '<p class="empty">Bu davrda xarajat yo\'q</p>'; $('catList').innerHTML = ''; return; }
    const top = cats.slice(0, 7), rest = cats.slice(7).reduce((s, c) => s + c.amount, 0);
    if (rest > 0) top.push({ name: 'Boshqalar', amount: rest });
    const total = top.reduce((s, c) => s + c.amount, 0), R = 70, C = 2 * Math.PI * R;
    let off = 0;
    const arcs = top.map((c, i) => {
      const len = C * c.amount / total, el = `<circle r="${R}" cx="95" cy="95" fill="none" stroke="${PALETTE[i % PALETTE.length]}" stroke-width="24" stroke-dasharray="${Math.max(len - 2, 0)} ${C}" stroke-dashoffset="${-off}"/>`;
      off += len; return el;
    }).join('');
    $('donutWrap').innerHTML = `<svg viewBox="0 0 190 190" role="img" aria-label="Xarajat kategoriyalari">${arcs}</svg><div class="center"><b>${short(total)}</b><span>jami xarajat</span></div>`;
    $('catList').innerHTML = top.map((c, i) => {
      const pc = c.amount / total * 100, col = PALETTE[i % PALETTE.length];
      return `<li><i class="sw" style="background:${col}"></i><span class="nm">${esc(plain(c.name))} <span class="muted small">${pc.toFixed(0)}%</span></span><span class="am">${fmt(c.amount)}</span><span class="pc"><i style="width:${pc}%;background:${col}"></i></span></li>`;
    }).join('');
  }

  function renderLine(d) {
    const W = 340, H = 150, pl = 30, pb = 18, pt = 10, n = d.daily.length;
    const lastIdx = d.daily.reduce((acc, x, i) => (x.date <= d.end ? i : acc), -1);
    let run = 0; const pts = d.daily.slice(0, lastIdx + 1).map((x) => (run += x.expense));
    const refs = [d.budgetLimit || 0, d.projectedExpense || 0, run];
    const max = Math.max(1, ...refs);
    const x = (i) => pl + (W - pl) * (n <= 1 ? 0.5 : i / (n - 1)), y = (v) => pt + (H - pt - pb) * (1 - v / max);
    let g = '';
    for (let i = 0; i <= 3; i++) { const v = max * i / 3; g += `<line class="grid" x1="${pl}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="0" y="${y(v) + 3}">${short(v)}</text>`; }
    const path = pts.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const area = pts.length ? `${path} L${x(pts.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z` : '';
    let extra = '';
    if (d.budgetLimit) extra += `<line x1="${pl}" x2="${W}" y1="${y(d.budgetLimit)}" y2="${y(d.budgetLimit)}" stroke="var(--accent)" stroke-dasharray="5 4" stroke-width="1.6"/><text x="${W}" y="${y(d.budgetLimit) - 4}" text-anchor="end" style="fill:var(--accent)">byudjet ${short(d.budgetLimit)}</text>`;
    if (d.projectedExpense && d.projectedExpense > run) extra += `<line x1="${pl}" x2="${W}" y1="${y(d.projectedExpense)}" y2="${y(d.projectedExpense)}" stroke="var(--muted)" stroke-dasharray="2 4" stroke-width="1.4"/><text x="${W}" y="${y(d.projectedExpense) - 4}" text-anchor="end">taxmin ${short(d.projectedExpense)}</text>`;
    const dots = pts.length ? `<circle cx="${x(pts.length - 1)}" cy="${y(run)}" r="4" fill="var(--expense)"/>` : '';
    $('lineChart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Yig'ma xarajat">${g}${extra}<path d="${area}" fill="var(--expense)" opacity=".12"/><path d="${path}" fill="none" stroke="var(--expense)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>${dots}</svg>`;
  }

  function renderInsights(d) {
    const rows = [];
    if (d.topDay) rows.push(['Eng qimmat kun', `${dm(d.topDay.date)} — ${fmt(d.topDay.amount)}`]);
    if (d.expenseCategories[0]) rows.push(['Eng katta kategoriya', `${plain(d.expenseCategories[0].name)} — ${fmt(d.expenseCategories[0].amount)}`]);
    if (d.incomeSources[0]) rows.push(['Asosiy daromad', `${plain(d.incomeSources[0].name)} — ${fmt(d.incomeSources[0].amount)}`]);
    if (d.kind === 'month' && d.projectedExpense && d.projectedExpense > d.expense) rows.push(['Oy oxirigacha taxmin', fmt(d.projectedExpense)]);
    rows.push(['Tasdiqlangan yozuvlar', String(d.txCount) + ' ta']);
    $('insightList').innerHTML = rows.map(([k, v]) => `<li><span>${esc(k)}</span><b>${esc(v)}</b></li>`).join('');
  }

  function renderTxs() {
    const badge = $('pendingBadge');
    if (state.pending) { badge.classList.remove('hidden'); badge.textContent = `${state.pending} ta kutilmoqda`; } else badge.classList.add('hidden');
    if (!state.txs.length) { $('txList').innerHTML = '<p class="empty">Bu oyda yozuv yo\'q. Botga "Tushlik 45 ming" deb yozing.</p>'; return; }
    let html = '', lastDay = '';
    state.txs.forEach((t, i) => {
      if (t.date !== lastDay) { lastDay = t.date; html += `<div class="day">${esc(dm(t.date))}</div>`; }
      const inc = t.type === 'INCOME', sign = inc ? '+' : '−', cur = t.currency === 'UZS' ? '' : ' ' + esc(t.currency);
      html += `<button class="tx ${inc ? 'in' : 'out'} ${t.status === 'PENDING' ? 'pending' : ''}" data-i="${i}">
        <span class="ic">${inc ? '↑' : '↓'}</span>
        <span><div class="t">${esc(t.description || plain(t.category))}</div><div class="s">${esc(plain(t.category))}${t.status === 'PENDING' ? ' · kutilmoqda' : ''}</div></span>
        <span class="a">${sign}${fmt(t.amount)}${cur}</span></button>`;
    });
    $('txList').innerHTML = html;
    $('txList').querySelectorAll('.tx').forEach((el) => el.addEventListener('click', () => openSheet(state.txs[Number(el.dataset.i)])));
  }

  /* ── bottom sheet: confirm / edit / cancel ── */
  function openSheet(t) {
    state.openTx = t;
    $('sheetTitle').textContent = `${t.type === 'INCOME' ? '+' : '−'}${fmt(t.amount)} ${t.currency === 'UZS' ? "so'm" : t.currency}`;
    $('sheetMeta').textContent = `${plain(t.category)} · ${dm(t.date)}${t.description ? ' · ' + t.description : ''}${t.status === 'PENDING' ? ' · kutilmoqda' : ''}`;
    $('sheetEdit').classList.add('hidden'); $('amountInput').value = '';
    const acts = $('sheetActions'); acts.innerHTML = '';
    const add = (label, cls, fn) => { const b = document.createElement('button'); b.className = 'btn ' + cls; b.textContent = label; b.addEventListener('click', fn); acts.appendChild(b); };
    if (t.status === 'PENDING') add('✅ Tasdiqlash', 'primary', () => act('confirm'));
    add('✏️ Summani o\'zgartirish', '', () => { $('sheetEdit').classList.remove('hidden'); $('amountInput').focus(); });
    add('🗑 Bekor qilish', 'danger', () => act('cancel'));
    $('sheet').classList.remove('hidden');
  }
  function closeSheet() { $('sheet').classList.add('hidden'); state.openTx = null; }

  async function act(kind, body) {
    const t = state.openTx; if (!t) return;
    try {
      await api(`/api/fin/transactions/${t.id}/${kind}`, { method: 'POST', body: JSON.stringify(body || {}) });
      haptic('success'); closeSheet();
      toast(kind === 'confirm' ? 'Tasdiqlandi ✅' : kind === 'cancel' ? 'Bekor qilindi' : 'Summa yangilandi ✅');
      await load();
    } catch (err) {
      haptic('error');
      toast(err.message === 'bad amount' ? 'Summani tushunmadim' : err.message === 'cancelled' ? 'Bu yozuv bekor qilingan' : 'Xatolik, qayta urinib ko\'ring');
    }
  }

  /* ── events ── */
  document.querySelectorAll('#seg button').forEach((b) => b.addEventListener('click', () => {
    state.kind = b.dataset.kind; if (state.kind !== 'month') state.month = state.today ? ymOf(state.today) : null; load();
  }));
  $('prevMonth').addEventListener('click', () => { state.month = shiftMonth(state.month, -1); load(); });
  $('nextMonth').addEventListener('click', () => { if (state.month < ymOf(state.today)) { state.month = shiftMonth(state.month, 1); load(); } });
  $('refresh').addEventListener('click', load);
  $('sheetBg').addEventListener('click', closeSheet);
  $('saveAmount').addEventListener('click', () => { const v = $('amountInput').value.trim(); if (v) act('edit', { amount: v }); });
  $('amountInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('saveAmount').click(); });

  load();
})();
