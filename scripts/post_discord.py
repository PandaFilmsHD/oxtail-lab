"""Posts Oxtail Lab picks to Discord through channel webhooks, with receipts so every pick is provably posted before kickoff.

Channels (GitHub secrets hold the webhook links, never printed):
  DISCORD_BEST      top plays per kickoff window, about 90 min before kickoff (strict: thin edges are skipped)
  DISCORD_PARLAYS   top parlays once per game day, before the first kickoff of the day
  DISCORD_KALSHI    top Kalshi buys per kickoff window
  DISCORD_RESULTS   a win or loss for every posted pick as it grades, plus a daily recap
  DISCORD_RECEIPTS  every pick the model locks (props, game lines, Kalshi), as a file + fingerprint, before kickoff

Run:  python scripts/post_discord.py          (normal, called by update.yml)
      python scripts/post_discord.py --test   (one hello per channel)
State lives in data/discord_state.json so nothing posts twice.
"""
import csv, datetime, hashlib, io, json, os, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
PT = ZoneInfo("America/Los_Angeles")
UNIT = 25.0
SITE = "https://pandafilmshd.github.io/oxtail-lab/"
HOOK = {c: os.environ.get(f"DISCORD_{c}", "").strip() for c in ("BEST", "PARLAYS", "KALSHI", "RESULTS", "RECEIPTS")}
LEAD_MIN = 100          # post a window this many minutes before kickoff (runs are 10 to 30 min apart)
MAX_BEST, MAX_KAL = 5, 5
PROP_EDGE, GAME_EDGE, KAL_EDGE = 3.5, 2.5, 4.0   # strict: below this it doesn't make the channel
COLORS = dict(best=0x5B7CFF, parlay=0xFFC83D, kalshi=0x3DDC84, win=0x3DDC84, loss=0xFF5A6E, receipt=0x8A8FB8, push=0x8A8FB8)


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def pt(s, fmt="%a %-I:%M %p"):
    return iso(s).astimezone(PT).strftime(fmt)


def odds(p):
    p = max(min(p / 100, 0.999), 0.001)
    return f"-{round(100 * p / (1 - p))}" if p >= 0.5 else f"+{round(100 * (1 - p) / p)}"


def am(price):
    return "" if price is None else (f"+{price}" if price > 0 else str(price))


def win_units(price):
    price = price if price else -110
    return price / 100 if price > 0 else 100 / -price


def usd(u):
    v = u * UNIT
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}".replace(".00", "")


def post(ch, content=None, embeds=None, file=None):
    """Send to one channel. Returns True on success. Quietly skips channels with no webhook set."""
    url = HOOK.get(ch)
    if not url or not url.startswith("https://"):
        print(f"discord: {ch} webhook not set, skipping")
        return False
    body = {"allowed_mentions": {"parse": []}}
    if content:
        body["content"] = content[:1990]
    if embeds:
        body["embeds"] = embeds[:10]
    for attempt in range(3):
        try:
            if file:
                r = requests.post(url + "?wait=true", data={"payload_json": json.dumps(body)}, files={"files[0]": file}, timeout=30)
            else:
                r = requests.post(url + "?wait=true", json=body, timeout=30)
        except Exception as e:
            print("discord:", ch, "error", type(e).__name__); time.sleep(2); continue
        if r.status_code == 429:
            time.sleep(float((r.json() or {}).get("retry_after", 2)) + 0.5); continue
        print("discord:", ch, r.status_code)
        if r.ok:
            time.sleep(0.6)
            return True
        return False
    return False


def load(p, d):
    try:
        return json.loads(p.read_text())
    except Exception:
        return d


# ---------- picks ----------
def prop_text(x):
    if x["market"] == "Anytime TD":
        return f"{x['player']} anytime TD"
    return f"{x['player']} {x['side']} {x['line']} {x['market'].lower()}"


