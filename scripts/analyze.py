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
FOUR = SET.get("playoff_teams") == 4
R1, SEMI, FINAL = (None, PO1, PO2) if FOUR else (PO1, PO2, PO3)   # 4-team playoffs: semis then final, no bye round
PLW = [w for w in (R1, SEMI, FINAL) if w]
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
    pr["sos_po"], pr["sos_po_label"] = sched_grade(p, [SEMI, FINAL])
    pr["schedule"] = [{"w": w, "opp": opp(p, w) or "BYE", "rank": opp_rank(p, w)} for w in WKS]
    for x in u:
        x["pts"] = round(league_pts(x), 1)
    pr["log"] = [{k: x.get(k) for k in ("w", "opp", "pts", "xfp", "tgt", "rec", "recyd", "car", "rushyd", "td", "tsh", "csh", "snap", "att", "passyd", "rz_tgt", "rz_car")} for x in u]
    PROF[n] = pr


# ---------------- automatic availability: who plays which week, from ESPN status + practice reports + ESPN news ----------------
import re as _re
SEASON_END = _re.compile(r"(remainder of the (?:20\d\d )?season|season-ending|out for the (?:rest of the )?season|torn (?:his )?acl|tearing his acl|ruptured (?:his )?achilles|torn achilles)", _re.I)
RET_WK = _re.compile(r"(?:return|back|activated|debut)\D{0,40}?week (\d{1,2})|week (\d{1,2})\D{0,25}?(?:return|comeback)", _re.I)
LONG = _re.compile(r"(several weeks|multiple weeks|extended (?:period|absence)|four to six weeks|4-6 weeks|six to eight weeks|long-term)", _re.I)
SHORT = _re.compile(r"(day-to-day|week-to-week|considered questionable|optimistic|expected to play|should be fine)", _re.I)
def auto_avail(n):
    p = P[n]; inj = p.get("inj") or "ACTIVE"
    if inj == "ACTIVE":
        return None
    txt = p.get("outlook") or ""
    rep = ((USAGE.get(norm(n)) or {}).get("injury") or {})
    prac = (rep.get("practice") or "") if (rep.get("week") or 0) >= CUR else ""   # only this week's practice reports count
    played = [int(w) for w, v in p["wk"].items() if v and int(w) < CUR]
    last = max(played) if played else CUR - 1
    q, why = {}, ""
    if SEASON_END.search(txt) and inj in ("OUT", "INJURY_RESERVE"):
        return {str(w): 0.0 for w in WKS}, "Out for the season (ESPN news)."
    m_ = RET_WK.search(txt); ret = None
    if m_:
        ret = int(m_.group(1) or m_.group(2))
        if ret < CUR:
            ret = None   # old news about a week that already passed
    if inj == "INJURY_RESERVE":
        start = max(CUR, last + 5)   # NFL IR: at least 4 games missed
        if ret: start = max(start, ret)
        if "Limited" in prac or "Full" in prac: start = max(CUR, start - 1)   # practicing = return window opened
        ramp = [0.4, 0.65, 0.8, 0.85]
        for w in WKS:
            q[str(w)] = 0.0 if w < start else ramp[min(w - start, 3)]
        why = f"On IR (last played Wk {last}). Earliest realistic return Wk {start}" + (" per ESPN news" if ret else "") + (", and he's back at practice" if ("Limited" in prac or "Full" in prac) else "") + "."
    elif inj in ("OUT", "SUSPENSION"):
        lg = LONG.search(txt)
        steps = [0.0, 0.2, 0.5, 0.75, 0.85] if lg else [0.0, 0.6, 0.8, 0.9]
        if ret: steps = [0.0] * max(0, ret - CUR) + [0.7, 0.85, 0.9]
        for i, w in enumerate(WKS):
            q[str(w)] = steps[min(i, len(steps) - 1)]
        why = "Ruled out this week" + (", news says it's a multi-week injury" if lg else "") + (f", ESPN says return around Wk {ret}" if ret else "") + "."
    elif inj == "DOUBTFUL":
        for i, w in enumerate(WKS):
            q[str(w)] = [0.25, 0.75, 0.92][min(i, 2)]
        why = "Doubtful this week."
    else:   # QUESTIONABLE
        c0 = 0.9 if "Full" in prac else 0.5 if "Did Not" in prac else 0.75
        if SHORT.search(txt): c0 = max(c0, 0.85)
        for i, w in enumerate(WKS):
            q[str(w)] = c0 if i == 0 else 0.95
        why = "Questionable this week" + (f" ({prac.replace(' in Practice', '').replace(' In Practice', '').lower()})" if prac else "") + "."
    return q, why
AUTO, AUTO_WHY = {}, {}
for n in P:
    r_ = auto_avail(n)
    if r_:
        AUTO[n], AUTO_WHY[n] = r_
MANUAL = {k: v for k, v in AVAIL.items() if isinstance(v, dict) and v.get("manual")}
AVAIL = {**AUTO, **MANUAL}   # your own "manual": true entries in assumptions.json still win
print(f"auto availability: {len(AUTO)} injured players, {len(MANUAL)} manual overrides; Puka: {AUTO.get('Puka Nacua')}")

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

# ---------------- role changes: judge a guy on games since his job changed, not the whole season ----------------
ROLE = {}
for n, p in P.items():
    if p["pos"] not in ("RB", "WR", "TE", "QB"):
        continue
    u = [x for x in (USAGE.get(norm(n)) or {}).get("weeks", []) if x["w"] < CUR and x.get("snap") is not None]
    if len(u) < 4:
        continue
    best = None
    for k in range(1, len(u) - 2):   # at least 3 games in the new role
        a, b = [x["snap"] for x in u[:k]], [x["snap"] for x in u[k:]]
        d = sum(b) / len(b) - sum(a) / len(a)
        if abs(d) >= 20 and (best is None or abs(d) > abs(best[1])):
            best = (k, d)
    if not best:
        continue
    k, d = best
    since = u[k]["w"]
    pts = [v for w_, v in p["wk"].items() if int(w_) >= since and int(w_) < CUR and v]
    if len(pts) < 3:
        continue
    ROLE[n] = {"since": since, "avg": round(sum(pts) / len(pts), 1), "games": len(pts), "dir": "up" if d > 0 else "down",
               "snap_before": round(sum(x["snap"] for x in u[:k]) / k), "snap_after": round(sum(x["snap"] for x in u[k:]) / (len(u) - k))}
AVG = {n: (ROLE[n]["avg"] if n in ROLE and n not in FILLIN else p["avg"]) for n, p in P.items()}   # "his average" = average in his current role
print(f"role changes: {len(ROLE)} (e.g. " + ", ".join(f"{k} since Wk{v['since']} {v['avg']} vs {P[k]['avg']}" for k, v in list(ROLE.items())[:5]) + ")")

