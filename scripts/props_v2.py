"""Drop-in v2 player-prop projections for scripts/props_lab.py.

Usage inside props_lab.main(), after U / cur / INJ are known:

    from proposal import V2                      # (copy this file next to props_lab.py)
    v2 = V2(U, load(D / "prop_dists_v2.json"), cur, out_keys={k for k, v in INJ.items() if v in OUTS})
    our_mean = v2.our_mean                       # (p, st) -> mean or None
    ratios   = v2.ratios                         # (st, pos, m) -> 100 ratio quantiles (game-script-conditional set)
    BETA     = v2.BETA                           # data-fit elasticities to team points (replaces the old BETA dict)
    # counts: replace rng.poisson(m * f) with v2.sample_count(st, pos, m * f, rng) and the Poisson P() with v2.p_over_count

Design (fit in /home/claude/pp_work, 2016-2025, validated train<=2022 / test 2023-25):
  stage A  mu_A = bA . [EWMA(h) of stat, expected stat (ffopportunity xs), opportunities, air yds, shares, snap%, (EWMA(h2) same), stat/n, 1]
  stage B  mu   = mu_A * exp(bB . [log(it / his avg it), log(his avg it / 22.5), (spread - his avg spread)/7, log(def ratio),
                                   vacated target share by pos (now - his avg), vacated RB carry share, QB1 out, wind>10, cold<40, 1])
Only season-to-date data from usage.json are used (same as the live file), games with < half his median snap% dropped,
QB passing uses games with 15+ attempts.
"""
import math
import numpy as np

USEK = {'pass_yds': 'passyd', 'pass_tds': 'ptd', 'att': 'att', 'rush_yds': 'rushyd', 'car': 'car', 'rutd': 'rutd',
        'rec_yds': 'recyd', 'rec': 'rec', 'tgt': 'tgt', 'td': 'td', 'rair': 'airyd'}
XSK = ('pass_yards_gained_exp', 'pass_touchdown_exp', 'rush_yards_gained_exp', 'rush_touchdown_exp', 'rec_yards_gained_exp',
       'receptions_exp', 'rec_touchdown_exp')
DEFST = {'pass_yds': 'passyd', 'pass_tds': 'ptd', 'rush_yds': 'rushyd', 'rec_yds': 'recyd', 'rec': 'rec', 'td': None}


def wval(w, name, pos=None):
    """per-game value of a feature base name from a usage.json week entry"""
    g = lambda k: float(w.get(k) or 0)
    xs = w.get('xs') or {}
    if name in USEK:
        return g(USEK[name])
    if name in XSK:
        return float(xs.get(name) or 0)
    if name in ('tsh', 'csh', 'snap'):
        return g(name) / 100.0
    if name == 'td_exp':
        return float(xs.get('rush_touchdown_exp') or 0) + float(xs.get('rec_touchdown_exp') or 0)
    if name == 'yds':
        return g('rushyd') + g('recyd')
    if name == 'opp':
        return g('car') + g('tgt')
    raise KeyError(name)


def ystat(w, st, pos):
    if st == 'td':   # the fit's td: rushing + receiving (usage.json 'td' also counts a QB's passing TDs)
        return float(w.get('rutd') or 0) + (0.0 if pos == 'QB' else float(w.get('retd') or 0))
    return wval(w, st)


