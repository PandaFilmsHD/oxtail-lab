"""Closing prop lines for the every-bet tracker's closing line value (CLV).
Runs every pipeline pass, does nothing unless a kickoff is 5-40 min away. Then it pulls DraftKings' current lines for the
PROP_EVENTS events of that week with the most open tracker prop bets, only the markets those bets are in (1 credit per market per event).
Game-line CLV needs no credits: nflverse's last line before kickoff (usage.json) is the close.
Budget: weekly full props pull ~85 credits (about 370 a month) + at most PROP_EVENTS x 6 a week here (about 52 a month at 2) < 500.
Writes data/props_close.json: {"week": W, "events_done": [...], "props": {event_id: {player: {market: {line, over, under} | {yes, no}}}}}"""
import os, json, pathlib, datetime
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
D = ROOT / "data"; OUT = D / "props_close.json"
KEY = os.environ.get("ODDS_API_KEY", "").strip()
API = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
PROP_EVENTS = 2
KEEP = 95   # pull_props.py refuses under 100 credits: never spend into next week's full pull
MK = {"Pass yds": "player_pass_yds", "Pass TDs": "player_pass_tds", "Rush yds": "player_rush_yds", "Rec yds": "player_reception_yds",
      "Catches": "player_receptions", "Anytime TD": "player_anytime_td"}


def load(p, d=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return d


def main():
    if not KEY:
        print("close: no ODDS_API_KEY, skipping"); return
    now = datetime.datetime.now(datetime.timezone.utc)
    R = load(D / "props_raw.json", {}) or {}; PK = load(D / "props_picks.json", {}) or {}
    cur = str(R.get("week") or "")
    C = load(OUT, {}) or {}
    if str(C.get("week")) != cur:
        C = {"week": cur, "events_done": [], "props": {}}
    soon = {e["id"] for e in R.get("events", []) if 300 <= (datetime.datetime.fromisoformat(e["at"].replace("Z", "+00:00")) - now).total_seconds() <= 2400}
    if not soon or len(C["events_done"]) >= PROP_EVENTS:
        return
    cnt, mks = {}, {}
    for k, x in (PK.get(cur) or {}).items():
        if k.startswith("PA|") and x.get("v") == 2 and not x.get("graded") and x.get("event") in soon and x.get("event") not in C["events_done"]:
            cnt[x["event"]] = cnt.get(x["event"], 0) + 1; mks.setdefault(x["event"], set()).add(MK[x["market"]])
    if not cnt:
        return
    ev = requests.get(f"{API}/events", params={"apiKey": KEY}, timeout=30)   # free call: tells us the credits left
    left = int(ev.headers.get("x-requests-remaining") or 0)
    for eid in sorted(cnt, key=lambda e: -cnt[e]):
        if len(C["events_done"]) >= PROP_EVENTS or left - len(mks[eid]) < KEEP:
            break
        r = requests.get(f"{API}/events/{eid}/odds", params={"apiKey": KEY, "bookmakers": "draftkings", "markets": ",".join(sorted(mks[eid])), "oddsFormat": "american"}, timeout=30)
        if not r.ok:
            print("close: event failed", r.status_code); continue
        left = int(r.headers.get("x-requests-remaining") or left)
        P = C["props"].setdefault(eid, {})
        for bk in r.json().get("bookmakers", []):
            for mk in bk.get("markets", []):
                by = {}
                for o in mk.get("outcomes", []):
                    by.setdefault(o.get("description") or o.get("name"), {})[o.get("name")] = o
                for nm, d in by.items():
                    if mk["key"] == "player_anytime_td":
                        P.setdefault(nm, {})["td"] = {"yes": (d.get("Yes") or {}).get("price"), "no": (d.get("No") or {}).get("price")}
                    elif "Over" in d and "Under" in d:
                        P.setdefault(nm, {})[mk["key"]] = {"line": d["Over"].get("point"), "over": d["Over"]["price"], "under": d["Under"]["price"]}
        C["events_done"].append(eid)
        print(f"close: saved closing props for event {eid} ({cnt[eid]} open bets), credits left {left}")
    C["at"] = now.isoformat(timespec="seconds")
    OUT.write_text(json.dumps(C, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("close: skipped", e)
