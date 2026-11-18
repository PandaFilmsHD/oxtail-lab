// Oxtail Lab front end. Reads data/latest.json, data/history.json, data/players.json (built by scripts/analyze.py).
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs || {})) { if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else if (v != null) e.setAttribute(k, v); } for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : String(c)); return e; };
const f1 = v => v == null || v === '' || Number.isNaN(+v) ? '—' : (+v).toFixed(1);
const pct = v => v == null ? '—' : (+v).toFixed(1) + '%';
const sgn = v => v == null ? '—' : (v > 0 ? '+' : '') + (+v).toFixed(1);
const cls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
let D, H, CARDS = null, LID = null, BASE = 'data/';
const tierOf = v => v == null ? null : v >= 80 ? 'legendary' : v >= 55 ? 'epic' : v >= 30 ? 'rare' : v >= 12 ? 'uncommon' : 'common';
const tierEl = v => { const t = tierOf(v); return t ? h('span', { class: 'tier ' + t, title: 'Trade value ' + v }, t.toUpperCase()) : ''; };
const TEAMC = { ARI: ['#97233F', '#FFB612'], ATL: ['#A71930', '#000000'], BAL: ['#241773', '#9E7C0C'], BUF: ['#00338D', '#C60C30'], CAR: ['#0085CA', '#101820'], CHI: ['#0B162A', '#C83803'], CIN: ['#FB4F14', '#000000'], CLE: ['#311D00', '#FF3C00'],
  DAL: ['#003594', '#869397'], DEN: ['#FB4F14', '#002244'], DET: ['#0076B6', '#B0B7BC'], GB: ['#203731', '#FFB612'], HOU: ['#03202F', '#A71930'], IND: ['#002C5F', '#A2AAAD'], JAX: ['#006778', '#D7A22A'], KC: ['#E31837', '#FFB81C'],
  LV: ['#000000', '#A5ACAF'], LAC: ['#0080C6', '#FFC20E'], LAR: ['#003594', '#FFA300'], MIA: ['#008E97', '#FC4C02'], MIN: ['#4F2683', '#FFC62F'], NE: ['#002244', '#C60C30'], NO: ['#101820', '#D3BC8D'], NYG: ['#0B2265', '#A71930'],
  NYJ: ['#125740', '#FFFFFF'], PHI: ['#004C54', '#A5ACAF'], PIT: ['#101820', '#FFB612'], SF: ['#AA0000', '#B3995D'], SEA: ['#002244', '#69BE28'], TB: ['#D50A0A', '#34302B'], TEN: ['#0C2340', '#4B92DB'], WSH: ['#5A1414', '#FFB612'] };
