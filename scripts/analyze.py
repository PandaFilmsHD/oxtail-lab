"""Turn data/model.json into everything the site shows (docs/data/latest.json + docs/data/history.json).

What it does:
  1. Values every player week by week (season avg + recent form, opponent matchup, injury odds).
  2. Sims the rest of the regular season and the playoff bracket (vectorized Monte Carlo).
  3. Searches 1-for-1, 2-for-1 and 1-for-2 trades with every team and sims the best ones.
  4. Ranks waiver adds, builds ROS + playoff rankings, finds bubble teams, writes a plain-English take.
"""
import json, math, os, time, pathlib, itertools
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
A = json.loads((ROOT / "assumptions.json").read_text())
LID = os.environ.get("LEAGUE") or str(CFG.get("default_league"))
LDIR = ROOT / "data" / LID
M = json.loads((LDIR / "model.json").read_text())
PREV_P = LDIR / "model_prev.json"
OUT = ROOT / "docs" / "data" / LID
OUT.mkdir(parents=True, exist_ok=True)
LCFG = (CFG.get("leagues") or {}).get(LID, {})
LA = (A.get("leagues") or {}).get(LID, {})   # per-league assumptions (core players, teams you won't trade with)

ME = M.get("my_team") or LCFG.get("team_id")
CUR, REG = M["cur"], M["reg_end"]
PO1, PO2, PO3 = REG + 1, REG + 2, REG + 3
WKS = [w for w in range(CUR, PO3 + 1)]
WGT = A["weights"]
SD = WGT["weekly_sd"]
POSID = {"QB": "1", "RB": "2", "WR": "3", "TE": "4", "K": "5", "D": "16"}
SKILL = ("QB", "RB", "WR", "TE")
# ---------------- league settings: straight from ESPN, then any overrides in config.json ----------------
DEFAULT_SETTINGS = {"teams": len(M["teams"]), "ppr": 1, "pass_td": 4, "playoff_teams": M["po_teams"], "reg_end": M["reg_end"],
                    "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "D": 1, "K": 1},
                    "scoring": {"3": 0.04, "4": 4, "20": -2, "24": 0.1, "25": 6, "53": 1, "42": 0.1, "43": 6, "72": -2, "19": 2, "26": 2, "44": 2}}
SET = {**DEFAULT_SETTINGS, **(M.get("settings") or {})}
SET["source"] = "ESPN" if M.get("settings") else "defaults (no ESPN settings in this pull)"
OVR_SET = {k: v for k, v in {**(CFG.get("league_overrides") or {}), **(LCFG.get("overrides") or {})}.items() if not k.startswith("_")}
SET.update(OVR_SET)
SC = {**DEFAULT_SETTINGS["scoring"], **(SET.get("scoring") or {})}
if "ppr" in OVR_SET: SC["53"] = OVR_SET["ppr"]
if "pass_td" in OVR_SET: SC["4"] = OVR_SET["pass_td"]
SET["ppr"], SET["pass_td"] = SC.get("53", 0), SC.get("4", 4)
ELIG = {"QB": ("QB",), "RB": ("RB",), "WR": ("WR",), "TE": ("TE",), "D": ("D",), "K": ("K",), "FLEX": ("RB", "WR", "TE"),
        "RB/WR": ("RB", "WR"), "WR/TE": ("WR", "TE"), "OP": ("QB", "RB", "WR", "TE")}
ORDER = ["QB", "RB", "WR", "TE", "D", "K", "RB/WR", "WR/TE", "FLEX", "OP"]
SLOTS = [(s, int(SET["slots"][s])) for s in ORDER if SET["slots"].get(s)]


def league_pts(x):
    """Score an nflverse box-score line with this league's ESPN scoring."""
    return (x.get("passyd", 0) * SC.get("3", 0) + x.get("ptd", 0) * SC.get("4", 0) + x.get("int_", 0) * SC.get("20", 0) +
            x.get("rushyd", 0) * SC.get("24", 0) + x.get("rutd", 0) * SC.get("25", 0) + x.get("rec", 0) * SC.get("53", 0) +
            x.get("recyd", 0) * SC.get("42", 0) + x.get("retd", 0) * SC.get("43", 0) + x.get("fl", 0) * SC.get("72", 0) + x.get("two", 0) * SC.get("19", 2))


def league_xpts(xs):
    """Score an expected stat line (ffopportunity) with this league's scoring."""
    return (xs["pass_yards_gained_exp"] * SC.get("3", 0) + xs["pass_touchdown_exp"] * SC.get("4", 0) + xs["pass_interception_exp"] * SC.get("20", 0) +
            xs["rush_yards_gained_exp"] * SC.get("24", 0) + xs["rush_touchdown_exp"] * SC.get("25", 0) + xs["receptions_exp"] * SC.get("53", 0) +
            xs["rec_yards_gained_exp"] * SC.get("42", 0) + xs["rec_touchdown_exp"] * SC.get("43", 0) +
            (xs["pass_two_point_conv_exp"] + xs["rush_two_point_conv_exp"] + xs["rec_two_point_conv_exp"]) * SC.get("19", 2))
rng = np.random.default_rng(12345)   # fixed seed: same inputs give the same odds
AVAIL = {k: v for k, v in A["avail"].items() if not k.startswith("_")}
OVR = {k: v for k, v in A["override"].items() if not k.startswith("_")}
CORE = set(LA.get("core", []))
NO_TRADE = set(LA.get("no_trade_teams", []))

# ---------------- rosters ----------------
P = {}
for p in M["players"]:
    P.setdefault(p["n"], p)
TEAMS = {t["id"]: t for t in M["teams"]}
TNAME = {t["id"]: t["name"] for t in M["teams"]}
ROSTER = {t["id"]: [] for t in M["teams"]}
for p in P.values():
    if p["t"] in ROSTER:
        ROSTER[p["t"]].append(p["n"])


# ---------------- player values ----------------
def opp(p, w):
    return (M["games"].get(p["nfl"]) or {}).get(str(w))  # None = bye


def opp_rank(p, w):
    g = opp(p, w)
    if not g:
        return None
    if g == "?":
        return 16
    return (M["rank"].get(POSID[p["pos"]]) or {}).get(g.lstrip("@"), 16)


def recent(p):
    ws = sorted((int(w) for w in p["wk"] if CUR - 5 <= int(w) < CUR), reverse=True)
    v = [p["wk"][str(w)] for w in ws if p["wk"][str(w)] > 0][:4]
    return sum(v) / len(v) if len(v) >= 2 else None


# ---------------- usage + profile (nflverse + ESPN extras) ----------------
import re, statistics
UP = ROOT / "data" / "usage.json"
_U = json.loads(UP.read_text()) if UP.exists() else {}
USAGE, NFLTEAMS = _U.get("players", {}), _U.get("teams", {})


def norm(n):
    n = n.lower().replace(".", "").replace("'", "").replace("’", "")
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"[^a-z]", "", n)


def games_left(p, through=18):
    return sum(1 for w in range(CUR, through + 1) if opp(p, w))


def sched_grade(p, weeks):
    rk = [opp_rank(p, w) for w in weeks if opp(p, w)]
    if not rk:
        return None, None
    a = sum(rk) / len(rk)
    return round(a, 1), ("Easy" if a >= 21 else "Tough" if a <= 12 else "Average")


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


