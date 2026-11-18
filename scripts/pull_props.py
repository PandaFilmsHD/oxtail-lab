"""Sportsbook player props (The Odds API) -> expected stats per player for the current NFL week.

Runs at most once a week and only once lines are up (Wednesday or later), to stay inside the free 500 credits/month.
Writes data/props.json: {"week": W, "at": iso, "players": {"Name": {"pass_yds":.., "pass_tds":.., "rush_yds":.., "rec_yds":.., "rec":.., "td_p":..}}}
"""
import os, json, time, math, pathlib, datetime, statistics
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEY = os.environ.get("ODDS_API_KEY", "").strip()
OUT = ROOT / "data" / "props.json"
RAW = ROOT / "data" / "props_raw.json"      # the actual lines + best prices, for the Prop Lab tab
GODDS = ROOT / "data" / "game_odds.json"
MARKETS = ["player_pass_yds", "player_pass_tds", "player_rush_yds", "player_reception_yds", "player_receptions", "player_anytime_td"]
API = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"


def prob(price):
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def main():
    if not KEY:
        print("props: no ODDS_API_KEY, skipping"); return
    cur = None
    for lp in sorted((ROOT / "data").glob("*/model.json")):
        cur = json.loads(lp.read_text()).get("cur"); break
    now = datetime.datetime.now(datetime.timezone.utc)
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    raw_old = json.loads(RAW.read_text()) if RAW.exists() else {}
    have_raw = raw_old.get("week") == cur
    if old.get("week") == cur and old.get("at") and have_raw:
        age = (now - datetime.datetime.fromisoformat(old["at"])).total_seconds() / 86400
        if age < 6:   # one full pull a week (about 85 credits) keeps us inside 500 a month
            print(f"props: have week {cur} from {age:.1f} days ago, skipping"); return
    if now.weekday() in (0, 1) and old.get("week") == cur and have_raw:   # Mon/Tue: lines for the next week aren't up yet
        print("props: early in the week, skipping"); return
    ev = requests.get(f"{API}/events", params={"apiKey": KEY}, timeout=30)
    ev.raise_for_status()
    events = [e for e in ev.json() if 0 < (datetime.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) - now).total_seconds() < 6 * 86400]
    print(f"props: {len(events)} games in the next 6 days, credits left {ev.headers.get('x-requests-remaining')}")
    lines = {}   # player -> market -> list of (point, over_prob) or td probs
    raw = {}     # player -> market -> list of (book, point, over_price, under_price)
    evinfo = []
    left = ev.headers.get("x-requests-remaining")
    # game lines: moneyline, spread, total with the best price at each number (3 credits)
    try:
        go = requests.get(f"{API}/odds", params={"apiKey": KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}, timeout=30)
        if go.ok:
            games = {}
            for g in go.json():
                gd = games.setdefault(g["id"], {"home": g["home_team"], "away": g["away_team"], "at": g["commence_time"], "books": {}})
                for bk in g.get("bookmakers", []):
                    for mk in bk.get("markets", []):
                        gd["books"].setdefault(mk["key"], []).append({"book": bk["key"], "outcomes": [{k: o.get(k) for k in ("name", "price", "point")} for o in mk.get("outcomes", [])]})
            GODDS.write_text(json.dumps({"at": now.isoformat(), "games": games}, separators=(",", ":")))
            left = go.headers.get("x-requests-remaining")
    except Exception as ex_:
        print("props: game odds failed", ex_)
    for e in events:
        evinfo.append({"id": e["id"], "home": e["home_team"], "away": e["away_team"], "at": e["commence_time"]})
        r = requests.get(f"{API}/events/{e['id']}/odds", params={"apiKey": KEY, "regions": "us", "markets": ",".join(MARKETS), "oddsFormat": "american"}, timeout=30)
        if not r.ok:
            print("props: event failed", r.status_code); continue
        for bk in r.json().get("bookmakers", []):
            for mk in bk.get("markets", []):
                outs = mk.get("outcomes", [])
                if mk["key"] == "player_anytime_td":
                    for o in outs:
                        nm_ = o.get("description") or o.get("name")
                        if o.get("name") != "No" and nm_:
                            raw.setdefault(nm_, {}).setdefault("td", []).append((bk["key"], None, o["price"], None, e["id"]))
                        if o.get("name") in ("Yes", "Over") or o.get("description"):
                            nm = o.get("description") or o.get("name")
                            if o.get("name") == "No":
                                continue
                            lines.setdefault(nm, {}).setdefault("td", []).append(prob(o["price"]))
                    continue
                by = {}
                for o in outs:
                    by.setdefault(o.get("description"), {})[o.get("name")] = o
                for nm, d in by.items():
                    if "Over" in d and "Under" in d and d["Over"].get("point") is not None:
                        raw.setdefault(nm, {}).setdefault(mk["key"], []).append((bk["key"], d["Over"]["point"], d["Over"]["price"], d["Under"]["price"], e["id"]))
                        po, pu = prob(d["Over"]["price"]), prob(d["Under"]["price"])
                        lines.setdefault(nm, {}).setdefault(mk["key"], []).append((d["Over"]["point"], po / (po + pu)))
        time.sleep(0.2)
        left = r.headers.get("x-requests-remaining")
    players = {}
    for nm, mk in lines.items():
        x = {}
        for k, L in mk.items():
            if k == "td":
                p = statistics.median(L) * 0.95   # strip some of the book's built-in margin
                x["td_p"] = round(min(0.95, p), 3)
                continue
            pt = statistics.median(a for a, _ in L); po = statistics.median(b for _, b in L)
            # the line is roughly the median outcome; nudge by how lopsided the over is, and add the usual mean-over-median skew
            skew = {"player_pass_yds": 1.02, "player_rush_yds": 1.08, "player_reception_yds": 1.08, "player_receptions": 1.03, "player_pass_tds": 1.0}[k]
            val = pt * (1 + (po - 0.5) * 0.25) * skew
            x[{"player_pass_yds": "pass_yds", "player_pass_tds": "pass_tds", "player_rush_yds": "rush_yds", "player_reception_yds": "rec_yds", "player_receptions": "rec"}[k]] = round(val, 2)
        players[nm] = x
    OUT.write_text(json.dumps({"week": cur, "at": now.isoformat(), "players": players}, sort_keys=True, separators=(",", ":")))
    # main line = the number most books hang; best price at that number; no-vig consensus chance of the over
    R = {}
    for nm, mk in raw.items():
        for k, L in mk.items():
            if k == "td":
                best = max(x[2] for x in L); R.setdefault(nm, {})["td"] = {"yes": best, "p": round(statistics.median(prob(x[2]) for x in L) * 0.93, 3), "books": len(L), "event": L[0][4]}
                continue
            pts = [x[1] for x in L]; main = max(set(pts), key=lambda v: (pts.count(v), -abs(v - statistics.median(pts))))
            at = [x for x in L if x[1] == main]
            pov = statistics.median(prob(x[2]) / (prob(x[2]) + prob(x[3])) for x in at)
            R.setdefault(nm, {})[k] = {"line": main, "over": max(x[2] for x in at), "under": max(x[3] for x in at), "p_over": round(pov, 3), "books": len(at), "event": at[0][4]}
    RAW.write_text(json.dumps({"week": cur, "at": now.isoformat(), "events": evinfo, "props": R}, separators=(",", ":")))
    print(f"props: {len(players)} players with lines saved for week {cur}, credits left {left if events else '?'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("props: skipped", e)
