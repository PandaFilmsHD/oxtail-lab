// Oxtail Lab front end. Reads data/latest.json, data/history.json, data/players.json (built by scripts/analyze.py).
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs || {})) { if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else if (v != null) e.setAttribute(k, v); } for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : String(c)); return e; };
const f1 = v => v == null || v === '' || Number.isNaN(+v) ? '—' : (+v).toFixed(1);
const pct = v => v == null ? '—' : (+v).toFixed(1) + '%';
const sgn = v => v == null ? '—' : (v > 0 ? '+' : '') + (+v).toFixed(1);
const cls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
let D, H, CARDS = null;
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
function tab(name) { $$('nav.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name)); $$('section').forEach(s => s.hidden = s.id !== name); history.replaceState(null, '', '#' + name); if (RENDER[name]) RENDER[name](); }
$$('nav.tabs button').forEach(b => b.addEventListener('click', () => tab(b.dataset.tab)));
function seg(id, onChange) { $$('#' + id + ' button').forEach(b => b.addEventListener('click', () => { $$('#' + id + ' button').forEach(x => x.classList.toggle('on', x === b)); onChange(b.dataset.v); })); }

// ---------- home ----------
function renderHome() {
  const s = D.summary, k = s.kpis, prev = H.runs.length > 1 ? H.runs[H.runs.length - 2] : null;
  const card = (label, v, pv) => h('div', { class: 'card' }, h('div', { class: 'k' }, label), h('div', { class: 'v' }, pct(v), pv != null && Math.abs(v - pv) >= 0.1 ? h('span', { class: 'd ' + cls(v - pv) }, sgn(v - pv)) : null));
  $('#kpis').replaceChildren(card('Win the title', k.ch, prev && prev.champ_pct), card('Make the final', k.fi, prev && prev.final_pct), card('Make playoffs', k.po, prev && prev.playoffs_pct), card('First round bye', k.bye, prev && prev.bye_pct), card('1 seed', k.s1, prev && prev.seed1_pct));
  $('#take').replaceChildren(...s.take.map(t => h('li', {}, t)));
  const tw = s.this_week; $('#tw-h').textContent = `Week ${tw.week} best lineup`;
  const lu = h('table', { class: 'lu' }, h('tbody', {}, tw.lineup.map(x => h('tr', {}, h('td', { class: 'mut' }, x.slot), h('td', { class: 'l' }, pl(x.player)), h('td', {}, f1(x.pts))))));
  $('#thisweek').replaceChildren(
    tw.opponent ? h('p', { style: 'margin:0 0 8px' }, `vs ${tw.opponent}: you win ${pct(tw.win_pct)} of sims. Projected ${f1(tw.total)} to ${f1(tw.opp_proj)}.`) : '',
    lu, h('p', { class: 'sub', style: 'margin:8px 0 0' }, 'Bench: ' + tw.bench.map(b => `${b.player} ${f1(b.pts)}`).join(', ')));
  line($('#trend'), H.runs.map(r => ({ y: r.champ_pct, at: r.at, w: r.week })), { label: q => `Wk ${q.w} · ${new Date(q.at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric' })}: ` });
  hbar($('#swing'), s.swing, { label: r => r.what.replace(' out for the playoffs', ''), value: r => r.change_pts, fmt: v => sgn(v) + ' pts', color: () => '#f85149', tipText: r => `${r.what}: title odds ${sgn(r.change_pts)}` });
}

// ---------- trades ----------
let TK = 'realistic';
function renderTrades() {
  const hide = $('#hideblocked').checked;
  const rows = D.trades.filter(t => (TK === 'all' || t.kind === TK) && !(hide && t.blocked));
  hbar($('#tchart'), rows.slice(0, 15), { label: r => `${r.give} → ${r.get}`, value: r => r.change_pts, fmt: v => sgn(v), color: r => r.blocked ? '#4a5260' : r.kind === 'realistic' ? '#3fb950' : '#f0b429', tipText: r => `${r.give} → ${r.get} (${r.partner}): title ${pct(r.title_pct)}, ${sgn(r.change_pts)} pts. Their lineup ${sgn(r.partner_weekly_gain)}/wk.` });
  const names = s => s.split(' + ');
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
  const pick = (box, list, set) => { box.replaceChildren(...list.sort((a, b) => val(b) - val(a)).map(p => { const cb = h('input', { type: 'checkbox' }); cb.checked = set.has(p.n); cb.addEventListener('change', () => cb.checked ? set.add(p.n) : set.delete(p.n)); return h('label', {}, cb, h('span', {}, p.n + ' · ' + p.pos), h('span', { class: 'pp' }, f1(val(p)) + ' playoff pts')); })); };
  pick($('#give'), T.players.filter(p => p.t === me && p.pos !== 'K'), LAB.give);
  const sel = $('#partner'); sel.replaceChildren(h('option', { value: '' }, 'Pick a team'), ...T.teams.filter(t => t.id !== me).map(t => h('option', { value: t.id }, t.name)), h('option', { value: '0' }, 'Free agents (add/drop)'));
  sel.value = LAB.partner == null ? '' : String(LAB.partner);
  sel.onchange = () => { LAB.partner = sel.value === '' ? null : +sel.value; LAB.get.clear(); renderLab(); };
  if (LAB.partner == null) $('#get').replaceChildren(h('span', { class: 'mut' }, 'Pick a team first'));
  else pick($('#get'), T.players.filter(p => p.t === LAB.partner && p.pos !== 'K'), LAB.get);
  const q = $('#outq'); q.value = LAB.q; q.oninput = () => { LAB.q = q.value; drawOut(); };
  const drawOut = () => { const s = LAB.q.toLowerCase(); const list = T.players.filter(p => p.t && (LAB.out.has(p.n) || (s && p.n.toLowerCase().includes(s)) || (!s && p.t === me))).slice(0, 40); $('#outlist').replaceChildren(...list.map(p => { const cb = h('input', { type: 'checkbox' }); cb.checked = LAB.out.has(p.n); cb.addEventListener('change', () => cb.checked ? LAB.out.add(p.n) : LAB.out.delete(p.n)); return h('label', {}, cb, h('span', {}, `${p.n} · ${p.pos}`), h('span', { class: 'pp' }, (T.teams.find(t => t.id === p.t) || {}).name || '')); })); };
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
  const b = $('#run'); b.disabled = true; $('#result').replaceChildren(h('span', { class: 'mut' }, 'Running…'));
  setTimeout(() => {
    try {
      const p = LAB.partner, trade = p != null && (LAB.give.size || LAB.get.size);
      const before = labSim(new Set(), new Set(), p, new Set(), 30000), after = labSim(trade ? LAB.give : new Set(), trade ? LAB.get : new Set(), p, LAB.out, 30000);
      const d = after.ch - before.ch, dp = after.preg - before.preg;
      const big = (k, v, c) => h('div', {}, h('div', { class: 'mut' }, k), h('div', { class: 'big ' + (c || '') }, v));
      const what = [trade ? `${[...LAB.give].join(' + ') || 'nothing'} for ${[...LAB.get].join(' + ') || 'nothing'}` : '', LAB.out.size ? `${[...LAB.out].join(', ')} out for the playoffs` : ''].filter(Boolean).join(' · ') || 'No changes picked';
      $('#result').replaceChildren(h('div', { class: 'mut', style: 'margin-bottom:8px' }, what),
        h('div', { class: 'row' }, big('Title now', pct(before.ch)), big('Title after', pct(after.ch)), big('Change', sgn(d) + ' pts', cls(d)), big('Make final', pct(after.fi))),
        h('p', { class: 'sub', style: 'margin:10px 0 0' }, `Your Wk${D.tester.po[1]} lineup ${f1(before.w16)} → ${f1(after.w16)}, Wk${D.tester.po[2]} ${f1(before.w17)} → ${f1(after.w17)}. Regular season ${f1(before.reg)} → ${f1(after.reg)} a week.` +
          (trade && p ? ` Their lineup changes ${sgn(dp)} a week: ${dp >= 0 ? 'they should like it' : dp > -2 ? 'close to fair for them' : 'they probably say no'}.` : '')));
    } finally { b.disabled = false; }
  }, 30);
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
    { k: 'player', h: 'Player', l: 1, f: r => pl(r.player) }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'owner', h: 'Team', l: 1 },
    { k: 'value', h: 'Trade value', t: '0-100. Points over a replacement starter for the rest of the season, adjusted for usage trend', f: r => r.value == null ? '—' : r.value },
    { k: 'my_value', h: 'Value to you', t: '0-100. Same idea but weighted to the playoff weeks you will actually play', f: r => r.my_value == null ? '—' : r.my_value },
    { k: 'po_n', h: 'Playoff rk', sv: r => -r.po_n, f: r => r.pos + r.po_n }, { k: 'playoff_pts', h: 'Playoff pts', f: r => f1(r.playoff_pts) },
    { k: 'ros_n', h: 'ROS rk', sv: r => -r.ros_n, f: r => r.pos + r.ros_n }, { k: 'ros_ppg', h: 'ROS ppg', f: r => f1(r.ros_ppg) },
    { k: 'xfp', h: 'xFP/g', t: 'Expected fantasy points per game from usage (this league\'s scoring)', f: r => f1(r.xfp) },
    { k: 'fpoe', h: 'Luck', t: 'Actual minus expected points per game. Big plus = running hot, may cool off. Big minus = due to bounce back.', f: r => sgn(r.fpoe), c: r => r.fpoe > 2 ? 'neg' : r.fpoe < -2 ? 'pos' : '' },
    { k: 'trend', h: 'Usage trend', t: 'Last 3 games touches+targets vs season average', f: r => r.trend == null ? '—' : (r.trend > 1 ? '▲ ' : r.trend < 1 ? '▼ ' : '') + Math.round((r.trend - 1) * 100) + '%', c: r => r.trend > 1.08 ? 'pos' : r.trend < 0.92 ? 'neg' : '' },
    { k: 'sos_ros', h: 'Sched ROS', l: 1, c: r => sosC(r.sos_ros) }, { k: 'sos_po', h: 'Sched playoffs', l: 1, c: r => sosC(r.sos_po) },
    { k: 'opp16', h: 'Semis', l: 1 }, { k: 'opp17', h: 'Final', l: 1 }
  ], rows, { rowClass: r => r.owner === 'You' ? 'me' : '', sort: { k: RK.sort === 'po' ? 'playoff_pts' : 'ros_ppg', d: -1 }, onRow: r => openCard(r.player) });
}
seg('rpos', v => { RK.pos = v; renderRanks(); }); seg('rsort', v => { RK.sort = v; renderRanks(); });
$('#rq').addEventListener('input', e => { RK.q = e.target.value; renderRanks(); }); $('#faonly').addEventListener('change', e => { RK.fa = e.target.checked; renderRanks(); });