def week_picks(PK, wk):
    """Every official pick this week: model props (key 'Player|Market'), game lines (G|...), Kalshi buys (KA|...)."""
    out = {}
    for k, x in (PK.get(str(wk)) or {}).items():
        if k.startswith(("PA|", "GA|")) or x.get("auto"):
            continue
        if k.startswith("KA|"):
            out[k] = dict(kind="kalshi", text=x.get("label") or "?", price=f"{x.get('cost')}¢", p=x.get("p"), edge=x.get("edge"), kick=x.get("kick"), at=x.get("at"), raw=x)
        elif k.startswith("G|"):
            out[k] = dict(kind="game", text=f"{x.get('game')}: {x.get('pick')}", price=am(x.get("price")) or "-110", p=x.get("p"), edge=x.get("edge"), kick=x.get("kick"), at=x.get("at"), raw=x)
        elif x.get("player") and x.get("market"):
            out[k] = dict(kind="prop", text=prop_text(x), price=am(x.get("price")), p=x.get("p"), edge=x.get("edge"), kick=x.get("kick"), at=x.get("at"), raw=x)
    return out


def receipts(S, PK, wk, now):
    """Post every newly locked pick (kickoff still ahead) as one file + fingerprint. At most hourly unless a kickoff is close."""
    picks = week_picks(PK, wk)
    sent = set(S.setdefault("receipted", []))
    new = {k: v for k, v in picks.items() if k not in sent and v["kick"] and iso(v["kick"]) > now}
    if not new:
        return
    soon = min(iso(v["kick"]) for v in new.values()) - now < datetime.timedelta(minutes=LEAD_MIN + 20)
    last = S.get("receipt_at")
    if last and now - iso(last) < datetime.timedelta(minutes=60) and not soon:
        return
    rows = sorted(new.items(), key=lambda kv: (kv[1]["kick"], kv[1]["kind"], kv[1]["text"]))
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["week", "kickoff_utc", "type", "pick", "price", "model_pct", "edge", "locked_at_utc", "id"])
    for k, v in rows:
        w.writerow([wk, v["kick"], v["kind"], v["text"], v["price"], v["p"], v["edge"], v.get("at") or now.isoformat(timespec="seconds"), k])
    data = buf.getvalue().encode()
    fp = hashlib.sha256(data).hexdigest()
    n = {t: sum(1 for _, v in rows if v["kind"] == t) for t in ("prop", "game", "kalshi")}
    first, lastk = pt(rows[0][1]["kick"]), pt(rows[-1][1]["kick"])
    e = {"title": f"🧾 Week {wk}: {len(rows)} picks locked", "color": COLORS["receipt"],
         "description": f"{n['prop']} props · {n['game']} game lines · {n['kalshi']} Kalshi buys\nKickoffs {first} to {lastk} PT\n\nFingerprint (SHA-256 of the attached file):\n`{fp}`\n\nThe file can't be edited after it's posted. Anyone can re-hash it to check.",
         "footer": {"text": "Every pick is graded at the price shown, wins and losses."}}
    if post("RECEIPTS", embeds=[e], file=(f"oxtail_wk{wk}_{now.strftime('%m%d_%H%M')}.csv", data, "text/csv")):
        S["receipted"] = sorted(sent | set(new))
        S["receipt_at"] = now.isoformat()
        S.setdefault("fingerprints", []).append({"at": now.isoformat(), "n": len(rows), "sha256": fp})


def windows(PLD, now):
    """Kickoff times about to start (inside the posting lead)."""
    ks = sorted({g["kick"] for g in PLD.get("games", []) if g.get("kick")})
    return [k for k in ks if datetime.timedelta(0) < iso(k) - now <= datetime.timedelta(minutes=LEAD_MIN)]


