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

ROOT = pathlib.Path(__file__).resolve().parents[1]
D = ROOT / "data"
OUT = ROOT / "docs" / "data" / "props_lab.json"
N = 50_000
W = 0.20            # how much of the gap between our number and the market's number we believe (tune from the record)
MAX_GAP = 0.35      # bigger disagreements usually mean the market knows something we don't (injury, role change): no play
MIN_EDGE = 0.025    # our chance has to beat the market's no-vig chance by 2.5+ points to be listed
PICKEM = 56.0       # pick'em legs (PrizePicks/Underdog/Pick6) need about 54 to 58% to profit, depending on the payout
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
BETA = {"pass_yds": 0.55, "pass_tds": 0.8, "rush_yds": 0.35, "rec_yds": 0.5, "rec": 0.35, "td": 0.9}   # how much a stat rides with his team's score


def key(n):
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", n.lower())
    return re.sub(r"[^a-z]", "", n)


def dec(am):
    return 1 + (am / 100 if am > 0 else 100 / -am)


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
    "wind_tiers": [(15, -2.0), (10, -1.5)],
    "wind_hit": {"10": {"old": 56.5, "new": 59.6, "n": 651}, "15": {"old": 51.4, "new": 61.4, "n": 210}},
    # rain at kickoff: -3.9 pts (SE 1.1), unders 60.3% (2016-20) / 74.6% (2021-25), n=131. Scale by forecast chance and trust 0.6.
    "rain_full": -3.9, "rain_trust": 0.6, "rain_min_prob": 40,
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