const initials = n => n.replace(/ (Jr\.|Sr\.|II|III|IV|V)$/, '').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
function avatar(name, nfl, pos, value, size = 28) {
  const [a, b] = TEAMC[nfl] || ['#3f5bff', '#a66bff'], t = tierOf(value);
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
const pl = n => h('a', { class: 'pl', href: '#', onclick: e => { e.preventDefault(); e.stopPropagation(); openCard(n); } }, n);

// ---------- charts ----------
function hbar(el, rows, { label, value, fmt, color, tipText }) {
  el.replaceChildren(); if (!rows.length) { el.append(h('div', { class: 'mut' }, 'Nothing to show')); return; }
  const W = Math.max(300, el.clientWidth - 20), bh = 22, gap = 6, H_ = rows.length * (bh + gap) + 6;
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H_}`);
  const lw = Math.min(W * 0.55, Math.max(...rows.map(r => label(r).length)) * 6.4 + 10);
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
function tab(name) { $$('nav.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name)); $$('section').forEach(s => s.hidden = s.id !== name); history.replaceState(null, '', location.search + '#' + name); if (RENDER[name]) RENDER[name](); }
$$('nav.tabs button').forEach(b => b.addEventListener('click', () => tab(b.dataset.tab)));
function seg(id, onChange) { $$('#' + id + ' button').forEach(b => b.addEventListener('click', () => { $$('#' + id + ' button').forEach(x => x.classList.toggle('on', x === b)); onChange(b.dataset.v); })); }

// ---------- home ----------
function renderHome() {
  const s = D.summary, k = s.kpis, prev = H.runs.length > 1 ? H.runs[H.runs.length - 2] : null;
  gauge($('#gauge'), k.ch); countUp($('#gnum'), k.ch);
  const rank = [...D.league].sort((a, b) => b.champ_pct - a.champ_pct).findIndex(r => r.me) + 1;
  $('#grank').textContent = `#${rank} of ${D.league.length} in title odds` + (prev && Math.abs(k.ch - prev.champ_pct) >= 0.1 ? ` · ${sgn(k.ch - prev.champ_pct)} since last update` : '');
  const tile = (c, label, v, pv) => { const vEl = h('span', {}, '0%'), bar = h('i'); const t = h('div', { class: 'tile ' + c }, h('div', { class: 'k' }, label), h('div', { class: 'v' }, vEl, pv != null && Math.abs(v - pv) >= 0.1 ? h('span', { class: 'd' }, sgn(v - pv)) : null), h('div', { class: 'bar' }, bar)); setTimeout(() => { countUp(vEl, v); bar.style.width = Math.min(100, v) + '%'; }, 60); return t; };
  $('#kpis').replaceChildren(tile('t1', '🏁 Make the final', k.fi, prev && prev.final_pct), tile('t2', '✅ Make playoffs', k.po, prev && prev.playoffs_pct), tile('t3', '😴 First round bye', k.bye, prev && prev.bye_pct), tile('t4', '👑 1 seed', k.s1, prev && prev.seed1_pct));
  $('#take').replaceChildren(...s.take.map((t, i) => { const li = h('li', {}, t); li.style.animationDelay = i * 70 + 'ms'; return li; }));
  const tw = s.this_week; $('#tw-h').textContent = `🗓️ Week ${tw.week} best lineup`;
  const lu = h('table', {}, h('tbody', {}, tw.lineup.map(x => h('tr', {}, h('td', { class: 'mut' }, x.slot), h('td', { class: 'l' }, h('span', { style: 'display:inline-flex;gap:8px;align-items:center' }, avatar(x.player, NFL(x.player), x.slot, VAL(x.player), 26), pl(x.player), tierEl(VAL(x.player)))), h('td', {}, f1(x.pts))))));
  $('#thisweek').replaceChildren(
    tw.opponent ? h('p', { style: 'margin:0 0 10px' }, `vs ${tw.opponent}: you win `, h('b', { style: 'color:var(--gold)' }, pct(tw.win_pct)), ` of sims. Projected ${f1(tw.total)} to ${f1(tw.opp_proj)}.`) : '',
    h('div', { class: 'tw' }, lu), h('p', { class: 'sub', style: 'margin:10px 0 0' }, 'Bench: ' + tw.bench.map(b => `${b.player} ${f1(b.pts)}`).join(', ')));
  line($('#trend'), H.runs.map(r => ({ y: r.champ_pct, at: r.at, w: r.week })), { label: q => `Wk ${q.w} · ${new Date(q.at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric' })}: ` });
  hbar($('#swing'), s.swing, { label: r => r.what.replace(' out for the playoffs', ''), value: r => r.change_pts, fmt: v => sgn(v) + ' pts', color: () => '#ff4d6d', tipText: r => `${r.what}: title odds ${sgn(r.change_pts)}` });
}

