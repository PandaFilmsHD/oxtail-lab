"""Prop Lab: simulate every NFL game this week 50,000 times and price game lines + player props.

What it trusts, from the backtests (see data/prop_dists.json):
  * Closing spreads and totals are very hard to beat (a scores/EPA ratings model went 49-51% ATS 2020-25),
    so game sims START from the Vegas line. The only adjustments are angles that held up in both 2016-20 and 2021-25:
    wind 10+ mph (unders 55.8% then 58.9%) and prime-time games (unders 52.7% then 55.0%).
  * Player props: our own stat projection (season average + last 3 games, regressed the way 10 seasons say) is blended
    with Sleeper's projection, then only PART of the gap to the market is trusted (W below). The record section grades
    every pick so W can be tuned once there's a real sample.
Writes docs/data/props_lab.json and keeps every pick in data/props_picks.json for grading.
"""
import json, math, pathlib, datetime, re, statistics
import numpy as np
from scipy.special import ndtr
from scipy.stats import nbinom, binom as _binom, poisson as _poisson

ROOT = pathlib.Path(__file__).resolve().parents[1]
D = ROOT / "data"
OUT = ROOT / "docs" / "data" / "props_lab.json"
N = 50_000
W = 0.20            # how much of the gap between our number and the market's number we believe (tune from the record)
MAX_GAP = 0.35      # bigger disagreements usually mean the market knows something we don't (injury, role change): no play
MIN_EDGE = 0.025    # our chance has to beat the market's no-vig chance by 2.5+ points to be listed
PICKEM = 56.0       # pick'em legs (PrizePicks/Underdog/Pick6) need about 54 to 58% to profit, depending on the payout
SLW = 0.35          # Sleeper's share of "ours" (v2 gets 0.65). Three-cornered hat v2/Sleeper/market on week-11 props: 0.35 (80% CI 0.30-0.40)
QB_LINK = 0.8       # same-game link: QB pass-yds latent loads 0.8 * sd_i / sd_QB on each receiver (fit 2016-22, checked 2023-25)
REC_RHO = 0.9       # receptions latent = 0.9 * his rec-yds latent + noise (QB pass yds ~ WR1 catches r 0.365 in the data)
WA_RESID = 0.25     # share of game sims from the residual method (rest = real margins: keeps the 3/7/10/14 masses). Was 0.5
rng = np.random.default_rng(11)

ROOF = {"ARI": "dome", "ATL": "dome", "DAL": "dome", "DET": "dome", "HOU": "dome", "IND": "dome", "LV": "dome", "LA": "dome", "LAC": "dome", "MIN": "dome", "NO": "dome"}
LOC = {"BAL": (39.278, -76.623), "BUF": (42.774, -78.787), "CAR": (35.226, -80.853), "CHI": (41.862, -87.617), "CIN": (39.095, -84.516),
       "CLE": (41.506, -81.700), "DEN": (39.744, -105.020), "GB": (44.501, -88.062), "JAX": (30.324, -81.637), "KC": (39.049, -94.484),
       "MIA": (25.958, -80.239), "NE": (42.091, -71.264), "NYG": (40.813, -74.074), "NYJ": (40.813, -74.074), "PHI": (39.901, -75.168),
       "PIT": (40.447, -80.016), "SF": (37.403, -121.970), "SEA": (47.595, -122.332), "TB": (27.976, -82.503), "TEN": (36.166, -86.771), "WAS": (38.908, -76.864)}
FULL = {"Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
        "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
        "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV",
        "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN", "New England Patriots": "NE",
        "New Orleans Saints": "NO", "New York Giants": "NYG", "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
        "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS"}
MK = {"player_pass_yds": ("pass_yds", "Pass yds"), "player_pass_tds": ("pass_tds", "Pass TDs"), "player_rush_yds": ("rush_yds", "Rush yds"),
      "player_reception_yds": ("rec_yds", "Rec yds"), "player_receptions": ("rec", "Catches"), "td": ("td", "Anytime TD")}
USE = {"pass_yds": "passyd", "pass_tds": "ptd", "rush_yds": "rushyd", "rec_yds": "recyd", "rec": "rec"}
SLP = {"pass_yds": "pass_yd", "pass_tds": "pass_td", "rush_yds": "rush_yd", "rec_yds": "rec_yd", "rec": "rec"}
BETA = {"pass_yds": 0.21, "pass_tds": 1.17, "rush_yds": 0.13, "rec_yds": 0.25, "rec": 0.11, "td": 1.18}   # fallback only (v2 has its own): fitted elasticity to team points, 2016-25


def key(n):
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", n.lower())
    return re.sub(r"[^a-z]", "", n)


def dec(am):
    return 1 + (am / 100 if am > 0 else 100 / -am)


def imp_p(am):
    return (100 / (am + 100) if am > 0 else -am / (-am + 100)) if am else None


def conflict(combo):
    """True if the parlay has two legs on one game's side (both spreads, or a moneyline and the other team's alt spread) or two on its total."""
    seen = set()
    for c in combo:
        if c.get("type") == "game":
            g_ = (c["game"], "total" if c.get("kindg") == "total" else "side")
            if g_ in seen:
                return True
            seen.add(g_)
    return False


def amer(p):
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def load(p, d=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return d


# ------------------------------------------------------------------ constants
GL = {
    # forecast wind mph (outdoor only) -> total shift. Game-time-wind coefficients were -2.1 (10-14) and -2.8 (15+),
    # multiplied by 0.7 because forecasts miss.
    "wind_tiers": [(15, -1.7), (10, -1.0)],   # EB 2006-26: -2.44 (15+), -1.42 (10-14) game-time, x 0.7 forecast trust
    "wind_hit": {"10": {"old": 56.5, "new": 59.6, "n": 651}, "15": {"old": 51.4, "new": 61.4, "n": 210}},
    # rain at kickoff: -3.9 pts (SE 1.1), unders 60.3% (2016-20) / 74.6% (2021-25), n=131. Scale by forecast chance and trust 0.6.
    "rain_full": -3.6, "rain_trust": 0.6, "rain_min_prob": 40,   # EB 2014-26 rain-at-kickoff: -3.6 (OLS -4.3, SE 1.1)
    # SNF+MNF unders 55.4% / 57.9% (n=372). Regression says -2.2 but 2006-15 went 48% and 2026 so far 8-12, so use -1.0.
    "prime": -1.0,
    "max_total_shift": 4.5,     # wind + rain + prime can stack; never move the total more than this
    # weeks 1-4 dogs: 54.8% / 54.0% ATS 2016-25 (n=623), 52.6% 2006-15, 51.7% 1999-05, 54.2% 2026. Mean +1.0 pt, shrunk to 0.7.
    "early_dog": 0.7, "early_dog_weeks": 4,
    # probation (log only): +0.3 total pts per snap-weighted defensive starter ruled Out/Doubtful (both teams summed, cap 5).
    "use_def_out": False, "def_out_per": 0.3, "def_out_cap": 5.0,
    # 2-team 6-pt teaser break-even per leg: sqrt(1/decimal). -110 -> 72.4%, -120 -> 73.9%, -130 -> 75.2%
    "teaser_price": -120,
}


def total_shift(wx, roof, wd, gt):
    """Points to add to the closing total, plus the angle text. wx = weather() dict or None."""
    shift, angles = 0.0, []
    if wx and roof != "dome":
        for th, s in GL["wind_tiers"]:
            if wx.get("wind", 0) >= th:
                h = GL["wind_hit"][str(th)]
                shift += s
                angles.append(f"Wind {wx['wind']} mph forecast. Unders hit {h['old']}% (2016-20) and {h['new']}% (2021-25) at {th}+ mph.")
                break
        rp = wx.get("rain") or 0
        if rp >= GL["rain_min_prob"]:
            s = GL["rain_full"] * GL["rain_trust"] * rp / 100
            shift += s
            angles.append(f"{rp}% rain chance. Unders hit 60% (2016-20) and 75% (2021-25) in games played in rain.")
    snf = wd == "Sunday" and (gt or "") >= "19:00"
    if snf or wd == "Monday":
        shift += GL["prime"]
        angles.append("SNF/MNF. Unders hit 55.4% (2016-20) and 57.9% (2021-25) on Sunday and Monday nights (Thursday games: no edge).")
    shift = max(-GL["max_total_shift"], min(GL["max_total_shift"], shift))
    return shift, angles


def spread_shift(week, spr):
    """Home-margin points to add (spr = home expected margin). Positive spr = home favored."""
    try:
        wk = int(week)
    except (TypeError, ValueError):
        return 0.0, []
    if 1 <= wk <= GL["early_dog_weeks"] and spr != 0:
        s = -GL["early_dog"] if spr > 0 else GL["early_dog"]      # move toward the dog
        return s, [f"Week {wk} underdog. Early-season dogs covered 54.8% (2016-20) and 54.0% (2021-25)."]
    return 0.0, []


def sample_game(spr, tot, resid, pool, rng, n, t_shift=0.0, m_shift=0.0, noise=0.6, cover=0.5, over=0.5):
    """Joint (home margin, total) sims.
    resid: current DIST['_game_resid'] rows [margin - spread, total - total_line, spread] (2016-25).
    pool : new DIST['_game_pool'] rows [margin - spread, total - total_line, spread, total_line, season] (2006-25).
    WA_RESID (0.25) of the draws = residual method (smooth tails); the rest = real margins from games with ~the same spread
    (keeps exact mass on 3, 7, 10, 14). Opt pass (expanding window, tuned 2011-20, tested 2021-26): 0.25 + 400-game pool
    + residual pool 2018-25 + margin pool 1999-2025 cut summed Brier (alt spreads + alt totals + key masses) 0.45753 -> 0.45697;
    P(|margin|=3) 10.4% -> 12.7% (actual 15.5%). Out of sample this cut the average calibration miss on alt spreads from 2.2 to ~1.5 pts
    of probability and improved Brier in both 2016-20 and 2021-25."""
    na = int(round(n * WA_RESID)); nb = n - na
    # A) current method
    near = resid[np.abs(resid[:, 2] - spr) <= max(3.0, abs(spr) * 0.35)]
    near = near if len(near) >= 150 else resid
    near = near - np.array([np.median(near[:, 0]), np.median(near[:, 1]), 0])
    i = rng.integers(0, len(near), na)
    mar_a = spr + near[i, 0] + rng.normal(0, noise, na)
    mar_a = mar_a + (spr - np.quantile(mar_a, 1 - cover))   # P(mar_a > spr) = cover
    tr_a = near[i, 1]
    # B) direct margins, favorite's view (home and away favorites pooled)
    psp = pool[:, 2]
    pfs = np.abs(psp)
    pfm = np.where(psp >= 0, 1.0, -1.0) * (psp + pool[:, 0])
    fs, sgn = abs(spr), (1.0 if spr >= 0 else -1.0)
    for w in (0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 99):
        sel = np.where(np.abs(pfs - fs) <= w)[0]
        if len(sel) >= 400:
            break
    j = sel[rng.integers(0, len(sel), nb)]
    fm = pfm[j] + np.round(fs - pfs[sel].mean())
    mar_b = sgn * fm
    tr_b = pool[j, 1] - np.median(pool[sel, 1])
    up, dn = mar_b > spr, mar_b < spr
    if up.any() and dn.any():   # reweight to the market's cover chance, keeping each side's shape, the pushes and the key numbers
        pu, pd_ = up.mean(), dn.mean()
        w_ = np.where(up, cover * (pu + pd_) / pu, np.where(dn, (1 - cover) * (pu + pd_) / pd_, 1.0))
        k_ = rng.choice(nb, nb, p=w_ / w_.sum()); mar_b, tr_b = mar_b[k_], tr_b[k_]
    if m_shift:   # integer-preserving shift: move |m_shift| of draws by one full point
        mar_b = mar_b + np.sign(m_shift) * (rng.random(nb) < min(1.0, abs(m_shift)))
        mar_a = mar_a + m_shift
    mar = np.r_[mar_a, mar_b]
    totl = tot + t_shift + np.r_[tr_a, tr_b] + rng.normal(0, noise, n)
    totl = totl - (np.quantile(totl, 1 - over) - (tot + t_shift))   # centered after the jitter: no angle = exactly the market's no-vig over chance
    p = rng.permutation(n)
    return mar[p], totl[p]


def teaser_legs(h, a, spr, mar, kick):
    """6-pt teaser legs that historically clear the 2-team break-even. Only dogs +1.5..+2.5 qualify
    (77.7% 2016-25, 75.0% 2006-15, 78.8% 2026). Favorites -7.5..-8.5 went 72.5%: below the 73.9% needed at -120."""
    out = []
    fs = abs(spr)
    if 1.5 <= fs <= 2.5:
        dog = a if spr > 0 else h
        fm = mar if spr > 0 else -mar           # favorite margin
        line = fs + 6
        p = float(np.mean(fm < line))           # dog +line covers
        out.append({"label": f"{dog} +{line:g} (6-pt teaser leg)", "p": p, "hist": 77.7, "kick": kick, "type": "teaser"})
    return out


def teaser_breakeven(price=None):
    price = price or GL["teaser_price"]
    d = 1 + (price / 100 if price > 0 else 100 / -price)
    return math.sqrt(1 / d)




def half(x):
    """Alt lines on the half point so they can't push."""
    return x if x % 1 else x - 0.5


_CFG = json.loads((ROOT / "config.json").read_text()) if (ROOT / "config.json").exists() else {}
EXCHANGE = _CFG.get("venue") == "exchange"   # Alex bets on DraftKings Predictions: buy shares in cents, a share pays $1


def ex_price(pm):
    """What a share of a side costs on the exchange, as American odds: the market's no-vig chance + about 1 cent of spread
    + a fee (Kalshi-style 0.07*p*(1-p); DraftKings Predictions' exact fee may differ)."""
    c_ = min(0.99, pm + _CFG.get("ex_spread", 0.01) + _CFG.get("ex_fee", 0.07) * pm * (1 - pm))
    return amer(c_)


def usd_(u):
    return "$" + format(u * float(_CFG.get("unit", 25)), ".2f").replace(".00", "")


def kfee(c):
    """Kalshi taker fee per $1 contract: 7% x price x (1 - price), rounded up to the cent on real orders."""
    return 0.07 * c * (1 - c)


def kbuy(p, c):
    """EV of buying one share at c cents (0-1) when it wins with chance p, after the fee."""
    return p / (c + kfee(c)) - 1 if c else None


def weather(team, kick):
    """Forecast wind (mph) and temp at kickoff from Open-Meteo. None if indoors or unavailable."""
    if team in ROOF or team not in LOC:
        return None
    try:
        import requests
        t = datetime.datetime.fromisoformat(kick.replace("Z", "+00:00"))
        if (t - datetime.datetime.now(datetime.timezone.utc)).days > 7:
            return None
        lat, lon = LOC[team]
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": lat, "longitude": lon, "hourly": "wind_speed_10m,temperature_2m,precipitation_probability",
                         "wind_speed_unit": "mph", "temperature_unit": "fahrenheit", "timezone": "UTC", "start_date": t.date().isoformat(), "end_date": t.date().isoformat()}, timeout=20)
        h = r.json()["hourly"]; i = min(range(len(h["time"])), key=lambda j: abs(datetime.datetime.fromisoformat(h["time"][j] + "+00:00") - t))
        return {"wind": round(h["wind_speed_10m"][i]), "temp": round(h["temperature_2m"][i]), "rain": h["precipitation_probability"][i]}
    except Exception:
        return None


