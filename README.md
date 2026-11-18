# Oxtail Lab

Title odds, trade targets, waiver picks and rest-of-season / playoff rankings for the **Od from oxtail** ESPN league.
A GitHub job pulls the league from ESPN plus free NFL advanced stats from nflverse a few times a day, reruns
the sims, and updates the site.

## One-time setup

### 1. Add your ESPN cookies as secrets
ESPN private leagues need two login cookies. They work like a password, so they go in GitHub **secrets**
(encrypted, never shown in the code or the site).

1. On a computer, open Chrome and log into ESPN Fantasy (open your league page).
2. Press `F12` (or right click → Inspect) → **Application** tab → **Cookies** → `https://fantasy.espn.com`.
3. Copy the value of **espn_s2** (long string) and **SWID** (looks like `{XXXXXXXX-XXXX-...}`, keep the braces).
4. In this repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Add `ESPN_S2` with the espn_s2 value, and `SWID` with the SWID value.

### 2. Turn on the website
**Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main`, folder `/docs` → Save.**
The site shows up at `https://<your-username>.github.io/oxtail-lab/` a minute later.

### 3. Run it
**Actions tab → "Update league + sims" → Run workflow.** After that it runs on its own about 6am, 11am, 5pm and 10pm
Pacific, plus right after waivers clear Wednesday morning.

## Changing things

| What | Where |
|---|---|
| League settings (scoring, PPR, lineup slots, playoff teams) | Pulled from ESPN automatically. To test something different, add it under `league_overrides` in `config.json`. |
| Injury guesses (chance a hurt player plays each week) | `assumptions.json` → `avail` |
| Force a player's value | `assumptions.json` → `override` |
| Players you'd never trade | `assumptions.json` → `core` |
| Teams you won't trade with | `assumptions.json` → `no_trade_teams` (ESPN team ids) |
| How many sims | `assumptions.json` → `sims` |

You can edit any of these right on github.com (open the file → pencil icon → commit). The next run uses them.

## What's in it

- **scripts/pull_espn.py**: rosters, standings, schedule, scores, injuries, projections, ownership, ESPN outlooks,
  defense-vs-position ranks and the league's own settings.
- **scripts/pull_nflverse.py**: target share, air yards share, WOPR, carry share, snap %, red zone and goal line usage,
  expected fantasy points (rescored to this league's settings), Next Gen Stats, PFR advanced stats, official injury
  reports, depth charts, team pace and pass rate over expected.
- **scripts/analyze.py**: player values, Monte Carlo season + bracket sims, trade search, waiver ranking, rankings,
  bubble teams, the auto-written take, and the change log.
- **docs/**: the website.

## Run it on your own computer (optional)
```
pip install -r requirements.txt
export ESPN_S2="..." SWID="{...}"
python scripts/pull_espn.py && python scripts/pull_nflverse.py && python scripts/analyze.py
cd docs && python -m http.server   # open http://localhost:8000
```