def best_bets(S, PLD, PK, wk, now):
    posted = S.setdefault("posted", {})
    for k in windows(PLD, now):
        if S.setdefault("best_done", {}).get(k):
            continue
        games = [g for g in PLD["games"] if g["kick"] == k]
        names = ", ".join(f"{g['away']} @ {g['home']}" for g in games)
        cands = []
        for g in games:
            for pl in g.get("plays") or []:
                if (pl.get("units") or 0) > 0 and (pl.get("edge") or 0) >= GAME_EDGE and pl.get("pick"):
                    side = pl["pick"].split()[0] if pl["kind"] == "tot" else pl["pick"].split()[0]
                    key = f"G|{g['home']}|{pl['kind']}|{side}"
                    auto = next((a for a in g.get("auto") or [] if a.get("kind") == pl["kind"]), {})
                    cands.append(dict(id=key, kind="game", icon="🏈", text=f"{g['away']} @ {g['home']}: {pl['pick']}", price=auto.get("price") or -110,
                                      p=pl["p"], edge=pl["edge"], why=(pl.get("why") or [])[:2], units=pl.get("units") or 0.5))
        for x in PLD.get("props") or []:
            if x.get("kick") == k and ((x.get("edge") or 0) >= PROP_EDGE or x.get("grade") in ("A", "B")):
                cands.append(dict(id=f"{x['player']}|{x['market']}", kind="prop", icon="👤", text=prop_text(x), price=x.get("price"),
                                  p=x["p"], edge=x["edge"], why=[w for w in (x.get("why") or []) if w.startswith(("🧮", "⬆️", "⬇️"))][:2], units=1 if x.get("grade") in ("A", "B") else 0.5))
        cands.sort(key=lambda c: -(c["edge"] or 0))
        cands = cands[:MAX_BEST]
        if cands:
            lines = []
            for c in cands:
                lines.append(f"{c['icon']} **{c['text']}** ({am(c['price'])})\n> {round(c['p'])}% in 50,000 sims · edge {c['edge']} · {c['units']}u ({usd(c['units'])[1:]})" +
                             "".join(f"\n> {w}" for w in c["why"]))
            e = {"title": f"🎯 Best bets · {pt(k)} PT", "description": f"*{names}*\n\n" + "\n\n".join(lines), "color": COLORS["best"],
                 "footer": {"text": f"Only take it at this price or better. 1u = ${UNIT:.0f}. Receipts in #receipts."}, "url": SITE + "#lab"}
        else:
            e = {"title": f"🎯 Best bets · {pt(k)} PT", "description": f"*{names}*\n\nNothing strong enough this slate. Passing is a pick too. 🧘", "color": COLORS["push"]}
        if post("BEST", embeds=[e]):
            S["best_done"][k] = now.isoformat()
            for c in cands:
                posted[c["id"]] = dict(week=str(wk), ch="best", text=c["text"], price=c["price"], units=c["units"], kick=k, at=now.isoformat())


def kalshi(S, PLD, wk, now):
    posted = S.setdefault("posted", {})
    K = PLD.get("kalshi") or {}
    for k in windows(PLD, now):
        if S.setdefault("kal_done", {}).get(k):
            continue
        L = [x for x in (K.get("buys") or []) + (K.get("safe") or []) if x.get("kick") == k and (x.get("edge") or 0) >= KAL_EDGE and x.get("ticker")]
        L = one_per(L, lambda x: x.get("player") or x.get("game") or x["label"], lambda x: x.get("edge") or 0)
        L.sort(key=lambda x: -(x.get("edge") or 0))
        L = L[:MAX_KAL]
        if not L:
            S["kal_done"][k] = now.isoformat(); continue   # quiet when there's nothing; no need for a pass post here
        lines = [f"💸 **{x['label']}** · buy **{x['side']}** at **{x['cost']}¢** or less\n> we have it {round(x['p'])}% · edge {x['edge']} · pays {usd(1 / ((x['cost'] + (x.get('fee') or 0)) / 100) - 1)} on {usd(1)[1:]}" for x in L]
        e = {"title": f"💸 Kalshi buys · {pt(k)} PT", "description": "\n\n".join(lines), "color": COLORS["kalshi"],
             "footer": {"text": "Only buy if a share costs less than our chance (in cents). Prices move fast."}}
        if post("KALSHI", embeds=[e]):
            S["kal_done"][k] = now.isoformat()
            for x in L:
                posted[f"KA|{x['ticker']}|{x['side']}"] = dict(week=str(wk), ch="kalshi", text=x["label"], cost=x["cost"], fee=x.get("fee") or 0, units=1, kick=k, at=now.isoformat())


