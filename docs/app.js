// Oxtail Lab front end. Reads data/latest.json, data/history.json, data/players.json (built by scripts/analyze.py).
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs || {})) { if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else if (v != null) e.setAttribute(k, v); } for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : String(c)); return e; };
const f1 = v => v == null || v === '' || Number.isNaN(+v) ? '—' : (+v).toFixed(1);
const pct = v => v == null ? '—' : (+v).toFixed(1) + '%';
const sgn = v => v == null ? '—' : (v > 0 ? '+' : '') + (+v).toFixed(1);
const cls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
const lastName = n => n.endsWith('D/ST') ? n : (n.split(' ').filter(x => !/^(Jr|Sr|II|III|IV|V)\.?$/.test(x)).slice(-1)[0] || n);
const PV = () => D.summary.po_view || D.summary.playoff_weeks;   // [round 1 (null in 4-team playoffs), semis, final]
let D, H, CARDS = null, LID = null, BASE = 'data/', LEAGUES = [];
const tierOf = v => v == null ? null : v >= 80 ? 'legendary' : v >= 55 ? 'epic' : v >= 30 ? 'rare' : v >= 12 ? 'uncommon' : 'common';
let TIERS = {};
const tierEl = n => { const t = typeof n === 'string' ? TIERS[n] : tierOf(n); return t ? h('span', { class: 'tier ' + t, title: 'Tier = rank at his position when healthy' }, t.toUpperCase()) : ''; };
const TEAMC = { ARI: ['#97233F', '#FFB612'], ATL: ['#A71930', '#000000'], BAL: ['#241773', '#9E7C0C'], BUF: ['#00338D', '#C60C30'], CAR: ['#0085CA', '#101820'], CHI: ['#0B162A', '#C83803'], CIN: ['#FB4F14', '#000000'], CLE: ['#311D00', '#FF3C00'],
  DAL: ['#003594', '#869397'], DEN: ['#FB4F14', '#002244'], DET: ['#0076B6', '#B0B7BC'], GB: ['#203731', '#FFB612'], HOU: ['#03202F', '#A71930'], IND: ['#002C5F', '#A2AAAD'], JAX: ['#006778', '#D7A22A'], KC: ['#E31837', '#FFB81C'],
  LV: ['#000000', '#A5ACAF'], LAC: ['#0080C6', '#FFC20E'], LAR: ['#003594', '#FFA300'], MIA: ['#008E97', '#FC4C02'], MIN: ['#4F2683', '#FFC62F'], NE: ['#002244', '#C60C30'], NO: ['#101820', '#D3BC8D'], NYG: ['#0B2265', '#A71930'],
  NYJ: ['#125740', '#FFFFFF'], PHI: ['#004C54', '#A5ACAF'], PIT: ['#101820', '#FFB612'], SF: ['#AA0000', '#B3995D'], SEA: ['#002244', '#69BE28'], TB: ['#D50A0A', '#34302B'], TEN: ['#0C2340', '#4B92DB'], WSH: ['#5A1414', '#FFB612'] };
const initials = n => n.replace(/ (Jr\.|Sr\.|II|III|IV|V)$/, '').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
function avatar(name, nfl, pos, value, size = 28) {
  const [a, b] = TEAMC[nfl] || ['#3f5bff', '#a66bff'], t = TIERS[name] || tierOf(value);
  const glow = { legendary: '#ffb300', epic: '#b05bff', rare: '#33a6ff', uncommon: '#3ddc84' }[t] || '#ffffff33';
  const e = h('span', { class: 'av', title: `${name} · ${pos} · ${nfl}` }, initials(name));
  e.style.cssText = `width:${size}px;height:${size}px;font-size:${size * .4}px;background:linear-gradient(145deg,${a},${b});box-shadow:0 0 0 2px ${glow},0 ${size > 40 ? 6 : 3}px 0 #0007`;
  if (size > 50) e.append(h('span', { class: 'avpos' }, pos));
  return e;
}
const NFL = n => (D.rankings.find(r => r.player === n) || (D.tester.players.find(p => p.n === n) || {})).nfl;
const POS = n => (D.rankings.find(r => r.player === n) || D.tester.players.find(p => p.n === n) || {}).pos || '';
const VAL = n => (CARDS && CARDS[n] && CARDS[n].value) ?? (D && D.rankings && (D.rankings.find(r => r.player === n) || {}).value);
function countUp(el, to, fmt = v => v.toFixed(1) + '%', ms = 1100) { const t0 = performance.now(); const step = t => { const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3); el.textContent = fmt(to * e); if (k < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); }
function confetti(n = 90) { const cs = ['#ffc83d', '#3ddc84', '#4cc9ff', '#ff6bd6', '#a66bff', '#ff9f43']; for (let i = 0; i < n; i++) { const c = h('div', { class: 'confetti' }); c.style.left = Math.random() * 100 + 'vw'; c.style.background = cs[i % cs.length]; c.style.animationDuration = 1.6 + Math.random() * 1.6 + 's'; c.style.animationDelay = Math.random() * .4 + 's'; document.body.append(c); setTimeout(() => c.remove(), 4000); } }
function gauge(el, v) {
  const ns = 'http://www.w3.org/2000/svg', W = 300, R = 120, cx = 150, cy = 150;
  const arc = (a0, a1) => { const p = a => [cx + R * Math.cos(Math.PI * (1 - a)), cy - R * Math.sin(Math.PI * (1 - a))]; const [x0, y0] = p(a0), [x1, y1] = p(a1); return `M${x0} ${y0} A${R} ${R} 0 0 1 ${x1} ${y1}`; };
  el.innerHTML = `<svg viewBox="0 0 ${W} 170"><defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stop-color="#ff4d6d"/><stop offset=".5" stop-color="#ffc83d"/><stop offset="1" stop-color="#3ddc84"/></linearGradient></defs>
    <path d="${arc(0, 1)}" stroke="#0f1138" stroke-width="26" fill="none" stroke-linecap="round"/><path id="garc" d="${arc(0, 1)}" stroke="url(#gg)" stroke-width="26" fill="none" stroke-linecap="round" pathLength="100" stroke-dasharray="0 100"/></svg>`;
  const a = $('#garc', el), k = Math.min(1, v / 60);   // gauge full at 60% title odds
  requestAnimationFrame(() => { a.style.transition = 'stroke-dasharray 1.2s cubic-bezier(.2,.8,.2,1)'; a.setAttribute('stroke-dasharray', `${(k * 100).toFixed(1)} 100`); });
}
const tip = $('#tip');
const showTip = (e, t) => { tip.hidden = false; tip.textContent = t; tip.style.left = Math.min(e.clientX + 12, innerWidth - 330) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; };
const hideTip = () => tip.hidden = true;

// ---------- generic sortable table ----------
function table(el, cols, rows, { key, rowClass, onRow, sort } = {}) {
  let st = sort || null;
  const draw = () => {
    let rs = [...rows];
    if (st) { const c = cols.find(c => c.k === st.k); const g = c.sv || (r => r[c.k]); rs.sort((a, b) => { const x = g(a), y = g(b); return (x == null) - (y == null) || (typeof x === 'string' ? x.localeCompare(y) : x - y) * st.d; }); }
    el.replaceChildren(
      h('thead', {}, h('tr', {}, cols.map(c => h('th', { class: (c.l ? 'l ' : '') + 's', title: c.t || '', onclick: () => { st = { k: c.k, d: st && st.k === c.k ? -st.d : (c.l ? 1 : -1) }; draw(); } }, c.h)))),
      h('tbody', {}, rs.map(r => { const tr = h('tr', { class: (rowClass ? rowClass(r) : '') + (onRow ? ' click' : '') }, cols.map(c => { const v = c.f ? c.f(r) : r[c.k]; const td = h('td', { class: (c.l ? 'l ' : '') + (c.c ? c.c(r) : '') }); td.append(v && v.nodeType ? v : (v == null ? '—' : v)); return td; })); if (onRow) tr.addEventListener('click', e => { if (!e.target.closest('.pl')) onRow(r); }); return tr; })));
  };
  draw();
}
const pl = n => /D\/ST$/.test(n) ? h('span', {}, n) : h('a', { class: 'pl', href: '#', onclick: e => { e.preventDefault(); e.stopPropagation(); openCard(n); } }, n);

// ---------- charts ----------
function hbar(el, rows, { label, value, fmt, color, tipText }) {
  el.replaceChildren(); if (!rows.length) { el.append(h('div', { class: 'mut' }, 'Nothing to show')); return; }
  const W = Math.max(300, el.clientWidth - 20), bh = 22, gap = 6, H_ = rows.length * (bh + gap) + 6;
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H_}`);
  const lw = Math.min(W * 0.5, Math.max(...rows.map(r => label(r).length)) * 7.8 + 12);   // a bit generous so names never get chopped
  const vals = rows.map(value), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals) || 1;
  const x = v => lw + (v - lo) / (hi - lo) * (W - lw - 54);
  rows.forEach((r, i) => {
    const y = i * (bh + gap) + 3, v = value(r), g = document.createElementNS(ns, 'g');
    const t = document.createElementNS(ns, 'text'); t.setAttribute('x', lw - 8); t.setAttribute('y', y + bh / 2 + 4); t.setAttribute('text-anchor', 'end'); t.textContent = label(r); g.append(t);
    const rc = document.createElementNS(ns, 'rect'); rc.setAttribute('x', x(Math.min(0, v))); rc.setAttribute('y', y + 2); rc.setAttribute('height', bh - 4); rc.setAttribute('rx', 3); rc.setAttribute('width', Math.max(1, Math.abs(x(v) - x(0)))); rc.setAttribute('fill', color(r)); g.append(rc);
    const vt = document.createElementNS(ns, 'text'); vt.setAttribute('x', (v >= 0 ? x(v) : x(0)) + 5); vt.setAttribute('y', y + bh / 2 + 4); vt.setAttribute('style', 'fill:#e8ecf2'); vt.textContent = fmt(v); g.append(vt);
    g.addEventListener('mousemove', e => showTip(e, tipText ? tipText(r) : label(r) + ': ' + fmt(v))); g.addEventListener('mouseleave', hideTip);
    svg.append(g);
  });
  el.append(svg);
}
function line(el, pts, { yfmt = v => v.toFixed(0) + '%', color = '#f0b429', label } = {}) {
  el.replaceChildren(); if (pts.length < 2) { el.append(h('div', { class: 'mut', style: 'padding:10px' }, 'The trend line fills in after a few updates.')); return; }
  const W = Math.max(300, el.clientWidth - 20), H_ = 170, pad = 30, ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H_}`);
  const ys = pts.map(p => p.y), lo = Math.max(0, Math.min(...ys) - 5), hi = Math.min(100, Math.max(...ys) + 5);
  const X = i => pad + i / (pts.length - 1) * (W - pad - 10), Y = v => H_ - 22 - (v - lo) / (hi - lo || 1) * (H_ - 40);
  [lo, (lo + hi) / 2, hi].forEach(v => { const t = document.createElementNS(ns, 'text'); t.setAttribute('x', 2); t.setAttribute('y', Y(v) + 4); t.textContent = yfmt(v); svg.append(t); const l = document.createElementNS(ns, 'line'); l.setAttribute('x1', pad); l.setAttribute('x2', W - 10); l.setAttribute('y1', Y(v)); l.setAttribute('y2', Y(v)); l.setAttribute('stroke', '#283141'); svg.append(l); });
  const p = document.createElementNS(ns, 'path'); p.setAttribute('d', pts.map((q, i) => (i ? 'L' : 'M') + X(i) + ' ' + Y(q.y)).join('')); p.setAttribute('fill', 'none'); p.setAttribute('stroke', color); p.setAttribute('stroke-width', 2.5); svg.append(p);
  pts.forEach((q, i) => { const c = document.createElementNS(ns, 'circle'); c.setAttribute('cx', X(i)); c.setAttribute('cy', Y(q.y)); c.setAttribute('r', 4); c.setAttribute('fill', color); c.addEventListener('mousemove', e => showTip(e, (label ? label(q) : '') + yfmt(q.y))); c.addEventListener('mouseleave', hideTip); svg.append(c); });
  el.append(svg);
}

