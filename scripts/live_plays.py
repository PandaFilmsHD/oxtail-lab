"""Live plays for Od from Oxtail: watches ESPN's public game feed every 30 seconds during games
and pushes every play by your starters to your phone. Opponent plays only go to the in-app feed.

python scripts/live_plays.py --probe   prints the feed format for the game that's on now (for setup/debug)
python scripts/live_plays.py           runs until no game with your or your opponent's starters is live or about to start
"""
import json, sys, time, re, urllib.request, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LID = "787523197"
SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUM = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={}"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 oxtail-lab"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def probe():
    sb = get(SB)
    evs = sb.get("events") or []
    print("events:", len(evs))
    for e in evs:
        c = e["competitions"][0]
        print(e["id"], e.get("shortName"), c["status"]["type"]["state"], c["status"]["type"].get("detail"), [x["team"]["abbreviation"] for x in c["competitors"]])
    live = [e for e in evs if e["competitions"][0]["status"]["type"]["state"] == "in"] or [e for e in evs if e["competitions"][0]["status"]["type"]["state"] == "post"]
    if not live:
        print("no live or finished game to probe"); return
    s = get(SUM.format(live[0]["id"]))
    print("summary keys:", list(s.keys()))
    dr = s.get("drives") or {}
    print("drives keys:", list(dr.keys()))
    cur = dr.get("current") or ((dr.get("previous") or [{}])[-1])
    print("drive keys:", list(cur.keys()))
    for p in (cur.get("plays") or [])[-3:]:
        print("PLAY:", json.dumps(p)[:1500])
    if s.get("scoringPlays"):
        print("SCORING PLAY:", json.dumps(s["scoringPlays"][-1])[:1200])
    bx = s.get("boxscore") or {}
    print("boxscore keys:", list(bx.keys()))
    for tm in (bx.get("players") or [])[:1]:
        print("team:", tm["team"]["abbreviation"])
        for st in tm.get("statistics") or []:
            print("  cat", st.get("name"), st.get("labels"), json.dumps((st.get("athletes") or [{}])[0])[:400])
    print("plays top-level?", "plays" in s, len(s.get("plays") or []))


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
