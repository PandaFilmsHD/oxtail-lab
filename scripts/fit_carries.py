"""Carries (rush attempts) model, trained on nflverse 2016 to now and tested the honest way: walk-forward.

For every season Y from 2019 on: train only on seasons before Y, predict every game of Y blind, score it.
Then roll forward. Compared each year against (a) his season average and (b) the simple fit (season avg + last 3).

What it uses (all known before kickoff):
  his recent carries (smoothed, recent games count more) and his share of the team's carries,
  how much his team runs, Vegas team points and spread (favorites run more to kill clock),
  wind at the game (nflverse game weather; live uses the forecast), the opponent's carries allowed, home/away.
Fit: Poisson regression per position (RB, QB). Spread: real outcome / projection ratios by projection size.
Writes into data/prop_dists.json:
  rush_att|RB, rush_att|QB  -> the simple fit (fallback)
  rush_att_v2               -> coefficients + every player's current numbers + team and defense numbers for live use
and data/carries_report.json (year-by-year results).
"""
import io, json, urllib.request, datetime
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
REL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{}.csv"
GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FIX = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
FEATS = ["log_c", "share", "log_team", "spread", "imp", "wind", "opp", "home", "log_last3"]
HL = 4.0


def fetch(url):
    for _ in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "oxtail-lab"}), timeout=120) as r:
                return r.read()
        except Exception as e:
            print("retry", type(e).__name__)
    raise RuntimeError(url)


def years():
    t = datetime.date.today()
    return list(range(2015, (t.year if t.month >= 9 else t.year - 1) + 1))


def build():
    S = pd.concat([pd.read_csv(io.BytesIO(fetch(REL.format(y))), low_memory=False) for y in years()], ignore_index=True)
    S = S[S.season_type == "REG"].copy()
    tcol = "team" if "team" in S.columns else "recent_team"
    S["team"] = S[tcol].map(lambda x: FIX.get(x, x)); S["opp_t"] = S["opponent_team"].map(lambda x: FIX.get(x, x))
    S["car"] = pd.to_numeric(S["carries"], errors="coerce").fillna(0)
    name = "player_display_name" if "player_display_name" in S.columns else "player_name"
    T = S.groupby(["season", "week", "team"])["car"].sum().rename("t_car").reset_index()
    S = S.merge(T, on=["season", "week", "team"])
    S["sh"] = S.car / S.t_car.clip(lower=1)
    G = pd.read_csv(io.BytesIO(fetch(GAMES)))
    G = G[G.game_type == "REG"].copy()
    for c in ("home_team", "away_team"):
        G[c] = G[c].map(lambda x: FIX.get(x, x))
    G["wind_"] = np.where(G.roof.isin(["dome", "closed"]), 0, pd.to_numeric(G.wind, errors="coerce").fillna(5))
    rows = []
    for side in ("home", "away"):
        sgn = 1 if side == "home" else -1
        rows.append(pd.DataFrame({"season": G.season, "week": G.week, "team": G[f"{side}_team"], "spread": sgn * G.spread_line,
                                  "imp": (G.total_line + sgn * G.spread_line) / 2, "wind": G.wind_, "home": 1 if side == "home" else 0}))
    S = S.merge(pd.concat(rows), on=["season", "week", "team"], how="left")
    S = S.sort_values(["player_id", "season", "week"]).reset_index(drop=True)
    a = 1 - 0.5 ** (1 / HL)
    g = S.groupby("player_id", sort=False)
    S["now_c"] = g.car.transform(lambda s: s.ewm(alpha=a).mean()); S["c_ew"] = g.now_c.shift(1)
    S["now_sh"] = g.sh.transform(lambda s: s.ewm(alpha=a).mean()); S["sh_ew"] = g.now_sh.shift(1)
    S["last3"] = g.car.transform(lambda s: s.rolling(3, min_periods=1).mean().shift(1))
    S["n_prev"] = g.cumcount()
    T = T.sort_values(["team", "season", "week"])
    T["now_t"] = T.groupby("team").t_car.transform(lambda s: s.ewm(alpha=1 - 0.5 ** (1 / 8)).mean()); T["t_ew"] = T.groupby("team").now_t.shift(1)
    S = S.merge(T[["season", "week", "team", "t_ew"]], on=["season", "week", "team"], how="left")
    A = T.rename(columns={"team": "opp_t"}).copy()          # carries a defense allows = the other team's carries
    A = S[["season", "week", "opp_t", "team"]].drop_duplicates().merge(T[["season", "week", "team", "t_car"]], on=["season", "week", "team"])
    A = A.groupby(["season", "week", "opp_t"])["t_car"].first().reset_index().sort_values(["opp_t", "season", "week"])
    A["now_a"] = A.groupby("opp_t").t_car.transform(lambda s: s.ewm(alpha=1 - 0.5 ** (1 / 8)).mean()); A["a_ew"] = A.groupby("opp_t").now_a.shift(1)
    lg = T.t_car.mean()
    S = S.merge(A[["season", "week", "opp_t", "a_ew"]], on=["season", "week", "opp_t"], how="left")
    S["log_c"] = np.log(S.c_ew.clip(lower=0) + 0.5); S["share"] = S.sh_ew; S["log_team"] = np.log(S.t_ew.fillna(lg) / lg)
    S["spread"] = S.spread.fillna(0) / 7; S["imp"] = (S.imp.fillna(22) - 22) / 7; S["wind"] = S.wind.fillna(5) / 10
    S["opp"] = np.log(S.a_ew.fillna(lg) / lg); S["log_last3"] = np.log(S.last3.clip(lower=0) + 0.5)
    return S, T, A, lg, name


