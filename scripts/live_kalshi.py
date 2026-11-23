"""⚡ Live Kalshi watcher: during NFL games, compares Kalshi's live player prices to a simple in-game projection.

For each player market in a live game:
  what he has so far (ESPN box score) + what he should still get in the time left
  (his pregame projection x share of the game left, nudged a little toward how he's actually being used today),
  with the spread of outcomes that shrinks as the game goes on.
Guards (the traps in live betting):
  skip anyone who looks hurt (injury in the play-by-play) or hasn't been involved in 12+ game minutes,
  skip blowouts (17+ points in the second half) and the last 5 minutes, and only flag big gaps (10%+ edge).
Tracked only for now: posts to DISCORD_LIVE and saves every pick to data/live_bets.json, graded by post_discord.py.

Run: python scripts/live_kalshi.py            (loop, until games end)
     python scripts/live_kalshi.py --should-run (prints yes when a game is on or about to start)
     python scripts/live_kalshi.py --dry        (one pass, print what it would flag, no posting)
"""
import datetime, json, math, os, re, subprocess, sys, time
from pathlib import Path
import requests
from scipy.stats import gamma, nbinom, poisson

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
from live_plays import all_plays, abbr, get, SB, SUM   # same ESPN feeds the fantasy play watcher uses
from pull_kalshi import teams_of, FIX as KFIX, API as KAPI