# ---------------- schedule-adjusted fantasy points allowed (the 4for4 / FantasyPros SOS idea) ----------------
# For every defense: how many more (or fewer) points did each position score against them than those same players normally score?
TEAMFIX = {"LA": "LAR", "WAS": "WSH", "JAC": "JAX", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
AFPA, AFPA_N, POS_GROUP_AVG, AFPA_RANK = {}, {}, {}, {}
_acc, _games, _grp = {}, {}, {}
for e in USAGE.values():
    pos_ = e.get("pos")
    if pos_ not in SKILL:
        continue
    wks = [x for x in e.get("weeks", []) if x["w"] < CUR]
    if len(wks) < 4:
        continue
    pts_ = [league_pts(x) for x in wks]
    avg_ = sum(pts_) / len(pts_)
    for x, pt in zip(wks, pts_):
        d = TEAMFIX.get(x.get("opp"), x.get("opp"))
        if not d:
            continue
        k = (d, pos_)
        _acc[k] = _acc.get(k, 0) + (pt - avg_)
        _games.setdefault(k, set()).add(x["w"])
        _grp[(d, pos_, x["w"])] = _grp.get((d, pos_, x["w"]), 0) + pt
for pos_ in SKILL:
    vals = [v for (d, ps, w), v in _grp.items() if ps == pos_]
    POS_GROUP_AVG[pos_] = sum(vals) / len(vals) if vals else 1
for (d, pos_), tot in _acc.items():
    n_ = len(_games[(d, pos_)])
    AFPA[(d, pos_)] = tot / n_          # extra points per game this defense gives the position, vs those players' norms
    AFPA_N[(d, pos_)] = n_
for pos_ in SKILL:
    L = sorted([d for (d, ps) in AFPA if ps == pos_], key=lambda d: AFPA[(d, pos_)])
    for i, d in enumerate(L):
        AFPA_RANK[(d, pos_)] = i + 1    # 1 = toughest, 32 = easiest (same direction as before)
print(f"schedule-adjusted points allowed: {len(AFPA)} defense/position pairs")


def opp_factor(p, w):
    """Matchup multiplier for player p in week w."""
    g = opp(p, w)
    if not g or g == "?":
        return 1.0
    d = g.lstrip("@")
    k = (d, p["pos"])
    if k in AFPA:
        shrink = AFPA_N[k] / (AFPA_N[k] + 4)            # small samples get pulled toward average
        return max(0.8, min(1.2, 1 + shrink * AFPA[k] / max(1.0, POS_GROUP_AVG[p["pos"]])))
    rk = opp_rank(p, w)
    return 1 + ((rk or 16.5) - 16.5) / 16.5 * WGT["matchup_swing"]


_espn_rank = opp_rank
def opp_rank(p, w):
    g = opp(p, w)
    if g and g != "?" and (g.lstrip("@"), p["pos"]) in AFPA_RANK:
        return AFPA_RANK[(g.lstrip("@"), p["pos"])]
    return _espn_rank(p, w)


PROF = {}
for n, p in P.items():
    if p["pos"] not in SKILL:
        continue
    pts = [p["wk"][k] for k in sorted(p["wk"], key=int) if int(k) < CUR]
    played = [x for x in pts if x != 0]
    e = USAGE.get(norm(n), {})
    u = [x for x in e.get("weeks", []) if x["w"] < CUR]
    pr = {"nfl": p["nfl"], "own": p["own"], "own_chg": p.get("own_chg"), "start_pct": p.get("start_pct"), "outlook": p.get("outlook") or None,
          "status": p.get("inj"), "ngs": e.get("ngs"), "pfr": e.get("pfr"), "report": e.get("injury") if (e.get("injury") or {}).get("week", 0) >= CUR - 1 else None, "depth": e.get("depth"),
          "team_ctx": NFLTEAMS.get(e.get("team") or "", None)}
    if len(played) >= 3:
        s = sorted(played)
        q = lambda f: s[min(len(s) - 1, int(f * (len(s) - 1) + 0.5))]
        hi_, lo_ = (25, 15) if p["pos"] == "QB" else (20, 8)
        pr.update(floor=round(q(0.25), 1), ceiling=round(q(0.75), 1), sd=round(statistics.pstdev(played), 1),
                  boom=round(100 * sum(x >= hi_ for x in played) / len(played)), bust=round(100 * sum(x < lo_ for x in played) / len(played)), games=len(played))
    if len(u) >= 3:
        last = u[-3:]
        g = lambda rows, k: mean([x.get(k) for x in rows])
        opps = [x.get("tgt", 0) + x.get("car", 0) for x in u]
        so, lo = sum(opps) / len(opps), sum(opps[-3:]) / 3
        pr.update(tsh=g(u, "tsh"), tsh3=g(last, "tsh"), ash=g(u, "ash"), csh=g(u, "csh"), csh3=g(last, "csh"), wopr=g(u, "wopr"), wopr3=g(last, "wopr"),
                  opp_g=round(so, 1), opp_g3=round(lo, 1), snap=g(u, "snap"), snap3=g(last, "snap"), epa=round(sum(x.get("epa", 0) for x in u), 1),
                  rz_tgt=sum(x.get("rz_tgt", 0) for x in u), rz_car=sum(x.get("rz_car", 0) for x in u), gl_car=sum(x.get("gl_car", 0) for x in u),
                  i10_tgt=sum(x.get("i10_tgt", 0) for x in u), trend=round(lo / so, 2) if so > 0 and p["pos"] != "QB" else None)
        xs = [x for x in u if x.get("xs")]
        if xs:  # expected points from usage, rescored with this league's settings (PPR, pass TD value, etc.)
            xl = [league_xpts(x["xs"]) for x in xs]
            al = [league_pts(x) for x in xs]
            xpg = sum(xl) / len(xl)
            pr.update(xfp=round(xpg, 1), xfp4=round(sum(xl[-4:]) / len(xl[-4:]), 1), fpoe=round(sum(al) / len(al) - xpg, 1))
            for x, xv in zip(xs, xl):
                x["xfp"] = round(xv, 1)
    gl = games_left(p, 17)
    if p.get("espn_ros") and gl:
        pg_ = p["espn_ros"] / gl
        if p["avg"] <= 0 or 0.35 * p["avg"] <= pg_ <= 2.5 * p["avg"]:   # ignore projections that don't make sense
            pr["espn_ros_pg"] = round(pg_, 1)
    pr["sos_ros"], pr["sos_ros_label"] = sched_grade(p, [w for w in WKS if CUR < w <= REG])
    pr["sos_po"], pr["sos_po_label"] = sched_grade(p, [PO2, PO3])
    pr["schedule"] = [{"w": w, "opp": opp(p, w) or "BYE", "rank": opp_rank(p, w)} for w in WKS]
    for x in u:
        x["pts"] = round(league_pts(x), 1)
    pr["log"] = [{k: x.get(k) for k in ("w", "opp", "pts", "xfp", "tgt", "rec", "recyd", "car", "rushyd", "td", "tsh", "csh", "snap", "att", "passyd", "rz_tgt", "rz_car")} for x in u]
    PROF[n] = pr

# ---------------- fill-in check: did he only score because the starter in front of him was hurt? ----------------
HURT = ("OUT", "INJURY_RESERVE", "SUSPENSION", "DOUBTFUL")
def team_weeks(nfl):
    ws = set()
    for e in USAGE.values():
        if e.get("team") == nfl:
            ws.update(x["w"] for x in e.get("weeks", []))
    return ws
FILLIN = {}
for n, p in P.items():
    if p["pos"] not in ("RB", "WR", "TE"):
        continue
    e = USAGE.get(norm(n)) or {}
    u = [x for x in e.get("weeks", []) if x.get("pts") is not None]
    if len(u) < 2 or ((e.get("depth") or {}).get("rank") == 1) or p.get("own", 0) >= 70:
        continue   # starters and guys the whole league rosters aren't fill-ins
    for m, q_ in P.items():
        if m == n or q_["nfl"] != p["nfl"] or q_["pos"] != p["pos"] or q_.get("inj") in HURT:
            continue
        em = USAGE.get(norm(m)) or {}
        if q_["avg"] < 0.8 * p["avg"] or ((em.get("depth") or {}).get("rank") != 1 and not (q_["avg"] > p["avg"] and q_.get("own", 0) >= 60)):
            continue   # the guy in front of him has to actually be the better player
        played = {x["w"] for x in em.get("weeks", []) if (x.get("snap") or 0) >= 25}
        out_w = {x["w"] for x in u if x["w"] not in played}
        pts_out = sum(league_pts(x) for x in u if x["w"] in out_w)
        tot = sum(league_pts(x) for x in u)
        if tot <= 0 or pts_out / tot < 0.6:
            continue
        with_ = [league_pts(x) for x in u if x["w"] in played]
        FILLIN[n] = {"starter": m, "out_weeks": sorted(out_w), "with_pg": (sum(with_) / len(with_)) if len(with_) >= 2 else None, "n_with": len(with_)}
        break
print(f"fill-in guys (scored mostly while the starter was out): {len(FILLIN)}", ", ".join(f"{k} (behind {v['starter']})" for k, v in FILLIN.items()))

V = {}
for n, p in P.items():
    r = recent(p)
    pr = PROF.get(n, {})
    xf = None
    if pr.get("xfp") is not None:
        xf = 0.5 * pr["xfp"] + 0.5 * pr.get("xfp4", pr["xfp"])
    parts = [(0.3, p["avg"])] + ([(0.25, r)] if r is not None else []) + ([(0.25, xf)] if xf else []) + ([(0.2, pr["espn_ros_pg"])] if pr.get("espn_ros_pg") else [])
    blend = sum(w_ * x for w_, x in parts) / sum(w_ for w_, _ in parts)
    if pr.get("trend"):  # usage going up or down lately moves value up to 10%
        blend *= max(0.9, min(1.1, 1 + 0.4 * (pr["trend"] - 1)))
    fi = FILLIN.get(n)
    if fi:   # his points came with the starter out; the starter's back, so value him as the backup he is now
        blend = min(blend, fi["with_pg"]) if fi["with_pg"] is not None else blend * 0.4
    base = OVR.get(n, blend)
    av = AVAIL.get(n)
    if av and av.get("v"):
        base = av["v"]
    v, q = {}, {}
    for w in WKS:
        if not opp(p, w):
            v[w], q[w] = 0.0, 0.0
            continue
        rk = opp_rank(p, w)
        f = opp_factor(p, w)
        v[w] = p["proj"] if (w == CUR and p.get("proj") is not None) else round(base * f, 2)
        inj = p.get("inj") or "ACTIVE"
        if av:
            q[w] = (1.0 if p.get("proj") else 0.0) if w == CUR else float(av.get(str(w), 0))
        elif inj in ("OUT", "INJURY_RESERVE", "SUSPENSION"):
            q[w] = 0.0
        elif inj == "DOUBTFUL":
            q[w] = 0.25 if w == CUR else 0.8
        elif inj == "QUESTIONABLE":
            q[w] = 0.75 if w == CUR else 0.95
        else:
            q[w] = 1.0
        if w == CUR and p.get("proj") == 0:
            q[w] = 0.0
    V[n] = {"v": v, "q": q, "base": round(base, 1), "recent": None if r is None else round(r, 1)}


def ev(n, w):
    x = V.get(n)
    return x["v"][w] * x["q"][w] if x and w in x["v"] else 0.0


# ---------------- lineups ----------------
def lineup(names, w, on=None, detail=False):
    pl = sorted(((ev(n, w) if on is None else (V[n]["v"][w] if on(n) else 0.0), n, P[n]["pos"]) for n in names), reverse=True)
    used, tot, lu = set(), 0.0, []
    for s, k in SLOTS:
        c = k
        for val, n, pos in pl:
            if not c:
                break
            if n in used:
                continue
            if pos in ELIG.get(s, ()):
                used.add(n); tot += val; c -= 1
                if detail:
                    lu.append({"slot": s, "player": n, "pts": round(val, 1)})
    return (tot, lu) if detail else tot


def scen(rost):
    """Per team per week: list of (prob, lineup total) over the top uncertain players being in or out."""
    sc = {}
    for t, names in rost.items():
        sc[t] = {}
        for w in WKS:
            unc = sorted([n for n in names if 0 < V[n]["q"][w] < 1 and V[n]["v"][w] > 4], key=lambda n: -V[n]["v"][w])[:4]
            L = []
            for mask in range(1 << len(unc)):
                pr, on = 1.0, set()
                for i, n in enumerate(unc):
                    qq = V[n]["q"][w]
                    if mask >> i & 1:
                        pr *= qq; on.add(n)
                    else:
                        pr *= 1 - qq
                L.append((pr, lineup(names, w, lambda n: (n in on) if n in unc else V[n]["q"][w] >= 0.5)))
            sc[t][w] = L
    return sc


IDS = sorted(TEAMS)
IX = {t: i for i, t in enumerate(IDS)}
W0 = np.array([TEAMS[t]["w"] + 0.5 * TEAMS[t].get("t", 0) for t in IDS], float)
PF0 = np.array([TEAMS[t]["pf"] for t in IDS], float)
REM = [m for m in M["sched"] if CUR <= m[0] <= REG]


def run(rost, N):
    sc = scen(rost)
    T = len(IDS)
    S = {}
    for w in WKS:
        arr = np.empty((N, T))
        for t in IDS:
            L = sc[t][w]
            pr = np.array([x[0] for x in L]); pr = pr / pr.sum()
            mu = np.array([x[1] for x in L])
            arr[:, IX[t]] = mu[rng.choice(len(L), N, p=pr)] if len(L) > 1 else mu[0]
        S[w] = arr + SD * rng.standard_normal((N, T))
    wins = np.tile(W0, (N, 1)); pf = np.tile(PF0, (N, 1))
    wkwin = np.zeros(T)
    for wk, h, a in REM:
        sh, sa = S[wk][:, IX[h]], S[wk][:, IX[a]]
        hw = sh > sa
        wins[:, IX[h]] += hw; wins[:, IX[a]] += ~hw
        pf[:, IX[h]] += sh; pf[:, IX[a]] += sa
        if wk == CUR:
            wkwin[IX[h]] += hw.mean(); wkwin[IX[a]] += (~hw).mean()
    seeds = np.argsort(-(wins * 100000 + pf), axis=1, kind="stable")
    r = np.arange(N)
    g = lambda w, x: S[w][r, x]
    NP = 4 if SET.get("playoff_teams") == 4 else 6
    s = [seeds[:, i] for i in range(NP)]
    pick = lambda w, a, b: (np.where(g(w, a) > g(w, b), a, b), np.where(g(w, a) > g(w, b), b, a))
    if NP == 6:   # top 2 seeds get a bye, then semis, then final
        w36, _ = pick(PO1, s[2], s[5]); w45, _ = pick(PO1, s[3], s[4])
        f1, l1 = pick(PO2, s[0], w45); f2, l2 = pick(PO2, s[1], w36)
        ch, ru = pick(PO3, f1, f2); t3, t4 = pick(PO3, l1, l2)
    else:         # 4 teams: semis then final
        f1, l1 = pick(PO1, s[0], s[3]); f2, l2 = pick(PO1, s[1], s[2])
        ch, ru = pick(PO2, f1, f2); t3, t4 = pick(PO2, l1, l2)
    cnt = lambda x: np.bincount(x.ravel(), minlength=T) / N * 100
    po = cnt(seeds[:, :NP]); bye = cnt(seeds[:, :2]) if NP == 6 else np.zeros(T); s1 = cnt(seeds[:, 0])
    out = {}
    for t in IDS:
        i = IX[t]
        out[t] = {"po": po[i], "bye": bye[i], "s1": s1[i], "ch": cnt(ch)[i], "fi": cnt(ch)[i] + cnt(ru)[i],
                  "p3": cnt(t3)[i], "wk": wkwin[i] * 100,
                  "means": {w: float(sum(p_ * m_ for p_, m_ in sc[t][w]) / sum(p_ for p_, _ in sc[t][w])) for w in WKS}}
    return out


def r1(x):
    return round(float(x), 1)


t0 = time.time()
base = run(ROSTER, A["sims"]["base"])
mine = base[ME]
print(f"base sims done {time.time() - t0:.1f}s, title {mine['ch']:.1f}%")

# which playoff weeks matter for me: if I'm likely on a bye, the semis + final
PW = ([PO1, PO2] if SET.get("playoff_teams") == 4 else [PO2, PO3] if mine["bye"] >= 50 else [PO1, PO2, PO3])
REGW = [w for w in WKS if CUR < w <= REG]
ros = lambda n: sum(ev(n, w) for w in WKS if w > CUR)
ros_pg = lambda n: ros(n) / max(1, sum(1 for w in WKS if w > CUR and opp(P[n], w)))   # per game he plays (byes don't count)
po_pts = lambda n: sum(ev(n, w) for w in (PO2, PO3))
reg_pts = lambda n: sum(ev(n, w) for w in REGW)
team_score = lambda rost, t, ws: sum(lineup(rost[t], w) for w in ws)
my_score = lambda rost: team_score(rost, ME, REGW) + WGT["playoff_week_weight"] * team_score(rost, ME, PW)
MY_BASE = my_score(ROSTER)
status = {t: ("contender" if base[t]["po"] >= 90 else "bubble" if base[t]["po"] >= 8 else "out") for t in IDS}
clone = lambda r: {k: list(v) for k, v in r.items()}

# ---------------- trade value (points over a replacement-level starter) ----------------
REPL_N = {"QB": 13, "RB": 30, "WR": 34, "TE": 13}
ROSW = [w for w in WKS if w > CUR]
ppg_ros = lambda n: sum(ev(n, w) for w in ROSW) / max(1, len(ROSW))
ppg_po = lambda n: sum(ev(n, w) for w in PW) / max(1, len(PW))
REPL = {}
for pos, k in REPL_N.items():
    L = sorted((ppg_ros(n) for n, p in P.items() if p["pos"] == pos), reverse=True)
    L2 = sorted((ppg_po(n) for n, p in P.items() if p["pos"] == pos), reverse=True)
    REPL[pos] = (L[min(k, len(L) - 1)] if L else 0, L2[min(k, len(L2) - 1)] if L2 else 0)
# market value = how the rest of the league sees a guy: season ppg + last 4 games, times how much of the rest of the season he'll play
def avail(n):  # share of his remaining games he's expected to play (bye weeks don't count against him)
    gw = [w for w in ROSW if opp(P[n], w)]
    return sum(V[n]["q"][w] for w in gw) / max(1, len(gw))
def pub(n):
    base_ = OVR.get(n, 0.6 * P[n]["avg"] + 0.4 * (V[n]["recent"] if V[n]["recent"] is not None else P[n]["avg"]))
    return base_ * avail(n)
REPL_PUB = {}
for pos, k in REPL_N.items():
    L = sorted((pub(n) for n, p in P.items() if p["pos"] == pos), reverse=True)
    REPL_PUB[pos] = L[min(k, len(L) - 1)] if L else 0
MKT = {n: max(0.0, pub(n) - REPL_PUB[p["pos"]]) for n, p in P.items() if p["pos"] in SKILL}
MV, MYV = {}, {}
for n, p in P.items():
    if p["pos"] not in SKILL:
        continue
    ra, rp = REPL[p["pos"]]
    trend = (PROF.get(n) or {}).get("trend") or 1
    MV[n] = max(0.0, ppg_ros(n) - ra) * max(0.9, min(1.1, trend))          # what the league should value him at
    MYV[n] = max(0.0, 0.4 * (sum(ev(n, w) for w in REGW) / max(1, len(REGW)) - ra) + 0.6 * (ppg_po(n) - rp))  # what he's worth to a team on a bye
_mk, _my = max(MKT.values() or [1]) or 1, max(MYV.values() or [1]) or 1
for n in MV:
    PROF.setdefault(n, {})
    PROF[n]["value"] = round(100 * MKT[n] / _mk)
    PROF[n]["my_value"] = round(100 * MYV[n] / _my)
mval = lambda n: MKT.get(n, 0) + 0.3  # tiny floor so bench guys aren't worth exactly zero

# ---------------- rarity tiers: rank at his position by healthy per-game value (injuries don't drop a tier) ----------------
TIER_CUTS = {"QB": (2, 5, 10, 18), "TE": (2, 5, 10, 18), "RB": (3, 8, 18, 36), "WR": (3, 8, 20, 40), "D": (1, 3, 6, 12), "K": (1, 3, 6, 12)}
TIERS = {}
for pos, cuts in TIER_CUTS.items():
    talent = lambda n: max(V[n]["base"], P[n]["avg"] if len([x for x in P[n]["wk"].values() if x]) >= 4 else 0)
    L = sorted([n for n, p in P.items() if p["pos"] == pos], key=lambda n: -talent(n))
    for i, n in enumerate(L):
        TIERS[n] = "legendary" if i < cuts[0] else "epic" if i < cuts[1] else "rare" if i < cuts[2] else "uncommon" if i < cuts[3] else "common"
for n in PROF:
    PROF[n]["tier"] = TIERS.get(n)

# positions where you only ever roster one (no backup TE / D / K), from assumptions.json
ONE_ONLY = set((A.get("waiver_rules") or {}).get("one_only", ["TE", "D", "K"]))

# ---------------- trade search ----------------
cands = []
mine_pool = [n for n in ROSTER[ME] if n not in CORE and P[n]["pos"] in SKILL and ros(n) > 6]
for t in IDS:
    if t == ME:
        continue
    theirs = sorted([n for n in ROSTER[t] if P[n]["pos"] in SKILL and ros(n) > 15], key=lambda n: -ros(n))[:13]
    gives = [[a] for a in mine_pool] + [list(c) for c in itertools.combinations(mine_pool, 2)]
    gets = [[a] for a in theirs] + [list(c) for c in itertools.combinations(theirs, 2)]
    base_t = team_score(ROSTER, t, REGW)
    for G in gives:
        for T_ in gets:
            if (len(G) == 2 and min(PROF[x].get("value", 0) for x in G) < 1) or (len(T_) == 2 and min(PROF[x].get("value", 0) for x in T_) < 5):
                continue  # no pointless throw-ins
            r = clone(ROSTER)
            for n in G: r[ME].remove(n); r[t].append(n)
            for n in T_: r[t].remove(n); r[ME].append(n)
            if any(sum(1 for x in r[ME] if P[x]["pos"] == pos_ and x not in AVAIL) > 1 for pos_ in ONE_ONLY if any(P[x]["pos"] == pos_ for x in T_)):
                continue   # would leave me holding a backup TE / D / K
            gain = my_score(r) - MY_BASE
            if gain < (3 if len(G) + len(T_) == 4 else 1.5):
                continue
            # value check uses market value (what other managers see), with a 25% tax on the side sending 2 players
            vin = sum(mval(n) for n in G) * (0.75 if len(G) > len(T_) else 1)   # 2-for-2 counts both sides fully
            vout = sum(mval(n) for n in T_) * (0.75 if len(T_) > len(G) else 1)
            ratio = vin / max(vout, 0.5)
            best_g, best_t = max(mval(n) for n in G), max(mval(n) for n in T_)
            need = (team_score(r, t, REGW) - base_t) / max(1, len(REGW)) if REGW else 0
            # nobody gives you two good players for one unless your guy is clearly the best player in the deal
            two_for_one_ok = len(T_) < 2 or best_g >= 1.15 * best_t
            real = two_for_one_ok and (ratio >= 1.1 if status[t] == "out" else (ratio >= 1.0 and need >= -1))
            stretch = (len(T_) < 2 or best_g >= best_t) and ratio >= 0.8 and need >= -6
            if not real and not stretch:
                continue
            cands.append({"t": t, "G": G, "T": T_, "gain": gain, "ratio": ratio, "need": need, "real": real, "r": r})
cands.sort(key=lambda c: -c["gain"])
seen, top, nr, ns = set(), [], 0, 0
quota = A["sims"]["trades_to_sim"]
for c in cands:
    k = ("+".join(c["T"]), c["real"])
    if k in seen or (c["real"] and nr >= quota * 2 // 3) or (not c["real"] and ns >= quota // 3) or sum(1 for x in top if x["T"] == c["T"]) >= 1:
        continue
    seen.add(k); top.append(c)
    if c["real"]: nr += 1
    else: ns += 1

# ---------------- the story behind each trade: why them, why me, and a message I can send ----------------
SLOTNAME = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "FLEX": "FLEX", "RB/WR/TE": "FLEX", "OP": "SUPERFLEX", "D/ST": "D", "K": "K"}
def nice_slot(s):
    return SLOTNAME.get(s, s)
def first(n):
    """Short name for chat: last name, skipping Jr./III, team name for defenses."""
    if n.endswith("D/ST"):
        return n.split(" ")[0]
    parts = [x for x in n.split(" ") if x.rstrip(".") not in ("Jr", "Sr", "II", "III", "IV", "V")]
    return parts[-1] if len(parts) > 1 else n
def poss(n):
    return n + ("'" if n.endswith("s") else "'s")
def slot_swap(before_lu, after_lu, new_players, leaving=()):
    """For a lineup before/after a trade: which new guy starts, in what slot, and who he pushes out (or whose spot he takes over)."""
    out = []
    aset = {x["player"]: x for x in after_lu}
    pushed = [x for x in before_lu if x["player"] not in aset and x["player"] not in leaving]
    for n in new_players:
        if n in aset:
            rep = min(pushed, key=lambda x: x["pts"]) if pushed else None
            if rep: pushed.remove(rep)
            out.append((n, nice_slot(aset[n]["slot"]), aset[n]["pts"], rep))
    return out
def depth(t, pos, w):
    """How many guys at a position on a team would score at least a starter-ish number."""
    bar = {"QB": 14, "RB": 9, "WR": 9, "TE": 7}.get(pos, 99)
    return sum(1 for n in ROSTER[t] if P[n]["pos"] == pos and ev(n, w) >= bar)
def trade_story(c):
    t, G, T_ = c["t"], c["G"], c["T"]
    w = next((x for x in REGW), CUR) if status[t] != "out" else PO2
    tb, tlu_b = lineup(ROSTER[t], w, detail=True)
    ta, tlu_a = lineup(c["r"][t], w, detail=True)
    rec = f'{TEAMS[t]["w"]}-{TEAMS[t]["l"]}'
    po = base[t]["po"]
    their_starting = {x["player"] for x in tlu_b}
    # what they get out of it
    fills = slot_swap(tlu_b, tlu_a, G, T_)
    skill_b = [x for x in tlu_b if P[x["player"]]["pos"] in SKILL and x["player"] not in T_]
    weak = min(skill_b, key=lambda x: x["pts"]) if skill_b else None
    sit = ("they're " + rec + " and fighting for a playoff spot (" + f"{po:.0f}%" + " to make it), so they need points now") if status[t] == "bubble" else \
          ("they're " + rec + " and basically locked into the playoffs, so they're building for a title run") if status[t] == "contender" else \
          ("they're " + rec + " and almost out of it, so they should care about value more than wins")
    why_them = [sit[0].upper() + sit[1:] + "."]
    if fills:
        n, sl, pts, rep = fills[0]
        why_them.append(f"{n} would start at {sl} for them" + (f" over {rep['player']} ({rep['pts']:.1f} pts)" if rep else f" in {poss(first(T_[0]))} spot") + f", about {pts:.1f} pts a week.")
    elif weak:
        why_them.append(f"Their weakest starter is {weak['player']} at {nice_slot(weak['slot'])} ({weak['pts']:.1f} pts).")
    spare = []
    for n in T_:
        pos = P[n]["pos"]
        if n not in their_starting:
            spare.append(f"{n} is on their bench right now")
        elif depth(t, pos, w) - sum(1 for x in tlu_b if P[x["player"]]["pos"] == pos) >= 1:
            spare.append(f"they're deep at {pos} ({depth(t, pos, w)} startable guys), so {first(n)} is easier to give up")
    if spare:
        why_them.append(spare[0][0].upper() + spare[0][1:] + ".")
    d = c["need"]
    if abs(d) >= 0.5:
        why_them.append(f"Their lineup goes {'up' if d > 0 else 'down'} about {abs(d):.1f} pts a week.")
    # what I get out of it
    my_w = PW[-1]
    _, mlu_b = lineup(ROSTER[ME], my_w, detail=True)
    _, mlu_a = lineup(c["r"][ME], my_w, detail=True)
    mine_fills = slot_swap(mlu_b, mlu_a, T_, G)
    po_gain = sum(lineup(c["r"][ME], x) - lineup(ROSTER[ME], x) for x in PW)
    why_me = []
    for n, sl, pts, rep in mine_fills[:2]:
        why_me.append(f"{n} starts at {sl} for you in the playoffs ({pts:.1f} pts)" + (f" over {rep['player']} ({rep['pts']:.1f})." if rep else f" in {poss(first(G[0]))} spot."))
    lost = [n for n in G if n in {x["player"] for x in mlu_b}]
    if lost:
        why_me.append(f"You lose {', '.join(lost)} from your playoff lineup, but it nets out to {po_gain:+.1f} pts across the playoff weeks.")
    else:
        why_me.append(f"{' and '.join(G)} {'is' if len(G) == 1 else 'are'} not starting for you in the playoffs anyway, so it's +{po_gain:.1f} pts across the playoff weeks.")
    # a message to send them
    gnames = " and ".join(G); tnames = " and ".join(T_)
    best_g = max(G, key=lambda n: mval(n)); best_t = max(T_, key=lambda n: mval(n))
    reasons = []
    if d >= 0.5:
        reasons.append(f"it bumps your lineup about {d:.1f} pts a week" + (" for the playoff push" if status[t] == "bubble" else ""))
    if fills:
        n, sl, pts, rep = fills[0]
        reasons.append(f"{first(n)} slides right into your {sl} spot" + (f" over {first(rep['player'])}" if rep else ""))
    if len(T_) > len(G):
        reasons.append(f"you turn two guys into the best player in the deal and open a roster spot for a waiver pickup" if mval(best_g) >= mval(best_t) else "you open a roster spot for a waiver pickup")
    elif len(G) > len(T_):
        reasons.append(f"you get depth for injuries and byes down the stretch")
    for n in T_:
        if n not in their_starting:
            reasons.append(f"{first(n)} is just sitting on your bench"); break
    if not reasons and mval(best_g) > mval(best_t):
        reasons.append(f"you're getting the best player in the deal")
    if len(reasons) < 2:
        for n in T_:
            pos = P[n]["pos"]
            if n in their_starting and depth(t, pos, w) - sum(1 for x in tlu_b if P[x["player"]]["pos"] == pos) >= 1:
                reasons.append(f"you're deep at {pos} so you can cover {poss(first(n))} spot"); break
    if status[t] == "out" and not reasons:
        reasons.append("you're probably not making the playoffs, so you might as well get the better long-term guy")
    pitch = f"Would you do {gnames} for {tnames}? " + (("I think " + reasons[0] + (", and " + reasons[1] if len(reasons) > 1 else "") + ".") if reasons else "Let me know if you'd want to tweak it.")
    if d < -0.5 and not c["real"]:
        why_them.append("Heads up: on paper this makes their lineup worse, so expect them to counter or say no.")
    elif d < -0.5:
        why_them.append("Their lineup dips a little, but they get fair value back, so it's a reasonable ask.")
    return {"why_them": why_them, "why_me": why_me, "pitch": pitch}

trades = []
for c in top:
    res_full = run(c["r"], A["sims"]["per_trade"]); res = res_full[ME]
    trades.append({"partner": TNAME[c["t"]], "partner_id": c["t"], "partner_status": status[c["t"]], "give": " + ".join(c["G"]),
                   "get": " + ".join(c["T"]), "title_pct": r1(res["ch"]), "change_pts": r1(res["ch"] - mine["ch"]), "final_pct": r1(res["fi"]),
                   "value_ratio": round(c["ratio"], 2), "partner_weekly_gain": r1(c["need"]), "kind": "realistic" if c["real"] else "stretch",
                   "blocked": c["t"] in NO_TRADE, "wk16": r1(res["means"][PO2]), "wk17": r1(res["means"][PO3]), "wk16_before": r1(mine["means"][PO2]), "wk17_before": r1(mine["means"][PO3]),
                   # the "why" breakdown
                   "weeks": [{"w": w, "before": r1(lineup(ROSTER[ME], w)), "after": r1(lineup(c["r"][ME], w)), "po": w in PW} for w in REGW + [w for w in (PO1, PO2, PO3) if w not in REGW]],
                   "their_before": r1(team_score(ROSTER, c["t"], REGW) / max(1, len(REGW))), "their_after": r1(team_score(c["r"], c["t"], REGW) / max(1, len(REGW))),
                   "their_po_pct": r1(base[c["t"]]["po"]), "their_title_after": r1(res_all[c["t"]]["ch"]) if (res_all := res_full) else None,
                   "mv_send": r1(sum(MKT.get(n, 0) for n in c["G"])), "mv_get": r1(sum(MKT.get(n, 0) for n in c["T"])), **trade_story(c)})
trades = [x for x in trades if x["change_pts"] >= 1.0]   # anything smaller is noise, not worth a trade
trades.sort(key=lambda x: (-x["change_pts"]))
print(f"trades: {len(cands)} candidates, {len(trades)} simmed, {time.time() - t0:.1f}s")

# ---------------- waivers ----------------
fa = [n for n, p in P.items() if p["t"] == 0 and p["pos"] in SKILL + ("D", "K")]
bench = sorted([n for n in ROSTER[ME] if n not in CORE and n not in AVAIL and (P[n].get("own") or 0) < 60], key=lambda n: ros(n) + po_pts(n))   # don't drop hurt guys we expect back, or names the league still values (trade those instead)
waivers = []
for n in fa:
    pos = P[n]["pos"]
    if pos in ONE_ONLY:   # never roster a backup TE/D/K: only a straight swap for the one you have
        same = sorted([x for x in ROSTER[ME] if P[x]["pos"] == pos and x not in CORE], key=ros)
        if not same:
            continue
        drop = same[0]
    else:
        if not bench:
            continue
        drop = next((x for x in bench if P[x]["pos"] == pos and ros(x) < ros(n)), bench[0])
        if P[drop]["pos"] in ONE_ONLY and sum(1 for x in ROSTER[ME] if P[x]["pos"] == P[drop]["pos"]) <= 1:
            drop = next((x for x in bench if P[x]["pos"] not in ONE_ONLY), drop)
    r = clone(ROSTER); r[ME].remove(drop); r[ME].append(n)
    waivers.append({"player": n, "pos": pos, "nfl": P[n]["nfl"], "drop": drop, "ros_gain": r1(my_score(r) - MY_BASE),
                    "this_week_gain": r1(lineup(r[ME], CUR) - lineup(ROSTER[ME], CUR)), "ros_ppg": r1(ros_pg(n)),
                    "playoff_pts": r1(po_pts(n)), "opp16": opp(P[n], PO2) or "BYE", "opp17": opp(P[n], PO3) or "BYE", "own_pct": P[n]["own"], "_r": r})
waivers = [x for x in waivers if x["ros_gain"] > 0 or (x["this_week_gain"] > 0 and x["ros_gain"] > -3)]
waivers.sort(key=lambda x: -(x["ros_gain"] + 2 * x["this_week_gain"]))
waivers = waivers[:12]
for x in waivers[:6]:
    if x["ros_gain"] > 0:
        x["change_pts"] = r1(run(x["_r"], A["sims"]["per_trade"])[ME]["ch"] - mine["ch"])
for x in waivers:
    x.pop("_r")

# ---------------- what could swing it: my key guys hurt for the playoffs ----------------
swing = []
keyguys = sorted([n for n in ROSTER[ME] if P[n]["pos"] in SKILL], key=lambda n: -po_pts(n))[:4]
for n in keyguys:
    saved = dict(V[n]["q"])
    for w in (PO1, PO2, PO3):
        V[n]["q"][w] = 0.0
    res = run(ROSTER, A["sims"]["per_trade"])[ME]
    V[n]["q"] = saved
    swing.append({"what": f"{n} out for the playoffs", "change_pts": r1(res["ch"] - mine["ch"])})
swing.sort(key=lambda x: x["change_pts"])

# ---------------- rankings ----------------
nw = max(1, len([w for w in WKS if w > CUR]))
ranks = []
for n, p in P.items():
    if p["pos"] not in SKILL:
        continue
    o = TNAME.get(p["t"], "Free agent")
    ranks.append({"player": n, "pos": p["pos"], "nfl": p["nfl"], "owner": "You" if p["t"] == ME else o, "ros_ppg": r1(ros_pg(n)),
                  "reg_pts": r1(reg_pts(n)), "playoff_pts": r1(po_pts(n)), "opp16": opp(p, PO2) or "BYE", "rank16": opp_rank(p, PO2),
                  "opp17": opp(p, PO3) or "BYE", "rank17": opp_rank(p, PO3), "recent": V[n]["recent"], "status": p["inj"],
                  "value": PROF.get(n, {}).get("value"), "my_value": PROF.get(n, {}).get("my_value"),
                  "xfp": PROF.get(n, {}).get("xfp"), "fpoe": PROF.get(n, {}).get("fpoe"), "trend": PROF.get(n, {}).get("trend"),
                  "sos_ros": PROF.get(n, {}).get("sos_ros_label"), "sos_po": PROF.get(n, {}).get("sos_po_label"), "own_chg": p.get("own_chg")})
for pos in SKILL:
    L = [x for x in ranks if x["pos"] == pos]
    for i, x in enumerate(sorted(L, key=lambda x: -x["ros_ppg"])): x["ros_n"] = i + 1
    for i, x in enumerate(sorted(L, key=lambda x: -x["playoff_pts"])): x["po_n"] = i + 1
lim = {"QB": 20, "TE": 20, "RB": 45, "WR": 55}
ranks = [x for x in ranks if x["ros_n"] <= lim[x["pos"]] or x["po_n"] <= lim[x["pos"]] or x["owner"] == "You"]
ranks.sort(key=lambda x: -x["playoff_pts"])


# ---------------- breakout radar: who's about to become a weekly starter? ----------------
# 1) opportunity right now = expected fantasy points from his targets/carries the last 3 games (league scoring)
# 2) + work freed up by a teammate who just got hurt, 3) x efficiency (shrunk toward average) x schedule,
# 4) Monte Carlo: role sticks or fades back + week-to-week noise -> chance he's a weekly starter the rest of the way
OUTS = ("OUT", "INJURY_RESERVE", "SUSPENSION")
GROUP = lambda pos: ("RB",) if pos == "RB" else ("WR", "TE")
def xlist(n):
    e = USAGE.get(norm(n)) or {}
    return [x for x in e.get("weeks", []) if x["w"] < CUR and x.get("xs")]
STARTER_N = {"RB": 24, "WR": 24, "TE": 12}
THR = {}
for pos, k in STARTER_N.items():
    L = sorted((ros_pg(n) for n, p in P.items() if p["pos"] == pos and p.get("inj") not in OUTS), reverse=True)
    THR[pos] = L[k - 1] if len(L) >= k else 8
team_wk = {}
for e in USAGE.values():
    for x in e.get("weeks", []):
        team_wk.setdefault(e.get("team"), set()).add(x["w"])
brk_rng = np.random.default_rng(777)
breakouts = []
for n, p in P.items():
    pos = p["pos"]
    if pos not in STARTER_N or p.get("inj") in OUTS or n in FILLIN:
        continue
    u = xlist(n)
    if len(u) < 3:
        continue
    e = USAGE.get(norm(n)) or {}
    tw = sorted(w for w in team_wk.get(e.get("team"), ()) if w < CUR)
    if not tw or u[-1]["w"] < tw[-1] - 1:   # hasn't played in his team's last 2 games
        continue
    xl = [league_xpts(x["xs"]) for x in u]; al = [league_pts(x) for x in u]
    now = sum(xl[-3:]) / 3
    before = sum(xl[:-3]) / len(xl[:-3]) if len(xl) >= 5 else sum(xl) / len(xl)
    # teammates who just went down (played in the team's recent games, now out) free up their work
    vac, vac_who = 0.0, []
    recent_team = set(tw[-3:])
    for m, q_ in P.items():
        if m == n or q_["nfl"] != p["nfl"] or q_["pos"] not in GROUP(pos) or q_.get("inj") not in OUTS:
            continue
        um = xlist(m)
        if sum(1 for x in um if x["w"] in recent_team) < 2:
            continue   # already out during his last 3 games, so it's baked into his numbers
        mx = sum(league_xpts(x["xs"]) for x in um[-4:]) / len(um[-4:])
        mates = [k for k, q2 in P.items() if q2["nfl"] == p["nfl"] and q2["pos"] in GROUP(pos) and q2.get("inj") not in OUTS and len(xlist(k)) >= 1]
        pool = sum(sum(league_xpts(x["xs"]) for x in xlist(k)[-3:]) / max(1, len(xlist(k)[-3:])) for k in mates) or 1
        share = min(0.6, now / pool)
        vac += 0.7 * mx * share; vac_who.append(m)
    g = len(xl); ratio = sum(al) / max(1, sum(xl))
    eff = 1 + (ratio - 1) * g / (g + 8)
    ng, pf = e.get("ngs") or {}, e.get("pfr") or {}
    tal = []
    try:
        if pos == "RB" and float(ng.get("ryoe_att") or 0) >= 0.3: eff *= 1.03; tal.append(f"he gains {float(ng['ryoe_att']):+.1f} yards per carry more than his blocking gives him")
        if pos != "RB" and float(ng.get("sep") or 0) >= (3.0 if pos == "TE" else 3.3): eff *= 1.03; tal.append(f"he gets open ({float(ng['sep']):.1f} yards of separation)")
        if pos != "RB" and float(ng.get("yacoe") or 0) >= 1: eff *= 1.02; tal.append("he makes extra yards after the catch")
    except (TypeError, ValueError):
        pass
    eff = max(0.8, min(1.25, eff))
    fw = [opp_factor(p, w) for w in ROSW if opp(p, w)]
    if not fw:
        continue
    sched = sum(fw) / len(fw)
    proj = (now + vac) * eff * sched
    fade = (before + 0.5 * vac) * eff * sched
    grew = all(x > before for x in xl[-3:])
    p_stick = 0.75 if grew or vac > 0 else 0.55
    N = 20000
    stick = brk_rng.random(N) < p_stick
    mu = np.where(stick, proj, 0.5 * (proj + fade)) * np.exp(0.18 * brk_rng.standard_normal(N) - 0.0162)
    games = [w for w in ROSW if opp(p, w)]
    k = 1 / 0.55 ** 2   # gamma shape for ~55% week-to-week swing
    wk = brk_rng.gamma(k, (mu / k)[:, None], (N, len(games)))
    avg = wk.mean(1)
    pw = [i for i, w in enumerate(games) if w in PW]
    p_starter = float((avg >= THR[pos]).mean())
    p_boom = float((wk[:, pw] >= 20).any(1).mean()) if pw else 0.0
    if p["avg"] >= THR[pos] * 0.95 or proj < THR[pos] * 0.7:
        continue   # already a starter, or not close
    why = []
    if now - before >= 1.5:
        why.append(f"His role is growing: his targets and carries are worth {now:.1f} pts a game the last 3 games, up from {before:.1f} before.")
    if vac_who:
        why.append(f"{' and '.join(vac_who)} just went down, which opens up about {vac:.1f} pts a game of work for him.")
    if ratio < 0.9 and g >= 4:
        why.append(f"He's been unlucky: scoring {sum(al)/g:.1f} a game when his usage says {sum(xl)/g:.1f}. That usually catches up.")
    s3, sa = mean([x.get("snap") for x in u[-3:]]), mean([x.get("snap") for x in u])
    if s3 and sa and s3 - sa >= 8:
        why.append(f"He's on the field more: {s3:.0f}% of snaps lately vs {sa:.0f}% on the season.")
    if tal:
        why.append("Talent check: " + ", and ".join(tal) + ".")
    if sched >= 1.05:
        why.append("Easy schedule the rest of the way.")
    elif sched <= 0.95:
        why.append("Heads up: tough schedule the rest of the way.")
    if not why:
        why.append(f"Steady usage worth about {now:.1f} pts a game, which is close to starter level.")
    owner = "Free agent" if p["t"] == 0 else TNAME.get(p["t"], "?")
    action = "ADD" if p["t"] == 0 else "STASH" if p["t"] == ME else "BUY"
    if action == "ADD" and pos in ONE_ONLY and any(P[x]["pos"] == pos for x in ROSTER[ME]):
        action = "WATCH"   # no backup TE: only worth it as a straight swap
    breakouts.append({"player": n, "pos": pos, "nfl": p["nfl"], "owner": owner, "mine": p["t"] == ME, "action": action, "own": p.get("own"),
                      "score": round(100 * p_starter), "p_starter": r1(100 * p_starter), "p_boom": r1(100 * p_boom), "season": r1(p["avg"]),
                      "usage_now": r1(now), "usage_before": r1(before), "proj": r1(proj), "median": r1(float(np.median(avg))), "threshold": r1(THR[pos]),
                      "trend": [r1(x) for x in xl], "pts": [r1(x) for x in al], "weeks": [x["w"] for x in u], "why": why})
breakouts.sort(key=lambda x: (-x["score"], -x["proj"]))
breakouts = breakouts[:30]
print(f"breakout radar: {len(breakouts)} candidates; top: " + ", ".join(f"{b['player']} {b['score']}%" for b in breakouts[:6]))

# ---------------- league, bubble, my team ----------------
league = []
for t in IDS:
    b = base[t]
    league.append({"id": t, "team": TNAME[t], "me": t == ME, "record": f'{TEAMS[t]["w"]}-{TEAMS[t]["l"]}', "pf": TEAMS[t]["pf"], "status": status[t],
                   "playoffs_pct": r1(b["po"]), "bye_pct": r1(b["bye"]), "seed1_pct": r1(b["s1"]), "champ_pct": r1(b["ch"]), "final_pct": r1(b["fi"]),
                   "win_this_week_pct": r1(b["wk"]), "wk16_lineup": r1(b["means"][PO2]), "wk17_lineup": r1(b["means"][PO3])})
league.sort(key=lambda x: (-x["champ_pct"], -x["playoffs_pct"]))
# ---------------- every team's card ----------------
def team_week(t, w):
    tot_, lu_ = lineup(ROSTER[t], w, detail=True)
    for x in lu_:
        n = x["player"]; x["opp"] = opp(P[n], w) or "BYE"; x["rank"] = opp_rank(P[n], w); x["chance"] = round(V[n]["q"][w], 2) if n in V else 1
    lab = "Final" if w == PO3 else "Semis" if w == PO2 else ("Round 1" if SET.get("playoff_teams") != 4 else "Semis") if w == PO1 else f"Week {w}"
    if SET.get("playoff_teams") == 4 and w == PO2: lab = "Final"
    return {"w": w, "label": lab, "total": r1(tot_), "expected": r1(base[t]["means"][w]), "lineup": lu_}
team_cards = {}
for t in IDS:
    rost = ROSTER[t]
    tw_tot, tw_lu = lineup(rost, CUR, detail=True)
    po_tot, po_lu = lineup(rost, PO2, detail=True)
    starters = {x["player"] for x in po_lu}
    team_cards[str(t)] = {"id": t, "name": TNAME[t], "me": t == ME, "record": f'{TEAMS[t]["w"]}-{TEAMS[t]["l"]}', "pf": TEAMS[t]["pf"],
        "odds": {"po": r1(base[t]["po"]), "bye": r1(base[t]["bye"]), "ch": r1(base[t]["ch"]), "fi": r1(base[t]["fi"])}, "status": status[t],
        "weekly": [r1(base[t]["means"][w]) for w in WKS], "weeks": WKS,
        "this_week": {"total": r1(tw_tot), "lineup": tw_lu}, "semis": {"week": PO2, "total": r1(po_tot), "lineup": po_lu},
        "roster": sorted([{"player": n, "pos": P[n]["pos"], "nfl": P[n]["nfl"], "status": P[n].get("inj"), "value": (PROF.get(n) or {}).get("value"),
                           "ros_pg": r1(ros_pg(n)), "playoff_pts": r1(po_pts(n)), "this_week": r1(ev(n, CUR)), "starter": n in starters} for n in rost],
                         key=lambda x: (not x["starter"], ["QB", "RB", "WR", "TE", "D", "K"].index(x["pos"]) if x["pos"] in ("QB", "RB", "WR", "TE", "D", "K") else 9, -x["playoff_pts"])),
        "schedule": [{"w": wk, "opp": TNAME[a if h == t else h]} for wk, h, a in REM if t in (h, a)],
        "weeks_lu": [team_week(t, w) for w in WKS]}
nextw = min(CUR + 1, PO3)
bubble = []
for t in IDS:
    if t == ME or status[t] == "contender":
        continue
    _, lu = lineup(ROSTER[t], nextw, detail=True)
    by = {}
    for x in lu:
        by.setdefault(x["slot"], []).append(x["pts"])
    weak = min(((s, min(by.get(s, [0]))) for s in SKILL), key=lambda x: x[1])
    starters = {x["player"] for x in lu}
    extra = sorted([n for n in ROSTER[t] if n not in starters and P[n]["pos"] in SKILL and ros(n) > 40], key=lambda n: -ros(n))[:3]
    bubble.append({"team": TNAME[t], "id": t, "record": f'{TEAMS[t]["w"]}-{TEAMS[t]["l"]}', "playoffs_pct": r1(base[t]["po"]), "status": status[t],
                   "needs": weak[0], "weakest_starter_pts": r1(weak[1]), "has_extra": ", ".join(extra) or "none", "blocked": t in NO_TRADE})
bubble.sort(key=lambda x: -x["playoffs_pct"])
my_players = []
for n in ROSTER[ME]:
    p = P[n]
    if p["pos"] == "K":
        continue
    o = {"player": n, "pos": p["pos"], "status": p["inj"], "season_avg": p["avg"], "recent": V[n]["recent"], "value_used": V[n]["base"],
         "playoff_expected": r1(po_pts(n)), "ros_ppg": r1(ros_pg(n))}
    for w, k in ((PO1, "wk15"), (PO2, "wk16"), (PO3, "wk17")):
        o[k + "_opp"] = opp(p, w) or "BYE"; o[k + "_rank"] = opp_rank(p, w); o[k + "_proj"] = r1(V[n]["v"][w]); o[k + "_chance"] = V[n]["q"][w]
    my_players.append(o)
my_players.sort(key=lambda x: -x["playoff_expected"])
ideal = {}
for w in (PO1, PO2, PO3):
    tot_, lu_ = lineup(ROSTER[ME], w, on=lambda n: V[n]["q"][w] > 0, detail=True)
    for x in lu_:
        x["chance"] = V[x["player"]]["q"][w]; x["opp"] = opp(P[x["player"]], w) or "BYE"; x["rank"] = opp_rank(P[x["player"]], w)
    bench_ = sorted([n for n in ROSTER[ME] if n not in {x["player"] for x in lu_} and V[n]["q"][w] > 0], key=lambda n: -V[n]["v"][w])[:4]
    ideal[w] = {"total": r1(tot_), "lineup": lu_, "next_up": [{"player": n, "pts": r1(V[n]["v"][w]), "pos": P[n]["pos"]} for n in bench_]}
healthy = {w: r1(lineup(ROSTER[ME], w, on=lambda n: V[n]["v"][w] > 0 and P[n].get("inj") not in ("OUT", "INJURY_RESERVE") or n in AVAIL)) for w in (PO1, PO2, PO3)}
tot, lu = lineup(ROSTER[ME], CUR, detail=True)
starters = {x["player"] for x in lu}
this_week = {"week": CUR, "total": r1(tot), "lineup": lu,
             "bench": [{"player": n, "pos": P[n]["pos"], "pts": r1(ev(n, CUR))} for n in sorted(ROSTER[ME], key=lambda n: -ev(n, CUR)) if n not in starters]}
my_game = next(([h, a] for wk, h, a in REM if wk == CUR and ME in (h, a)), None)
if my_game:
    o = my_game[0] if my_game[1] == ME else my_game[1]
    this_week["opponent"] = TNAME[o]; this_week["win_pct"] = r1(mine["wk"]); this_week["opp_proj"] = r1(base[o]["means"][CUR])


# ---------------- week by week: my best lineup every week, and who I'm playing ----------------
from math import erf, sqrt
def lu_rows(rost, w):
    tot_, lu_ = lineup(rost, w, detail=True)
    for x in lu_:
        n = x["player"]; x["pos"] = P[n]["pos"]; x["nfl"] = P[n]["nfl"]; x["opp"] = opp(P[n], w) or "BYE"; x["rank"] = opp_rank(P[n], w); x["chance"] = round(V[n]["q"][w], 2) if n in V else 1
    return tot_, lu_
schedule = []
for w in WKS:
    tot_, lu_ = lu_rows(ROSTER[ME], w)
    used = {x["player"] for x in lu_}
    bench_ = [{"player": n, "pos": P[n]["pos"], "pts": r1(ev(n, w)), "opp": opp(P[n], w) or "BYE", "nfl": P[n]["nfl"]} for n in sorted(ROSTER[ME], key=lambda n: -ev(n, w)) if n not in used][:7]
    byes = [n for n in ROSTER[ME] if not opp(P[n], w)]
    hurt = [n for n in ROSTER[ME] if n in V and V[n]["q"][w] < 0.6 and opp(P[n], w)]
    row = {"w": w, "kind": "reg" if w <= REG else "po", "label": f"Week {w}" + ("" if w <= REG else " · " + ("Round 1" if w == PO1 else "Semis" if w == PO2 else "Final") if SET.get("playoff_teams") != 4 else ""),
           "total": r1(tot_), "lineup": lu_, "bench": bench_, "byes": byes, "hurt": hurt}
    g = next(((h, a) for wk, h, a in REM if wk == w and ME in (h, a)), None)
    if g:
        o = g[0] if g[1] == ME else g[1]
        ot, olu = lu_rows(ROSTER[o], w)
        row.update({"opponent": TNAME[o], "opp_id": o, "opp_total": r1(ot), "opp_lineup": olu,
                    "win_pct": r1(mine["wk"]) if w == CUR else r1(50 * (1 + erf((tot_ - ot) / (SD * sqrt(2) * sqrt(2)))))})
    if w == PO1 and SET.get("playoff_teams") != 4:
        row["note"] = f"You have a {mine['bye']:.0f}% chance at a bye this week."
    schedule.append(row)


# ---------------- start/sit toss-ups: close calls in this week's lineup ----------------
def Phi(x):
    return 0.5 * (1 + erf(x / sqrt(2)))
def psd(n, mu):
    sd_ = (PROF.get(n) or {}).get("sd")
    return max(sd_ or 0, 0.45 * mu, 3.0)
tossups = []
sw0 = schedule[0] if schedule else None
if sw0:
    st_ = sw0["lineup"]; used_ = {x["player"] for x in st_}
    benchp = [n for n in ROSTER[ME] if n not in used_ and ev(n, CUR) > 0]
    for x in st_:
        sn = x["player"]; ms = ev(sn, CUR)
        alts = [n for n in benchp if P[n]["pos"] in ELIG.get(x["slot"], ()) and ev(n, CUR) >= ms - 2.5]
        if not alts:
            continue
        a = max(alts, key=lambda n: ev(n, CUR)); ma = ev(a, CUR)
        pw = Phi((ms - ma) / sqrt(psd(sn, ms) ** 2 + psd(a, ma) ** 2))
        pa, pb = PROF.get(sn) or {}, PROF.get(a) or {}
        notes = []
        if V[sn]["q"][CUR] < 1: notes.append(f"{sn} is {round(100 * V[sn]['q'][CUR])}% to play, so if he's ruled out, {a} is your guy.")
        if (pb.get("ceiling") or 0) - (pa.get("ceiling") or 0) >= 3: notes.append(f"{a} has the higher ceiling ({pb['ceiling']} vs {pa.get('ceiling')}). Go with him if you need a big week.")
        if (pa.get("floor") or 0) - (pb.get("floor") or 0) >= 2: notes.append(f"{sn} is safer (floor {pa['floor']} vs {pb.get('floor')}).")
        ra, rb = opp_rank(P[sn], CUR), opp_rank(P[a], CUR)
        if ra and rb and abs(ra - rb) >= 10: notes.append(f"Matchups: {sn} vs #{ra}, {a} vs #{rb} (32 = easiest).")
        call = f"Start {sn}" if pw >= 0.55 else "Coin flip" if pw >= 0.45 else f"Start {a}"
        tossups.append({"slot": nice_slot(x["slot"]), "start": sn, "start_pts": r1(ms), "alt": a, "alt_pts": r1(ma), "p_start_better": r1(100 * pw), "call": call, "notes": notes,
                        "start_opp": opp(P[sn], CUR) or "BYE", "alt_opp": opp(P[a], CUR) or "BYE"})
    tossups.sort(key=lambda t: abs(t["p_start_better"] - 50))

# ---------------- claim or skip on each waiver idea ----------------
BRK = {b["player"]: b for b in breakouts}
WEQ = max(1, len(REGW) + WGT["playoff_week_weight"] * len(PW))   # my_score counts playoff weeks double
for x in waivers:
    b = BRK.get(x["player"]); cp = x.get("change_pts")
    if (cp is not None and cp >= 0.5) or x["ros_gain"] >= 3:
        x["verdict"], x["why"] = "CLAIM", f"Real upgrade: about {x['ros_gain'] / WEQ:+.1f} pts a week in your lineup the rest of the way" + (f", title odds {cp:+.1f}%." if cp is not None else ".")
    elif x["this_week_gain"] >= 3:
        x["verdict"], x["why"] = "CLAIM", f"Fills a hole this week ({x['this_week_gain']:+.1f} pts). You can drop him again after."
    elif b and b["score"] >= 40 and x["drop"] not in {y["player"] for y in (sw0["lineup"] if sw0 else [])} and x["drop"] not in {t["alt"] for t in tossups} | {t["start"] for t in tossups} and x["ros_gain"] >= -1:
        x["verdict"], x["why"] = "CLAIM", f"Breakout radar gives him {b['score']}% to become a weekly starter. Cheap lottery ticket for {x['drop']}."
    else:
        x["verdict"], x["why"] = "SKIP", f"Barely moves the needle ({x['this_week_gain']:+.1f} this week, {x['ros_gain']:+.1f} rest of season)."
waivers.sort(key=lambda x: (x["verdict"] != "CLAIM", -(x["ros_gain"] + 2 * x["this_week_gain"])))
seen_drop = {}
for x in waivers:   # one claim per guy you'd drop: the best one wins, the rest are backups
    if x["verdict"] == "CLAIM":
        if x["drop"] in seen_drop:
            x["verdict"], x["why"] = "SKIP", f"Backup plan if {seen_drop[x['drop']]} is gone. " + x["why"]
        else:
            seen_drop[x["drop"]] = x["player"]

# ---------------- this week's to-do list ----------------
todo = []
if sw0:
    for x in sw0["lineup"]:
        if x["opp"] != "BYE" and x["chance"] >= 0.8:
            continue
        pos_ = P[x["player"]]["pos"]
        fa_ = max([w for w in waivers if w["pos"] == pos_ and w["this_week_gain"] > 0], key=lambda w: w["this_week_gain"], default=None)
        alt_ = next((b for b in sw0["bench"] if b["opp"] != "BYE" and b["pos"] in ELIG.get(x["slot"], ()) and b["pts"] > 0), None)
        what = "is on bye" if x["opp"] == "BYE" else f"might not play ({round(100 * x['chance'])}%)"
        fix = f"Add {fa_['player']} and drop {fa_['drop']} ({fa_['this_week_gain']:+.1f} pts)." if fa_ else f"Start {alt_['player']} ({alt_['pts']}) instead." if alt_ else "Check his status before kickoff."
        todo.append({"urgent": x["opp"] == "BYE" or x["chance"] < 0.5, "icon": "🚨" if x["opp"] == "BYE" else "🩹", "title": f"{x['player']} {what}", "detail": fix, "tab": "home"})
for w in [w for w in waivers if w["verdict"] == "CLAIM"][:2]:
    if any(w["player"] in t["detail"] or w["drop"] in t["detail"] for t in todo):
        continue
    todo.append({"urgent": False, "icon": "📥", "title": f"Claim {w['player']}, drop {w['drop']}", "detail": w["why"], "tab": "waivers"})
for t in tossups[:1]:
    if 45 <= t["p_start_better"] <= 55:
        todo.append({"urgent": False, "icon": "🤔", "title": f"Toss-up at {t['slot']}: {t['start']} or {t['alt']}", "detail": f"{t['start']} {t['start_pts']} vs {t['alt']} {t['alt_pts']}. " + " ".join(t["notes"][:1]), "tab": "home"})
rt = next((x for x in trades if x["kind"] == "realistic" and x["change_pts"] >= 1.5), None)
if rt:
    todo.append({"urgent": False, "icon": "🤝", "title": f"Offer {rt['give']} for {rt['get']} ({rt['partner']})", "detail": f"Title odds {rt['change_pts']:+.1f}%. {rt['pitch']}", "tab": "trades"})
if not todo:
    todo.append({"urgent": False, "icon": "✅", "title": "You're all set this week", "detail": "Lineup is full, no must-have waiver claims, no trade worth forcing.", "tab": "home"})

# ---------------- change log vs last pull ----------------
changes = []
if PREV_P.exists():
    prev = json.loads(PREV_P.read_text())
    ptn = {t["id"]: t["name"] for t in prev["teams"]}
    old = {p["n"]: p["t"] for p in prev["players"]}
    for n, p in P.items():
        a, b = old.get(n, 0), p["t"]
        if a == b:
            continue
        if a == 0 and b:
            changes.append({"type": "add", "text": f"{TNAME[b]} added {n}"})
        elif a and b == 0:
            changes.append({"type": "drop", "text": f"{ptn.get(a, a)} dropped {n}"})
        elif a and b:
            changes.append({"type": "trade", "text": f"{n}: {ptn.get(a, a)} → {TNAME[b]}"})
    for n, p in P.items():
        q = next((x for x in prev["players"] if x["n"] == n), None)
        if q and p["t"] and q.get("inj") != p.get("inj") and p["pos"] in SKILL:
            changes.append({"type": "injury", "text": f"{n} ({TNAME.get(p['t'], 'FA')}): {q.get('inj')} → {p.get('inj')}"})
hist_p = OUT / "history.json"
hist = json.loads(hist_p.read_text()) if hist_p.exists() else {"runs": [], "moves": []}
stamp = M["at"]
if changes:
    hist["moves"] = ([{"at": stamp, **c} for c in changes] + hist["moves"])[:300]
if not hist["runs"] or hist["runs"][-1]["at"] != stamp:
    hist["runs"].append({"at": stamp, "week": CUR, "champ_pct": r1(mine["ch"]), "final_pct": r1(mine["fi"]), "playoffs_pct": r1(mine["po"]),
                         "bye_pct": r1(mine["bye"]), "seed1_pct": r1(mine["s1"])})
    hist["runs"] = hist["runs"][-400:]
prev_run = hist["runs"][-2] if len(hist["runs"]) > 1 else None

# ---------------- my take (plain-English, written from the numbers) ----------------
take = []
ch = mine["ch"]
lead = max((x for x in league if not x["me"]), key=lambda x: x["champ_pct"])
if mine["bye"] >= 85:
    take.append(f"The bye is close to locked ({r1(mine['bye'])}%), so your season is really two games: Week {PO2} and Week {PO3}. Every move below is judged mostly on those two lineups, with Weeks {CUR + 1} to {REG} still counted so nobody who stinks until then sneaks in.")
elif mine["po"] >= 90:
    take.append(f"You're in ({r1(mine['po'])}%) but the bye isn't safe yet ({r1(mine['bye'])}%). Regular season wins still matter a lot.")
else:
    take.append(f"Playoffs aren't safe ({r1(mine['po'])}%). Win now beats playoff matchups right now.")
take.append(f"You win it all in {r1(ch)}% of sims" + (f" ({'up' if ch > prev_run['champ_pct'] else 'down'} from {prev_run['champ_pct']}% last update)" if prev_run and abs(ch - prev_run["champ_pct"]) >= 1 else "") + f". Your biggest threat is {lead['team']} at {lead['champ_pct']}%.")
okr = [x for x in trades if x["kind"] == "realistic" and not x["blocked"] and x["change_pts"] > 0]
if okr:
    b = okr[0]
    take.append(f"Best trade they should actually accept: {b['give']} for {b['get']} with {b['partner']}. Title odds +{b['change_pts']} and their lineup moves {b['partner_weekly_gain']:+} a week.")
    if len(okr) > 1:
        b2 = next((x for x in okr[1:] if not set(x["give"].split(" + ")) & set(b["give"].split(" + "))), okr[1])
        take.append(f"Backup plan: {b2['give']} for {b2['get']} ({b2['partner']}), +{b2['change_pts']}.")
st = [x for x in trades if x["kind"] == "stretch" and not x["blocked"]]
if st:
    take.append(f"Swing big if they bite: {st[0]['give']} for {st[0]['get']} ({st[0]['partner']}) is worth +{st[0]['change_pts']}, but it hurts their lineup so expect a no unless they're desperate.")
if swing:
    take.append(f"Biggest risk: {swing[0]['what'].replace(' out for the playoffs', '')} getting hurt costs you {abs(swing[0]['change_pts'])} points of title odds. Make sure you have a real backup there.")
wtop = next((x for x in waivers if x["ros_gain"] >= 3), None)
if wtop:
    take.append(f"Waivers: {wtop['player']} ({wtop['pos']}) for {wtop['drop']} adds about {wtop['ros_gain']} weighted lineup points.")

summary = {"at": M["at"], "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "league": M.get("league_name") or LCFG.get("name") or LID, "league_id": LID, "week": CUR, "reg_end": REG,
           "playoff_weeks": [PO1, PO2, PO3], "weighted_weeks": PW, "me": ME, "my_team": TNAME[ME],
           "kpis": {k: r1(mine[k]) for k in ("ch", "fi", "po", "bye", "s1")}, "healthy_lineup": healthy, "ideal_lineups": ideal, "tiers": TIERS,
           "expected_lineup": {w: r1(mine["means"][w]) for w in (PO1, PO2, PO3)}, "take": take, "this_week": this_week, "swing": swing,
           "sims": A["sims"], "assumptions": {"avail": AVAIL, "override": OVR},
           "settings": {k: v for k, v in SET.items() if k != "scoring"}, "scoring": SC, "overrides": OVR_SET}
tester = {"weeks": WKS, "cur": CUR, "reg": REG, "po": [PO1, PO2, PO3], "np": 4 if SET.get("playoff_teams") == 4 else 6, "me": ME, "sd": SD,
          "slots": SLOTS, "elig": ELIG,
          "teams": [{"id": t, "name": TNAME[t], "wins": TEAMS[t]["w"] + 0.5 * TEAMS[t].get("t", 0), "pf": TEAMS[t]["pf"]} for t in IDS],
          "sched": REM, "players": [{"n": n, "pos": p["pos"], "t": p["t"], "nfl": p["nfl"], "e": [r1(ev(n, w)) for w in WKS], "v": [r1(V[n]["v"][w]) for w in WKS], "q": [round(V[n]["q"][w], 2) for w in WKS]}
                                    for n, p in P.items() if ros(n) + ev(n, CUR) > 3 or p["t"] == ME]}
latest = {"summary": summary, "teams": team_cards, "league": league, "trades": trades, "waivers": waivers, "rankings": ranks, "bubble": bubble, "schedule": schedule, "breakouts": breakouts, "tossups": tossups, "todo": todo,
          "my_players": my_players, "tester": tester}
PO_STARTERS = {x["player"] for w in (PO2, PO3) for x in lineup(ROSTER[ME], w, detail=True)[1]}


def verdict(n, p, pr):
    """Plain-English call + the 3-6 reasons that matter most."""
    R = []
    tr, lk = pr.get("trend"), pr.get("fpoe")
    if tr and tr >= 1.12: R.append(("📈", f"Usage is up {round((tr - 1) * 100)}% over his last 3 games ({pr.get('opp_g3')} touches+targets a game)."))
    if tr and tr <= 0.88: R.append(("📉", f"Usage is down {round((1 - tr) * 100)}% over his last 3 games."))
    if lk is not None and lk >= 2.5: R.append(("🍀", f"Running hot: scoring {lk} more a game than his usage says he should. Expect a little cool-down."))
    if lk is not None and lk <= -2.5: R.append(("🔄", f"Unlucky so far: scoring {abs(lk)} less a game than his usage says. Due to bounce back."))
    po = [(s_["opp"], s_["rank"]) for s_ in pr.get("schedule", []) if s_["w"] in (PO2, PO3)]
    if pr.get("sos_po_label") == "Easy": R.append(("🟢", "Soft playoff matchups: " + " and ".join(o for o, _ in po) + "."))
    if pr.get("sos_po_label") == "Tough": R.append(("🔴", "Tough playoff matchups: " + " and ".join(o for o, _ in po) + "."))
    if pr.get("sos_ros_label") == "Easy" and pr.get("sos_po_label") != "Easy": R.append(("🟢", "Easy schedule the rest of the regular season."))
    qs = [V[n]["q"][w] for w in (PO2, PO3) if opp(p, w)]
    if qs and min(qs) < 0.95: R.append(("🚑", f"Only about {round(100 * sum(qs) / len(qs))}% likely to play in the playoffs."))
    if any(not opp(p, w) for w in (PO2, PO3)): R.append(("💤", "Has a bye during the fantasy playoffs."))
    dep = pr.get("depth") or {}
    if n in FILLIN:
        fi = FILLIN[n]
        R.append(("🔁", f"Most of his points came in Weeks {', '.join(map(str, fi['out_weeks']))} while {fi['starter']} was out. {fi['starter']} is back, so he's valued as a backup now" + (f" ({fi['with_pg']:.1f} pts a game when they both played)." if fi["with_pg"] is not None else " (he's barely played with him back).")))
    if dep.get("rank", 1) > 1 and p["pos"] != "QB" and p.get("inj") not in ("OUT", "INJURY_RESERVE"): R.append(("🪜", f"Listed {dep['pos']}{dep['rank']} on the depth chart."))
    rz = (pr.get("rz_tgt") or 0) + (pr.get("rz_car") or 0)
    if pr.get("games") and rz / pr["games"] >= 2.2: R.append(("🎯", f"Big red zone role: {rz} red zone chances in {pr['games']} games."))
    if (pr.get("snap3") or 0) >= 85 and p["pos"] != "QB": R.append(("🔒", f"Every-down guy: on the field for {pr['snap3']}% of snaps lately."))
    if pr.get("boom", 0) >= 40: R.append(("💥", f"Boom machine: 20+ points in {pr['boom']}% of his games."))
    if pr.get("bust", 0) >= 40: R.append(("🎲", f"Risky: under 8 points in {pr['bust']}% of his games."))
    if (p.get("own_chg") or 0) >= 5: R.append(("🔥", f"Getting picked up fast ({p['own_chg']:+}% rostered this week)."))
    v, mv = pr.get("value") or 0, pr.get("my_value") or 0
    mine_, fa_ = p["t"] == ME, p["t"] == 0
    if mine_:
        starts = n in PO_STARTERS
        if starts and n in CORE: call, line = "KEEP", "Core piece of your playoff lineup. Not going anywhere."
        elif starts: call, line = "KEEP", "Starts for you in the playoffs. Only move him for a clear upgrade at his spot."
        elif v >= 25: call, line = "SELL", "Not in your best playoff lineup, but the league still values him. Use him as a trade chip."
        elif ros_pg(n) < 6 and not AVAIL.get(n): call, line = "DROP", "Not helping you. His roster spot could be a waiver add."
        else: call, line = "HOLD", "Bench depth. Fine to keep as injury insurance."
    elif fa_:
        call, line = ("ADD", "Worth a waiver claim for your roster.") if mv >= 15 or ros_pg(n) >= 9 else ("PASS", "Not worth a roster spot right now.")
    else:
        if mv >= 45 and v <= mv + 5: call, line = "BUY", "Would help your playoff lineup and the price shouldn't be crazy."
        elif mv >= 45: call, line = "PRICEY", "Would help you, but his team will value him high."
        else: call, line = "PASS", "Wouldn't move the needle much for your title odds."
    return call, line, [{"icon": a, "text": b} for a, b in R[:6]]


cards = {}
for n, p in P.items():
    if p["pos"] not in SKILL or not (p["t"] or ros(n) > 20):
        continue
    pr = dict(PROF.get(n, {}))
    pr.update(name=n, pos=p["pos"], owner=TNAME.get(p["t"], "Free agent"), mine=p["t"] == ME, avg=p["avg"], recent=V[n]["recent"], value_used=V[n]["base"],
              ros_ppg=round(ros_pg(n), 1), playoff_pts=round(po_pts(n), 1), reg_pts=round(reg_pts(n), 1),
              weekly=[{"w": w, "ev": round(ev(n, w), 1), "if_active": round(V[n]["v"][w], 1), "chance": V[n]["q"][w]} for w in WKS],
              espn_pts={k: v for k, v in sorted(p["wk"].items(), key=lambda kv: int(kv[0]))})
    pr["verdict"], pr["verdict_line"], pr["reasons"] = verdict(n, p, pr)
    cards[n] = pr
(OUT / "players.json").write_text(json.dumps(cards, separators=(",", ":")))
(OUT / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
hist_p.write_text(json.dumps(hist, separators=(",", ":")))
print(f"done in {time.time() - t0:.1f}s: title {r1(ch)}%, {len(trades)} trades, {len(waivers)} waivers, {len(ranks)} ranked, {len(changes)} changes")
