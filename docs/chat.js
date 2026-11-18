// Ask Coach: a chatbot that reads this league's data. Uses YOUR Anthropic API key, stored only in this browser.
(() => {
  const MODEL = 'claude-sonnet-5-5';
  const GEMINI_MODELS = ['gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.5-flash-lite', 'gemini-flash-lite-latest'];   // if one is busy, try the next   // free tier; tries the newest Flash first
  const store = { get: k => { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } }, set: (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} } };
  const provider = () => store.get('oxtail_gemini_key') ? 'gemini' : store.get('oxtail_anthropic_key') ? 'anthropic' : '';
  const getKey = () => store.get(provider() === 'gemini' ? 'oxtail_gemini_key' : 'oxtail_anthropic_key');
  const setKey = k => { store.set('oxtail_gemini_key', ''); store.set('oxtail_anthropic_key', ''); if (k) store.set(k.startsWith('sk-ant') ? 'oxtail_anthropic_key' : 'oxtail_gemini_key', k); };
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
    lines.push('\nTRADES FOUND BY THE SIM (give -> get | partner | kind | my title odds change in pts | title% after | their lineup change per week | value ratio, >1 means I send more market value | blocked = team I said I won\'t trade with):');
    D.trades.forEach(t => lines.push(`${t.give} -> ${t.get} | ${t.partner} | ${t.kind} | ${t.change_pts > 0 ? '+' : ''}${t.change_pts} | ${t.title_pct}% | ${t.partner_weekly_gain} | ${t.value_ratio}${t.blocked ? ' | BLOCKED' : ''}`));
    lines.push('\nWAIVER IDEAS (add | drop | weighted lineup pts gained | title odds change | ROS/g | playoff pts | rostered%):');
    D.waivers.forEach(w => lines.push(`${w.player} ${w.pos} | drop ${w.drop} | ${w.ros_gain} | ${w.change_pts ?? '-'} | ${w.ros_ppg} | ${w.playoff_pts} | ${w.own_pct}%`));
    lines.push('\nTEAMS TO WORK (team | record | playoff% | weakest starting spot | spare pieces):');
    D.bubble.forEach(b => lines.push(`${b.team} | ${b.record} | ${b.playoffs_pct}% | ${b.needs} (${b.weakest_starter_pts} pts) | ${b.has_extra}${b.blocked ? ' | BLOCKED' : ''}`));
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
      el('div', { class: 'chath' }, el('div', { class: 'chatt' }, '🤖 Coach', el('small', {}, (D ? D.summary.league : '') + (provider() ? ' · ' + (provider() === 'gemini' ? 'Gemini (free)' : 'Claude') : ''))), el('div', {},
        provider() ? el('button', { class: 'chatx', title: 'Remove saved key', onclick: () => { if (confirm('Remove your saved key from this browser?')) { setKey(''); render(); } } }, '🔑') : '',
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
    return el('div', { class: 'chatkey' },
      el('p', {}, el('b', {}, '⭐ Free: chat right here with Google Gemini. '), '1) Go to ', el('a', { href: 'https://aistudio.google.com/apikey', target: '_blank', rel: 'noopener' }, 'aistudio.google.com/apikey'), ' and sign in with Google. 2) Click Create API key. 3) Paste it below. No card needed. It\'s saved only in this browser, not on GitHub.'),
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
  async function askGemini(onText) {
    const base = { systemInstruction: { parts: [{ text: SYS + '\n\n===== LEAGUE DATA =====\n' + context() }] },
      contents: history.slice(0, -1).slice(-12).map(m => ({ role: m.role === 'assistant' ? 'model' : 'user', parts: [{ text: m.content }] })) };
    // fast settings first (no slow "thinking" step); if a model doesn't accept them, retry plain
    const configs = [{ maxOutputTokens: 2048, temperature: 0.6, thinkingConfig: { thinkingBudget: 0 } }, { maxOutputTokens: 2048, temperature: 0.6 }];
    let last = '';
    for (const model of GEMINI_MODELS) {
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
          if (r.status === 403) throw new Error('Google blocked the request: ' + msg);
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
    throw new Error(last.startsWith('Google') ? last : 'Gemini didn\'t answer. ' + last);
  }
  function toggle(open) { panel.hidden = !open; fab.hidden = open; if (open) { render(); setTimeout(() => { const i = $c('#chatq'); if (i) i.focus(); }, 50); } }
  window.askCoach = q => { toggle(true); ask(q); };
})();
