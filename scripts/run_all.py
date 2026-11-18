"""Run the whole pipeline for every league: ESPN pull -> nflverse -> sims per league -> site index."""
import json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
CFG = json.loads((ROOT / "config.json").read_text())


def run(args, env=None):
    print("$", " ".join(args), flush=True)
    return subprocess.run([PY] + args, cwd=ROOT, env={**os.environ, **(env or {})}).returncode


status = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "espn_ok": True, "message": None}
if "--no-pull" not in sys.argv:
    if run(["scripts/pull_espn.py"]) != 0:
        status.update(espn_ok=False, message="Couldn't pull from ESPN. If this keeps happening your ESPN cookies probably expired: update the ESPN_S2 and SWID secrets.")
    run(["scripts/pull_nflverse.py"])
leagues = json.loads((ROOT / "data" / "leagues.json").read_text()) if (ROOT / "data" / "leagues.json").exists() else {}
for d in sorted((ROOT / "data").iterdir()):
    if (d / "model.json").exists() and d.name not in leagues:
        leagues[d.name] = {}
index = []
for lid in leagues:
    if not (ROOT / "data" / lid / "model.json").exists():
        continue
    if run(["scripts/analyze.py"], {"LEAGUE": lid}) != 0:
        print(f"analysis failed for {lid}")
    lp = ROOT / "docs" / "data" / lid / "latest.json"
    if lp.exists():
        s = json.loads(lp.read_text())["summary"]
        index.append({"id": lid, "name": s["league"], "team": s["my_team"], "week": s["week"], "at": s["at"], "champ_pct": s["kpis"]["ch"]})
default = str(CFG.get("default_league"))
index.sort(key=lambda x: (x["id"] != default, x["name"] or ""))
(ROOT / "docs" / "data" / "leagues.json").write_text(json.dumps({"leagues": index, "status": status}, indent=1))
print(f"site index: {len(index)} league(s)")
