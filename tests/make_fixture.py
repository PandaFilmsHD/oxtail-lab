"""Turn the compact Week 11 pull (tests/fixture_compact.json) into data/model.json format for local testing."""
import json, pathlib
H = pathlib.Path(__file__).parent
c = json.loads((H / "fixture_compact.json").read_text())
cur = c["cur"]
def kv(s): return {k: float(v) for k, v in (x.split(":") for x in s.split(",") if x)}
games = {}
for ab, s in c["games"].items():
    g = {str(cur): "?"}
    for i, o in enumerate(s.split(",")):
        if o != "BYE": g[str(cur + 1 + i)] = o
    games[ab] = g if ab != "FA" else {}
m = {"at": c["at"], "season": 2026, "league_id": 787523197, "cur": cur, "reg_end": c["regEnd"], "po_teams": c["po"],
     "teams": [{"id": t[0], "name": t[1], "w": t[2], "l": t[3], "t": 0, "pf": t[4], "waiver": t[5], "scores": {k: v for k, v in kv(t[6]).items()}} for t in c["teams"]],
     "settings": {"name": "Od from oxtail", "teams": 12, "ppr": 1, "pass_td": 6, "playoff_teams": 6, "reg_end": 14,
                  "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "D": 1, "K": 1, "BE": 7, "IR": 1},
                  "scoring": {"3": 0.04, "4": 6, "20": -2, "24": 0.1, "25": 6, "53": 1, "42": 0.1, "43": 6, "72": -2, "19": 2, "26": 2, "44": 2},
                  "tiebreak": "TOTAL_POINTS_SCORED", "veto_votes": 5, "waiver_type": "Rolling/priority"},
     "sched": c["sched"], "games": games, "rank": c["rank"],
     "players": [{"id": p[0], "n": p[1], "pos": p[2], "nfl": p[3], "t": p[4], "inj": p[5], "avg": p[6], "proj": p[7], "own": p[8], "wk": kv(p[9])} for p in c["players"]]}
out = H.parent / "data" / "model.json"; out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(m, separators=(",", ":")))
print("wrote", out, len(m["players"]), "players")
