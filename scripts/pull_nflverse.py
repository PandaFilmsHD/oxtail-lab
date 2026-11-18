"""Pull free official-source NFL data from nflverse / ffverse and boil it down per player.

Sources (all public GitHub releases, updated through the season):
  stats_player_week   weekly box score + target share, air yards share, WOPR, EPA
  snap_counts         offensive snap %
  ffopportunity       expected fantasy points (what a player's usage "should" score)
  play_by_play        red zone / goal line usage, team pace and pass rate over expected
  nextgen_stats       separation, YAC over expected, rush yards over expected, CPOE
  pfr_advstats        broken tackles, drops, yards before/after contact
  injuries            official practice + game status reports
  depth_charts        current depth chart spot

Writes data/usage.json ({players: {...}, teams: {...}}). Every source fails soft.
"""
import csv
import os, gzip, io, json, re, pathlib, collections, traceback
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEASON = json.loads((ROOT / "config.json").read_text())["season"]
REL = "https://github.com/nflverse/nflverse-data/releases/download"
SKILL = ("QB", "RB", "WR", "TE")


def norm(n):
    n = (n or "").lower().replace(".", "").replace("'", "").replace("’", "")
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"[^a-z]", "", n)


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def rows(url, gz=False):
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    text = gzip.decompress(r.content).decode() if gz else r.text
    return csv.DictReader(io.StringIO(text))


def soft(name, fn):
    try:
        out = fn()
        print(f"  {name}: ok")
        return out
    except Exception:
        print(f"  {name}: FAILED (skipping)\n" + traceback.format_exc(limit=1))
        return None


P = {}       # norm name -> player record
GSIS = {}    # gsis id -> norm name
TEAMS = collections.defaultdict(dict)


def player(k, name=None, team=None, pos=None):
    e = P.setdefault(k, {"name": name, "team": team, "pos": pos, "weeks": {}})
    if team:
        e["team"] = team
    return e


def wk(e, w):
    return e["weeks"].setdefault(int(w), {"w": int(w)})


def box():
    tc = collections.Counter()
    data = [r for r in rows(f"{REL}/stats_player/stats_player_week_{SEASON}.csv") if r["season_type"] == "REG"]
    for r in data:
        tc[(r["team"], r["week"])] += f(r["carries"]) or 0
    for r in data:
        if r["position_group"] not in SKILL:
            continue
        k = norm(r["player_display_name"]); GSIS[r["player_id"]] = k
        e = player(k, r["player_display_name"], r["team"], r["position_group"])
        car = f(r["carries"]) or 0
        t = tc[(r["team"], r["week"])]
        wk(e, r["week"]).update(
            opp=r["opponent_team"], pts=f(r["fantasy_points_ppr"]) or 0, tgt=int(f(r["targets"]) or 0), rec=int(f(r["receptions"]) or 0),
            recyd=int(f(r["receiving_yards"]) or 0), car=int(car), rushyd=int(f(r["rushing_yards"]) or 0),
            td=int(sum(f(r[c]) or 0 for c in ("receiving_tds", "rushing_tds", "passing_tds"))), att=int(f(r["attempts"]) or 0),
            passyd=int(f(r["passing_yards"]) or 0), int_=int(f(r["passing_interceptions"]) or 0),
            ptd=int(f(r["passing_tds"]) or 0), rutd=int(f(r["rushing_tds"]) or 0), retd=int(f(r["receiving_tds"]) or 0),
            fl=int(sum(f(r[c]) or 0 for c in ("rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"))),
            two=int(sum(f(r[c]) or 0 for c in ("passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions"))),
            tsh=round((f(r["target_share"]) or 0) * 100, 1), ash=round((f(r["air_yards_share"]) or 0) * 100, 1),
            wopr=round(f(r["wopr"]) or 0, 2), csh=round(car / t * 100, 1) if t else 0, airyd=int(f(r["receiving_air_yards"]) or 0),
            epa=round(sum(f(r[c]) or 0 for c in ("passing_epa", "rushing_epa", "receiving_epa")), 1))


def snaps():
    for r in rows(f"{REL}/snap_counts/snap_counts_{SEASON}.csv"):
        k = norm(r["player"])
        if r.get("game_type") == "REG" and k in P and f(r.get("offense_pct")) is not None:
            wk(P[k], r["week"])["snap"] = round(f(r["offense_pct"]) * 100)