// ---------- trades ----------
let TK = 'realistic';
function renderTrades() {
  const hide = $('#hideblocked').checked;
  const rows = D.trades.filter(t => (TK === 'all' || t.kind === TK) && !(hide && t.blocked));
  const names = s => s.split(' + ');
  const side = (cls_, k, list) => h('div', { class: 'side ' + cls_ }, h('div', { class: 'k' }, k), ...names(list).map(n => h('div', { class: 'p' }, avatar(n, NFL(n), POS(n), VAL(n), 34), h('div', { class: 'pn' }, pl(n), h('div', {}, h('span', { class: 'pp2' }, POS(n) + ' · ' + (NFL(n) || '')), ' ', tierEl(VAL(n)))))));
  $('#tcards').replaceChildren(...(rows.length ? rows.slice(0, 12).map((r, i) => { const c = h('div', { class: 'tcard ' + (r.blocked ? 'blocked' : r.kind) },
      h('div', { class: 'who' }, h('span', {}, '🤝 ' + r.partner), h('span', { class: 'tag ' + (r.blocked ? 'no' : r.kind === 'realistic' ? 'real' : 'stretch') }, r.blocked ? 'BLOCKED' : r.kind === 'realistic' ? 'REALISTIC' : 'STRETCH')),
      h('div', { class: 'swap' }, side('give', 'YOU GIVE', r.give), h('div', { class: 'arrow' }, '➜'), side('get', 'YOU GET', r.get)),
      h('div', { class: 'foot' }, h('div', { class: 'boost', style: r.change_pts < 0 ? 'color:var(--red)' : '' }, sgn(r.change_pts) + '%', h('small', {}, `title odds → ${pct(r.title_pct)}`)),
        h('div', { class: 'meta' }, `Their lineup ${sgn(r.partner_weekly_gain)}/wk`, h('br'), `Your Wk${D.tester.po[1]}/${D.tester.po[2]}: ${f1(r.wk16)} / ${f1(r.wk17)}`),
        h('button', { class: 'btn small', onclick: () => { LAB.give = new Set(names(r.give)); LAB.partner = r.partner_id; LAB.get = new Set(names(r.get)); tab('lab'); } }, 'TEST IT'))); c.style.animationDelay = i * 40 + 'ms'; return c; })
    : [h('div', { class: 'box mut' }, 'No trades in this group right now.')]));
  table($('#ttable'), [
    { k: 'give', h: 'You give', l: 1, f: r => h('span', {}, ...names(r.give).flatMap((n, i) => [i ? ' + ' : '', pl(n)])) },
    { k: 'get', h: 'You get', l: 1, f: r => h('span', {}, ...names(r.get).flatMap((n, i) => [i ? ' + ' : '', pl(n)])) },
    { k: 'partner', h: 'From', l: 1 },
    { k: 'change_pts', h: 'Title +pts', f: r => sgn(r.change_pts), c: r => cls(r.change_pts) },
    { k: 'title_pct', h: 'Title %', f: r => pct(r.title_pct) },
    { k: 'partner_weekly_gain', h: 'Their pts/wk', t: "How much the other team's best lineup changes per week for the rest of the regular season", f: r => sgn(r.partner_weekly_gain), c: r => r.partner_weekly_gain >= -1 ? 'pos' : 'neg' },
    { k: 'value_ratio', h: 'Value ratio', t: 'Trade value you send ÷ value you get (over 1 = you overpay, so they should like it)', f: r => (+r.value_ratio).toFixed(2) },
    { k: 'wk16', h: 'Your Wk16', f: r => f1(r.wk16) }, { k: 'wk17', h: 'Your Wk17', f: r => f1(r.wk17) },
    { k: 'kind', h: 'Type', l: 1, f: r => h('span', { class: 'tag ' + (r.blocked ? 'no' : r.kind === 'realistic' ? 'real' : 'stretch') }, r.blocked ? 'Blocked' : r.kind === 'realistic' ? 'Realistic' : 'Stretch') }
  ], rows, { rowClass: r => r.blocked ? 'dim' : '', onRow: r => { LAB.give = new Set(names(r.give)); LAB.partner = r.partner_id; LAB.get = new Set(names(r.get)); tab('lab'); } });
}
seg('tkind', v => { TK = v; renderTrades(); }); $('#hideblocked').addEventListener('change', renderTrades);