// ---------- my team ----------
function renderTeam() {
  const wk = (r, k) => { const ch = r[k + '_chance']; return h('span', {}, f1(r[k + '_proj']) + (ch < 1 ? ` (${Math.round(ch * 100)}%)` : ''), h('span', { class: 'opp' }, `${r[k + '_opp']}${r[k + '_rank'] ? ' · ' + r[k + '_rank'] : ''}`)); };
  const po = D.summary.playoff_weeks;
  table($('#ptable'), [
    { k: 'player', h: 'Player', l: 1, f: r => pl(r.player) }, { k: 'pos', h: 'Pos', l: 1 }, { k: 'status', h: 'Status', l: 1 },
    { k: 'season_avg', h: 'Season avg', f: r => f1(r.season_avg) }, { k: 'recent', h: 'Last 4', f: r => f1(r.recent) }, { k: 'ros_ppg', h: 'ROS ppg', f: r => f1(r.ros_ppg) },
    { k: 'wk15', h: `Wk ${po[0]}`, f: r => wk(r, 'wk15') }, { k: 'wk16', h: `Wk ${po[1]} semis`, f: r => wk(r, 'wk16') }, { k: 'wk17', h: `Wk ${po[2]} final`, f: r => wk(r, 'wk17') },
    { k: 'playoff_expected', h: 'Expected semis+final', f: r => f1(r.playoff_expected) }
  ], D.my_players, { onRow: r => openCard(r.player) });
}

