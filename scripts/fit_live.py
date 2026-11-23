"""In-game model, trained on nflverse play-by-play (2016 to now): what happens in the REST of a game, given the score and clock.

Snapshots: every regular season game at 7 moments (end of each quarter and the middle of each quarter).
1) Team points left: Poisson fit on (time left, score margin, margin x time, Vegas team total), plus real outcome pairs
   (both teams, same game) so live sims keep the right spread and the link between the two teams.
2) Player stats left (rush yds, rec yds, catches, pass yds, pass TDs, anytime TD) by position: his normal per-game number x time left,
   adjusted for game script (margin, margin x time) and how he's being used so far today. Real outcome ratios give the spread.
Backtest: trained on 2016-2022, tested on 2023 to now vs the simple version (normal number x time left).
Writes data/live_model.json and data/live_report.json.
"""
import io, json, math, datetime, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
REL = "https://github.com/nflverse/nflverse-data/releases/download/"
GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FIX = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
CHECK = [3150, 2700, 2250, 1800, 1350, 900, 450]      # seconds left: mid Q1, end Q1, mid Q2, half, mid Q3, end Q3, mid Q4
TRAIN_END, FIRST = 2022, 2016
STATS = {"rush_yds": ("RB", "WR", "QB"), "rec_yds": ("WR", "TE", "RB"), "rec": ("WR", "TE", "RB"), "pass_yds": ("QB",), "pass_tds": ("QB",), "td": ("RB", "WR", "TE")}
WEEKCOL = {"rush_yds": "rushing_yards", "rec_yds": "receiving_yards", "rec": "receptions", "pass_yds": "passing_yards", "pass_tds": "passing_tds", "td": "_td"}
PCOLS = ["season", "week", "season_type", "game_id", "home_team", "away_team", "posteam", "play_type", "game_seconds_remaining", "total_home_score", "total_away_score",
         "passer_player_id", "rusher_player_id", "receiver_player_id", "passing_yards", "rushing_yards", "receiving_yards", "complete_pass", "pass_touchdown",
         "td_player_id", "two_point_attempt"]
QS = np.linspace(0.01, 0.99, 49)


def fetch(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "oxtail-lab"}), timeout=180) as r:
                return r.read()
        except Exception as e:
            print("  retry", url.rsplit("/", 1)[-1], type(e).__name__)
    raise RuntimeError(url)


def years():
    t = datetime.date.today()
    return list(range(FIRST, (t.year if t.month >= 9 else t.year - 1) + 1))


def load():
    P, S = [], []
    for y in years():
        try:
            df = pd.read_csv(io.BytesIO(fetch(f"{REL}pbp/play_by_play_{y}.csv.gz")), compression="gzip", usecols=PCOLS, low_memory=False)
            P.append(df[df.season_type == "REG"]); print("pbp", y, len(P[-1]))
        except Exception as e:
            print("pbp", y, "skipped", e)
        try:
            S.append(pd.read_csv(io.BytesIO(fetch(f"{REL}stats_player/stats_player_week_{y}.csv")), low_memory=False)); print("stats", y)
        except Exception as e:
            print("stats", y, "skipped", e)
    return pd.concat(P, ignore_index=True), pd.concat(S, ignore_index=True), pd.read_csv(io.BytesIO(fetch(GAMES)))


def team_snapshots(P, G):
    G = G[G.game_type == "REG"].dropna(subset=["home_score", "away_score"]).copy()
    G["imp_h"] = (G.total_line + G.spread_line) / 2; G["imp_a"] = (G.total_line - G.spread_line) / 2
    fin = G.set_index("game_id")[["season", "home_score", "away_score", "imp_h", "imp_a"]]
    P = P.dropna(subset=["game_seconds_remaining"]).sort_values(["game_id", "game_seconds_remaining"], ascending=[True, False])
    rows = []
    for gid, g in P.groupby("game_id", sort=False):
        if gid not in fin.index:
            continue
        f = fin.loc[gid]
        gs = g.game_seconds_remaining.values; hs = g.total_home_score.ffill().fillna(0).values; as_ = g.total_away_score.ffill().fillna(0).values
        for T in CHECK:
            i = np.searchsorted(-gs, -T, side="right") - 1      # last play at or before this moment
            h0, a0 = (hs[i], as_[i]) if i >= 0 else (0, 0)
            rows.append({"game_id": gid, "season": int(f.season), "T": T, "frac": T / 3600, "h0": h0, "a0": a0, "hrem": f.home_score - h0, "arem": f.away_score - a0,
                         "imp_h": f.imp_h, "imp_a": f.imp_a})
    return pd.DataFrame(rows)


def team_X(df, side):
    m = (df.h0 - df.a0) if side == "h" else (df.a0 - df.h0)
    d7 = np.clip(m, -24, 24) / 7
    return np.column_stack([d7, d7 * (1 - df.frac), np.log(df.frac), np.clip(m, -24, 24).abs() / 7 * (1 - df.frac)])