// ---------- trade lab ----------
const LAB = { give: new Set(), get: new Set(), out: new Set(), partner: null, q: '' };
function renderLab() {
  const T = D.tester, me = T.me, val = p => (p.e[T.weeks.indexOf(T.po[1])] || 0) + (p.e[T.weeks.indexOf(T.po[2])] || 0);
  const pick = (box, list, set) => { box.replaceChildren(...list.sort((a, b) => val(b) - val(a)).map(p => { const cb = h('input', { type: 'checkbox' }); cb.checked = set.has(p.n); const lb = h('label', { class: cb.checked ? 'sel' : '' }, cb, h('span', {}, p.n + ' · ' + p.pos), tierEl(VAL(p.n)), h('span', { class: 'pp' }, f1(val(p)) + ' playoff pts')); cb.addEventListener('change', () => { cb.checked ? set.add(p.n) : set.delete(p.n); lb.classList.toggle('sel', cb.checked); }); return lb; })); };
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
  const tot = (pl_, wi) => { const a = pl_.map(p => ({ pos: p.pos, v: out.has(p.n) && W[wi] >= T.po[0] ? 0 : p.e[wi], n: p.n })).sort((x, y) => y.v - x.v); const u = new Set(); let s = 0; for (const [sl, k] of T.slots) { let c = k; for (const x of a) { if (!c) break; if (u.has(x.n)) continue; if ((T.elig[sl] || []).includes(x.pos)) { u.add(x.n); s += x.v; c--; } } } return s; };
  const E = {}; T.teams.forEach(t => E[t.id] = W.map((_, i) => tot(ros[t.id], i)));
  const wi = w => W.indexOf(w), rn = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
  const sc = (t, w) => E[t][wi(w)] + T.sd * rn(), g = (a, b, w) => sc(a, w) > sc(b, w) ? [a, b] : [b, a];
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
  return { ch: ch / N * 100, fi: fi / N * 100, po: po / N * 100, w16: E[me][wi(T.po[1])], w17: E[me][wi(T.po[2])], reg: avg(me), preg: partner ? avg(partner) : 0 };
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
        h('p', { class: 'sub', style: 'margin:10px 0 0' }, `Your Wk${D.tester.po[1]} lineup ${f1(before.w16)} → ${f1(after.w16)}, Wk${D.tester.po[2]} ${f1(before.w17)} → ${f1(after.w17)}. Regular season ${f1(before.reg)} → ${f1(after.reg)} a week.` +
          (trade && p ? ` Their lineup changes ${sgn(dp)} a week: ${dp >= 0 ? 'they should like it' : dp > -2 ? 'close to fair for them' : 'they probably say no'}.` : '')));
      countUp(afterEl, after.ch); if (d >= 1.5) setTimeout(confetti, 500);
    } finally { b.disabled = false; }
  }, 450);
});
$('#clear').addEventListener('click', () => { LAB.give.clear(); LAB.get.clear(); LAB.out.clear(); LAB.partner = null; LAB.q = ''; renderLab(); $('#result').replaceChildren(h('span', { class: 'mut' }, 'Pick players and hit Run.')); });

// ---------- waivers ----------
function renderWaivers() {
  table($('#wtable'), [
    { k: 'player', h: 'Add', l: 1, f: r => pl(r.player) }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'drop', h: 'Drop', l: 1 },
    { k: 'ros_gain', h: 'Lineup pts gained', f: r => sgn(r.ros_gain), c: r => cls(r.ros_gain) }, { k: 'this_week_gain', h: 'This week', f: r => sgn(r.this_week_gain), c: r => cls(r.this_week_gain) },
    { k: 'change_pts', h: 'Title +pts', f: r => r.change_pts == null ? '—' : sgn(r.change_pts) }, { k: 'ros_ppg', h: 'ROS ppg', f: r => f1(r.ros_ppg) },
    { k: 'playoff_pts', h: 'Playoff pts', f: r => f1(r.playoff_pts) }, { k: 'opp16', h: 'Semis', l: 1 }, { k: 'opp17', h: 'Final', l: 1 }, { k: 'own_pct', h: 'Rostered', f: r => r.own_pct + '%' }
  ], D.waivers);
}

