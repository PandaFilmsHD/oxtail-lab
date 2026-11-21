"""Train and backtest the D/ST and kicker models on nflverse history, then save what analyze.py needs.

D/ST: each piece of defense scoring (sacks, picks, fumble recoveries, defense/return TDs, safeties+blocks,
points allowed) gets its own model from pregame info only: the opponent's Vegas expected score, the
opponent offense's sack/pick/fumble habits, the defense's own pressure and takeaway habits, home/road,
dome, wind, cold, and whether the opponent is starting a backup QB. The pieces are then scored with this
league's own D/ST scoring (learned from this season's real ESPN D/ST scores).

K: field goal tries and extra points from the team's expected score, spread, how often its drives stall
into field goals, the opponent's defense, dome, wind and cold; scored with the league's kicker rules.

Backtest: train on earlier seasons, test on the next one (2019-2025), compared with the old one-line
formulas. Writes data/dk_model.json and data/dk_report.json.

python scripts/fit_dk.py                 downloads nflverse data (works in GitHub Actions)
python scripts/fit_dk.py --local DIR     uses already-downloaded files in DIR
"""
import io, json, math, sys, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor, Ridge

ROOT = Path(__file__).resolve().parent.parent
LID = "787523197"
REL = "https://github.com/nflverse/nflverse-data/releases/download/"
GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FIX = {"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR", "WSH": "WAS"}
SEASONS = list(range(2012, 2027))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "oxtail-lab"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def load(local=None):
    teams = []
    for y in SEASONS:
        try:
            if local:
                f = Path(local) / f"stats_team_week_{y}.csv"
                if not f.exists() or f.stat().st_size < 5000:
                    continue
                df = pd.read_csv(f)
            else:
                df = pd.read_csv(io.BytesIO(fetch(f"{REL}stats_team/stats_team_week_{y}.csv")))
            teams.append(df)
        except Exception as ex:
            print("no team stats for", y, ex)
    T = pd.concat(teams, ignore_index=True)
    G = pd.read_csv(io.BytesIO(fetch(GAMES)) if not local or not (Path(local) / "games.csv").exists() else Path(local) / "games.csv")
    return T, G


def tm(x):
    return FIX.get(x, x)