// ---------- tabs ----------
function tab(name) { $$('nav.tabs button').forEach(b => { b.classList.toggle('on', b.dataset.tab === name); if (b.dataset.tab === name) b.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' }); }); $$('section').forEach(s => s.hidden = s.id !== name); history.replaceState(null, '', location.search + '#' + name); if (RENDER[name]) RENDER[name](); }
$$('nav.tabs button').forEach(b => b.addEventListener('click', () => tab(b.dataset.tab)));
function seg(id, onChange) { $$('#' + id + ' button').forEach(b => b.addEventListener('click', () => { $$('#' + id + ' button').forEach(x => x.classList.toggle('on', x === b)); onChange(b.dataset.v); })); }

// ---------- home ----------
function renderTodo() {
  const T = D.todo || [];
  const dk = t => `done:${LID}:${D.summary.week}:${t.title}`, isDone = t => { try { return localStorage.getItem(dk(t)) === '1'; } catch (e) { return false; } };
  $('#todo').replaceChildren(...T.map((t, i) => { const cb = h('input', { type: 'checkbox', class: 'tdone', title: 'Mark done' }); cb.checked = isDone(t);
    cb.addEventListener('click', e => { e.stopPropagation(); try { localStorage.setItem(dk(t), cb.checked ? '1' : ''); } catch (er) {} c.classList.toggle('done', cb.checked); });
    const c = h('div', { class: 'todoi' + (t.urgent ? ' urgent' : '') + (isDone(t) ? ' done' : ''), onclick: () => tab(t.tab) }, cb, h('div', { class: 'ti' }, t.icon), h('div', { style: 'flex:1' }, h('b', {}, t.title), h('div', { class: 'td' }, t.detail))); c.style.animationDelay = i * 60 + 'ms'; return c; }));
  const others = LEAGUES.filter(l => l.id !== LID);
  $('#otherl').replaceChildren(...(others.length ? [h('div', { class: 'mut', style: 'font-size:12px;font-weight:800;margin:10px 0 6px' }, 'YOUR OTHER LEAGUES'), h('div', { class: 'chips2' }, others.map(l => { const urg = (l.todo || []).filter(t => t.urgent);
    return h('a', { class: 'chip2 lchip' + (urg.length ? ' urgent' : ''), href: `?league=${l.id}#home`, onclick: () => { try { localStorage.setItem('league', l.id); } catch (e) {} } }, h('b', {}, l.name.trim()), h('small', {}, urg.length ? ` 🚨 ${urg[0].title}` : (l.todo || []).length ? ` · ${(l.todo || []).length} to do` : ' · all set')); }))] : []));
}
function renderToss() {
  const T = D.tossups || [];
  $('#toss').replaceChildren(...(T.length ? T.slice(0, 4).map(t => h('div', { class: 'toss' },
    h('div', { class: 'tossh' }, h('span', { class: 'mut' }, t.slot), h('span', { class: 'tag ' + (t.call === 'Coin flip' ? 'stretch' : 'real') }, t.call.toUpperCase())),
    h('div', { class: 'tossv' }, h('div', { class: t.p_start_better >= 50 ? 'win' : '' }, pl(t.start), h('small', {}, ` ${f1(t.start_pts)} · ${t.start_opp}`)), h('span', { class: 'mut' }, 'vs'), h('div', { class: t.p_start_better < 50 ? 'win' : '' }, pl(t.alt), h('small', {}, ` ${f1(t.alt_pts)} · ${t.alt_opp}`))),
    h('div', { class: 'tbarw' }, h('i', { style: `width:${t.p_start_better}%` })), h('div', { class: 'mut', style: 'font-size:12px' }, `${lastName(t.start)} outscores ${lastName(t.alt)} in ${Math.round(t.p_start_better)}% of sims`),
    t.notes.length ? h('ul', { class: 'bwhy' }, t.notes.map(n => h('li', {}, n))) : ''))
    : [h('p', { class: 'mut', style: 'margin:0' }, 'No close calls this week. Your best lineup is clear.')]));
}
function renderHome() {
  renderTodo(); renderToss();
  const s = D.summary, k = s.kpis, prev = H.runs.length > 1 ? H.runs[H.runs.length - 2] : null;
  gauge($('#gauge'), k.ch); countUp($('#gnum'), k.ch);
  const rank = [...D.league].sort((a, b) => b.champ_pct - a.champ_pct).findIndex(r => r.me) + 1;
  $('#grank').textContent = `#${rank} of ${D.league.length} in title odds` + (prev && Math.abs(k.ch - prev.champ_pct) >= 0.1 ? ` · ${sgn(k.ch - prev.champ_pct)} since last update` : '');
  const tile = (c, label, v, pv) => { const vEl = h('span', {}, '0%'), bar = h('i'); const t = h('div', { class: 'tile ' + c }, h('div', { class: 'k' }, label), h('div', { class: 'v' }, vEl, pv != null && Math.abs(v - pv) >= 0.1 ? h('span', { class: 'd' }, sgn(v - pv)) : null), h('div', { class: 'bar' }, bar)); setTimeout(() => { countUp(vEl, v); bar.style.width = Math.min(100, v) + '%'; }, 60); return t; };
  $('#kpis').replaceChildren(tile('t1', '🏁 Make the final', k.fi, prev && prev.final_pct), tile('t2', '✅ Make playoffs', k.po, prev && prev.playoffs_pct), ...(PV()[0] ? [tile('t3', '😴 First round bye', k.bye, prev && prev.bye_pct)] : []), tile('t4', '👑 1 seed', k.s1, prev && prev.seed1_pct));
  $('#take').replaceChildren(...s.take.map((t, i) => { const li = h('li', {}, t); li.style.animationDelay = i * 70 + 'ms'; return li; }));
  const tw = s.this_week; $('#tw-h').textContent = `🗓️ Week ${tw.week} best lineup`;
  const lu = h('table', {}, h('tbody', {}, tw.lineup.map(x => h('tr', {}, h('td', { class: 'mut' }, x.slot), h('td', { class: 'l' }, h('span', { style: 'display:inline-flex;gap:8px;align-items:center' }, avatar(x.player, NFL(x.player), x.slot, VAL(x.player), 26), pl(x.player), tierEl((x.player)))), h('td', {}, f1(x.pts), x.opp === 'BYE' ? h('span', { class: 'opp neg' }, 'bye') : x.chance != null && x.chance < 1 ? h('span', { class: 'opp ' + (x.chance < 0.7 ? 'neg' : '') }, Math.round(x.chance * 100) + '% to play') : '')))));
  const sw = (D.schedule || []).find(x => x.w === tw.week), alerts = [];
  if (sw) sw.lineup.forEach(x => { if (x.opp !== 'BYE' && x.chance >= 0.8) return;
    const fa = (D.waivers || []).filter(w => w.pos === POS(x.player) && w.this_week_gain > 0).sort((a, b) => b.this_week_gain - a.this_week_gain)[0];
    const benchAlt = sw.bench.find(b => b.opp !== 'BYE' && (D.tester.elig[x.slot] || []).includes(b.pos));
    alerts.push(h('div', { class: 'alert' }, h('b', {}, x.opp === 'BYE' ? `⚠️ ${x.player} is on bye. ` : `⚠️ ${x.player} might not play (${Math.round(x.chance * 100)}%). `),
      fa ? `Add ${fa.player} (+${f1(fa.this_week_gain)} this week), drop ${fa.drop}.` : benchAlt ? `Start ${benchAlt.player} (${f1(benchAlt.pts)}) instead.` : `Check his status before kickoff.`)); });
  $('#thisweek').replaceChildren(...alerts,
    s.lock ? h('p', { class: 'sub', style: 'margin:0 0 8px' }, `🔒 First lock: ${s.lock.when_pt} PT (${s.lock.players.map(lastName).join(', ')})`) : '',
    tw.opponent ? h('p', { style: 'margin:0 0 10px' }, `vs ${tw.opponent}: you win `, h('b', { style: 'color:var(--gold)' }, pct(tw.win_pct)), ` of sims. You ${f1(tw.total)} vs them ${f1(tw.opp_proj)}.`) : '',
    h('div', { class: 'tw' }, lu), h('div', { class: 'chips2', style: 'margin-top:10px' }, h('span', { class: 'mut', style: 'font-size:12px;font-weight:800;align-self:center' }, 'BENCH'), tw.bench.filter(b => b.pts > 0).map(b => h('span', { class: 'chip2' }, pl(b.player), h('small', {}, ' ' + f1(b.pts))))),
    tw.bench.some(b => b.pts <= 0) ? h('p', { class: 'sub', style: 'margin:6px 0 0' }, 'Out or on bye: ' + tw.bench.filter(b => b.pts <= 0).map(b => b.player).join(', ')) : '');
  line($('#trend'), H.runs.map(r => ({ y: r.champ_pct, at: r.at, w: r.week })), { label: q => `Wk ${q.w} · ${new Date(q.at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric' })}: ` });
  hbar($('#swing'), s.swing, { label: r => r.what.replace(' out for the playoffs', ''), value: r => r.change_pts, fmt: v => sgn(v) + '%', color: () => '#ff4d6d', tipText: r => `If ${r.what.replace(' out for the playoffs', '')} gets hurt for the playoffs: title odds ${sgn(r.change_pts)}%` });
}

// ---------- trades ----------
let TK = 'realistic';
const LIM = { trades: 8, boom: 6, ranks: 40 };
const moreBtn = (key, total, redraw) => total > LIM[key] ? h('button', { class: 'btn small blue', style: 'margin:10px auto;display:block', onclick: () => { LIM[key] += key === 'ranks' ? 40 : 8; redraw(); } }, `SHOW MORE (${total - LIM[key]} left)`) : '';
function deadlineEl() {
  const dl = D.summary.trade_deadline; if (!dl) return '';
  const days = (dl - Date.now()) / 864e5; if (days < 0) return h('div', { class: 'alert' }, '🔒 The trade deadline has passed. Trades are locked for the season.');
  return h('div', { class: 'alert', style: days > 7 ? 'background:#13163f;border-color:#ffc83d' : '' }, `⏳ Trade deadline: ${new Date(dl).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })} (${Math.ceil(days)} day${Math.ceil(days) === 1 ? '' : 's'} left).`);
}
function renderTrades() {
  $('#tdl').replaceChildren(deadlineEl());
  const rows = D.trades.filter(t => TK === 'all' || t.kind === TK);
  const names = s => s.split(' + ');
  const side = (cls_, k, list) => h('div', { class: 'side ' + cls_ }, h('div', { class: 'k' }, k), ...names(list).map(n => h('div', { class: 'p' }, avatar(n, NFL(n), POS(n), VAL(n), 34), h('div', { class: 'pn' }, pl(n), h('div', {}, h('span', { class: 'pp2' }, POS(n) + ' · ' + (NFL(n) || '')), ' ', tierEl((n)))))));
  $('#tcards').replaceChildren(...(rows.length ? rows.slice(0, LIM.trades).map((r, i) => { const why = h('div', { class: 'why', hidden: '' }); const c = h('div', { class: 'tcard ' + (r.blocked ? 'blocked' : r.kind) },
      h('div', { class: 'who' }, h('span', {}, '🤝 ' + r.partner), h('span', {}, r.accept_pct != null ? h('span', { class: 'tag ' + (r.accept_pct >= 60 ? 'real' : r.accept_pct >= 35 ? 'stretch' : 'no'), title: 'Rough chance they say yes' }, `${r.accept_pct}% YES`) : '', ' ', h('span', { class: 'tag ' + (r.blocked ? 'no' : r.kind === 'realistic' ? 'real' : 'stretch') }, r.blocked ? 'BLOCKED' : r.kind === 'realistic' ? 'REALISTIC' : 'STRETCH'))),
      h('div', { class: 'swap' }, side('give', 'YOU GIVE', r.give), h('div', { class: 'arrow' }, '➜'), side('get', 'YOU GET', r.get)),
      r.why_them && r.why_them.length ? h('div', { class: 'tpitch' }, h('div', { class: 'k' }, '🎯 WHY ' + r.partner.toUpperCase()), h('div', {}, r.why_them.slice(0, 2).join(' '))) : '',
      r.pitch ? h('div', { class: 'tmsg' }, h('div', { class: 'k' }, '💬 SEND THEM THIS'), h('div', { class: 'txt' }, r.pitch),
        h('button', { class: 'btn small', onclick: e => { navigator.clipboard.writeText(r.pitch).then(() => { e.target.textContent = 'COPIED ✓'; setTimeout(() => e.target.textContent = 'COPY', 1500); }); } }, 'COPY')) : '',
      h('div', { class: 'foot' }, h('div', { class: 'boost', style: r.change_pts < 0 ? 'color:var(--red)' : '' }, sgn(r.change_pts) + '%', h('small', {}, `title odds → ${pct(r.title_pct)}`)),
        h('div', { class: 'meta' }, `Their lineup ${sgn(r.partner_weekly_gain)}/wk`, h('br'), r.wk16_before != null ? `Your Wk${PV()[1]}: ${f1(r.wk16_before)} → ${f1(r.wk16)}` : `Your Wk${PV()[1]}: ${f1(r.wk16)}`, h('br'), r.wk17_before != null ? `Your Wk${PV()[2]}: ${f1(r.wk17_before)} → ${f1(r.wk17)}` : `Your Wk${PV()[2]}: ${f1(r.wk17)}`),
        h('div', { class: 'tbtns' }, h('button', { class: 'btn small blue', onclick: () => { why.hidden = !why.hidden; if (!why.hidden && !why.childNodes.length) why.append(...whyTrade(r)); } }, 'WHY?'),
          h('button', { class: 'btn small', onclick: () => { LAB.give = new Set(names(r.give)); LAB.partner = r.partner_id; LAB.get = new Set(names(r.get)); tab('lab'); setTimeout(() => { $('#run').click(); $('#run').scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 120); } }, 'TEST IT'))), why); c.style.animationDelay = Math.min(i, 12) * 40 + 'ms'; return c; })
    : [h('div', { class: 'box mut' }, 'No trades in this group right now.')]), moreBtn('trades', rows.length, renderTrades));
  table($('#ttable'), [
    { k: 'give', h: 'You give', l: 1, f: r => h('span', {}, ...names(r.give).flatMap((n, i) => [i ? ' + ' : '', pl(n)])) },
    { k: 'get', h: 'You get', l: 1, f: r => h('span', {}, ...names(r.get).flatMap((n, i) => [i ? ' + ' : '', pl(n)])) },
    { k: 'partner', h: 'From', l: 1 },
    { k: 'change_pts', h: 'Title +pts', f: r => sgn(r.change_pts), c: r => cls(r.change_pts) },
    { k: 'title_pct', h: 'Title %', f: r => pct(r.title_pct) },
    { k: 'partner_weekly_gain', h: 'Their pts/wk', t: "How much the other team's best lineup changes per week for the rest of the regular season", f: r => sgn(r.partner_weekly_gain), c: r => r.partner_weekly_gain >= -1 ? 'pos' : 'neg' },
    { k: 'value_ratio', h: 'You pay / you get', t: 'Trade value you send ÷ value you get (over 1 = you overpay, so they should like it)', f: r => (+r.value_ratio).toFixed(2) },
    { k: 'wk16', h: `Your Wk${PV()[1]}`, f: r => f1(r.wk16) }, { k: 'wk17', h: `Your Wk${PV()[2]}`, f: r => f1(r.wk17) },
    { k: 'accept_pct', h: 'Chance they say yes', f: r => r.accept_pct != null ? r.accept_pct + '%' : '—' },
    { k: 'kind', h: 'Type', l: 1, f: r => h('span', { class: 'tag ' + (r.blocked ? 'no' : r.kind === 'realistic' ? 'real' : 'stretch') }, r.blocked ? 'Blocked' : r.kind === 'realistic' ? 'Realistic' : 'Stretch') }
  ], rows, { rowClass: r => r.blocked ? 'dim' : '', onRow: r => { LAB.give = new Set(names(r.give)); LAB.partner = r.partner_id; LAB.get = new Set(names(r.get)); tab('lab'); } });
}
seg('tkind', v => { TK = v; renderTrades(); });

function whyTrade(r) {
  const wk = r.weeks || [], reg = wk.filter(x => !x.po), po = wk.filter(x => x.po);
  const avg = (a, k) => a.length ? a.reduce((s, x) => s + x[k], 0) / a.length : 0;
  const dReg = avg(reg, 'after') - avg(reg, 'before'), dPo = avg(po, 'after') - avg(po, 'before'), dThem = (r.their_after ?? 0) - (r.their_before ?? 0);
  const mx = Math.max(1, ...wk.map(x => Math.abs(x.after - x.before)));
  const bars = h('div', { class: 'wbars' }, wk.map(x => { const d = x.after - x.before; const b = h('div', { class: 'wb' + (x.po ? ' po' : '') }, h('div', { class: 'wbv ' + (d >= 0 ? 'up' : 'dn') }), h('div', { class: 'k' }, (x.po ? '🏆' : '') + 'W' + x.w), h('div', { class: 'k ' + cls(d) }, sgn(d))); b.firstChild.style.height = Math.max(4, Math.abs(d) / mx * 46) + 'px'; return b; }));
  const L = [];
  const lists = [];
  if (r.why_me && r.why_me.length) lists.push(h('div', {}, h('b', {}, '✅ Why you do it'), h('ul', {}, r.why_me.map(t => h('li', {}, t)))));
  if (r.why_them && r.why_them.length) lists.push(h('div', {}, h('b', {}, `🤝 Why ${r.partner} might say yes`), h('ul', {}, r.why_them.map(t => h('li', {}, t)))));
  L.push(`📊 Your lineup gets ${sgn(dReg)} a week the rest of the regular season and ${sgn(dPo)} a week in your playoff games. Those playoff weeks count double, which is why this ${dPo > dReg ? 'trade is built for the playoffs' : 'helps now and later'}.`);
  L.push(`🤝 ${r.partner}'s lineup ${dThem >= 0 ? 'gets better' : 'gets worse'} by ${f1(Math.abs(dThem))} a week (${f1(r.their_before)} → ${f1(r.their_after)}). They're ${r.their_po_pct >= 90 ? 'a playoff lock' : r.their_po_pct >= 8 ? `a bubble team at ${f1(r.their_po_pct)}% to make it, so wins now matter to them` : 'basically eliminated, so they should care about value, not this week'}.`);
  L.push(`💎 Market value: you send ${f1(r.mv_send)} and get ${f1(r.mv_get)} (points per game over a replacement starter). ${r.value_ratio >= 1 ? 'You\'re giving fair or better value, so it\'s an easy sell.' : 'You\'re asking for more than you give, so expect pushback.'}`);
  L.push(`🏆 Title odds ${pct(D.summary.kpis.ch)} → ${pct(r.title_pct)} (${sgn(r.change_pts)}).`);
  return [...lists, bars, ...L.map(t => h('p', {}, t)), h('button', { class: 'btn small', onclick: () => window.askCoach && askCoach(`Explain this trade for me: I give ${r.give} and get ${r.get} from ${r.partner}. Why does it help me, would they actually accept, and what are the risks?`) }, '🤖 ASK COACH ABOUT IT')];
}
function openTeam(id) {
  const t = (D.teams || {})[String(id)]; if (!t) return;
  const WL = t.weeks_lu || [];
  let cur = (WL.find(x => x.label === 'Final') || WL[WL.length - 1] || {}).w;
  const luBox = h('div', {}), wkBtns = h('div', { class: 'weekbar', style: 'margin-top:10px' });
  const drawLu = () => {
    const L = WL.find(x => x.w === cur); if (!L) return;
    wkBtns.replaceChildren(...WL.map(x => h('button', { class: 'wk' + (x.w === cur ? ' on' : '') + (/Final|Semis|Round/.test(x.label) ? ' po' : ''), onclick: () => { cur = x.w; drawLu(); } }, h('div', { class: 'n' }, x.label === 'Final' ? '🏆 Final' : x.label === 'Semis' ? '⚔️ Semis' : x.label === 'Round 1' ? 'Rd 1' : 'W' + x.w), h('div', { class: 's' }, f1(x.expected)), x.win_pct != null ? h('div', { class: 's ' + (x.win_pct >= 50 ? 'pos' : 'neg') }, Math.round(x.win_pct) + '%') : '')));
    const top = L.lineup.slice().sort((a, b) => b.pts - a.pts).slice(0, 3);
    const easy = L.lineup.filter(x => x.rank >= 22).map(x => lastName(x.player));
    const risky = L.lineup.filter(x => x.chance < 0.8).map(x => `${x.player} (${Math.round(x.chance * 100)}%)`);
    const vs = L.opponent ? h('div', { class: 'vs', style: 'margin-top:4px' }, h('div', { class: 'side' }, h('div', { class: 'k' }, t.name.toUpperCase()), h('div', { class: 'v' }, f1(L.expected))),
        h('div', { class: 'mid' }, h('div', { class: 'big ' + (L.win_pct >= 50 ? 'pos' : 'neg') }, Math.round(L.win_pct) + '%'), h('div', { class: 'k' }, 'to win')),
        h('div', { class: 'side', style: 'cursor:pointer', title: 'Open their card', onclick: () => { m.remove(); openTeam(L.opp_id); } }, h('div', { class: 'k' }, L.opponent.toUpperCase() + ' ↗'), h('div', { class: 'v' }, f1(L.opp_expected)), h('div', { class: 'mut', style: 'font-size:12px' }, L.opp_record)))
      : /Final|Semis|Round/.test(L.label) ? h('p', { class: 'sub', style: 'margin:4px 0 0' }, 'Playoff opponent depends on the bracket.') : '';
    const OL = L.opp_id != null ? (((D.teams || {})[String(L.opp_id)] || {}).weeks_lu || []).find(x => x.w === L.w) : null;
    if (OL) {   // head-to-head: their lineup | slot | opponent's lineup
      const cell = (x, right) => x ? h('div', { class: 'mu-p' + (right ? ' r' : '') }, h('div', { class: 'mu-n' }, lastName(x.player) === x.player ? x.player : x.player.split(' ')[0][0] + '. ' + lastName(x.player)),
        h('div', { class: 'mu-s' }, h('b', {}, f1(x.pts)), ' ', h('span', { class: x.rank >= 22 ? 'pos' : x.rank <= 11 ? 'neg' : 'mut' }, x.opp), x.chance < 1 ? h('span', { class: 'neg' }, ` ${Math.round(x.chance * 100)}%`) : '')) : h('div', { class: 'mu-p' });
      const rows = L.lineup.map((x, i) => h('div', { class: 'mu-row' }, cell(x, false), h('div', { class: 'mu-slot' }, x.slot), cell(OL.lineup[i], true)));
      luBox.replaceChildren(vs, h('div', { class: 'mu' }, h('div', { class: 'mu-row mu-h' }, h('div', { class: 'mu-p' }, h('b', {}, t.name)), h('div', { class: 'mu-slot' }, ''), h('div', { class: 'mu-p r' }, h('b', { style: 'cursor:pointer', onclick: () => { m.remove(); openTeam(L.opp_id); } }, L.opponent + ' ↗'))),
        ...rows, h('div', { class: 'mu-row mu-h' }, h('div', { class: 'mu-p' }, h('b', {}, f1(L.total))), h('div', { class: 'mu-slot' }, 'TOT'), h('div', { class: 'mu-p r' }, h('b', {}, f1(OL.total))))),
        h('p', { class: 'sub', style: 'margin:8px 0 0' }, 'Points if he plays. Red % = chance to play. Matchup colors: green easy, red tough.'));
      setTimeout(() => { const on = wkBtns.querySelector('.on'); if (on) wkBtns.scrollLeft = on.offsetLeft - wkBtns.clientWidth / 2 + on.clientWidth / 2; }, 30);
      return;
    }
    luBox.replaceChildren(vs,
      h('h3', {}, `${L.label === 'Final' || L.label === 'Semis' ? '🏆 ' : '🗓️ '}${L.label}${/^Week/.test(L.label) ? '' : ` (Wk ${L.w})`} best lineup · ${f1(L.total)}`),
      h('p', { class: 'sub', style: 'margin:0 0 8px' }, `Why ${f1(L.expected)}: their top scorers that week are ${top.map(x => `${x.player} ${f1(x.pts)}`).join(', ')}.` + (easy.length ? ` Easy matchups for ${easy.join(', ')}.` : '') + (risky.length ? ` Might not play: ${risky.join(', ')}.` : '') + (Math.abs(L.total - L.expected) >= 0.5 ? ` The ${f1(L.expected)} on the League page is a bit ${L.expected < L.total ? 'lower' : 'higher'} because it also covers bench guys stepping in if someone sits.` : '')),
      h('div', { class: 'tw' }, h('table', {}, h('tbody', {}, L.lineup.map(x => h('tr', {}, h('td', { class: 'mut' }, x.slot),
        h('td', { class: 'l' }, h('span', { style: 'display:inline-flex;gap:8px;align-items:center' }, avatar(x.player, NFL(x.player), POS(x.player), VAL(x.player), 26), h('span', {}, pl(x.player), ' ', tierEl(x.player),
          h('div', { class: 'mut', style: 'font-size:12px' }, x.opp, x.rank ? h('span', { class: x.rank >= 22 ? 'pos' : x.rank <= 11 ? 'neg' : 'mut' }, x.rank >= 22 ? ' · easy' : x.rank <= 11 ? ' · tough' : ' · avg') : '')))),
        h('td', {}, f1(x.pts), x.chance < 1 ? h('span', { class: 'opp ' + (x.chance < 0.7 ? 'neg' : '') }, Math.round(x.chance * 100) + '% to play') : ''))),
        h('tr', {}, h('td', {}), h('td', { class: 'l' }, h('b', {}, 'TOTAL')), h('td', {}, h('b', {}, f1(L.total))))))));
    setTimeout(() => { const on = wkBtns.querySelector('.on'); if (on) wkBtns.scrollLeft = on.offsetLeft - wkBtns.clientWidth / 2 + on.clientWidth / 2; }, 30);
  };
  drawLu();
  const bench = t.roster.filter(x => !x.starter);
  const m = h('div', { class: 'modal', onclick: e => { if (e.target === m) m.remove(); } }, h('div', { class: 'mbox' },
    h('button', { class: 'x', onclick: () => m.remove() }, '×'),
    h('h2', {}, (t.me ? '⭐ ' : '') + t.name),
    h('div', { class: 'sub', style: 'margin:6px 0 14px;font-size:14px' }, `${t.record} · ${f1(t.pf)} points for · ${t.status}`),
    h('div', { class: 'tiles', style: 'grid-template-columns:repeat(auto-fit,minmax(150px,1fr))' },
      [['t2', '✅ Playoffs', t.odds.po], ['t3', '😴 Bye', t.odds.bye], ['t1', '🏁 Final', t.odds.fi], ['t4', '🏆 Title', t.odds.ch]].map(([c, k, v]) => h('div', { class: 'tile ' + c }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, pct(v))))),
    wkBtns, luBox,
    h('h3', {}, '🪑 Bench'), h('div', { class: 'chips2' }, bench.map(x => h('span', { class: 'chip2' }, pl(x.player), h('small', {}, ` ${x.pos} · ${f1(x.ros_pg)}/g`), x.status && x.status !== 'ACTIVE' ? h('small', { class: 'neg' }, ' ' + x.status.replace('_', ' ')) : ''))),
    t.schedule.length ? [h('h3', {}, '📅 Their remaining games'), h('div', { class: 'chips2' }, t.schedule.map(g => h('span', { class: 'chip2' }, `Wk ${g.w}: ${g.opp}`)))] : '',
    !t.me ? h('div', { class: 'bar', style: 'margin-top:16px' }, h('button', { class: 'btn small', onclick: () => { m.remove(); LAB.partner = t.id; LAB.get.clear(); tab('lab'); } }, '🧪 BUILD A TRADE WITH THEM'),
      h('button', { class: 'btn small blue', onclick: () => { m.remove(); window.askCoach && askCoach(`What's the best trade I could realistically make with ${t.name}? Look at their roster, needs and odds.`); } }, '🤖 ASK COACH')) : ''));
  document.body.append(m);
}

// ---------- trade lab ----------
const LAB = { give: new Set(), get: new Set(), out: new Set(), partner: null, q: '' };
function renderLab() {
  const T = D.tester, me = T.me, val = p => (p.e[T.weeks.indexOf(PV()[1])] || 0) + (p.e[T.weeks.indexOf(PV()[2])] || 0);
  const pick = (box, list, set) => { box.replaceChildren(...list.sort((a, b) => val(b) - val(a)).map(p => { const cb = h('input', { type: 'checkbox' }); cb.checked = set.has(p.n); const lb = h('label', { class: cb.checked ? 'sel' : '' }, cb, h('span', {}, p.n + ' · ' + p.pos), tierEl((p.n)), h('span', { class: 'pp' }, f1(val(p)) + ' playoff pts')); cb.addEventListener('change', () => { cb.checked ? set.add(p.n) : set.delete(p.n); lb.classList.toggle('sel', cb.checked); }); return lb; })); };
  pick($('#give'), T.players.filter(p => p.t === me && p.pos !== 'K'), LAB.give);
  const sel = $('#partner'); sel.replaceChildren(h('option', { value: '' }, 'Pick a team'), ...T.teams.filter(t => t.id !== me).map(t => h('option', { value: t.id }, t.name)), h('option', { value: '0' }, 'Free agents (add/drop)'));
  sel.value = LAB.partner == null ? '' : String(LAB.partner);
  sel.onchange = () => { LAB.partner = sel.value === '' ? null : +sel.value; LAB.get.clear(); renderLab(); };
  if (LAB.partner == null) $('#get').replaceChildren(h('span', { class: 'mut' }, 'Pick a team first'));
  else pick($('#get'), T.players.filter(p => p.t === LAB.partner && p.pos !== 'K'), LAB.get);
  const q = $('#outq'); q.value = LAB.q; q.oninput = () => { LAB.q = q.value; drawOut(); };
  const drawOut = () => { const s = LAB.q.toLowerCase(); const list = T.players.filter(p => p.t && (LAB.out.has(p.n) || (s && p.n.toLowerCase().includes(s)) || (!s && p.t === me))).slice(0, 40); $('#outlist').replaceChildren(...list.map(p => { const cb = h('input', { type: 'checkbox' }); cb.checked = LAB.out.has(p.n); const lb = h('label', { class: cb.checked ? 'sel' : '' }); cb.addEventListener('change', () => { cb.checked ? LAB.out.add(p.n) : LAB.out.delete(p.n); lb.classList.toggle('sel', cb.checked); }); lb.append(cb, h('span', {}, `${p.n} · ${p.pos}`), h('span', { class: 'pp' }, (T.teams.find(t => t.id === p.t) || {}).name || '')); return lb; })); };
  drawOut();
}
function labSim(give, get, partner, out, N) {
  const T = D.tester, me = T.me, W = T.weeks, ros = {};
  T.teams.forEach(t => ros[t.id] = []);
  T.players.forEach(p => { let t = p.t; if (t === me && give.has(p.n)) t = partner; else if (t === partner && get.has(p.n)) t = me; if (ros[t]) ros[t].push(p); });
  // same math as the main sim: for each week, the 4 most uncertain starters are either in or out, weighted by their chance to play
  const lineupTot = (pl_, wi, on) => { const a = pl_.map(p => ({ pos: p.pos, v: on(p) ? p.vv[wi] : 0, n: p.n })).sort((x, y) => y.v - x.v); const u = new Set(); let s = 0; for (const [sl, k] of T.slots) { let c = k; for (const x of a) { if (!c) break; if (u.has(x.n)) continue; if ((T.elig[sl] || []).includes(x.pos)) { u.add(x.n); s += x.v; c--; } } } return s; };
  const SC = {}, E = {}, SD_ = {};
  T.teams.forEach(t => {
    const pl_ = ros[t.id].map(p => ({ ...p, vv: p.v.map((v, i) => out.has(p.n) && W[i] >= T.po[0] ? 0 : v), qq: p.q.map((q, i) => out.has(p.n) && W[i] >= T.po[0] ? 0 : q) }));
    SC[t.id] = W.map((_, wi) => {
      const unc = pl_.filter(p => p.qq[wi] > 0 && p.qq[wi] < 1 && p.vv[wi] > 4).sort((a, b) => b.vv[wi] - a.vv[wi]).slice(0, 4), L = [];
      for (let m = 0; m < (1 << unc.length); m++) {
        let pr = 1; const on = new Set();
        unc.forEach((p, i) => { if (m >> i & 1) { pr *= p.qq[wi]; on.add(p.n); } else pr *= 1 - p.qq[wi]; });
        L.push([pr, lineupTot(pl_, wi, p => unc.includes(p) ? on.has(p.n) : p.qq[wi] >= 0.5)]);
      }
      const tp = L.reduce((a, x) => a + x[0], 0); let c = 0; const cum = L.map(x => (c += x[0] / tp));
      return { L, cum, mean: L.reduce((a, x) => a + x[0] * x[1], 0) / tp };
    });
    E[t.id] = SC[t.id].map(x => x.mean);
    // this team's own week-to-week swing: each starter's swing, plus teammates on the same NFL team moving together
    SD_[t.id] = W.map((_, wi) => { if (!T.sdk) return T.sd; const a = pl_.map(p => ({ p, v: p.vv[wi] * (p.qq[wi] >= 0.5 ? 1 : 0) })).sort((x, y) => y.v - x.v), u = new Set(), st = [];
      for (const [sl, k] of T.slots) { let c = k; for (const x of a) { if (!c) break; if (u.has(x.p.n)) continue; if ((T.elig[sl] || []).includes(x.p.pos)) { u.add(x.p.n); if (x.v > 0) st.push(x.p); c--; } } }
      let v = st.reduce((s_, p) => s_ + p.s[wi] ** 2, 0);
      for (let i = 0; i < st.length; i++) for (let j = i + 1; j < st.length; j++) if (st[i].nfl === st[j].nfl) { const pr = [st[i].pos, st[j].pos].sort(); const r = (T.rho.find(x => x[0] === pr[0] && x[1] === pr[1]) || [0, 0, 0])[2]; v += 2 * r * st[i].s[wi] * st[j].s[wi]; }
      return T.sdk * Math.sqrt(Math.max(v, 25)); });
  });
  const wi = w => W.indexOf(w), rn = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
  const draw = (t, w) => { const S = SC[t][wi(w)]; if (S.L.length === 1) return S.L[0][1]; const r = Math.random(); let k = 0; while (k < S.cum.length - 1 && S.cum[k] < r) k++; return S.L[k][1]; };
  const sc = (t, w) => draw(t, w) + SD_[t][wi(w)] * rn(), g = (a, b, w) => sc(a, w) > sc(b, w) ? [a, b] : [b, a];
  const ids = T.teams.map(t => t.id); let ch = 0, fi = 0, po = 0;
  for (let n = 0; n < N; n++) {
    const Wn = {}, F = {}; T.teams.forEach(t => { Wn[t.id] = t.wins; F[t.id] = t.pf; });
    for (const [w, a, b] of T.sched) { const x = sc(a, w), y = sc(b, w); F[a] += x; F[b] += y; if (x > y) Wn[a]++; else Wn[b]++; }
    const o = [...ids].sort((x, y) => Wn[y] - Wn[x] || F[y] - F[x]);
    if (o.slice(0, T.np).includes(me)) po++;
    let f1_, f2_;
    if (T.np === 6) { const [w36] = g(o[2], o[5], T.po[0]), [w45] = g(o[3], o[4], T.po[0]); [f1_] = g(o[0], w45, T.po[1]); [f2_] = g(o[1], w36, T.po[1]); const [c] = g(f1_, f2_, T.po[2]); if (c === me) ch++; }
    else { [f1_] = g(o[0], o[3], T.po[0]); [f2_] = g(o[1], o[2], T.po[0]); const [c] = g(f1_, f2_, T.po[1]); if (c === me) ch++; }
    if (f1_ === me || f2_ === me) fi++;
  }
  const regW = W.filter(w => w > T.cur && w <= T.reg), avg = t => regW.length ? regW.reduce((a, w) => a + E[t][wi(w)], 0) / regW.length : 0;
  return { ch: ch / N * 100, fi: fi / N * 100, po: po / N * 100, w16: E[me][wi(PV()[1])], w17: E[me][wi(PV()[2])], reg: avg(me), preg: partner ? avg(partner) : 0 };
}
$('#run').addEventListener('click', () => {
  const b = $('#run'); b.disabled = true; const pg = h('i'); $('#result').replaceChildren(h('div', { class: 'mut' }, 'Simulating 60,000 seasons…'), h('div', { class: 'prog' }, pg)); requestAnimationFrame(() => pg.style.width = '85%');
  setTimeout(() => {
    try {
      const p = LAB.partner, trade = p != null && (LAB.give.size || LAB.get.size);
      const before = labSim(new Set(), new Set(), p, new Set(), 30000), after = labSim(trade ? LAB.give : new Set(), trade ? LAB.get : new Set(), p, LAB.out, 30000);
      const d = after.ch - before.ch, dp = after.preg - before.preg;
      const big = (k, v, c) => h('div', {}, h('div', { class: 'mut' }, k), h('div', { class: 'big ' + (c || '') }, v));
      const what = [trade ? `${[...LAB.give].join(' + ') || 'nothing'} for ${[...LAB.get].join(' + ') || 'nothing'}` : '', LAB.out.size ? `${[...LAB.out].join(', ')} out for the playoffs` : ''].filter(Boolean).join(' · ') || 'No changes picked';
      const afterEl = h('div', { class: 'big ' + cls(d) }, '0%');
      $('#result').replaceChildren(h('div', { class: 'mut', style: 'margin-bottom:8px' }, what),
        h('div', { class: 'row' }, big('Title now', pct(before.ch)), h('div', {}, h('div', { class: 'mut' }, 'Title after'), afterEl), big('Change', sgn(d) + ' pts', cls(d)), big('Make final', pct(after.fi))),
        h('p', { class: 'sub', style: 'margin:10px 0 0' }, `Your Wk${PV()[1]} lineup ${f1(before.w16)} → ${f1(after.w16)}, Wk${PV()[2]} ${f1(before.w17)} → ${f1(after.w17)}. Regular season ${f1(before.reg)} → ${f1(after.reg)} a week.` +
          (trade && p ? ` Their lineup changes ${sgn(dp)} a week: ${dp >= 0 ? 'they should like it' : dp > -2 ? 'close to fair for them' : 'they probably say no'}.` : '')));
      countUp(afterEl, after.ch); if (d >= 1.5) setTimeout(confetti, 500);
    } finally { b.disabled = false; }
  }, 450);
});
$('#clear').addEventListener('click', () => { LAB.give.clear(); LAB.get.clear(); LAB.out.clear(); LAB.partner = null; LAB.q = ''; renderLab(); $('#result').replaceChildren(h('span', { class: 'mut' }, 'Pick players and hit Run.')); });

// ---------- waivers ----------
function renderWaivers() {
  table($('#wtable'), [
    { k: 'verdict', h: 'Call', l: 1, f: r => h('span', { class: 'tag ' + (r.verdict === 'CLAIM' ? 'real' : 'no'), title: r.why }, r.verdict === 'CLAIM' ? '✅ CLAIM' : 'SKIP') },
    { k: 'player', h: 'Add', l: 1, f: r => h('span', {}, pl(r.player), h('div', { class: 'mut', style: 'font-size:12px;max-width:260px;white-space:normal' }, r.why)) }, { k: 'drop', h: 'Drop', l: 1 },
    { k: 'this_week_gain', h: 'This week', f: r => sgn(r.this_week_gain), c: r => cls(r.this_week_gain) }, { k: 'ros_gain', h: 'Rest of season', t: 'Lineup points gained, playoff weeks count double', f: r => sgn(r.ros_gain), c: r => cls(r.ros_gain) },
    { k: 'change_pts', h: 'Title odds', f: r => r.change_pts == null ? '—' : sgn(r.change_pts) + '%' }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'ros_ppg', h: 'Pts/wk rest of yr', f: r => f1(r.ros_ppg) },
    { k: 'playoff_pts', h: 'Playoff pts', f: r => f1(r.playoff_pts) }, { k: 'opp16', h: 'Semis', l: 1 }, { k: 'opp17', h: 'Final', l: 1 }, { k: 'own_pct', h: 'Rostered', f: r => r.own_pct + '%' }
  ], D.waivers);
}

// ---------- rankings ----------
const RK = { pos: 'ALL', sort: 'po', q: '', fa: false };
function renderRanks() {
  const q = RK.q.toLowerCase();
  const allRows = D.rankings.filter(r => (RK.pos === 'ALL' || r.pos === RK.pos) && (!RK.fa || r.owner === 'Free agent') && (!q || r.player.toLowerCase().includes(q) || r.owner.toLowerCase().includes(q)));
  const rows = allRows.slice(0, LIM.ranks);
  setTimeout(() => { const old = document.getElementById('rmore'); if (old) old.remove(); const b = moreBtn('ranks', allRows.length, renderRanks); if (b) { b.id = 'rmore'; $('#rtable').closest('.tw').after(b); } }, 0);
  const sosC = v => v === 'Easy' ? 'pos' : v === 'Tough' ? 'neg' : '';
  table($('#rtable'), [
    { k: 'player', h: 'Player', l: 1, f: r => h('span', { style: 'display:inline-flex;gap:8px;align-items:center' }, avatar(r.player, r.nfl, r.pos, r.value, 26), pl(r.player)) }, { k: 'tier', h: 'Tier', l: 1, sv: r => ['common', 'uncommon', 'rare', 'epic', 'legendary'].indexOf(TIERS[r.player]), f: r => tierEl(r.player) }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'owner', h: 'Team', l: 1 },
    { k: 'value', h: 'Trade value', t: '0-100. Points over a replacement starter for the rest of the season, adjusted for usage trend', f: r => r.value == null ? '—' : r.value },
    { k: 'my_value', h: 'Value to you', t: '0-100. Same idea but weighted to the playoff weeks you will actually play', f: r => r.my_value == null ? '—' : r.my_value },
    { k: 'po_n', h: 'Playoff rk', sv: r => -r.po_n, f: r => r.pos + r.po_n }, { k: 'playoff_pts', h: 'Playoff pts', f: r => f1(r.playoff_pts) },
    { k: 'ros_n', h: 'ROS rk', sv: r => -r.ros_n, f: r => r.pos + r.ros_n }, { k: 'ros_ppg', h: 'Pts/wk rest of yr', f: r => f1(r.ros_ppg) },
    { k: 'xfp', h: 'xFP/g', t: 'Expected fantasy points per game from usage (this league\'s scoring)', f: r => f1(r.xfp) },
    { k: 'fpoe', h: 'Luck', t: 'Actual minus expected points per game. Big plus = running hot, may cool off. Big minus = due to bounce back.', f: r => sgn(r.fpoe), c: r => r.fpoe > 2 ? 'neg' : r.fpoe < -2 ? 'pos' : '' },
    { k: 'trend', h: 'Usage trend', t: 'Last 3 games touches+targets vs season average', f: r => r.trend == null ? '—' : (r.trend > 1 ? '▲ ' : r.trend < 1 ? '▼ ' : '') + Math.round((r.trend - 1) * 100) + '%', c: r => r.trend > 1.08 ? 'pos' : r.trend < 0.92 ? 'neg' : '' },
    { k: 'sos_ros', h: 'Sched ROS', l: 1, c: r => sosC(r.sos_ros) }, { k: 'sos_po', h: 'Sched playoffs', l: 1, c: r => sosC(r.sos_po) },
    { k: 'opp16', h: 'Semis', l: 1 }, { k: 'opp17', h: 'Final', l: 1 }
  ], rows, { rowClass: r => r.owner === 'You' ? 'me' : '', sort: { k: RK.sort === 'po' ? 'playoff_pts' : RK.sort === 'val' ? 'value' : 'ros_ppg', d: -1 }, onRow: r => openCard(r.player) });
}
seg('rpos', v => { RK.pos = v; renderRanks(); }); seg('rsort', v => { RK.sort = v; renderRanks(); });
$('#rq').addEventListener('input', e => { RK.q = e.target.value; renderRanks(); }); $('#faonly').addEventListener('change', e => { RK.fa = e.target.checked; renderRanks(); });

// ---------- my team ----------
function renderTeam() {
  const sm = D.summary, po = PV(), hl = sm.healthy_lineup || {}, ex = sm.expected_lineup || {};
  const lt = (c, k, a, b) => h('div', { class: 'tile ' + c }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, f1(a)), h('div', { style: 'font-size:13px;font-weight:800' }, `${f1(b)} expected after injury odds (what League shows)`));
  $('#lusum').replaceChildren(lt('t1', `Wk ${po[1]} semis · full strength`, hl[po[1]], ex[po[1]]), lt('t3', `Wk ${po[2]} final · full strength`, hl[po[2]], ex[po[2]]));
  const IL = sm.ideal_lineups || {};
  if (sm.bracket) {   // who you're likely to face, and how often you win
    const col = (k, label, w) => h('div', {}, h('div', { class: 'k', style: 'font-family:var(--game);color:var(--gold);font-size:13px;margin-bottom:6px' }, `${label} (Wk ${w})`),
      ...(sm.bracket[k] || []).map(o => h('div', { class: 'pathrow', onclick: () => openTeam(o.id) }, h('span', { class: 'pn' }, o.team), h('span', { class: 'mut' }, `${Math.round(o.pct)}% you face them`), h('b', { class: o.win >= 50 ? 'pos' : 'neg' }, `${Math.round(o.win)}% win`))));
    const box = h('div', { class: 'box', id: 'pathbox', style: 'margin-bottom:16px' }, h('h3', {}, '🏆 Your playoff path'), h('p', { class: 'sub', style: 'margin:0 0 8px' }, 'From the sims: who you meet most often, and how often you beat them. Tap a team to scout them.'),
      h('div', { class: 'grid2', style: 'gap:12px' }, col('semis', '⚔️ Semis', po[1]), col('final', '🏆 Final', po[2])));
    const o_ = $('#pathbox'); if (o_) o_.replaceWith(box); else $('#lusum').before(box);
  }
  { const w = po[1], nums = [['💪 Full strength', hl[w], 'Everyone who can play plays. Best case.'], ['🎯 Expected', ex[w], 'Injury odds built in, and if a hurt guy sits your bench steps in. This is the number used everywhere on the site.']];
    const box = h('div', { class: 'box', style: 'margin-bottom:16px' }, h('h3', {}, `🤔 Your Wk ${w} lineup: best case vs expected`), h('div', { class: 'three' }, nums.filter(x => x[1] != null).map(([k, v, t]) => h('div', { class: 'st' }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, f1(v)), h('div', { class: 'gn' }, t)))),
      h('p', { class: 'sub', style: 'margin:8px 0 0' }, 'They only differ because of injured or questionable guys. Each player in a lineup shows what he scores if he plays.'));
    const old_ = $('#threebox'); box.id = 'threebox'; if (old_) old_.replaceWith(box); else $('#lusum').after(box); }
  $('#ideal').replaceChildren(...po.filter(w => w && IL[w]).map(w => { const L = IL[w];
    return h('div', { class: 'box' }, h('h3', {}, (w === po[0] ? '😴 Wk ' + w + ' (bye if you get it)' : w === po[1] ? '⚔️ Wk ' + w + ' semis' : '🏆 Wk ' + w + ' final') + ` · ${f1(L.total)}`),
      h('div', { class: 'tw' }, h('table', {}, h('tbody', {}, L.lineup.map(x => h('tr', {}, h('td', { class: 'mut' }, x.slot),
        h('td', { class: 'l' }, h('span', { style: 'display:inline-flex;gap:8px;align-items:center' }, avatar(x.player, NFL(x.player), POS(x.player), VAL(x.player), 26), h('span', {}, pl(x.player),
          h('div', { class: 'mut', style: 'font-size:12px', title: x.rank ? `defense rank vs his position: ${x.rank} (32 = easiest)` : '' }, x.opp, x.rank ? h('span', { class: x.rank >= 22 ? 'pos' : x.rank <= 11 ? 'neg' : 'mut' }, x.rank >= 22 ? ' · easy' : x.rank <= 11 ? ' · tough' : ' · avg') : '')))),
        h('td', {}, f1(x.pts), x.chance < 1 ? h('span', { class: 'opp ' + (x.chance < 0.7 ? 'neg' : '') }, Math.round(x.chance * 100) + '% to play') : '')))))),
      L.next_up.length ? h('p', { class: 'sub', style: 'margin:8px 0 0' }, 'Next up: ' + L.next_up.map(b => `${b.player} ${f1(b.pts)}`).join(', ')) : ''); }));
  const wk = (r, k) => { const ch = r[k + '_chance']; return h('span', {}, f1(r[k + '_proj']) + (ch < 1 ? ` (${Math.round(ch * 100)}%)` : ''), h('span', { class: 'opp' }, `${r[k + '_opp']}${r[k + '_rank'] ? ' · ' + r[k + '_rank'] : ''}`)); };
  table($('#ptable'), [
    { k: 'player', h: 'Player', l: 1, f: r => h('span', {}, pl(r.player), ' ', tierEl((r.player))) }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'status', h: 'Status', l: 1 },
    { k: 'season_avg', h: 'Season avg', f: r => f1(r.season_avg) }, { k: 'recent', h: 'Last 4', f: r => f1(r.recent) }, { k: 'ros_ppg', h: 'Pts/wk rest of yr', f: r => f1(r.ros_ppg) },
    ...(po[0] ? [{ k: 'wk15', h: `Wk ${po[0]}`, f: r => wk(r, 'wk15') }] : []), { k: 'wk16', h: `Wk ${po[1]} semis`, f: r => wk(r, 'wk16') }, { k: 'wk17', h: `Wk ${po[2]} final`, f: r => wk(r, 'wk17') },
    { k: 'playoff_expected', h: 'Expected semis+final', f: r => f1(r.playoff_expected) }
  ], D.my_players, { onRow: r => openCard(r.player) });
}