def fit_team(TS):
    TS = TS.dropna(subset=["imp_h", "imp_a"])
    long = []
    for side in ("h", "a"):
        X = team_X(TS, side); base = (TS["imp_" + side] * TS.frac).clip(lower=0.5).values; y = TS[side + "rem"].clip(lower=0).values
        long.append((X, base, y, TS.season.values))
    X = np.vstack([l[0] for l in long]); base = np.concatenate([l[1] for l in long]); y = np.concatenate([l[2] for l in long]); se = np.concatenate([l[3] for l in long])
    tr, te = se <= TRAIN_END, se > TRAIN_END
    m = PoissonRegressor(alpha=1e-5, max_iter=2000).fit(X[tr], y[tr] / base[tr], sample_weight=base[tr])
    mu_te = m.predict(X[te]) * base[te]
    dev = lambda yy, mu: float(np.mean(2 * (np.where(yy > 0, yy * np.log(np.maximum(yy, 1e-9) / mu), 0) - (yy - mu))))
    rep = {"test_n": int(te.sum()), "dev_model": round(dev(y[te], mu_te), 4), "dev_simple": round(dev(y[te], base[te]), 4)}
    m2 = PoissonRegressor(alpha=1e-5, max_iter=2000).fit(X, y / base, sample_weight=base)
    # real outcome pairs (home ratio, away ratio) per moment, for the live sims
    Xh, Xa = team_X(TS, "h"), team_X(TS, "a")
    mh = m2.predict(Xh) * (TS.imp_h * TS.frac).clip(lower=0.5).values; ma = m2.predict(Xa) * (TS.imp_a * TS.frac).clip(lower=0.5).values
    pairs = {}
    for T in CHECK:
        k = (TS["T"] == T).values
        rh, ra = TS.hrem.values[k] / mh[k], TS.arem.values[k] / ma[k]
        idx = np.random.default_rng(7).choice(k.sum(), size=min(1500, k.sum()), replace=False)
        pairs[str(T)] = [[round(float(rh[i]), 3), round(float(ra[i]), 3)] for i in idx]
    return {"coef": [float(c) for c in m2.coef_], "icpt": float(m2.intercept_), "pairs": pairs}, rep


def player_snapshots(P, S, G):
    P = P[P.play_type.isin(["run", "pass"]) & (P.two_point_attempt.fillna(0) == 0)].dropna(subset=["game_seconds_remaining"]).copy()
    P["td_rr"] = P.td_player_id.notna() & ((P.td_player_id == P.rusher_player_id) | (P.td_player_id == P.receiver_player_id))
    parts = {
        "rush_yds": P.dropna(subset=["rusher_player_id"]).assign(pid=lambda d: d.rusher_player_id, v=lambda d: d.rushing_yards.fillna(0)),
        "rec_yds": P.dropna(subset=["receiver_player_id"]).assign(pid=lambda d: d.receiver_player_id, v=lambda d: d.receiving_yards.fillna(0)),
        "rec": P.dropna(subset=["receiver_player_id"]).assign(pid=lambda d: d.receiver_player_id, v=lambda d: d.complete_pass.fillna(0)),
        "pass_yds": P.dropna(subset=["passer_player_id"]).assign(pid=lambda d: d.passer_player_id, v=lambda d: d.passing_yards.fillna(0)),
        "pass_tds": P.dropna(subset=["passer_player_id"]).assign(pid=lambda d: d.passer_player_id, v=lambda d: d.pass_touchdown.fillna(0)),
        "td": P[P.td_rr].assign(pid=lambda d: d.td_player_id, v=1.0),
    }
    S = S[S.season_type == "REG"].copy()
    tcol = "team" if "team" in S.columns else "recent_team"
    S["team"] = S[tcol].map(lambda x: FIX.get(x, x))
    for c in ("rushing_yards", "receiving_yards", "receptions", "passing_yards", "passing_tds", "rushing_tds", "receiving_tds"):
        S[c] = pd.to_numeric(S.get(c), errors="coerce").fillna(0)
    S["_td"] = S.rushing_tds + S.receiving_tds
    S = S.sort_values(["player_id", "season", "week"])
    a = 1 - 0.5 ** (1 / 6)
    base = S[["player_id", "season", "week", "position", "team"]].copy()
    for st, col in WEEKCOL.items():
        base["b_" + st] = S.groupby("player_id")[col].transform(lambda s: s.ewm(alpha=a, adjust=True).mean().shift(1))
    base["n"] = S.groupby("player_id").cumcount()
    gmap = P.groupby("game_id")[["season", "week", "home_team", "away_team"]].first().reset_index()
    return parts, base, gmap