# ---------------- backtest: which mix of season avg / last 4 / usage actually predicted past weeks best? ----------------
# Vegas: each team's implied points for a week (spread + over/under), keyed by ESPN team code
_RAWL = _U.get("lines") or {}
LINES = {int(w): {TEAMFIX.get(t, t): v for t, v in d.items()} for w, d in _RAWL.items()}
_its = [v["it"] for d in LINES.values() for v in d.values()]
IT_AVG = sum(_its) / len(_its) if _its else 22.5
def it_ratio(team, w):
    v = (LINES.get(w) or {}).get(team)
    return v["it"] / IT_AVG if v else None
VEGAS_B = {}
EFF_K = 8
POSI = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
def backtest():
    rows = []
    for n, p in P.items():
        if p["pos"] not in ("QB", "RB", "WR", "TE"):
            continue
        wk = {int(k): v for k, v in p["wk"].items() if v}
        e = USAGE.get(norm(n)) or {}
        xw = {x["w"]: league_xpts(x["xs"]) for x in e.get("weeks", []) if x.get("xs")}
        for W in sorted(wk):
            if W >= CUR:
                continue
            prior = [wk[k] for k in sorted(wk) if k < W]
            if len(prior) < 3:
                continue
            xp = [xw[k] for k in sorted(xw) if k < W]
            if len(xp) < 3:
                continue
            avg_, rec_ = sum(prior) / len(prior), sum(prior[-4:]) / len(prior[-4:])
            xf_ = 0.5 * sum(xp) / len(xp) + 0.5 * sum(xp[-4:]) / len(xp[-4:])
            act = [wk[k] for k in sorted(xw) if k < W and k in wk]
            g_ = len(act); ratio_ = sum(act) / max(1, sum(xw[k] for k in sorted(xw) if k < W and k in wk))
            xf_ *= max(0.75, min(1.35, 1 + (ratio_ - 1) * g_ / (g_ + EFF_K)))   # usage, adjusted for how efficient he's been (shrunk toward average)
            ux = next((x for x in e.get("weeks", []) if x["w"] == W), None)
            tm = None
            if ux:   # his team that week = whoever was playing his opponent
                o_ = TEAMFIX.get(ux.get("opp"), ux.get("opp"))
                tm = next((t for t, v in (LINES.get(W) or {}).items() if TEAMFIX.get(v["opp"], v["opp"]) == o_), None)
            itr = it_ratio(tm, W) if tm else None
            rows.append((avg_, rec_, xf_, wk[W], POSI[p["pos"]], itr if itr else 0.0, opp_factor(p, W) if opp(p, W) else 1.0))
    if len(rows) < 200:
        return None
    X = np.array(rows)
    best = None
    for a in np.arange(0, 1.01, 0.05):
        for b in np.arange(0, 1.01 - a, 0.05):
            c = 1 - a - b
            pred = a * X[:, 0] + b * X[:, 1] + c * X[:, 2]
            mae = float(np.abs(pred - X[:, 3]).mean())
            if best is None or mae < best[0]:
                best = (mae, a, b, c)
    mae_avg = float(np.abs(X[:, 0] - X[:, 3]).mean())
    OLD = np.array([0.375, 0.3125, 0.3125]); fit = np.array(best[1:])
    use = 0.5 * OLD + 0.5 * fit   # lean toward what the backtest says, but don't let one season of data swing everything
    mae_use = float(np.abs(X[:, :3] @ use - X[:, 3]).mean())
    # Vegas: does a high implied team total predict beating the projection, beyond the defense matchup?
    vg = {}
    pred = (X[:, :3] @ use) * X[:, 6]
    for pos, i in POSI.items():
        m_ = (X[:, 4] == i) & (X[:, 5] > 0) & (pred > 2)
        if m_.sum() < 40:
            continue
        lx, ly = np.log(X[m_, 5]), np.log(np.clip(X[m_, 3], 0.5, None) / pred[m_])
        b = float(np.cov(lx, ly)[0, 1] / max(1e-9, np.var(lx, ddof=1)))
        b = max(0.0, min(1.5, b * m_.sum() / (m_.sum() + 150)))   # shrink toward no effect when the sample is small
        before_ = float(np.abs(pred[m_] - X[m_, 3]).mean()); after_ = float(np.abs(pred[m_] * X[m_, 5] ** b - X[m_, 3]).mean())
        if after_ > before_:
            b = 0.0; after_ = before_
        VEGAS_B[pos] = round(b, 2)
        vg[pos] = {"n": int(m_.sum()), "elasticity": round(b, 2), "mae_without": round(before_, 2), "mae_with": round(after_, 2)}
    old = 0.375 * X[:, 0] + 0.3125 * X[:, 1] + 0.3125 * X[:, 2]
    return {"n": len(rows), "mae_season_avg": round(mae_avg, 2), "mae_old_mix": round(float(np.abs(old - X[:, 3]).mean()), 2),
            "mae_best": round(best[0], 2), "mae_used": round(mae_use, 2), "w_avg": round(float(use[0]), 2), "w_recent": round(float(use[1]), 2), "w_xfp": round(float(use[2]), 2),
            "fit_avg": round(float(best[1]), 2), "fit_recent": round(float(best[2]), 2), "fit_xfp": round(float(best[3]), 2), "vegas": vg}
BT = backtest()
BW = (0.3, 0.25, 0.25) if not BT else tuple(0.8 * BT[k] for k in ("w_avg", "w_recent", "w_xfp"))   # ESPN's rest-of-season number keeps the other 0.2
print("backtest:", BT)