def parlays(S, PLD, now):
    """Once per game day: top 2-leg and 3-leg parlays whose legs haven't started."""
    ks = [k for k in sorted({g["kick"] for g in PLD.get("games", [])}) if iso(k) > now]
    if not ks:
        return
    first = ks[0]
    day = iso(first).astimezone(PT).strftime("%Y-%m-%d")
    if S.setdefault("parlay_days", {}).get(day) or iso(first) - now > datetime.timedelta(minutes=LEAD_MIN):
        return
    kick_of = {l["label"]: l.get("kick") for l in PLD.get("legs") or []}
    eod = iso(first).astimezone(PT).replace(hour=23, minute=59)
    def ok(pl):
        kk = [kick_of.get(l["label"]) for l in pl["legs"]]
        return all(x and now < iso(x) <= eod for x in kk)
    P = PLD.get("parlays") or {}
    pick = [pl for pl in (P.get("2") or []) if ok(pl)][:2] + [pl for pl in (P.get("3") or []) if ok(pl)][:1]
    if not pick:
        S["parlay_days"][day] = now.isoformat(); return
    lines = []
    for pl in pick:
        legs = "\n".join(f"> • {l['label']} ({round(l['p'])}%)" for l in pl["legs"])
        lines.append(f"🎲 **{len(pl['legs'])}-leg · hits {round(pl['p'])}% · fair {am(pl['fair'])}**{' · same game' if pl.get('same_game') else ''}\n{legs}\n> Only take it at better than **{am(pl['fair'])}**.")
    e = {"title": f"🎲 Parlays · {iso(first).astimezone(PT).strftime('%A')}", "description": "\n\n".join(lines), "color": COLORS["parlay"],
         "footer": {"text": "Same-game legs are correlated; the hit % already accounts for that."}}
    if post("PARLAYS", embeds=[e]):
        S["parlay_days"][day] = now.isoformat()


def results(S, PK, now):
    posted = S.setdefault("posted", {})
    rec = S.setdefault("record", {"w": 0, "l": 0, "p": 0, "u": 0.0})
    out = []
    for key, x in posted.items():
        if x.get("result"):
            continue
        g = (PK.get(x["week"]) or {}).get(key)
        if not g or not g.get("graded"):
            if g is None and iso(x["kick"]) < now - datetime.timedelta(days=3):
                x["result"] = "gone"
            continue
        res = "push" if g.get("result") is None else "win" if g["result"] else "loss"
        if x["ch"] == "kalshi":
            u = (100 / (x["cost"] + x["fee"]) - 1) if res == "win" else -1 if res == "loss" else 0
        else:
            u = x["units"] * (win_units(x.get("price")) if res == "win" else -1 if res == "loss" else 0)
        x.update(result=res, u=round(u, 3), actual=g.get("actual"), graded_at=now.isoformat())
        rec["w" if res == "win" else "l" if res == "loss" else "p"] += 1; rec["u"] = round(rec["u"] + u, 3)
        out.append(x)
    if out:
        lines = [f"{'✅' if x['result'] == 'win' else '❌' if x['result'] == 'loss' else '➖'} {x['text']}{f' (had {x['actual']})' if x.get('actual') is not None else ''} · **{usd(x['u'])}**" for x in out]
        good = sum(x["u"] for x in out) >= 0
        e = {"title": f"📊 {len(out)} graded", "description": "\n".join(lines)[:3900], "color": COLORS["win" if good else "loss"],
             "footer": {"text": f"Discord record: {rec['w']}-{rec['l']}{'-' + str(rec['p']) if rec['p'] else ''} · {usd(rec['u'])} at ${UNIT:.0f} a unit"}}
        post("RESULTS", embeds=[e])
    # daily recap: the morning after, for picks graded the day before
    today = now.astimezone(PT).date()
    yday = (today - datetime.timedelta(days=1)).isoformat()
    if now.astimezone(PT).hour >= 8 and S.setdefault("recaps", {}).get(yday) is None:
        day = [x for x in posted.values() if x.get("graded_at") and iso(x["graded_at"]).astimezone(PT).date().isoformat() == yday and x["result"] in ("win", "loss", "push")]
        S["recaps"][yday] = len(day)
        if day:
            w = sum(x["result"] == "win" for x in day); l = sum(x["result"] == "loss" for x in day); u = sum(x["u"] for x in day)
            bestp = max(day, key=lambda x: x["u"])
            e = {"title": f"🗓️ {datetime.date.fromisoformat(yday).strftime('%A')} recap: {w}-{l} · {usd(u)}", "color": COLORS["win" if u >= 0 else "loss"],
                 "description": f"Best hit: {bestp['text']} ({usd(bestp['u'])})\nAll time here: {rec['w']}-{rec['l']} · {usd(rec['u'])}"}
            post("RESULTS", embeds=[e])


