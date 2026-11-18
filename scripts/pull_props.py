"""Sportsbook player props (The Odds API) -> expected stats per player for the current NFL week.

Runs at most once every 3 days and only once lines are up (Wednesday or later), to stay inside the free 500 credits/month.
Writes data/props.json: {"week": W, "at": iso, "players": {"Name": {"pass_yds":.., "pass_tds":.., "rush_yds":.., "rec_yds":.., "rec":.., "td_p":..}}}
"""
import os, json, time, math, pathlib, datetime, statistics
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEY = os.environ.get("ODDS_API_KEY", "").strip()
OUT = ROOT / "data" / "props.json"
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
    if old.get("week") == cur and old.get("at"):
        age = (now - datetime.datetime.fromisoformat(old["at"])).total_seconds() / 86400
        if age < 3:
            print(f"props: have week {cur} from {age:.1f} days ago, skipping"); return
    if now.weekday() in (0, 1) and old.get("week") == cur:   # Mon/Tue: lines for the next week aren't up yet
        print("props: early in the week, skipping"); return
    ev = requests.get(f"{API}/events", params={"apiKey": KEY}, timeout=30)
    ev.raise_for_status()
    events = [e for e in ev.json() if 0 < (datetime.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) - now).total_seconds() < 6 * 86400]
    print(f"props: {len(events)} games in the next 6 days, credits left {ev.headers.get('x-requests-remaining')}")
    lines = {}   # player -> market -> list of (point, over_prob) or td probs
    for e in events:
        r = requests.get(f"{API}/events/{e['id']}/odds", params={"apiKey": KEY, "regions": "us", "markets": ",".join(MARKETS), "oddsFormat": "american"}, timeout=30)
        if not r.ok:
            print("props: event failed", r.status_code); continue
        for bk in r.json().get("bookmakers", []):
            for mk in bk.get("markets", []):
                outs = mk.get("outcomes", [])
                if mk["key"] == "player_anytime_td":
                    for o in outs:
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
    print(f"props: {len(players)} players with lines saved for week {cur}, credits left {left if events else '?'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("props: skipped", e)