def fit_players(parts, base, gmap, TS):
    Gi = gmap.copy()
    for c in ("home_team", "away_team"):
        Gi[c] = Gi[c].map(lambda x: FIX.get(x, x))
    TSi = TS.set_index(["game_id", "T"])
    out, rep = {}, {}
    for st, poss in STATS.items():
        df = parts[st]
        rows = []
        for T in CHECK:
            before = df[df.game_seconds_remaining >= T].groupby(["game_id", "pid"])["v"].sum()
            after = df[df.game_seconds_remaining < T].groupby(["game_id", "pid"])["v"].sum()
            j = pd.concat([before.rename("so_far"), after.rename("rem")], axis=1).fillna(0).reset_index()
            j["T"] = T
            rows.append(j)
        J = pd.concat(rows, ignore_index=True).merge(Gi, on="game_id", how="left")
        J = J.merge(base, left_on=["pid", "season", "week"], right_on=["player_id", "season", "week"], how="inner")
        J = J[J.position.isin(poss) & (J.n >= 3)].dropna(subset=["b_" + st])
        J = J[J["b_" + st] > (0.05 if st in ("td", "pass_tds") else 0.5)]
        J = J.join(TSi[["h0", "a0", "imp_h", "imp_a"]], on=["game_id", "T"], how="left").dropna(subset=["h0"])
        home = J.team == J.home_team
        J["m"] = np.where(home, J.h0 - J.a0, J.a0 - J.h0)
        J["frac"] = J["T"] / 3600
        bsl = J["b_" + st].values
        c = 1.0 if st in ("rec", "td", "pass_tds") else 10.0
        J["today"] = np.log((J.so_far + c) / (bsl * (1 - J.frac) + c))
        d7 = np.clip(J.m, -24, 24) / 7
        X = np.column_stack([d7, d7 * (1 - J.frac), np.log(J.frac), J.today, J.today * (1 - J.frac)])
        expo = np.maximum(bsl * J.frac.values, 1e-3); y = J.rem.clip(lower=0).values; se = J.season.values
        pos_out, pos_rep = {}, {}
        for pos in poss:
            k = (J.position == pos).values
            if k.sum() < 3000:
                continue
            tr, te = k & (se <= TRAIN_END), k & (se > TRAIN_END)
            m = PoissonRegressor(alpha=1e-5, max_iter=2000).fit(X[tr], y[tr] / expo[tr], sample_weight=expo[tr])
            mu = m.predict(X[te]) * expo[te]
            dev = lambda yy, mm: float(np.mean(2 * (np.where(yy > 0, yy * np.log(np.maximum(yy, 1e-9) / np.maximum(mm, 1e-9)), 0) - (yy - mm))))
            m2 = PoissonRegressor(alpha=1e-5, max_iter=2000).fit(X[k], y[k] / expo[k], sample_weight=expo[k])
            mu_all = m2.predict(X[k]) * expo[k]
            ratio = y[k] / np.maximum(mu_all, 1e-6)
            # real spread of outcomes: ratio quantiles by how much is expected (low / mid / high) and by time left
            mb = np.digitize(mu_all, np.quantile(mu_all, [1 / 3, 2 / 3]))
            fb = np.digitize(J.frac.values[k], [0.3, 0.6])
            qt = {f"{a_}{b_}": [round(float(v), 4) for v in np.quantile(ratio[(mb == a_) & (fb == b_)], QS)] for a_ in range(3) for b_ in range(3) if ((mb == a_) & (fb == b_)).sum() >= 200}
            pos_out[pos] = {"coef": [float(x) for x in m2.coef_], "icpt": float(m2.intercept_), "mu_cut": [float(v) for v in np.quantile(mu_all, [1 / 3, 2 / 3])], "q": qt, "c": c}
            pos_rep[pos] = {"test_n": int(te.sum()), "dev_model": round(dev(y[te], mu), 4), "dev_simple": round(dev(y[te], expo[te]), 4),
                            "weights": dict(zip(["margin", "margin_x_time_gone", "log_time_left", "usage_today", "usage_today_x_time_gone"], [round(float(x), 3) for x in m2.coef_]))}
            print(st, pos, pos_rep[pos])
        out[st], rep[st] = pos_out, pos_rep
    return out, rep


def main():
    P, S, G = load()
    for c in ("home_team", "away_team", "posteam"):
        P[c] = P[c].map(lambda x: FIX.get(x, x))
    G["home_team"] = G.home_team.map(lambda x: FIX.get(x, x)); G["away_team"] = G.away_team.map(lambda x: FIX.get(x, x))
    TS = team_snapshots(P, G)
    print("team snapshots", len(TS))
    team, trep = fit_team(TS)
    print("team", trep)
    parts, base, gmap = player_snapshots(P, S, G)
    players, prep = fit_players(parts, base, gmap, TS)
    D.mkdir(exist_ok=True)
    (D / "live_model.json").write_text(json.dumps({"fitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "check": CHECK,
                                                    "qs": [float(q) for q in QS], "team": team, "players": players}))
    (D / "live_report.json").write_text(json.dumps({"team": trep, "players": prep}, indent=1))
    print("live_model.json written")


if __name__ == "__main__":
    main()