// ---------- league ----------
function renderLeague() {
  hbar($('#lchart'), D.league.filter(r => r.champ_pct > 0.05), { label: r => r.team.length > 18 ? r.team.slice(0, 17) + '…' : r.team, value: r => r.champ_pct, fmt: v => v.toFixed(1) + '%', color: r => r.me ? '#ffc83d' : '#4cc9ff' });
  table($('#ltable'), [
    { k: 'team', h: 'Team (tap one)', l: 1, f: r => h('b', {}, r.team + ' ›') }, { k: 'record', h: 'Record' }, { k: 'pf', h: 'Pts scored', f: r => f1(r.pf) }, { k: 'playoffs_pct', h: 'Playoffs', f: r => pct(r.playoffs_pct) }, { k: 'bye_pct', h: 'Bye', f: r => pct(r.bye_pct) },
    { k: 'champ_pct', h: 'Title', f: r => pct(r.champ_pct) }, { k: 'final_pct', h: 'Final', f: r => pct(r.final_pct) }, { k: 'win_this_week_pct', h: 'Win this week', f: r => pct(r.win_this_week_pct) },
    { k: 'wk16_lineup', h: 'Semis (expected)', t: 'Best lineup with injury odds built in. Full-strength numbers are on My Team.', f: r => f1(r.wk16_lineup) }, { k: 'wk17_lineup', h: 'Final (expected)', t: 'Best lineup with injury odds built in. Full-strength numbers are on My Team.', f: r => f1(r.wk17_lineup) }
  ], D.league, { rowClass: r => r.me ? 'me' : '', onRow: r => openTeam(r.id) });
  table($('#btable'), [
    { k: 'team', h: 'Team', l: 1 }, { k: 'record', h: 'Record' }, { k: 'playoffs_pct', h: 'Playoff %', f: r => pct(r.playoffs_pct) }, { k: 'needs', h: 'Weakest spot', l: 1 },
    { k: 'weakest_starter_pts', h: "That starter's pts", f: r => f1(r.weakest_starter_pts) }, { k: 'has_extra', h: 'Bench pieces they could move', l: 1 }
  ], D.bubble, { rowClass: r => r.blocked ? 'dim' : '', onRow: r => openTeam(r.id) });
  const mv = H.moves || [];
  if (!mv.length) $('#mtable').replaceChildren(h('tbody', {}, h('tr', {}, h('td', { class: 'mut' }, 'Moves show up here starting with the second update.'))));
  else table($('#mtable'), [{ k: 'at', h: 'When', l: 1, f: r => new Date(r.at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) }, { k: 'type', h: 'Type', l: 1, f: r => h('span', { class: 'tag ' + r.type }, r.type) }, { k: 'text', h: 'What', l: 1 }], mv.slice(0, 120));
}

