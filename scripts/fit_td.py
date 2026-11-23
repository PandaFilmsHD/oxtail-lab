"""Anytime touchdown model for RB / WR / TE, trained on nflverse play-by-play.

What predicts a touchdown (in order of how much it matters, per the fit):
  red zone work (carries inside the 20, inside the 5, targets inside the 20, end zone targets) as a share of his team's,
  how many points Vegas expects his team to score, how often his team gets into the red zone,
  how many TDs the opponent gives up to his position, his overall carry and target share, and his own TD rate.
Every feature is pregame only (built from games before the one being predicted), smoothed so recent games count more.

Fit: a Poisson model of rushing + receiving TDs per position. P(anytime TD) = 1 - exp(-expected TDs).
Backtest: trained on 2017-2022, tested on 2023 to now, against a baseline that only knows his past TD rate and team points.
Writes data/td_model.json (coefficients, every active player's current features, defense factors) and data/td_report.json.
"""
import io, json, math, sys, urllib.request, datetime
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
REL = "https://github.com/nflverse/nflverse-data/releases/download/"
GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FIX = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
HL = 6.0                       # half-life in games for the smoothing
POS = ("RB", "WR", "TE")
FEATS = ["imp", "team_rz", "rz_car", "gl_car", "rz_tgt", "ez_tgt", "car_sh", "tgt_sh", "td_rate", "opp"]
TRAIN_END, FIRST = 2022, 2017


def fetch(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "oxtail-lab"}), timeout=180) as r:
                return r.read()
        except Exception as e:
            print("  retry", url.rsplit("/", 1)[-1], type(e).__name__)
    raise RuntimeError("download failed " + url)


def years():
    now = datetime.date.today()
    last = now.year if now.month >= 9 else now.year - 1
    return list(range(FIRST - 1, last + 1))


PBP_COLS = ["season", "week", "season_type", "game_id", "posteam", "defteam", "play_type", "yardline_100", "rusher_player_id",
            "receiver_player_id", "air_yards", "two_point_attempt"]


def load():
    P, S = [], []
    for y in years():
        try:
            df = pd.read_csv(io.BytesIO(fetch(f"{REL}pbp/play_by_play_{y}.csv.gz")), compression="gzip", usecols=PBP_COLS, low_memory=False)
            P.append(df[(df.season_type == "REG") & df.play_type.isin(["run", "pass"]) & (df.two_point_attempt.fillna(0) == 0)])
            print("pbp", y, len(P[-1]))
        except Exception as e:
            print("pbp", y, "skipped", e)
        try:
            s = pd.read_csv(io.BytesIO(fetch(f"{REL}stats_player/stats_player_week_{y}.csv")), low_memory=False)
            S.append(s)
            print("stats", y, len(s))
        except Exception as e:
            print("stats", y, "skipped", e)
    P = pd.concat(P, ignore_index=True)
    S = pd.concat(S, ignore_index=True)
    G = pd.read_csv(io.BytesIO(fetch(GAMES)))
    return P, S, G


def tm(x):
    return FIX.get(x, x)


