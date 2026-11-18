"""FantasyPros consensus weekly projections (an average of many experts) -> data/fp_proj.json.
Raw stats are saved so analyze.py can score them with each league's own settings (6-pt pass TDs, PPR...)."""
import json, pathlib, re, time, datetime
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fp_proj.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}


def rows_from(html):
    i = html.find('"rows":[')
    if i < 0:
        return []
    return json.JSONDecoder().raw_decode(html[i + 7:])[0]


def main():
    cur = None
    for lp in sorted((ROOT / "data").glob("*/model.json")):
        cur = json.loads(lp.read_text()).get("cur"); break
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    now = datetime.datetime.now(datetime.timezone.utc)
    if old.get("week") == cur and old.get("at") and (now - datetime.datetime.fromisoformat(old["at"])).total_seconds() < 6 * 3600:
        print("fantasypros: fresh enough, skipping"); return
    out = {}
    for pos in ("qb", "rb", "wr", "te"):
        r = requests.get(f"https://www.fantasypros.com/nfl/projections/{pos}.php", params={"week": cur, "scoring": "PPR"}, headers=UA, timeout=30)
        r.raise_for_status()
        for x in rows_from(r.text):
            pl = x.get("player") or {}
            if pl.get("name"):
                out[f"{pl['name']}|{pl.get('team') or ''}"] = {k: v for k, v in x.items() if isinstance(v, (int, float)) and k not in ("id",)} | {"pos": pos.upper()}
        time.sleep(1)
    if len(out) > 100:
        OUT.write_text(json.dumps({"week": cur, "at": now.isoformat(), "players": out}, sort_keys=True, separators=(",", ":")))
    print(f"fantasypros: {len(out)} players for week {cur}; stat keys e.g. {sorted(next(iter(out.values())).keys()) if out else []}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("fantasypros: skipped", e)