// ---------- how it works / settings ----------
function rcTable(rc) {
  const head = h('thead', {}, h('tr', {}, ['Week', 'Our model off by', 'ESPN off by', 'Sleeper off by', 'Vegas props off by', 'Team scores off by', 'Matchups called right', 'Close start/sit calls right'].map(x => h('th', {}, x))));
  const rows = rc.slice().reverse().map(r => h('tr', {}, h('td', {}, 'Wk ' + r.w), h('td', { class: r.espn_mae == null ? '' : (r.model_mae_vs_espn <= r.espn_mae ? 'pos' : 'neg') }, f1(r.model_mae) + ' pts'), h('td', {}, r.espn_mae == null ? '—' : f1(r.espn_mae) + ' pts'), h('td', {}, r.sleeper_mae == null ? '—' : f1(r.sleeper_mae) + ' pts'), h('td', {}, r.props_mae == null ? '—' : f1(r.props_mae) + ' pts'),
    h('td', {}, r.team_mae != null ? f1(r.team_mae) + ' pts' : '—'), h('td', {}, r.games ? `${r.picked_right}/${r.games}` : '—'), h('td', {}, r.startsit_pct != null ? r.startsit_pct + '%' : '—')));
  return h('div', { class: 'tw' }, h('table', {}, head, h('tbody', {}, rows)));
}
let VAPID_PUB = null;
function alertsBox() {
  const box = h('div', { class: 'box', id: 'alertsbox', style: 'margin-bottom:16px;border-color:#ffc83d' }, h('h3', {}, '🔔 Phone alerts'));
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  const out = h('div', {});
  const b64 = s => { const p = '='.repeat((4 - s.length % 4) % 4), r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from([...r].map(c => c.charCodeAt(0))); };
  const go = async () => {
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) { out.replaceChildren(h('p', { class: 'neg' }, standalone ? 'This phone doesn\'t support app alerts (iPhone needs iOS 16.4 or newer).' : 'Open Oxtail from your home screen app first, then tap this again.')); return; }
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') { out.replaceChildren(h('p', { class: 'neg' }, 'Alerts are blocked. Turn them on in Settings → Notifications → Oxtail, then try again.')); return; }
      if (!VAPID_PUB) VAPID_PUB = (await fetch('push_key.txt?v=' + Date.now()).then(r => r.ok ? r.text() : '')).trim();
      if (!VAPID_PUB) { out.replaceChildren(h('p', { class: 'neg' }, 'Alerts aren\'t set up on GitHub yet (missing NTFY_TOPIC secret).')); return; }
      const reg = await navigator.serviceWorker.ready;
      const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(VAPID_PUB) });
      const code = JSON.stringify(sub);
      out.replaceChildren(h('p', {}, '✅ Alerts are on for this phone. One last step: copy this code and save it as a GitHub secret named ', h('b', {}, 'PUSH_SUB'), '. That\'s what lets GitHub send alerts straight to this app.'),
        h('textarea', { readonly: '', style: 'width:100%;height:70px;font-size:11px;background:#0f1138;color:#fff;border:2px solid #2c3080;border-radius:10px' }, code),
        h('div', { class: 'bar' }, h('button', { class: 'btn small', onclick: e => navigator.clipboard.writeText(code).then(() => e.target.textContent = 'COPIED ✓') }, 'COPY CODE'),
          h('a', { class: 'btn small blue', href: 'https://github.com/PandaFilmsHD/oxtail-lab/settings/secrets/actions/new', target: '_blank', rel: 'noopener' }, 'OPEN GITHUB SECRETS')));
    } catch (er) { out.replaceChildren(h('p', { class: 'neg' }, 'Couldn\'t turn on alerts: ' + er.message)); }
  };
  box.append(h('p', { class: 'sub', style: 'margin:0 0 8px' }, standalone ? 'Get injury news, lineup warnings and in-game updates for your players. Tapping an alert opens Oxtail.' : 'Open Oxtail from your home screen icon (not the browser), then tap the button.'),
    h('button', { class: 'btn small', onclick: go }, '🔔 TURN ON ALERTS'), out);
  return box;
}
function renderHow() {
  if (!$('#alertsbox')) $('#how').prepend(alertsBox());
  if (!$('#appver')) { const v = h('p', { class: 'k', id: 'appver', style: 'text-align:center;margin:8px 0' }, 'App version: checking…'); $('#how').prepend(v);
    caches.keys().then(k => { v.textContent = 'App version: ' + (k.filter(x => /^oxtail-v/.test(x)).join(', ') || 'none') + ' · alert fix 4'; }).catch(() => { v.textContent = 'App version: unknown'; }); }
  const rc = D.summary.report_card || [], vr = D.summary.variance;
  if (!$('#rcbox')) $('#how').append(h('div', { class: 'box', id: 'rcbox', style: 'margin-top:16px' }, h('h3', {}, '📝 Weekly report card'),
    rc.length ? [h('p', { class: 'sub' }, 'Every week we save our projections before kickoff, then grade them once the games are done. Lower "off by" is better.'), rcTable(rc)]
      : h('p', { class: 'sub', style: 'margin:0' }, `Starting now. This week's projections are saved, and the first grades show up after Week ${D.summary.week} is done. Until then, the backtest below is the best check.`),
    vr ? h('p', { class: 'sub', style: 'margin:10px 0 0' }, `Week-to-week swings: teams in this league swing about ${f1(vr.league_actual_sd)} pts from week to week. The sims build each team's swing player by player (boom/bust guys swing more, and a QB and his receivers rise and fall together), then match that real number.`) : ''));
  const bt = D.summary.backtest;
  if (bt && !$('#btbox')) $('#how').append(h('div', { class: 'box', id: 'btbox', style: 'margin-top:16px' }, h('h3', {}, '🎯 How accurate are the projections?'),
    h('p', {}, `We replayed ${bt.n.toLocaleString()} past player-weeks this season: for each one, we projected him using only what we knew before that week, then checked what he actually scored.`),
    h('ul', { class: 'bwhy' }, h('li', {}, `Just using season average: off by ${bt.mae_season_avg} pts per player per week.`), h('li', {}, `Our old mix: off by ${bt.mae_old_mix}.`), h('li', {}, `Our mix now: off by ${bt.mae_used}. Best possible mix this season: ${bt.mae_best}.`)),
    h('p', { class: 'sub', style: 'margin:0' }, `Current mix: ${Math.round(bt.w_xfp * 80)}% usage (what his targets and carries are worth, adjusted for how efficient he is), ${Math.round(bt.w_avg * 80)}% season average, ${Math.round(bt.w_recent * 80)}% last 4 games, 20% ESPN. Usage has been the best predictor every time, so it gets the most weight. It updates every run.`),
    bt.history ? h('p', { class: 'sub', style: 'margin:8px 0 0' }, `Also tested on ${bt.history.n.toLocaleString()} player-weeks from the ${bt.history.seasons[0]} to ${bt.history.seasons[bt.history.seasons.length - 1]} seasons: the recipe beat plain season average every year. Those seasons set 70% of the mix and this season sets 30%, so one weird year can't throw it off.`) : '',
    bt.vegas ? h('p', { class: 'sub', style: 'margin:8px 0 0' }, 'Vegas lines (how many points each team is expected to score): ' + Object.entries(bt.vegas).map(([k, v]) => v.elasticity > 0 ? `${k} ${f1(v.mae_without)} → ${f1(v.mae_with)}` : `${k} no help yet`).join(', ') + '. We only use them as much as they actually improved past projections.') : ''));
  const s = D.summary, st = s.settings, sc = s.scoring;
  const names = { '3': 'Passing yard', '4': 'Passing TD', '20': 'Interception', '24': 'Rushing yard', '25': 'Rushing TD', '53': 'Reception', '42': 'Receiving yard', '43': 'Receiving TD', '72': 'Fumble lost', '19': 'Passing 2pt', '26': 'Rushing 2pt', '44': 'Receiving 2pt' };
  const slots = Object.entries(st.slots || {}).map(([k, v]) => `${v} ${k}`).join(', ');
  $('#assume').replaceChildren(
    h('h3', { style: 'margin-top:16px' }, `League settings (from ${st.source})`),
    h('div', { class: 'tw' }, h('table', {}, h('tbody', {},
      [['League', st.name || s.league], ['Teams', st.teams], ['Reception', sc['53'] + ' pt' + (sc['53'] == 1 ? ' (full PPR)' : '')], ['Passing TD', sc['4'] + ' pts'], ['Lineup', slots],
       ['Playoff teams', st.playoff_teams], ['Regular season ends', 'Week ' + st.reg_end], ['Tiebreak', st.tiebreak || '—'], ['Trade veto votes', st.veto_votes ?? '—'], ['Waivers', st.waiver_type || '—']]
        .map(([a, b]) => h('tr', {}, h('td', { class: 'l' }, a), h('td', { class: 'l' }, String(b ?? '—'))))))),
    h('h3', { style: 'margin-top:16px' }, 'Scoring used for every number'),
    h('p', { class: 'sub' }, Object.entries(names).filter(([id]) => sc[id] != null).map(([id, n]) => `${n}: ${sc[id]}`).join(' · ')),
    Object.keys(s.overrides || {}).length ? h('p', { class: 'neg' }, 'Testing overrides from config.json: ' + JSON.stringify(s.overrides)) : '',
    h('h3', { style: 'margin-top:16px' }, 'Injury guesses in use'),
    h('p', { class: 'sub' }, Object.entries(s.assumptions.avail).map(([n, a]) => `${n}: ` + Object.entries(a).filter(([k]) => k !== 'v').map(([w, p]) => `Wk${w} ${Math.round(p * 100)}%`).join(', ')).join(' · ')),
    h('p', { class: 'sub' }, `Sims per update: ${s.sims.base.toLocaleString()} league seasons + ${s.sims.per_trade.toLocaleString()} per trade. Data from ESPN pulled ${new Date(s.at).toLocaleString()}.`));
}