def build(T, G):
    T = T[T["season_type"] == "REG"].copy()
    T["team"] = T["team"].map(tm); T["opponent_team"] = T["opponent_team"].map(tm)
    G = G[G["game_type"] == "REG"].copy()
    for c in ("home_team", "away_team"):
        G[c] = G[c].map(tm)
    rows = []
    for _, g in G.iterrows():
        if pd.isna(g.get("spread_line")) or pd.isna(g.get("total_line")):
            continue
        sp, tot = float(g["spread_line"]), float(g["total_line"])   # spread_line: home team's expected margin
        for side, opp_side, sgn in (("home", "away", 1), ("away", "home", -1)):
            rows.append({"season": g["season"], "week": g["week"], "team": g[f"{side}_team"], "opp": g[f"{opp_side}_team"],
                         "home": 1 if side == "home" else 0, "it": (tot + sgn * sp) / 2, "opp_it": (tot - sgn * sp) / 2, "spread": sgn * sp, "total": tot,
                         "dome": 1 if str(g.get("roof")) in ("dome", "closed") else 0,
                         "wind": 0.0 if str(g.get("roof")) in ("dome", "closed") else float(g["wind"]) if not pd.isna(g.get("wind")) else 8.0,
                         "cold": 1 if (not pd.isna(g.get("temp")) and float(g["temp"]) <= 32 and str(g.get("roof")) not in ("dome", "closed")) else 0,
                         "opp_qb": g.get(f"{opp_side}_qb_id"),
                         "pa": g[f"{opp_side}_score"], "pf": g[f"{side}_score"]})
    D = pd.DataFrame(rows)
    keep = ["season", "week", "team", "attempts", "sacks_suffered", "passing_interceptions", "fumbles_lost_total", "carries",
            "def_sacks", "def_interceptions", "fumble_recovery_opp", "def_tds", "special_teams_tds", "fumble_recovery_tds",
            "def_safeties", "def_punt_blocks", "def_fg_blocks", "def_pat_blocks",
            "fg_made", "fg_att", "fg_missed", "fg_made_0_19", "fg_made_20_29", "fg_made_30_39", "fg_made_40_49", "fg_made_50_59", "fg_made_60_",
            "pat_made", "pat_att", "pat_missed", "passing_tds", "rushing_tds"]
    keep = [c for c in keep if c in T.columns]
    S = T[keep].copy()
    for c in keep[3:]:
        S[c] = pd.to_numeric(S[c], errors="coerce").fillna(0)
    D = D.merge(S, on=["season", "week", "team"], how="left")
    O = S.add_prefix("o_").rename(columns={"o_season": "season", "o_week": "week", "o_team": "opp"})
    D = D.merge(O, on=["season", "week", "opp"], how="left")
    D = D.dropna(subset=["def_sacks", "o_attempts"]).sort_values(["season", "week"]).reset_index(drop=True)
    # pieces of D/ST scoring
    D["y_sack"] = D["def_sacks"]; D["y_int"] = D["def_interceptions"]; D["y_fr"] = D["fumble_recovery_opp"]
    D["y_td"] = D["def_tds"] + D["special_teams_tds"]
    D["y_sfb"] = D["def_safeties"] + D["def_punt_blocks"] + D["def_fg_blocks"] + D.get("def_pat_blocks", 0)
    # backup QB: opponent's starter isn't the QB who started most of their games so far this season
    D["opp_backup"] = 0
    for (s, o), grp in D.groupby(["season", "opp"]):
        seen = {}
        for i in grp.sort_values("week").index:
            q = D.at[i, "opp_qb"]
            if seen and not pd.isna(q):
                top = max(seen, key=seen.get)
                D.at[i, "opp_backup"] = 1 if (q != top and seen[top] >= 3) else 0
            if not pd.isna(q):
                seen[q] = seen.get(q, 0) + 1
    return D


def roll(D):
    """Pregame habits: each team's rates from its earlier games, carried over from last season and shrunk to the league."""
    D = D.copy()
    D["o_drop"] = D["o_attempts"] + D["o_sacks_suffered"]
    D["o_plays"] = D["o_drop"] + D["o_carries"]
    rates = {
        # opponent offense habits (what this defense faces)
        "oo_sack": ("opp", "o_sacks_suffered", "o_drop"), "oo_int": ("opp", "o_passing_interceptions", "o_attempts"), "oo_fum": ("opp", "o_fumbles_lost_total", "o_plays"),
        # this defense's habits
        "dd_sack": ("team", "def_sacks", "o_drop"), "dd_int": ("team", "def_interceptions", "o_attempts"), "dd_fr": ("team", "fumble_recovery_opp", "o_plays"),
        # kicking: how often this offense ends up kicking FGs, and how often this team's opponents do
        "kk_fga": ("team", "fg_att", None), "kk_stall": ("team", "fg_att", "drv_end"), "kk_acc": ("team", "fg_made", "fg_att"),
    }
    D["drv_end"] = D["fg_att"] + D["passing_tds"] + D["rushing_tds"]
    D["one"] = 1.0
    # opponent-defense FG allowed: FGs the opponent's opponents attempted (i.e., opponent defense allows) -> use o_ columns of the other row
    out = {k: np.full(len(D), np.nan) for k in rates}
    out["od_fga"] = np.full(len(D), np.nan)
    league = {}
    for k, (who, num, den) in rates.items():
        n_ = D[num].sum(); d_ = D[den].sum() if den else len(D)
        league[k] = n_ / max(d_, 1)
    league["od_fga"] = league["kk_fga"]
    PRIOR = 6.0   # games of league-average to shrink toward
    for k, (who, num, den) in rates.items():
        key = "opp" if who == "opp" else "team"
        hist = {}
        for i in D.index:
            t = D.at[i, key]; s = D.at[i, "season"]
            h = hist.get(t)
            if h is None or h["s"] != s:
                carry = (h["n"] / max(h["d"], 1e-9), ) if h else None
                h = {"s": s, "n": 0.0, "d": 0.0, "prev": carry[0] if carry else league[k]}
                hist[t] = h
            # rate = (this season so far + prior made of half last-season rate + half league)
            p0 = 0.5 * h["prev"] + 0.5 * league[k]
            dper = (D[den].mean() if den else 1.0)
            out[k][i] = (h["n"] + p0 * PRIOR * dper) / (h["d"] + PRIOR * dper)
            h["n"] += D.at[i, num]; h["d"] += (D.at[i, den] if den else 1.0)
    # opponent defense: FGs allowed per game (team's opponents' fg_att), from the opponent's perspective
    hist = {}
    for i in D.index:
        o = D.at[i, "opp"]; s = D.at[i, "season"]
        h = hist.get(o)
        if h is None or h["s"] != s:
            h = {"s": s, "n": 0.0, "d": 0.0, "prev": (h["n"] / max(h["d"], 1)) if h else league["od_fga"]}
            hist[o] = h
        p0 = 0.5 * h["prev"] + 0.5 * league["od_fga"]
        out["od_fga"][i] = (h["n"] + p0 * PRIOR) / (h["d"] + PRIOR)
        h["n"] += D.at[i, "fg_att"]; h["d"] += 1
    for k in out:
        D[k] = out[k]
    return D, league