HIST = "https://github.com/PandaFilmsHD/oxtail-lab/commits/main/data/props_picks.json"


def one_per(items, who, edge):
    """Kalshi lists ladders (60+, 70+, 80+...). Keep only the best rung per player or game."""
    best = {}
    for it in items:
        w_ = who(it)
        if w_ not in best or edge(it) > edge(best[w_]):
            best[w_] = it
    return [it for it in items if best.get(who(it)) is it]


def qualifies(k, v):
    x = v["raw"]
    if v["kind"] == "kalshi":
        return (x.get("edge") or 0) >= KAL_EDGE
    if v["kind"] == "game":
        return (x.get("edge") or 0) >= GAME_EDGE
    return (x.get("edge") or 0) >= PROP_EDGE or x.get("grade") in ("A", "B")


def backfill(S, PK, wk, now):
    """One time: picks the model locked this week whose games already started. Posted late and labeled that way."""
    if str(wk) in S.setdefault("backfilled", []):
        print("discord: week already backfilled"); return
    picks = week_picks(PK, wk)
    past = sorted([(k, v) for k, v in picks.items() if v["kick"] and iso(v["kick"]) <= now], key=lambda kv: (kv[1]["kick"], kv[1]["kind"], kv[1]["text"]))
    if not past:
        S["backfilled"].append(str(wk)); return
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["week", "kickoff_utc", "type", "pick", "price", "model_pct", "edge", "locked_at_utc", "id"])
    for k, v in past:
        w.writerow([wk, v["kick"], v["kind"], v["text"], v["price"], v["p"], v["edge"], v.get("at") or "", k])
    data = buf.getvalue().encode(); fp = hashlib.sha256(data).hexdigest()
    e = {"title": f"📂 Week {wk} backfill: {len(past)} picks from games already played", "color": COLORS["receipt"],
         "description": f"These were locked by the model **before** their games, but this channel didn't exist yet, so they're posted late.\n\n"
                        f"Proof for these is the bot's GitHub history, where each pick was saved before kickoff:\n{HIST}\n\nFingerprint: `{fp}`",
         "footer": {"text": "From here on, every pick posts here before kickoff."}}
    post("RECEIPTS", embeds=[e], file=(f"oxtail_wk{wk}_backfill.csv", data, "text/csv"))
    sel = [(k, v) for k, v in past if qualifies(k, v)]
    kal = one_per([kv for kv in sel if kv[1]["kind"] == "kalshi"], lambda kv: (kv[1]["raw"].get("player") or f"{kv[1]['raw'].get('away')}@{kv[1]['raw'].get('home')}", kv[1]["kick"]), lambda kv: kv[1]["edge"] or 0)
    sel = [kv for kv in sel if kv[1]["kind"] != "kalshi"] + kal
    sel.sort(key=lambda kv: (kv[1]["kick"], kv[1]["text"]))
    posted = S.setdefault("posted", {})
    lines, klines = [], []
    for k, v in sel:
        x = v["raw"]; units = 1 if v["kind"] == "kalshi" or x.get("grade") in ("A", "B") else 0.5
        icon = {"prop": "👤", "game": "🏈", "kalshi": "💸"}[v["kind"]]
        (klines if v["kind"] == "kalshi" else lines).append(f"{icon} {v['text']} ({v['price']}) · {pt(v['kick'])}")
        d = dict(week=str(wk), ch="kalshi" if v["kind"] == "kalshi" else "best", text=v["text"], units=units, kick=v["kick"], at=now.isoformat(), backfill=True)
        if v["kind"] == "kalshi":
            d.update(cost=x.get("cost") or 0, fee=x.get("fee") or 0)
        else:
            d.update(price=x.get("price") or -110)
        posted[k] = d
    for ch, L_ in (("BEST", lines), ("KALSHI", klines)):
        if not L_:
            continue
        chunks, cur = [], ""
        for ln in L_:
            if len(cur) + len(ln) > 3800:
                chunks.append(cur); cur = ""
            cur += ln + "\n"
        chunks.append(cur)
        for i, c in enumerate(chunks):
            post(ch, embeds=[{"title": f"📂 Week {wk} so far (posted late)" + (f" {i + 1}/{len(chunks)}" if len(chunks) > 1 else ""), "color": COLORS["push"],
                                  "description": "The top picks from games that already started, using the same strict cutoff as normal. Grades land in #results.\n\n" + c,
                                  "footer": {"text": "Locked before kickoff; see #receipts for the GitHub proof."}}])
    S["backfilled"].append(str(wk))