V = {}
for n, p in P.items():
    r = recent(p)
    pr = PROF.get(n, {})
    xf = None
    if pr.get("xfp") is not None:
        xf = 0.5 * pr["xfp"] + 0.5 * pr.get("xfp4", pr["xfp"])
        g_ = pr.get("games") or 0
        if pr["xfp"] > 0 and pr.get("fpoe") is not None:
            ratio_ = (pr["xfp"] + pr["fpoe"]) / pr["xfp"]
            xf *= max(0.75, min(1.35, 1 + (ratio_ - 1) * g_ / (g_ + EFF_K)))
    parts = [(BW[0], AVG[n])] + ([(BW[1], r)] if r is not None else []) + ([(BW[2], xf)] if xf else []) + ([(0.2, pr["espn_ros_pg"])] if pr.get("espn_ros_pg") else [])
    parts = [x for x in parts if x[0] > 0] or [(1, p["avg"])]
    if p["pos"] in ("D", "K"):   # one huge D/ST game shouldn't drive the number: lean on the median game and ESPN's rest-of-season
        gs = sorted(v_ for v_ in p["wk"].values() if v_)
        med = gs[len(gs) // 2] if gs else p["avg"]
        gl_ = games_left(p, 17)
        er = p["espn_ros"] / gl_ if p.get("espn_ros") and gl_ else None
        if er is not None and not (0.35 * max(p["avg"], 1) <= er <= 2.5 * max(p["avg"], 1)):
            er = None
        parts = [(0.45, med), (0.15, p["avg"])] + ([(0.4, er)] if er is not None else [])
    blend = sum(w_ * x for w_, x in parts) / sum(w_ for w_, _ in parts)
    if pr.get("trend"):  # usage going up or down lately moves value up to 10%
        blend *= max(0.9, min(1.1, 1 + 0.4 * (pr["trend"] - 1)))
    fi = FILLIN.get(n)
    if fi:   # his points came with the starter out; the starter's back, so value him as the backup he is now
        blend = min(blend, fi["with_pg"]) if fi["with_pg"] is not None else blend * 0.4
    base = OVR.get(n, blend)
    av = AVAIL.get(n)
    if av and av.get("v") and av.get("manual"):
        base = av["v"]
    v, q = {}, {}
    for w in WKS:
        if not opp(p, w):
            v[w], q[w] = 0.0, 0.0
            continue
        rk = opp_rank(p, w)
        f = opp_factor(p, w)
        itr = it_ratio(p["nfl"], w)
        vf = itr ** VEGAS_B.get(p["pos"], 0) if itr else 1.0   # Vegas implied team points, only as much as the backtest says it helps
        v[w] = p["proj"] if (w == CUR and p.get("proj") is not None) else round(base * f * vf, 2)
        inj = p.get("inj") or "ACTIVE"
        if av:
            q[w] = float(av.get(str(w), 0))
            if w == CUR and p.get("proj") == 0: q[w] = 0.0
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
    mc = None
    if opp(p, CUR):   # the model's own number for this week (before ESPN's projection is swapped in)
        itr = it_ratio(p["nfl"], CUR)
        mc = round(base * opp_factor(p, CUR) * (itr ** VEGAS_B.get(p["pos"], 0) if itr else 1.0), 2)
    V[n] = {"v": v, "q": q, "base": round(base, 1), "recent": None if r is None else round(r, 1), "model_cur": mc}


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


# ---------------- how much each team's score swings week to week (player by player, teammates move together) ----------------
CV = {"QB": 0.40, "RB": 0.55, "WR": 0.60, "TE": 0.65, "D": 0.65, "K": 0.45}
RHO = {frozenset(("QB", "WR")): 0.35, frozenset(("QB", "TE")): 0.30, frozenset(("QB", "RB")): 0.10, frozenset(("WR",)): -0.05, frozenset(("RB",)): -0.10, frozenset(("WR", "TE")): -0.05}
def p_sd(n, mu):
    pr = PROF.get(n) or {}
    g_, sd_ = pr.get("games") or 0, pr.get("sd")
    prior = CV.get(P[n]["pos"], 0.55) * max(mu, 1.0)
    if sd_ is None:
        return prior
    # his own game log, scaled to this week's projection, blended with a normal swing for his position
    own = sd_ * (mu / max(1.0, P[n]["avg"])) if P[n]["avg"] > 0 else prior
    return math.sqrt((g_ * own ** 2 + 4 * prior ** 2) / (g_ + 4))
_TSD = {}
def team_sd_raw(names, w):
    key = (tuple(sorted(names)), w)
    if key in _TSD:
        return _TSD[key]
    _, lu_ = lineup(names, w, detail=True)
    pl_ = [(x["player"], P[x["player"]]["pos"], P[x["player"]]["nfl"], p_sd(x["player"], x["pts"])) for x in lu_ if x["pts"] > 0]
    var = sum(sd_ ** 2 for *_, sd_ in pl_)
    for i in range(len(pl_)):
        for j in range(i + 1, len(pl_)):
            if pl_[i][2] == pl_[j][2]:
                var += 2 * RHO.get(frozenset((pl_[i][1], pl_[j][1])), 0) * pl_[i][3] * pl_[j][3]
    _TSD[key] = math.sqrt(max(var, 25))
    return _TSD[key]
# calibrate to how much teams in this league really swung week to week this season
_res = []
for t, tm in TEAMS.items():
    sc_ = [v for k, v in (tm.get("scores") or {}).items() if int(k) < CUR and v > 0]
    if len(sc_) >= 4:
        mu_ = sum(sc_) / len(sc_); _res += [x - mu_ for x in sc_]
EMP_SD = math.sqrt(sum(x * x for x in _res) / max(1, len(_res) - len(TEAMS))) if len(_res) > 30 else SD
_raw = [team_sd_raw(ROSTER[t], w) for t in TEAMS for w in WKS]
SD_K = EMP_SD / (sum(_raw) / len(_raw))
def team_sd(names, w):
    return SD_K * team_sd_raw(names, w)
print(f"weekly swing: league actual {EMP_SD:.1f}, player-level model avg {sum(_raw) / len(_raw):.1f} -> scale {SD_K:.2f}")

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
        sdv = np.array([team_sd(rost[t], w) for t in IDS])
        S[w] = arr + sdv[None, :] * rng.standard_normal((N, T))
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
po_pts = lambda n: sum(ev(n, w) for w in (SEMI, FINAL))
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
    """What other managers think he's worth: mostly ESPN's rest-of-season number (what they see in the app),
    plus season average and recent form, scaled by how often managers actually start him."""
    p = P[n]; av_ = AVG.get(n, p["avg"]); rec = V[n]["recent"] if V[n]["recent"] is not None else av_
    gl_ = games_left(p, 17)
    er = p["espn_ros"] / gl_ if p.get("espn_ros") and gl_ else None
    if er is not None and av_ > 0 and not (0.35 * av_ <= er <= 2.5 * av_):
        er = None
    base_ = OVR.get(n, (0.5 * er + 0.35 * av_ + 0.15 * rec) if er is not None else (0.7 * av_ + 0.3 * rec))
    st = p.get("start_pct")
    perceive = 0.75 + 0.5 * (st / 100) if st is not None else 1.0
    return base_ * perceive * avail(n)
REPL_PUB = {}
for pos, k in REPL_N.items():
    L = sorted((pub(n) for n, p in P.items() if p["pos"] == pos), reverse=True)
    REPL_PUB[pos] = L[min(k, len(L) - 1)] if L else 0
MKT_OWN = {n: max(0.0, pub(n) - REPL_PUB[p["pos"]]) for n, p in P.items() if p["pos"] in SKILL}
# Market value = FantasyCalc (prices from millions of real trades) when we have it, our own estimate otherwise.
_fcp = ROOT / "data" / LID / "fantasycalc.json"
FC = json.loads(_fcp.read_text()) if _fcp.exists() else {}
FCV = {}
for n, p in P.items():
    x = FC.get(str(p.get("id")))
    if x and x.get("v") and p["pos"] in SKILL:
        v_ = float(x["v"])
        if p["pos"] == "QB" and SET.get("pass_td", 4) >= 6:
            v_ *= 1.15   # FantasyCalc assumes 4-pt passing TDs; QBs are worth more here
        FCV[n] = v_
if len(FCV) >= 60:
    # put our own estimate on the same scale for guys FantasyCalc doesn't list (deep bench / waiver types)
    pairs = sorted((MKT_OWN[n], FCV[n]) for n in FCV if MKT_OWN.get(n, 0) > 0)
    k_ = (sum(b for _, b in pairs) / max(1e-9, sum(a for a, _ in pairs))) if pairs else 1.0
    floor_ = min(FCV.values())
    MKT = {n: (FCV[n] if n in FCV else min(MKT_OWN[n] * k_, floor_ * 0.9)) for n in MKT_OWN}
    MKT_SRC = "FantasyCalc"
else:
    MKT, MKT_SRC = MKT_OWN, "own estimate"
print(f"market values: {MKT_SRC} ({len(FCV)} matched)")
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
THEIR_START = {t: {x["player"] for x in lineup(ROSTER[t], next(iter(REGW), SEMI), detail=True)[1]} for t in IDS}
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
            # what they get vs what they give, in market value. The side getting the best player in a
            # 2-for-1 pays an ~8% "stud premium" (PFF's redraft rule), instead of a flat tax.
            vin, vout = sum(mval(n) for n in G), sum(mval(n) for n in T_)
            if len(G) != len(T_):
                stud = max(G + T_, key=mval)
                if stud in G and len(G) < len(T_): vin += 0.08 * mval(stud)
                if stud in T_ and len(T_) < len(G): vout += 0.08 * mval(stud)
            ratio = vin / max(vout, 0.5)
            best_g, best_t = max(mval(n) for n in G), max(mval(n) for n in T_)
            need = (team_score(r, t, REGW) - base_t) / max(1, len(REGW)) if REGW else 0
            # nobody gives you two good players for one unless your guy is clearly the best player in the deal
            two_for_one_ok = len(T_) < 2 or best_g >= 1.15 * best_t
            both_start = len(T_) == 2 and all(x in THEIR_START[t] for x in T_)
            if both_start and need < -8:
                continue   # nobody hands over two starters and gets way worse
            st_g = max((P[x].get("start_pct") or 0) for x in G); st_t = max((P[x].get("start_pct") or 0) for x in T_)
            start_ok = st_g >= st_t - 25   # nobody gives up a guy they start every week for a guy the whole league benches
            real = two_for_one_ok and start_ok and not (both_start and need < -3) and (ratio >= 1.15 if status[t] == "out" else (ratio >= 0.95 and need >= -1))   # within 5% = fair; eliminated teams want a clear win
            stretch = (len(T_) < 2 or best_g >= best_t) and ratio >= 0.85 and need >= -6 and st_g >= st_t - 45
            if not real and not stretch:
                continue
            cands.append({"t": t, "G": G, "T": T_, "gain": gain, "ratio": ratio, "need": need, "real": real, "r": r})
cands.sort(key=lambda c: -c["gain"])
seen, top, nr, ns = set(), [], 0, 0
quota = A["sims"]["trades_to_sim"]
for c in cands:
    k = ("+".join(c["T"]), c["real"])
    core_t = max(c["T"], key=mval); core_g = max(c["G"], key=mval)
    if k in seen or (c["real"] and nr >= quota * 2 // 3) or (not c["real"] and ns >= quota // 3) or sum(1 for x in top if x["T"] == c["T"]) >= 1 \
       or sum(1 for x in top if max(x["T"], key=mval) == core_t) >= 2 or sum(1 for x in top if x["t"] == c["t"] and max(x["G"], key=mval) == core_g) >= 2:   # variety: not 7 versions of the same deal
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
def took_spot(n, leaving):
    """The guy at the same position he replaces in the trade, if any."""
    return next((x for x in leaving if P[x]["pos"] == P[n]["pos"]), None)
def slot_swap(before_lu, after_lu, new_players, leaving=()):
    """For a lineup before/after a trade: which new guy starts, in what slot, and who he pushes out (or whose spot he takes over)."""
    out = []
    aset = {x["player"]: x for x in after_lu}
    pushed = [x for x in before_lu if x["player"] not in aset and x["player"] not in leaving]
    for n in new_players:
        if n in aset:
            same = [x for x in pushed if P[x["player"]]["pos"] == P[n]["pos"]]   # only call it a swap when it's the same position
            rep = min(same, key=lambda x: x["pts"]) if same else None
            if rep: pushed.remove(rep)
            out.append((n, nice_slot(aset[n]["slot"]), aset[n]["pts"], rep))
    return out
def depth(t, pos, w):
    """How many guys at a position on a team would score at least a starter-ish number."""
    bar = {"QB": 14, "RB": 9, "WR": 9, "TE": 7}.get(pos, 99)
    return sum(1 for n in ROSTER[t] if P[n]["pos"] == pos and ev(n, w) >= bar)
def trade_story(c):
    t, G, T_ = c["t"], c["G"], c["T"]
    w = next((x for x in REGW), CUR) if status[t] != "out" else SEMI
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
        why_them.append(f"{n} would start at {sl} for them" + (f" over {rep['player']} ({rep['pts']:.1f} pts)" if rep else f" in {poss(first(took_spot(n, T_)))} spot" if took_spot(n, T_) else "") + f", about {pts:.1f} pts a week.")
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
        why_me.append(f"{n} starts at {sl} for you in the playoffs ({pts:.1f} pts)" + (f" over {rep['player']} ({rep['pts']:.1f})." if rep else f" in {poss(first(took_spot(n, G)))} spot." if took_spot(n, G) else "."))
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
    if c["ratio"] >= 1.35:
        why_me.append(f"You're overpaying in trade value ({c['ratio']:.2f}x what you get back). It still helps your title odds, but try asking for a little more first.")
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
                   "blocked": c["t"] in NO_TRADE, "wk16": r1(res["means"][SEMI]), "wk17": r1(res["means"][FINAL]), "wk16_before": r1(mine["means"][SEMI]), "wk17_before": r1(mine["means"][FINAL]),
                   # the "why" breakdown
                   "weeks": [{"w": w, "before": r1(mine["means"][w]), "after": r1(res["means"][w]), "po": w in PW} for w in REGW + [w for w in PLW if w not in REGW]],
                   "their_before": r1(team_score(ROSTER, c["t"], REGW) / max(1, len(REGW))), "their_after": r1(team_score(c["r"], c["t"], REGW) / max(1, len(REGW))),
                   "their_po_pct": r1(base[c["t"]]["po"]), "their_title_after": r1(res_all[c["t"]]["ch"]) if (res_all := res_full) else None,
                   "mv_send": r1(sum(MKT.get(n, 0) for n in c["G"])), "mv_get": r1(sum(MKT.get(n, 0) for n in c["T"])), **trade_story(c)})
