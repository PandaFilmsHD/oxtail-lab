"""Live plays for Od from Oxtail: watches ESPN's public game feed every 30 seconds during games
and pushes every play by your starters to your phone. Opponent plays only show in the in-app feed.

python scripts/live_plays.py --probe        prints the feed format for the game that's on now (setup/debug)
python scripts/live_plays.py --should-run   prints "yes" if a game with your or your opponent's starters is on or starts within 40 min
python scripts/live_plays.py                runs until none of those games is live or about to start
"""
import json, os, re, sys, time, urllib.request, datetime as dt
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
SC = {}
TOT = {}


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
    global SC
    SC = (M.get("settings") or {}).get("scoring") or {}
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
            hits.append({**people[aid], "aid": aid})
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


YDS_RE = re.compile(r"for (-?\d+) yards?|for no gain|(\d+) Yd", re.I)


def play_points(p, h, aid, sc):
    """Fantasy points this play is worth to one tracked player, using the league's own scoring.
    sc: ESPN statId -> points. Returns (points or None if we can't tell, short label)."""
    S = lambda k, d=0.0: float(sc.get(str(k), d) or 0)
    tx = (p.get("text") or ""); tl = tx.lower(); ty = ((p.get("type") or {}).get("text") or "").lower()
    if "no play" in tl or "penalty" in ty:
        return 0.0, "no play"
    m = YDS_RE.search(tx); yds = p.get("statYardage")
    if yds is None:
        yds = int(m.group(1) or m.group(2)) if m and (m.group(1) or m.group(2)) else 0
    td = bool(p.get("scoringPlay")) and "touchdown" in tl
    roles = [str(pa.get("type") or "").lower() for pa in p.get("participants") or [] if str(((pa.get("athlete") or {}).get("id")) or "") == aid]
    role = " ".join(roles)
    if h["pos"] == "D":
        if "sack" in ty or "sack" in tl: return S(99), "sack"
        if "interception" in ty: return S(95) + (S(104, 6) if td else 0), "INT" + (" TD" if td else "")
        if "fumble" in ty and "opponent" in ty: return S(96) + (S(103, 6) if td else 0), "fumble rec"
        if "safety" in ty: return S(98), "safety"
        if "blocked" in ty: return S(97) + (S(93, 6) if td else 0), "block"
        return None, ""
    if h["pos"] == "K" or "kick" in role:
        if "extra point" in tl or "kick is good" in tl or "kick is no good" in tl:
            good = "no good" not in tl
            return (S(86, 1) if good else S(88, -1)), ("XP" if good else "missed XP")
        if "field goal" in ty or "field goal" in tl:
            if "no good" in tl or "missed" in ty or "blocked" in tl: return S(85, -1), "missed FG"
            fm = re.search(r"(\d+)[ -](?:yd|yard)s?[ -]field goal", tx, re.I)
            d_ = int(fm.group(1)) if fm else 30
            return (S(201, 5) if d_ >= 60 else S(198, 4) if d_ >= 50 else S(77, 3) if d_ >= 40 else S(80, 3)), f"{d_} yd FG"
        return None, ""
    pts, lab = 0.0, ""
    lost = "fumble" in tl and ("recovered by" in tl and "fumble recovery (opponent)" in ty)
    if "pass" in role or (h["pos"] == "QB" and ("pass" in ty or "pass" in tl) and "sack" not in ty):
        if "intercept" in ty or "intercepted" in tl: return S(20, -2), "INT"
        if "incomplete" in tl: return 0.0, "incomplete"
        pts = yds * S(3, 0.04) + (S(4, 4) if td else 0); lab = f"{yds} yd pass" + (" TD" if td else "")
    elif "receiv" in role or "target" in role or ("pass" in tl and h["pos"] in ("WR", "TE", "RB")):
        if "incomplete" in tl: return 0.0, "target"
        pts = S(53) + yds * S(42, 0.1) + (S(43, 6) if td else 0); lab = f"{yds} yd catch" + (" TD" if td else "")
    elif "rush" in role or "rush" in ty or "scramble" in tl or "run" in ty:
        pts = yds * S(24, 0.1) + (S(25, 6) if td else 0); lab = f"{yds} yd run" + (" TD" if td else "")
    elif "sack" in ty:
        return 0.0, "sacked"
    else:
        return None, ""
    if lost and "fumbler" in role:
        pts += S(72, -2); lab += ", fumble lost"
    return round(pts, 2), lab


def clean(t):
    t = re.sub(r"\((?:Shotgun|No Huddle|No Huddle, Shotgun)\)\s*", "", t or "", flags=re.I)
    t = re.sub(r"\s*\([^()]*\)\.?\s*$", ".", t).replace("..", ".")
    return t.strip()


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
    global TOT
    TOT = {}
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
                for h in match(off, p, people, dst, home_away):
                    pts, lab = play_points(p, h, h.get("aid", ""), SC)
                    if pts:
                        TOT[h["name"]] = TOT.get(h["name"], 0) + pts
                    if first or h["side"] != "me":
                        continue
                    if h["pos"] == "QB":   # QBs: only the big stuff, or the phone never stops buzzing
                        m_ = YDS_RE.search(p.get("text") or ""); y_ = p.get("statYardage")
                        y_ = y_ if y_ is not None else (int(m_.group(1) or m_.group(2)) if m_ and (m_.group(1) or m_.group(2)) else 0)
                        run_ = "run" in (lab or "")
                        if not (p.get("scoringPlay") or (pts is not None and pts < 0) or y_ >= (15 if run_ else 20)):
                            continue
                    q = (p.get("period") or {}).get("number"); clk = (p.get("clock") or {}).get("displayValue", "")
                    icon = "🔥" if p.get("scoringPlay") else "🛡️" if h["pos"] == "D" else "🏈"
                    txt = (p.get("text") or "").strip()
                    last = h["name"].split()[-1] if h["pos"] != "D" else h["name"]
                    title = f"{icon} {last}" + (f" {lab}" if lab else "") + (f"  {pts:+.1f}" if pts else "")
                    body = f"{clean(txt)}\nQ{q} {clk} · {last} {TOT.get(h['name'], 0):.1f} pts today · {e.get('shortName', '')}"
                    send(title, body, priority=3, click=SITE + "#home", tag=f"play{pid}")
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


def dry():
    """One pass over every play so far in live games: show what would be pushed, send nothing."""
    people, dst, _ = load_lineups()
    teams = {x["team"] for x in people.values()} | set(dst)
    evs = relevant_events(get(SB), teams)
    print("teams", sorted(teams), "events", [(e["shortName"], st) for e, st, _ in evs])
    for e, st, _ in evs:
        if st == "pre":
            continue
        s = get(SUM.format(e["id"])); ha = [x["team"]["abbreviation"] for x in e["competitions"][0]["competitors"]]
        n = 0
        for off, p in all_plays(s):
            n += 1
            for h in match(off, p, people, dst, ha):
                pts, lab = play_points(p, h, h.get("aid", ""), SC)
                print(h["side"], h["name"], "|", lab, pts, "|", clean(p.get("text"))[:90])
        print(e["shortName"], "plays:", n)
    from notify import send
    print("push test:", send("🏈 Oxtail Lab test", "If you see this, play pushes can reach your phone.", click=SITE + "#home", tag="livetest"))


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    elif "--dry" in sys.argv:
        dry()
    elif "--should-run" in sys.argv:
        print("yes" if should_run() else "no")
    else:
        run()