// ---------- rankings ----------
const RK = { pos: 'ALL', sort: 'po', q: '', fa: false };
function renderRanks() {
  const q = RK.q.toLowerCase();
  const rows = D.rankings.filter(r => (RK.pos === 'ALL' || r.pos === RK.pos) && (!RK.fa || r.owner === 'Free agent') && (!q || r.player.toLowerCase().includes(q) || r.owner.toLowerCase().includes(q)));
  const sosC = v => v === 'Easy' ? 'pos' : v === 'Tough' ? 'neg' : '';
  table($('#rtable'), [
    { k: 'player', h: 'Player', l: 1, f: r => h('span', { style: 'display:inline-flex;gap:8px;align-items:center' }, avatar(r.player, r.nfl, r.pos, r.value, 26), pl(r.player)) }, { k: 'tier', h: 'Tier', l: 1, sv: r => r.value ?? -1, f: r => tierEl(r.value) }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'owner', h: 'Team', l: 1 },
    { k: 'value', h: 'Trade value', t: '0-100. Points over a replacement starter for the rest of the season, adjusted for usage trend', f: r => r.value == null ? '—' : r.value },
    { k: 'my_value', h: 'Value to you', t: '0-100. Same idea but weighted to the playoff weeks you will actually play', f: r => r.my_value == null ? '—' : r.my_value },
    { k: 'po_n', h: 'Playoff rk', sv: r => -r.po_n, f: r => r.pos + r.po_n }, { k: 'playoff_pts', h: 'Playoff pts', f: r => f1(r.playoff_pts) },
    { k: 'ros_n', h: 'ROS rk', sv: r => -r.ros_n, f: r => r.pos + r.ros_n }, { k: 'ros_ppg', h: 'ROS ppg', f: r => f1(r.ros_ppg) },
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
  const sm = D.summary, po = sm.playoff_weeks, hl = sm.healthy_lineup || {}, ex = sm.expected_lineup || {};
  const lt = (c, k, a, b) => h('div', { class: 'tile ' + c }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, f1(a)), h('div', { style: 'font-size:13px;font-weight:800' }, `${f1(b)} after injury odds`));
  $('#lusum').replaceChildren(lt('t1', `Wk ${po[1]} semis · full strength`, hl[po[1]], ex[po[1]]), lt('t3', `Wk ${po[2]} final · full strength`, hl[po[2]], ex[po[2]]));
  const wk = (r, k) => { const ch = r[k + '_chance']; return h('span', {}, f1(r[k + '_proj']) + (ch < 1 ? ` (${Math.round(ch * 100)}%)` : ''), h('span', { class: 'opp' }, `${r[k + '_opp']}${r[k + '_rank'] ? ' · ' + r[k + '_rank'] : ''}`)); };
  table($('#ptable'), [
    { k: 'player', h: 'Player', l: 1, f: r => h('span', {}, pl(r.player), ' ', tierEl(VAL(r.player))) }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'status', h: 'Status', l: 1 },
    { k: 'season_avg', h: 'Season avg', f: r => f1(r.season_avg) }, { k: 'recent', h: 'Last 4', f: r => f1(r.recent) }, { k: 'ros_ppg', h: 'ROS ppg', f: r => f1(r.ros_ppg) },
    { k: 'wk15', h: `Wk ${po[0]}`, f: r => wk(r, 'wk15') }, { k: 'wk16', h: `Wk ${po[1]} semis`, f: r => wk(r, 'wk16') }, { k: 'wk17', h: `Wk ${po[2]} final`, f: r => wk(r, 'wk17') },
    { k: 'playoff_expected', h: 'Expected semis+final', f: r => f1(r.playoff_expected) }
  ], D.my_players, { onRow: r => openCard(r.player) });
}