HOOK = os.environ.get("DISCORD_LIVE", "").strip()
RAWP = "https://raw.githubusercontent.com/PandaFilmsHD/oxtail-lab/main/docs/data/props_lab.json"
SERIES = {"KXNFLPASSYDS": "pass_yds", "KXNFLRSHYDS": "rush_yds", "KXNFLRECYDS": "rec_yds", "KXNFLREC": "rec", "KXNFLPASSTDS": "pass_tds", "KXNFLANYTD": "td"}
LABEL = {"pass_yds": "pass yds", "rush_yds": "rush yds", "rec_yds": "rec yds", "rec": "catches", "pass_tds": "pass TDs", "td": "anytime TD"}
CV = {"pass_yds": 0.35, "rush_yds": 0.70, "rec_yds": 0.80}   # full-game spread for yards; counts use NB / Poisson
EDGE, MAX_GAME, IDLE_MIN = 0.10, 6, 12
EFIX = {"WSH": "WAS", "LAR": "LA", "JAC": "JAX"}            # ESPN / Kalshi -> one spelling
NK = lambda n: re.sub(r"[^a-z]", "", re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", (n or "").lower()))


def nowu():
    return datetime.datetime.now(datetime.timezone.utc)


def kfee(c):
    return math.ceil(100 * 0.07 * c * (1 - c)) / 100


def live_games():
    """ESPN games in progress: id, teams, period, seconds left in period, score."""
    out = []
    for e in get(SB).get("events", []):
        c = e["competitions"][0]; st = c["status"]
        if st["type"].get("state") != "in":
            continue
        tm = {t["homeAway"]: t for t in c["competitors"]}
        out.append({"id": e["id"], "home": EFIX.get(tm["home"]["team"]["abbreviation"], tm["home"]["team"]["abbreviation"]),
                    "away": EFIX.get(tm["away"]["team"]["abbreviation"], tm["away"]["team"]["abbreviation"]),
                    "hs": int(tm["home"].get("score") or 0), "as": int(tm["away"].get("score") or 0),
                    "period": int(st.get("period") or 1), "clock": float(st.get("clock") or 0), "short": e.get("shortName", ""),
                    "half": st["type"].get("name") == "STATUS_HALFTIME"})
    return out


def frac_left(g):
    if g["period"] > 4:
        return 0.0
    return max(0.0, ((4 - g["period"]) * 900 + g["clock"]) / 3600)


def box(s):
    """Player stats so far from the ESPN summary: name key -> {stat: value}."""
    out = {}
    for t in (s.get("boxscore") or {}).get("players", []):
        for cat in t.get("statistics", []):
            keys = cat.get("keys") or []
            for a in cat.get("athletes", []):
                nm = (a.get("athlete") or {}).get("displayName"); v = dict(zip(keys, a.get("stats") or []))
                o = out.setdefault(NK(nm), {"name": nm})
                num = lambda k: float(str(v.get(k, "0")).split("/")[0] or 0) if v.get(k) not in (None, "--") else 0.0
                if cat.get("name") == "passing":
                    o["pass_yds"] = num("passingYards"); o["pass_tds"] = num("passingTouchdowns")
                elif cat.get("name") == "rushing":
                    o["rush_yds"] = num("rushingYards"); o["rush_td"] = num("rushingTouchdowns"); o["car"] = num("rushingAttempts")
                elif cat.get("name") == "receiving":
                    o["rec"] = num("receptions"); o["rec_yds"] = num("receivingYards"); o["rec_td"] = num("receivingTouchdowns")
    for o in out.values():
        o["td"] = o.get("rush_td", 0) + o.get("rec_td", 0)
    return out


def involvement(s):
    """Every play with its game-second, so we can tell when a player last touched the ball or got hurt."""
    out = []
    for _, p in all_plays(s):
        tx = p.get("text") or ""
        per = (p.get("period") or {}).get("number") or 1
        try:
            mm, ss = (p.get("clock") or {}).get("displayValue", "15:00").split(":")
            out.append(((per - 1) * 900 + (900 - (int(mm) * 60 + int(ss))), tx))
        except Exception:
            continue
    return out


def last_touch(plays, tag):
    return max((t for t, tx in plays if tag in tx), default=-1e9)


def hurt(plays, tag):
    return any(tag in tx and "injur" in tx.lower() for _, tx in plays)


def p_over(stat, mean_left, need):
    """Chance he gets more than `need` in the time left."""
    if need < 0:
        return 1.0
    if stat in CV:
        return float(gamma.sf(need, a=max(0.05, (1 / CV[stat] ** 2) * F_LEFT), scale=max(1e-6, mean_left) / max(0.05, (1 / CV[stat] ** 2) * F_LEFT)))
    k = math.floor(need) + 1                       # needs at least k more
    if stat == "rec":
        r = max(0.5, 8 * F_LEFT)
        return float(nbinom.sf(k - 1, r, r / (r + max(1e-6, mean_left))))
    return float(poisson.sf(k - 1, max(1e-6, mean_left)))


F_LEFT = 1.0


def kalshi_markets(teams):
    """Open Kalshi player markets for the live games."""
    out = []
    for ser, stat in SERIES.items():
        cur = ""
        for _ in range(5):
            try:
                j = requests.get(f"{KAPI}/markets", params={"series_ticker": ser, "status": "open", "limit": 1000, **({"cursor": cur} if cur else {})}, timeout=20).json()
            except Exception as ex:
                print("kalshi", ser, type(ex).__name__); break
            for m in j.get("markets", []):
                a, h = teams_of(m["event_ticker"])
                a, h = EFIX.get(KFIX.get(a, a), KFIX.get(a, a)), EFIX.get(KFIX.get(h, h), KFIX.get(h, h))
                if (a, h) not in teams:
                    continue
                f_ = lambda x: float(x) if x not in (None, "") else None
                out.append({"stat": stat, "player": (m.get("title") or "").split(":")[0].strip(), "strike": m.get("floor_strike"), "ticker": m["ticker"],
                            "ask": f_(m.get("yes_ask_dollars")), "no_ask": f_(m.get("no_ask_dollars")), "game": (a, h)})
            cur = j.get("cursor") or ""
            if not cur:
                break
            time.sleep(0.15)
    return out


def scan(PL, seen, per_game, dry=False):
    global F_LEFT
    games = live_games()
    if not games:
        return []
    proj = {NK(k): v for k, v in (PL.get("proj") or {}).items()}
    mk = kalshi_markets({(g["away"], g["home"]) for g in games})
    picks = []
    for g in games:
        f = frac_left(g); F_LEFT = max(f, 1e-3)
        mar = g["hs"] - g["as"]
        if f < 5 / 60 or (g["period"] >= 3 and abs(mar) > 17):
            continue
        try:
            s = get(SUM.format(g["id"]))
        except Exception as ex:
            print("summary failed", ex); continue
        B, PLAYS = box(s), involvement(s)
        elapsed = (1 - f) * 3600
        for m in [x for x in mk if x["game"] == (g["away"], g["home"])]:
            k = NK(m["player"]); pj = proj.get(k); st = m["stat"]
            if not pj or st not in pj or m["strike"] is None:
                continue
            tag = abbr(m["player"])
            if hurt(PLAYS, tag):
                continue
            if pj.get("pos") != "QB" and elapsed > 10 * 60 and (elapsed - last_touch(PLAYS, tag)) > IDLE_MIN * 60:
                continue   # not involved lately: hurt, benched, or inactive
            cur = (B.get(k) or {}).get(st, 0.0)
            need = m["strike"] - cur
            if need < 0:
                continue   # already over: Kalshi will settle it
            m_full = pj[st]
            pace = cur / max(0.15, 1 - f)                                  # what he's on pace for today
            m_left = f * (0.75 * m_full + 0.25 * min(pace, 2 * m_full))
            py = p_over(st, m_left, need)
            for side, pp, c in (("Yes", py, m["ask"]), ("No", 1 - py, m["no_ask"])):
                if not c or not 0.05 <= c <= 0.92:
                    continue
                cost = c + kfee(c); edge = pp - c; ev = pp / cost - 1
                key = f"{m['ticker']}|{side}"
                if edge < EDGE or ev <= 0 or key in seen or per_game.get(g["id"], 0) >= MAX_GAME:
                    continue
                if any(x.startswith(m["ticker"].rsplit("-", 1)[0]) for x in seen):   # one bet per player per stat per game
                    continue
                n_ = int(round(m["strike"] + 0.5))
                label = f"{m['player']} {n_}+ {LABEL[st]}" if side == "Yes" else f"{m['player']} under {n_} {LABEL[st]}"
                pick = {"ticker": m["ticker"], "side": side, "label": label, "player": m["player"], "team": pj.get("team"), "stat": st, "strike": m["strike"],
                        "cost": round(100 * c), "fee": round(100 * kfee(c), 1), "p": round(100 * pp, 1), "edge": round(100 * edge, 1), "ev": round(100 * ev, 1),
                        "had": cur, "proj_full": round(m_full, 1), "proj_left": round(m_left, 1), "q": g["period"], "clock": f"{int(g['clock'] // 60)}:{int(g['clock'] % 60):02d}",
                        "score": f"{g['away']} {g['as']}, {g['home']} {g['hs']}", "game": g["short"], "week": PL.get("week"), "at": nowu().isoformat(timespec="seconds"),
                        "date": nowu().astimezone(datetime.timezone(datetime.timedelta(hours=-8))).strftime("%Y-%m-%d")}
                picks.append(pick); seen.add(key); per_game[g["id"]] = per_game.get(g["id"], 0) + 1
                if not dry:
                    post(pick)
    return picks


def post(x):
    unit = LABEL[x["stat"]]
    desc = (f"**{x['score']}** · Q{x['q']} {x['clock']}\n"
            f"{x['player'].split()[-1]} has **{x['had']:g} {unit}** so far. Pregame we had him at {x['proj_full']:g}; for the time left we expect about **{x['proj_left']:g} more**.\n"
            f"That makes it **{round(x['p'])}%**, but Kalshi is selling it at **{x['cost']}¢**: **+{x['edge']}% edge**, +{x['ev']}% EV.\n\n"
            f"👉 Buy **{x['side']}** at **{x['cost']}¢** or less.")
    e = {"title": f"⚡ Live: {x['label']}", "description": desc, "color": 0xFFD23D,
         "footer": {"text": "Tracked only for now (testing the live model). Graded in #results under ⚡ Live."}}
    if not HOOK:
        print("would post:", x["label"], x["cost"], x["p"]); return
    try:
        r = requests.post(HOOK + "?wait=true", json={"embeds": [e], "allowed_mentions": {"parse": []}}, timeout=20)
        print("discord live", r.status_code, x["label"])
    except Exception as ex:
        print("discord live failed", type(ex).__name__)


def save(picks):
    if not picks:
        return
    p = D / "live_bets.json"
    old = json.loads(p.read_text()) if p.exists() else []
    old += picks
    p.write_text(json.dumps(old, indent=1))
    for i in range(4):
        r = subprocess.run("git add data/live_bets.json && (git diff --cached --quiet || git commit -qm 'live bets') && git pull -q --rebase -X theirs origin main && git push -q", shell=True)
        if r.returncode == 0:
            return
        time.sleep(5 * (i + 1))


def load_pl():
    try:
        return requests.get(RAWP, timeout=30).json()
    except Exception:
        return json.loads((ROOT / "docs" / "data" / "props_lab.json").read_text())


def should_run():
    try:
        for e in get(SB).get("events", []):
            st = e["competitions"][0]["status"]["type"].get("state")
            kick = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
            if st == "in" or (st == "pre" and 0 < (kick - nowu()).total_seconds() < 20 * 60):
                return True
    except Exception:
        pass
    return False


def run():
    t0, PL, loaded = time.time(), load_pl(), time.time()
    seen, per_game, buf, last_save, idle = set(), {}, [], time.time(), 0
    for x in (json.loads((D / "live_bets.json").read_text()) if (D / "live_bets.json").exists() else []):
        if x.get("date") == nowu().astimezone(datetime.timezone(datetime.timedelta(hours=-8))).strftime("%Y-%m-%d"):
            seen.add(f"{x['ticker']}|{x['side']}")
    while time.time() - t0 < 5.6 * 3600:
        if time.time() - loaded > 1800:
            PL, loaded = load_pl(), time.time()
        try:
            got = scan(PL, seen, per_game)
            buf += got
            idle = 0 if live_games() else idle + 1
        except Exception as ex:
            print("scan failed", type(ex).__name__, ex)
        if buf and time.time() - last_save > 300:
            save(buf); buf, last_save = [], time.time()
        if idle >= 10 and not should_run():
            break
        time.sleep(75)
    save(buf)


if __name__ == "__main__":
    if "--should-run" in sys.argv:
        print("yes" if should_run() else "no")
    elif "--dry" in sys.argv:
        for x in scan(load_pl(), set(), {}, dry=True):
            print(x)
    else:
        run()
