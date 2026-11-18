"""Phone alerts for Alex's players across all leagues, each with a short report that opens in the app.

Runs after every update. Never repeats an alert (state in data/alert_state.json).
Reports live in docs/data/alerts.json; tapping an alert opens ?alert=<id>.
"""
import json, pathlib, re, time, hashlib, datetime, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from notify import send, SITE

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_P = ROOT / "data" / "alert_state.json"
OUT_P = ROOT / "docs" / "data" / "alerts.json"
STATE = json.loads(STATE_P.read_text()) if STATE_P.exists() else {}
FIRST_RUN = not STATE
STATE.setdefault("sent", {}); STATE.setdefault("inj", {}); STATE.setdefault("news", {})
FEED = json.loads(OUT_P.read_text()).get("alerts", []) if OUT_P.exists() else []
NOW = datetime.datetime.now(datetime.timezone.utc)
STATUS = {"OUT": "OUT", "INJURY_RESERVE": "on IR", "DOUBTFUL": "DOUBTFUL", "QUESTIONABLE": "QUESTIONABLE", "SUSPENSION": "SUSPENDED", "ACTIVE": "cleared (active)"}
BAD = ("OUT", "INJURY_RESERVE", "DOUBTFUL", "SUSPENSION")
NEWSY = re.compile(r"(injur|question|doubtful|ruled out|won't return|will not return|return|locker room|carted|limp|x-ray|mri|concussion|ankle|knee|hamstring|groin|shoulder|foot|toe|back|ribs|illness|did not practice|limited|full practice|inactive|active|benched|starting|start|placed on|activated|designated|surgery|season)", re.I)
alerts = []


def first_sentence(t, n=110):
    t = re.sub(r"\s+", " ", t or "").strip()
    s = re.split(r"(?<=[.!?])\s", t)[0] if t else ""
    return (s[: n - 1] + "…") if len(s) > n else s


def fire(key, title, body, report, player=None, leagues=(), actions=(), priority=3):
    if key in STATE["sent"]:
        return
    STATE["sent"][key] = NOW.isoformat()
    if FIRST_RUN:
        return   # first run: just remember what's already true, don't flood the phone or the feed
    aid = hashlib.sha1(key.encode()).hexdigest()[:10]
    alerts.append({"id": aid, "at": NOW.isoformat(), "title": title, "body": body, "player": player, "leagues": list(leagues), "report": report, "actions": list(actions)})
    if True:
        send(title, body, priority=priority, click=f"{SITE}?alert={aid}", tag=aid)


leagues = json.loads((ROOT / "docs" / "data" / "leagues.json").read_text()).get("leagues", [])
mine = {}   # player name -> info across leagues
for L in leagues:
    lid = L["id"]
    mp, lp, cp = ROOT / "data" / lid / "model.json", ROOT / "docs" / "data" / lid / "latest.json", ROOT / "docs" / "data" / lid / "players.json"
    if not (mp.exists() and lp.exists()):
        continue
    M, D = json.loads(mp.read_text()), json.loads(lp.read_text())
    C = json.loads(cp.read_text()) if cp.exists() else {}
    me, cur = M["my_team"], M["cur"]
    T = D["tester"]; wi = T["weeks"].index(cur) if cur in T["weeks"] else 0
    tp = {p["n"]: p for p in T["players"]}
    name = (L.get("name") or "").strip()
    roster = [p for p in M["players"] if p["t"] == me]
    for p in roster:
        mine.setdefault(p["n"], {"p": p, "leagues": [], "card": C.get(p["n"]), "slots": []})
        mine[p["n"]]["leagues"].append((lid, name)); mine[p["n"]]["slots"].append((name, p.get("slot")))

    # ---- lineup check: what's actually set in ESPN right now ----
    if not any(p.get("slot") for p in roster):
        continue   # no lineup info in this pull
    starters = [p for p in roster if p.get("slot") not in (None, "BE", "IR")]
    bench = [p for p in roster if p.get("slot") == "BE"]
    sched = next((r for r in D.get("schedule", []) if r["w"] == cur), None) or {}
    byes = set(sched.get("byes") or [])
    val = lambda n: (tp.get(n) or {}).get("e", [0] * 99)[wi] if n in tp else 0
    full = lambda n: (tp.get(n) or {}).get("v", [0] * 99)[wi] if n in tp else 0
    elig = T.get("elig", {})
    for s in starters:
        prob = "on bye" if s["n"] in byes else STATUS.get(s["inj"]) if s["inj"] in BAD else None
        if not prob:
            continue
        alts = sorted([b for b in bench if b["pos"] in elig.get(s["slot"], [s["pos"]]) and b["n"] not in byes and b["inj"] not in BAD], key=lambda b: -val(b["n"]))
        fa = next((w for w in D.get("waivers", []) if w["pos"] == s["pos"] and w["this_week_gain"] > 0), None)
        fix = f"Start {alts[0]['n']} ({val(alts[0]['n']):.1f})" if alts else f"Add {fa['player']}, drop {fa['drop']}" if fa else "Find a replacement on waivers"
        fire(f"lineup|{lid}|{cur}|{s['n']}|{prob}", f"⚠️ {s['n']} is {prob}", f"He's in your {name} lineup. {fix}.",
             {"what": f"{s['n']} ({s['pos']}) is {prob} for Week {cur} but he's still in your starting lineup in {name}.",
              "means": "He'll score 0 if you leave him there." if prob != "QUESTIONABLE" else "",
              "todo": fix + "." + (f" {fa['why']}" if fa and not alts else "")},
             s["n"], [name], [{"label": "OPEN LINEUP", "tab": "home", "league": lid}], 5)
    # empty slots
    need = {k: int(v) for k, v in (M["settings"].get("slots") or {}).items() if k not in ("BE", "IR")}
    have = {}
    for s in starters:
        have[s["slot"]] = have.get(s["slot"], 0) + 1
    for k, n_ in need.items():
        if have.get(k, 0) < n_:
            fire(f"empty|{lid}|{cur}|{k}", f"⚠️ Empty {k} spot in {name}", f"You have no {k} set for Week {cur}.",
                 {"what": f"Your {k} slot is empty in {name}.", "means": "An empty slot scores 0.", "todo": "Set someone there before his game starts."},
                 None, [name], [{"label": "OPEN LINEUP", "tab": "home", "league": lid}], 5)
    # start/sit: a bench guy clearly better than a starter (3+ pts, healthy)
    for s in starters:
        if s["n"] in byes or s["inj"] in BAD:
            continue
        better = [b for b in bench if b["pos"] in elig.get(s["slot"], [s["pos"]]) and b["n"] not in byes and b["inj"] not in BAD and val(b["n"]) >= val(s["n"]) + 3]
        if better:
            b = max(better, key=lambda b: val(b["n"]))
            fire(f"swap|{lid}|{cur}|{s['n']}|{b['n']}", f"🔁 Start {b['n']} over {s['n']}", f"{name}: +{val(b['n']) - val(s['n']):.1f} pts projected.",
                 {"what": f"In {name}, {b['n']} is on your bench and projects {full(b['n']):.1f} this week. {s['n']} is starting at {s['slot']} and projects {full(s['n']):.1f}.",
                  "means": f"That's about {val(b['n']) - val(s['n']):.1f} more expected points.", "todo": f"Swap them unless you know something we don't."},
                 b["n"], [name], [{"label": "OPEN LINEUP", "tab": "home", "league": lid}], 4)

