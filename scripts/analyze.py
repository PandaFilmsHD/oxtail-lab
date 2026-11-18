"""Turn data/model.json into everything the site shows (docs/data/latest.json + docs/data/history.json).

What it does:
  1. Values every player week by week (season avg + recent form, opponent matchup, injury odds).
  2. Sims the rest of the regular season and the playoff bracket (vectorized Monte Carlo).
  3. Searches 1-for-1, 2-for-1 and 1-for-2 trades with every team and sims the best ones.
  4. Ranks waiver adds, builds ROS + playoff rankings, finds bubble teams, writes a plain-English take.
"""
import json, math, time, pathlib, itertools
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
A = json.loads((ROOT / "assumptions.json").read_text())
M = json.loads((ROOT / "data" / "model.json").read_text())
PREV_P = ROOT / "data" / "model_prev.json"
OUT = ROOT / "docs" / "data"
OUT.mkdir(parents=True, exist_ok=True)

ME = CFG["my_team_id"]
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
OVR_SET = {k: v for k, v in (CFG.get("league_overrides") or {}).items() if not k.startswith("_")}
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
rng = np.random.default_rng()
AVAIL = {k: v for k, v in A["avail"].items() if not k.startswith("_")}
OVR = {k: v for k, v in A["override"].items() if not k.startswith("_")}
CORE = set(A.get("core", []))
NO_TRADE = set(A.get("no_trade_teams", []))

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