def main():
    if "--test" in sys.argv:
        for ch, t in [("BEST", "🎯 Oxtail Picks is connected. Best bets post here about 90 minutes before each kickoff."),
                      ("PARLAYS", "🎲 Oxtail Parlays is connected. Parlays post here before the first game each day."),
                      ("KALSHI", "💸 Oxtail Kalshi is connected. Kalshi buys post here before each kickoff."),
                      ("RESULTS", "📊 Oxtail Results is connected. Every posted pick gets graded here, wins and losses."),
                      ("RECEIPTS", "🧾 Oxtail Receipts is connected. Every locked pick gets posted here with a fingerprint before kickoff.")]:
            print(ch, "ok" if post(ch, content=t) else "FAILED")
        return
    if not any(HOOK.values()):
        print("discord: no webhooks set"); return
    PLD = load(ROOT / "docs" / "data" / "props_lab.json", None)
    PK = load(D / "props_picks.json", {})
    if not PLD:
        print("discord: no props lab data"); return
    S = load(D / "discord_state.json", {})
    now = now_utc()
    wk = PLD.get("week")
    if "--backfill" in sys.argv:
        try:
            backfill(S, PK, wk, now)
        finally:
            (D / "discord_state.json").write_text(json.dumps(S, indent=1, sort_keys=True))
    for step in (lambda: receipts(S, PK, wk, now), lambda: best_bets(S, PLD, PK, wk, now), lambda: kalshi(S, PLD, wk, now),
                 lambda: parlays(S, PLD, now), lambda: results(S, PK, now)):
        try:
            step()
        except Exception as e:
            print("discord: step failed", type(e).__name__, e)
        (D / "discord_state.json").write_text(json.dumps(S, indent=1, sort_keys=True))   # save after each step so a crash never double posts


if __name__ == "__main__":
    main()