trades = [x for x in trades if x["change_pts"] >= 1.0]   # anything smaller is noise, not worth a trade
trades.sort(key=lambda x: (-x["change_pts"]))
print(f"trades: {len(cands)} candidates, {len(trades)} simmed, {time.time() - t0:.1f}s")

# ---------------- waivers ----------------
fa = [n for n, p in P.items() if p["t"] == 0 and p["pos"] in SKILL + ("D", "K")]
BACK = {n for n, a in AVAIL.items() if sum(float(a.get(str(w), 0)) for w in PLW) / max(1, len(PLW)) >= 0.4}   # hurt but expected back for the playoffs
bench = sorted([n for n in ROSTER[ME] if n not in CORE and n not in BACK and (P[n].get("own") or 0) < 60], key=lambda n: ros(n) + po_pts(n))   # don't drop hurt guys we expect back, or names the league still values (trade those instead)
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
                    "playoff_pts": r1(po_pts(n)), "opp16": opp(P[n], SEMI) or "BYE", "opp17": opp(P[n], FINAL) or "BYE", "own_pct": P[n]["own"], "_r": r})
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
                  "reg_pts": r1(reg_pts(n)), "playoff_pts": r1(po_pts(n)), "opp16": opp(p, SEMI) or "BYE", "rank16": opp_rank(p, SEMI),
                  "opp17": opp(p, FINAL) or "BYE", "rank17": opp_rank(p, FINAL), "recent": V[n]["recent"], "status": p["inj"],
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
    if pos not in STARTER_N or p.get("inj") in OUTS + ("DOUBTFUL",) or n in FILLIN:
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
                   "win_this_week_pct": r1(b["wk"]), "wk16_lineup": r1(b["means"][SEMI]), "wk17_lineup": r1(b["means"][FINAL])})