def main():
    U = load(D / "usage.json", {}); DIST = load(D / "prop_dists.json", {}); RAW = load(D / "props_raw.json", {}) or {}
    KAL = load(D / "kalshi.json", {}) or {}   # real Kalshi share prices (pull_kalshi.py)
    KBUY = []                                 # every Kalshi rung where our chance beats what a share costs
    SL = load(D / "sleeper_proj.json", {}) or {}; GO = load(D / "game_odds.json", {}) or {}
    M = next((load(p) for p in sorted(D.glob("*/model.json"))), {}) or {}
    cur = str(M.get("cur") or RAW.get("week") or "")
    lines = (U.get("lines") or {}).get(cur, {}); kick = (U.get("kick") or {}).get(cur, {})
    now = datetime.datetime.now(datetime.timezone.utc)
    if not lines or not DIST:
        print("props_lab: no lines or fits yet"); return
    resid = np.array(DIST["_game_resid"], dtype=float)
    pool = pool_g = np.array(DIST.get("_game_pool") or [r + [0, 0] for r in DIST["_game_resid"]], dtype=float)
    TEASERS = []
    ANG = DIST["_angles"]

    # best prices for game markets (Odds API), keyed by home team
    gbest, gdk = {}, {}
    for g in (GO.get("games") or {}).values():
        h, a = FULL.get(g["home"]), FULL.get(g["away"])
        if not h or (kick.get(h) and g.get("at") and abs((datetime.datetime.fromisoformat(g["at"].replace("Z", "+00:00")) - datetime.datetime.fromisoformat(kick[h].replace("Z", "+00:00"))).total_seconds()) > 43200):
            continue   # game_odds holds this week AND next: keep only this week's game for each home team
        b = gbest.setdefault(h, {})
        for mk, books in g["books"].items():
            if mk not in ("h2h", "spreads", "totals"):
                continue
            for bk in books:
                for o in bk["outcomes"]:
                    side = {"h2h": "ml", "spreads": "spr", "totals": "tot"}[mk]
                    nm = o["name"]; tag = "home" if FULL.get(nm) == h else "away" if FULL.get(nm) == a else nm.lower()
                    k_ = (side, tag, o.get("point"))
                    if k_ not in b or o["price"] > b[k_]["price"]:
                        b[k_] = {"price": o["price"], "book": bk["book"]}
                    if bk["book"] == "draftkings":
                        gdk.setdefault(h, {})[(side, tag)] = (o.get("point"), o["price"], g.get("at"))

    # players: name -> usage entry
    PL = {key(v["name"]): v for v in (U.get("players") or {}).values()}
    SLK = {}
    SLP_ = SL.get("players", {}) if str(SL.get("week")) == str(M.get("cur") or RAW.get("week") or "") else {}
    for k_, v in SLP_.items():
        n_, t_ = (k_.split("|") + [""])[:2]
        if v and t_:   # skip retired/teamless duplicates that share a name
            SLK[(key(n_), t_)] = v

    def our_mean(p, st):
        """Season average + last 3, regressed the way 10 seasons of games say (prop_dists.json)."""
        fit = DIST.get(f"{st}|{p['pos']}")
        if not fit:
            return None
        wk = [w for w in p.get("weeks", []) if w.get("w", 0) < int(cur)]
        sn = sorted(w.get("snap") or 0 for w in wk)
        if sn:
            med = sn[len(sn) // 2]
            wk = [w for w in wk if (w.get("snap") or 0) >= 0.5 * med]   # drop games he got hurt in or barely played
        if p["pos"] == "QB" and st in ("pass_yds", "pass_tds"):
            wk = [w for w in wk if (w.get("att") or 0) >= 15]
        if st == "td":
            vals = [(w.get("rutd", 0) or 0) + (w.get("td", 0) - (w.get("rutd", 0) or 0) if p["pos"] != "QB" else 0) for w in wk]
            vals = [(w.get("rutd", 0) or 0) + (w.get("retd", 0) or 0) for w in wk]
        else:
            vals = [w.get(USE[st], 0) or 0 for w in wk]
        if len(vals) < 3:
            return None
        m = (1 - fit["w_recent"]) * np.mean(vals) + fit["w_recent"] * np.mean(vals[-3:])
        return max(0.0, float(fit["slope"] * m + fit["icpt"]))

    def sl_mean(n, st, team=None):
        v = SLK.get((key(n), {"LA": "LAR"}.get(team, team)))
        if not v:
            return None
        if st == "td":
            return (v.get("rush_td", 0) or 0) + (v.get("rec_td", 0) or 0)
        return v.get(SLP[st])

    def ratios(st, pos, m):
        fit = DIST.get(f"{st}|{pos}")
        if not fit:
            return None
        b = next((b for b in fit["buckets"] if m <= b["hi"]), fit["buckets"][-1])
        return np.array(b["ratios"])

    def p_over_given_mean(st, pos, m, line):
        if st in ("pass_tds", "td"):
            k = math.floor(line)
            return 1 - sum(math.exp(-m) * m ** i / math.factorial(i) for i in range(k + 1))
        r = ratios(st, pos, m)
        return float(np.mean(r * m > line)) if r is not None else None

    def mkt_mean(st, pos, line, p_over):
        lo, hi = 0.01, max(4 * line, 5)
        for _ in range(40):
            mid = (lo + hi) / 2
            if p_over_given_mean(st, pos, mid, line) < p_over:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    games, sims, done, LEGS, SAFE = [], {}, set(), [], []
    HOMES = {FULL.get(e["home"]) for e in RAW.get("events", [])} | {FULL.get(g["home"]) for g in (GO.get("games") or {}).values()}
    for tm_, L_ in lines.items():   # older stats files don't say who's home: fill it in from the odds feed
        if "home" not in L_:
            L_["home"] = tm_ in HOMES if HOMES else L_["spread"] >= lines.get(L_["opp"], {}).get("spread", 0)
    # injury status for every player ESPN gives us (all four leagues' player pools)
    INJ = {}
    for mp in sorted(D.glob("*/model.json"), key=lambda p_: (load(p_, {}) or {}).get("at") or ""):   # newest file last, so it wins
        for q in (load(mp, {}) or {}).get("players", []):
            INJ[key(q["n"])] = q.get("inj") or "ACTIVE"
    OUTS = ("OUT", "INJURY_RESERVE", "DOUBTFUL", "SUSPENSION")
    # who was already out when the lines went up: anyone ruled out AFTER that makes his teammates' lines stale
    IS = load(D / "props_inj_state.json", {}) or {}
    if IS.get("lines_at") != RAW.get("at"):
        IS = {"lines_at": RAW.get("at"), "out": [k for k, v in INJ.items() if v in OUTS]}
    newly_out = {k for k, v in INJ.items() if v in OUTS} - set(IS["out"])
    # v2 projections (usage + Vegas + defense + teammate injuries + weather; 2-6% more accurate than v1 out of sample)
    DIST2 = load(D / "prop_dists_v2.json", {}) or {}
    V2A = V2B = None
    if DIST2:
        try:
            from props_v2 import V2
            all_out = {k for k, v in INJ.items() if v in OUTS}
            V2A = V2(U, DIST2, cur, out_keys=all_out, key=key)                 # everyone who's out right now
            V2B = V2(U, DIST2, cur, out_keys=all_out - newly_out, key=key)     # as of when the lines went up
        except Exception as ex_:
            print("props_lab: v2 projections unavailable", ex_); V2A = V2B = None
    (D / "props_inj_state.json").write_text(json.dumps(IS, separators=(",", ":")))
    NEWS = {}   # team -> list of (position, player entry) newly ruled out
    for k in newly_out:
        q = PL.get(k)
        if q and q.get("pos") in ("QB", "RB", "WR", "TE"):
            NEWS.setdefault(q["team"], []).append(q)
    for tm, L in lines.items():
        if not L.get("home") or tm in done:
            continue
        h, a = tm, L["opp"]; done |= {h, a}
        k_ = kick.get(h)
        started = bool(k_) and datetime.datetime.fromisoformat(k_.replace("Z", "+00:00")) <= now
        spr, tot = L["spread"], L["total"]   # spread = home expected margin
        wx = weather(h, k_) if k_ and not started else None
        roof = "dome" if h in ROOF else (L.get("roof") or "outdoors")
        shift, angles = total_shift(wx, roof, L.get("wd"), L.get("gt"))   # wind tiers, rain, SNF/MNF (backtested vs closing lines)
        m_shift, s_angles = spread_shift(cur, spr)                            # weeks 1-4 dogs
        nv2 = lambda x_, y_: imp_p(x_ or -110) / (imp_p(x_ or -110) + imp_p(y_ or -110))
        mar, totl = sample_game(spr, tot, resid, pool, rng, N, t_shift=shift, m_shift=m_shift,
                                cover=nv2(L.get("spread_odds"), lines.get(a, {}).get("spread_odds")), over=nv2(L.get("over_odds"), L.get("under_odds")))   # half the sims use real margins: keeps 3 and 7 right
        hp = np.maximum(0, (totl + mar) / 2); ap = np.maximum(0, (totl - mar) / 2)
        sims[h] = (hp, ap, mar); sims[a] = (ap, hp, -mar)
        b = gbest.get(h, {})
        def price(side, tag, pt, default):
            x = b.get((side, tag, pt)); return (x["price"], x["book"]) if x else (default, None)
        def line_odds(v):
            return v if v not in (None, 0) else -110
        plays = []
        p_home_cover = float(np.mean(mar > spr)) / max(1e-9, float(np.mean(mar != spr)))
        p_over = float(np.mean(totl > tot)) / max(1e-9, float(np.mean(totl != tot)))
        p_home_win = float(np.mean(hp > ap) + 0.5 * np.mean(hp == ap))
        opts = [("spr", "home", -spr, f"{h} {-spr:+g}", p_home_cover, line_odds(L.get("spread_odds"))),
                ("spr", "away", spr, f"{a} {spr:+g}", 1 - p_home_cover, line_odds(lines.get(a, {}).get("spread_odds"))),
                ("tot", "over", tot, f"Over {tot:g}", p_over, line_odds(L.get("over_odds"))),
                ("tot", "under", tot, f"Under {tot:g}", 1 - p_over, line_odds(L.get("under_odds"))),
                ("ml", "home", None, f"{h} moneyline", p_home_win, L.get("ml")), ("ml", "away", None, f"{a} moneyline", 1 - p_home_win, lines.get(a, {}).get("ml"))]
        imp = lambda am: (100 / (am + 100) if am > 0 else -am / (-am + 100)) if am else None
        for i, (side, tag, pt, label, p, dflt) in enumerate(opts):
            if dflt is None:
                continue
            other = opts[i + 1 if i % 2 == 0 else i - 1][5]
            if other is None:
                continue
            pm = imp(dflt) / (imp(dflt) + imp(other))   # the market's chance with the vig taken out (what Kalshi/Polymarket trade near)
            edge = p - pm
            if edge >= MIN_EDGE and ((side == "tot" and angles) or (side == "spr" and s_angles)):   # only the angles that held up in the backtest create game plays
                plays.append({"pick": label, "p": round(100 * p, 1), "p_mkt": round(100 * pm, 1), "edge": round(100 * edge, 1), "max_c": int(100 * p - 2), "kind": side})
        auto = []   # every-bet tracker: on each market, the side the sims like more than DraftKings' no-vig number, at DK's own number and price
        dkb = gdk.get(h, {})
        if all(dkb.get(z) for z in (("spr", "home"), ("spr", "away"), ("tot", "over"), ("tot", "under"), ("ml", "home"), ("ml", "away"))):
            # DraftKings' own numbers: re-run the sims from DK's spread and total (same angles), so "edge" = angles + DK's price, not a stale-line gap
            nvd = lambda t1, t2: imp_p(dkb[t1][1]) / (imp_p(dkb[t1][1]) + imp_p(dkb[t2][1]))
            mar_u, totl_u = sample_game(-dkb[("spr", "home")][0], dkb[("tot", "over")][0], resid, pool, rng, N, t_shift=shift, m_shift=m_shift,
                                        cover=nvd(("spr", "home"), ("spr", "away")), over=nvd(("tot", "over"), ("tot", "under")))
            src = "dk"
        else:
            mar_u, totl_u, src = mar, totl, "consensus"
        phw_u = float(np.mean(mar_u > 0) + 0.5 * np.mean(mar_u == 0))
        def p_at(side, tag, pt):   # our chance at any number, from the same sims, pushes taken out
            if side == "ml":
                return phw_u if tag == "home" else 1 - phw_u
            v = ((mar_u if tag == "home" else -mar_u) + pt) if side == "spr" else ((totl_u - pt) if tag == "over" else (pt - totl_u))
            return float(np.mean(v > 0)) / max(1e-9, float(np.mean(v != 0)))
        for i, (side, tags) in zip((0, 2, 4), (("spr", ("home", "away")), ("tot", ("over", "under")), ("ml", ("home", "away")))):
            q_ = [dkb.get((side, t)) for t in tags] if src == "dk" else [(opts[i][2], opts[i][5], None), (opts[i + 1][2], opts[i + 1][5], None)]
            if any(z[1] in (None, 0) for z in q_) or (side != "ml" and any(z[0] is None for z in q_)):
                continue
            ims = [imp_p(z[1]) for z in q_]
            sides = [(p_at(side, t, z[0]) - im / sum(ims), t, z[0], int(z[1]), p_at(side, t, z[0]), im / sum(ims)) for t, z, im in zip(tags, q_, ims)]
            e_, t, pt, pr_, p_, pm_ = max(sides)
            if EXCHANGE:
                pr_ = ex_price(pm_)   # the share price he'd actually pay
            team = h if t == "home" else a
            ev_ = p_ * dec(pr_) - 1
            if pr_ >= -150 and e_ > 0.006:   # no angle = edge 0 at the market's own price (+-0.2 pts of sim noise): nothing to test there
                auto.append({"pick": f"{team} {pt:+g}" if side == "spr" else f"{t.title()} {pt:g}" if side == "tot" else f"{team} moneyline", "tag": t, "pt": pt,
                             "price": pr_, "src": src, "p": round(100 * p_, 1), "p_mkt": round(100 * pm_, 1), "edge": round(100 * e_, 1), "ev": round(100 * ev_, 1),
                             "tier": "bet" if ev_ >= 0 else "lean", "kind": side,
                             "bucket": "angle" if (side == "tot" and angles) or (side == "spr" and s_angles) else "ml" if side == "ml" else "no_angle"})
        if not started:
            gk = tuple(sorted((h, a)))
            for t in teaser_legs(h, a, spr, mar, k_):
                t["game"] = gk; t["breakeven"] = round(100 * teaser_breakeven(), 1); TEASERS.append(t)
            fav, dog, fm = (h, a, mar) if spr > 0 else (a, h, -mar)
            for lab, ind in ((f"{fav} moneyline", fm > 0),
                             *[(f"{fav} {-x:+g} alt spread", fm > x) for x in (1.5, 2.5, 3.5, 4.5, 6.5, 7.5) if x < abs(spr)],
                             *[(f"{dog} {x:+g} alt spread", -fm > -x) for x in (7.5, 9.5, 10.5, 13.5, 14.5, 16.5, 17.5, 20.5) if x > abs(spr)],
                             *[(f"{a} @ {h} Over {x:g}", totl > x) for x in [half(tot - k) for k in (5, 7, 9, 11)]],
                             *[(f"{a} @ {h} Under {x:g}", totl < x) for x in [half(tot + k) for k in (5, 7, 9, 11)]]):
                pa = float(ind.mean())
                if 0.84 <= pa <= 0.92 and "moneyline" not in lab:
                    SAFE.append({"lean": 0.0, "label": lab, "game": gk, "who": lab.split()[0], "p": pa, "ind": ind, "type": "game", "kick": k_})
                if 0.70 <= pa <= (0.93 if "moneyline" in lab else 0.90):
                    LEGS.append({"kindg": "total" if (" Over " in lab or " Under " in lab) else "ml" if "moneyline" in lab else "spread", "lean": (0.004 if (" Under " in lab and angles) else 0.0), "label": lab, "game": gk, "who": (f"{a}@{h} total" if (" Over " in lab or " Under " in lab) else lab.split()[0]), "p": pa, "ind": ind, "type": "game", "kick": k_})
        kg = (KAL.get("games") or {}).get(h) if not started else None
        if kg and kg.get("away") == a:
            def kadd(lab, pk_, c_, tick, kind):
                if c_ and 0.03 <= c_ <= 0.97:
                    KBUY.append({"label": lab, "side": "Yes", "p": round(100 * pk_, 1), "cost": round(100 * c_), "fee": round(100 * kfee(c_), 1), "ev": round(100 * kbuy(pk_, c_), 1),
                                 "edge": round(100 * (pk_ - c_), 1), "kick": k_, "ticker": tick, "type": "game", "kind": kind, "home": h, "away": a, "game": "-".join(sorted((h, a)))})
            for tm_, q_ in kg.get("ml", {}).items():
                pw = float(np.mean(mar > 0) + 0.5 * np.mean(mar == 0)) if tm_ == h else float(np.mean(mar < 0) + 0.5 * np.mean(mar == 0))
                kadd(f"{tm_} to win", pw, q_.get("ask"), q_.get("ticker"), "ml")
            for q_ in kg.get("spread", []):
                tm_, sk_ = q_.get("team"), q_.get("strike")
                if sk_ is None:
                    continue
                m_ = mar if tm_ == h else -mar
                ps_ = float(np.mean(m_ > sk_))
                kadd(f"{tm_} by {int(sk_ + 0.5)}+", ps_, q_.get("ask"), q_.get("ticker"), "spread")
                kadd(f"{tm_} doesn't win by {int(sk_ + 0.5)}+", 1 - ps_, q_.get("no_ask"), q_.get("ticker"), "spread_no")
            for q_ in kg.get("total", []):
                sk_ = q_.get("strike")
                if sk_ is None:
                    continue
                po_ = float(np.mean(totl > sk_))
                kadd(f"{a} @ {h} over {sk_:g}", po_, q_.get("ask"), q_.get("ticker"), "total")
                kadd(f"{a} @ {h} under {sk_:g}", 1 - po_, q_.get("no_ask"), q_.get("ticker"), "total_no")
        games.append({"home": h, "away": a, "kick": k_, "started": started, "spread": spr, "total": tot, "roof": roof, "weather": wx, "angles": angles + s_angles,
                      "sim": {"home_win": round(100 * p_home_win, 1), "home_cover": round(100 * p_home_cover, 1), "over": round(100 * p_over, 1),
                              "home_pts": round(float(np.mean(hp)), 1), "away_pts": round(float(np.mean(ap)), 1), "fair_total": round(float(np.median(totl)), 1),
                              "fair_home_ml": amer(p_home_win), "blowout": round(100 * float(np.mean(np.abs(mar) >= 17)), 1), "one_score": round(100 * float(np.mean(np.abs(mar) <= 8)), 1)},
                      "plays": sorted(plays, key=lambda x: -x["edge"]), "auto": auto})

    # ---------------- player props ----------------
    ev_team = {}
    for e in RAW.get("events", []):
        ev_team[e["id"]] = (FULL.get(e["home"]), FULL.get(e["away"]), e["at"])
    BYLAST = {}
    for v in (U.get("players") or {}).values():
        nm_ = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", v["name"].lower()).split()
        if nm_:
            BYLAST.setdefault((re.sub(r"[^a-z]", "", nm_[-1]), v.get("team")), []).append(v)
    def match(nm, mks):
        if re.search(r"(D/ST|Defense)$", nm):
            return None
        evid = next((x.get("event") for x in mks.values() if x.get("event")), None)
        teams = ev_team.get(evid, (None, None))[:2]
        p = PL.get(key(nm))
        if p:
            return p if (p.get("team") in teams or not evid) else None   # same name, different team: not him
        parts = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", nm.lower()).split()
        c = [v for t in teams for v in BYLAST.get((re.sub(r"[^a-z]", "", parts[-1]) if parts else "", t), [])]
        return c[0] if len(c) == 1 else None   # Drew/Andrew Ogletree, Zonovan/Bam Knight, Greg/Gregory Desrosiers
    LONGS = []
    # same-game pass link: QB pass-yds latent = sum_i c_i*eta_i + sqrt(1 - sum c_i^2)*eps, c_i = QB_LINK * sd_i / sd_QB from the posted lines.
    # Receivers' etas are independent (WR1~WR2 residual r = -0.004 in 2016-25). Uniforms = Phi(latent), so every marginal is unchanged.
    # 2023-25 check: QB~WR1 rec yds r 0.08 -> 0.45 (data 0.48), QB~WR2 0.04 -> 0.40 (0.38), QB~TE1 0.03 -> 0.36 (0.35), WR1~WR2 ~0 (0).
    LATQ, LATR = {}, {}
    # same-game TD link: one shared "scoring through the air" shock per team. QB pass TDs load 0.7, WR TDs 0.6, TE 0.48,
    # RB -0.3 (RB TDs come at the expense of passing TDs). Fit so sim correlations match 2016-25 residuals:
    # QB pass TDs ~ WR1 anytime TD 0.43, ~ TE1 0.36, ~ RB1 0.00.
    TDLAT = {t_: rng.standard_normal(N).astype(np.float32) for t_ in lines}
    TD_LOAD = {"QB": 0.7, "WR": 0.6, "TE": 0.48, "RB": -0.3}
    def count_ppf(st_, pos_, lam_, u_):
        fit_ = DIST2.get(f"{st_}|{pos_}") or {}
        v_ = fit_.get("var_over_mean_gs", 1.0); lam_ = np.maximum(lam_, 1e-9); u_ = np.clip(u_, 1e-9, 1 - 1e-9)
        if v_ < 0.99:
            q_ = float(np.clip(1 - v_, 0.01, 0.99)); n_ = np.maximum(np.round(lam_ / q_), 1)
            return _binom.ppf(u_, n_, np.minimum(lam_ / n_, 1.0))
        return _poisson.ppf(u_, lam_)
    def _cv(st_, pos_, m_):
        B_ = (DIST2.get(f"{st_}|{pos_}") or {}).get("buckets")
        if not B_:
            return None
        r_ = np.array(next((b for b in B_ if m_ <= b["hi"]), B_[-1])["ratios"])
        return float(r_.std() / max(r_.mean(), 1e-6))
    if DIST2 and V2A:
        qbs_, recs_ = {}, {}
        for nm_, mks_ in (RAW.get("props") or {}).items():
            q_ = match(nm_, mks_)
            if not q_:
                continue
            py_, ry_ = mks_.get("player_pass_yds") or {}, mks_.get("player_reception_yds") or {}
            if q_["pos"] == "QB" and py_.get("line"):
                qbs_.setdefault(q_["team"], []).append((py_["line"], key(nm_)))
            if q_["pos"] in ("WR", "TE", "RB") and ry_.get("line"):
                cv_ = _cv("rec_yds", q_["pos"], max(ry_["line"], 1))
                if cv_:
                    recs_.setdefault(q_["team"], {})[key(nm_)] = cv_ * ry_["line"]
        lrng = np.random.default_rng(29)
        for tm_ in sorted(set(qbs_) | set(recs_)):
            eta_ = {k_: lrng.standard_normal(N).astype(np.float32) for k_ in sorted(recs_.get(tm_, {}))}
            LATR.update(eta_)
            if tm_ in qbs_:
                ql_, qk_ = max(qbs_[tm_]); cvq_ = _cv("pass_yds", "QB", ql_)
                if cvq_:
                    c_ = {k_: QB_LINK * sd_ / (cvq_ * ql_) for k_, sd_ in recs_.get(tm_, {}).items()}
                    s2_ = sum(v * v for v in c_.values())
                    if s2_ > 0.95:
                        c_ = {k_: v * math.sqrt(0.95 / s2_) for k_, v in c_.items()}; s2_ = 0.95
                    g_ = math.sqrt(1 - s2_) * lrng.standard_normal(N)
                    for k_, v in c_.items():
                        g_ = g_ + v * eta_[k_]
                    LATQ[qk_] = g_.astype(np.float32)
    def price_props(CORR, RAT):
        props, legs = [], []
        longs = LONGS if RAT is None else []
        for nm, mks in (RAW.get("props") or {}).items() if RAW.get("week") == int(cur or 0) or str(RAW.get("week")) == cur else []:
            p = match(nm, mks)
            if not p or p.get("team") not in sims or INJ.get(key(nm)) in OUTS or INJ.get(key(p["name"])) in OUTS:
                continue
            news = [q for q in NEWS.get(p["team"], []) if q["name"] != p["name"]]
            tm = p["team"]; own, opp, mar = sims[tm]
            pts_mean = float(np.mean(own))
            for mk, x in mks.items():
                if mk not in MK:
                    continue
                st, label = MK[mk]
                if st == "td" and p["pos"] == "QB":
                    st_fit = "td"
                if f"{st}|{p['pos']}" not in DIST:
                    continue
                k_ = kick.get(tm)
                if k_ and datetime.datetime.fromisoformat(k_.replace("Z", "+00:00")) <= now:
                    continue
                def proj(model):
                    om_ = None
                    if model is not None:
                        try:
                            om_ = model.our_mean(p, st)
                        except Exception:
                            om_ = None
                    if om_ is None:
                        om_ = our_mean(p, st)
                    sl_ = sl_mean(nm, st, tm)
                    if om_ is not None and sl_ is not None:
                        return float((1 - SLW) * om_ + SLW * sl_)
                    v_ = [v for v in (om_, sl_) if v is not None]
                    return float(v_[0]) if v_ else None
                ours_raw = proj(V2A)
                if ours_raw is None:
                    continue
                ours_m = ours_raw / CORR.get(st, 1.0)
                base_raw = proj(V2B) if news else ours_raw
                base_m = (base_raw if base_raw is not None else ours_raw) / CORR.get(st, 1.0)
                why_news, w_ = [], W
                if news and abs(ours_m - base_m) > 0.03 * max(base_m, 0.3):   # the model's own teammate-injury effect, measured on 2016-25
                    why_news = [f"{q['name']} is out" for q in news]
                if why_news:
                    w_ = 0.6   # the line was set before the news, so trust our number a lot more
                if st != "td" and (x.get("line") is None or x.get("p_over") is None):
                    continue
                if st == "td":
                    line = 0.5; p_mkt = min(0.97, x["p"])
                else:
                    line = x["line"]; p_mkt = x["p_over"]
                # game script: he does more when his team scores more
                bet_ = (DIST2.get(f"{st}|{p['pos']}") or {}).get("beta_gs") if V2A else None
                f = (np.maximum(own, 1) / max(1, pts_mean)) ** (bet_ if bet_ is not None else BETA[st])
                if st == "rush_yds" and p["pos"] == "RB":
                    f = f * (np.exp(V2A.RB_MARGIN * np.clip(mar, -21, 21)) if V2A else (1 + 0.008 * np.clip(mar, -21, 21)))
                f = f / f.mean()
                if st in ("pass_tds", "td", "rec"):   # counts: Poisson around his mean, mixed over the game sims
                    fs = f[:4000]; k = math.floor(line)
                    if V2A:   # receptions: negative binomial; TDs: binomial (pass TDs are under-dispersed once team points are known)
                        def P(m, fs=fs, line=line, whole=(line % 1 == 0), st=st, pos=p["pos"]):
                            ov = V2A.p_over_count(st, pos, m * fs, line)
                            if not whole:
                                return ov
                            push = V2A.p_over_count(st, pos, m * fs, line - 1) - ov
                            return ov / max(1e-9, 1 - push)
                    else:
                        def P(m, fs=fs, k=k, whole=(line % 1 == 0)):
                            lam = np.maximum(1e-9, m * fs); cdf = np.zeros_like(lam); term = np.exp(-lam); last = term
                            for i in range(k + 1):
                                cdf += term; last = term; term = term * lam / (i + 1)
                            ov = float(np.mean(1 - cdf))
                            return ov / max(1e-9, 1 - float(np.mean(last))) if whole else ov   # whole line: chance given no push
                else:                                  # yards: real past outcomes vs projection, times game script, same draws every time
                    lat_ = (LATQ if (st == "pass_yds" and p["pos"] == "QB") else LATR if (st == "rec_yds" and p["pos"] != "QB") else {}).get(key(nm))
                    ru = np.minimum(ndtr(lat_.astype(float)), 1 - 1e-9) if lat_ is not None else rng.random(N)   # same draws for every shape; latent = same-game link
                    def mkP(bm):
                        r_ = (V2A.ratios(st, p["pos"], max(bm, 1)) if V2A else None)
                        r_ = r_ if r_ is not None else ratios(st, p["pos"], max(bm, 1))
                        b_ = r_[(ru * len(r_)).astype(int)] * f
                        return b_, (lambda m, b_=b_, line=line: float(np.mean(m * b_ > line)))
                    base, P = mkP(line)
                def invert(P):
                    lo, hi = 0.005, max(8 * line, 10)
                    if P(hi) < p_mkt:
                        return None                    # the shape can't reach the market's chance: don't price it
                    for _ in range(45):
                        mid = (lo + hi) / 2
                        lo, hi = (mid, hi) if P(mid) < p_mkt else (lo, mid)
                    return (lo + hi) / 2
                mm = invert(P)
                if mm is not None and st not in ("pass_tds", "td", "rec"):
                    base, P = mkP(mm); mm = invert(P)   # second pass: shape for the market's own average
                if mm is None:
                    continue
                if RAT is not None:
                    if mm > 0.05:
                        RAT.setdefault(st, []).append(ours_raw / mm)
                    continue
                fin = mm + W * (base_m - mm) + 0.8 * (ours_m - base_m)   # news part trusted, the rest at W
                big_gap = bool(abs(ours_m - mm) > MAX_GAP * max(mm, 0.5)) and not why_news
                po = P(fin)
                if st == "rec" and V2A and p["pos"] != "QB" and key(nm) in LATR:   # same latent as his rec yds (QB link), NB marginal unchanged
                    r_nb = float((DIST2.get(f"rec|{p['pos']}") or {}).get("nb_r_gs", 10.0)); lam_ = np.maximum(fin * f, 1e-9)
                    u_ = ndtr(REC_RHO * LATR[key(nm)].astype(float) + math.sqrt(1 - REC_RHO ** 2) * rng.standard_normal(N))
                    X = nbinom.ppf(np.clip(u_, 1e-9, 1 - 1e-9), r_nb, r_nb / (r_nb + lam_))
                elif V2A and tm in TDLAT and ((st == "pass_tds" and p["pos"] == "QB") or (st == "td" and p["pos"] in ("WR", "TE", "RB"))):
                    ld_ = TD_LOAD["QB" if st == "pass_tds" else p["pos"]]
                    u_ = ndtr(ld_ * TDLAT[tm].astype(float) + math.sqrt(1 - ld_ ** 2) * rng.standard_normal(N))
                    X = count_ppf(st, p["pos"], np.maximum(0, fin * f), u_)
                else:
                    X = (V2A.sample_count(st, p["pos"], np.maximum(0, fin * f), rng) if V2A else rng.poisson(np.maximum(0, fin * f))) if st in ("pass_tds", "td", "rec") else fin * base
                if RAT is None and not big_gap:
                    kp_ = (KAL.get("props") or {}).get(key(nm)) or {}
                    for q_ in kp_.get(label, []):
                        sk_ = q_.get("strike")
                        if sk_ is None:
                            continue
                        py_ = float(np.mean(X > sk_))
                        for side_k, pk_, c_ in (("Yes", py_, q_.get("ask")), ("No", 1 - py_, q_.get("no_ask"))):
                            if not c_ or not 0.03 <= c_ <= 0.97:
                                continue
                            ev_k = kbuy(pk_, c_)
                            n_ = int(round(sk_ + 0.5))
                            KBUY.append({"label": f"{nm} {n_}+ {label.lower()}" if side_k == "Yes" else f"{nm} under {n_} {label.lower()}", "side": side_k, "p": round(100 * pk_, 1),
                                         "cost": round(100 * c_), "fee": round(100 * kfee(c_), 1), "ev": round(100 * ev_k, 1), "edge": round(100 * (pk_ - c_), 1), "kick": k_,
                                         "ticker": q_.get("ticker"), "type": "prop", "player": nm, "team": tm, "market": label, "strike": sk_, "spread": round(100 * ((q_.get("ask") or 0) - (q_.get("bid") or 0))) if q_.get("bid") else None,
                                         "game": "-".join(sorted((tm, lines[tm]["opp"]))), "news": why_news})
                if not big_gap and st != "td":
                    # alt lines he hits about 75-82% of the time, for parlays
                    for side_, q in (("Over", 0.28), ("Over", 0.22), ("Over", 0.17), ("Under", 0.72), ("Under", 0.78), ("Under", 0.83)):
                        v = float(np.quantile(X, q))
                        alt = (math.floor(v) - 0.5) if side_ == "Over" else (math.ceil(v) + 0.5)
                        if side_ == "Over" and alt < 0.5:
                            continue
                        ind = (X > alt) if side_ == "Over" else (X < alt)
                        pa = float(ind.mean())
                        if 0.66 <= pa <= 0.86:
                            legs.append({"lean": (po - p_mkt) * (1 if side_ == "Over" else -1), "label": f"{nm} {side_} {alt:g} {label.lower()}", "game": tuple(sorted((tm, lines[tm]["opp"]))), "who": nm, "p": pa, "ind": ind, "type": "prop", "kick": k_})
                    # the low alt lines (hit 86-94%) for the +200-300 ladders
                    for side_, q in (("Over", 0.15), ("Over", 0.12), ("Over", 0.09), ("Under", 0.85), ("Under", 0.88), ("Under", 0.91)):
                        v = float(np.quantile(X, q))
                        alt = (math.floor(v) - 0.5) if side_ == "Over" else (math.ceil(v) + 0.5)
                        if side_ == "Over" and alt < 0.5:
                            continue
                        ind = (X > alt) if side_ == "Over" else (X < alt)
                        pa = float(ind.mean())
                        if 0.84 <= pa <= 0.92 and RAT is None:
                            SAFE.append({"lean": (po - p_mkt) * (1 if side_ == "Over" else -1), "label": f"{nm} {side_} {alt:g} {label.lower()}", "game": tuple(sorted((tm, lines[tm]["opp"]))), "who": nm, "p": pa, "ind": ind, "type": "prop", "kick": k_})
                if not big_gap and st != "td" and po > p_mkt:
                    # longshots: the big alt over (hits 15-30%) on guys the sims like more than the market
                    for q in (0.75, 0.82):
                        v = float(np.quantile(X, q)); alt = math.floor(v) + 0.5
                        ind = X > alt; pa = float(ind.mean())
                        if 0.12 <= pa <= 0.32:
                            longs.append({"label": f"{nm} Over {alt:g} {label.lower()}", "p": round(100 * pa, 1), "fair": amer(pa), "lean": round(100 * (po - p_mkt), 1), "kick": k_,
                                          "game": tuple(sorted((tm, lines[tm]["opp"]))), "who": nm, "ind": ind})
                if st == "td" and po > p_mkt and 0.12 <= po <= 0.40 and not big_gap:
                    longs.append({"label": f"{nm} anytime TD", "p": round(100 * po, 1), "fair": amer(po), "lean": round(100 * (po - p_mkt), 1), "kick": k_, "price": x["yes"],
                                  "game": tuple(sorted((tm, lines[tm]["opp"]))), "who": nm, "ind": X >= 1})
                dk_ = x.get("dk") or {}
                if st == "td":
                    cand = [("Yes", po, x["yes"])]
                else:
                    cand = [("Over", po, x["over"]), ("Under", 1 - po, x["under"])]
                def p_line(L_):   # our chance of the over at any number (DraftKings may hang a different one), pushes taken out
                    if st in ("pass_tds", "td", "rec"):
                        if V2A:
                            ov = V2A.p_over_count(st, p["pos"], fin * fs, L_)
                            return ov if L_ % 1 else ov / max(1e-9, 1 - (V2A.p_over_count(st, p["pos"], fin * fs, L_ - 1) - ov))
                        from scipy import stats as ss_
                        lam = np.maximum(1e-9, fin * fs); ov = float(np.mean(ss_.poisson.sf(math.floor(L_), lam)))
                        return ov if L_ % 1 else ov / max(1e-9, 1 - float(np.mean(ss_.poisson.pmf(int(L_), lam))))
                    return float(np.mean(fin * base > L_))
                dkl = 0.5 if st == "td" else dk_.get("line")
                dko, dku = (dk_.get("yes"), dk_.get("no")) if st == "td" else (dk_.get("over"), dk_.get("under"))
                dk_po = (po if dkl == line else p_line(dkl)) if (dkl is not None and dko) else None
                dk_nv = (imp_p(dko) / (imp_p(dko) + imp_p(dku))) if (dko and dku) else (p_mkt if dkl == line else None)
                stp = 1.0 if st in ("pass_tds", "td", "rec") else max(1.0, round(0.05 * line))
                dpdl = None if st == "td" else round((p_line(line - stp) - p_line(line + stp)) / (2 * stp), 5)   # over's chance lost per unit of line: CLV when the line moves
                for side, pp, price in cand:
                    ev = pp * dec(price) - 1
                    pm_ = p_mkt if side in ("Over", "Yes") else 1 - p_mkt
                    ov_ = side in ("Over", "Yes")
                    dpr = (dko if ov_ else dku) if dk_po is not None else None
                    dps = (dk_po if ov_ else 1 - dk_po) if dpr else None
                    dpm = (dk_nv if ov_ else 1 - dk_nv) if (dpr and dk_nv is not None) else None
                    props.append({"uname": p["name"], "event": x.get("event"), "dpdl": dpdl, "dk_line": dkl if dpr else None, "dk_price": dpr,
                                  "dk_p": round(100 * dps, 1) if dpr else None, "dk_pm": round(100 * dpm, 1) if dpm is not None else None,
                                  "dk_edge": round(100 * (dps - dpm), 1) if dpm is not None else None, "dk_ev": round(100 * (dps * dec(dpr) - 1), 1) if dpr else None,
                                  "news": why_news, "edge": round(100 * (pp - pm_), 1), "max_c": int(100 * pp - 2), "pickem": st != "td" and 100 * pp >= PICKEM and not big_gap, "big_gap": big_gap, "player": nm, "team": tm, "opp": lines[tm]["opp"], "pos": p["pos"], "market": label, "side": side, "line": line if st != "td" else None,
                                  "price": price, "dk": dpr if dkl == line else None, "books": x.get("books"), "p": round(100 * pp, 1), "p_mkt": round(100 * (p_mkt if side in ("Over", "Yes") else 1 - p_mkt), 1),
                                  "ev": round(100 * ev, 1), "ours": round(ours_m, 2), "mkt": round(mm, 2), "final": round(fin, 2), "kick": k_,
                                  "median": round(float(np.median(X)), 1), "p10": round(float(np.percentile(X, 10)), 1), "p90": round(float(np.percentile(X, 90)), 1)})
        return props, legs
    _rat = {}
    price_props({}, _rat)   # pass 1: how far our average sits from the market's average, by market
    CORR_RAW = {k: float(np.median(v)) for k, v in _rat.items() if len(v) >= 8}
    # remove 75% of the league-wide gap between our numbers and the market's: most of it is our bias, but if the market
    # itself leans one way on a whole stat (e.g. overs priced too high) the leftover 25% lets that show up, and the tracker tests it
    CORR = {k: v ** 0.75 for k, v in CORR_RAW.items()}
    props, plegs = price_props(CORR, None)   # pass 2: with that systematic gap removed
    ALLP = props   # both sides, for the tracker
    LEGS += plegs
    best = {}
    for x in props:   # one side per player-market
        k_ = (x["player"], x["market"])
        if k_ not in best or x["edge"] > best[k_]["edge"]:
            best[k_] = x
    props = sorted(best.values(), key=lambda x: (-x["edge"], -x["p"]))
    for x in props:
        x["grade"] = "" if x["big_gap"] or (x["market"] == "Anytime TD" and x["p"] < 20) else "A" if x["edge"] >= 6 else "B" if x["edge"] >= 4 else "C" if x["edge"] >= MIN_EDGE * 100 else ""

    # ---------------- parlays: 2 and 3 legs of 70%+ legs, chance from the same sims (same-game legs keep their correlation) ----------------
    import itertools
    LEGS.sort(key=lambda x: (-x["lean"], -x["p"]))   # legs our sims lean toward first
    pool = []
    seen_who, seen_lab = {}, set()
    for L_ in LEGS:   # keep variety: at most 2 legs per player/team, no repeats
        if L_["label"] in seen_lab:
            continue
        seen_lab.add(L_["label"])
        if seen_who.get(L_["who"], 0) < 2:
            pool.append(L_); seen_who[L_["who"]] = seen_who.get(L_["who"], 0) + 1
    pool = pool[:45]
    def joint(legs):
        by_g = {}
        for L_ in legs:
            by_g.setdefault(L_["game"], []).append(L_["ind"])
        pr = 1.0
        for inds in by_g.values():
            m = inds[0]
            for z in inds[1:]:
                m = m & z
            pr *= float(m.mean())
        return pr
    parlays = {2: [], 3: []}
    for n_ in (2, 3):
        cand = []
        lo_, hi_ = (0.52, 0.72) if n_ == 2 else (0.34, 0.52)   # 2-leg around -110 to -250, 3-leg around even money or better
        for combo in itertools.combinations(pool[:32 if n_ == 3 else 45], n_):
            if len({c["who"] for c in combo}) < n_:
                continue
            if max(sum(1 for c in combo if c["game"] == g) for g in {c["game"] for c in combo}) > 2 or conflict(combo):
                continue
            pj = joint(combo); naive = math.prod(c["p"] for c in combo)
            if not lo_ <= pj <= hi_:
                continue
            cand.append((pj, pj / naive, combo))
        # best = legs we lean toward + legs that help each other (same game) + higher chance
        cand.sort(key=lambda z: -(sum(c["lean"] for c in z[2]) + 0.5 * (z[1] - 1) + 0.15 * z[0]))
        used = {}
        for pj, lift, combo in cand:
            if any(used.get(c["label"], 0) >= 2 for c in combo):
                continue
            for c in combo:
                used[c["label"]] = used.get(c["label"], 0) + 1
            parlays[n_].append({"p": round(100 * pj, 1), "fair": amer(pj), "lift": round(lift, 3), "same_game": len({c["game"] for c in combo}) < n_,
                                "legs": [{"label": c["label"], "p": round(100 * c["p"], 1), "type": c["type"], "lean": round(100 * c["lean"], 1)} for c in combo]})
            if len(parlays[n_]) >= 8:
                break
    # game-line parlays: 3 or 4 games, one leg each (different games = independent), paying +100 or better
    gpool = [L_ for L_ in LEGS if L_["type"] == "game"]
    by_game = {}
    for L_ in gpool:
        by_game.setdefault(L_["game"], []).append(L_)
    for n_ in (3, 4):
        cand = []
        gl = sorted(gpool, key=lambda z: (-z["lean"], -z["p"]))
        for combo in itertools.combinations(gl[:40], n_):
            if len({c["game"] for c in combo}) < n_:
                continue
            pj = math.prod(c["p"] for c in combo)
            kinds = [c["kindg"] for c in combo]
            if 0.34 <= pj <= 0.50 and max(kinds.count(k) for k in set(kinds)) <= 2:
                cand.append((pj, combo))
        # mix of bet types reads better and spreads the risk; then the highest chance wins
        cand.sort(key=lambda z: -(z[0] + sum(c["lean"] for c in z[1])))
        used, outp = {}, []
        for pj, combo in cand:
            if any(used.get(c["label"], 0) >= 2 for c in combo):
                continue
            for c in combo:
                used[c["label"]] = used.get(c["label"], 0) + 1
            outp.append({"p": round(100 * pj, 1), "fair": amer(pj), "lift": 1.0, "same_game": False, "units": 0.5,
                         "legs": [{"label": c["label"], "p": round(100 * c["p"], 1), "type": "game", "lean": round(100 * c["lean"], 1)} for c in combo]})
            if len(outp) >= 6:
                break
        parlays[f"g{n_}"] = outp
    # model's confident parlays: only legs our sims like MORE than the market (lean 2+ pts) and that hit 70%+, paying +150 to +400
    hot = [L_ for L_ in pool if L_["lean"] >= 0.02 and L_["p"] >= 0.70]
    conf = []
    for n_ in (3, 4, 5):
        for combo in itertools.combinations(hot[:24], n_):
            if len({c["who"] for c in combo}) < n_ or max(sum(1 for c in combo if c["game"] == g) for g in {c["game"] for c in combo}) > 2 or conflict(combo):
                continue
            pj = joint(combo)
            if 0.20 <= pj <= 0.40:
                conf.append((sum(c["lean"] for c in combo), pj, combo))
    conf.sort(key=lambda z: -(z[0] + 0.3 * z[1]))
    used = {}
    parlays["conf"] = []
    for le, pj, combo in conf:
        if any(used.get(c["label"], 0) >= 1 for c in combo):
            continue
        for c in combo:
            used[c["label"]] = 1
        parlays["conf"].append({"p": round(100 * pj, 1), "fair": amer(pj), "lift": 1.0, "same_game": len({c["game"] for c in combo}) < len(combo), "lean": round(100 * le, 1),
                                "legs": [{"label": c["label"], "p": round(100 * c["p"], 1), "type": c["type"], "lean": round(100 * c["lean"], 1)} for c in combo]})
        if len(parlays["conf"]) >= 4:
            break
    import base64
    bits = lambda L_: base64.b64encode(np.packbits(L_["ind"][:4096]).tobytes()).decode()
    # longshot parlays for fun: 2 or 3 of the longshot singles (big alt overs, anytime TDs), priced together from the sims
    LONGS.sort(key=lambda z: -(z["lean"] + 0.05 * z["p"]))
    _seen = set(); LONGS[:] = [L_ for L_ in LONGS if not (L_["label"] in _seen or _seen.add(L_["label"]))]
    LS = [dict(L_, p_=L_["p"] / 100) for L_ in LONGS[:20]]
    lp = []
    for n_ in (2, 3):
        for combo in itertools.combinations(LS, n_):
            if len({c["who"] for c in combo}) < n_:
                continue
            by_g = {}
            for c in combo:
                by_g.setdefault(c["game"], []).append(c["ind"])
            pj = 1.0
            for inds in by_g.values():
                m_ = inds[0]
                for z in inds[1:]:
                    m_ = m_ & z
                pj *= float(m_.mean())
            if (0.03 if n_ == 2 else 0.01) <= pj <= 0.15:
                lp.append((sum(c["lean"] for c in combo), pj, n_, combo))
    lp.sort(key=lambda z: -(z[0] + 30 * z[1])); used = {}; long_parlays = []; per_n = {2: 0, 3: 0}
    for sc_, pj, n_, combo in lp:
        if per_n[n_] >= 3 or any(used.get(c["label"], 0) >= 2 for c in combo):
            continue
        per_n[n_] += 1
        for c in combo:
            used[c["label"]] = used.get(c["label"], 0) + 1
        long_parlays.append({"p": round(100 * pj, 1), "fair": amer(pj), "legs": [{"label": c["label"], "p": c["p"], "lean": c["lean"]} for c in combo]})
        if len(long_parlays) >= 6:
            break
    LONGS.sort(key=lambda z: -(z["lean"] + 0.05 * z["p"]))
    # +200-300 ladders: 5-8 low alt lines (86-94% each), props + game lines, max 2 per game, priced together from the sims
    SAFE_ = []
    _sl = set()
    for L_ in sorted([z for z in SAFE if z["kick"] and z["kick"] > now.isoformat()], key=lambda z: (-(z["lean"] >= 0.01), abs(z["p"] - 0.86), -z["lean"])):
        if L_["label"] not in _sl:
            _sl.add(L_["label"]); SAFE_.append(L_)
    ladders, used_l = [], {}
    for start in range(min(len(SAFE_), 40)):
        pick_ = [SAFE_[start]]
        if used_l.get(pick_[0]["label"], 0) >= 1:
            continue
        for L_ in SAFE_:
            if L_ in pick_ or any(L_["who"] == c["who"] for c in pick_) or sum(1 for c in pick_ if c["game"] == L_["game"]) >= 2 or used_l.get(L_["label"], 0) >= 1:
                continue
            if L_["type"] == "game" and any(c["game"] == L_["game"] and c["type"] == "game" for c in pick_):
                continue   # no over + under of one total, or both sides of one spread
            trial = pick_ + [L_]
            if joint(trial) < 0.25:
                continue
            pick_ = trial
            if len(pick_) >= 10 or joint(pick_) <= 0.33:
                break
        pj = joint(pick_)
        if 5 <= len(pick_) <= 10 and 0.25 <= pj <= 0.33 and frozenset(c["label"] for c in pick_) not in {frozenset(l["label"] for l in x["legs"]) for x in ladders}:
            for c in pick_:
                used_l[c["label"]] = used_l.get(c["label"], 0) + 1
            ladders.append({"p": round(100 * pj, 1), "fair": amer(pj), "lift": 1.0, "same_game": len({c["game"] for c in pick_}) < len(pick_), "ladder": True,
                            "legs": [{"label": c["label"], "p": round(100 * c["p"], 1), "type": c["type"], "lean": round(100 * c["lean"], 1)} for c in pick_]})
        if len(ladders) >= 6:
            break
    parlays["ladder"] = ladders
    # best 4-leg parlays at +200 to +300. The math: a parlay's real value = (chance it hits) x (payout). Books price each leg with
    # their margin, so every extra leg costs you; the way to beat that is legs the sims like MORE than the market (lean) and
    # same-game legs that help each other (lift > 1 = they hit together more often than the book's multiplied price assumes).
    # Score = total lean + correlation lift, only combos that land at 25-33% (+200 to +300).
    best4, cand4 = [], []
    _s4 = set(); p4 = []
    for L_ in sorted(LEGS, key=lambda z: (-z["lean"], -z["p"])):   # legs at 66-80%: four of them land near +200 to +300
        if 0.66 <= L_["p"] <= 0.80 and L_["label"] not in _s4 and (L_["kick"] or "") > now.isoformat():
            _s4.add(L_["label"]); p4.append(L_)
    p4 = p4[:36]
    for combo in itertools.combinations(p4, 4):
        if len({c["who"] for c in combo}) < 4 or max(sum(1 for c in combo if c["game"] == g) for g in {c["game"] for c in combo}) > 2:
            continue
        pj = joint(combo)
        if 0.25 <= pj <= 0.33:
            naive = math.prod(c["p"] for c in combo); lift = pj / naive
            cand4.append((sum(c["lean"] for c in combo) + 0.6 * math.log(lift), pj, lift, combo))
    cand4.sort(key=lambda z: -z[0]); used4 = {}
    for sc_, pj, lift, combo in cand4:
        if any(used4.get(c["label"], 0) >= 1 for c in combo):
            continue
        for c in combo:
            used4[c["label"]] = 1
        nl = sum(1 for c in combo if c["lean"] >= 0.02)
        why = [f"{nl} of 4 legs are ones the sims like more than the market"] if nl else ["No leg has a model lean: this one is a fair-price check"]
        if lift > 1.01:
            why.append(f"same-game legs help each other: hits {round(100 * (lift - 1))}% more often than if they were unrelated")
        best4.append({"p": round(100 * pj, 1), "fair": amer(pj), "lift": round(lift, 3), "same_game": lift > 1.001, "why": why,
                      "legs": [{"label": c["label"], "p": round(100 * c["p"], 1), "type": c["type"], "lean": round(100 * c["lean"], 1)} for c in combo]})
        if len(best4) >= 4:
            break
    parlays["best4"] = best4
    # favorite stacks: 4-6 heavy favorites / safe alt spreads (one per game) that add up to about +250 to +400
    fs_ = []
    favml = []
    for g in games:   # every favorite's moneyline that wins 60%+ in the sims
        if g["started"]:
            continue
        hw = g["sim"]["home_win"] / 100; fav = g["home"] if hw >= 0.5 else g["away"]; pf = max(hw, 1 - hw)
        if pf >= 0.60:
            favml.append({"label": f"{fav} moneyline", "p": pf, "game": tuple(sorted((g["home"], g["away"]))), "kindg": "ml"})
    gl_ = favml + sorted([L_ for L_ in gpool if L_["kindg"] == "spread" and L_["p"] >= 0.75], key=lambda z: -z["p"])[:10]   # every favorite ML, plus the safest alt spreads
    for n_ in (4, 5, 6):
        for combo in itertools.combinations(gl_, n_):
            if len({c["game"] for c in combo}) < n_:
                continue
            pj = math.prod(c["p"] for c in combo)
            if 0.20 <= pj <= 0.32:
                fs_.append((pj, n_, combo))
    fs_.sort(key=lambda z: (-sum(1 for c in z[2] if "moneyline" in c["label"]), -z[0]))   # prefer straight moneylines, then the safest
    fav_stacks, used = [], {}
    for pj, n_, combo in fs_:
        if any(used.get(c["label"], 0) >= 2 for c in combo):
            continue
        for c in combo:
            used[c["label"]] = used.get(c["label"], 0) + 1
        fav_stacks.append({"p": round(100 * pj, 1), "fair": amer(pj), "legs": [{"label": c["label"], "p": round(100 * c["p"], 1), "lean": 0} for c in combo]})
        if len(fav_stacks) >= 4:
            break
    ml_all = sorted(favml, key=lambda z: -z["p"])
    ml_long = []
    for n_ in (len(ml_all), 8, 6):
        if n_ >= 4 and n_ <= len(ml_all):
            legs_ = ml_all[:n_]; pj = math.prod(c["p"] for c in legs_)
            ml_long.append({"p": round(100 * pj, 1), "fair": amer(pj), "n": n_, "legs": [{"label": c["label"], "p": round(100 * c["p"], 1), "lean": 0} for c in legs_]})
    longshots = {"ml_long": ml_long, "fav_stacks": fav_stacks, "singles": [{k: v for k, v in L_.items() if k not in ("ind", "game", "who")} for L_ in LONGS[:30]], "parlays": long_parlays}
    legs_out = [{"label": L_["label"], "p": round(100 * L_["p"], 1), "fair": amer(L_["p"]), "type": L_["type"], "kick": L_["kick"], "lean": round(100 * L_["lean"], 1),
                 "game": "-".join(L_["game"]), "bits": bits(L_)} for L_ in pool]

    # ---------------- record: save picks until kickoff, grade after ----------------
    PK = load(D / "props_picks.json", {}) or {}
    wk = PK.setdefault(cur, {})
    live = {f"{x['player']}|{x['market']}" for x in props if x["grade"]}
    for k_old in [k for k, v in wk.items() if not k.startswith(("G|", "GA|", "PA|")) and k not in live and not v.get("graded") and v.get("kick") and v["kick"] > now.isoformat()]:
        del wk[k_old]
    for x in props:
        if x["grade"]:
            wk[f"{x['player']}|{x['market']}"] = {k: x[k] for k in ("player", "uname", "team", "market", "side", "line", "price", "p", "p_mkt", "edge", "grade", "kick")}
    for g in games:
        if not g["started"]:   # an angle that went away before kickoff (forecast changed): drop its old pick
            live_ = {f"G|{g['home']}|{pl['kind']}|{pl['pick'].split()[0]}" for pl in g["plays"]}
            for k_old in [k for k, v in wk.items() if k.startswith(f"G|{g['home']}|") and k not in live_ and not v.get("graded")]:
                del wk[k_old]
        for pl in g["plays"]:
            if not g["started"]:
                for k_old in [k for k, v in wk.items() if k.startswith(f"G|{g['home']}|") and v.get("kind") == pl["kind"] and v.get("result") is None and not v.get("graded")]:
                    del wk[k_old]
                wk[f"G|{g['home']}|{pl['kind']}|{pl['pick'].split()[0]}"] = {"game": f"{g['away']} @ {g['home']}", "home": g["home"], "pick": pl["pick"], "kind": pl["kind"],
                                                    "p": pl["p"], "p_mkt": pl["p_mkt"], "edge": pl["edge"], "spread": g["spread"], "total": g["total"], "kick": g["kick"]}
    nowi = now.isoformat(timespec="seconds")
    for k_old in [k for k, v in wk.items() if k.startswith(("GA|", "PA|")) and v.get("v") != 2 and not v.get("graded") and (v.get("kick") or "9") > nowi]:
        del wk[k_old]   # v1 tracker entries (wrong prices, never locked) for games that haven't started
    locked = lambda k, tier: k in wk and wk[k].get("v") == 2 and not (wk[k].get("tier") in ("lean", "best") and tier == "bet")
    for g in games:   # every-bet tracker: game lines, locked the first time a market qualifies
        if not g["started"]:
            for au in g.get("auto", []):
                ka = f"GA|{g['home']}|{au['kind']}"
                if not locked(ka, au["tier"]):
                    wk[ka] = {"v": 2, "game": f"{g['away']} @ {g['home']}", "home": g["home"], "spread": g["spread"], "total": g["total"], "kick": g["kick"], "auto": True, "at": nowi} | \
                             {k: au[k] for k in ("pick", "kind", "tag", "pt", "price", "src", "p", "p_mkt", "edge", "ev", "tier", "bucket")}
    TRK = {}   # one side per player-market, chosen by DraftKings' edge when DK has the prop (DK's line can flip which side is better)
    for x in ALLP:
        k2, sc = (x["player"], x["market"]), ((1, x["dk_edge"]) if x.get("dk_edge") is not None else (0, x["edge"]))
        if k2 not in TRK or sc > TRK[k2][0]:
            TRK[k2] = (sc, x)
    for _, x in TRK.values():   # every-bet tracker: props 1+ pt better than DraftKings' no-vig chance, -150 or better, at DK's own line and price
        if not (x["kick"] and x["kick"] > nowi) or x["big_gap"]:
            continue
        if x.get("dk_price") and x.get("dk_edge") is not None:
            t_ = dict(src="dk", line=x["dk_line"], price=int(x["dk_price"]), p=x["dk_p"], p_mkt=x["dk_pm"], edge=x["dk_edge"], ev=x["dk_ev"])
            t_["tier"] = "bet" if t_["ev"] >= 0 else "lean"
        else:   # no DraftKings line in the pull: best price of the consensus books, kept OUT of the headline (no one gets the best of 5 every time)
            t_ = dict(src="best", line=x["line"], price=int(x["price"]), p=x["p"], p_mkt=x["p_mkt"], edge=x["edge"], ev=x["ev"], tier="best")
        if EXCHANGE and t_.get("p_mkt") is not None:   # price it as an exchange share at the market's chance
            t_["price"] = ex_price(t_["p_mkt"] / 100); t_["ev"] = round(100 * (t_["p"] / 100 * dec(t_["price"]) - 1), 1)
            t_["src"] = t_["src"] + "_ex"
            t_["tier"] = "bet" if t_["ev"] >= 0 else "lean"   # on an exchange the market's no-vig chance IS the price, so every line counts
        if t_["price"] >= -150 and t_["edge"] >= 1.0:
            kp = f"PA|{x['player']}|{x['market']}"
            if not locked(kp, t_["tier"]):
                wk[kp] = {k: x[k] for k in ("player", "uname", "team", "market", "side", "kick", "event", "dpdl")} | t_ | {"v": 2, "news": bool(x["news"]), "auto": True, "at": nowi}
    liq = lambda x: x.get("spread") is None or x["spread"] <= 6                # skip rungs with a wide bid/ask gap (thin market)
    def best_per(L, k=2):   # at most k rungs per player/game so one guy's ladder doesn't fill the list
        out_, c_ = [], {}
        for x in L:
            g_ = x.get("player") or x["label"].split()[0] + x.get("kind", "")
            if c_.get(g_, 0) < k:
                out_.append(x); c_[g_] = c_.get(g_, 0) + 1
        return out_
    kal_out = {"at": KAL.get("at"), "n": len(KBUY),
               "buys": best_per(sorted([x for x in KBUY if 20 <= x["p"] <= 80 and x["edge"] >= 3 and x["ev"] >= 4 and liq(x)], key=lambda x: -x["edge"]))[:40],
               "safe": best_per(sorted([x for x in KBUY if x["p"] > 80 and x["edge"] >= 2.5 and x["ev"] >= 2 and liq(x)], key=lambda x: -x["edge"]))[:20],
               "longs": best_per(sorted([x for x in KBUY if 5 <= x["p"] < 20 and x["ev"] >= 20 and x["edge"] >= 2 and liq(x)], key=lambda x: -x["ev"]), 1)[:15]}
    for x in kal_out["buys"] + kal_out["safe"]:   # Kalshi tracker: 1 unit at the real share price + fee, locked the first time it shows
        kk = f"KA|{x['ticker']}|{x['side']}"
        if kk not in wk and x.get("kick") and x["kick"] > now.isoformat():
            wk[kk] = {k: x.get(k) for k in ("label", "side", "p", "cost", "fee", "ev", "edge", "kick", "type", "kind", "player", "team", "market", "strike", "home", "away")} | {"kal": True, "at": now.isoformat()}
    stat_by = {}
    for v in (U.get("players") or {}).values():
        for w in v.get("weeks", []):
            stat_by[(key(v["name"]), str(w["w"]))] = w
    box_in = {(v.get("team"), str(w["w"])) for v in (U.get("players") or {}).values() for w in v.get("weeks", []) if "pts" in w}
    rec = {"props": [0, 0, 0.0], "games": [0, 0, 0.0], "auto_games": [0, 0, 0.0], "auto_props": [0, 0, 0.0], "kalshi": [0, 0, 0.0], "weeks": {}}
    PC = load(D / "props_close.json", {}) or {}
    MKR = {"Pass yds": "player_pass_yds", "Pass TDs": "player_pass_tds", "Rush yds": "player_rush_yds", "Rec yds": "player_reception_yds", "Catches": "player_receptions", "Anytime TD": "td"}
    def game_clv(x, Lh, La):
        """Closing (nflverse, last line before kickoff) no-vig chance of OUR bet at OUR number, and the EV that implies at the price we got."""
        nv = lambda a_, b_: imp_p(a_ or -110) / (imp_p(a_ or -110) + imp_p(b_ or -110))
        hs = x["tag"] in ("home", "over")
        if x["kind"] == "ml":
            if not (Lh.get("ml") and La.get("ml")):
                return None
            q = nv(Lh["ml"], La["ml"]); q = q if hs else 1 - q
        else:
            cs, ct = Lh.get("spread"), Lh.get("total")
            if cs is None or ct is None:
                return None
            mar_c, tot_c = sample_game(cs, ct, resid, pool_g, rng, 20000, cover=nv(Lh.get("spread_odds"), La.get("spread_odds")), over=nv(Lh.get("over_odds"), Lh.get("under_odds")))
            v = ((mar_c if hs else -mar_c) + x["pt"]) if x["kind"] == "spr" else ((tot_c - x["pt"]) if hs else (x["pt"] - tot_c))
            q = min(0.99, max(0.01, float(np.mean(v > 0)) / max(1e-9, float(np.mean(v != 0)))))   # closing market's chance at OUR number
        return {"q": round(100 * q, 1), "ev": round(100 * (q * dec(x["price"]) - 1), 1), "pts": round(100 * q - x["p_mkt"], 1),
                "close": Lh.get("ml") if x["kind"] == "ml" else Lh.get("spread") if x["kind"] == "spr" else Lh.get("total")}
    def prop_clv(w_, x):
        c = ((PC.get("props") or {}).get(x.get("event")) or {}).get(x["player"], {}).get(MKR[x["market"]]) if str(PC.get("week")) == w_ else None
        if not c:
            return None
        if x["market"] == "Anytime TD":
            if not (c.get("yes") and c.get("no")):
                return None
            qo = imp_p(c["yes"]) / (imp_p(c["yes"]) + imp_p(c["no"]))
        else:
            if c.get("line") is None or not (c.get("over") and c.get("under")) or (c["line"] != x["line"] and x.get("dpdl") is None):
                return None
            qo = imp_p(c["over"]) / (imp_p(c["over"]) + imp_p(c["under"])) + (x.get("dpdl") or 0) * (c["line"] - x["line"])
        q = min(0.99, max(0.01, qo if x["side"] in ("Over", "Yes") else 1 - qo))
        return {"q": round(100 * q, 1), "ev": round(100 * (q * dec(x["price"]) - 1), 1), "pts": round(100 * q - x["p_mkt"], 1), "close": c.get("line")}
    EB = lambda e: "1-2.5" if e < 2.5 else "2.5-4" if e < 4 else "4-6" if e < 6 else "6+"
    TR = {}
    def tally(k, x):
        g_ = k.startswith("GA|")
        names = ["all games" if g_ else "all props", f"{'games' if g_ else 'props'} {x['tier']}s"]
        names += ([f"{x['kind']} {x['bucket']}"] if g_ else [x["market"], f"edge {EB(x['edge'] or 0)}"] + (["news"] if x.get("news") else []))
        for nm_ in names:
            s = TR.setdefault(nm_, dict(w=0, l=0, push=0, open=0, u=0.0, var=0.0, pm=0.0, po=0.0, ev=0.0, cn=0, cs=0.0, cq=0.0))
            if not x.get("graded"):
                s["open"] += 1; continue
            if x.get("result") is None:
                s["push"] += 1; continue
            d_, pm = dec(x["price"]), (x.get("p_mkt") or 50) / 100
            s["w" if x["result"] else "l"] += 1; s["u"] += (d_ - 1) if x["result"] else -1
            s["var"] += pm * (1 - pm) * d_ * d_; s["pm"] += pm; s["po"] += (x.get("p") or 50) / 100; s["ev"] += (x.get("ev") or 0) / 100
            if x.get("clv"):
                s["cn"] += 1; s["cs"] += x["clv"]["ev"] / 100; s["cq"] += (x["clv"]["ev"] / 100) ** 2
    for w_, picks in PK.items():
        for k, x in picks.items():
            if k.startswith("KA|"):   # Kalshi buys: grade from final scores / box scores, profit at the real share price
                if x.get("result") is None:
                    if x["type"] == "game":
                        Lh = (U.get("lines") or {}).get(w_, {}).get(x["home"], {}); La = (U.get("lines") or {}).get(w_, {}).get(x["away"], {})
                        if Lh.get("pts") is None or La.get("pts") is None:
                            continue
                        hm, tt = Lh["pts"] - La["pts"], Lh["pts"] + La["pts"]
                        lab = x["label"]; tm0 = lab.split()[0]
                        mm_ = hm if tm0 == x["home"] else -hm
                        if x["kind"] == "ml":
                            x["result"] = None if hm == 0 else mm_ > 0
                        elif x["kind"] in ("spread", "spread_no"):
                            sk = float(lab.split(" by ")[-1].replace("+", "")) - 0.5 if "by" in lab else None
                            sk = sk if sk is not None else 0
                            x["result"] = (mm_ > sk) if x["kind"] == "spread" else not (mm_ > sk)
                        else:
                            sk = float(lab.split()[-1])
                            x["result"] = (tt > sk) if x["kind"] == "total" else not (tt > sk)
                        x["graded"] = True
                    else:
                        if (x["team"], w_) not in box_in:
                            continue
                        s_ = stat_by.get((key(x["player"]), w_))
                        if not s_:
                            continue
                        stv = {"Pass yds": s_.get("passyd", 0), "Pass TDs": s_.get("ptd", 0), "Rush yds": s_.get("rushyd", 0), "Rec yds": s_.get("recyd", 0),
                               "Catches": s_.get("rec", 0), "Anytime TD": (s_.get("rutd", 0) or 0) + (s_.get("retd", 0) or 0)}[x["market"]]
                        x["actual"] = stv; x["result"] = (stv > x["strike"]) == (x["side"] == "Yes"); x["graded"] = True
                if x.get("graded") and x.get("result") is not None:
                    c_ = (x["cost"] + x["fee"]) / 100
                    rec["kalshi"][0 if x["result"] else 1] += 1; rec["kalshi"][2] += (1 / c_ - 1) if x["result"] else -1
                continue
            if x.get("result") is None:
                if k.startswith(("G|", "GA|")):
                    Lh = (U.get("lines") or {}).get(w_, {}).get(x["home"], {})
                    La = (U.get("lines") or {}).get(w_, {}).get(Lh.get("opp"), {})
                    if Lh.get("pts") is None or La.get("pts") is None:
                        continue
                    m_ = Lh["pts"] - La["pts"]; t_ = Lh["pts"] + La["pts"]
                    pk = x["pick"]
                    if x.get("v") == 2:   # tracker v2: graded at the number it was bet at (DraftKings' number, not the consensus one)
                        sg = 1 if x["tag"] in ("home", "over") else -1
                        v_ = (sg * m_ + x["pt"]) if x["kind"] == "spr" else (sg * (t_ - x["pt"])) if x["kind"] == "tot" else sg * m_
                        x["result"] = None if v_ == 0 else bool(v_ > 0)
                        x["clv"] = game_clv(x, Lh, La)
                    elif pk.startswith("Over") or pk.startswith("Under"):
                        x["result"] = None if t_ == x["total"] else ((t_ > x["total"]) == pk.startswith("Over"))
                    elif "moneyline" in pk:
                        x["result"] = None if m_ == 0 else ((m_ > 0) == (pk.split()[0] == x["home"]))
                    else:
                        cover = m_ - x["spread"]
                        x["result"] = None if cover == 0 else ((cover > 0) == (pk.split()[0] == x["home"]))
                    x["graded"] = True
                else:
                    if (x["team"], w_) not in box_in or (U.get("lines") or {}).get(w_, {}).get(x["team"], {}).get("pts") is None:
                        continue   # box score for his game isn't in yet
                    s_ = stat_by.get((key(x.get("uname") or x["player"]), w_))
                    if s_ and "pts" not in s_ and not (s_.get("snap") or 0):
                        s_ = None   # only a 0-snap row: treat like he didn't play
                    if not s_:
                        k2 = x.get("kick")
                        if k2 and (now - datetime.datetime.fromisoformat(k2.replace("Z", "+00:00"))).days >= 2 and (U.get("lines") or {}).get(w_, {}).get(x["team"], {}).get("pts") is not None:
                            x["result"] = None; x["graded"] = True; x["void"] = True   # didn't play: bet is refunded
                        continue
                    stv = {"Pass yds": s_.get("passyd", 0), "Pass TDs": s_.get("ptd", 0), "Rush yds": s_.get("rushyd", 0), "Rec yds": s_.get("recyd", 0),
                           "Catches": s_.get("rec", 0), "Anytime TD": (s_.get("rutd", 0) or 0) + (s_.get("retd", 0) or 0)}[x["market"]]
                    x["actual"] = stv
                    x["result"] = (stv >= 1) if x["market"] == "Anytime TD" else (None if stv == x["line"] else ((stv > x["line"]) == (x["side"] == "Over")))
                    x["graded"] = True
                    if x.get("v") == 2:
                        x["clv"] = prop_clv(w_, x)
            if x.get("auto"):   # tracker: headline = 1 unit on every +EV ("bet" tier) v2 entry at the price it was locked at
                if x.get("v") == 2:
                    if x.get("graded") and x.get("result") is not None and x.get("tier") == "bet":
                        b_ = rec["auto_games" if k.startswith("GA|") else "auto_props"]
                        b_[0 if x["result"] else 1] += 1; b_[2] += (dec(x["price"]) - 1) if x["result"] else -1
                continue
            if x.get("graded") and x.get("result") is not None:
                b_ = rec["games" if k.startswith("G|") else "props"]
                b_[0 if x["result"] else 1] += 1
                pm0 = (x.get("p_mkt") or 52.4) / 100
                c_ = min(0.99, pm0 + 0.01 + 0.07 * pm0 * (1 - pm0))   # what a share really costs: market chance + 1 cent + fee (not the flattering no-vig price)
                b_[2] += ((1 - c_) / c_) if x["result"] else -1   # $1 bought at the market price, like a Kalshi contract
                ww = rec["weeks"].setdefault(w_, [0, 0, 0.0])
                ww[0 if x["result"] else 1] += 1; ww[2] += ((1 - c_) / c_) if x["result"] else -1
    for w_, picks in PK.items():   # tracker buckets, open bets included
        for k, x in picks.items():
            if x.get("auto") and x.get("v") == 2:
                tally(k, x)
    (D / "props_picks.json").write_text(json.dumps(PK, separators=(",", ":"), sort_keys=True))
    rec["kalshi_open"] = sum(1 for k, x in wk.items() if k.startswith("KA|") and not x.get("graded"))
    for b_ in (rec["props"], rec["games"], rec["auto_games"], rec["auto_props"], rec["kalshi"]):
        b_[2] = round(b_[2], 2)
    for k in rec["weeks"]:
        rec["weeks"][k][2] = round(rec["weeks"][k][2], 2)
    graded = [dict(x, week=w_) for w_, pk in PK.items() for k, x in pk.items() if x.get("graded") and not x.get("auto")]
    rec["auto_open"] = {"games": sum(1 for k, x in wk.items() if k.startswith("GA|") and x.get("tier") == "bet" and not x.get("graded")),
                        "props": sum(1 for k, x in wk.items() if k.startswith("PA|") and x.get("tier") == "bet" and not x.get("graded"))}
    def summ(s):
        n = s["w"] + s["l"]; o = {"w": s["w"], "l": s["l"], "push": s["push"], "open": s["open"], "units": round(s["u"], 2)}
        if n:
            o.update(roi=round(100 * s["u"] / n, 1), roi_se=round(100 * s["var"] ** 0.5 / n, 1), hit=round(100 * s["w"] / n, 1),
                     hit_mkt=round(100 * s["pm"] / n, 1), hit_ours=round(100 * s["po"] / n, 1), ev_claimed=round(100 * s["ev"] / n, 1))
        if s["cn"]:
            m_ = s["cs"] / s["cn"]; sd = max(0.0, s["cq"] / s["cn"] - m_ * m_) ** 0.5
            o.update(clv_n=s["cn"], clv_ev=round(100 * m_, 2), clv_se=round(100 * sd / s["cn"] ** 0.5, 2),
                     clv_n_needed=int(min(99999, (2 * sd / max(abs(m_), 0.005)) ** 2)))   # bets for the CLV average to sit 2 SE from zero
        return o
    rec["tracker"] = {k_: summ(v) for k_, v in sorted(TR.items())}
    UNIT = float((load(ROOT / "config.json", {}) or {}).get("unit", 25))
    STAKE = {"A": 1.0, "B": 0.5, "C": 0.25}
    for x in props:
        x["units"] = STAKE.get(x["grade"], 0)
    for g in games:
        for pl in g["plays"]:
            pl["units"] = 0.5
    for n_ in parlays:
        for pl in parlays[n_]:
            le_ = sum(l.get("lean", 0) for l in pl["legs"])
            pl["edge_legs"] = le_
            # only size a bet when the legs carry real model lean; otherwise it's a fair-price check (books charge extra on alt lines)
            pl["units"] = (0.25 if n_ == "conf" else 0.5) if le_ >= 3 else 0
    legs_out = legs_out + [{"label": L_["label"], "p": round(100 * L_["p"], 1), "fair": amer(L_["p"]), "type": "game", "kick": L_["kick"], "lean": round(100 * L_["lean"], 1),
                            "game": "-".join(L_["game"]), "bits": bits(L_)}
                           for L_ in sorted(gpool, key=lambda z: -z["p"]) if L_["label"] not in {l["label"] for l in legs_out}]
    rec["dollars"] = {k: round(rec[k][2] * UNIT, 2) for k in ("props", "games", "auto_games", "auto_props")}
    out = {"unit": UNIT, "kalshi": kal_out, "week": int(cur), "at": now.isoformat(timespec="seconds"), "sims": N, "trust": W, "min_edge": MIN_EDGE * 100, "pickem_cut": PICKEM,
           "parlays": parlays, "longshots": longshots, "teasers": [{k: (round(100 * v, 1) if k == "p" else v) for k, v in t.items() if k != "game"} for t in TEASERS], "legs": legs_out, "bias": {k: round(v, 3) for k, v in CORR_RAW.items()},
           "pickem_plays": sorted([x for x in props if x["pickem"]], key=lambda x: -x["p"])[:25],
           "lines_at": RAW.get("at"), "prices_at": GO.get("at"), "games": sorted(games, key=lambda g: g["kick"] or ""),
           "props": [x for x in props if x["grade"]][:80], "all_props": len(props),
           "record": rec, "recent": sorted(graded, key=lambda x: (x["week"], x.get("kick") or ""), reverse=True)[:40],
           "backtest": {"ats_model": "49 to 51% against the closing spread 2020-25 (need 52.4% to profit at -110)", "line_mae": 9.8, "model_mae": 10.3,
                        "angles": ANG}}
    # phone alerts: news plays (lines older than the injury) + a weekly "lines are in" digest
    al = []
    for x in props:
        if x.get("news") and x["grade"] in ("A", "B"):
            ln = f"{x['side']} {x['line']} " if x["line"] is not None else ""
            al.append({"key": f"prop|{cur}|{x['player']}|{x['market']}|{x['side']}|{x['line']}", "title": f"📈 {x['news'][0]}: {x['player']} {ln}{x['market'].lower()}",
                       "body": f"{round(x['p'])}% in the sims vs {round(x['p_mkt'])}% market. Line is from before the news.",
                       "report": {"what": f"{', '.join(x['news'])}. The {x['player']} line ({ln}{x['market'].lower()}) was set before that.",
                                  "means": f"Re-running 50,000 sims with his extra work: {round(x['p'])}% vs the market's {round(x['p_mkt'])}%. Usually {x['p10']} to {x['p90']}.",
                                  "todo": f"Worth it on Kalshi/Polymarket under {x['max_c']}¢, or as a pick'em leg. Lines move fast after news, so check the number first."}})
    if out["parlays"].get(2) and RAW.get("at"):
        b2 = out["parlays"][2][0]
        al.append({"key": f"digest|{cur}|{RAW.get('at')}", "title": f"🎲 Week {cur} Prop Lab is ready",
                   "body": f"Top 2-leg: {' + '.join(l['label'] for l in b2['legs'])} ({round(b2['p'])}%, fair {b2['fair']:+d}).",
                   "report": {"what": f"New lines are in and every game ran {N:,} times.", "means": "Best 2-leg: " + "; ".join(f"{l['label']} ({round(l['p'])}%)" for l in b2["legs"]) + f". Hits {round(b2['p'])}% in the sims, fair odds {b2['fair']:+d}.",
                              "todo": f"Only take it if the app pays better than {b2['fair']:+d}. More parlays, props and pick'em plays in the Prop Lab tab."}})
    # late news vs LIVE Kalshi prices: a teammate was ruled out after the lines went up and the share hasn't caught up
    for x in sorted([x for x in KBUY if x.get("news") and x["edge"] >= 4 and x["ev"] >= 6 and 15 <= x["p"] <= 90 and (x.get("spread") is None or x["spread"] <= 6)], key=lambda x: -x["edge"])[:3]:
        if x.get("kick") and 0 < (datetime.datetime.fromisoformat(x["kick"].replace("Z", "+00:00")) - now).total_seconds() < 6 * 3600:
            al.append({"key": f"kal|{cur}|{x['ticker']}|{x['side']}", "title": f"📈 {x['news'][0]}: {x['label']} under {x['cost'] + 1}¢",
                       "body": f"Sims {round(x['p'])}% vs {x['cost']}¢ on Kalshi. Buy before it moves.",
                       "report": {"what": f"{', '.join(x['news'])}. Kalshi's {x['label']} share is still {x['cost']}¢.",
                                  "means": f"Re-running 50,000 sims with the extra work he gets: {round(x['p'])}% to hit. After the {x['fee']}¢ fee that's +{x['ev']}% expected value.",
                                  "todo": f"Buy YES at {x['cost']}¢ or less ({usd_(0.5)} is a normal bet). If the price already jumped, skip it."}})
    # Sunday game-day digest: once, after the early inactives (about 9 AM PT), with the slate's best stuff
    if now.weekday() == 6 and (16, 45) <= (now.hour, now.minute) <= (18, 30):
        bits_ = []
        b4 = (out["parlays"].get("best4") or [None])[0]
        if b4:
            bits_.append(f"Best 4-leg ({round(b4['p'])}%, worth up to {round(b4['p'])}¢): " + "; ".join(l["label"] for l in b4["legs"]))
        kb_ = (out.get("kalshi") or {}).get("buys") or []
        if kb_:
            bits_.append("Top Kalshi buys: " + "; ".join(f"{x['label']} {round(x['p'])}% vs {x['cost']}¢" for x in kb_[:3]))
        ang = [f"{g['away']} @ {g['home']} {pl['pick']} ({round(pl['p'])}%)" for g in games if not g["started"] for pl in g["plays"]]
        if ang:
            bits_.append("Angle plays: " + "; ".join(ang[:3]))
        nw = [x for x in props if x.get("news") and x["grade"]]
        if nw:
            bits_.append("Late news: " + "; ".join(f"{x['player']} {x['side']} {x['line'] or ''} {x['market'].lower()}" for x in nw[:3]))
        lad = (out["parlays"].get("ladder") or [None])[0]
        if lad:
            bits_.append(f"Alt ladder ({round(lad['p'])}%, fair {lad['fair']:+d}): {len(lad['legs'])} legs at about 86% each")
        if bits_:
            al.append({"key": f"sunday|{cur}", "title": f"🏈 Week {cur} game day: the board",
                       "body": (bits_[0][:120] + "…") if len(bits_[0]) > 120 else bits_[0],
                       "report": {"what": "Inactives for the early games are in and everything re-ran 50,000 times.", "means": "\n".join(bits_),
                                  "todo": "Buy only at or under the cents shown. Full lists are in the Prop Lab tab."}})
    out["alerts"] = al
    old = load(OUT, {}) or {}
    if {k: v for k, v in old.items() if k != "at"} != json.loads(json.dumps({k: v for k, v in out.items() if k != "at"})):   # only write when something changed
        OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"props_lab: {len(games)} games x {N:,} sims, {len(props)} props priced, {len(out['props'])} plays, record props {rec['props'][:2]} games {rec['games'][:2]}")


if __name__ == "__main__":
    main()
