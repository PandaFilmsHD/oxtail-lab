"""Kalshi NFL prices (free public market data, no key) -> data/kalshi.json.

Kalshi posts game lines and player props as ladders of yes/no contracts priced in cents ("Reggie Virgil: 40+ receiving yards",
"Washington wins by over 4.5 points", "Full Game: over 47.5 points"). We keep the best ask/bid for every rung of every game
in the next 8 days so props_lab.py can compare our sim chance with what a share actually costs.
Runs every update, but skips if the last pull is under 15 minutes old.
"""
import json, pathlib, datetime, re, time
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "kalshi.json"
API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"KXNFLGAME": "ml", "KXNFLSPREAD": "spread", "KXNFLTOTAL": "total",
          "KXNFLPASSYDS": "Pass yds", "KXNFLRSHYDS": "Rush yds", "KXNFLRECYDS": "Rec yds", "KXNFLREC": "Catches",
          "KXNFLPASSTDS": "Pass TDs", "KXNFLANYTD": "Anytime TD"}
ABBR = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "JAC", "KC", "LV",
        "LAC", "LA", "LAR", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS", "WSH"}
FIX = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS"}


def key(n):
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", n.lower())
    return re.sub(r"[^a-z]", "", n)


def teams_of(event_ticker):
    """KXNFLSPREAD-26NOV23CINWAS -> ('CIN', 'WAS')  (away, home)."""
    code = event_ticker.split("-", 1)[1][7:]
    for i in (2, 3):
        a, h = code[:i], code[i:]
        if a in ABBR and h in ABBR:
            return FIX.get(a, a), FIX.get(h, h)
    return None, None


def ev_day(code):
    """26NOV22TBDET -> date(2026, 11, 22), the game's date from the event code."""
    try:
        return datetime.datetime.strptime(code[:7].title(), "%y%b%d").date()
    except (TypeError, ValueError):
        return None


def keep(entry, code):
    """One game per team and one game per player: the window can hold two games for a team (Thanksgiving week),
    and mixing their prices would compare this week's sims to next week's market. Keep the sooner game.
    Returns 'same' (add to it), 'new' (replace it with this sooner game) or 'skip'."""
    if entry is None or entry.get("ev") in (None, code):
        return "same"
    a, b = ev_day(code), ev_day(entry["ev"])
    return "new" if (a and b and a < b) else "skip"


def f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if old.get("at") and (now - datetime.datetime.fromisoformat(old["at"])).total_seconds() < 15 * 60:
        print("kalshi: pulled under 15 min ago, skipping"); return
    games, props, n = {}, {}, 0
    for ser, kind in SERIES.items():
        cursor = ""
        for _ in range(20):
            try:
                r = requests.get(f"{API}/markets", params={"series_ticker": ser, "status": "open", "limit": 1000, **({"cursor": cursor} if cursor else {})}, timeout=30)
                r.raise_for_status(); j = r.json()
            except Exception as ex:
                print("kalshi:", ser, "failed", str(ex)[:100]); break
            for m in j.get("markets", []):
                try:
                    occ = datetime.datetime.fromisoformat((m.get("occurrence_datetime") or m.get("expected_expiration_time")).replace("Z", "+00:00"))
                except Exception:
                    continue
                if not (-0.2 < (occ - now).total_seconds() / 86400 < 8):
                    continue
                away, home = teams_of(m["event_ticker"])
                if not home:
                    continue
                q = {"strike": m.get("floor_strike"), "ask": f(m.get("yes_ask_dollars")), "bid": f(m.get("yes_bid_dollars")),
                     "no_ask": f(m.get("no_ask_dollars")), "vol": f(m.get("volume_fp")) or 0, "ticker": m["ticker"]}
                if q["ask"] is None and q["no_ask"] is None:
                    continue
                code = m["event_ticker"].split("-", 1)[1]
                if kind in ("ml", "spread", "total"):
                    k_ = keep(games.get(home), code)
                    if k_ == "skip":
                        continue
                    if k_ == "new" or home not in games:
                        games[home] = {"away": away, "home": home, "at": occ.isoformat(), "ev": code, "ml": {}, "spread": [], "total": []}
                n += 1
                g = games.get(home)
                if kind == "ml":
                    tm = FIX.get(m["ticker"].rsplit("-", 1)[-1], m["ticker"].rsplit("-", 1)[-1])
                    g["ml"][tm] = q
                elif kind == "spread":
                    tm = re.match(r"[A-Z]+", m["ticker"].rsplit("-", 1)[-1]).group(0)
                    q["team"] = FIX.get(tm, tm); g["spread"].append(q)
                elif kind == "total":
                    g["total"].append(q)
                else:
                    nm = (m.get("title") or "").split(":")[0].strip()
                    if nm:
                        k_ = keep(props.get(key(nm)), code)
                        if k_ == "skip":
                            continue
                        if k_ == "new" or key(nm) not in props:
                            props[key(nm)] = {"name": nm, "home": home, "ev": code}
                        props[key(nm)].setdefault(kind, []).append(q)
            cursor = j.get("cursor") or ""
            if not cursor:
                break
            time.sleep(0.15)
    OUT.write_text(json.dumps({"at": now.isoformat(timespec="seconds"), "games": games, "props": props}, separators=(",", ":")))
    print(f"kalshi: {n} prices, {len(games)} games, {len(props)} players")


if __name__ == "__main__":
    main()