// ---------- player card ----------
const VCOL = { 'SELL HIGH': ['#ff9f43', '#c46a12'], KEEP: ['#3ddc84', '#1f9a54'], HOLD: ['#4cc9ff', '#1a7fc2'], ADD: ['#3ddc84', '#1f9a54'], BUY: ['#3ddc84', '#1f9a54'], PRICEY: ['#ffc83d', '#c98a00'], PASS: ['#8a90c9', '#4a4f8f'] };
// ---------- nerd stat grader: good / average / bad + what it means in plain English ----------
function gradeStat(key, v, c, games, tc) {
  const n = parseFloat(v); if (v == null || isNaN(n)) return null;
  const P = c.pos, G = Math.max(1, games || 1), pg = n / G;
  const band = (x, hi, lo, good, avg, bad) => x >= hi ? ['good', good] : x >= lo ? ['ok', avg] : ['bad', bad];
  const rec = P === 'WR' || P === 'TE';
  switch (key) {
    case 'tsh': return [...(P === 'RB' ? band(n, 12, 7, 'Lots of passes come his way for a RB. Huge in PPR.', 'Normal amount of targets for a RB.', 'Barely used in the passing game, so his floor is low in PPR.')
      : band(n, 22, 15, 'He gets a big chunk of his team\'s targets. This is the best sign for a WR/TE.', 'Decent share of targets, but not a go-to guy.', 'He doesn\'t get many targets, so he needs TDs to score.')), true];
    case 'csh': return [...band(n, 60, 40, 'Workhorse: he gets most of the carries.', 'Splits carries with someone, so weeks will swing.', 'Not the main ball carrier. Hard to trust.'), true];
    case 'snap': return [...(P === 'RB' ? band(n, 65, 45, 'On the field a ton for a RB.', 'Part of a rotation.', 'Barely on the field.') : band(n, P === 'TE' ? 75 : 82, P === 'TE' ? 55 : 68, 'Almost never comes off the field.', 'Plays a lot, but not every down.', 'Part time player, which caps his ceiling.')), true];
    case 'opp': if (P === 'QB') return null; return [...(P === 'RB' ? band(n, 18, 13, 'Lots of touches every game. Volume is king for RBs.', 'Average workload.', 'Low workload, so he needs big plays.') : band(n, P === 'TE' ? 6.5 : 8, P === 'TE' ? 4.5 : 6, 'Gets the ball thrown his way a lot.', 'Average volume.', 'Low volume.')), true];
    case 'wopr': return [...band(n, 0.55, 0.35, 'Elite mix of targets and deep looks. WOPR is one of the best stats for predicting WR points.', 'Solid but not elite usage.', 'Weak usage, so his good games are probably luck.'), true];
    case 'rz_tgt': return [...band(pg, 1, 0.5, 'Gets targets near the end zone, which is where TDs come from.', 'Some red zone looks.', 'Rarely targeted near the end zone.'), true];
    case 'rz_car': return [...band(pg, 2.5, 1.2, 'Gets the ball near the end zone a lot. TD upside.', 'Some red zone work.', 'Doesn\'t get the ball near the end zone much.'), true];
    case 'gl_car': return [...band(pg, 0.6, 0.25, 'He\'s the goal line back. Easy TDs.', 'Gets some goal line work.', 'Someone else gets the goal line carries.'), false];
    case 'epa': return [...band(n, 5, -5, 'His plays help his team score more than average.', 'About average efficiency.', 'His plays hurt the offense on average.'), false];
    case 'boom': return [...band(n, 30, 15, 'Lots of 20+ point type weeks. Can win you a week by himself.', 'Some big weeks.', 'Rarely has a monster week.'), false];
    case 'bust': return [...(n <= 20 ? ['good', 'Rarely lays an egg. Safe to start.'] : n <= 40 ? ['ok', 'Has some bad weeks, normal for most players.'] : ['bad', 'Busts a lot. Risky to start in the playoffs.']), false];
    case 'fpoe': return [...(n >= 3 ? ['bad', 'Scoring way more than his usage says. Probably lucky (long TDs), so expect him to cool off.'] : n <= -3 ? ['good', 'Scoring less than his usage says. He\'s been unlucky and should bounce back.'] : ['ok', 'Scoring about what his usage says. What you see is real.']), true];
    case 'espn': return ['ok', 'What ESPN projects him for each game from here. Just one opinion we blend in.', false];
    case 'cpoe': return [...band(n, 2, -2, 'Completes more passes than expected. Accurate.', 'About average accuracy.', 'Misses throws he should make.'), false];
    case 'aggr': return ['ok', n >= 18 ? 'Throws into tight windows a lot. More big plays and more picks.' : 'Plays it pretty safe with where he throws.', false];
    case 'ttt': return ['ok', n >= 3 ? 'Holds the ball a while. More sacks, but more deep shots.' : 'Gets the ball out fast.', false];
    case 'qadot': return ['ok', n >= 8.5 ? 'Throws deep a lot. Boom or bust.' : 'Throws short, safe passes.', false];
    case 'rating': return [...band(n, 100, 85, 'Playing really well overall.', 'Average QB play.', 'Struggling overall.'), false];
    case 'ryoe': return [...band(n, 0.3, -0.3, 'Gets more yards than his blocking gives him. Talent shows.', 'Gets about what the blocking gives him.', 'Gets fewer yards than expected.'), false];
    case 'box': return [...(n >= 30 ? ['bad', 'Defenses stack the box on him a lot, which makes running hard.'] : ['good', 'Usually faces normal fronts, so running lanes are there.']), false];
    case 'yac_att': return [...band(n, 3.2, 2.6, 'Breaks through contact well.', 'Average after contact.', 'Goes down easy.'), false];
    case 'btk': if (P === 'TE' || P === 'QB') return null; return [...band(pg, 0.8, 0.35, 'Breaks a lot of tackles. Hard to bring down.', 'Breaks a few tackles.', 'Rarely breaks tackles.'), false];
    case 'sep': return [...band(n, P === 'TE' ? 3.0 : 3.3, P === 'TE' ? 2.3 : 2.7, 'Gets open easily.', 'Average at getting open.', 'Usually covered tight, so he needs contested catches.'), false];
    case 'yacoe': return [...band(n, 1, -0.5, 'Makes extra yards after the catch.', 'Normal after the catch.', 'Doesn\'t do much after the catch.'), false];
    case 'adot': return ['ok', n >= 13 ? 'Deep threat: big plays but up and down weeks.' : n <= 7 ? 'Short, safe targets. Steady in PPR.' : 'Mix of short and deep targets.', false];
    case 'air': return [...band(n, 30, 20, 'Gets a big share of where the ball goes downfield.', 'Some of the downfield looks.', 'Not much of the downfield work.'), false];
    case 'drops': return [...(n >= 5 ? ['bad', 'Drops too many. Could cost him targets.'] : ['good', 'Sure hands.']), false];
    case 'plays': return [...band(n, 65, 60, 'Fast, busy offense. More plays means more chances to score.', 'Average pace.', 'Slow offense with fewer plays.'), false];
    case 'proe': return [...(P === 'RB' ? (n <= -3 ? ['good', 'Team leans on the run more than normal. Good for RBs.'] : n >= 3 ? ['bad', 'Team throws more than normal, so fewer carries.'] : ['ok', 'Normal run/pass mix.']) : (n >= 3 ? ['good', 'Team throws more than normal. Good for QBs and pass catchers.'] : n <= -3 ? ['bad', 'Team runs more than normal, so fewer targets.'] : ['ok', 'Normal run/pass mix.'])), false];
    case 'depth': return [...(n <= 1 ? ['good', 'Starter on the depth chart.'] : n === 2 ? ['ok', 'Second on the depth chart.'] : ['bad', 'Backup on the depth chart.']), false];
  }
  return null;
}
async function openCard(name) {
  if (!CARDS) CARDS = await fetch(BASE + 'players.json?v=' + D.summary.built).then(r => r.json());
  const c = CARDS[name]; if (!c) return;
  const ng = c.ngs || {}, pf = c.pfr || {}, rep = c.report, dep = c.depth, tc = c.team_ctx || {};
  const big = (k, v, sub) => h('div', { class: 'st hot' }, h('div', { class: 'k' }, k), h('div', { class: 'v', style: 'font-size:28px' }, v), sub ? h('div', { class: 'k', style: 'text-transform:none' }, sub) : '');
  const NERD = [];
  const stat = (k, v, t, key, raw) => { if (v == null || v === '—') return null; const g = key ? gradeStat(key, raw ?? v, c, (c.log || []).length || null, tc) : null; if (g) NERD.push({ k, g });
    return h('div', { class: 'st' + (g ? ' g-' + g[0] : ''), title: t || '' }, h('div', { class: 'k' }, (g && g[2] ? '⭐ ' : '') + k), h('div', { class: 'v' }, v), g ? h('div', { class: 'gn' }, g[1]) : ''); };
  const [vc, vd] = VCOL[c.verdict] || VCOL.HOLD;
  // floor / ceiling bar
  const fc = c.floor != null ? (() => { const mx = Math.max(35, c.ceiling + 5), L = c.floor / mx * 100, R = c.ceiling / mx * 100, A = Math.min(100, (c.ros_ppg || 0) / mx * 100);
    return h('div', {}, h('div', { style: 'position:relative;height:16px;background:#0f1138;border-radius:99px;border:2px solid #2c3080;margin:14px 0 6px' },
      h('div', { style: `position:absolute;left:${L}%;width:${R - L}%;top:0;bottom:0;background:linear-gradient(90deg,#4cc9ff,#3ddc84);border-radius:99px` }),
      h('div', { style: `position:absolute;left:calc(${A}% - 3px);top:-5px;width:6px;height:22px;background:#ffc83d;border-radius:3px;box-shadow:0 0 8px #ffc83d` })),
      h('div', { class: 'sub', style: 'margin:0' }, `Bad game ≈ ${f1(c.floor)} · good game ≈ ${f1(c.ceiling)} · gold line = what we expect per week the rest of the way (${f1(c.ros_ppg)})`)); })() : '';
  // value meters
  const meter = (label, v, col) => h('div', { style: 'margin:6px 0' }, h('div', { style: 'display:flex;justify-content:space-between;font-size:13px' }, h('span', {}, label), h('b', {}, v ?? '—')),
    h('div', { style: 'height:12px;background:#0f1138;border-radius:99px;overflow:hidden;border:2px solid #2c3080' }, h('div', { style: `width:${v || 0}%;height:100%;background:${col};border-radius:99px` })));
  const lg = (c.log || []).filter(x => x.pts != null), mx = Math.max(25, ...lg.map(x => x.pts));
  const spark = h('div', { class: 'spark' }, lg.map(x => { const i = h('i', { class: x.pts >= (c.pos === 'QB' ? 25 : 20) ? 'boom' : x.pts < (c.pos === 'QB' ? 15 : 8) ? 'bust' : '' }); i.style.height = Math.max(3, x.pts / mx * 100) + '%'; i.addEventListener('mousemove', e => showTip(e, `Wk ${x.w} vs ${x.opp}: ${f1(x.pts)} pts (usage said ${f1(x.xfp)})`)); i.addEventListener('mouseleave', hideTip); return i; }));
  const sosC = r => r == null ? '' : r >= 22 ? 'easy' : r <= 11 ? 'hard' : 'mid';
  const po = PV();
  const sch = h('div', { class: 'sched' }, c.schedule.map((s, i) => { const wk = c.weekly[i] || {}; return h('div', { class: 'sw ' + (s.opp === 'BYE' ? 'bye' : sosC(s.rank)), style: po.includes(s.w) ? 'outline:3px solid #ffc83d;outline-offset:2px' : '', title: s.rank ? `${s.opp} defense ranks ${s.rank} of 32 vs ${c.pos} (32 = easiest)` : '' },
    h('div', { class: 'k' }, (s.w === po[1] ? 'SEMIS ' : s.w === po[2] ? 'FINAL ' : 'WK ') + s.w), h('div', {}, s.opp), h('div', { class: 'k' }, s.opp === 'BYE' ? '' : f1(wk.ev) + ' pts' + (wk.chance < 1 ? ` · ${Math.round(wk.chance * 100)}%` : ''))); }));
  const ngsRows = c.pos === 'QB' ? [['Completion % over expected', ng.cpoe], ['Aggressiveness', ng.aggr], ['Time to throw', ng.ttt], ['Air yards / throw', ng.adot], ['Passer rating', ng.rating]]
    : c.pos === 'RB' ? [['Rush yds over expected / carry', ng.ryoe_att], ['Stacked boxes faced', ng.stacked_box != null ? ng.stacked_box + '%' : null], ['Yds after contact / carry', pf.yac_att], ['Broken tackles', pf.broken_tackles]]
    : [['Separation (yds)', ng.sep], ['YAC over expected', ng.yacoe], ['Avg depth of target', ng.adot], ['Share of team air yards', ng.air_share != null ? ng.air_share + '%' : null], ['Drops', pf.drops], ['Broken tackles', pf.broken_tackles]];
  const pick = (a, b) => b ?? a;
  const usageTiles = [stat('Target share', c.tsh != null ? `${c.tsh}% → ${c.tsh3}%` : null, 'season → last 3', c.pos !== 'QB' && 'tsh', pick(c.tsh, c.tsh3)), stat('Carry share', c.csh ? `${c.csh}% → ${c.csh3}%` : null, '', c.pos === 'RB' && 'csh', pick(c.csh, c.csh3)), stat('Snaps', c.snap != null ? `${c.snap}% → ${c.snap3}%` : null, '', c.pos !== 'QB' && 'snap', pick(c.snap, c.snap3)),
    stat('Touches+targets/g', c.opp_g != null ? `${c.opp_g} → ${c.opp_g3}` : null, '', 'opp', pick(c.opp_g, c.opp_g3)), stat('WOPR', c.wopr != null ? `${c.wopr} → ${c.wopr3}` : null, '', c.pos !== 'QB' && c.pos !== 'RB' && 'wopr', pick(c.wopr, c.wopr3)), stat('Red zone targets', c.rz_tgt || null, '', c.pos !== 'QB' && 'rz_tgt'), stat('Red zone carries', c.rz_car || null, '', c.pos === 'RB' && 'rz_car'), stat('Goal line carries', c.gl_car || null, '', c.pos === 'RB' && 'gl_car'), stat('EPA', c.epa, '', 'epa'), stat('Boom %', c.boom != null ? c.boom + '%' : null, '', 'boom', c.boom), stat('Bust %', c.bust != null ? c.bust + '%' : null, '', 'bust', c.bust), stat('Luck / game', c.fpoe != null ? sgn(c.fpoe) : null, 'actual minus expected', 'fpoe', c.fpoe), stat('ESPN ROS / game', c.espn_ros_pg != null ? f1(c.espn_ros_pg) : null, '', 'espn', c.espn_ros_pg)].filter(Boolean);
  const NGK = { 'Completion % over expected': 'cpoe', 'Aggressiveness': 'aggr', 'Time to throw': 'ttt', 'Air yards / throw': 'qadot', 'Passer rating': 'rating', 'Rush yds over expected / carry': 'ryoe', 'Stacked boxes faced': 'box', 'Yds after contact / carry': 'yac_att', 'Broken tackles': 'btk', 'Separation (yds)': 'sep', 'YAC over expected': 'yacoe', 'Avg depth of target': 'adot', 'Share of team air yards': 'air', 'Drops': 'drops' };
  const ngsTiles = ngsRows.map(([k, v]) => stat(k, v, '', NGK[k], typeof v === 'string' ? parseFloat(v) : v)).filter(Boolean);
  const teamTiles = [stat('Plays / game', tc.plays_pg, '', 'plays'), stat('Pass rate vs expected', tc.proe != null ? sgn(tc.proe) + '%' : null, '', 'proe', tc.proe), dep ? stat('Depth chart', dep.pos + dep.rank, '', 'depth', dep.rank) : null].filter(Boolean);
  const goods = NERD.filter(x => x.g[0] === 'good'), bads = NERD.filter(x => x.g[0] === 'bad'), key_ = NERD.filter(x => x.g[2]);
  const keyGood = key_.filter(x => x.g[0] === 'good').length, keyBad = key_.filter(x => x.g[0] === 'bad').length;
  const overall = keyGood > keyBad + 1 ? 'The stats that matter most (⭐) look strong. His production is backed by real usage.' : keyBad > keyGood + 1 ? 'The stats that matter most (⭐) look weak. Be careful trusting his points.' : 'The most important stats (⭐) are mixed, so he\'s about what his points say.';
  const nerdSum = NERD.length ? h('div', { class: 'nerdsum' }, h('b', {}, '📝 Quick read: '), overall,
    goods.length ? h('div', { class: 'pos' }, '👍 Good: ' + goods.map(x => x.k).join(', ')) : '', bads.length ? h('div', { class: 'neg' }, '👎 Bad: ' + bads.map(x => x.k).join(', ')) : '',
    h('div', { class: 'mut', style: 'font-size:12px;margin-top:4px' }, 'Green = good, gray = average or just info, red = bad. ⭐ = matters most for fantasy points.')) : '';
  const log = h('div', { class: 'tw' }, h('table', {}, h('thead', {}, h('tr', {}, ['Wk', 'Opp', 'Pts', 'Usage said', c.pos === 'QB' ? 'Pass yds' : 'Targets', c.pos === 'QB' ? 'Attempts' : 'Catches', 'Yds', 'Carries', 'TD', 'Tgt share', 'Snaps', 'RZ'].map((x, i) => h('th', { class: i < 2 ? 'l' : '' }, x)))),
    h('tbody', {}, lg.slice().reverse().map(x => h('tr', {}, h('td', { class: 'l' }, x.w), h('td', { class: 'l' }, x.opp), h('td', {}, h('b', {}, f1(x.pts))), h('td', { class: 'mut' }, f1(x.xfp)), h('td', {}, c.pos === 'QB' ? x.passyd : x.tgt), h('td', {}, c.pos === 'QB' ? x.att : x.rec), h('td', {}, c.pos === 'QB' ? x.rushyd : (x.recyd || 0) + (x.rushyd || 0)), h('td', {}, x.car), h('td', {}, x.td), h('td', {}, x.tsh != null ? x.tsh + '%' : '—'), h('td', {}, x.snap != null ? x.snap + '%' : '—'), h('td', {}, (x.rz_tgt || 0) + (x.rz_car || 0)))))));
  const statusBits = [c.status && c.status !== 'ACTIVE' ? '🚑 ' + c.status.replace('_', ' ') : '', rep && rep.practice ? `Wk${rep.week} practice: ${rep.practice.replace(' in Practice', '').replace('Participation', '')}${rep.injury ? ' (' + rep.injury + ')' : ''}` : '', `${c.own}% rostered` + (c.own_chg ? ` (${sgn(c.own_chg)})` : '')].filter(Boolean);
  const m = h('div', { class: 'modal', onclick: e => { if (e.target === m) m.remove(); } }, h('div', { class: 'mbox ' + (TIERS[c.name] || '') },
    h('button', { class: 'x', onclick: () => m.remove() }, '×'),
    h('div', { style: 'display:flex;gap:18px;align-items:center;margin-bottom:14px' }, avatar(c.name, c.nfl, c.pos, c.value, 96),
      h('div', {}, h('h2', {}, c.name, ' ', tierEl(c.name)),
        h('div', { class: 'sub', style: 'margin:6px 0 0;font-size:14px' }, `${c.pos} · ${c.nfl} · ${c.mine ? '⭐ Your team' : c.owner}`),
        statusBits.length ? h('div', { class: 'sub', style: 'margin:2px 0 0' }, statusBits.join('  ·  ')) : '')),
    h('div', { style: `display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:12px 16px;border-radius:16px;background:linear-gradient(90deg,${vc}33,transparent);border:2px solid ${vc}` },
      h('div', { style: `font-family:var(--game);font-size:28px;color:#111;background:${vc};padding:4px 16px;border-radius:12px;box-shadow:0 4px 0 ${vd}` }, c.verdict),
      h('div', { style: 'font-size:16px;flex:1;min-width:200px' }, c.verdict_line)),
    h('div', { class: 'stats', style: 'margin-top:14px' }, big('Playoff projection', f1(c.playoff_pts), `Wk ${po[1]} + Wk ${po[2]} expected`), big('Per week rest of way', f1(c.ros_ppg)), big('Season average', f1(c.avg), c.recent != null ? `last 4: ${f1(c.recent)}` : ''),
      big('Usage says', f1(c.xfp), 'expected pts / game')),
    fc,
    h('div', { class: 'grid2', style: 'margin-top:6px' },
      h('div', {}, h('h3', {}, '🔎 Why'), c.reasons.length ? h('div', { style: 'display:grid;gap:8px' }, c.reasons.map(r => h('div', { style: 'display:flex;gap:10px;align-items:flex-start;background:#0f1138;border:2px solid #2c3080;border-radius:12px;padding:8px 10px' }, h('span', { style: 'font-size:20px' }, r.icon), h('span', {}, r.text))))
        : h('p', { class: 'mut' }, 'Nothing unusual. What you see is what you get.')),
      h('div', {}, h('h3', {}, '💎 Value'), meter('Trade value (what the league thinks)', c.value, 'linear-gradient(90deg,#a64bff,#ff6bd6)'), meter('Value to you (your playoff weeks)', c.my_value, 'linear-gradient(90deg,#ffc83d,#ff9f43)'),
        lg.length ? [h('h3', {}, '📊 Every game (gold = 20+, red = dud)'), spark] : '')),
    h('h3', {}, `🗓️ Schedule (green = easy defense, red = tough) · rest of season ${c.sos_ros_label || '—'}, playoffs ${c.sos_po_label || '—'}`), sch,
    h('div', { class: 'bar', style: 'margin-top:16px' }, h('button', { class: 'btn small blue', onclick: () => { m.remove(); window.askCoach && askCoach(`Tell me about ${c.name}. Should I ${c.mine ? 'keep or trade him' : c.owner === 'Free agent' ? 'pick him up' : 'trade for him'}? Why?`); } }, '🤖 ASK COACH ABOUT HIM')),
    c.outlook ? [h('h3', {}, '📰 ESPN outlook'), h('p', { style: 'margin:0;line-height:1.55' }, c.outlook)] : '',
    h('details', { style: 'margin-top:18px' }, h('summary', { style: 'cursor:pointer;font-family:var(--game);color:var(--gold);font-size:16px' }, '🤓 Nerd stats'),
      nerdSum,
      h('h3', {}, 'Usage'), h('div', { class: 'stats' }, usageTiles),
      h('h3', {}, 'Next Gen Stats + PFR'), h('div', { class: 'stats' }, ngsTiles),
      h('h3', {}, `${c.nfl} offense`), h('div', { class: 'stats' }, teamTiles),
      h('h3', {}, 'Game log'), log)));
  document.body.append(m);
}
addEventListener('keydown', e => { if (e.key === 'Escape') $$('.modal').forEach(m => m.remove()); });