// ---------- league ----------
function renderLeague() {
  hbar($('#lchart'), D.league.filter(r => r.champ_pct > 0.05), { label: r => r.team, value: r => r.champ_pct, fmt: v => v.toFixed(1) + '%', color: r => r.me ? '#f0b429' : '#58a6ff' });
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
async function openCard(name) {
  if (!CARDS) CARDS = await fetch('data/players.json?v=' + D.summary.built).then(r => r.json());
  const c = CARDS[name]; if (!c) return;
  const stat = (k, v, t) => h('div', { class: 'st', title: t || '' }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, v == null ? '—' : v));
  const sosC = r => r == null ? '' : r >= 22 ? 'easy' : r <= 11 ? 'hard' : 'mid';
  const sch = h('div', { class: 'sched' }, c.schedule.map(s => h('div', { class: 'sw ' + (s.opp === 'BYE' ? 'bye' : sosC(s.rank)), title: s.rank ? `Defense rank vs ${c.pos}: ${s.rank} (32 = easiest)` : '' }, h('div', { class: 'k' }, 'Wk ' + s.w), h('div', {}, s.opp), h('div', { class: 'k' }, s.rank ? '#' + s.rank : ''))));
  const ng = c.ngs || {}, pf = c.pfr || {}, rep = c.report, dep = c.depth, tc = c.team_ctx || {};
  const ngsTxt = c.pos === 'QB' ? [['CPOE', ng.cpoe], ['Aggressiveness', ng.aggr], ['Time to throw', ng.ttt], ['Air yards/att', ng.adot], ['Passer rating', ng.rating]]
    : c.pos === 'RB' ? [['Rush yds over exp/att', ng.ryoe_att], ['Stacked box %', ng.stacked_box], ['Yds after contact/att', pf.yac_att], ['Broken tackles', pf.broken_tackles]]
    : [['Separation (yds)', ng.sep], ['Cushion', ng.cushion], ['YAC over exp', ng.yacoe], ['Avg depth of target', ng.adot], ['Share of team air yards', ng.air_share != null ? ng.air_share + '%' : null], ['Drops', pf.drops], ['Broken tackles', pf.broken_tackles]];
  const log = h('div', { class: 'tw' }, h('table', {}, h('thead', {}, h('tr', {}, ['Wk', 'Opp', 'Pts', 'xFP', c.pos === 'QB' ? 'Pass yds' : 'Tgt', c.pos === 'QB' ? 'Att' : 'Rec', 'Yds', 'Car', 'TD', 'Tgt %', 'Snap %', 'RZ'].map((x, i) => h('th', { class: i < 2 ? 'l' : '' }, x)))),
    h('tbody', {}, (c.log || []).slice().reverse().map(x => h('tr', {}, h('td', { class: 'l' }, x.w), h('td', { class: 'l' }, x.opp), h('td', {}, f1(x.pts)), h('td', { class: 'mut' }, f1(x.xfp)), h('td', {}, c.pos === 'QB' ? x.passyd : x.tgt), h('td', {}, c.pos === 'QB' ? x.att : x.rec), h('td', {}, c.pos === 'QB' ? x.rushyd : (x.recyd || 0) + (x.rushyd || 0)), h('td', {}, x.car), h('td', {}, x.td), h('td', {}, x.tsh != null ? x.tsh + '%' : '—'), h('td', {}, x.snap != null ? x.snap + '%' : '—'), h('td', {}, (x.rz_tgt || 0) + (x.rz_car || 0)))))));
  const weekly = h('div', { class: 'sched' }, c.weekly.map(x => h('div', { class: 'sw', title: x.chance < 1 ? `${f1(x.if_active)} if he plays, ${Math.round(x.chance * 100)}% chance` : '' }, h('div', { class: 'k' }, 'Wk ' + x.w), h('div', {}, f1(x.ev)), h('div', { class: 'k' }, x.chance < 1 ? Math.round(x.chance * 100) + '%' : ''))));
  const m = h('div', { class: 'modal', onclick: e => { if (e.target === m) m.remove(); } }, h('div', { class: 'mbox' },
    h('button', { class: 'x', onclick: () => m.remove() }, '×'),
    h('h2', { style: 'margin:0' }, c.name, h('span', { class: 'mut', style: 'font-weight:400;font-size:14px' }, `  ${c.pos} · ${c.nfl} · ${c.owner}`)),
    h('p', { class: 'sub', style: 'margin:4px 0 12px' }, [c.status && c.status !== 'ACTIVE' ? 'ESPN: ' + c.status : '', rep ? `Wk${rep.week} report: ${rep.practice || ''}${rep.status ? ', ' + rep.status : ''}${rep.injury ? ' (' + rep.injury + ')' : ''}` : '', dep ? `Depth chart: ${dep.pos}${dep.rank}` : '', `Rostered ${c.own}%` + (c.own_chg ? ` (${sgn(c.own_chg)} this week)` : '')].filter(Boolean).join(' · ')),
    h('div', { class: 'stats' }, stat('Trade value', c.value, '0-100 league value'), stat('Value to you', c.my_value), stat('ROS ppg', f1(c.ros_ppg)), stat('Playoff pts', f1(c.playoff_pts)), stat('Season avg', f1(c.avg)), stat('Last 4', f1(c.recent)),
      stat('xFP/g', f1(c.xfp), 'Expected fantasy points per game from his usage, in your scoring'), stat('Luck', sgn(c.fpoe), 'Actual minus expected per game'), stat('Floor', f1(c.floor), '25th percentile game'), stat('Ceiling', f1(c.ceiling), '75th percentile game'),
      stat('Boom %', c.boom != null ? c.boom + '%' : null), stat('Bust %', c.bust != null ? c.bust + '%' : null), stat('ESPN ROS/g', f1(c.espn_ros_pg))),
    h('h3', {}, 'Usage'),
    h('div', { class: 'stats' }, stat('Target share', c.tsh != null ? `${c.tsh}% (L3 ${c.tsh3}%)` : null), stat('Carry share', c.csh != null ? `${c.csh}% (L3 ${c.csh3}%)` : null), stat('WOPR', c.wopr != null ? `${c.wopr} (L3 ${c.wopr3})` : null),
      stat('Snap %', c.snap != null ? `${c.snap}% (L3 ${c.snap3}%)` : null), stat('Touches+tgts/g', c.opp_g != null ? `${c.opp_g} (L3 ${c.opp_g3})` : null), stat('Usage trend', c.trend != null ? sgn((c.trend - 1) * 100) + '%' : null),
      stat('Red zone tgts', c.rz_tgt), stat('Inside-10 tgts', c.i10_tgt), stat('Red zone carries', c.rz_car), stat('Goal line carries', c.gl_car), stat('EPA (season)', c.epa)),
    h('h3', {}, 'Advanced (Next Gen Stats + PFR)'), h('div', { class: 'stats' }, ngsTxt.map(([k, v]) => stat(k, v))),
    h('h3', {}, `Team: ${c.nfl}`), h('div', { class: 'stats' }, stat('Offensive plays/game', tc.plays_pg), stat('Pass rate vs expected', tc.proe != null ? sgn(tc.proe) + '%' : null)),
    h('h3', {}, `Schedule (defense rank vs ${c.pos}, green = easy) · ROS ${c.sos_ros_label || '—'}, playoffs ${c.sos_po_label || '—'}`), sch,
    h('h3', {}, 'Expected points each week (injury odds included)'), weekly,
    c.outlook ? [h('h3', {}, 'ESPN outlook'), h('p', { style: 'margin:0', class: 'sub' }, c.outlook)] : '',
    h('h3', {}, 'Game log'), log));
  document.body.append(m);
}
addEventListener('keydown', e => { if (e.key === 'Escape') $$('.modal').forEach(m => m.remove()); });

const RENDER = { home: renderHome, trades: renderTrades, lab: renderLab, waivers: renderWaivers, ranks: renderRanks, team: renderTeam, league: renderLeague, how: renderHow };
let rz; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { const t = $('nav.tabs button.on').dataset.tab; if (['home', 'trades', 'league'].includes(t)) RENDER[t](); }, 200); });

(async () => {
  const v = Date.now();
  [D, H] = await Promise.all([fetch('data/latest.json?v=' + v).then(r => r.json()), fetch('data/history.json?v=' + v).then(r => r.json()).catch(() => ({ runs: [], moves: [] }))]);
  const at = new Date(D.summary.at);
  $('#upd').textContent = `${D.summary.league} · Week ${D.summary.week} · ESPN data from ${at.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`;
  const start = (location.hash || '#home').slice(1);
  tab(RENDER[start] ? start : 'home');
  setTimeout(() => fetch('data/players.json?v=' + D.summary.built).then(r => r.json()).then(j => CARDS = j).catch(() => {}), 800);
})();