league.sort(key=lambda x: (-x["champ_pct"], -x["playoffs_pct"]))
# ---------------- every team's card ----------------
from math import erf as _erf
def best_lu(rost, w):
    """The lineup you'd actually set: anyone likely to play (50%+) at his full projection. The total is what you'd expect after injury odds."""
    _, lu_ = lineup(rost, w, on=lambda n: V[n]["q"][w] >= 0.5, detail=True)
    for x in lu_:
        n = x["player"]; x["pos"] = P[n]["pos"]; x["nfl"] = P[n]["nfl"]; x["opp"] = opp(P[n], w) or "BYE"; x["rank"] = opp_rank(P[n], w)
        x["chance"] = round(V[n]["q"][w], 2); x["exp"] = r1(ev(n, w))
    used = {x["player"] for x in lu_}
    maybe = [{"player": n, "pts": r1(V[n]["v"][w]), "chance": round(V[n]["q"][w], 2)} for n in rost if n not in used and 0 < V[n]["q"][w] < 0.5 and V[n]["v"][w] >= 10]
    return sum(x["exp"] for x in lu_), lu_, maybe
def team_week(t, w):
    tot_, lu_, _ = best_lu(ROSTER[t], w)
    lab = "Final" if w == PO3 else "Semis" if w == PO2 else ("Round 1" if SET.get("playoff_teams") != 4 else "Semis") if w == PO1 else f"Week {w}"
    if SET.get("playoff_teams") == 4 and w == PO2: lab = "Final"
    row = {"w": w, "label": lab, "total": r1(tot_), "expected": r1(base[t]["means"][w]), "lineup": lu_}
    g = next(((h, a) for wk, h, a in REM if wk == w and t in (h, a)), None)
    if g:
        o = g[0] if g[1] == t else g[1]
        me_, op_ = base[t]["means"][w], base[o]["means"][w]
        row.update({"opp_id": o, "opponent": TNAME[o], "opp_expected": r1(op_), "opp_record": f'{TEAMS[o]["w"]}-{TEAMS[o]["l"]}',
                    "win_pct": r1(base[t]["wk"]) if w == CUR else r1(50 * (1 + _erf((me_ - op_) / (math.sqrt(team_sd(ROSTER[t], w) ** 2 + team_sd(ROSTER[o], w) ** 2) * math.sqrt(2)))))})
    return row