def sample_game(spr, tot, resid, pool, rng, n, t_shift=0.0, m_shift=0.0, noise=0.6):
    """Joint (home margin, total) sims.
    resid: current DIST['_game_resid'] rows [margin - spread, total - total_line, spread] (2016-25).
    pool : new DIST['_game_pool'] rows [margin - spread, total - total_line, spread, total_line, season] (2006-25).
    Half the draws = current residual method (smooth tails); half = real margins from games with ~the same spread
    (keeps exact mass on 3, 7, 10, 14). Out of sample this cut the average calibration miss on alt spreads from 2.2 to ~1.5 pts
    of probability and improved Brier in both 2016-20 and 2021-25."""
    na = n // 2; nb = n - na
    # A) current method
    near = resid[np.abs(resid[:, 2] - spr) <= max(3.0, abs(spr) * 0.35)]
    near = near if len(near) >= 150 else resid
    near = near - np.array([np.median(near[:, 0]), np.median(near[:, 1]), 0])
    i = rng.integers(0, len(near), na)
    mar_a = spr + near[i, 0] + rng.normal(0, noise, na)
    tr_a = near[i, 1]
    # B) direct margins, favorite's view (home and away favorites pooled)
    psp = pool[:, 2]
    pfs = np.abs(psp)
    pfm = np.where(psp >= 0, 1.0, -1.0) * (psp + pool[:, 0])
    fs, sgn = abs(spr), (1.0 if spr >= 0 else -1.0)
    for w in (0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 99):
        sel = np.where(np.abs(pfs - fs) <= w)[0]
        if len(sel) >= 250:
            break
    j = sel[rng.integers(0, len(sel), nb)]
    fm = pfm[j] + np.round(fs - pfs[sel].mean())
    mar_b = sgn * fm
    tr_b = pool[j, 1] - np.median(pool[sel, 1])
    if m_shift:   # integer-preserving shift: move |m_shift| of draws by one full point
        mar_b = mar_b + np.sign(m_shift) * (rng.random(nb) < min(1.0, abs(m_shift)))
        mar_a = mar_a + m_shift
    mar = np.r_[mar_a, mar_b]
    totl = tot + t_shift + np.r_[tr_a, tr_b] + rng.normal(0, noise, n)
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
    SL = load(D / "sleeper_proj.json", {}) or {}; GO = load(D / "game_odds.json", {}) or {}
    M = next((load(p) for p in sorted(D.glob("*/model.json"))), {}) or {}
    cur = str(M.get("cur") or RAW.get("week") or "")
    lines = (U.get("lines") or {}).get(cur, {}); kick = (U.get("kick") or {}).get(cur, {})
    now = datetime.datetime.now(datetime.timezone.utc)
    if not lines or not DIST:
        print("props_lab: no lines or fits yet"); return
    resid = np.array(DIST["_game_resid"], dtype=float)
    pool = np.array(DIST.get("_game_pool") or [r + [0, 0] for r in DIST["_game_resid"]], dtype=float)
    TEASERS = []
    ANG = DIST["_angles"]

    # best prices for game markets (Odds API), keyed by home team
    gbest = {}
    for g in (GO.get("games") or {}).values():
        h, a = FULL.get(g["home"]), FULL.get(g["away"])
        if not h:
            continue
        b = gbest.setdefault(h, {})
        for mk, books in g["books"].items():
            for bk in books:
                for o in bk["outcomes"]:
                    side = {"h2h": "ml", "spreads": "spr", "totals": "tot"}[mk]
                    nm = o["name"]; tag = "home" if FULL.get(nm) == h else "away" if FULL.get(nm) == a else nm.lower()
                    k_ = (side, tag, o.get("point"))
                    if k_ not in b or o["price"] > b[k_]["price"]:
                        b[k_] = {"price": o["price"], "book": bk["book"]}

    # players: name -> usage entry
    PL = {key(v["name"]): v for v in (U.get("players") or {}).values()}
    SLK = {}
    for k_, v in SL.items():
        n_, t_ = (k_.split("|") + [""])[:2]
        if v:
            SLK[key(n_)] = v

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
            vals = [(w.get("td", 0) or 0) for w in wk] if p["pos"] != "QB" else [(w.get("rutd", 0) or 0) for w in wk]
        else:
            vals = [w.get(USE[st], 0) or 0 for w in wk]
        if len(vals) < 3:
            return None
        m = (1 - fit["w_recent"]) * np.mean(vals) + fit["w_recent"] * np.mean(vals[-3:])
        return max(0.0, fit["slope"] * m + fit["icpt"])

    def sl_mean(n, st):
        v = SLK.get(key(n))
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

    games, sims, done, LEGS = [], {}, set(), []
    HOMES = {FULL.get(e["home"]) for e in RAW.get("events", [])} | {FULL.get(g["home"]) for g in (GO.get("games") or {}).values()}
    for tm_, L_ in lines.items():   # older stats files don't say who's home: fill it in from the odds feed
        if "home" not in L_:
            L_["home"] = tm_ in HOMES if HOMES else L_["spread"] >= lines.get(L_["opp"], {}).get("spread", 0)
    # injury status for every player ESPN gives us (all four leagues' player pools)
    INJ = {}
    for mp in sorted(D.glob("*/model.json")):
        for q in (load(mp, {}) or {}).get("players", []):
            INJ[key(q["n"])] = q.get("inj") or "ACTIVE"
    OUTS = ("OUT", "INJURY_RESERVE", "DOUBTFUL", "SUSPENSION")
    # who was already out when the lines went up: anyone ruled out AFTER that makes his teammates' lines stale
    IS = load(D / "props_inj_state.json", {}) or {}
    if IS.get("lines_at") != RAW.get("at"):
        IS = {"lines_at": RAW.get("at"), "out": [k for k, v in INJ.items() if v in OUTS]}
    newly_out = {k for k, v in INJ.items() if v in OUTS} - set(IS["out"])
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
        mar, totl = sample_game(spr, tot, resid, pool, rng, N, t_shift=shift, m_shift=m_shift)   # half the sims use real margins: keeps 3 and 7 right
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
                if 0.70 <= pa <= 0.90:
                    LEGS.append({"kindg": "total" if (" Over " in lab or " Under " in lab) else "ml" if "moneyline" in lab else "spread", "lean": (0.004 if (" Under " in lab and angles) else 0.0), "label": lab, "game": gk, "who": lab.split()[0], "p": pa, "ind": ind, "type": "game", "kick": k_})
        games.append({"home": h, "away": a, "kick": k_, "started": started, "spread": spr, "total": tot, "roof": roof, "weather": wx, "angles": angles + s_angles,
                      "sim": {"home_win": round(100 * p_home_win, 1), "home_cover": round(100 * p_home_cover, 1), "over": round(100 * p_over, 1),
                              "home_pts": round(float(np.mean(hp)), 1), "away_pts": round(float(np.mean(ap)), 1), "fair_total": round(float(np.median(totl)), 1),
                              "fair_home_ml": amer(p_home_win), "blowout": round(100 * float(np.mean(np.abs(mar) >= 17)), 1), "one_score": round(100 * float(np.mean(np.abs(mar) <= 8)), 1)},
                      "plays": sorted(plays, key=lambda x: -x["edge"])})

    # ---------------- player props ----------------
    ev_team = {}
    for e in RAW.get("events", []):
        ev_team[e["id"]] = (FULL.get(e["home"]), FULL.get(e["away"]), e["at"])
    def price_props(CORR, RAT):
        props, legs = [], []
        for nm, mks in (RAW.get("props") or {}).items() if RAW.get("week") == int(cur or 0) or str(RAW.get("week")) == cur else []:
            p = PL.get(key(nm))
            if not p or p.get("team") not in sims or INJ.get(key(nm)) in OUTS:
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
                ours = [v for v in (our_mean(p, st), sl_mean(nm, st)) if v is not None]
                if not ours:
                    continue
                ours_m = float(np.mean(ours)) / CORR.get(st, 1.0)
                ours_raw = float(np.mean(ours))
                why_news, w_ = [], W
                for q in news:   # a teammate got ruled out after the line went up
                    if q["pos"] == "QB" and p["pos"] in ("WR", "TE", "RB") and st in ("rec", "rec_yds", "td"):
                        ours_m *= 0.86; why_news.append(f"{q['name']} (QB) is out")   # backup QB: pass catchers -14% (fantasy backtest)
                    elif q["pos"] == p["pos"] or (q["pos"] in ("WR", "TE") and p["pos"] in ("WR", "TE") and st in ("rec", "rec_yds")):
                        qm = our_mean(q, st)
                        if qm:
                            mates = max(1, sum(1 for z in PL.values() if z.get("team") == p["team"] and z.get("pos") == q["pos"] and z["name"] != q["name"] and z.get("depth", {}).get("rank", 9) <= 3))
                            ours_m += (0.45 if q["pos"] == p["pos"] == "RB" else 0.3) * qm / mates
                            why_news.append(f"{q['name']} is out")
                if why_news:
                    w_ = 0.6   # the line was set before the news, so trust our number a lot more
                if st == "td":
                    line = 0.5; p_mkt = min(0.97, x["p"])
                else:
                    line = x["line"]; p_mkt = x["p_over"]
                # game script: he does more when his team scores more
                f = (np.maximum(own, 1) / max(1, pts_mean)) ** BETA[st]
                if st == "rush_yds" and p["pos"] == "RB":
                    f = f * (1 + 0.008 * np.clip(mar, -21, 21))
                f = f / f.mean()
                if st in ("pass_tds", "td", "rec"):   # counts: Poisson around his mean, mixed over the game sims
                    fs = f[:4000]; k = math.floor(line)
                    def P(m, fs=fs, k=k):
                        lam = np.maximum(1e-9, m * fs); cdf = np.zeros_like(lam); term = np.exp(-lam)
                        for i in range(k + 1):
                            cdf += term; term = term * lam / (i + 1)
                        return float(np.mean(1 - cdf))
                else:                                  # yards: real past outcomes vs projection, times game script, same draws every time
                    rr = ratios(st, p["pos"], max(ours_m, 1))
                    base = rr[rng.integers(0, len(rr), N)] * f
                    P = lambda m, base=base, line=line: float(np.mean(m * base > line))
                lo, hi = 0.005, max(4 * line, 6)
                for _ in range(45):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if P(mid) < p_mkt else (lo, mid)
                mm = (lo + hi) / 2                     # the average the market's price implies
                if RAT is not None:
                    if mm > 0.05:
                        RAT.setdefault(st, []).append(ours_raw / mm)
                    continue
                fin = mm + w_ * (ours_m - mm)
                big_gap = abs(ours_m - mm) > MAX_GAP * max(mm, 0.5) and not why_news
                po = P(fin)
                X = rng.poisson(np.maximum(0, fin * f)) if st in ("pass_tds", "td", "rec") else fin * base
                if not big_gap and st != "td":
                    # alt lines he hits about 75-82% of the time, for parlays
                    for side_, q in (("Over", 0.22), ("Over", 0.17), ("Under", 0.78), ("Under", 0.83)):
                        v = float(np.quantile(X, q))
                        alt = (math.floor(v) - 0.5) if side_ == "Over" else (math.ceil(v) + 0.5)
                        if side_ == "Over" and alt < 0.5:
                            continue
                        ind = (X > alt) if side_ == "Over" else (X < alt)
                        pa = float(ind.mean())
                        if 0.72 <= pa <= 0.86:
                            legs.append({"lean": (po - p_mkt) * (1 if side_ == "Over" else -1), "label": f"{nm} {side_} {alt:g} {label.lower()}", "game": tuple(sorted((tm, lines[tm]["opp"]))), "who": nm, "p": pa, "ind": ind, "type": "prop", "kick": k_})
                if st == "td":
                    cand = [("Yes", po, x["yes"])]
                else:
                    cand = [("Over", po, x["over"]), ("Under", 1 - po, x["under"])]
                for side, pp, price in cand:
                    ev = pp * dec(price) - 1
                    pm_ = p_mkt if side in ("Over", "Yes") else 1 - p_mkt
                    props.append({"news": why_news, "edge": round(100 * (pp - pm_), 1), "max_c": int(100 * pp - 2), "pickem": st != "td" and 100 * pp >= PICKEM and not big_gap, "big_gap": big_gap, "player": nm, "team": tm, "opp": lines[tm]["opp"], "pos": p["pos"], "market": label, "side": side, "line": line if st != "td" else None,
                                  "price": price, "books": x.get("books"), "p": round(100 * pp, 1), "p_mkt": round(100 * (p_mkt if side in ("Over", "Yes") else 1 - p_mkt), 1),
                                  "ev": round(100 * ev, 1), "ours": round(ours_m, 2), "mkt": round(mm, 2), "final": round(fin, 2), "kick": k_,
                                  "median": round(float(np.median(X)), 1), "p10": round(float(np.percentile(X, 10)), 1), "p90": round(float(np.percentile(X, 90)), 1)})
        return props, legs
    _rat = {}
    price_props({}, _rat)   # pass 1: how far our average sits from the market's average, by market
    CORR = {k: float(np.median(v)) for k, v in _rat.items() if len(v) >= 8}
    props, plegs = price_props(CORR, None)   # pass 2: with that systematic gap removed
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
            if max(sum(1 for c in combo if c["game"] == g) for g in {c["game"] for c in combo}) > 2:
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
            if len({c["who"] for c in combo}) < n_ or max(sum(1 for c in combo if c["game"] == g) for g in {c["game"] for c in combo}) > 2:
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
    legs_out = [{"label": L_["label"], "p": round(100 * L_["p"], 1), "fair": amer(L_["p"]), "type": L_["type"], "kick": L_["kick"], "lean": round(100 * L_["lean"], 1)} for L_ in pool]

    # ---------------- record: save picks until kickoff, grade after ----------------
    PK = load(D / "props_picks.json", {}) or {}
    wk = PK.setdefault(cur, {})
    for x in props:
        if x["grade"]:
            wk[f"{x['player']}|{x['market']}"] = {k: x[k] for k in ("player", "team", "market", "side", "line", "price", "p", "p_mkt", "edge", "grade", "kick")}
    for g in games:
        for pl in g["plays"]:
            if not g["started"]:
                wk[f"G|{g['home']}|{pl['pick']}"] = {"game": f"{g['away']} @ {g['home']}", "home": g["home"], "pick": pl["pick"], "kind": pl["kind"],
                                                    "p": pl["p"], "p_mkt": pl["p_mkt"], "edge": pl["edge"], "spread": g["spread"], "total": g["total"], "kick": g["kick"]}
    stat_by = {}
    for v in (U.get("players") or {}).values():
        for w in v.get("weeks", []):
            stat_by[(key(v["name"]), str(w["w"]))] = w
    rec = {"props": [0, 0, 0.0], "games": [0, 0, 0.0], "weeks": {}}
    for w_, picks in PK.items():
        for k, x in picks.items():
            if x.get("result") is None:
                if k.startswith("G|"):
                    Lh = (U.get("lines") or {}).get(w_, {}).get(x["home"], {})
                    La = (U.get("lines") or {}).get(w_, {}).get(Lh.get("opp"), {})
                    if Lh.get("pts") is None or La.get("pts") is None:
                        continue
                    m_ = Lh["pts"] - La["pts"]; t_ = Lh["pts"] + La["pts"]
                    pk = x["pick"]
                    if pk.startswith("Over") or pk.startswith("Under"):
                        x["result"] = None if t_ == x["total"] else ((t_ > x["total"]) == pk.startswith("Over"))
                    elif "moneyline" in pk:
                        x["result"] = (m_ > 0) == pk.startswith(x["home"])
                    else:
                        cover = m_ - x["spread"]
                        x["result"] = None if cover == 0 else ((cover > 0) == pk.startswith(x["home"]))
                    x["graded"] = True
                else:
                    s_ = stat_by.get((key(x["player"]), w_))
                    if not s_:
                        k2 = x.get("kick")
                        if k2 and (now - datetime.datetime.fromisoformat(k2.replace("Z", "+00:00"))).days >= 2 and (U.get("lines") or {}).get(w_, {}).get(x["team"], {}).get("pts") is not None:
                            x["result"] = None; x["graded"] = True; x["void"] = True   # didn't play: bet is refunded
                        continue
                    stv = {"Pass yds": s_.get("passyd", 0), "Pass TDs": s_.get("ptd", 0), "Rush yds": s_.get("rushyd", 0), "Rec yds": s_.get("recyd", 0),
                           "Catches": s_.get("rec", 0), "Anytime TD": (s_.get("td", 0) or 0) if True else 0}[x["market"]]
                    x["actual"] = stv
                    x["result"] = (stv >= 1) if x["market"] == "Anytime TD" else (None if stv == x["line"] else ((stv > x["line"]) == (x["side"] == "Over")))
                    x["graded"] = True
            if x.get("graded") and x.get("result") is not None:
                b_ = rec["games" if k.startswith("G|") else "props"]
                b_[0 if x["result"] else 1] += 1
                c_ = (x.get("p_mkt") or 52.4) / 100
                b_[2] += ((1 - c_) / c_) if x["result"] else -1   # $1 bought at the market price, like a Kalshi contract
                ww = rec["weeks"].setdefault(w_, [0, 0, 0.0])
                ww[0 if x["result"] else 1] += 1; ww[2] += ((1 - c_) / c_) if x["result"] else -1
    (D / "props_picks.json").write_text(json.dumps(PK, separators=(",", ":"), sort_keys=True))
    for b_ in (rec["props"], rec["games"]):
        b_[2] = round(b_[2], 2)
    for k in rec["weeks"]:
        rec["weeks"][k][2] = round(rec["weeks"][k][2], 2)
    graded = [dict(x, week=w_) for w_, pk in PK.items() for k, x in pk.items() if x.get("graded")]
    UNIT = float((load(ROOT / "config.json", {}) or {}).get("unit", 25))
    STAKE = {"A": 1.0, "B": 0.5, "C": 0.25}
    for x in props:
        x["units"] = STAKE.get(x["grade"], 0)
    for g in games:
        for pl in g["plays"]:
            pl["units"] = 0.5
    for n_ in parlays:
        for pl in parlays[n_]:
            pl["units"] = 0.25 if n_ == "conf" else 0.5
    legs_out = legs_out + [{"label": L_["label"], "p": round(100 * L_["p"], 1), "fair": amer(L_["p"]), "type": "game", "kick": L_["kick"], "lean": round(100 * L_["lean"], 1)}
                           for L_ in sorted(gpool, key=lambda z: -z["p"]) if L_["label"] not in {l["label"] for l in legs_out}]
    rec["dollars"] = {k: round(rec[k][2] * UNIT, 2) for k in ("props", "games")}
    out = {"unit": UNIT, "week": int(cur), "at": now.isoformat(timespec="seconds"), "sims": N, "trust": W, "min_edge": MIN_EDGE * 100, "pickem_cut": PICKEM,
           "parlays": parlays, "teasers": [{k: (round(100 * v, 1) if k == "p" else v) for k, v in t.items() if k != "game"} for t in TEASERS], "legs": legs_out, "bias": {k: round(v, 3) for k, v in CORR.items()},
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
    out["alerts"] = al
    old = load(OUT, {}) or {}
    if {k: v for k, v in old.items() if k != "at"} != json.loads(json.dumps({k: v for k, v in out.items() if k != "at"})):   # only write when something changed
        OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"props_lab: {len(games)} games x {N:,} sims, {len(props)} props priced, {len(out['props'])} plays, record props {rec['props'][:2]} games {rec['games'][:2]}")


if __name__ == "__main__":
    main()