def build(P, S, G):
    P = P.copy()
    for c in ("posteam", "defteam"):
        P[c] = P[c].map(tm)
    run = P.play_type == "run"
    pas = (P.play_type == "pass") & P.receiver_player_id.notna()
    P["rz"] = P.yardline_100 <= 20
    P["gl"] = P.yardline_100 <= 5
    P["ez"] = pas & (P.air_yards >= P.yardline_100)
    k = ["season", "week", "posteam"]
    team = pd.DataFrame({
        "t_car": run.groupby([P[c] for c in k]).sum(),
        "t_tgt": pas.groupby([P[c] for c in k]).sum(),
        "t_rz_car": (run & P.rz).groupby([P[c] for c in k]).sum(),
        "t_gl_car": (run & P.gl).groupby([P[c] for c in k]).sum(),
        "t_rz_tgt": (pas & P.rz).groupby([P[c] for c in k]).sum(),
        "t_ez_tgt": P.ez.groupby([P[c] for c in k]).sum(),
        "t_rz_plays": P.rz.groupby([P[c] for c in k]).sum(),
    }).reset_index().rename(columns={"posteam": "team"})
    R = P[run].groupby(["season", "week", "posteam", "rusher_player_id"]).agg(rz_car=("rz", "sum"), gl_car=("gl", "sum")).reset_index()
    R = R.rename(columns={"posteam": "team", "rusher_player_id": "player_id"})
    Q = P[pas].groupby(["season", "week", "posteam", "receiver_player_id"]).agg(rz_tgt=("rz", "sum"), ez_tgt=("ez", "sum")).reset_index()
    Q = Q.rename(columns={"posteam": "team", "receiver_player_id": "player_id"})

    S = S[S.season_type == "REG"].copy()
    tcol = "team" if "team" in S.columns else "recent_team"
    S["team"] = S[tcol].map(tm)
    S["opp"] = S["opponent_team"].map(tm)
    name = "player_display_name" if "player_display_name" in S.columns else "player_name"
    S = S[S.position.isin(POS)]
    for c in ("carries", "targets", "rushing_tds", "receiving_tds"):
        S[c] = pd.to_numeric(S.get(c), errors="coerce").fillna(0)
    S["tds"] = S.rushing_tds + S.receiving_tds
    X = S[["season", "week", "player_id", name, "position", "team", "opp", "carries", "targets", "tds"]].rename(columns={name: "name", "position": "pos"})
    X = X.merge(R, on=["season", "week", "team", "player_id"], how="left").merge(Q, on=["season", "week", "team", "player_id"], how="left")
    X = X.merge(team, on=["season", "week", "team"], how="left")
    for c in ("rz_car", "gl_car", "rz_tgt", "ez_tgt"):
        X[c] = X[c].fillna(0)
    sh = lambda a, b: np.where(X[b] > 0, X[a] / X[b].where(X[b] > 0, 1), 0.0)
    X["s_rz_car"] = sh("rz_car", "t_rz_car"); X["s_gl_car"] = sh("gl_car", "t_gl_car")
    X["s_rz_tgt"] = sh("rz_tgt", "t_rz_tgt"); X["s_ez_tgt"] = sh("ez_tgt", "t_ez_tgt")
    X["s_car"] = sh("carries", "t_car"); X["s_tgt"] = sh("targets", "t_tgt")

    # Vegas implied points for his team that game
    G = G[G.game_type == "REG"].copy()
    G["home_team"] = G.home_team.map(tm); G["away_team"] = G.away_team.map(tm)
    imp = pd.concat([
        pd.DataFrame({"season": G.season, "week": G.week, "team": G.home_team, "imp": (G.total_line + G.spread_line) / 2}),
        pd.DataFrame({"season": G.season, "week": G.week, "team": G.away_team, "imp": (G.total_line - G.spread_line) / 2})])
    X = X.merge(imp, on=["season", "week", "team"], how="left")
    X["imp"] = X["imp"].fillna(22.0)

    X = X.sort_values(["player_id", "season", "week"]).reset_index(drop=True)
    a = 1 - 0.5 ** (1 / HL)
    g = X.groupby("player_id", sort=False)
    for src, dst in (("s_rz_car", "rz_car"), ("s_gl_car", "gl_car"), ("s_rz_tgt", "rz_tgt"), ("s_ez_tgt", "ez_tgt"), ("s_car", "car_sh"), ("s_tgt", "tgt_sh"), ("tds", "td_rate")):
        X["now_" + dst] = g[src].transform(lambda s: s.ewm(alpha=a, adjust=True).mean())            # after this game (for next week)
        X[dst] = g["now_" + dst].shift(1)                                                           # before this game (for training)
    X["n_prev"] = g.cumcount()
    # team red zone trips, smoothed, pregame
    T = team.sort_values(["team", "season", "week"]).copy()
    T["now_team_rz"] = T.groupby("team")["t_rz_plays"].transform(lambda s: s.ewm(alpha=1 - 0.5 ** (1 / 8), adjust=True).mean())
    T["team_rz"] = T.groupby("team")["now_team_rz"].shift(1)
    X = X.merge(T[["season", "week", "team", "team_rz"]], on=["season", "week", "team"], how="left")
    # defense: TDs allowed to each position per game, smoothed, vs league average, pregame
    A = X.groupby(["season", "week", "opp", "pos"])["tds"].sum().reset_index().sort_values(["opp", "pos", "season", "week"])
    A["now_allow"] = A.groupby(["opp", "pos"])["tds"].transform(lambda s: s.ewm(alpha=1 - 0.5 ** (1 / 8), adjust=True).mean())
    A["allow"] = A.groupby(["opp", "pos"])["now_allow"].shift(1)
    lg = A.groupby("pos")["tds"].mean()
    A["opp_f"] = A["allow"] / A["pos"].map(lg)
    X = X.merge(A[["season", "week", "opp", "pos", "opp_f"]], on=["season", "week", "opp", "pos"], how="left")
    X["opp"] = np.log(X["opp_f"].fillna(1.0).clip(0.4, 2.5))
    X["team_rz"] = np.log(X["team_rz"].fillna(T.t_rz_plays.mean()).clip(3, 40) / T.t_rz_plays.mean())
    X["imp"] = np.log(X["imp"].clip(8, 40) / 22.0)
    return X, T, A, lg


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(y, p):
    o = np.argsort(p); r = np.empty(len(p)); r[o] = np.arange(1, len(p) + 1)
    n1 = y.sum(); n0 = len(y) - n1
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / max(1, n1 * n0))