// ---------- schedule ----------
let SW = null;
function renderSched() {
  const S = D.schedule || []; if (!S.length) { $('#wkview').replaceChildren(h('div', { class: 'box mut' }, 'No schedule data yet.')); return; }
  if (SW === null || !S.find(x => x.w === SW)) SW = (S.find(x => x.w >= D.summary.week) || S[0]).w;
  $('#wkbar').replaceChildren(...S.map(x => h('button', { class: 'wk' + (x.w === SW ? ' on' : '') + (x.kind === 'po' ? ' po' : ''), onclick: () => { SW = x.w; renderSched(); } },
    h('div', { class: 'n' }, (x.kind === 'po' ? '🏆 ' : '') + 'W' + x.w), h('div', { class: 's' }, f1(x.total)),
    x.win_pct != null ? h('div', { class: 's ' + (x.win_pct >= 50 ? 'pos' : 'neg') }, Math.round(x.win_pct) + '%') : h('div', { class: 's mut' }, x.kind === 'po' ? 'playoffs' : ''))));
  const x = S.find(r => r.w === SW), i = S.indexOf(x);
  const lu = (L, mine) => h('div', { class: 'tw' }, h('table', {}, h('tbody', {}, L.map(p => h('tr', {}, h('td', { class: 'mut' }, p.slot),
    h('td', { class: 'l' }, h('span', { style: 'display:inline-flex;gap:8px;align-items:center' }, avatar(p.player, p.nfl, p.pos, VAL(p.player), 26), h('span', {}, mine ? pl(p.player) : p.player,
      h('div', { class: 'mut', style: 'font-size:12px', title: p.rank ? `defense rank vs his position: ${p.rank} (32 = easiest)` : '' }, p.opp, p.rank ? h('span', { class: p.rank >= 22 ? 'pos' : p.rank <= 11 ? 'neg' : 'mut' }, p.rank >= 22 ? ' · easy' : p.rank <= 11 ? ' · tough' : ' · avg') : '')))),
    h('td', {}, f1(p.pts), p.chance < 1 ? h('span', { class: 'opp ' + (p.chance < 0.7 ? 'neg' : '') }, Math.round(p.chance * 100) + '% to play') : '')))))); 
  const nav = h('div', { class: 'wknav' }, h('button', { class: 'btn small blue', disabled: i === 0 ? '' : null, onclick: () => { SW = S[i - 1].w; renderSched(); } }, '◀ PREV'),
    h('div', { class: 'wkt' }, x.label), h('button', { class: 'btn small blue', disabled: i === S.length - 1 ? '' : null, onclick: () => { SW = S[i + 1].w; renderSched(); } }, 'NEXT ▶'));
  const head = x.opponent ? h('div', { class: 'vs' }, h('div', { class: 'side' }, h('div', { class: 'k' }, 'YOU'), h('div', { class: 'v' }, f1(x.total))),
      h('div', { class: 'mid' }, h('div', { class: 'big ' + (x.win_pct >= 50 ? 'pos' : 'neg') }, Math.round(x.win_pct) + '%'), h('div', { class: 'k' }, 'to win')),
      h('div', { class: 'side', style: 'cursor:pointer', onclick: () => openTeam(x.opp_id) }, h('div', { class: 'k' }, x.opponent.toUpperCase()), h('div', { class: 'v' }, f1(x.opp_total))))
    : h('div', { class: 'vs' }, h('div', { class: 'side' }, h('div', { class: 'k' }, 'YOUR BEST LINEUP'), h('div', { class: 'v' }, f1(x.total))), h('div', { class: 'mid' }, h('div', { class: 'k' }, x.note || 'Opponent set by the bracket')));
  const notes = [];
  if (x.byes.length) notes.push(h('p', { class: 'sub' }, '😴 On bye: ' + x.byes.join(', ')));
  const outs = x.bench.filter(b => b.chance === 0 && b.opp !== 'BYE').map(b => b.player), iffy = x.hurt.filter(n => !outs.includes(n));
  if (outs.length) notes.push(h('p', { class: 'sub' }, '🚑 Out: ' + outs.join(', ')));
  if (iffy.length) notes.push(h('p', { class: 'sub' }, '🩹 Might not play: ' + iffy.join(', ')));
  (x.maybe || []).forEach(m => notes.push(h('p', { class: 'alert', style: 'background:#13163f;border-color:#ffc83d' }, `⭐ If ${m.player} is active, start him: ${f1(m.pts)} projected. Right now he's only ${Math.round(m.chance * 100)}% to play, so he's not in the lineup below.`)));
  notes.push(h('p', { class: 'sub', style: 'margin:0 0 8px' }, 'Each player shows what he scores if he plays. The total is your expected score after injury odds (same number as the League page).'));
  if (x.w === D.summary.week && [4, 5, 6, 0, 1].includes(new Date().getDay())) notes.push(h('p', { class: 'sub' }, '⏱️ This week is in progress, so the projection is for the full week and the win % comes from the live sim.'));
  $('#wkview').replaceChildren(nav, head, ...notes, h('div', { class: 'grid2', style: 'grid-template-columns:repeat(auto-fit,minmax(320px,1fr))' },
    h('div', { class: 'box' }, h('h3', {}, '🛡️ Your best lineup · ' + f1(x.total)), lu(x.lineup, true),
      x.bench.length ? h('p', { class: 'sub', style: 'margin:8px 0 0' }, 'Bench: ' + x.bench.map(b => `${b.player} ${b.opp === 'BYE' ? '(bye)' : f1(b.pts) + (b.chance != null && b.chance < 1 ? ` (${Math.round(b.chance * 100)}% to play)` : '')}`).join(', ')) : ''),
    x.opp_lineup ? h('div', { class: 'box' }, h('h3', {}, `⚔️ ${x.opponent} · ${f1(x.opp_total)}`), lu(x.opp_lineup, false)) : ''));
}
// ---------- breakouts ----------
let BP = 'all', BO = 'all';
function spark2(xs, ps, ws) {
  const W = 220, H = 54, mx = Math.max(10, ...xs, ...ps), X = i => 6 + i * (W - 12) / Math.max(1, xs.length - 1), Y = v => H - 6 - v / mx * (H - 14);
  const pth = a => a.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1)).join(' ');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('class', 'bspark');
  svg.innerHTML = `<path d="${pth(xs)}" fill="none" stroke="#4cc9ff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><path d="${pth(ps)}" fill="none" stroke="#ffc83d" stroke-width="2" stroke-dasharray="4 4" opacity=".85"/>` +
    xs.map((v, i) => `<circle cx="${X(i)}" cy="${Y(v)}" r="3" fill="#4cc9ff"><title>Wk ${ws[i]}: usage worth ${v}, scored ${ps[i]}</title></circle>`).join('');
  return svg;
}
function renderBoom() {
  const B = (D.breakouts || []).filter(b => (BP === 'all' || b.pos === BP) && (BO === 'all' || (BO === 'fa' ? b.owner === 'Free agent' : BO === 'mine' ? b.mine : b.owner !== 'Free agent' && !b.mine)));
  const AC = { ADD: ['real', '➕ ADD'], BUY: ['stretch', '💰 BUY'], STASH: ['real', '⭐ YOURS'], WATCH: ['no', '👀 WATCH'] };
  $('#bcards').replaceChildren(...(B.length ? B.slice(0, LIM.boom).map((b, i) => { const ring = h('div', { class: 'bring' }, h('div', { class: 'bpct' }, b.score + '%'), h('div', { class: 'k' }, 'starter odds')); ring.style.setProperty('--p', b.score);
    const c = h('div', { class: 'bcard' + (b.score >= 60 ? ' hot' : '') },
      h('div', { class: 'bhead' }, avatar(b.player, b.nfl, b.pos, VAL(b.player), 44), h('div', { style: 'flex:1;min-width:0' }, h('div', { class: 'bn' }, pl(b.player)), h('div', { class: 'sub', style: 'margin:0' }, `${b.pos} · ${b.nfl} · ${b.mine ? 'your team' : b.owner}`)),
        h('span', { class: 'tag ' + AC[b.action][0] }, AC[b.action][1])),
      h('div', { class: 'bmid' }, ring, h('div', { class: 'bnums' },
        h('div', {}, h('span', { class: 'mut' }, 'Season avg '), h('b', {}, f1(b.season))), h('div', {}, h('span', { class: 'mut' }, 'Projected next '), h('b', { class: 'pos' }, f1(b.proj))),
        h('div', {}, h('span', { class: 'mut' }, 'Starter line '), h('b', {}, f1(b.threshold))), h('div', {}, h('span', { class: 'mut' }, '20+ pt playoff game '), h('b', {}, Math.round(b.p_boom) + '%')))),
      h('div', { class: 'blegend' }, h('span', { style: 'color:#4cc9ff' }, '━ usage worth'), h('span', { style: 'color:#ffc83d' }, '┅ actually scored')), spark2(b.trend, b.pts, b.weeks),
      h('ul', { class: 'bwhy' }, b.why.map(t => h('li', {}, t))),
      h('div', { class: 'tbtns' }, h('button', { class: 'btn small blue', onclick: () => openCard(b.player) }, 'PLAYER CARD'),
        h('button', { class: 'btn small', onclick: () => window.askCoach && askCoach(`Is ${b.player} about to break out? The breakout sim gives him ${b.score}% to be a weekly starter. Should I ${b.action === 'ADD' ? 'pick him up and who would I drop' : b.action === 'BUY' ? 'try to trade for him and what would it cost' : 'start or hold him'}?`) }, '🤖 ASK COACH')));
    c.style.animationDelay = Math.min(i, 12) * 40 + 'ms'; return c; }) : [h('div', { class: 'box mut' }, 'Nobody in this group right now.')]), moreBtn('boom', B.length, renderBoom));
}
seg('bpos', v => { BP = v; renderBoom(); }); seg('bown', v => { BO = v; renderBoom(); });
// tab bar: fade + arrows on whichever side has more tabs
(() => { const nav = $('#tabs'), wrap = nav.parentElement; const upd = () => { const l = nav.scrollLeft > 4, r = nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 4; [nav, wrap].forEach(e => { e.classList.toggle('fl', l); e.classList.toggle('fr', r); }); };
  nav.addEventListener('scroll', upd, { passive: true }); addEventListener('resize', upd); setTimeout(upd, 50); setTimeout(upd, 600);
  $$('.navarr').forEach(b => b.addEventListener('click', () => nav.scrollBy({ left: (b.classList.contains('l') ? -1 : 1) * nav.clientWidth * 0.7, behavior: 'smooth' }))); })();

