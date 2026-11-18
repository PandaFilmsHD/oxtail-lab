"""Send one realistic TEST alert (with its in-app report) so Alex can see what an in-game injury alert looks like."""
import json, pathlib, datetime, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from notify import send, SITE
ROOT = pathlib.Path(__file__).resolve().parents[1]
D = json.loads((ROOT / "docs/data/787523197/latest.json").read_text())
te = sorted([x for x in D["rankings"] if x["pos"] == "TE" and x["owner"] == "Free agent"], key=lambda x: -x["ros_ppg"])[:2]
ch = D["summary"]["kpis"]["ch"]
a = {"id": "mockbowers", "at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "player": "Brock Bowers", "leagues": ["Od from oxtail"],
     "title": "🚨 TEST: Bowers questionable to return (ankle)",
     "body": "Went to the locker room in the 2nd quarter. Starting in Od from oxtail.",
     "report": {
         "what": "TEST (this didn't really happen). Brock Bowers went to the locker room late in the 2nd quarter vs Denver after a defender rolled up on his right ankle on a short catch. The Raiders list him questionable to return. He had 4 catches for 52 yards (11.2 fantasy pts) before he left.",
         "means": f"If he doesn't come back you're stuck with his 11.2 for this game. The bigger question is the playoffs: ankle injuries like this usually show up on the next week's practice report, so until we see practice we'd drop him to about 60% for next week. Your title odds would go from {ch}% to roughly {round(ch * 0.9, 1)}% if he misses time.",
         "todo": "Nothing to do for this game, since lineups lock once it starts. For next week you don't roster a backup TE, so watch his Wednesday practice. If he's limited or out, the best TE on waivers is " + " or ".join(f"{t['player']} ({t['ros_ppg']} a game)" for t in te) + ". You'll get another alert the moment his status changes."},
     "actions": [{"label": "BOWERS CARD", "player": "Brock Bowers"}, {"label": "WAIVERS", "tab": "waivers"}]}
p = ROOT / "docs/data/alerts.json"
feed = json.loads(p.read_text()).get("alerts", []) if p.exists() else []
feed = [a] + [x for x in feed if x["id"] != a["id"]]
p.write_text(json.dumps({"alerts": feed[:60]}, separators=(",", ":")))
send(a["title"], a["body"], priority=5, click=f"{SITE}?league=787523197&alert={a["id"]}", tag=a["id"] + str(int(datetime.datetime.now().timestamp())), alert=a)
