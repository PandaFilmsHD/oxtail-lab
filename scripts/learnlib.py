"""Learning from our own results: for every type of bet, did they hit as often as the model said?

Bet types ("buckets"): Kalshi by market, side and price band; sportsbook props by market and side; game lines by kind; live picks by stat, side, price band.
For each bucket:  k = (how much better than the price we actually did) / (how much better we said we'd do),
shrunk toward 1 by how much evidence there really is: one bet's hit/miss is worth far less than its edge, so small edges need
hundreds of bets before k moves much (prior: k = 1, give or take 0.5). k is clamped to 0..1.3.
Discord picks, @everyone pings and live alerts use edge x k, so bet types that keep underdelivering stop making the cut.
"""
import json, math, datetime
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "data"
PRIOR = 40       # kept for the file format
TAU = 0.5        # prior spread of k


def band(c):
    return "<25c" if c < 25 else "25-50c" if c < 50 else "50-75c" if c < 75 else "75c+"


def bucket(kind, x):
    if kind == "kalshi":
        mk = x.get("market") or x.get("kind") or "game"
        return f"KA|{mk}|{x.get('side')}|{band(x.get('cost') or 50)}"
    if kind == "live":
        return f"LV|{x.get('stat')}|{x.get('side')}|{band(x.get('cost') or 50)}"
    if kind == "game":
        return f"GL|{x.get('kind') or 'line'}"
    return f"PR|{x.get('market')}|{x.get('side')}"


def compute(PK, live_bets=None, live_done=None):
    B = {}
    def add(key, pred, mkt, hit, u):
        b = B.setdefault(key, {"n": 0, "pred": 0.0, "mkt": 0.0, "hit": 0, "u": 0.0})
        b["n"] += 1; b["pred"] += pred; b["mkt"] += mkt; b["hit"] += hit; b["u"] += u
    for w_, pk in PK.items():
        for k, x in pk.items():
            if k.startswith(("PA|", "GA|")) or not x.get("graded") or x.get("result") is None:
                continue
            hit = 1 if x["result"] else 0
            if k.startswith("KA|"):
                c = ((x.get("cost") or 0) + (x.get("fee") or 0)) / 100
                add(bucket("kalshi", x), (x.get("p") or 0) / 100, (x.get("cost") or 0) / 100, hit, (1 / c - 1) if hit else -1)
            elif x.get("p") is not None and x.get("p_mkt") is not None:
                pr = x.get("price") or -110
                win = pr / 100 if pr > 0 else 100 / -pr
                add(bucket("game" if k.startswith("G|") else "prop", x), x["p"] / 100, x["p_mkt"] / 100, hit, win if hit else -1)
    for x in live_bets or []:
        r = (live_done or {}).get(f"{x['ticker']}|{x['side']}")
        if r:
            add(bucket("live", x), x["p"] / 100, x["cost"] / 100, 1 if r["win"] else 0, r["u"])
    out = {}
    for key, b in B.items():
        n = b["n"]; said = (b["pred"] - b["mkt"]) / n; did = (b["hit"] - b["mkt"]) / n
        e_ = max(said, 0.01)
        pbar = min(0.95, max(0.05, b["mkt"] / n))
        prec_data = n * e_ ** 2 / (pbar * (1 - pbar))       # how sharply these results pin down k
        k = (1 / TAU ** 2 + prec_data * (did / e_)) / (1 / TAU ** 2 + prec_data)
        out[key] = {"n": n, "said_pct": round(100 * b["pred"] / n, 1), "hit_pct": round(100 * b["hit"] / n, 1), "price_pct": round(100 * b["mkt"] / n, 1),
                    "u": round(b["u"], 2), "k": round(min(1.3, max(0.0, k)), 3), "evidence": round(prec_data / (prec_data + 1 / TAU ** 2), 3)}
    return out


def load():
    try:
        return json.loads((D / "learned.json").read_text()).get("buckets", {})
    except Exception:
        return {}


def k_for(L, key):
    return (L.get(key) or {}).get("k", 1.0)


def save(buckets):
    (D / "learned.json").write_text(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "prior_bets": PRIOR, "buckets": buckets}, indent=1, sort_keys=True))