// ---------------- Prop Lab ----------------
let PLD = null, PLV = 'parlay', PLM = 'all', PLPICK = new Set(), PGS = 'bets', PPS = 'value';
const odds = a => (a > 0 ? '+' : '') + a;
const fairOf = p => { p = Math.min(Math.max(p, 0.01), 0.99); return p >= 0.5 ? Math.round(-100 * p / (1 - p)) : Math.round(100 * (1 - p) / p); };
const usd = u => '$' + (Math.round(u * ((PLD && PLD.unit) || 25) * 100) / 100).toFixed(2).replace(/\.00$/, '');
const kickT = k => k ? new Date(k).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
function legRow(l) { return h('div', { class: 'leg' }, h('span', { class: 'lp ' + (l.p >= 80 ? 'hi' : '') }, Math.round(l.p) + '%'), h('span', { class: 'll' }, l.label), l.lean >= 2 ? h('span', { class: 'tag real', title: 'Our sims lean this way vs the market' }, '✓ LEAN') : ''); }
async function renderProps() {
  if (!PLD) PLD = await fetch('data/props_lab.json?v=' + Date.now()).then(r => r.ok ? r.json() : null).catch(() => null);
  const body = $('#pbody');
  if (!PLD) { body.replaceChildren(h('div', { class: 'box mut' }, 'Prop Lab data isn\'t built yet. It runs with the next update.')); return; }
  const ago = PLD.lines_at ? Math.round((Date.now() - new Date(PLD.lines_at)) / 36e5) : null;
  $('#psub').textContent = `Week ${PLD.week}: every game simulated ${PLD.sims.toLocaleString()} times${ago != null ? `, prop lines from ${ago < 1 ? 'under an hour' : ago + 'h'} ago` : ''}. Fair odds = what the sims say the bet is really worth.`;
  const tip = h('div', { class: 'box ptip' }, h('b', {}, '💡 How to use it: '), 'Only take a bet when the app pays MORE than our fair odds (or on Kalshi/Polymarket, when it costs LESS than our max price). The sims start from the betting market and only move for things that held up over 10 seasons, so most edges are small.');
  let out = [];
  if (PLV === 'parlay') {
    const card = pl => h('div', { class: 'pcard' }, h('div', { class: 'phead' }, h('div', {}, h('div', { class: 'pbig' }, Math.round(pl.p) + '%'), h('div', { class: 'k' }, 'hits in the sims')),
        h('div', { style: 'text-align:right' }, h('div', { class: 'pfair' }, odds(pl.fair)), h('div', { class: 'k' }, 'fair odds'), pl.same_game ? h('span', { class: 'tag stretch', style: 'margin-top:4px' }, 'SAME GAME') : '')),
      ...pl.legs.map(legRow), pl.units ? h('div', { class: 'pstake' }, h('span', {}, `Bet ${usd(pl.units)}`), h('span', { class: 'k' }, `only if DraftKings pays better than ${odds(pl.fair)}`))
        : h('div', { class: 'pstake', style: 'color:var(--mut);border-color:#3a3f9e' }, h('span', {}, 'No model edge'), h('span', { class: 'k' }, `fair is ${odds(pl.fair)}: only worth it if DraftKings pays more`)));
    const P = PLD.parlays || {};
    const srt = L => PPS === 'likely' ? [...(L || [])].sort((a, b) => b.p - a.p) : L;
    const sect = (t, L) => L && L.length ? [h('h3', { style: 'margin-top:18px' }, t), h('div', { class: 'bgrid' }, ...srt(L).map(card))] : [];
    const sbar = h('div', { class: 'seg', style: 'margin:0 0 6px' }, ...[['value', '💎 Best value'], ['likely', '🎯 Most likely to hit']].map(([v, t]) => h('button', { class: PPS === v ? 'on' : '', onclick: () => { PPS = v; renderProps(); } }, t)));
    out = [tip, sbar, ...sect('🔥 Model\'s confident parlays (bigger payout, every leg is one the sims like more than the market)', P.conf), ...sect('🏈 4-team game parlays (+100 or better)', P.g4), ...sect('🏈 3-team game parlays', P.g3), ...sect('🎯 2-leg player props', P['2']), ...sect('🎯 3-leg player props (+100ish)', P['3'])];
    const T = PLD.teasers || [];
    if (T.length) out.push(h('h3', { style: 'margin-top:18px' }, '🧷 6-point teaser legs'), h('p', { class: 'sub', style: 'margin:0 0 8px' }, `Underdogs of +1.5 to +2.5 teased up 6 points have covered about 78% since 2006. A 2-team teaser at -120 needs ${T[0].breakeven}% per leg, so pair two of these from different games.`),
      h('div', { class: 'leglist' }, ...T.map(t => h('div', { class: 'legpick' }, h('span', { class: 'lp hi' }, Math.round(t.p) + '%'), h('span', { class: 'll' }, t.label), h('span', { class: 'mut', style: 'margin-left:auto' }, kickT(t.kick))))));
    // build your own
    const legs = PLD.legs || [];
    const chosen = legs.filter(l => PLPICK.has(l.label));
    const byG = {}; chosen.forEach(l => (byG[l.game || l.label] = byG[l.game || l.label] || []).push(l));
    const bitsOf = l => l._b || (l._b = Uint8Array.from(atob(l.bits || ''), c => c.charCodeAt(0)));
    const pop = b => { let n = 0; for (let i = 0; i < b.length; i++) { let v = b[i]; while (v) { n += v & 1; v >>= 1; } } return n; };
    const pj = Object.values(byG).reduce((a, g) => { if (g.length === 1 || !g.every(l => l.bits)) return a * g.reduce((x, l) => x * l.p / 100, 1);
      const m = bitsOf(g[0]).slice(); g.slice(1).forEach(l => { const b = bitsOf(l); for (let i = 0; i < m.length; i++) m[i] &= b[i]; }); return a * pop(m) / (m.length * 8); }, 1);
    const sum = h('div', { class: 'pbuild' }, chosen.length ? h('span', {}, `${chosen.length} legs: `, h('b', {}, Math.round(pj * 100) + '%'), ` to hit, fair odds `, h('b', { class: 'pos' }, odds(fairOf(pj)))) : h('span', { class: 'mut' }, 'Tap legs below to build your own.'),
      chosen.length ? h('button', { class: 'btn small', onclick: () => { PLPICK.clear(); renderProps(); } }, 'CLEAR') : '');
    out.push(h('h3', { style: 'margin-top:18px' }, '🧱 Build your own'), h('p', { class: 'sub', style: 'margin:0 0 8px' }, 'Every leg here hits 70 to 90% of the time in the sims: moneylines, alt spreads, alt totals and alt player lines. Legs from the same game are priced together from the same sims; different games are independent.'), sum,
      h('div', { class: 'leglist' }, ...legs.map(l => h('label', { class: 'legpick' + (PLPICK.has(l.label) ? ' on' : '') }, h('input', { type: 'checkbox', ...(PLPICK.has(l.label) ? { checked: '' } : {}), onchange: e => { e.target.checked ? PLPICK.add(l.label) : PLPICK.delete(l.label); renderProps(); } }),
        h('span', { class: 'lp' }, Math.round(l.p) + '%'), h('span', { class: 'll' }, l.label), h('span', { class: 'mut', style: 'margin-left:auto' }, odds(l.fair))))));
  } else if (PLV === 'props') {
    const mk = ['all', ...new Set((PLD.props || []).map(x => x.market))];
    const L = (PLD.props || []).filter(x => PLM === 'all' || x.market === PLM);
    out = [tip, h('div', { class: 'seg', style: 'margin:10px 0' }, ...mk.map(m => h('button', { class: m === PLM ? 'on' : '', onclick: () => { PLM = m; renderProps(); } }, m === 'all' ? 'All' : m))),
      h('div', { class: 'bgrid' }, ...L.slice(0, 40).map(x => h('div', { class: 'pcard' },
        h('div', { class: 'phead' }, h('div', { style: 'min-width:0' }, h('div', { class: 'bn' }, x.player), h('div', { class: 'k' }, `${x.pos} · ${x.team} vs ${x.opp} · ${kickT(x.kick)}`)), h('span', { class: 'grade g' + x.grade }, x.grade)),
        h('div', { class: 'pline' }, `${x.side} ${x.line != null ? x.line + ' ' : ''}${x.market.toLowerCase()}`),
        h('div', { class: 'pnums' }, h('div', {}, h('b', { class: 'pos' }, Math.round(x.p) + '%'), h('span', { class: 'k' }, 'our chance')), h('div', {}, h('b', {}, Math.round(x.p_mkt) + '%'), h('span', { class: 'k' }, 'market')),
          h('div', {}, h('b', { class: 'gold' }, x.max_c + '¢'), h('span', { class: 'k' }, 'max to pay'))),
        x.units ? h('div', { class: 'pstake' }, h('span', {}, `Bet ${usd(x.units)}`), h('span', { class: 'k' }, `${x.units} unit${x.units === 1 ? '' : 's'}`)) : '',
        (x.news || []).length ? h('div', { class: 'k', style: 'color:var(--gold)' }, '📰 ' + x.news.join(', ') + '. Line is from before the news.') : '',
        x.market !== 'Anytime TD' ? h('div', { class: 'k' }, `Sims: usually ${x.p10} to ${x.p90}, middle ${x.median}. Our avg ${x.ours} vs market ${x.mkt}.`) : h('div', { class: 'k' }, `Market ${Math.round(x.p_mkt)}%, ours ${Math.round(x.p)}%.`))))];
    if (!L.length) out.push(h('div', { class: 'box mut' }, 'No props clear the bar right now.'));
  } else if (PLV === 'games') {
    const now = Date.now();
    const best = g => Math.max(0, ...g.plays.map(p => p.edge));
    const fav = g => Math.max(g.sim.home_win, 100 - g.sim.home_win);
    let G = (PLD.games || []).filter(g => !g.started);
    G = PGS === 'bets' ? G.sort((a, b) => (b.plays.length - a.plays.length) || (best(b) - best(a)) || (b.angles.length - a.angles.length) || (fav(b) - fav(a)))
      : PGS === 'likely' ? G.sort((a, b) => fav(b) - fav(a)) : G.sort((a, b) => (a.kick || '').localeCompare(b.kick || ''));
    const gbar = h('div', { class: 'seg', style: 'margin:0 0 10px' }, ...[['bets', '🔥 Worth betting first'], ['likely', '🎯 Most likely to win'], ['time', '🕐 Kickoff']].map(([v, t]) => h('button', { class: PGS === v ? 'on' : '', onclick: () => { PGS = v; renderProps(); } }, t)));
    const done = (PLD.games || []).filter(g => g.started).length;
    out = [gbar, done ? h('p', { class: 'sub', style: 'margin:0 0 8px' }, `${done} game${done === 1 ? '' : 's'} already started (hidden).`) : '', h('div', { class: 'bgrid' }, ...G.map(g => { const s = g.sim;
      return h('div', { class: 'pcard' + (g.started ? ' dim' : '') }, h('div', { class: 'phead' }, h('div', {}, h('div', { class: 'bn' }, `${g.away} @ ${g.home}`), h('div', { class: 'k' }, `${kickT(g.kick)}${g.started ? ' · started' : ''}`)),
          h('div', { style: 'text-align:right' }, h('div', { class: 'k' }, `${g.home} ${(-g.spread) > 0 ? '+' : ''}${-g.spread} · O/U ${g.total}`))),
        h('div', { class: 'pnums' }, h('div', {}, h('b', {}, `${s.away_pts}-${s.home_pts}`), h('span', { class: 'k' }, 'avg score')), h('div', {}, h('b', { class: 'pos' }, Math.round(Math.max(s.home_win, 100 - s.home_win)) + '%'), h('span', { class: 'k' }, `${s.home_win >= 50 ? g.home : g.away} wins`)),
          h('div', {}, h('b', {}, odds(s.fair_home_ml)), h('span', { class: 'k' }, `fair ${g.home} ML`))),
        h('div', { class: 'k' }, `One-score game ${Math.round(s.one_score)}% · blowout (17+) ${Math.round(s.blowout)}%${g.weather ? ` · ${g.weather.temp}°F, wind ${g.weather.wind} mph` : g.roof === 'dome' ? ' · indoors' : ''}`),
        ...g.angles.map(a => h('div', { class: 'k', style: 'color:var(--gold)' }, '📐 ' + a)),
        ...(g.plays.length ? g.plays.map(p => h('div', { class: 'leg' }, h('span', { class: 'tag real' }, 'PLAY'), h('span', { class: 'll' }, `${p.pick}: ${Math.round(p.p)}% vs market ${Math.round(p.p_mkt)}%, pay up to ${p.max_c}¢. Bet ${usd(p.units || 0.5)}`))) : [h('div', { class: 'k mut' }, g.angles.length ? `Leans under (${Math.round(100 - s.over)}% in the sims), but not by enough to beat the price.` : 'No edge on the spread or total. The line is the best guess here.')]));
    }))];
  } else if (PLV === 'long') {
    const L = PLD.longshots || { singles: [], parlays: [] };
    out = [h('div', { class: 'box ptip' }, '🎰 For fun. These hit 1 to 35% of the time, so most of them lose. Every leg here is one the sims like more than the market does. Keep it to a small bet: ', h('b', {}, usd(0.2)), ' or so.'),
      ...((L.fav_stacks || []).length ? [h('h3', {}, '💪 Favorite stacks (about +250 to +400)'), h('div', { class: 'bgrid' }, ...L.fav_stacks.map(pl => h('div', { class: 'pcard' }, h('div', { class: 'phead' }, h('div', {}, h('div', { class: 'pbig' }, pl.p + '%'), h('div', { class: 'k' }, 'all hit in the sims')),
        h('div', { style: 'text-align:right' }, h('div', { class: 'pfair' }, odds(pl.fair)), h('div', { class: 'k' }, 'fair odds'))), ...pl.legs.map(legRow), h('div', { class: 'k' }, `Worth it only if DraftKings pays more than ${odds(pl.fair)}.`))))] : []),
      h('h3', { style: 'margin-top:18px' }, '🎰 Longshot parlays'), h('div', { class: 'bgrid' }, ...(L.parlays || []).map(pl => h('div', { class: 'pcard' }, h('div', { class: 'phead' }, h('div', {}, h('div', { class: 'pbig', style: 'color:var(--gold)' }, pl.p + '%'), h('div', { class: 'k' }, 'hits in the sims')),
        h('div', { style: 'text-align:right' }, h('div', { class: 'pfair' }, odds(pl.fair)), h('div', { class: 'k' }, 'fair odds'))), ...pl.legs.map(legRow), h('div', { class: 'k' }, `Worth it only if DraftKings pays more than ${odds(pl.fair)}.`)))),
      h('h3', { style: 'margin-top:18px' }, '🎯 Longshot singles'), h('div', { class: 'leglist' }, ...(L.singles || []).map(x => h('div', { class: 'legpick' }, h('span', { class: 'lp' }, Math.round(x.p) + '%'), h('span', { class: 'll' }, x.label), h('span', { class: 'mut', style: 'margin-left:auto;text-align:right' }, `fair ${odds(x.fair)}`, x.price ? h('div', { class: 'k' }, `best ${odds(x.price)}`) : ''))))];
  } else if (PLV === 'pickem') {
    const L = PLD.pickem_plays || [];
    out = [h('div', { class: 'box ptip' }, `Pick'em apps (PrizePicks, Underdog, Pick6) pay a fixed amount, so all that matters is the chance each leg hits. Rough break-even per leg: 2-pick at 3x needs 57.7%, 3-pick at 6x needs 55%, 5-pick flex needs about 54%. Lines on those apps can differ from these, so check the number.`),
      h('div', { class: 'leglist' }, ...L.map(x => h('div', { class: 'legpick' }, h('span', { class: 'lp ' + (x.p >= 57.7 ? 'hi' : '') }, Math.round(x.p) + '%'), h('span', { class: 'll' }, `${x.player} ${x.side} ${x.line} ${x.market.toLowerCase()}`), h('span', { class: 'mut', style: 'margin-left:auto' }, `${x.team} vs ${x.opp}`))))];
    if (!L.length) out.push(h('div', { class: 'box mut' }, `Nothing at ${PLD.pickem_cut}%+ right now.`));
  } else {
    const R = PLD.record || {}; const row = (n, b) => h('div', { class: 'pathrow', style: 'cursor:default' }, h('span', { class: 'pn' }, n), h('span', {}, `${b[0]}-${b[1]}`), h('b', { class: b[2] >= 0 ? 'pos' : 'neg' }, `${b[2] >= 0 ? '+' : '-'}${usd(Math.abs(b[2]))}`));
    out = [h('div', { class: 'box' }, h('h3', {}, '📊 How the picks have done'), h('p', { class: 'sub', style: 'margin:0 0 8px' }, `Every graded play at 1 unit (${usd(1)}), bought at the market price. Small samples swing a lot: give it a few weeks before trusting it.`),
      row('Player props', R.props || [0, 0, 0]), row('Game lines', R.games || [0, 0, 0]),
      h('h3', { style: 'margin-top:14px' }, '🧾 Every-bet tracker'), h('p', { class: 'sub', style: 'margin:0 0 8px' }, `Pretend 1 unit (${usd(1)}) on every game line where the sims like a side more than the market and it's -150 or better, and every prop the sims like by 1+ point at -150 or better, at DraftKings' price. This is the honest test of whether the model has an edge.${R.auto_open ? ` Open this week: ${R.auto_open.games} game bets, ${R.auto_open.props} props.` : ''}`),
      row('All game lines', R.auto_games || [0, 0, 0]), row('All props', R.auto_props || [0, 0, 0]),
      h('div', { class: 'k', style: 'margin-top:10px' }, `Backtest: ${PLD.backtest.ats_model}. The closing line missed by ${PLD.backtest.line_mae} pts on average vs ${PLD.backtest.model_mae} for a ratings model, so game sims start from the line.`)),
      h('div', { class: 'leglist', style: 'margin-top:12px' }, ...(PLD.recent || []).map(x => h('div', { class: 'legpick' }, h('span', { class: 'tag ' + (x.void || x.result == null ? 'no' : x.result ? 'real' : 'stretch') }, x.void ? 'VOID' : x.result == null ? 'PUSH' : x.result ? 'WIN' : 'LOSS'),
        h('span', { class: 'll' }, x.pick ? `Wk ${x.week} ${x.game}: ${x.pick}` : `Wk ${x.week} ${x.player} ${x.side} ${x.line ?? ''} ${x.market.toLowerCase()}${x.actual != null ? ` (had ${x.actual})` : ''}`)))),
      !(PLD.recent || []).length ? h('div', { class: 'box mut' }, 'No graded picks yet. This week\'s picks get graded after the games.') : ''];
  }
  body.replaceChildren(...out.filter(Boolean));
}
seg('pview', v => { PLV = v; renderProps(); });