DST_X = ["opp_it", "spread", "home", "dome", "wind", "cold", "opp_backup", "oo_sack", "oo_int", "oo_fum", "dd_sack", "dd_int", "dd_fr"]
K_X = ["it", "spread", "home", "dome", "wind", "cold", "kk_fga", "kk_stall", "kk_acc", "od_fga"]
PA_TIERS = [(0, 0, "89"), (1, 6, "90"), (7, 13, "91"), (14, 17, "92"), (18, 21, "123"), (22, 27, "124"), (28, 34, "125"), (35, 45, "126"), (46, 99, "127")]


def k_points(df, sc):
    S = lambda k, d: float(sc.get(k, d))
    return ((df["fg_made_0_19"] + df["fg_made_20_29"] + df["fg_made_30_39"]) * S("80", 3) + df["fg_made_40_49"] * S("77", 4)
            + df["fg_made_50_59"] * S("198", 5) + df.get("fg_made_60_", 0) * S("201", 5)
            + df["fg_missed"] * S("85", -1) + df["pat_made"] * S("86", 1) + df["pat_missed"] * S("88", -1))


def std_dst(df):
    pa = df["pa"]
    tier = np.select([pa == 0, pa <= 6, pa <= 13, pa <= 17, pa <= 27, pa <= 34, pa <= 45], [5, 4, 3, 1, 0, -1, -3], -5)
    return df["y_sack"] + 2 * df["y_int"] + 2 * df["y_fr"] + 6 * df["y_td"] + 2 * df["y_sfb"] + tier


def fit_parts(tr):
    m = {}
    Xs = tr[DST_X].values
    mu, sd = Xs.mean(0), Xs.std(0) + 1e-9
    Z = (Xs - mu) / sd
    for y in ("y_sack", "y_int", "y_fr", "y_td", "y_sfb"):
        r = PoissonRegressor(alpha=0.5, max_iter=500).fit(Z, tr[y].values)
        m[y] = r
    pa = Ridge(alpha=5.0).fit(Z, tr["pa"].values)
    resid = tr["pa"].values - pa.predict(Z)
    return {"mu": mu, "sd": sd, "m": m, "pa": pa, "pa_sd": float(resid.std())}


def pa_tier_ev(mean, sd, pts):
    """Expected points from the points-allowed tiers when points allowed ~ Normal(mean, sd)."""
    from math import erf, sqrt
    cdf = lambda x: 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    ev = 0.0
    for lo, hi, k in PA_TIERS:
        p = cdf(hi + 0.5) - cdf(lo - 0.5 if lo > 0 else -99)
        ev += p * pts.get(k, 0.0)
    return ev


