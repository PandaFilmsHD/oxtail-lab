"""Pull everything the sim needs from a private ESPN fantasy football league.

Needs two cookies from a logged-in ESPN session, passed as environment variables:
  ESPN_S2  (the espn_s2 cookie)
  SWID     (the SWID cookie, including the curly braces)
League id and season come from config.json.

Writes data/model.json. If a previous model exists it is kept as data/model_prev.json
so the analyzer can log what changed (adds, drops, trades).
"""
import json, os, sys, time, shutil, pathlib
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
SEASON = CFG["season"]
READS = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D"}

s2, swid = os.environ.get("ESPN_S2", "").strip(), os.environ.get("SWID", "").strip()
if not s2 or not swid:
    sys.exit("Missing ESPN_S2 or SWID. Add them as repo secrets (see README).")
S = requests.Session()
NEWS = {}   # player id -> latest ESPN injury/news note, shared across leagues in one run
S.cookies.set("espn_s2", s2, domain=".espn.com")
S.cookies.set("SWID", swid, domain=".espn.com")
S.headers["User-Agent"] = "Mozilla/5.0 oxtail-lab"


def get(url, params=None, flt=None, tries=4):
    h = {"x-fantasy-filter": json.dumps(flt)} if flt else {}
    for i in range(tries):
        r = S.get(url, params=params, headers=h, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403):
            raise RuntimeError(f"ESPN said {r.status_code}: cookies wrong/expired, or this league is private to someone else")
        time.sleep(2 * (i + 1))
    r.raise_for_status()


def pro_schedule():
    """NFL schedule -> opponent by week for every pro team."""
    pro = get(f"{READS}/{SEASON}", {"view": "proTeamSchedules_wl"})
    abbr = {t["id"]: t["abbrev"] for t in pro["settings"]["proTeams"]}
    games = {}
    for t in pro["settings"]["proTeams"]:
        g = {}
        for wk, lst in (t.get("proGamesByScoringPeriod") or {}).items():
            if not lst:
                continue
            x = lst[0]
            home = x["homeProTeamId"] == t["id"]
            g[str(wk)] = ("" if home else "@") + abbr[x["awayProTeamId"] if home else x["homeProTeamId"]]
        games[t["abbrev"]] = g
    return games, abbr


def discover():
    """Every ESPN football league on this account for this season: {league_id: {name, team_id}}."""
    out = {}
    try:
        r = S.get(f"https://fan.api.espn.com/apis/v2/fans/{swid}", params={"displayEvents": "false", "displayNow": "false", "displayRecs": "false",
                  "context": "fantasy", "source": "espncom-fantasy-lm", "lang": "en", "section": "espn", "region": "us"}, timeout=30)
        r.raise_for_status()
        for pref in r.json().get("preferences", []):
            e = (pref.get("metaData") or {}).get("entry") or {}
            if (str(e.get("abbrev", "")).lower() != "ffl" and e.get("gameId") != 1) or e.get("seasonId") != SEASON:
                continue
            for g in e.get("groups", []):
                out[str(g["groupId"])] = {"name": (g.get("groupName") or "").strip(), "team_id": e.get("entryId")}
        print(f"found {len(out)} football league(s) on your ESPN account")
    except Exception as ex:
        print("league discovery failed, using config.json leagues:", ex)
    return out