const RENDER = { props: renderProps, boom: renderBoom, sched: renderSched, home: renderHome, trades: renderTrades, lab: renderLab, waivers: renderWaivers, ranks: renderRanks, team: renderTeam, league: renderLeague, how: renderHow };
let rz; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { const t = $('nav.tabs button.on').dataset.tab; if (['home', 'trades', 'league'].includes(t)) RENDER[t](); }, 200); });

(async () => {
  const v = Date.now();
  const idx = await fetch('data/leagues.json?v=' + v).then(r => r.json()).catch(() => null);
  const leagues = idx ? idx.leagues : []; LEAGUES = leagues;
  const want = new URLSearchParams(location.search).get('league') || (() => { try { return localStorage.getItem('league'); } catch (e) { return null; } })();
  LID = (leagues.find(l => l.id === want) || leagues[0] || {}).id;
  BASE = LID ? `data/${LID}/` : 'data/';
  const sel = $('#league');
  sel.replaceChildren(...leagues.map(l => h('option', { value: l.id }, `${l.name} · ${f1(l.champ_pct)}%`)));
  sel.value = LID || ''; sel.parentElement.hidden = leagues.length < 2;
  sel.addEventListener('change', () => { try { localStorage.setItem('league', sel.value); } catch (e) {} const u = new URL(location); u.searchParams.set('league', sel.value); location.href = u.toString(); });
  if (idx && idx.status && idx.status.espn_ok === false) $('#banner').replaceChildren(h('div', { class: 'banner' }, '⚠️ ' + idx.status.message));
  try {
    [D, H] = await Promise.all([fetch(BASE + 'latest.json?v=' + v).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }), fetch(BASE + 'history.json?v=' + v).then(r => r.json()).catch(() => ({ runs: [], moves: [] }))]);
  } catch (e) {
    $('#banner').replaceChildren(h('div', { class: 'banner' }, '⚠️ Couldn\'t load your league. Check your connection, then pull down to retry.', h('button', { class: 'btn small', style: 'margin-left:8px', onclick: () => location.reload() }, 'RETRY')));
    document.body.classList.remove('loading'); return;
  }
  document.body.classList.remove('loading');
  { const hrs = (Date.now() - new Date(D.summary.at)) / 36e5; if (hrs > 12) $('#banner').append(h('div', { class: 'banner' }, `⚠️ This data is ${Math.round(hrs)} hours old. The auto-update may be stuck.`)); }
  TIERS = D.summary.tiers || {};
  const at = new Date(D.summary.at);
  $('#upd').textContent = `${D.summary.my_team} · Week ${D.summary.week} · updated ${at.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}`;
  document.title = `Oxtail Lab · ${D.summary.league}`;
  const start = (location.hash || '#home').slice(1);
  const card_ = new URLSearchParams(location.search).get('card');
  if (card_) { history.replaceState(null, '', location.pathname + '?league=' + LID + location.hash); setTimeout(() => openCard(card_), 700); }
  tab(RENDER[start] ? start : 'home');
  setTimeout(() => fetch(BASE + 'players.json?v=' + D.summary.built).then(r => r.json()).then(j => { CARDS = j; }).catch(() => {}), 600);
})();

// installable app: register the service worker, and refresh the data whenever the app comes back to the screen
if ('serviceWorker' in navigator) addEventListener('load', () => navigator.serviceWorker.register('sw.js', { updateViaCache: 'none' }).then(r => { r.update().catch(() => {}); document.addEventListener('visibilitychange', () => { if (!document.hidden) r.update().catch(() => {}); }); }).catch(() => {}));
// a tapped alert the service worker saved: show it whenever the app is on screen (covers taps while the app was asleep)
let PEND_BUSY = false;
async function checkPendingAlert() {
  if (PEND_BUSY || document.hidden) return; PEND_BUSY = true;
  try {
    const c = await caches.open('oxtail-alerts'); const r = await c.match('pending');
    if (r) { const p = await r.json(); await c.delete('pending');
      if (Date.now() - (p.t || 0) < 15 * 60 * 1000 && p.id) { $$('.modal').forEach(m => m.remove()); if (p.alert && p.alert.report) { openAlert(p.alert); renderAlertList(); } else await showAlertById(p.id); } }
  } catch (e) {}
  PEND_BUSY = false;
}
document.addEventListener('visibilitychange', () => setTimeout(checkPendingAlert, 250));
addEventListener('focus', () => setTimeout(checkPendingAlert, 250));
addEventListener('pageshow', () => setTimeout(checkPendingAlert, 400));
setTimeout(checkPendingAlert, 900);
// safety net: when the app comes on screen, show any alert from the last 20 minutes you haven't opened yet
// (keeps checking for 2 minutes, since the site can take a minute to publish a brand-new alert)
let FRESH_T = 0;
async function showFreshAlert() {
  if (document.hidden || $$('.modal').length) return;
  const t0 = Date.now(); FRESH_T = t0;
  for (let i = 0; i < 9 && FRESH_T === t0 && !document.hidden; i++) {
    ALERTS = null;
    const seen = seenAlerts();
    const a = (await loadAlerts()).find(x => !seen.has(x.id) && Date.now() - new Date(x.at) < 20 * 60 * 1000);
    if (a) { if (!$$('.modal').length) { openAlert(a); renderAlertList(); } else markSeen(a.id); return; }   // never close something you have open
    if ($$('.asheetwrap').length) return;
    await new Promise(r => setTimeout(r, 15000));
  }
}
try { if (!localStorage.getItem(SEEN_K)) localStorage.setItem(SEEN_K, '[]'); } catch (e) {}
document.addEventListener('visibilitychange', () => { if (!document.hidden) setTimeout(showFreshAlert, 1500); });
setTimeout(showFreshAlert, 2500);
let _hiddenAt = 0;
document.addEventListener('visibilitychange', () => { if (document.hidden) _hiddenAt = Date.now(); else if (_hiddenAt && Date.now() - _hiddenAt > 10 * 60 * 1000) location.reload(); });



// ---------- alert reports: tapping a phone alert opens ?alert=<id> ----------
let ALERTS = null;
async function loadAlerts() {
  if (!ALERTS) {
    ALERTS = await fetch('data/alerts.json?v=' + Date.now()).then(r => r.ok ? r.json() : { alerts: [] }).catch(() => ({ alerts: [] }));
    try { const c = await caches.open('oxtail-alerts'); const have = new Set((ALERTS.alerts || []).map(a => a.id));   // add ones that came by push but aren't published yet
      for (const k of await c.keys()) { const a = await (await c.match(k)).json(); if (!have.has(a.id)) (ALERTS.alerts = ALERTS.alerts || []).push(a); }
      ALERTS.alerts.sort((a, b) => (b.at || '').localeCompare(a.at || '')); } catch (e) {}
  }
  return ALERTS.alerts || [];
}
const SEEN_K = 'oxtail_seen_alerts';
const seenAlerts = () => { try { return new Set(JSON.parse(localStorage.getItem(SEEN_K) || '[]')); } catch (e) { return new Set(); } };
const markSeen = id => { try { const s = seenAlerts(); s.add(id); localStorage.setItem(SEEN_K, JSON.stringify([...s].slice(-200))); } catch (e) {} };
function openAlert(a) {
  markSeen(a.id);
  const ago = m => { const d = (Date.now() - new Date(m)) / 6e4; return d < 60 ? Math.round(d) + ' min ago' : d < 1440 ? Math.round(d / 60) + ' hr ago' : Math.round(d / 1440) + ' days ago'; };
  const sec = (k, t) => t ? h('div', { class: 'arep' }, h('div', { class: 'k' }, k), h('p', {}, t)) : '';
  const m = h('div', { class: 'modal asheetwrap', onclick: e => { if (e.target === m) m.remove(); } }, h('div', { class: 'asheet' },
    h('div', { class: 'ashead' }, h('div', { style: 'flex:1;min-width:0' }, h('div', { class: 'astitle' }, a.title),
      h('div', { class: 'sub', style: 'margin:2px 0 0;font-size:12px' }, ago(a.at) + (a.leagues && a.leagues.length ? ' · ' + a.leagues.join(', ') : ''))),
      h('button', { class: 'asx', 'aria-label': 'Close', onclick: () => m.remove() }, '×')),
    h('div', { class: 'asbody' }, sec('📰 WHAT HAPPENED', a.report.what), sec('📊 WHAT IT MEANS', a.report.means), sec('✅ WHAT TO DO', a.report.todo),
      h('div', { class: 'bar', style: 'margin-top:8px' }, (a.actions || []).map(x => h('button', { class: 'btn small', onclick: () => {
        m.remove();
        if (x.league && x.league !== LID) { location.href = `?league=${x.league}${x.player ? '&card=' + encodeURIComponent(x.player) : ''}#${x.tab || 'home'}`; return; }
        if (x.player) openCard(x.player); else if (x.tab) tab(x.tab);
      } }, x.label))))));
  document.body.append(m);
}
async function showAlertById(id) {
  // the report can land a few seconds after the alert itself: show a loading sheet and keep checking for up to 2 minutes
  const sheet = h('div', { class: 'modal' }, h('div', { class: 'mbox', style: 'max-width:520px;text-align:center' }, h('button', { class: 'x', onclick: () => sheet.remove() }, '×'), h('h2', { style: 'font-size:22px' }, '📣 Loading alert…'), h('p', { class: 'sub' }, 'Grabbing the full report.')));
  let shown = false;
  // 1) the report usually rode in with the push itself: show it right away, whatever league you're on
  try { const c = await caches.open('oxtail-alerts'); const r = await c.match('alert-' + id); if (r) { const a = await r.json(); $$('.modal').forEach(m => m.remove()); openAlert(a); renderAlertList(); return; } } catch (e) {}
  // 2) otherwise wait for the site to publish it (up to 5 minutes)
  for (let i = 0; i < 60; i++) {
    ALERTS = null;
    const a = (await loadAlerts()).find(x => x.id === id);
    if (a) { sheet.remove(); openAlert(a); shown = true; break; }
    if (i === 0) document.body.append(sheet);
    if (!document.body.contains(sheet)) return;   // closed it
    await new Promise(r => setTimeout(r, 5000));
  }
  if (!shown && document.body.contains(sheet)) sheet.querySelector('p').textContent = 'Couldn\'t find this alert\'s report. Pull down to refresh in a minute.';
  renderAlertList();
}
async function renderAlertList() {
  const list = await loadAlerts(), box = document.getElementById('alertlist');
  if (box) box.replaceChildren(...(list.length ? list.slice(0, 5).map(a => h('div', { class: 'todoi', onclick: () => openAlert(a) }, h('div', { style: 'flex:1' }, h('b', {}, a.title), h('div', { class: 'td' }, a.body)))) : [h('p', { class: 'mut', style: 'margin:0' }, 'No alerts yet. You\'ll get one when something changes with your players.')]));
}
(async () => {
  const id = new URLSearchParams(location.search).get('alert');
  if (id) { caches.open('oxtail-alerts').then(c => c.delete('pending')).catch(() => {}); history.replaceState(null, '', location.pathname + (LID ? '?league=' + LID : '') + location.hash); setTimeout(() => showAlertById(id), 400); }
  renderAlertList();
  if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('message', ev => { if (ev.data && ev.data.type === 'alert' && ev.data.id) { caches.open('oxtail-alerts').then(c => c.delete('pending')).catch(() => {}); $$('.modal').forEach(m => m.remove()); if (ev.data.alert && ev.data.alert.report) openAlert(ev.data.alert); else showAlertById(ev.data.id); } });
  // if the app was asleep when you tapped, the message can get lost: check again when it comes back to the screen
  document.addEventListener('visibilitychange', () => { const id = new URLSearchParams(location.search).get('alert'); if (!document.hidden && id && !$$('.asheetwrap').length) showAlertById(id); });
})();

document.addEventListener('click', e => { if (e.target.classList && e.target.classList.contains('modal')) e.target.remove(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') { const m = $$('.modal'); if (m.length) m[m.length - 1].remove(); } });