team_cards = {}
for t in IDS:
    rost = ROSTER[t]
    tw_tot, tw_lu = lineup(rost, CUR, detail=True)
    po_tot, po_lu = lineup(rost, SEMI, detail=True)
    starters = {x["player"] for x in po_lu}
    team_cards[str(t)] = {"id": t, "name": TNAME[t], "me": t == ME, "record": f'{TEAMS[t]["w"]}-{TEAMS[t]["l"]}', "pf": TEAMS[t]["pf"],
        "odds": {"po": r1(base[t]["po"]), "bye": r1(base[t]["bye"]), "ch": r1(base[t]["ch"]), "fi": r1(base[t]["fi"])}, "status": status[t],
        "weekly": [r1(base[t]["means"][w]) for w in WKS], "weeks": WKS,
        "this_week": {"total": r1(tw_tot), "lineup": tw_lu}, "semis": {"week": SEMI, "total": r1(po_tot), "lineup": po_lu},
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
    for w, k in [x for x in ((R1, "wk15"), (SEMI, "wk16"), (FINAL, "wk17")) if x[0]]:
        o[k + "_opp"] = opp(p, w) or "BYE"; o[k + "_rank"] = opp_rank(p, w); o[k + "_proj"] = r1(V[n]["v"][w]); o[k + "_chance"] = V[n]["q"][w]
    my_players.append(o)
my_players.sort(key=lambda x: -x["playoff_expected"])
ideal = {}
for w in PLW:
    tot_, lu_ = lineup(ROSTER[ME], w, on=lambda n: V[n]["q"][w] > 0, detail=True)
    for x in lu_:
        x["chance"] = V[x["player"]]["q"][w]; x["opp"] = opp(P[x["player"]], w) or "BYE"; x["rank"] = opp_rank(P[x["player"]], w)
    bench_ = sorted([n for n in ROSTER[ME] if n not in {x["player"] for x in lu_} and V[n]["q"][w] > 0], key=lambda n: -V[n]["v"][w])[:4]
    ideal[w] = {"total": r1(tot_), "lineup": lu_, "next_up": [{"player": n, "pts": r1(V[n]["v"][w]), "pos": P[n]["pos"]} for n in bench_]}
healthy = {w: r1(lineup(ROSTER[ME], w, on=lambda n: V[n]["v"][w] > 0 and P[n].get("inj") not in ("OUT", "INJURY_RESERVE") or n in AVAIL)) for w in PLW}
tot, lu, _ = best_lu(ROSTER[ME], CUR)
starters = {x["player"] for x in lu}
this_week = {"week": CUR, "total": r1(tot), "lineup": lu,
             "bench": [{"player": n, "pos": P[n]["pos"], "pts": r1(ev(n, CUR))} for n in sorted(ROSTER[ME], key=lambda n: -ev(n, CUR)) if n not in starters]}
my_game = next(([h, a] for wk, h, a in REM if wk == CUR and ME in (h, a)), None)
if my_game:
    o = my_game[0] if my_game[1] == ME else my_game[1]
    this_week["opponent"] = TNAME[o]; this_week["win_pct"] = r1(mine["wk"]); this_week["opp_proj"] = r1(lineup(ROSTER[o], CUR))


# ---------------- week by week: my best lineup every week, and who I'm playing ----------------
from math import erf, sqrt
def lu_rows(rost, w):
    tot_, lu_, _ = best_lu(rost, w)
    return tot_, lu_
schedule = []
for w in WKS:
    tot_, lu_ = lu_rows(ROSTER[ME], w)
    used = {x["player"] for x in lu_}
    bench_ = [{"player": n, "pos": P[n]["pos"], "pts": r1(V[n]["v"][w]), "chance": round(V[n]["q"][w], 2), "opp": opp(P[n], w) or "BYE", "nfl": P[n]["nfl"]} for n in sorted(ROSTER[ME], key=lambda n: -ev(n, w)) if n not in used][:7]
    byes = [n for n in ROSTER[ME] if not opp(P[n], w)]
    hurt = [n for n in ROSTER[ME] if n in V and V[n]["q"][w] < 0.6 and opp(P[n], w)]
    _, _, maybe_ = best_lu(ROSTER[ME], w)
    row = {"maybe": maybe_, "w": w, "kind": "reg" if w <= REG else "po", "label": f"Week {w}" + ("" if w <= REG else " · " + ("Round 1" if w == PO1 else "Semis" if w == PO2 else "Final") if SET.get("playoff_teams") != 4 else ""),
           "total": r1(tot_), "lineup": lu_, "bench": bench_, "byes": byes, "hurt": hurt}
    g = next(((h, a) for wk, h, a in REM if wk == w and ME in (h, a)), None)
    if g:
        o = g[0] if g[1] == ME else g[1]
        ot, olu = lu_rows(ROSTER[o], w)
        row.update({"opponent": TNAME[o], "opp_id": o, "opp_total": r1(ot), "opp_lineup": olu,
                    "win_pct": r1(mine["wk"]) if w == CUR else r1(50 * (1 + erf((base[ME]["means"][w] - base[o]["means"][w]) / (sqrt(team_sd(ROSTER[ME], w) ** 2 + team_sd(ROSTER[o], w) ** 2) * sqrt(2)))))})
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
    if (cp is not None and cp >= 0.5 and x["ros_gain"] / WEQ >= 1) or x["ros_gain"] / WEQ >= 1.5:
        x["verdict"], x["why"] = "CLAIM", f"Real upgrade: about {x['ros_gain'] / WEQ:+.1f} pts a week in your lineup the rest of the way" + (f", title odds {cp:+.1f}%." if cp is not None and abs(cp) >= 1 else ".")
    elif x["this_week_gain"] >= 3:
        x["verdict"], x["why"] = "CLAIM", f"Fills a hole this week ({x['this_week_gain']:+.1f} pts). You can drop him again after."
    elif b and b["score"] >= 40 and x["drop"] not in {y["player"] for y in (sw0["lineup"] if sw0 else [])} and x["drop"] not in {t["alt"] for t in tossups} | {t["start"] for t in tossups} and x["ros_gain"] >= -1:
        x["verdict"], x["why"] = "CLAIM", f"Breakout radar gives him {b['score']}% to become a weekly starter. Cheap lottery ticket for {x['drop']}."
    else:
        x["verdict"], x["why"] = "SKIP", f"Barely moves the needle ({x['this_week_gain']:+.1f} this week, {x['ros_gain']:+.1f} rest of season)."
waivers.sort(key=lambda x: (x["verdict"] != "CLAIM", -(x["ros_gain"] + 2 * x["this_week_gain"])))
CLAIMED = {x["player"] for x in waivers if x["verdict"] == "CLAIM"}
seen_drop = {}
for x in waivers:   # one claim per guy you'd drop: the best one wins, the rest are backups
    if x["verdict"] == "CLAIM":
        if x["drop"] in seen_drop:
            x["verdict"], x["why"] = "SKIP", f"Backup plan if {seen_drop[x['drop']]} is gone. " + x["why"]
        else:
            seen_drop[x["drop"]] = x["player"]

for b in breakouts:   # the breakout tab and the waiver tab should never disagree
    if b["action"] == "ADD" and b["player"] not in {x["player"] for x in waivers if x["verdict"] == "CLAIM"}:
        b["action"] = "WATCH"
# ---------------- this week's to-do list ----------------
todo = []
if sw0:
    for x in sw0["lineup"]:
        if x["opp"] != "BYE" and x["chance"] >= 0.8:
            continue
        pos_ = P[x["player"]]["pos"]
        fa_ = max([w for w in waivers if w["pos"] == pos_ and w["this_week_gain"] > 0], key=lambda w: w["this_week_gain"], default=None)
        if not fa_ and x["opp"] == "BYE":   # bye week: stream the best free agent for just this week
            bf = max([n for n, q_ in P.items() if q_["t"] == 0 and q_["pos"] == pos_ and opp(q_, CUR)], key=lambda n: ev(n, CUR), default=None)
            if bf and ev(bf, CUR) > 0:
                fa_ = {"player": bf, "drop": x["player"] if pos_ in ONE_ONLY else (bench[0] if bench else x["player"]), "this_week_gain": r1(ev(bf, CUR))}
        alt_ = next((b for b in sw0["bench"] if b["opp"] != "BYE" and b["pos"] in ELIG.get(x["slot"], ()) and b["pts"] > 0), None)
        what = "is on bye" if x["opp"] == "BYE" else f"might not play ({round(100 * x['chance'])}%)"
        likely = x["opp"] != "BYE" and x["chance"] >= 0.5
        if likely:
            fix = (f"If he's ruled out, start {alt_['player']} ({alt_['pts']})." if alt_ else f"If he's ruled out, {fa_['player']} is the best pickup." if fa_ else "Check his status before kickoff.")
        else:
            fix = (f"Stream {fa_['player']} this week: add him, drop {fa_['drop']} ({fa_['this_week_gain']:+.1f} pts). Grab your guy back after if he's still there." if fa_ and fa_["drop"] == x["player"] else f"Add {fa_['player']} and drop {fa_['drop']} ({fa_['this_week_gain']:+.1f} pts).") if fa_ else f"Start {alt_['player']} ({alt_['pts']}) instead." if alt_ else "Check his status before kickoff."
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
DL = SET.get("trade_deadline")
DL_DAYS = None
if DL:
    DL_DAYS = (DL / 1000 - time.time()) / 86400
    if 0 <= DL_DAYS <= 10:
        todo.append({"urgent": DL_DAYS <= 3, "icon": "⏳", "title": f"Trade deadline in {max(1, round(DL_DAYS))} day{'s' if round(DL_DAYS) != 1 else ''}", "detail": "After that, trades lock for the season. Send any offers you want before then.", "tab": "trades"})
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


# ---------------- lineup lock + starters whose status just changed ----------------
from zoneinfo import ZoneInfo as _ZI
import datetime as _dt
_KR = _U.get("kick") or {}
KICK = {int(w): {TEAMFIX.get(t, t): v for t, v in d.items()} for w, d in _KR.items()}
lock = None
if sw0:
    ks = sorted(((KICK.get(CUR) or {}).get(P[x["player"]]["nfl"]), x["player"]) for x in sw0["lineup"] if (KICK.get(CUR) or {}).get(P[x["player"]]["nfl"]))
    if ks:
        k0 = _dt.datetime.strptime(ks[0][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        firsts = [n for k, n in ks if k == ks[0][0]]
        local = k0.astimezone(_ZI("America/Los_Angeles"))
        when = local.strftime("%a %-I:%M %p")
        lock = {"first": ks[0][0], "when_pt": when, "players": firsts, "games": {n: k for k, n in ks}}
        hrs = (k0 - _dt.datetime.now(_dt.timezone.utc)).total_seconds() / 3600
        if 0 < hrs <= 96:
            todo.insert(0, {"urgent": hrs <= 24, "icon": "🔒", "title": f"Set your lineup by {when} PT", "detail": f"{', '.join(firsts)} {'plays' if len(firsts) == 1 else 'play'} first. Players lock when their game starts.", "tab": "home"})
starters_now = {x["player"] for x in (sw0["lineup"] if sw0 else [])}
for c_ in changes:
    if c_["type"] == "injury":
        nm = c_["text"].split(" (")[0]
        if nm in starters_now:
            todo.insert(0, {"urgent": True, "icon": "📣", "title": f"Status change: {nm}", "detail": c_["text"].split(": ", 1)[-1].replace("None", "healthy") + ". Recheck your lineup.", "tab": "home"})

# ---------------- weekly report card: save this week's projections, grade past weeks against what happened ----------------
ARCH_P = ROOT / "data" / LID / "proj_archive.json"
ARCH = json.loads(ARCH_P.read_text()) if ARCH_P.exists() else {}
snap_ = {"players": {}, "teams": {str(t): r1(base[t]["means"][CUR]) for t in IDS}, "games": []}
for n, p in P.items():
    if p["pos"] in ("QB", "RB", "WR", "TE") and V[n].get("model_cur") is not None and (p["t"] or (p.get("proj") or 0) >= 5):
        snap_["players"][n] = [V[n]["model_cur"], p.get("proj")]
for wk, h, a in REM:
    if wk == CUR:
        snap_["games"].append([h, a, r1(base[h]["wk"])])
_has_e = lambda sn: sum(1 for v in sn["players"].values() if v[1] is not None)
if str(CUR) not in ARCH or _has_e(snap_) >= _has_e(ARCH[str(CUR)]):   # once games start ESPN drops its projections; keep the pre-kickoff snapshot
    ARCH[str(CUR)] = snap_
ARCH_P.write_text(json.dumps(ARCH, separators=(",", ":")))
report = []
for wk_s, sn in sorted(ARCH.items(), key=lambda x: int(x[0])):
    W = int(wk_s)
    if W >= CUR:
        continue
    pe_m, pe_e, n_, ne_, pm_e = 0.0, 0.0, 0, 0, 0.0
    for n, (m_, e_) in sn["players"].items():
        act = (P.get(n) or {}).get("wk", {}).get(str(W))
        if not act:
            continue
        pe_m += abs(m_ - act); n_ += 1
        if e_ is not None:
            pe_e += abs(e_ - act); pm_e += abs(m_ - act); ne_ += 1
    te, tn = 0.0, 0
    for t, pr_ in sn["teams"].items():
        a_ = (TEAMS.get(int(t)) or {}).get("scores", {}).get(str(W))
        if a_:
            te += abs(pr_ - a_); tn += 1
    calls, right, brier = 0, 0, 0.0
    for h, a, ph in sn["games"]:
        sh, sa = (TEAMS.get(h) or {}).get("scores", {}).get(str(W)), (TEAMS.get(a) or {}).get("scores", {}).get(str(W))
        if sh is None or sa is None:
            continue
        hw = 1.0 if sh > sa else 0.0
        calls += 1; right += int((ph >= 50) == (hw == 1)); brier += (ph / 100 - hw) ** 2
    if n_:
        report.append({"w": W, "players": n_, "model_mae": r1(pe_m / n_), "espn_mae": r1(pe_e / ne_) if ne_ else None, "model_mae_vs_espn": r1(pm_e / ne_) if ne_ else None, "team_mae": r1(te / tn) if tn else None,
                       "games": calls, "picked_right": right, "brier": round(brier / calls, 3) if calls else None})

hist_p = OUT / "history.json"
hist = json.loads(hist_p.read_text()) if hist_p.exists() else {"runs": [], "moves": []}
stamp = M["at"]
if changes:
    hist["moves"] = ([{"at": stamp, **c} for c in changes] + hist["moves"])[:300]
if hist["runs"] and hist["runs"][-1]["at"] == stamp:
    hist["runs"].pop()   # same ESPN data, newer model: replace the row instead of keeping a stale one
if True:
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
if swing and abs(swing[0]["change_pts"]) >= 3:
    take.append(f"Biggest risk: {swing[0]['what'].replace(' out for the playoffs', '')} getting hurt costs you {abs(swing[0]['change_pts'])}% in title odds. Make sure you have a real backup there.")
wtop = next((x for x in waivers if x.get("verdict") == "CLAIM" and x["ros_gain"] / WEQ >= 1), None)
if wtop:
    take.append(f"Waivers: {wtop['player']} ({wtop['pos']}) for {wtop['drop']} adds about {wtop['ros_gain'] / WEQ:+.1f} pts a week to your lineup.")

summary = {"at": M["at"], "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "league": M.get("league_name") or LCFG.get("name") or LID, "league_id": LID, "week": CUR, "reg_end": REG,
           "playoff_weeks": [PO1, PO2, PO3], "po_view": [R1, SEMI, FINAL], "weighted_weeks": PW, "me": ME, "my_team": TNAME[ME],
           "kpis": {k: r1(mine[k]) for k in ("ch", "fi", "po", "bye", "s1")}, "healthy_lineup": healthy, "ideal_lineups": ideal, "tiers": TIERS,
           "expected_lineup": {w: r1(mine["means"][w]) for w in PLW}, "take": take, "this_week": this_week, "swing": swing,
           "sims": A["sims"], "market_source": MKT_SRC, "lock": lock, "trade_deadline": SET.get("trade_deadline"), "backtest": BT, "report_card": report, "variance": {"league_actual_sd": r1(EMP_SD), "scale": round(SD_K, 2)}, "assumptions": {"avail": AVAIL, "override": OVR},
           "settings": {k: v for k, v in SET.items() if k != "scoring"}, "scoring": SC, "overrides": OVR_SET}
tester = {"sdk": round(SD_K, 3), "rho": [[sorted(k)[0], sorted(k)[-1], v] for k, v in RHO.items()], "weeks": WKS, "cur": CUR, "reg": REG, "po": [PO1, PO2, PO3], "np": 4 if SET.get("playoff_teams") == 4 else 6, "me": ME, "sd": SD,
          "slots": SLOTS, "elig": ELIG,
          "teams": [{"id": t, "name": TNAME[t], "wins": TEAMS[t]["w"] + 0.5 * TEAMS[t].get("t", 0), "pf": TEAMS[t]["pf"]} for t in IDS],
          "sched": REM, "players": [{"n": n, "pos": p["pos"], "t": p["t"], "nfl": p["nfl"], "e": [r1(ev(n, w)) for w in WKS], "v": [r1(V[n]["v"][w]) for w in WKS], "q": [round(V[n]["q"][w], 2) for w in WKS], "s": [r1(p_sd(n, ev(n, w))) for w in WKS]}
                                    for n, p in P.items() if ros(n) + ev(n, CUR) > 3 or p["t"] == ME]}
latest = {"summary": summary, "teams": team_cards, "league": league, "trades": trades, "waivers": waivers, "rankings": ranks, "bubble": bubble, "schedule": schedule, "breakouts": breakouts, "tossups": tossups, "todo": todo,
          "my_players": my_players, "tester": tester}
PO_STARTERS = {x["player"] for w in (SEMI, FINAL) for x in lineup(ROSTER[ME], w, detail=True)[1]}


def verdict(n, p, pr):
    """Plain-English call + the 3-6 reasons that matter most."""
    R = []
    tr, lk = pr.get("trend"), pr.get("fpoe")
    if tr and tr >= 1.12: R.append(("📈", f"Usage is up {round((tr - 1) * 100)}% over his last 3 games ({pr.get('opp_g3')} touches+targets a game)."))
    if tr and tr <= 0.88: R.append(("📉", f"Usage is down {round((1 - tr) * 100)}% over his last 3 games."))
    if lk is not None and lk >= 2.5: R.append(("🍀", f"Running hot: scoring {lk} more a game than his usage says he should. Expect a little cool-down."))
    if lk is not None and lk <= -2.5: R.append(("🔄", f"Unlucky so far: scoring {abs(lk)} less a game than his usage says. Due to bounce back."))
    po = [(s_["opp"], s_["rank"]) for s_ in pr.get("schedule", []) if s_["w"] in (SEMI, FINAL)]
    if pr.get("sos_po_label") == "Easy": R.append(("🟢", "Soft playoff matchups: " + " and ".join(o for o, _ in po) + "."))
    if pr.get("sos_po_label") == "Tough": R.append(("🔴", "Tough playoff matchups: " + " and ".join(o for o, _ in po) + "."))
    if pr.get("sos_ros_label") == "Easy" and pr.get("sos_po_label") != "Easy": R.append(("🟢", "Easy schedule the rest of the regular season."))
    qs = [V[n]["q"][w] for w in (SEMI, FINAL) if opp(p, w)]
    if qs and min(qs) < 0.95: R.append(("🚑", f"Only about {round(100 * sum(qs) / len(qs))}% likely to play in the playoffs."))
    if any(not opp(p, w) for w in (SEMI, FINAL)): R.append(("💤", "Has a bye during the fantasy playoffs."))
    dep = pr.get("depth") or {}
    if n in AUTO_WHY and n not in MANUAL:
        a_ = AVAIL[n]; R.append(("🩺", AUTO_WHY[n] + " Chance to play: " + ", ".join(f"Wk {w} {round(100 * float(a_[str(w)]))}%" for w in WKS if w >= CUR and float(a_.get(str(w), 0)) > 0)[:120] + "."))
    if n in ROLE and n not in FILLIN and abs(ROLE[n]["avg"] - p["avg"]) >= 1.5:
        ro = ROLE[n]; R.append(("📈" if ro["dir"] == "up" else "📉", f"His role changed in Wk {ro['since']} (snaps {ro['snap_before']}% → {ro['snap_after']}%). He's averaged {ro['avg']} since then vs {p['avg']} on the season, so we judge him on the new role."))
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
        elif ros_pg(n) < 6 and n not in BACK: call, line = "DROP", "Not helping you. His roster spot could be a waiver add."
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

# keep the league picker / "other leagues" strip in sync even when only this league reran
_li = ROOT / "docs" / "data" / "leagues.json"
if _li.exists():
    try:
        _idx = json.loads(_li.read_text())
        for _l in _idx.get("leagues", []):
            if str(_l.get("id")) == str(LID):
                _l.update({"champ_pct": summary["kpis"]["ch"], "week": CUR, "at": summary["at"], "todo": todo[:4]})
        _li.write_text(json.dumps(_idx, indent=1))
    except Exception as _e:
        print("leagues.json sync skipped:", _e)