// ---------- league ----------
function renderLeague() {
  hbar($('#lchart'), D.league.filter(r => r.champ_pct > 0.05), { label: r => r.team, value: r => r.champ_pct, fmt: v => v.toFixed(1) + '%', color: r => r.me ? '#ffc83d' : '#4cc9ff' });
  table($('#ltable'), [
    { k: 'team', h: 'Team', l: 1 }, { k: 'record', h: 'Record' }, { k: 'pf', h: 'PF', f: r => f1(r.pf) }, { k: 'playoffs_pct', h: 'Playoffs', f: r => pct(r.playoffs_pct) }, { k: 'bye_pct', h: 'Bye', f: r => pct(r.bye_pct) },
    { k: 'champ_pct', h: 'Title', f: r => pct(r.champ_pct) }, { k: 'final_pct', h: 'Final', f: r => pct(r.final_pct) }, { k: 'win_this_week_pct', h: 'Win this week', f: r => pct(r.win_this_week_pct) },
    { k: 'wk16_lineup', h: 'Semis lineup', f: r => f1(r.wk16_lineup) }, { k: 'wk17_lineup', h: 'Final lineup', f: r => f1(r.wk17_lineup) }
  ], D.league, { rowClass: r => r.me ? 'me' : '' });
  table($('#btable'), [
    { k: 'team', h: 'Team', l: 1 }, { k: 'record', h: 'Record' }, { k: 'playoffs_pct', h: 'Playoff %', f: r => pct(r.playoffs_pct) }, { k: 'needs', h: 'Weakest spot', l: 1 },
    { k: 'weakest_starter_pts', h: "That starter's pts", f: r => f1(r.weakest_starter_pts) }, { k: 'has_extra', h: 'Bench pieces they could move', l: 1 }
  ], D.bubble, { rowClass: r => r.blocked ? 'dim' : '' });
  const mv = H.moves || [];
  if (!mv.length) $('#mtable').replaceChildren(h('tbody', {}, h('tr', {}, h('td', { class: 'mut' }, 'Moves show up here starting with the second update.'))));
  else table($('#mtable'), [{ k: 'at', h: 'When', l: 1, f: r => new Date(r.at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) }, { k: 'type', h: 'Type', l: 1, f: r => h('span', { class: 'tag ' + r.type }, r.type) }, { k: 'text', h: 'What', l: 1 }], mv.slice(0, 120));
}

