// Ask Coach: a chatbot that reads this league's data. Uses YOUR Anthropic API key, stored only in this browser.
(() => {
  const MODEL = 'claude-sonnet-5-5';
  const GEMINI_MODELS = ['gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.5-flash-lite', 'gemini-flash-lite-latest'];   // if one is busy, try the next   // free tier; tries the newest Flash first
  const store = { get: k => { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } }, set: (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} } };
  const provider = () => store.get('oxtail_gemini_key') ? 'gemini' : store.get('oxtail_anthropic_key') ? 'anthropic' : store.get('oxtail_local') ? 'local' : '';
  const getKey = () => provider() === 'local' ? 'local' : store.get(provider() === 'gemini' ? 'oxtail_gemini_key' : 'oxtail_anthropic_key');
  const setKey = k => { store.set('oxtail_gemini_key', ''); store.set('oxtail_anthropic_key', ''); store.set('oxtail_local', ''); if (k === 'local') { store.set('oxtail_local', '1'); return; } if (k) store.set(k.startsWith('sk-ant') ? 'oxtail_anthropic_key' : 'oxtail_gemini_key', k); };
  let history = [], busy = false;

  // ---------- build the data the bot reads ----------
  function cardLine(c) {
    if (!c) return '';
    const sch = (c.schedule || []).map(s => `W${s.w} ${s.opp}${s.rank ? '(' + s.rank + ')' : ''}`).join(', ');
    const bits = [
      `${c.name} | ${c.pos} ${c.nfl} | team: ${c.mine ? 'MINE' : c.owner} | tier ${TIERS[c.name] || 'n/a'} (rank at position when healthy) | trade value ${c.value ?? '-'} /100, value to me ${c.my_value ?? '-'} /100 | verdict ${c.verdict}: ${c.verdict_line}`,
      `  pts: season avg ${c.avg}, last 4 ${c.recent ?? '-'}, usage-expected ${c.xfp ?? '-'}/g, luck ${c.fpoe ?? '-'}/g, ROS ${c.ros_ppg}/game, playoff (semis+final) ${c.playoff_pts}, floor ${c.floor ?? '-'}, ceiling ${c.ceiling ?? '-'}, boom ${c.boom ?? '-'}%, bust ${c.bust ?? '-'}%`,
      `  usage: tgt share ${c.tsh ?? '-'}%→${c.tsh3 ?? '-'}% (last 3), carry share ${c.csh ?? '-'}%→${c.csh3 ?? '-'}%, snaps ${c.snap ?? '-'}%→${c.snap3 ?? '-'}%, trend ${c.trend ?? '-'}, red zone tgts ${c.rz_tgt ?? 0}, rz carries ${c.rz_car ?? 0}`,
      `  status ${c.status}${c.report ? `, Wk${c.report.week} practice: ${c.report.practice || ''} ${c.report.injury || ''}` : ''} | schedule (def rank vs pos, 32=easiest): ${sch} | ROS sched ${c.sos_ros_label || '-'}, playoff sched ${c.sos_po_label || '-'}`,
      c.reasons && c.reasons.length ? '  notes: ' + c.reasons.map(r => r.text).join(' ') : ''
    ];
    return bits.filter(Boolean).join('\n');
  }
  function context() {
    const s = D.summary, C = CARDS || {}, lines = [];
    const st = s.settings || {}, sc = s.scoring || {};
    lines.push(`LEAGUE: ${s.league} (ESPN). My team: ${s.my_team}. Current week ${s.week}. Regular season ends week ${s.reg_end}. Playoff weeks ${s.playoff_weeks.join(', ')} (${st.playoff_teams} teams make it). Data pulled ${s.at}.`);
    lines.push(`SCORING: ${sc['53']} per reception, ${sc['4']} per passing TD, lineup ${Object.entries(st.slots || {}).map(([k, v]) => v + ' ' + k).join(', ')}. ${st.teams} teams. Tiebreak ${st.tiebreak || '-'}.`);
    lines.push(`MY ODDS: title ${s.kpis.ch}%, final ${s.kpis.fi}%, playoffs ${s.kpis.po}%, bye ${s.kpis.bye}%, 1 seed ${s.kpis.s1}%. Playoff lineup full strength ${JSON.stringify(s.healthy_lineup)}, after injury odds ${JSON.stringify(s.expected_lineup)}.`);
    lines.push(`THIS WEEK: best lineup ${s.this_week.lineup.map(x => `${x.slot} ${x.player} ${x.pts}`).join('; ')}. Bench: ${s.this_week.bench.map(b => b.player + ' ' + b.pts).join(', ')}.${s.this_week.opponent ? ` Opponent ${s.this_week.opponent}, my win chance ${s.this_week.win_pct}%.` : ''}`);
    lines.push(`BIGGEST RISKS (title odds change if he misses the playoffs): ${s.swing.map(x => x.what.replace(' out for the playoffs', '') + ' ' + x.change_pts).join(', ')}`);
    lines.push(`AUTO TAKE: ${s.take.join(' ')}`);
    lines.push('\nSTANDINGS + SIM ODDS (team | record | PF | playoffs% | bye% | title% | expected semis lineup):');
    D.league.forEach(r => lines.push(`${r.team}${r.me ? ' (ME)' : ''} | ${r.record} | ${r.pf} | ${r.playoffs_pct} | ${r.bye_pct} | ${r.champ_pct} | ${r.wk16_lineup}`));
    lines.push('\nTRADES FOUND BY THE SIM (give -> get | partner | kind | my title odds change in pts | title% after | their lineup change per week | value ratio, >1 means I send more market value), then why it works for me, why they might say yes, and a message I can send them:');
    D.trades.forEach(t => { lines.push(`${t.give} -> ${t.get} | ${t.partner} | ${t.kind} | ${t.change_pts > 0 ? '+' : ''}${t.change_pts} | ${t.title_pct}% | ${t.partner_weekly_gain} | ${t.value_ratio}`); if (t.why_me) lines.push('   why me: ' + t.why_me.join(' ') + '\n   why them: ' + t.why_them.join(' ') + '\n   message: ' + t.pitch); });
    lines.push('\nWAIVER IDEAS (add | drop | weighted lineup pts gained | title odds change | ROS/g | playoff pts | rostered%):');
    D.waivers.forEach(w => lines.push(`${w.player} ${w.pos} | drop ${w.drop} | ${w.ros_gain} | ${w.change_pts ?? '-'} | ${w.ros_ppg} | ${w.playoff_pts} | ${w.own_pct}%`));
    lines.push('\nTEAMS TO WORK (team | record | playoff% | weakest starting spot | spare pieces):');
    D.bubble.forEach(b => lines.push(`${b.team} | ${b.record} | ${b.playoffs_pct}% | ${b.needs} (${b.weakest_starter_pts} pts) | ${b.has_extra}`));
    if (D.schedule) { lines.push('\nWEEK BY WEEK (my best lineup, opponent, win chance):'); D.schedule.forEach(x => lines.push(`${x.label}: ${x.total}${x.opponent ? ` vs ${x.opponent} ${x.opp_total}, win ${x.win_pct}%` : ''} | ` + x.lineup.map(p => `${p.slot} ${p.player} ${p.pts} vs ${p.opp}`).join(', ') + (x.byes.length ? ' | on bye: ' + x.byes.join(', ') : ''))); }
    const want = new Set();
    D.my_players.forEach(p => want.add(p.player));
    D.trades.forEach(t => (t.give + ' + ' + t.get).split(' + ').forEach(n => want.add(n)));
    D.waivers.forEach(w => want.add(w.player));
    [...D.rankings].sort((a, b) => (b.value || 0) - (a.value || 0)).slice(0, 45).forEach(r => want.add(r.player));
    lines.push('\nPLAYER DETAIL (my roster, everyone in a trade or waiver idea, and the top players by value):');
    want.forEach(n => { if (C[n]) lines.push(cardLine(C[n])); });
    lines.push('\nRANKINGS (player | pos | team owner | value | ROS/g | playoff pts | playoff rank | ROS rank | luck | usage trend | sched ROS/playoffs):');
    D.rankings.forEach(r => lines.push(`${r.player} | ${r.pos} | ${r.owner} | ${r.value ?? '-'} | ${r.ros_ppg} | ${r.playoff_pts} | ${r.pos}${r.po_n} | ${r.pos}${r.ros_n} | ${r.fpoe ?? '-'} | ${r.trend ?? '-'} | ${r.sos_ros || '-'}/${r.sos_po || '-'}`));
    if (H && H.moves && H.moves.length) { lines.push('\nRECENT LEAGUE MOVES:'); H.moves.slice(0, 40).forEach(m => lines.push(`${m.at} ${m.type}: ${m.text}`)); }
    return lines.join('\n');
  }
  const METHOD = `HOW THE SITE'S NUMBERS WORK (explain these when asked):
- Player value per week = blend of season average (30%), last 4 games (25%), expected points from usage (25%, rescored to this league's scoring) and ESPN's rest-of-season projection (20%), nudged up to ±10% by usage trend, then by that week's opponent using schedule-adjusted fantasy points allowed (like 4for4 aFPA / FantasyPros SOS): how many more or fewer points each defense gives a position compared with what those same players normally score, capped at ±20% and shrunk toward average for small samples. Defense rank shown is 1 = toughest, 32 = easiest. Injury guesses give each player a chance to play each week.
- Sims: every team starts its best lineup each week, weekly scores swing about 24 points, the rest of the regular season and the playoff bracket are played out 200,000 times. Title % = share of those seasons won.
- Trade search: every 1-for-1, 2-for-1 and 1-for-2 with every team. Scored on my lineup points for the rest of the regular season plus my playoff weeks (counted double), then the best ones are simmed 30,000 times each. "Change" = title odds with the trade minus title odds now.
- Market/trade value (0-100) = how other managers see a player: season ppg + last 4 games over a replacement-level starter at his position, times how much of the season he'll play. "Value to me" weights the playoff weeks I'll actually play.
- REALISTIC trade = the value I send is at least equal to what I get (2-player sides get a 25% discount), their weekly lineup doesn't drop more than 1 point (unless they're eliminated), and if I get 2 players for 1, my player has to be clearly the best piece. STRETCH = helps me more but they'd probably say no.
- Luck = actual minus usage-expected points per game; big plus means likely to cool off. Floor/ceiling = 25th/75th percentile games.`;
  const SYS = `You are "Coach", the assistant inside Oxtail Lab, a fantasy football analysis site for one manager (the user). Talk like a sharp friend who knows fantasy football: casual, direct, no fluff, no dashes, short paragraphs or tight bullets. Use specific numbers from the data. Answer the question first, then the why.
MY ROSTER RULES (always follow): I never roster a backup TE, a backup D/ST or a backup K. Never suggest adding or trading for one unless it directly replaces the one I have. Only use the data below for facts about players, odds, trades and the league. If something isn't in the data (like news from today or a player not listed), say so plainly instead of guessing. When explaining a trade, cover: what it does for my lineup in the regular season and in my playoff weeks, why the other team might say yes or no (their lineup change, their playoff odds, value ratio), and the risks. You can use general fantasy knowledge for reasoning, but numbers come from the data.

${METHOD}`;

  // ---------- UI ----------
  const $c = s => document.querySelector(s);
  const el = (t, a = {}, ...k) => h(t, a, ...k);
  const fab = el('button', { class: 'chatfab', title: 'Ask Coach', onclick: () => toggle(true) }, '🤖', el('span', {}, 'Ask Coach'));
  const panel = el('div', { class: 'chatp', hidden: '' });
  document.body.append(fab, panel);
  const md = t => { const d = el('div'); t.split(/\n{2,}/).forEach(par => { const lines = par.split('\n'); if (lines.every(l => /^\s*([-*•]|\d+\.)\s+/.test(l))) { const ul = el('ul'); lines.forEach(l => { const li = el('li'); fmt(li, l.replace(/^\s*([-*•]|\d+\.)\s+/, '')); ul.append(li); }); d.append(ul); } else { const p = el('p'); fmt(p, par); d.append(p); } }); return d; };
  const fmt = (node, txt) => txt.split(/(\*\*[^*]+\*\*)/).forEach(part => { if (/^\*\*[^*]+\*\*$/.test(part)) node.append(el('b', {}, part.slice(2, -2))); else node.append(document.createTextNode(part)); });

  function render() {
    const k = getKey();
    panel.replaceChildren(
      el('div', { class: 'chath' }, el('div', { class: 'chatt' }, '🤖 Coach', el('small', {}, (D ? D.summary.league : '') + (provider() ? ' · ' + ({ gemini: 'Gemini (free)', anthropic: 'Claude', local: 'Free AI on your device' })[provider()] : ''))), el('div', {},
        provider() ? el('button', { class: 'chatx', title: 'Switch AI', onclick: () => { if (confirm('Switch to a different AI (removes any saved key from this browser)?')) { setKey(''); render(); } } }, '🔑') : '',
        el('button', { class: 'chatx', onclick: () => toggle(false) }, '×'))),
      el('div', { class: 'chatlog', id: 'chatlog' }, ...(history.length ? history.map(m => el('div', { class: 'msg ' + m.role }, m.role === 'assistant' ? md(m.content) : m.content))
        : [el('div', { class: 'msg assistant' }, md(`Yo. I've got everything for **${D ? D.summary.league : 'your league'}**: your roster, every trade the sim found, waivers, rankings and the odds. Ask me anything.`)),
           el('div', { class: 'chips' }, ['Explain my best realistic trade', 'Who should I target to win it all?', 'Should I sell anyone right now?', 'Break down my playoff lineup', 'Who are the best waiver adds for me?'].map(q => el('button', { onclick: () => ask(q) }, q)))])),
      k ? el('form', { class: 'chatin', onsubmit: e => { e.preventDefault(); const i = $c('#chatq'); if (i.value.trim()) { ask(i.value.trim()); i.value = ''; } } }, el('input', { id: 'chatq', placeholder: 'Ask about a trade, player, matchup…', autocomplete: 'off' }), el('button', { class: 'btn small', type: 'submit' }, 'SEND'))
        : keyScreen());
    const lg = $c('#chatlog'); lg.scrollTop = lg.scrollHeight;
  }
  function keyScreen() {
    const inp = el('input', { type: 'password', placeholder: 'Paste your free Gemini key (AIza…)', autocomplete: 'off' });
    const gpu = !!navigator.gpu;
    return el('div', { class: 'chatkey' },
      el('p', {}, el('b', {}, '🆓 Free, no key, no account: '), gpu ? 'run a small AI right on this device. The first time it downloads about 2 GB (then it\'s saved), and it works best on a laptop or desktop. It\'s not as smart as Claude, but it reads your league data.' : 'this browser can\'t run the on-device AI (it needs WebGPU). Try Chrome or Edge on a laptop or desktop, or use an option below.'),
      gpu ? el('button', { class: 'btn small', style: 'margin-bottom:10px', onclick: () => { setKey('local'); render(); } }, 'START FREE COACH') : '',
      el('p', {}, el('b', {}, 'Or use Google Gemini (smarter, free key). '), '1) Go to ', el('a', { href: 'https://aistudio.google.com/apikey', target: '_blank', rel: 'noopener' }, 'aistudio.google.com/apikey'), ' and sign in with Google. 2) Click Create API key. 3) Paste it below. No card needed. It\'s saved only in this browser, not on GitHub.'),
      el('form', { onsubmit: e => { e.preventDefault(); const v = inp.value.trim(); if (v) { setKey(v); render(); } } }, inp, el('button', { class: 'btn small', type: 'submit' }, 'SAVE')),
      el('p', { class: 'mut', style: 'font-size:12px;margin-top:6px' }, 'Have a Claude API key (sk-ant-…) instead? Paste that in the same box and it\'ll use Claude.'),
      el('p', { style: 'margin-top:14px' }, el('b', {}, 'No key at all: '), 'Type your question and I\'ll copy it with all the league data, then open Claude so you can paste it in.'),
      el('form', { onsubmit: e => { e.preventDefault(); const q = e.target.querySelector('input').value.trim() || 'Give me your take on my team and the best moves.'; copyToClaude(q); } }, el('input', { placeholder: 'Your question…' }), el('button', { class: 'btn small', type: 'submit' }, 'COPY + OPEN')));
  }
  async function copyToClaude(q) {
    await ensureCards();
    const txt = `${SYS}\n\n===== DATA =====\n${context()}\n\n===== MY QUESTION =====\n${q}`;
    try { await navigator.clipboard.writeText(txt); } catch (e) { const ta = el('textarea'); ta.value = txt; document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
    window.open('https://claude.ai/new', '_blank', 'noopener');
    alert('Copied! Paste it into the new Claude chat (Ctrl+V / long-press → Paste).');
  }
  // a short version of the league data for the small on-device model (it can only read a few thousand words)
  function compact(q) {
    const s = D.summary, L = [];
    const tw = s.this_week || {};
    L.push(`League: ${s.league}. Me: ${s.my_team}. Week ${s.week}. Scoring: ${s.settings.ppr ? 'full PPR' : 'standard'}, ${s.settings.pass_td || 4}-pt passing TDs. Playoffs weeks ${s.playoff_weeks.join(', ')}.`);
    L.push(`My odds: playoffs ${s.kpis.po}%, bye ${s.kpis.bye}%, title ${s.kpis.ch}%, make final ${s.kpis.fi}%.`);
    (s.take || []).slice(0, 3).forEach(t => L.push('Take: ' + t));
    if (tw.lineup) L.push(`This week (W${tw.week}) vs ${tw.opponent || '?'}: me ${tw.total} vs ${tw.opp_proj ?? '?'}, win ${tw.win_pct ?? '?'}%. Lineup: ` + tw.lineup.map(x => `${x.slot} ${x.player} ${x.pts}`).join(', '));
    (D.schedule || []).filter(x => x.w > s.week).forEach(x => L.push(`${x.label}: best lineup ${x.total}${x.opponent ? ` vs ${x.opponent} ${x.opp_total}, win ${x.win_pct}%` : ''}. ` + x.lineup.map(p => `${p.slot} ${p.player} ${p.pts}${p.chance < 1 ? ` (${Math.round(p.chance * 100)}% to play)` : ''}`).join(', ')));
    L.push('Top trades (title odds change):');
    D.trades.slice(0, 6).forEach(t => L.push(`- give ${t.give}, get ${t.get} from ${t.partner} [${t.kind}] ${t.change_pts > 0 ? '+' : ''}${t.change_pts} pts. Why me: ${(t.why_me || []).join(' ')} Why them: ${(t.why_them || []).join(' ')}`));
    L.push('Top waiver adds: ' + (D.waivers || []).slice(0, 4).map(w => `add ${w.player} drop ${w.drop} (${w.change_pts > 0 ? '+' : ''}${w.change_pts})`).join('; '));
    const ql = (q || '').toLowerCase(), hits = Object.values(CARDS || {}).filter(c => { const parts = c.name.toLowerCase().split(' '); return parts.some(p => p.length > 3 && ql.includes(p)); }).slice(0, 4);
    hits.forEach(c => L.push('PLAYER ' + cardLine(c).split('\n').slice(0, 3).join(' ')));
    return L.join('\n');
  }
  const SYS_SMALL = 'You are a fantasy football coach for the user (Alex). Answer using ONLY the league data below; never make up stats or players. Be short and casual: 3 to 6 sentences or a short list, plain words, no dashes. Rules: never suggest a backup TE, backup D/ST or backup K; never suggest dropping injured players expected back. Title odds changes are percentage points.';
  let ENGINE = null, ENGINE_P = null;
  async function localEngine(onStatus) {
    if (ENGINE) return ENGINE;
    if (!ENGINE_P) ENGINE_P = (async () => {
      const webllm = await import('https://esm.run/@mlc-ai/web-llm');
      const tries = ['Qwen2.5-3B-Instruct-q4f16_1-MLC', 'Llama-3.2-3B-Instruct-q4f16_1-MLC', 'Qwen2.5-1.5B-Instruct-q4f16_1-MLC'];
      let err;
      for (const m of tries) {
        try { return await webllm.CreateMLCEngine(m, { initProgressCallback: r => onStatus && onStatus(r) }, { context_window_size: 8192 }); }
        catch (e) { err = e; }
      }
      throw err;
    })();
    try { ENGINE = await ENGINE_P; return ENGINE; } catch (e) { ENGINE_P = null; throw e; }
  }
  async function askLocal(onText, setStatus) {
    const eng = await localEngine(r => setStatus(`Loading the free AI${r.progress ? ' ' + Math.round(r.progress * 100) + '%' : ''} (first time only)…`));
    setStatus('');
    const q = history[history.length - 2].content;
    const msgs = [{ role: 'system', content: SYS_SMALL + '\n\n=== LEAGUE DATA ===\n' + compact(q) }, ...history.slice(0, -1).slice(-5).map(m => ({ role: m.role, content: m.content }))];
    const it = await eng.chat.completions.create({ messages: msgs, stream: true, max_tokens: 600, temperature: 0.4 });
    let txt = '';
    for await (const ch of it) { const d = ch.choices[0] && ch.choices[0].delta && ch.choices[0].delta.content; if (d) { txt += d; onText(txt); } }
    return txt;
  }
  async function ensureCards() { if (!CARDS) CARDS = await fetch(BASE + 'players.json?v=' + D.summary.built).then(r => r.json()); }

  async function ask(q) {
    if (busy) return;
    if (!getKey()) { toggle(true); const i = panel.querySelector('.chatkey form:last-child input'); if (i) i.value = q; return; }
    busy = true; await ensureCards();
    history.push({ role: 'user', content: q }); history.push({ role: 'assistant', content: '' });
    render();
    const out = panel.querySelectorAll('.msg.assistant'); const bubble = out[out.length - 1];
    const t0 = Date.now(), think = el('span', { class: 'thinking' }, 'Coach is thinking'), dots = el('span', { class: 'dots' }, '…');
    bubble.replaceChildren(think, dots); const tick = setInterval(() => { if (bubble.contains(dots)) dots.textContent = '… ' + Math.round((Date.now() - t0) / 1000) + 's'; else clearInterval(tick); }, 1000);
    const onText = txt => { bubble.replaceChildren(md(txt)); const lg = $c('#chatlog'); lg.scrollTop = lg.scrollHeight; };
    try {
      if (provider() === 'gemini') { history[history.length - 1].content = (await askGemini(onText)) || '(no answer)'; return; }
      if (provider() === 'local') { history[history.length - 1].content = (await askLocal(onText, t => { if (t) think.textContent = t; })) || '(no answer)'; return; }
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-api-key': getKey(), 'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true' },
        body: JSON.stringify({ model: MODEL, max_tokens: 1500, stream: true,
          system: [{ type: 'text', text: SYS }, { type: 'text', text: '===== LEAGUE DATA =====\n' + context(), cache_control: { type: 'ephemeral' } }],
          messages: history.slice(0, -1).slice(-12).map(m => ({ role: m.role, content: m.content })) })
      });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(r.status === 401 ? 'That API key didn\'t work. Tap 🔑 to remove it and paste a new one.' : (e.error && e.error.message) || 'Request failed (' + r.status + ')'); }
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '', txt = '';
      for (;;) {
        const { done, value } = await rd.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i; while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
          if (!line.startsWith('data:')) continue;
          try { const ev = JSON.parse(line.slice(5)); if (ev.type === 'content_block_delta' && ev.delta && ev.delta.text) { txt += ev.delta.text; onText(txt); } } catch (e) {}
        }
      }
      history[history.length - 1].content = txt || '(no answer)';
    } catch (e) {
      history[history.length - 1].content = '⚠️ ' + e.message;
    } finally { clearInterval(tick); busy = false; render(); }
  }
  // ask Google which models this key can actually use, then pick the best Flash ones
  let MODELS_CACHE = null;
  async function geminiModels() {
    if (MODELS_CACHE) return MODELS_CACHE;
    try {
      const r = await fetch('https://generativelanguage.googleapis.com/v1beta/models?pageSize=200', { headers: { 'x-goog-api-key': getKey() } });
      const j = await r.json();
      if (r.status === 400 && /API key/i.test(JSON.stringify(j))) throw new Error('Google says that key isn\'t valid. Tap 🔑, remove it, and paste the key again (it starts with AIza).');
      const ok = (j.models || []).filter(m => (m.supportedGenerationMethods || []).includes('generateContent')).map(m => m.name.replace('models/', ''))
        .filter(n => /gemini/.test(n) && !/(image|tts|audio|live|embedding|vision|exp-|thinking|computer|robotics|nano)/.test(n));
      const score = n => (/flash/.test(n) ? 100 : /pro/.test(n) ? 40 : 0) + (/lite/.test(n) ? -15 : 0) + (/latest/.test(n) ? 8 : 0) + (/preview/.test(n) ? -3 : 0) + parseFloat((n.match(/gemini-(\d+(\.\d+)?)/) || [0, 0])[1]) * 4;
      ok.sort((a, b) => score(b) - score(a));
      if (ok.length) { MODELS_CACHE = [...new Set([...ok.slice(0, 6), ...GEMINI_MODELS])]; return MODELS_CACHE; }
      if (j.error) throw new Error('Google: ' + j.error.message + ' Make a fresh key at aistudio.google.com/apikey and paste it again.');
    } catch (e) { if (/key/i.test(e.message) || /Google:/.test(e.message)) throw e; }
    return GEMINI_MODELS;
  }
  async function askGemini(onText) {
    const base = { systemInstruction: { parts: [{ text: SYS + '\n\n===== LEAGUE DATA =====\n' + context() }] },
      contents: history.slice(0, -1).slice(-12).map(m => ({ role: m.role === 'assistant' ? 'model' : 'user', parts: [{ text: m.content }] })) };
    // fast settings first (no slow "thinking" step); if a model doesn't accept them, retry plain
    const configs = [{ maxOutputTokens: 2048, temperature: 0.6, thinkingConfig: { thinkingBudget: 0 } }, { maxOutputTokens: 2048, temperature: 0.6 }];
    let last = '';
    const models = await geminiModels();
    for (const model of models) {
      for (const gc of configs) {
        const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 120000);
        let r;
        try {
          r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, { method: 'POST', signal: ctl.signal,
            headers: { 'content-type': 'application/json', 'x-goog-api-key': getKey() }, body: JSON.stringify({ ...base, generationConfig: gc }) });
        } catch (e) { clearTimeout(timer); throw new Error(e.name === 'AbortError' ? 'Gemini took over 2 minutes. Try again or ask something shorter.' : 'Couldn\'t reach Google: ' + e.message); }
        clearTimeout(timer);
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          const msg = (j.error && j.error.message) || ('Request failed (' + r.status + ')');
          if (r.status === 400 && /API key/i.test(msg)) throw new Error('Google says that key isn\'t valid. Tap 🔑, remove it, and paste the key again (it starts with AIza).');
          if (r.status === 429) throw new Error('You hit Gemini\'s free limit for the moment. Wait a minute and try again.');
          if (r.status === 403 || r.status === 404) { last = last || `Your key can't use ${model}.`; break; }   // this key can't use this model: try the next
          if (r.status === 503 || r.status === 500) { last = 'Google\'s free models are all busy right now. Give it a minute and try again.'; await new Promise(z => setTimeout(z, 1200)); break; }   // busy: next model
          last = `${model}: ${msg}`; continue;   // unsupported setting: retry this model without it
        }
        const c = (j.candidates || [])[0] || {};
        const txt = ((c.content || {}).parts || []).filter(p => !p.thought).map(p => p.text || '').join('').trim();
        if (txt) { onText(txt); return txt; }
        if (j.promptFeedback && j.promptFeedback.blockReason) throw new Error('Gemini refused that one (' + j.promptFeedback.blockReason + '). Try rewording.');
        last = `${model}: empty answer (${c.finishReason || 'no reason given'})`;
      }
    }
    if (/can't use/.test(last)) throw new Error('Your Google key doesn\'t have access to any of the Gemini chat models. Go to aistudio.google.com/apikey, click Create API key (pick "Create in new project"), and paste that new key here.');
    throw new Error(last.startsWith('Google') ? last : 'Gemini didn\'t answer. ' + last);
  }
  function toggle(open) { panel.hidden = !open; fab.hidden = open; if (open) { render(); setTimeout(() => { const i = $c('#chatq'); if (i) i.focus(); }, 50); } }
  window.askCoach = q => { toggle(true); ask(q); };
})();