def pred_parts(M, df):
    Z = (df[DST_X].values - M["mu"]) / M["sd"]
    out = {y: M["m"][y].predict(Z) for y in M["m"]}
    out["pa"] = M["pa"].predict(Z)
    return out


def league_weights(D):
    """This league's D/ST scoring, learned from this season's real ESPN D/ST scores vs the box-score pieces."""
    try:
        Mo = json.loads((ROOT / "data" / LID / "model.json").read_text())
    except Exception:
        return None
    cur = int(D["season"].max())
    name2team = {}
    for p in Mo["players"]:
        if p["pos"] == "D":
            name2team[p["n"]] = tm(p["nfl"])
    rows = []
    for p in Mo["players"]:
        if p["pos"] != "D":
            continue
        for w, v in (p.get("wk") or {}).items():
            r = D[(D["season"] == cur) & (D["week"] == int(w)) & (D["team"] == tm(p["nfl"]))]
            if len(r) == 1:
                rr = r.iloc[0]
                rows.append({"pts": float(v), "sack": rr["y_sack"], "int": rr["y_int"], "fr": rr["y_fr"], "td": rr["y_td"], "sfb": rr["y_sfb"], "pa": rr["pa"]})
    if len(rows) < 60:
        return None
    L = pd.DataFrame(rows)
    X = np.column_stack([L["sack"], L["int"], L["fr"], L["td"], L["sfb"]] + [((L["pa"] >= lo) & (L["pa"] <= hi)).astype(float) for lo, hi, _ in PA_TIERS])
    r = Ridge(alpha=0.3, fit_intercept=False).fit(X, L["pts"].values)
    c = r.coef_
    w = {"sack": c[0], "int": c[1], "fr": c[2], "td": c[3], "sfb": c[4], "pa": {k: c[5 + i] for i, (_, _, k) in enumerate(PA_TIERS)}}
    fit_err = float(np.mean(np.abs(L["pts"].values - r.predict(X))))
    return w, len(L), fit_err


def dst_points(parts, w, pa_sd):
    ev = w["sack"] * parts["y_sack"] + w["int"] * parts["y_int"] + w["fr"] * parts["y_fr"] + w["td"] * parts["y_td"] + w["sfb"] * parts["y_sfb"]
    return ev + np.array([pa_tier_ev(m, pa_sd, w["pa"]) for m in parts["pa"]])


def spearman_by_week(df, pred, y):
    vals = []
    for (s, wk), g in df.assign(_p=pred, _y=y).groupby(["season", "week"]):
        if len(g) >= 8:
            vals.append(g["_p"].rank().corr(g["_y"].rank()))
    return float(np.nanmean(vals))


