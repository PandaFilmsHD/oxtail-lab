"""Carries (rush attempts) prop fit, same format as the other props in data/prop_dists.json.

Projection: (1 - w) x season average + w x last 3 games, then slope x that + intercept, fitted on 2016-2025 regular seasons
(only games before the one being predicted). Spread: real outcome / projection ratios in 4 buckets by projection size.
Backtest: fit on 2016-2022, judged on 2023-2025 vs the plain season average. Writes rush_att|RB and rush_att|QB into prop_dists.json.
"""
import io, json, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
REL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{}.csv"


def fetch(y):
    with urllib.request.urlopen(urllib.request.Request(REL.format(y), headers={"User-Agent": "oxtail-lab"}), timeout=120) as r:
        return pd.read_csv(io.BytesIO(r.read()), low_memory=False)


def rows(S, pos):
    S = S[(S.season_type == "REG") & (S.position == pos)].copy()
    S["car"] = pd.to_numeric(S["carries"], errors="coerce").fillna(0)
    out = []
    for (pid, se), g in S.sort_values(["week"]).groupby(["player_id", "season"]):
        c = g.car.values
        for i in range(3, len(c)):
            if pos == "RB" and np.mean(c[:i]) < 3:      # skip guys who barely touch it
                continue
            out.append((se, np.mean(c[:i]), np.mean(c[i - 3:i]), c[i]))
    return np.array(out)


def fit(R):
    best = None
    for w in np.linspace(0, 1, 11):
        m = (1 - w) * R[:, 1] + w * R[:, 2]
        A = np.vstack([m, np.ones_like(m)]).T
        sl, ic = np.linalg.lstsq(A, R[:, 3], rcond=None)[0]
        mae = float(np.mean(np.abs(sl * m + ic - R[:, 3])))
        if best is None or mae < best[0]:
            best = (mae, w, sl, ic)
    return best


def main():
    S = pd.concat([fetch(y) for y in range(2016, 2026)], ignore_index=True)
    dist = json.loads((D / "prop_dists.json").read_text())
    rep = {}
    for pos in ("RB", "QB"):
        R = rows(S, pos)
        tr, te = R[R[:, 0] <= 2022], R[R[:, 0] > 2022]
        mae, w, sl, ic = fit(tr)
        m_te = (1 - w) * te[:, 1] + w * te[:, 2]
        rep[pos] = {"test_n": int(len(te)), "mae_model": round(float(np.mean(np.abs(sl * m_te + ic - te[:, 3]))), 3),
                    "mae_season_avg": round(float(np.mean(np.abs(te[:, 1] - te[:, 3]))), 3)}
        mae, w, sl, ic = fit(R)                                  # refit on everything for live use
        proj = sl * ((1 - w) * R[:, 1] + w * R[:, 2]) + ic
        ratio = R[:, 3] / np.maximum(proj, 0.5)
        cuts = np.quantile(proj, [0.25, 0.5, 0.75])
        rng = np.random.default_rng(3)
        buckets = []
        for lo, hi in zip([-1, *cuts], [*cuts, proj.max() + 1]):
            k = (proj > lo) & (proj <= hi)
            r_ = ratio[k]
            buckets.append({"hi": float(hi), "ratios": [round(float(x), 4) for x in np.sort(rng.choice(r_, size=min(400, len(r_)), replace=False))]})
        dist[f"rush_att|{pos}"] = {"w_recent": float(w), "slope": float(sl), "icpt": float(ic), "mae": float(mae), "n": int(len(R)), "buckets": buckets}
        print(pos, rep[pos], "w_recent", round(w, 2), "slope", round(sl, 3), "icpt", round(ic, 2))
    (D / "prop_dists.json").write_text(json.dumps(dist))
    (D / "carries_report.json").write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