PROF = {}
for n, p in P.items():
    if p["pos"] not in SKILL:
        continue
    pts = [p["wk"][k] for k in sorted(p["wk"], key=int) if int(k) < CUR]
    played = [x for x in pts if x != 0]
    e = USAGE.get(norm(n), {})
    u = [x for x in e.get("weeks", []) if x["w"] < CUR]
    pr = {"nfl": p["nfl"], "own": p["own"], "own_chg": p.get("own_chg"), "start_pct": p.get("start_pct"), "outlook": p.get("outlook") or None,
          "status": p.get("inj"), "ngs": e.get("ngs"), "pfr": e.get("pfr"), "report": e.get("injury"), "depth": e.get("depth"),
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
    gl = games_left(p)
    if p.get("espn_ros") and gl:
        pr["espn_ros_pg"] = round(p["espn_ros"] / gl, 1)
    pr["sos_ros"], pr["sos_ros_label"] = sched_grade(p, [w for w in WKS if CUR < w <= REG])
    pr["sos_po"], pr["sos_po_label"] = sched_grade(p, [PO2, PO3])
    pr["schedule"] = [{"w": w, "opp": opp(p, w) or "BYE", "rank": opp_rank(p, w)} for w in WKS]
    for x in u:
        x["pts"] = round(league_pts(x), 1)
    pr["log"] = [{k: x.get(k) for k in ("w", "opp", "pts", "xfp", "tgt", "rec", "recyd", "car", "rushyd", "td", "tsh", "csh", "snap", "att", "passyd", "rz_tgt", "rz_car")} for x in u]
    PROF[n] = pr

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
        f = 1 + (rk - 16.5) / 16.5 * WGT["matchup_swing"]
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
MV, MYV = {}, {}
for n, p in P.items():
    if p["pos"] not in SKILL:
        continue
    ra, rp = REPL[p["pos"]]
    trend = (PROF.get(n) or {}).get("trend") or 1
    MV[n] = max(0.0, ppg_ros(n) - ra) * max(0.9, min(1.1, trend))          # what the league should value him at
    MYV[n] = max(0.0, 0.4 * (sum(ev(n, w) for w in REGW) / max(1, len(REGW)) - ra) + 0.6 * (ppg_po(n) - rp))  # what he's worth to a team on a bye
_mx, _my = max(MV.values() or [1]) or 1, max(MYV.values() or [1]) or 1
for n in MV:
    PROF.setdefault(n, {})
    PROF[n]["value"] = round(100 * MV[n] / _mx)
    PROF[n]["my_value"] = round(100 * MYV[n] / _my)
mval = lambda n: MV.get(n, 0) + 0.3  # tiny floor so bench guys aren't worth exactly zero

# ---------------- trade search ----------------
cands = []
mine_pool = [n for n in ROSTER[ME] if n not in CORE and P[n]["pos"] in SKILL and ros(n) > 6]
for t in IDS:
    if t == ME:
        continue
    theirs = sorted([n for n in ROSTER[t] if P[n]["pos"] in SKILL and ros(n) > 25], key=lambda n: -ros(n))[:9]
    gives = [[a] for a in mine_pool] + [list(c) for c in itertools.combinations(mine_pool, 2)]
    gets = [[a] for a in theirs] + [list(c) for c in itertools.combinations(theirs, 2)]
    base_t = team_score(ROSTER, t, REGW)
    for G in gives:
        for T_ in gets:
            if len(G) == 2 and len(T_) == 2:
                continue
            if (len(G) == 2 and min(PROF[x].get("value", 0) for x in G) < 1) or (len(T_) == 2 and min(PROF[x].get("value", 0) for x in T_) < 5):
                continue  # no pointless throw-ins
            r = clone(ROSTER)
            for n in G: r[ME].remove(n); r[t].append(n)
            for n in T_: r[t].remove(n); r[ME].append(n)
            gain = my_score(r) - MY_BASE
            if gain < 2:
                continue
            vin = sum(mval(n) for n in G) * (0.8 if len(G) > len(T_) else 1)   # 2-for-1: the side getting the best player wins ties
            vout = sum(mval(n) for n in T_) * (0.8 if len(T_) > len(G) else 1)
            ratio = vin / max(vout, 1)
            need = (team_score(r, t, REGW) - base_t) / max(1, len(REGW)) if REGW else 0
            real = ratio >= 1.0 if status[t] == "out" else (need >= -1 and ratio >= 0.9)
            lvl = 2 if ratio >= 1.05 else 1 if ratio >= 0.92 else 0 if ratio >= 0.82 else -1
            if status[t] != "out":
                lvl += (need >= 2) - (need <= -3)
            if not real and lvl < 1:
                continue
            cands.append({"t": t, "G": G, "T": T_, "gain": gain, "ratio": ratio, "need": need, "real": real, "r": r})
cands.sort(key=lambda c: -c["gain"])
seen, top, nr, ns = set(), [], 0, 0
quota = A["sims"]["trades_to_sim"]
for c in cands:
    k = ("+".join(c["T"]), c["real"])
    if k in seen or (c["real"] and nr >= quota * 2 // 3) or (not c["real"] and ns >= quota // 3):
        continue
    seen.add(k); top.append(c)
    if c["real"]: nr += 1
    else: ns += 1
trades = []
for c in top:
    res = run(c["r"], A["sims"]["per_trade"])[ME]
    trades.append({"partner": TNAME[c["t"]], "partner_id": c["t"], "partner_status": status[c["t"]], "give": " + ".join(c["G"]),
                   "get": " + ".join(c["T"]), "title_pct": r1(res["ch"]), "change_pts": r1(res["ch"] - mine["ch"]), "final_pct": r1(res["fi"]),
                   "value_ratio": round(c["ratio"], 2), "partner_weekly_gain": r1(c["need"]), "kind": "realistic" if c["real"] else "stretch",
                   "blocked": c["t"] in NO_TRADE, "wk16": r1(res["means"][PO2]), "wk17": r1(res["means"][PO3])})
trades.sort(key=lambda x: -x["change_pts"])
print(f"trades: {len(cands)} candidates, {len(trades)} simmed, {time.time() - t0:.1f}s")

# ---------------- waivers ----------------
fa = [n for n, p in P.items() if p["t"] == 0 and p["pos"] in SKILL + ("D", "K")]
bench = sorted([n for n in ROSTER[ME] if n not in CORE], key=lambda n: ros(n) + po_pts(n))
waivers = []
for n in fa:
    pos = P[n]["pos"]
    if pos in ("D", "K"):
        same = sorted([x for x in ROSTER[ME] if P[x]["pos"] == pos], key=ros)
        drop = same[0] if len(same) > 1 else bench[0]
    else:
        drop = next((x for x in bench if P[x]["pos"] == pos and ros(x) < ros(n)), bench[0])
    r = clone(ROSTER); r[ME].remove(drop); r[ME].append(n)
    waivers.append({"player": n, "pos": pos, "nfl": P[n]["nfl"], "drop": drop, "ros_gain": r1(my_score(r) - MY_BASE),
                    "this_week_gain": r1(lineup(r[ME], CUR) - lineup(ROSTER[ME], CUR)), "ros_ppg": r1(ros(n) / max(1, len(WKS) - 1)),
                    "playoff_pts": r1(po_pts(n)), "opp16": opp(P[n], PO2) or "BYE", "opp17": opp(P[n], PO3) or "BYE", "own_pct": P[n]["own"], "_r": r})
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
    ranks.append({"player": n, "pos": p["pos"], "nfl": p["nfl"], "owner": "You" if p["t"] == ME else o, "ros_ppg": r1(ros(n) / nw),
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

# ---------------- league, bubble, my team ----------------
league = []
for t in IDS:
    b = base[t]
    league.append({"id": t, "team": TNAME[t], "me": t == ME, "record": f'{TEAMS[t]["w"]}-{TEAMS[t]["l"]}', "pf": TEAMS[t]["pf"], "status": status[t],
                   "playoffs_pct": r1(b["po"]), "bye_pct": r1(b["bye"]), "seed1_pct": r1(b["s1"]), "champ_pct": r1(b["ch"]), "final_pct": r1(b["fi"]),
                   "win_this_week_pct": r1(b["wk"]), "wk16_lineup": r1(b["means"][PO2]), "wk17_lineup": r1(b["means"][PO3])})
league.sort(key=lambda x: (-x["champ_pct"], -x["playoffs_pct"]))
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
         "playoff_expected": r1(po_pts(n)), "ros_ppg": r1(ros(n) / nw)}
    for w, k in ((PO1, "wk15"), (PO2, "wk16"), (PO3, "wk17")):
        o[k + "_opp"] = opp(p, w) or "BYE"; o[k + "_rank"] = opp_rank(p, w); o[k + "_proj"] = r1(V[n]["v"][w]); o[k + "_chance"] = V[n]["q"][w]
    my_players.append(o)
my_players.sort(key=lambda x: -x["playoff_expected"])
tot, lu = lineup(ROSTER[ME], CUR, detail=True)
starters = {x["player"] for x in lu}
this_week = {"week": CUR, "total": r1(tot), "lineup": lu,
             "bench": [{"player": n, "pos": P[n]["pos"], "pts": r1(ev(n, CUR))} for n in sorted(ROSTER[ME], key=lambda n: -ev(n, CUR)) if n not in starters]}
my_game = next(([h, a] for wk, h, a in REM if wk == CUR and ME in (h, a)), None)
if my_game:
    o = my_game[0] if my_game[1] == ME else my_game[1]
    this_week["opponent"] = TNAME[o]; this_week["win_pct"] = r1(mine["wk"]); this_week["opp_proj"] = r1(base[o]["means"][CUR])

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
take.append(f"You win it all in {r1(ch)}% of sims. Next best is {lead['team']} at {lead['champ_pct']}%." + (f" That's {'up' if ch > prev_run['champ_pct'] else 'down'} from {prev_run['champ_pct']}% last update." if prev_run and abs(ch - prev_run["champ_pct"]) >= 1 else ""))
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

summary = {"at": M["at"], "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "league": CFG["league_name"], "week": CUR, "reg_end": REG,
           "playoff_weeks": [PO1, PO2, PO3], "weighted_weeks": PW, "me": ME, "my_team": TNAME[ME],
           "kpis": {k: r1(mine[k]) for k in ("ch", "fi", "po", "bye", "s1")}, "take": take, "this_week": this_week, "swing": swing,
           "sims": A["sims"], "assumptions": {"avail": AVAIL, "override": OVR},
           "settings": {k: v for k, v in SET.items() if k != "scoring"}, "scoring": SC, "overrides": OVR_SET}
tester = {"weeks": WKS, "cur": CUR, "reg": REG, "po": [PO1, PO2, PO3], "np": 4 if SET.get("playoff_teams") == 4 else 6, "me": ME, "sd": SD,
          "slots": SLOTS, "elig": ELIG,
          "teams": [{"id": t, "name": TNAME[t], "wins": TEAMS[t]["w"] + 0.5 * TEAMS[t].get("t", 0), "pf": TEAMS[t]["pf"]} for t in IDS],
          "sched": REM, "players": [{"n": n, "pos": p["pos"], "t": p["t"], "nfl": p["nfl"], "e": [r1(ev(n, w)) for w in WKS]}
                                    for n, p in P.items() if ros(n) + ev(n, CUR) > 3 or p["t"] == ME]}
latest = {"summary": summary, "league": league, "trades": trades, "waivers": waivers, "rankings": ranks, "bubble": bubble,
          "my_players": my_players, "tester": tester}
cards = {}
for n, p in P.items():
    if p["pos"] not in SKILL or not (p["t"] or ros(n) > 20):
        continue
    pr = dict(PROF.get(n, {}))
    pr.update(name=n, pos=p["pos"], owner=TNAME.get(p["t"], "Free agent"), mine=p["t"] == ME, avg=p["avg"], recent=V[n]["recent"], value_used=V[n]["base"],
              ros_ppg=round(ros(n) / nw, 1), playoff_pts=round(po_pts(n), 1), reg_pts=round(reg_pts(n), 1),
              weekly=[{"w": w, "ev": round(ev(n, w), 1), "if_active": round(V[n]["v"][w], 1), "chance": V[n]["q"][w]} for w in WKS],
              espn_pts={k: v for k, v in sorted(p["wk"].items(), key=lambda kv: int(kv[0]))})
    cards[n] = pr
(OUT / "players.json").write_text(json.dumps(cards, separators=(",", ":")))
(OUT / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
hist_p.write_text(json.dumps(hist, separators=(",", ":")))
print(f"done in {time.time() - t0:.1f}s: title {r1(ch)}%, {len(trades)} trades, {len(waivers)} waivers, {len(ranks)} ranked, {len(changes)} changes")