def main():
    local = sys.argv[sys.argv.index("--local") + 1] if "--local" in sys.argv else None
    T, G = load(local)
    D, league = roll(build(T, G))
    print("rows", len(D), "seasons", sorted(D["season"].unique()))
    Mo = json.loads((ROOT / "data" / LID / "model.json").read_text())
    sc = (Mo.get("settings") or {}).get("scoring") or {}
    D["k_pts"] = k_points(D, sc)
    D["dst_std"] = std_dst(D)
    lw = league_weights(D)
    STD_W = {"sack": 1, "int": 2, "fr": 2, "td": 6, "sfb": 2, "pa": {"89": 5, "90": 4, "91": 3, "92": 1, "123": 0, "124": 0, "125": -1, "126": -3, "127": -5}}
    W = lw[0] if lw else STD_W
    print("league D/ST weights:", {k: (round(v, 2) if not isinstance(v, dict) else {a: round(b, 2) for a, b in v.items()}) for k, v in W.items()}, "from", lw[1] if lw else 0, "games, fit error", round(lw[2], 2) if lw else None)
    D["dst_lg"] = (W["sack"] * D["y_sack"] + W["int"] * D["y_int"] + W["fr"] * D["y_fr"] + W["td"] * D["y_td"] + W["sfb"] * D["y_sfb"]
                   + sum(W["pa"].get(k, 0) * ((D["pa"] >= lo) & (D["pa"] <= hi)) for lo, hi, k in PA_TIERS))
    seasons = sorted(D["season"].unique())
    test_years = [s for s in seasons if s >= max(seasons[0] + 3, 2019)]
    rep = {"dst": [], "k": []}
    allp = {"dst_new": [], "dst_old": [], "k_new": [], "k_old": [], "dst_y": [], "k_y": [], "idx": []}
    for ty in test_years:
        tr, te = D[D["season"] < ty], D[D["season"] == ty]
        if len(te) < 50 or len(tr) < 500:
            continue
        M = fit_parts(tr)
        pn = dst_points(pred_parts(M, te), W, M["pa_sd"])
        # old app formula, scaled the same way the app scales it (to the league's typical D/ST game)
        scale = tr["dst_lg"].median() / 5.0 if tr["dst_lg"].median() > 0 else 1.0
        po = np.maximum(0, scale * (5.3 - 0.6 * (te["opp_it"].values - 22)))
        Xk = tr[K_X].values; mu, sd = Xk.mean(0), Xk.std(0) + 1e-9
        kr = Ridge(alpha=10.0).fit((Xk - mu) / sd, tr["k_pts"].values)
        kn = kr.predict((te[K_X].values - mu) / sd)
        kscale = tr["k_pts"].median() / 7.9 if tr["k_pts"].median() > 0 else 1.0
        ko = np.maximum(0, kscale * (3.6 + 0.07 * 7.9 + 0.17 * te["it"].values))
        y_d, y_k = te["dst_lg"].values, te["k_pts"].values
        r_d = {"season": int(ty), "games": len(te), "mae_new": float(np.mean(np.abs(pn - y_d))), "mae_old": float(np.mean(np.abs(po - y_d))),
               "rank_new": spearman_by_week(te, pn, y_d), "rank_old": spearman_by_week(te, po, y_d)}
        r_k = {"season": int(ty), "games": len(te), "mae_new": float(np.mean(np.abs(kn - y_k))), "mae_old": float(np.mean(np.abs(ko - y_k))),
               "rank_new": spearman_by_week(te, kn, y_k), "rank_old": spearman_by_week(te, ko, y_k)}
        rep["dst"].append(r_d); rep["k"].append(r_k)
        print("test", ty, "D/ST mae new/old", round(r_d["mae_new"], 3), round(r_d["mae_old"], 3), "rank", round(r_d["rank_new"], 3), round(r_d["rank_old"], 3),
              "| K mae", round(r_k["mae_new"], 3), round(r_k["mae_old"], 3), "rank", round(r_k["rank_new"], 3), round(r_k["rank_old"], 3))
    # final fit on everything, save for analyze.py
    M = fit_parts(D)
    Xk = D[K_X].values; mu, sd = Xk.mean(0), Xk.std(0) + 1e-9
    kr = Ridge(alpha=10.0).fit((Xk - mu) / sd, D["k_pts"].values)
    cur = int(D["season"].max())
    # each team's current habits (rates going into its next game)
    last = {}
    Dc = D[D["season"] == cur]
    for t in sorted(set(Dc["team"]) | set(Dc["opp"])):
        last[t] = {}
    snap, _ = roll(pd.concat([D, D[D["season"] == cur].tail(0)]))
    # rates after the latest game: recompute by appending a fake next row per team is overkill; use the last row's rate updated with that game
    for t in last:
        r_t = D[(D["season"] == cur) & (D["team"] == t)].sort_values("week")
        r_o = D[(D["season"] == cur) & (D["opp"] == t)].sort_values("week")
        if len(r_t):
            n = len(r_t)
            last[t]["dd_sack"] = float((r_t["def_sacks"].sum() + 6 * r_t["dd_sack"].iloc[0] * r_t["o_drop"].mean()) / (r_t["o_drop"].sum() + 6 * r_t["o_drop"].mean()))
            last[t]["dd_int"] = float((r_t["def_interceptions"].sum() + 6 * r_t["dd_int"].iloc[0] * r_t["o_attempts"].mean()) / (r_t["o_attempts"].sum() + 6 * r_t["o_attempts"].mean()))
            last[t]["dd_fr"] = float((r_t["fumble_recovery_opp"].sum() + 6 * r_t["dd_fr"].iloc[0] * r_t["o_plays"].mean()) / (r_t["o_plays"].sum() + 6 * r_t["o_plays"].mean()))
            last[t]["kk_fga"] = float((r_t["fg_att"].sum() + 6 * r_t["kk_fga"].iloc[0]) / (n + 6))
            last[t]["kk_stall"] = float((r_t["fg_att"].sum() + 6 * r_t["kk_stall"].iloc[0] * max(r_t["drv_end"].mean(), 0.1)) / (r_t["drv_end"].sum() + 6 * max(r_t["drv_end"].mean(), 0.1)))
            last[t]["kk_acc"] = float((r_t["fg_made"].sum() + 6 * r_t["kk_acc"].iloc[0] * max(r_t["fg_att"].mean(), 0.1)) / (r_t["fg_att"].sum() + 6 * max(r_t["fg_att"].mean(), 0.1)))
            last[t]["od_fga"] = float((r_o["fg_att"].sum() + 6 * r_t["od_fga"].iloc[0]) / (len(r_o) + 6)) if len(r_o) else league["od_fga"]
        if len(r_o):   # this team as an offense, seen from defenses that faced it
            last[t]["oo_sack"] = float((r_o["o_sacks_suffered"].sum() + 6 * r_o["oo_sack"].iloc[0] * r_o["o_drop"].mean()) / (r_o["o_drop"].sum() + 6 * r_o["o_drop"].mean()))
            last[t]["oo_int"] = float((r_o["o_passing_interceptions"].sum() + 6 * r_o["oo_int"].iloc[0] * r_o["o_attempts"].mean()) / (r_o["o_attempts"].sum() + 6 * r_o["o_attempts"].mean()))
            last[t]["oo_fum"] = float((r_o["o_fumbles_lost_total"].sum() + 6 * r_o["oo_fum"].iloc[0] * r_o["o_plays"].mean()) / (r_o["o_plays"].sum() + 6 * r_o["o_plays"].mean()))
    out = {
        "season": cur, "trained_rows": int(len(D)),
        "dst": {"x": DST_X, "mu": M["mu"].tolist(), "sd": M["sd"].tolist(), "pa_sd": M["pa_sd"],
                "parts": {y: {"coef": M["m"][y].coef_.tolist(), "icpt": float(M["m"][y].intercept_)} for y in M["m"]},
                "pa": {"coef": M["pa"].coef_.tolist(), "icpt": float(M["pa"].intercept_)},
                "w": W, "tiers": PA_TIERS},
        "k": {"x": K_X, "mu": mu.tolist(), "sd": sd.tolist(), "coef": kr.coef_.tolist(), "icpt": float(kr.intercept_)},
        "league_rates": league, "teams": last,
    }
    (ROOT / "data" / "dk_model.json").write_text(json.dumps(out, separators=(",", ":"), default=float))
    summ = {k: {"mae_new": float(np.mean([r["mae_new"] for r in v])), "mae_old": float(np.mean([r["mae_old"] for r in v])),
                "rank_new": float(np.mean([r["rank_new"] for r in v])), "rank_old": float(np.mean([r["rank_old"] for r in v]))} for k, v in rep.items() if v}
    rep["summary"] = summ
    (ROOT / "data" / "dk_report.json").write_text(json.dumps(rep, indent=1, default=float))
    print("SUMMARY", json.dumps(summ))
    # what drives each piece (standardized coefficients), for the report
    for y in M["m"]:
        print("D/ST", y, {x: round(c, 3) for x, c in zip(DST_X, M["m"][y].coef_)})
    print("PA", {x: round(c, 2) for x, c in zip(DST_X, M["pa"].coef_)}, "sd", round(M["pa_sd"], 2))
    print("K", {x: round(c, 3) for x, c in zip(K_X, kr.coef_)})


if __name__ == "__main__":
    main()