XFP_COLS = ("pass_yards_gained_exp", "pass_touchdown_exp", "pass_interception_exp", "pass_two_point_conv_exp", "rush_yards_gained_exp",
            "rush_touchdown_exp", "rush_two_point_conv_exp", "receptions_exp", "rec_yards_gained_exp", "rec_touchdown_exp", "rec_two_point_conv_exp")


def xfp():
    for r in rows(f"https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{SEASON}.csv"):
        k = GSIS.get(r["player_id"]) or norm(r["full_name"])
        if k in P and f(r.get("total_fantasy_points_exp")) is not None:
            # keep the expected stat line so the analyzer can rescore it with the league's own scoring
            parts = {c: round(f(r.get(c)) or 0, 3) for c in XFP_COLS}
            wk(P[k], r["week"]).update(xfp=round(f(r["total_fantasy_points_exp"]), 1), xs=parts)


def pbp():
    team_plays = collections.Counter(); team_games = collections.defaultdict(set); team_pass = collections.Counter(); team_xpass = collections.Counter(); team_xn = collections.Counter()
    for r in rows(f"{REL}/pbp/play_by_play_{SEASON}.csv.gz", gz=True):
        if r.get("season_type") != "REG":
            continue
        pt, w, yl = r.get("posteam"), r.get("week"), f(r.get("yardline_100"))
        if not pt or yl is None:
            continue
        is_pass, is_rush = r.get("pass_attempt") == "1", r.get("rush_attempt") == "1"
        if is_pass or is_rush:
            team_plays[pt] += 1; team_games[pt].add(w)
            if r.get("xpass") not in (None, "", "NA"):
                team_pass[pt] += is_pass; team_xpass[pt] += f(r["xpass"]) or 0; team_xn[pt] += 1
        if yl > 20:
            continue
        pid = r.get("receiver_player_id") if is_pass else r.get("rusher_player_id") if is_rush else None
        k = GSIS.get(pid)
        if not k:
            continue
        x = wk(P[k], w)
        if is_pass and r.get("receiver_player_id"):
            x["rz_tgt"] = x.get("rz_tgt", 0) + 1
            if yl <= 10: x["i10_tgt"] = x.get("i10_tgt", 0) + 1
        if is_rush:
            x["rz_car"] = x.get("rz_car", 0) + 1
            if yl <= 5: x["gl_car"] = x.get("gl_car", 0) + 1
    for t in team_plays:
        g = len(team_games[t]) or 1
        TEAMS[t].update(plays_pg=round(team_plays[t] / g, 1), proe=round(100 * (team_pass[t] - team_xpass[t]) / max(1, team_xn[t]), 1))


def ngs():
    for kind, cols in (("receiving", {"avg_separation": "sep", "avg_cushion": "cushion", "avg_yac_above_expectation": "yacoe",
                                      "avg_intended_air_yards": "adot", "percent_share_of_intended_air_yards": "air_share", "catch_percentage": "catch_pct"}),
                       ("rushing", {"rush_yards_over_expected_per_att": "ryoe_att", "efficiency": "eff", "percent_attempts_gte_eight_defenders": "stacked_box",
                                    "avg_time_to_los": "time_to_los"}),
                       ("passing", {"completion_percentage_above_expectation": "cpoe", "aggressiveness": "aggr", "avg_time_to_throw": "ttt",
                                    "avg_intended_air_yards": "adot", "passer_rating": "rating"})):
        for r in rows(f"{REL}/nextgen_stats/ngs_{kind}.csv.gz", gz=True):
            if r["season"] != str(SEASON) or r["season_type"] != "REG" or r["week"] != "0":
                continue
            k = GSIS.get(r.get("player_gsis_id")) or norm(r["player_display_name"])
            if k in P:
                P[k].setdefault("ngs", {}).update({v: round(f(r[c]), 2) for c, v in cols.items() if f(r.get(c)) is not None})


