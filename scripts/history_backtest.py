"""Backtest the projection recipe on past NFL seasons (nflverse), using only info known before each game.

Usage: python3 scripts/history_backtest.py <folder with stats_YYYY.csv + ep_YYYY.csv> <games.csv>
Writes data/history_fit.json, which analyze.py uses as a starting point (this season's data then nudges it).
"""
import csv, json, math, sys, pathlib
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = pathlib.Path(sys.argv[1]); GAMES = sys.argv[2]
SEASONS = list(range(2016, 2026))
FIX = {"LA": "LAR", "WAS": "WSH", "JAC": "JAX", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
fx = lambda t: FIX.get(t, t)

# league scoring (main league: full PPR, 6-pt passing TD)
S = dict(py=0.04, ptd=6, int=-2, ry=0.1, rtd=6, rec=1, recy=0.1, retd=6, fl=-2, two=2)


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def actual(r):
    return (f(r["passing_yards"]) * S["py"] + f(r["passing_tds"]) * S["ptd"] + f(r["passing_interceptions"]) * S["int"] +
            f(r["rushing_yards"]) * S["ry"] + f(r["rushing_tds"]) * S["rtd"] + f(r["receptions"]) * S["rec"] +
            f(r["receiving_yards"]) * S["recy"] + f(r["receiving_tds"]) * S["retd"] +
            (f(r.get("rushing_fumbles_lost")) + f(r.get("receiving_fumbles_lost")) + f(r.get("sack_fumbles_lost"))) * S["fl"] +
            (f(r.get("passing_2pt_conversions")) + f(r.get("rushing_2pt_conversions")) + f(r.get("receiving_2pt_conversions"))) * S["two"])


def xparts(e):
    """expected points split into non-TD and TD parts"""
    td = f(e["pass_touchdown_exp"]) * S["ptd"] + f(e["rush_touchdown_exp"]) * S["rtd"] + f(e["rec_touchdown_exp"]) * S["retd"]
    ntd = (f(e["pass_yards_gained_exp"]) * S["py"] + f(e["pass_interception_exp"]) * S["int"] + f(e["rush_yards_gained_exp"]) * S["ry"] +
           f(e["receptions_exp"]) * S["rec"] + f(e["rec_yards_gained_exp"]) * S["recy"] +
           (f(e["pass_two_point_conv_exp"]) + f(e["rush_two_point_conv_exp"]) + f(e["rec_two_point_conv_exp"])) * S["two"])
    return ntd, td


def atd(r):
    return f(r["passing_tds"]) * S["ptd"] + f(r["rushing_tds"]) * S["rtd"] + f(r["receiving_tds"]) * S["retd"]


lines = {}
SPR = {}
for g in csv.DictReader(open(GAMES)):
    if g["game_type"] != "REG" or not g["total_line"] or not g["spread_line"]:
        continue
    y, w = int(g["season"]), int(g["week"]); tot, spr = float(g["total_line"]), float(g["spread_line"])
    lines[(y, w, fx(g["home_team"]))] = tot / 2 + spr / 2
    lines[(y, w, fx(g["away_team"]))] = tot / 2 - spr / 2
    SPR[(y, w, fx(g["home_team"]))] = spr; SPR[(y, w, fx(g["away_team"]))] = -spr

rows = []
for y in SEASONS:
    ep = {}
    for e in csv.DictReader(open(SRC / f"ep_{y}.csv")):
        ep[(e["player_id"], int(e["week"]))] = e
    games = {}
    for r in csv.DictReader(open(SRC / f"stats_{y}.csv")):
        if r.get("season_type") != "REG" or r["position"] not in ("QB", "RB", "WR", "TE"):
            continue
        w = int(r["week"]); e = ep.get((r["player_id"], w))
        games.setdefault(r["player_id"], []).append({"w": w, "pos": r["position"], "team": fx(r["team"]), "opp": fx(r["opponent_team"]),
                                                     "pts": actual(r), "atd": atd(r), "x": xparts(e) if e else None})
    its = [v for (yy, _, _), v in lines.items() if yy == y]; it_avg = sum(its) / len(its)
    # defense "points allowed vs normal" using only earlier weeks
    def dvp_upto(W, _cache={}):
        key = (y, W)
        if key in _cache:
            return _cache[key]
        acc, gw, grp = {}, {}, {}
        for gl in games.values():
            prior = [g for g in gl if g["w"] < W]
            if len(prior) < 3:
                continue
            a = sum(g["pts"] for g in prior) / len(prior)
            for g in prior:
                k = (g["opp"], g["pos"]); acc[k] = acc.get(k, 0) + g["pts"] - a
                gw.setdefault(k, set()).add(g["w"]); grp[(g["opp"], g["pos"], g["w"])] = grp.get((g["opp"], g["pos"], g["w"]), 0) + g["pts"]
        gavg = {}
        for pos in ("QB", "RB", "WR", "TE"):
            v = [t for (d, p_, w_), t in grp.items() if p_ == pos]; gavg[pos] = sum(v) / len(v) if v else 1
        out = {k: (len(gw[k]) / (len(gw[k]) + 4)) * (t / len(gw[k])) / max(1, gavg[k[1]]) for k, t in acc.items()}
        _cache[key] = out
        return out
    for gl in games.values():
        gl.sort(key=lambda g: g["w"])
        for i, g in enumerate(gl):
            prior = gl[:i]
            px = [p for p in prior if p["x"]]
            if len(prior) < 3 or len(px) < 3 or g["w"] < 4:
                continue
            avg = sum(p["pts"] for p in prior) / len(prior); rec = sum(p["pts"] for p in prior[-4:]) / len(prior[-4:])
            nx = [p["x"][0] for p in px]; xt = [p["x"][1] for p in px]; na = [p["pts"] - p["atd"] for p in px]
            kk = {"QB": 12, "RB": 16}.get(g["pos"], 8); n_ = len(px)
            eff = max(0.75, min(1.35, 1 + (sum(na) / max(0.1, sum(nx)) - 1) * n_ / (n_ + kk)))
            m2 = lambda a: 0.5 * sum(a) / len(a) + 0.5 * sum(a[-4:]) / len(a[-4:])
            xf = m2(nx) * eff + m2(xt)
            it = lines.get((y, g["w"], g["team"])); itr = it / it_avg if it else 0.0
            mf = dvp_upto(g["w"]).get((g["opp"], g["pos"]), 0.0)
            # backup test: did a same-team same-position teammate who averaged 12+ play last week but not this week?
            starter_out = 0.0
            for oid, ol in games.items():
                if ol is gl or not ol or ol[0]["pos"] != g["pos"]:
                    continue
                last = [o for o in ol if o["w"] == g["w"] - 1 and o["team"] == g["team"]]
                now_ = [o for o in ol if o["w"] == g["w"]]
                pr_ = [o["pts"] for o in ol if o["w"] < g["w"]]
                if last and not now_ and pr_ and sum(pr_) / len(pr_) >= 12:
                    starter_out = 1.0; break
            rows.append((avg, rec, xf, g["pts"], ("QB", "RB", "WR", "TE").index(g["pos"]), itr, mf, y, it or 0.0, SPR.get((y, g["w"], g["team"]), 0.0), starter_out))
    print(y, "rows so far", len(rows))

X = np.array(rows)
print("total player-weeks:", len(X))
act = X[:, 3]
mae = lambda p: float(np.abs(p - act).mean())
best = None
for a in np.arange(0, 1.01, 0.05):
    for b in np.arange(0, 1.01 - a, 0.05):
        c = 1 - a - b; e_ = mae(a * X[:, 0] + b * X[:, 1] + c * X[:, 2])
        if best is None or e_ < best[0]:
            best = (e_, a, b, c)
w = np.array(best[1:]); base = X[:, :3] @ w
# matchup strength + cap (per position caps applied like the live model)
capp = np.where(X[:, 4] <= 1, 0.15, 0.08)
bm = (mae(base), 0.0)
for s_ in np.arange(0, 2.01, 0.05):
    e_ = mae(base * np.clip(1 + s_ * X[:, 6], 1 - capp, 1 + capp))
    if e_ < bm[0] - 1e-9:
        bm = (e_, float(s_))
pred = base * np.clip(1 + bm[1] * X[:, 6], 1 - capp, 1 + capp)
veg = {}
for pos, i in (("QB", 0), ("RB", 1), ("WR", 2), ("TE", 3)):
    m = (X[:, 4] == i) & (X[:, 5] > 0)
    bb = min(((float(np.abs(pred[m] * X[m, 5] ** b_ - act[m]).mean()), float(b_)) for b_ in np.arange(0, 1.51, 0.05)))
    veg[pos] = {"b": round(bb[1], 2), "mae_without": round(float(np.abs(pred[m] - act[m]).mean()), 3), "mae_with": round(bb[0], 3), "n": int(m.sum())}
per_season = {}
for y in SEASONS:
    m = X[:, 7] == y
    per_season[y] = {"n": int(m.sum()), "mae_season_avg": round(float(np.abs(X[m, 0] - act[m]).mean()), 3), "mae_model": round(float(np.abs(pred[m] - act[m]).mean()), 3)}
# ---- test the blind-review rules, leave-one-season-out (fit on 3 seasons, score the 4th) ----
it_ = X[:, 8]; spr_ = X[:, 9]; posi = X[:, 4]; has = it_ > 0
def rule_test(name, feat_fn):
    gain = 0.0; tot_n = 0; coefs = []
    for y in SEASONS:
        tr = (X[:, 7] != y) & has; te = (X[:, 7] == y) & has
        F = feat_fn(tr); r = act[tr] - pred[tr]
        c = float((F * r).sum() / max(1e-9, (F * F).sum()))
        coefs.append(c)
        Ft = feat_fn(te)
        gain += float(np.abs(pred[te] - act[te]).sum() - np.abs(pred[te] + c * Ft - act[te]).sum()); tot_n += int(te.sum())
    return {"rule": name, "coef": round(float(np.mean(coefs)), 3), "pts_saved_per_100": round(100 * gain / max(1, tot_n), 2)}
tests = [
    rule_test("QB: x (implied total - 22.5)", lambda m: np.where(posi[m] == 0, it_[m] - 22.5, 0)),
    rule_test("non-QB: x (implied total - 22.5)", lambda m: np.where(posi[m] != 0, it_[m] - 22.5, 0)),
    rule_test("TE flat bias", lambda m: np.where(posi[m] == 3, 1.0, 0)),
    rule_test("TE low implied (<19)", lambda m: np.where((posi[m] == 3) & (it_[m] < 19), 1.0, 0)),
    rule_test("RB favored 2.5-6.5", lambda m: np.where((posi[m] == 1) & (spr_[m] >= 2.5) & (spr_[m] <= 6.5), 1.0, 0)),
    rule_test("RB x spread (favored +)", lambda m: np.where(posi[m] == 1, spr_[m], 0)),
    rule_test("backup WR (proj<7), starter WR out", lambda m: np.where((posi[m] == 2) & (X[m, 10] > 0) & (pred[m] < 7), 1.0, 0)),
    rule_test("backup RB (proj<7), starter RB out", lambda m: np.where((posi[m] == 1) & (X[m, 10] > 0) & (pred[m] < 7), 1.0, 0)),
    rule_test("backup TE (proj<7), starter TE out", lambda m: np.where((posi[m] == 3) & (X[m, 10] > 0) & (pred[m] < 7), 1.0, 0)),
    rule_test("any WR, starter WR out", lambda m: np.where((posi[m] == 2) & (X[m, 10] > 0), 1.0, 0)),
    rule_test("any RB, starter RB out", lambda m: np.where((posi[m] == 1) & (X[m, 10] > 0), 1.0, 0)),
]
for t_ in tests: print(t_)
out = {"rules": tests, "seasons": SEASONS, "n": len(X), "w_avg": round(best[1], 2), "w_recent": round(best[2], 2), "w_xfp": round(best[3], 2),
       "mae_season_avg": round(mae(X[:, 0]), 3), "mae_blend": round(best[0], 3), "match_s": round(bm[1], 2), "mae_with_matchup": round(bm[0], 3),
       "vegas": veg, "per_season": per_season}
print(json.dumps(out, indent=1))
(ROOT / "data" / "history_fit.json").write_text(json.dumps(out, indent=1))