def pull_league(LEAGUE, games, abbr, want_team=None):
    BASE = f"{READS}/{SEASON}/segments/0/leagues/{LEAGUE}"
    d = get(BASE, [("view", v) for v in ("mRoster", "mTeam", "mMatchupScore", "mSettings")])
    cur = d.get("scoringPeriodId") or d["status"]["currentMatchupPeriodId"]
    ss = d["settings"]["scheduleSettings"]
    reg_end = ss["matchupPeriodCount"]
    po_teams = ss["playoffTeamCount"]

    st_ = d["settings"]
    scoring = {}
    for it in (st_.get("scoringSettings") or {}).get("scoringItems", []):
        scoring[str(it["statId"])] = it.get("points", 0)
    slot_names = {0: "QB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE", 7: "OP", 16: "D", 17: "K", 20: "BE", 21: "IR", 23: "FLEX"}
    slots = {slot_names.get(int(k), k): v for k, v in (st_.get("rosterSettings") or {}).get("lineupSlotCounts", {}).items() if v}
    settings = {"name": st_.get("name"), "teams": st_.get("size") or len(d["teams"]), "scoring": scoring,
                "ppr": scoring.get("53", 0), "pass_td": scoring.get("4", 4), "slots": slots, "playoff_teams": po_teams, "reg_end": reg_end,
                "playoff_matchup_length": ss.get("playoffMatchupPeriodLength", 1), "tiebreak": ss.get("playoffSeedingRule"),
                "trade_deadline": (st_.get("tradeSettings") or {}).get("deadlineDate"), "veto_votes": (st_.get("tradeSettings") or {}).get("vetoVotesRequired"),
                "waiver_type": "FAAB" if (st_.get("acquisitionSettings") or {}).get("isUsingAcquisitionBudget") else "Rolling/priority"}

    teams, roster_of = [], {}
    for t in d["teams"]:
        name = t.get("name") or f'{t.get("location", "")} {t.get("nickname", "")}'.strip()
        rec = t["record"]["overall"]
        teams.append({"id": t["id"], "name": name, "abbrev": t.get("abbrev", ""), "w": rec["wins"], "l": rec["losses"],
                      "t": rec.get("ties", 0), "pf": round(rec["pointsFor"], 1), "waiver": t.get("waiverRank"), "scores": {}})
        for e in t.get("roster", {}).get("entries", []):
            roster_of[e["playerId"]] = t["id"]
    tmap = {t["id"]: t for t in teams}
    me = want_team
    sw = swid.lower()
    for t in d["teams"]:
        owners = [o.lower() for o in (t.get("owners") or [])] + [str(t.get("primaryOwner", "")).lower()]
        if sw in owners:
            me = t["id"]
    sched = []
    for m in d.get("schedule", []):
        wk = m["matchupPeriodId"]
        if wk > reg_end or "away" not in m:
            continue
        h, a = m["home"]["teamId"], m["away"]["teamId"]
        if wk < cur:
            tmap[h]["scores"][str(wk)] = round(m["home"].get("totalPoints", 0), 1)
            tmap[a]["scores"][str(wk)] = round(m["away"].get("totalPoints", 0), 1)
        else:
            sched.append([wk, h, a])

    pr = get(BASE, {"view": "mPositionalRatings", "scoringPeriodId": cur})
    rank = {}
    prs = pr.get("positionAgainstOpponent", {}).get("positionalRatings", {})
    for pos in ("1", "2", "3", "4", "5", "16"):
        rank[pos] = {abbr[int(tid)]: v["rank"] for tid, v in (prs.get(pos, {}).get("ratingsByOpponent") or {}).items() if int(tid) in abbr}

    # free agents worth looking at: top owned at each position
    fa = []
    for slot, lim in ((0, 25), (2, 40), (4, 40), (6, 25), (16, 12), (17, 10)):
        flt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "filterSlotIds": {"value": [slot]},
                           "limit": lim, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
        r = get(BASE, {"view": "kona_player_info", "scoringPeriodId": cur}, flt)
        fa += [x["player"]["id"] for x in r.get("players", [])]
    ids = list(dict.fromkeys(list(roster_of) + fa))

    players = []
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        flt = {"players": {"filterIds": {"value": chunk},
                           "filterStatsForTopScoringPeriodIds": {"value": 17, "additionalValue": [f"00{SEASON}", f"10{SEASON}"]}}}
        r = get(BASE, {"view": "kona_player_info", "scoringPeriodId": cur}, flt)
        for x in r.get("players", []):
            p = x["player"]
            pos = POS.get(p.get("defaultPositionId"))
            if not pos:
                continue
            st = p.get("stats") or []
            wk, proj, avg, season_proj, season_act = {}, None, 0, None, None
            for z in st:
                if z.get("seasonId") != SEASON:
                    continue
                sp, src = z.get("scoringPeriodId"), z.get("statSourceId")
                if src == 0 and sp == 0 and z.get("statSplitTypeId") == 0:
                    avg = round(z.get("appliedAverage") or 0, 1)
                    season_act = z.get("appliedTotal")
                elif src == 1 and sp == 0 and z.get("statSplitTypeId") == 0:
                    season_proj = z.get("appliedTotal")
                elif src == 0 and sp and sp > 0:
                    wk[str(sp)] = round(z.get("appliedTotal", 0), 1)
                elif src == 1 and sp == cur:
                    proj = round(z.get("appliedTotal", 0), 1)
            own = p.get("ownership") or {}
            outlook = ""
            obw = ((p.get("outlooks") or {}).get("outlooksByWeek") or {})
            if obw:
                outlook = obw[max(obw, key=lambda k: int(k))]
            elif p.get("seasonOutlook"):
                outlook = p["seasonOutlook"]
            if not avg:
                played = [v for v in wk.values() if v != 0]
                avg = round(sum(played) / len(played), 1) if played else 0
            players.append({"id": p["id"], "n": p["fullName"], "pos": pos, "nfl": abbr.get(p.get("proTeamId"), "FA"),
                            "t": roster_of.get(p["id"], 0), "inj": p.get("injuryStatus") or ("ACTIVE" if not p.get("injured") else "OUT"),
                            "avg": avg, "proj": proj, "own": round(own.get("percentOwned", 0)), "own_chg": round(own.get("percentChange", 0) or 0, 1),
                            "start_pct": round(own.get("percentStarted", 0) or 0), "espn_ros": round(season_proj, 1) if season_proj else None,  # ESPN keeps this as a rest-of-season projection
                            "outlook": (outlook or "")[:600], "wk": wk})
        time.sleep(0.3)

    # Latest injury news straight from ESPN's injury + news feeds (the outlook text above can be weeks old)
    hurt = [x for x in players if x["inj"] not in ("ACTIVE", None) and (x["t"] or x["own"] >= 3) and x["pos"] != "D"]
    for x in hurt:
        if x["id"] in NEWS:
            x["news"] = NEWS[x["id"]]; continue
        nw = {"text": "", "date": None, "ret": None, "type": None}
        try:
            r_ = requests.get(f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes/{x['id']}/injuries", params={"limit": 2}, timeout=20)
            items = (r_.json() or {}).get("items") or [] if r_.ok else []
            if items and items[0].get("$ref"):
                it = requests.get(items[0]["$ref"].replace("http://", "https://"), timeout=20).json()
                det = it.get("details") or {}
                nw.update(text=(it.get("longComment") or it.get("shortComment") or ""), date=it.get("date"), ret=det.get("returnDate"),
                          type=" ".join(str(det.get(k) or "") for k in ("type", "detail", "location")).strip() or None, fstatus=((det.get("fantasyStatus") or {}).get("description")))
        except Exception:
            pass
        try:
            r2 = requests.get("https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players", params={"playerId": x["id"], "limit": 6}, timeout=20)
            feed = (r2.json() or {}).get("feed") or [] if r2.ok else []
            last = [w for w in x["n"].split() if w.rstrip(".") not in ("Jr", "Sr", "II", "III", "IV")][-1]
            def _txt(f):
                return __import__("re").sub(r"<[^>]+>", " ", (f.get("headline") or "") + ". " + (f.get("story") or f.get("description") or ""))
            # only notes that are actually about him (his name up front), not general articles that mention him
            feed = [f for f in feed if last.lower() in _txt(f)[:160].lower() and len(_txt(f)) < 2500]
            if feed:
                f0 = feed[0]
                txt = _txt(f0)
                if not nw["date"] or (f0.get("published") or "") > (nw["date"] or ""):
                    nw["text"] = (txt + " " + nw["text"]).strip()
                    nw["date"] = f0.get("published") or nw["date"]
                else:
                    nw["text"] = (nw["text"] + " " + txt).strip()
        except Exception:
            pass
        nw["text"] = nw["text"][:700]
        NEWS[x["id"]] = nw; x["news"] = nw
        time.sleep(0.05)
    dbg = [x for x in hurt if x["n"] in ("Michael Wilson", "Puka Nacua")]
    for x in dbg:
        print(f"  news {x['n']}: {json.dumps(x.get('news'))[:400]}")
    print(f"  injury news: {sum(1 for x in hurt if (x.get('news') or {}).get('text'))}/{len(hurt)} hurt players have fresh notes")

    # FantasyCalc market values (built from millions of real trades), for this league's size / PPR / superflex
    try:
        ppr_ = scoring.get("53", 0); ppr_ = 1 if ppr_ >= 0.75 else 0.5 if ppr_ >= 0.25 else 0
        nq = 2 if slots.get("OP") else 1
        fr = requests.get("https://api.fantasycalc.com/values/current", params={"isDynasty": "false", "numQbs": nq, "numTeams": settings["teams"], "ppr": ppr_}, timeout=60)
        fr.raise_for_status()
        fc = {}
        for x in fr.json():
            pl = x.get("player") or {}
            if pl.get("espnId"):
                fc[str(pl["espnId"])] = {"v": x.get("redraftValue") or x.get("value"), "trend": x.get("trend30Day"), "name": pl.get("name")}
        fp = ROOT / "data" / str(LEAGUE) / "fantasycalc.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        txt = json.dumps(fc, sort_keys=True, separators=(",", ":"))
        if not fp.exists() or fp.read_text() != txt:
            fp.write_text(txt)
        print(f"  fantasycalc: {len(fc)} players with market values")
    except Exception as e:
        print(f"  fantasycalc: skipped ({e})")
    model = {"my_team": me, "league_name": settings.get("name"), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "season": SEASON, "league_id": LEAGUE, "cur": cur,
             "reg_end": reg_end, "po_teams": po_teams, "settings": settings, "teams": teams, "sched": sched, "games": games, "rank": rank, "players": players}
    out = ROOT / "data" / str(LEAGUE) / "model.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        old = json.loads(out.read_text())
        if {k: v for k, v in old.items() if k != "at"} == {k: v for k, v in model.items() if k != "at"}:
            print(f"{settings.get('name')} ({LEAGUE}): nothing changed since last pull")
            return
        shutil.copy(out, out.parent / "model_prev.json")
    out.write_text(json.dumps(model, separators=(",", ":")))
    print(f"{settings.get('name')} ({LEAGUE}) week {cur}: you are team {me}, {len(teams)} teams, {len(players)} players, {len(sched)} games left")


def main():
    games, abbr = pro_schedule()
    leagues = {str(k): dict(v) for k, v in (CFG.get("leagues") or {}).items() if not str(k).startswith("_")}
    if CFG.get("auto_discover", True):
        for lid, info in discover().items():
            leagues.setdefault(lid, {}).update({k: v for k, v in info.items() if v is not None and k not in leagues.get(lid, {})})
    skip = {str(x) for x in CFG.get("skip_leagues", [])}
    ok = []
    for lid, info in leagues.items():
        if lid in skip:
            continue
        try:
            pull_league(int(lid), games, abbr, info.get("team_id"))
            ok.append(lid)
        except SystemExit:
            raise
        except Exception as ex:
            print(f"league {lid} failed: {ex}")
    if not ok:
        sys.exit("No leagues pulled.")
    (ROOT / "data" / "leagues.json").write_text(json.dumps({lid: leagues[lid] for lid in ok}, indent=1))


if __name__ == "__main__":
    main()