# ---- injury status changes + fresh news for any of my players (one alert per player across leagues) ----
for n, x in mine.items():
    p, card = x["p"], x["card"] or {}
    pid = str(p["id"]); old = STATE["inj"].get(pid); new = p.get("inj") or "ACTIVE"
    lnames = sorted({l for _, l in x["leagues"]})
    nw = p.get("news") or {}
    news_txt = (nw.get("text") or "").strip()
    avail = next((r["text"] for r in card.get("reasons", []) if r.get("icon") == "🩺"), "")
    starting = [l for l, sl in x["slots"] if sl not in (None, "BE", "IR")]
    if old is not None and old != new and p["pos"] != "D":
        good = new == "ACTIVE"
        fire(f"inj|{pid}|{old}|{new}|{nw.get('date')}", f"{'✅' if good else '🚨'} {n} {STATUS.get(new, new)}",
             (first_sentence(news_txt, 100) or f"Was {STATUS.get(old, old)}.") + (f" Starting in {', '.join(starting)}." if starting and not good else ""),
             {"what": news_txt or f"{n} went from {STATUS.get(old, old)} to {STATUS.get(new, new)}.",
              "means": avail or ("He's good to go." if good else ""),
              "todo": ("Keep him in your lineup." if good else f"Have a backup ready in {', '.join(starting)}." if starting else "He's on your bench, no lineup change needed.")},
             n, lnames, [{"label": "PLAYER CARD", "player": n}], 5 if (not good and starting) else 3)
    STATE["inj"][pid] = new
    d = nw.get("date")
    if d and news_txt and STATE["news"].get(pid) != d:
        if STATE["news"].get(pid) is not None and NEWSY.search(news_txt) and (starting or new != "ACTIVE"):
            fire(f"news|{pid}|{d}", f"📰 {n}", first_sentence(news_txt, 110),
                 {"what": news_txt, "means": avail, "todo": (f"He's in your lineup in {', '.join(starting)}. Check his status before kickoff." if starting else "")},
                 n, lnames, [{"label": "PLAYER CARD", "player": n}], 4 if starting else 3)
        STATE["news"][pid] = d

# ---- Prop Lab: injury news that makes a line stale, plus the weekly digest ----
PLB = ROOT / "docs" / "data" / "props_lab.json"
if PLB.exists():
    for a_ in (json.loads(PLB.read_text()).get("alerts") or [])[:4]:
        fire(a_["key"], a_["title"], a_["body"], a_["report"], None, (), [{"label": "PROP LAB", "tab": "props"}], 4 if a_["key"].startswith("prop|") else 3)

# keep things tidy
cut = (NOW - datetime.timedelta(days=21)).isoformat()
STATE["sent"] = {k: v for k, v in STATE["sent"].items() if v >= cut}
FEED = (alerts + FEED)[:60]
OUT_P.write_text(json.dumps({"alerts": FEED}, separators=(",", ":")))
STATE_P.write_text(json.dumps(STATE, separators=(",", ":")))
print(f"alerts: {len(alerts)} new{' (first run: recorded, not sent)' if FIRST_RUN else ''}")