// ---------- how it works / settings ----------
function renderHow() {
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
async function openCard(name) {
  if (!CARDS) CARDS = await fetch(BASE + 'players.json?v=' + D.summary.built).then(r => r.json());
  const c = CARDS[name]; if (!c) return;
  const ng = c.ngs || {}, pf = c.pfr || {}, rep = c.report, dep = c.depth, tc = c.team_ctx || {};
  const big = (k, v, sub) => h('div', { class: 'st hot' }, h('div', { class: 'k' }, k), h('div', { class: 'v', style: 'font-size:28px' }, v), sub ? h('div', { class: 'k', style: 'text-transform:none' }, sub) : '');
  const stat = (k, v, t) => v == null || v === '—' ? null : h('div', { class: 'st', title: t || '' }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, v));
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
  const po = D.summary.playoff_weeks;
  const sch = h('div', { class: 'sched' }, c.schedule.map((s, i) => { const wk = c.weekly[i] || {}; return h('div', { class: 'sw ' + (s.opp === 'BYE' ? 'bye' : sosC(s.rank)), style: po.includes(s.w) ? 'outline:3px solid #ffc83d;outline-offset:2px' : '', title: s.rank ? `${s.opp} defense ranks ${s.rank} of 32 vs ${c.pos} (32 = easiest)` : '' },
    h('div', { class: 'k' }, (s.w === po[1] ? 'SEMIS ' : s.w === po[2] ? 'FINAL ' : 'WK ') + s.w), h('div', {}, s.opp), h('div', { class: 'k' }, s.opp === 'BYE' ? '' : f1(wk.ev) + ' pts' + (wk.chance < 1 ? ` · ${Math.round(wk.chance * 100)}%` : ''))); }));
  const ngsRows = c.pos === 'QB' ? [['Completion % over expected', ng.cpoe], ['Aggressiveness', ng.aggr], ['Time to throw', ng.ttt], ['Air yards / throw', ng.adot], ['Passer rating', ng.rating]]
    : c.pos === 'RB' ? [['Rush yds over expected / carry', ng.ryoe_att], ['Stacked boxes faced', ng.stacked_box != null ? ng.stacked_box + '%' : null], ['Yds after contact / carry', pf.yac_att], ['Broken tackles', pf.broken_tackles]]
    : [['Separation (yds)', ng.sep], ['YAC over expected', ng.yacoe], ['Avg depth of target', ng.adot], ['Share of team air yards', ng.air_share != null ? ng.air_share + '%' : null], ['Drops', pf.drops], ['Broken tackles', pf.broken_tackles]];
  const log = h('div', { class: 'tw' }, h('table', {}, h('thead', {}, h('tr', {}, ['Wk', 'Opp', 'Pts', 'Usage said', c.pos === 'QB' ? 'Pass yds' : 'Targets', c.pos === 'QB' ? 'Attempts' : 'Catches', 'Yds', 'Carries', 'TD', 'Tgt share', 'Snaps', 'RZ'].map((x, i) => h('th', { class: i < 2 ? 'l' : '' }, x)))),
    h('tbody', {}, lg.slice().reverse().map(x => h('tr', {}, h('td', { class: 'l' }, x.w), h('td', { class: 'l' }, x.opp), h('td', {}, h('b', {}, f1(x.pts))), h('td', { class: 'mut' }, f1(x.xfp)), h('td', {}, c.pos === 'QB' ? x.passyd : x.tgt), h('td', {}, c.pos === 'QB' ? x.att : x.rec), h('td', {}, c.pos === 'QB' ? x.rushyd : (x.recyd || 0) + (x.rushyd || 0)), h('td', {}, x.car), h('td', {}, x.td), h('td', {}, x.tsh != null ? x.tsh + '%' : '—'), h('td', {}, x.snap != null ? x.snap + '%' : '—'), h('td', {}, (x.rz_tgt || 0) + (x.rz_car || 0)))))));
  const statusBits = [c.status && c.status !== 'ACTIVE' ? '🚑 ' + c.status.replace('_', ' ') : '', rep && rep.practice ? `Wk${rep.week} practice: ${rep.practice.replace(' in Practice', '').replace('Participation', '')}${rep.injury ? ' (' + rep.injury + ')' : ''}` : '', `${c.own}% rostered` + (c.own_chg ? ` (${sgn(c.own_chg)})` : '')].filter(Boolean);
  const m = h('div', { class: 'modal', onclick: e => { if (e.target === m) m.remove(); } }, h('div', { class: 'mbox ' + (tierOf(c.value) || '') },
    h('button', { class: 'x', onclick: () => m.remove() }, '×'),
    h('div', { style: 'display:flex;gap:18px;align-items:center;margin-bottom:14px' }, avatar(c.name, c.nfl, c.pos, c.value, 96),
      h('div', {}, h('h2', {}, c.name, ' ', tierEl(c.value)),
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
    c.outlook ? [h('h3', {}, '📰 ESPN outlook'), h('p', { style: 'margin:0;line-height:1.55' }, c.outlook)] : '',
    h('details', { style: 'margin-top:18px' }, h('summary', { style: 'cursor:pointer;font-family:var(--game);color:var(--gold);font-size:16px' }, '🤓 Nerd stats'),
      h('h3', {}, 'Usage'), h('div', { class: 'stats' }, [stat('Target share', c.tsh != null ? `${c.tsh}% → ${c.tsh3}%` : null, 'season → last 3'), stat('Carry share', c.csh ? `${c.csh}% → ${c.csh3}%` : null), stat('Snaps', c.snap != null ? `${c.snap}% → ${c.snap3}%` : null),
        stat('Touches+targets/g', c.opp_g != null ? `${c.opp_g} → ${c.opp_g3}` : null), stat('WOPR', c.wopr != null ? `${c.wopr} → ${c.wopr3}` : null), stat('Red zone targets', c.rz_tgt || null), stat('Red zone carries', c.rz_car || null), stat('Goal line carries', c.gl_car || null), stat('EPA', c.epa), stat('Boom %', c.boom != null ? c.boom + '%' : null), stat('Bust %', c.bust != null ? c.bust + '%' : null), stat('Luck / game', c.fpoe != null ? sgn(c.fpoe) : null, 'actual minus expected'), stat('ESPN ROS / game', c.espn_ros_pg != null ? f1(c.espn_ros_pg) : null)].filter(Boolean)),
      h('h3', {}, 'Next Gen Stats + PFR'), h('div', { class: 'stats' }, ngsRows.map(([k, v]) => stat(k, v)).filter(Boolean)),
      h('h3', {}, `${c.nfl} offense`), h('div', { class: 'stats' }, [stat('Plays / game', tc.plays_pg), stat('Pass rate vs expected', tc.proe != null ? sgn(tc.proe) + '%' : null), dep ? stat('Depth chart', dep.pos + dep.rank) : null].filter(Boolean)),
      h('h3', {}, 'Game log'), log)));
  document.body.append(m);
}
addEventListener('keydown', e => { if (e.key === 'Escape') $$('.modal').forEach(m => m.remove()); });

const RENDER = { home: renderHome, trades: renderTrades, lab: renderLab, waivers: renderWaivers, ranks: renderRanks, team: renderTeam, league: renderLeague, how: renderHow };
let rz; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { const t = $('nav.tabs button.on').dataset.tab; if (['home', 'trades', 'league'].includes(t)) RENDER[t](); }, 200); });

