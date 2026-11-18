"""Phone alerts through ntfy (free push app). The topic name is the NTFY_TOPIC GitHub secret.

python3 scripts/notify.py test        -> sends one sample alert
(the update run imports send() to push real alerts)
"""
import os, sys, json, requests

SITE = "https://pandafilmshd.github.io/oxtail-lab/"


def send(title, body, tags=None, priority=3, click=None):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("notify: no NTFY_TOPIC set, skipping")
        return False
    r = requests.post("https://ntfy.sh/", json={"topic": topic, "title": title, "message": body, "tags": tags or [],
                                                "priority": priority, "click": click or SITE}, timeout=20)
    print(f"notify: {title} -> {r.status_code}")
    return r.ok


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "test":
    send("🚨 Bowers ruled OUT (test)", "Brock Bowers is OUT for Week 11. You have no backup TE, so pick one up: best free agent this week is Colby Parkinson (8.4 proj). Tap to open Oxtail.", ["rotating_light", "football"], 5)
    send("🔒 Lineup locks Thu 5:15 PM (test)", "Stroud, Fairbairn, McGowan and Hutchinson play Thursday. Lineup looks good: no one on bye or out.", ["lock"], 3)
    send("🤕 Puka update (test)", "ESPN: Nacua (knee) expected back mid-December. Oxtail now has him 65% for Week 15, 80% Week 16. Title odds 28.9% → 30.2%.", ["chart_with_upwards_trend"], 3)
