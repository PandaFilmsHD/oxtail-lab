"""Live plays for Od from Oxtail: watches ESPN's public game feed every 30 seconds during games
and pushes every play by your starters to your phone. Opponent plays only show in the in-app feed.

python scripts/live_plays.py --probe        prints the feed format for the game that's on now (setup/debug)
python scripts/live_plays.py --should-run   prints "yes" if a game with your or your opponent's starters is on or starts within 40 min
python scripts/live_plays.py                runs until none of those games is live or about to start
"""
import json, os, sys, time, urllib.request, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
LID = "787523197"
SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUM = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={}"
RAW = "https://raw.githubusercontent.com/PandaFilmsHD/oxtail-lab/main/"
SITE = "https://pandafilmshd.github.io/oxtail-lab/"
DEF_PLAYS = ("sack", "interception", "fumble recovery (opponent)", "safety", "blocked", "fumble return", "defensive")
TEAM_FIX = {"WAS": "WSH"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 oxtail-lab", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def now():
    return dt.datetime.now(dt.timezone.utc)


def load_lineups():
    """Your starters and your opponent's starters this week: {espn athlete id: info}, plus D/ST teams."""
    try:
        M = get(RAW + f"data/{LID}/model.json"); L = get(RAW + f"docs/data/{LID}/latest.json")
    except Exception:
        M = json.loads((ROOT / "data" / LID / "model.json").read_text()); L = json.loads((ROOT / "docs" / "data" / LID / "latest.json").read_text())
    byid = {p["id"]: p for p in M["players"] if p.get("id") is not None}
    me = M["my_team"]; opp = ((L.get("summary") or {}).get("this_week") or {}).get("opp_id")
    people, dst = {}, {}
    for side, tid in (("me", me), ("opp", opp)):
        rows = ((M.get("live") or {}).get(str(tid)) or {}).get("rows") or []
        for pid, slot, *_ in rows:
            if slot in (20, 21) or pid not in byid:
                continue
            p = byid[pid]
            team = TEAM_FIX.get(p["nfl"], p["nfl"])
            if p["pos"] == "D":
                dst[team] = {"name": p["n"], "side": side}
            else:
                people[str(pid)] = {"name": p["n"], "pos": p["pos"], "team": team, "side": side}
    return people, dst, (L.get("summary") or {}).get("this_week", {}).get("opponent", "opponent")


def relevant_events(sb, teams):
    out = []
    for e in sb.get("events") or []:
        c = e["competitions"][0]
        abbs = {x["team"]["abbreviation"] for x in c["competitors"]}
        if abbs & teams:
            out.append((e, c["status"]["type"]["state"], dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))))
    return out


def all_plays(s):
    dr = s.get("drives") or {}
    drives = list(dr.get("previous") or []) + ([dr["current"]] if dr.get("current") else [])
    for d in drives:
        off = ((d.get("team") or {}).get("abbreviation"))
        for p in d.get("plays") or []:
            yield off, p


def short(name):
    parts = name.split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 and "D/ST" not in name else name


def match(off, p, people, dst, home_away):
    """Which of the tracked players/defenses this play involves."""
    hits = []
    for pa in p.get("participants") or []:
        aid = str(((pa.get("athlete") or {}).get("id")) or "")
        if aid in people:
            hits.append(people[aid])
    tx = ((p.get("type") or {}).get("text") or "").lower()
    if any(k in tx for k in DEF_PLAYS) or p.get("scoringPlay") and "return" in tx:
        for team in home_away:
            if team in dst and team != off:
                hits.append({"name": dst[team]["name"], "pos": "D", "team": team, "side": dst[team]["side"]})
    seen, out = set(), []
    for h in hits:
        if h["name"] not in seen:
            seen.add(h["name"]); out.append(h)
    return out


def should_run():
    people, dst, _ = load_lineups()
    teams = {x["team"] for x in people.values()} | set(dst)
    sb = get(SB)
    for e, state, start in relevant_events(sb, teams):
        if state == "in" or (state == "pre" and (start - now()).total_seconds() < 40 * 60):
            return True
    return False


def run():
    from notify import send
    t0 = time.time()
    people, dst, opp_name = load_lineups()
    seen, last_load, first = set(), time.time(), True
    print(f"watching {len(people)} players and {len(dst)} D/STs")
    while time.time() - t0 < 5.6 * 3600:
        if time.time() - last_load > 600:
            try:
                people, dst, opp_name = load_lineups()
            except Exception as ex:
                print("lineup reload failed", ex)
            last_load = time.time()
        teams = {x["team"] for x in people.values()} | set(dst)
        try:
            evs = relevant_events(get(SB), teams)
        except Exception as ex:
            print("scoreboard failed", ex); time.sleep(30); continue
        live = [e for e, st, _ in evs if st == "in"]
        soon = [s for e, st, s in evs if st == "pre" and (s - now()).total_seconds() < 40 * 60]
        if not live and not soon:
            print("no tracked game live or about to start, stopping"); break
        sent = 0
        for e in live:
            try:
                s = get(SUM.format(e["id"]))
            except Exception as ex:
                print("summary failed", e["id"], ex); continue
            home_away = [x["team"]["abbreviation"] for x in e["competitions"][0]["competitors"]]
            for off, p in all_plays(s):
                pid = p.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                if first:
                    continue   # don't replay everything that already happened when we start
                for h in match(off, p, people, dst, home_away):
                    if h["side"] != "me":
                        continue
                    q = (p.get("period") or {}).get("number"); clk = (p.get("clock") or {}).get("displayValue", "")
                    icon = "🔥" if p.get("scoringPlay") else "🛡️" if h["pos"] == "D" else "🏈"
                    txt = (p.get("text") or "").strip()
                    send(f"{icon} {short(h['name'])}", f"{txt} (Q{q} {clk})", priority=3, click=SITE + "#home", tag=f"play{pid}")
                    sent += 1
        first = False
        if sent:
            print(f"{now():%H:%M:%S} sent {sent}")
        time.sleep(30)


def probe():
    sb = get(SB)
    for e in sb.get("events") or []:
        c = e["competitions"][0]
        print(e["id"], e.get("shortName"), c["status"]["type"]["state"], c["status"]["type"].get("detail"))
    people, dst, _ = load_lineups()
    print("tracking", len(people), "players", sorted(dst), "D/STs:", [v["name"] + " (" + v["side"] + ")" for v in people.values()][:20])
    live = [e for e in sb.get("events") or [] if e["competitions"][0]["status"]["type"]["state"] in ("in", "post")]
    if live:
        s = get(SUM.format(live[0]["id"]))
        ha = [x["team"]["abbreviation"] for x in live[0]["competitions"][0]["competitors"]]
        n = 0
        for off, p in all_plays(s):
            n += 1
        print("plays so far:", n, "last:", [((p.get("type") or {}).get("text"), p.get("text")) for _, p in list(all_plays(s))[-3:]])
        print("should run:", should_run())


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    elif "--should-run" in sys.argv:
        print("yes" if should_run() else "no")
    else:
        run()