(async () => {
  const v = Date.now();
  const idx = await fetch('data/leagues.json?v=' + v).then(r => r.json()).catch(() => null);
  const leagues = idx ? idx.leagues : [];
  const want = new URLSearchParams(location.search).get('league') || (() => { try { return localStorage.getItem('league'); } catch (e) { return null; } })();
  LID = (leagues.find(l => l.id === want) || leagues[0] || {}).id;
  BASE = LID ? `data/${LID}/` : 'data/';
  const sel = $('#league');
  sel.replaceChildren(...leagues.map(l => h('option', { value: l.id }, `${l.name} · ${f1(l.champ_pct)}%`)));
  sel.value = LID || ''; sel.parentElement.hidden = leagues.length < 2;
  sel.addEventListener('change', () => { try { localStorage.setItem('league', sel.value); } catch (e) {} const u = new URL(location); u.searchParams.set('league', sel.value); location.href = u.toString(); });
  if (idx && idx.status && idx.status.espn_ok === false) $('#banner').replaceChildren(h('div', { class: 'banner' }, '⚠️ ' + idx.status.message));
  [D, H] = await Promise.all([fetch(BASE + 'latest.json?v=' + v).then(r => r.json()), fetch(BASE + 'history.json?v=' + v).then(r => r.json()).catch(() => ({ runs: [], moves: [] }))]);
  const at = new Date(D.summary.at);
  $('#upd').textContent = `${D.summary.my_team} · Week ${D.summary.week} · updated ${at.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}`;
  document.title = `Oxtail Lab · ${D.summary.league}`;
  const start = (location.hash || '#home').slice(1);
  tab(RENDER[start] ? start : 'home');
  setTimeout(() => fetch(BASE + 'players.json?v=' + D.summary.built).then(r => r.json()).then(j => { CARDS = j; }).catch(() => {}), 600);
})();