class V2:
    def __init__(self, U, FITS, cur, out_keys=(), key=None):
        self.U, self.F, self.cur = U, FITS, int(cur)
        self.key = key or (lambda n: ''.join(ch for ch in n.lower() if ch.isalnum()))
        self.out = set(out_keys)
        self.lines = U.get('lines') or {}
        self.P = list((U.get('players') or {}).values())
        meta = FITS.get('_meta', {})
        self.def_k, self.def_clip = meta.get('def_shrink_games', 3), meta.get('def_clip', [0.6, 1.6])
        self.BETA = {st: FITS.get(f'{st}|{"QB" if st.startswith("pass") else "WR"}', {}).get('beta_gs') for st in
                     ('pass_yds', 'pass_tds', 'rec_yds', 'rec', 'td')}
        self.BETA['rush_yds'] = FITS.get('rush_yds|RB', {}).get('beta_gs', 0.13)
        self.BETA_POS = {k: v.get('beta_gs') for k, v in FITS.items() if not k.startswith('_')}
        self.RB_MARGIN = FITS.get('rush_yds|RB', {}).get('margin_gs', 0.0127)   # exp(c * margin) on RB rush yds in the sims
        self._def = self._build_def()
        self._vac = {}

    # ---------------- helpers ----------------
    def _weeks(self, p, before):
        return sorted([w for w in p.get('weeks', []) if w.get('w', 0) < before and ((w.get('snap') or 0) > 0 or (w.get('att') or 0) + (w.get('car') or 0) + (w.get('tgt') or 0) > 0)],
                      key=lambda w: w['w'])

    @staticmethod
    def _filter(wk, pos, st):
        sn = sorted(float(w.get('snap') or 0) for w in wk)
        if sn:
            med = float(np.median(sn))
            wk = [w for w in wk if float(w.get('snap') or 0) >= 0.5 * med]
        if pos == 'QB' and st in ('pass_yds', 'pass_tds'):
            wk = [w for w in wk if (w.get('att') or 0) >= 15]
        return wk

    def _line(self, w, team):
        return (self.lines.get(str(w)) or {}).get(team) or {}

    # -------- opponent defense vs position: (allowed + k*avg) / (baseline + k*avg), prior weeks only --------
    def _build_def(self):
        A = {}   # (opp, pos, stat) -> list of (week, actual, baseline)
        for p in self.P:
            pos = p.get('pos')
            wk = self._weeks(p, self.cur)
            for i, w in enumerate(wk):
                prior = self._filter(wk[:i], pos, 'x')
                if not prior or not w.get('opp'):
                    continue
                for st in DEFST:
                    tdv = lambda z: float(z.get('rutd') or 0) + float(z.get('retd') or 0)
                    a = ystat(w, st, pos) if st != 'td' else tdv(w)
                    b = float(np.mean([ystat(x, st, pos) if st != 'td' else tdv(x) for x in prior]))
                    d = A.setdefault((w['opp'], pos, st), {})
                    s = d.setdefault(w['w'], [0.0, 0.0]); s[0] += a; s[1] += b
        avg = {}
        for (o, pos, st), d in A.items():
            avg.setdefault((pos, st), []).extend(v[1] for v in d.values())
        self._def_avg = {k: float(np.mean(v)) for k, v in avg.items()}
        return A

    def def_ratio(self, opp, pos, st, before):
        d = self._def.get((opp, pos, st), {})
        a = sum(v[0] for wk, v in d.items() if wk < before); b = sum(v[1] for wk, v in d.items() if wk < before)
        k = self.def_k * self._def_avg.get((pos, st), 0.0)
        r = (a + k) / (b + k) if (b + k) > 0 else 1.0
        return float(np.clip(r, *self.def_clip))

    # -------- teammates out: vacated target share by position, vacated RB carry share, QB1 out --------
    def vacated(self, team, week):
        ck = (team, week)
        if ck in self._vac:
            return self._vac[ck]
        mates = [q for q in self.P if q.get('team') == team and q.get('pos') in ('QB', 'RB', 'WR', 'TE')]
        v = dict(vac_WR=0., vac_TE=0., vac_RB=0., vacc_RB=0., qb_out=0.)
        qb_att = {}
        for q in mates:
            pw = self._weeks(q, week)
            if q['pos'] == 'QB' and len(pw) >= 1:
                qb_att[q['name']] = sum(float(w.get('att') or 0) for w in pw)
        pqb = max(qb_att, key=qb_att.get) if qb_att else None
        for q in mates:
            pw = self._weeks(q, week)
            if len(pw) < 2:
                continue
            if week >= self.cur:
                absent = self.key(q['name']) in self.out
            else:   # past week: no entry that week, and he either came back later or is on the injury list now
                played = any(w.get('w') == week for w in q.get('weeks', []))
                later = any(w.get('w', 0) > week for w in q.get('weeks', []) if w.get('w', 0) < self.cur)
                absent = (not played) and (later or self.key(q['name']) in self.out) and bool(self._line(week, team))
            if not absent:
                continue
            ts = float(np.mean([wval(w, 'tsh') for w in pw])); cs = float(np.mean([wval(w, 'csh') for w in pw]))
            if q['pos'] in ('WR', 'TE', 'RB'):
                v[f"vac_{q['pos']}"] += ts
            if q['pos'] == 'RB':
                v['vacc_RB'] += cs
            if q['pos'] == 'QB' and q['name'] == pqb:
                v['qb_out'] = 1.
        self._vac[ck] = v
        return v

    # ---------------- the projection ----------------
    def our_mean(self, p, st, detail=False):
        pos = p.get('pos'); fit = self.F.get(f'{st}|{pos}')
        if not fit:
            return None
        team = p.get('team')
        wk = self._filter(self._weeks(p, self.cur), pos, st)
        n = len(wk)
        if n < 3:
            return None
        # stage A
        xa = []
        for c in fit['colsA']:
            if c == 'icpt':
                xa.append(1.0); continue
            if c == 'own_over_n':
                xa.append(xa[0] / n); continue
            c0 = c[1:] if c.startswith('q') else c
            hs, base = c0[1:].split('_', 1)
            h = float(hs)
            wts = 0.5 ** (np.arange(n)[::-1] / h)
            vals = np.array([ystat(w, base, pos) if base in ('td', 'rutd') else wval(w, base, pos) for w in wk])
            xa.append(float(wts @ vals / wts.sum()))
        muA = max(float(np.dot(fit['bA'], xa)), 0.005 if st in ('td', 'pass_tds') else 0.05)
        # stage B
        L = self._line(self.cur, team)
        its = [self._line(w['w'], team).get('it') for w in wk]; sps = [self._line(w['w'], team).get('spread') for w in wk]
        its = [x for x in its if x]; sps = [x for x in sps if x is not None]
        mit = float(np.mean(its)) if its else 22.5; msp = float(np.mean(sps)) if sps else 0.0
        it = L.get('it') or mit; spr = L.get('spread') if L.get('spread') is not None else msp
        vac_now = self.vacated(team, self.cur)
        vac_hist = [self.vacated(team, w['w']) for w in wk]
        dv = lambda k_: vac_now[k_] - float(np.mean([h[k_] for h in vac_hist]))
        outdoor = (L.get('roof') or 'outdoors') in ('outdoors', 'open')
        wind = float(L.get('wind') or 0) if outdoor else 0.0
        temp = float(L.get('temp')) if (outdoor and L.get('temp') is not None) else 60.0
        feat = dict(log_it_rel=math.log(it / mit), log_it_lvl=math.log(mit / 22.5), spread_rel=(spr - msp) / 7,
                    log_def=math.log(self.def_ratio(L.get('opp') or (wk[-1].get('opp')), pos, st, self.cur)) if L.get('opp') else 0.0,
                    vac_WR=dv('vac_WR'), vac_TE=dv('vac_TE'), vac_RB=dv('vac_RB'), vacc_RB=dv('vacc_RB'), qb_out=dv('qb_out'),
                    wind10=min(max(wind - 10, 0), 25) / 10, cold40=min(max(40 - temp, 0), 40) / 10, icpt=1.0)
        xb = [feat[c] for c in fit['colsB']]
        mult = math.exp(float(np.dot(fit['bB'], xb)))
        mu = muA * mult
        if detail:
            return dict(mu=mu, muA=muA, mult=mult, n=n, feat={c: round(v, 4) for c, v in zip(fit['colsB'], xb)})
        return mu

    # ---------------- distributions ----------------
    def ratios(self, st, pos, m, game_script=True):
        """100 quantiles of actual/projection. game_script=True: the set to multiply by the sims' f=(own/mean)^BETA
        (variance already explained by team points removed); False: marginal set (use if you do NOT multiply by f)."""
        fit = self.F.get(f'{st}|{pos}')
        if not fit or 'buckets' not in fit:
            return None
        B = fit['buckets_gs'] if game_script else fit['buckets']
        b = next((b for b in B if m <= b['hi']), B[-1])
        return np.array(b['ratios'])

    def sample_count(self, st, pos, lam, rng, game_script=True):
        """draws for counts given per-sim means lam (= m * f). rec: negative binomial; pass TDs: under-dispersed binomial;
        anytime TD: binomial with the fitted var/mean when < 1, else Poisson."""
        fit = self.F.get(f'{st}|{pos}', {}); lam = np.maximum(np.asarray(lam, float), 1e-9)
        if st == 'rec':
            r = fit.get('nb_r_gs' if game_script else 'nb_r', 10.0)
            return rng.negative_binomial(r, r / (r + lam))
        v = fit.get('var_over_mean_gs' if game_script else 'var_over_mean', 1.0)
        if st in ('pass_tds', 'td') and v < 0.99:   # under-dispersed once team points are known
            q = float(np.clip(1 - v, 0.01, 0.99)); n_ = np.maximum(np.round(lam / q), 1).astype(int)
            return rng.binomial(n_, np.minimum(lam / n_, 1.0))
        return rng.poisson(lam)

    def p_over_count(self, st, pos, lam, line, rng=None, game_script=True):
        """P(X > line) averaged over sims (lam array)."""
        from scipy import stats as ss
        fit = self.F.get(f'{st}|{pos}', {}); lam = np.maximum(np.asarray(lam, float), 1e-9); k = math.floor(line)
        if st == 'rec':
            r = fit.get('nb_r_gs' if game_script else 'nb_r', 10.0)
            return float(np.mean(ss.nbinom.sf(k, r, r / (r + lam))))
        v = fit.get('var_over_mean_gs' if game_script else 'var_over_mean', 1.0)
        if st in ('pass_tds', 'td') and v < 0.99:
            q = float(np.clip(1 - v, 0.01, 0.99)); n_ = np.maximum(np.round(lam / q), 1)
            return float(np.mean(ss.binom.sf(k, n_, np.minimum(lam / n_, 1.0))))
        return float(np.mean(ss.poisson.sf(k, lam)))