def simple(tr, te):
    """The old way: (1-w) season average + w last 3, slope + intercept."""
    def m(df, w):
        return (1 - w) * df.season_avg + w * df.last3
    best = None
    for w in np.linspace(0, 1, 11):
        A_ = np.vstack([m(tr, w), np.ones(len(tr))]).T
        sl, ic = np.linalg.lstsq(A_, tr.car, rcond=None)[0]
        e = float(np.mean(np.abs(sl * m(tr, w) + ic - tr.car)))
        if best is None or e < best[0]:
            best = (e, w, sl, ic)
    _, w, sl, ic = best
    return sl * m(te, w) + ic, (w, sl, ic)


def main():
    S, T, A, lg, name = build()
    S["season_avg"] = S.groupby(["player_id", "season"]).car.transform(lambda s: s.expanding().mean().shift(1))
    dist = json.loads((D / "prop_dists.json").read_text())
    report, live = {}, {"feats": FEATS, "pos": {}, "lg_team": float(lg), "half_life": HL}
    for pos in ("RB", "QB"):
        d = S[(S.position == pos) & (S.n_prev >= 3)].dropna(subset=["c_ew", "share", "season_avg", "last3"])
        if pos == "RB":
            d = d[d.c_ew >= 3]
        yrs, rows_ = sorted(d.season.unique()), []
        for Y in [y for y in yrs if y >= 2019]:
            tr, te = d[d.season < Y], d[d.season == Y]
            if len(te) < 200:
                continue
            mu, sd = tr[FEATS].mean(), tr[FEATS].std().replace(0, 1)
            mdl = PoissonRegressor(alpha=1e-4, max_iter=3000).fit(((tr[FEATS] - mu) / sd).values, tr.car)
            pm = mdl.predict(((te[FEATS] - mu) / sd).values)
            ps, _ = simple(tr, te)
            rows_.append({"season": int(Y), "n": int(len(te)), "mae_model": round(float(np.mean(np.abs(pm - te.car))), 3),
                          "mae_simple": round(float(np.mean(np.abs(ps - te.car))), 3), "mae_season_avg": round(float(np.mean(np.abs(te.season_avg - te.car))), 3)})
            print(pos, rows_[-1])
        tot = lambda k: sum(r[k] * r["n"] for r in rows_) / sum(r["n"] for r in rows_)
        report[pos] = {"by_season": rows_, "all_years": {k: round(tot(k), 3) for k in ("mae_model", "mae_simple", "mae_season_avg")}}
        mu, sd = d[FEATS].mean(), d[FEATS].std().replace(0, 1)
        mdl = PoissonRegressor(alpha=1e-4, max_iter=3000).fit(((d[FEATS] - mu) / sd).values, d.car)
        proj = mdl.predict(((d[FEATS] - mu) / sd).values)
        ratio = d.car.values / np.maximum(proj, 0.5)
        cuts = np.quantile(proj, [0.25, 0.5, 0.75]); rng = np.random.default_rng(3); buckets = []
        for lo, hi in zip([-1, *cuts], [*cuts, proj.max() + 1]):
            r_ = ratio[(proj > lo) & (proj <= hi)]
            buckets.append({"hi": float(hi), "ratios": [round(float(x), 4) for x in np.sort(rng.choice(r_, size=min(400, len(r_)), replace=False))]})
        live["pos"][pos] = {"mean": [float(mu[f]) for f in FEATS], "scale": [float(sd[f]) for f in FEATS], "coef": [float(c) for c in mdl.coef_], "icpt": float(mdl.intercept_),
                            "weights": {f: round(float(c), 3) for f, c in zip(FEATS, mdl.coef_)}}
        _, (w, sl, ic) = simple(d, d)
        dist[f"rush_att|{pos}"] = {"w_recent": float(w), "slope": float(sl), "icpt": float(ic), "mae": float(np.mean(np.abs(proj - d.car))), "n": int(len(d)), "buckets": buckets}
        print(pos, "all years", report[pos]["all_years"], "weights", live["pos"][pos]["weights"])
    cur = int(S.season.max())
    last = S[S.season == cur].sort_values(["player_id", "week"]).groupby("player_id").tail(1)
    live["players"] = {r[name]: {"team": r.team, "pos": r.position, "c": round(float(r.now_c), 3), "sh": round(float(r.now_sh), 4),
                                 "last3": round(float(S[(S.player_id == r.player_id) & (S.season == cur)].car.tail(3).mean()), 2)}
                       for _, r in last.iterrows() if r.position in ("RB", "QB")}
    live["team"] = {t: round(float(v), 2) for t, v in T.sort_values(["season", "week"]).groupby("team").now_t.last().items()}
    live["allow"] = {t: round(float(v), 2) for t, v in A.sort_values(["season", "week"]).groupby("opp_t").now_a.last().items()}
    dist["rush_att_v2"] = live
    (D / "prop_dists.json").write_text(json.dumps(dist))
    (D / "carries_report.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
