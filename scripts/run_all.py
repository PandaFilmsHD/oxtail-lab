"""Run the whole pipeline for every league: ESPN pull -> nflverse -> sims per league -> site index."""
import hashlib, json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
CFG = json.loads((ROOT / "config.json").read_text())


def run(args, env=None):
    print("$", " ".join(args), flush=True)
    return subprocess.run([PY] + args, cwd=ROOT, env={**os.environ, **(env or {})}).returncode


status = {"espn_ok": True, "message": None}
if "--no-pull" not in sys.argv:
    if run(["scripts/pull_espn.py"]) != 0:
        status.update(espn_ok=False, message="Couldn't pull from ESPN. If this keeps happening your ESPN cookies probably expired: update the ESPN_S2 and SWID secrets.")
    if os.environ.get("NFL_CACHED") == "true" and (ROOT / "data" / "usage.json").exists():
        print("nflverse: using stats pulled in the last 6 hours")
    else:
        run(["scripts/pull_nflverse.py"])
leagues = json.loads((ROOT / "data" / "leagues.json").read_text()) if (ROOT / "data" / "leagues.json").exists() else {}
for d in sorted((ROOT / "data").iterdir()):
    if (d / "model.json").exists() and d.name not in leagues:
        leagues[d.name] = {}
index = []
for lid in leagues:
    if not (ROOT / "data" / lid / "model.json").exists():
        continue
    # only rerun the sims when something actually changed (league data, nflverse stats, settings, injury guesses, or the code)
    hp = ROOT / "docs" / "data" / lid / ".inputs"
    srcs = [ROOT / "data" / lid / "model.json", ROOT / "data" / "usage.json", ROOT / "config.json", ROOT / "assumptions.json"] + sorted((ROOT / "scripts").glob("*.py"))
    hsh = hashlib.sha256()
    for f in srcs:
        if f.exists():
            b = f.read_bytes()
            if f.name == "model.json":
                m_ = json.loads(b); m_.pop("at", None); b = json.dumps(m_, sort_keys=True).encode()
            hsh.update(b)
    hsh = hsh.hexdigest()
    if "--force" not in sys.argv and hp.exists() and hp.read_text() == hsh and (ROOT / "docs" / "data" / lid / "latest.json").exists():
        print(f"{lid}: no changes, skipping sims")
    elif run(["scripts/analyze.py"], {"LEAGUE": lid}) != 0:
        print(f"analysis failed for {lid}")
    else:
        hp.write_text(hsh)
    lp = ROOT / "docs" / "data" / lid / "latest.json"
    if lp.exists():
        L = json.loads(lp.read_text()); s = L["summary"]
        index.append({"id": lid, "name": s["league"], "team": s["my_team"], "week": s["week"], "at": s["at"], "champ_pct": s["kpis"]["ch"], "todo": L.get("todo", [])[:4]})
default = str(CFG.get("default_league"))
index.sort(key=lambda x: (x["id"] != default, x["name"] or ""))
(ROOT / "docs" / "data" / "leagues.json").write_text(json.dumps({"leagues": index, "status": status}, indent=1))
print(f"site index: {len(index)} league(s)")