def fit_pos(X, pos):
    d = X[(X.pos == pos) & (X.n_prev >= 1) & (X.season >= FIRST)].dropna(subset=["rz_car", "td_rate"]).copy()
    tr, te = d[d.season <= TRAIN_END], d[d.season > TRAIN_END]
    mu, sd = tr[FEATS].mean(), tr[FEATS].std().replace(0, 1)
    Z = lambda df, f=FEATS: ((df[f] - mu[f]) / sd[f]).values
    m = PoissonRegressor(alpha=1e-4, max_iter=3000).fit(Z(tr), tr.tds.clip(0, 4))
    base_f = ["imp", "td_rate"]
    b = PoissonRegressor(alpha=1e-4, max_iter=3000).fit(Z(tr, base_f), tr.tds.clip(0, 4))
    y = (te.tds > 0).astype(int).values
    pm = 1 - np.exp(-m.predict(Z(te))); pb = 1 - np.exp(-b.predict(Z(te, base_f)))
    cal = []
    for lo, hi in ((0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, 1)):
        s = (pm >= lo) & (pm < hi)
        if s.sum() >= 30:
            cal.append([round(float(pm[s].mean()) * 100, 1), round(float(y[s].mean()) * 100, 1), int(s.sum())])
    # refit on everything for live use
    mu2, sd2 = d[FEATS].mean(), d[FEATS].std().replace(0, 1)
    m2 = PoissonRegressor(alpha=1e-4, max_iter=3000).fit(((d[FEATS] - mu2) / sd2).values, d.tds.clip(0, 4))
    rep = {"test_n": int(len(te)), "test_seasons": f"{TRAIN_END + 1}-{int(d.season.max())}", "td_rate": round(float(y.mean()) * 100, 1),
           "logloss_model": round(ll(y, pm), 4), "logloss_base": round(ll(y, pb), 4), "brier_model": round(float(np.mean((pm - y) ** 2)), 4),
           "brier_base": round(float(np.mean((pb - y) ** 2)), 4), "auc_model": round(auc(y, pm), 3), "auc_base": round(auc(y, pb), 3), "calibration": cal,
           "weights": {f: round(float(c), 3) for f, c in sorted(zip(FEATS, m2.coef_), key=lambda t: -abs(t[1]))}}
    by = []
    for s_ in sorted(te.season.unique()):
        k_ = (te.season == s_).values
        by.append([int(s_), round(ll(y[k_], pm[k_]), 4), round(ll(y[k_], pb[k_]), 4)])
    rep["by_season"] = by
    return {"mean": [float(mu2[f]) for f in FEATS], "scale": [float(sd2[f]) for f in FEATS], "coef": [float(c) for c in m2.coef_], "icpt": float(m2.intercept_)}, rep


def main():
    P, S, G = load()
    X, T, A, lg = build(P, S, G)
    out = {"fitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "feats": FEATS, "pos": {}, "half_life_games": HL}
    report = {}
    for pos in POS:
        out["pos"][pos], report[pos] = fit_pos(X, pos)
        r = report[pos]
        print(f"{pos}: test n={r['test_n']} TD rate {r['td_rate']}% | logloss {r['logloss_model']} vs base {r['logloss_base']} | AUC {r['auc_model']} vs {r['auc_base']} | weights {r['weights']}")
    # current snapshot: every player's features as of his latest game this season
    cur = int(X.season.max())
    last = X[X.season == cur].sort_values(["player_id", "week"]).groupby("player_id").tail(1)
    Tn = T.sort_values(["team", "season", "week"]).groupby("team").tail(1).set_index("team")
    players = {}
    for _, r in last.iterrows():
        players[r.player_id] = {"name": r["name"], "team": r.team, "pos": r.pos, "week": int(r.week), "games": int(r.n_prev) + 1,
                                **{f: round(float(r["now_" + f]), 4) for f in ("rz_car", "gl_car", "rz_tgt", "ez_tgt", "car_sh", "tgt_sh", "td_rate")}}
    team_rz = {t: round(float(math.log(min(40, max(3, v)) / T.t_rz_plays.mean())), 4) for t, v in Tn["now_team_rz"].items()}
    An = A.sort_values(["opp", "pos", "season", "week"]).groupby(["opp", "pos"]).tail(1)
    opp = {}
    for _, r in An.iterrows():
        opp.setdefault(r.opp, {})[r.pos] = round(float(math.log(min(2.5, max(0.4, r.now_allow / lg[r.pos])))), 4)
    out.update(season=cur, players=players, team_rz=team_rz, opp=opp)
    D.mkdir(exist_ok=True)
    (D / "td_model.json").write_text(json.dumps(out))
    (D / "td_report.json").write_text(json.dumps(report, indent=1))
    print("td_model.json:", len(players), "players")


if __name__ == "__main__":
    main()