def pfr():
    acc = collections.defaultdict(collections.Counter)
    for kind in ("rec", "rush"):
        for r in rows(f"{REL}/pfr_advstats/advstats_week_{kind}_{SEASON}.csv"):
            if r.get("game_type") != "REG":
                continue
            k = norm(r["pfr_player_name"])
            if k not in P:
                continue
            a = acc[k]
            if kind == "rec":
                a["rec_btk"] += f(r.get("receiving_broken_tackles")) or 0; a["drops"] += f(r.get("receiving_drop")) or 0
            else:
                a["rush_btk"] += f(r.get("rushing_broken_tackles")) or 0
                a["ybc"] += f(r.get("rushing_yards_before_contact")) or 0; a["yac"] += f(r.get("rushing_yards_after_contact")) or 0
                a["att"] += f(r.get("carries")) or 0
    for k, a in acc.items():
        d = {"broken_tackles": int(a["rec_btk"] + a["rush_btk"]), "drops": int(a["drops"])}
        if a["att"]:
            d.update(ybc_att=round(a["ybc"] / a["att"], 2), yac_att=round(a["yac"] / a["att"], 2))
        P[k]["pfr"] = d


def injuries():
    last = {}
    for r in rows(f"{REL}/injuries/injuries_{SEASON}.csv"):
        k = GSIS.get(r.get("gsis_id")) or norm(r["full_name"])
        if k in P and (k not in last or int(r["week"]) >= int(last[k]["week"])):
            last[k] = r
    for k, r in last.items():
        P[k]["injury"] = {"week": int(r["week"]), "status": r["report_status"] or None, "practice": r["practice_status"] or None,
                          "injury": " / ".join(x for x in (r["report_primary_injury"] or r["practice_primary_injury"], r["report_secondary_injury"] or r["practice_secondary_injury"]) if x) or None}


def depth():
    latest, keep = {}, []
    for r in rows(f"{REL}/depth_charts/depth_charts_{SEASON}.csv"):
        if r["pos_abb"] not in ("QB", "RB", "WR", "TE"):
            continue
        t = r["team"]
        if t not in latest or r["dt"] > latest[t]:
            latest[t] = r["dt"]; keep = [x for x in keep if x["team"] != t]
        if r["dt"] == latest[t]:
            keep.append(r)
    for r in keep:
        k = norm(r["player_name"])
        if k in P:
            cur = P[k].get("depth")
            rank = int(f(r["pos_rank"]) or 9)
            if not cur or rank < cur["rank"]:
                P[k]["depth"] = {"pos": r["pos_abb"], "rank": rank, "espn_id": r.get("espn_id")}


LINES = {}  # week -> team -> Vegas implied points (from the spread + over/under)
KICK = {}   # week -> team -> kickoff time (UTC ISO)
def lines():
    src = os.environ.get("GAMES_CSV") or "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
    rd = csv.DictReader(open(src)) if os.path.exists(src) else rows(src)
    for g in rd:
        if g.get("season") != str(SEASON) or g.get("game_type") != "REG":
            continue
        w = int(g["week"]); h, a = g["home_team"], g["away_team"]
        try:   # nflverse times are US Eastern
            from zoneinfo import ZoneInfo
            import datetime as _dt
            k = _dt.datetime.strptime(g["gameday"] + " " + g["gametime"], "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("America/New_York")).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            KICK.setdefault(str(w), {})[h] = k; KICK[str(w)][a] = k
        except Exception:
            pass
        tot, spr = f(g.get("total_line")), f(g.get("spread_line"))
        if tot is None or spr is None:
            continue
        hs, as_ = f(g.get("home_score")), f(g.get("away_score"))
        LINES.setdefault(str(w), {})[h] = {"it": round(tot / 2 + spr / 2, 2), "opp": a, "total": tot, "spread": spr, "pts": hs}
        LINES[str(w)][a] = {"it": round(tot / 2 - spr / 2, 2), "opp": h, "total": tot, "spread": -spr, "pts": as_}
    return LINES


def main():
    print("nflverse:")
    if soft("weekly box scores", box) is None and not P:
        return
    for name, fn in (("snap counts", snaps), ("expected fantasy points", xfp), ("play by play", pbp), ("next gen stats", ngs),
                     ("pfr advanced", pfr), ("injury reports", injuries), ("depth charts", depth), ("vegas lines", lines)):
        soft(name, fn)
    for e in P.values():
        e["weeks"] = [e["weeks"][w] for w in sorted(e["weeks"])]
    out = ROOT / "data" / "usage.json"
    out.parent.mkdir(exist_ok=True)
    txt = json.dumps({"players": P, "teams": TEAMS, "lines": LINES, "kick": KICK}, separators=(",", ":"), sort_keys=True)
    if not out.exists() or out.read_text() != txt:
        out.write_text(txt)
    print(f"usage.json: {len(P)} players, {len(TEAMS)} teams")


if __name__ == "__main__":
    main()
