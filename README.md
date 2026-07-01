# ⚾ MLB DFS Projection & Contest Engine

A local-first, **100% free** desktop app that turns sportsbook odds into MLB
fantasy projections, scores your draft contests, tracks results, and (in later
phases) learns your opponents to predict their draft picks.

- **Free & local only.** Python stdlib, SQLite, Streamlit, pandas/numpy,
  rapidfuzz, scikit-learn, and free local OCR (Phase 2). No paid APIs, no cloud,
  nothing recurring.
- **One SQLite file** holds all data — back up the app by copying it.

> **Status: Phases 1–4 complete** — projections + registry (Phase 1); draft
> ingestion (OCR/manual), snake-order reconstruction, fuzzy matching, and contest
> scoring (Phase 2); substitutions, settling, opponent H2H, History & Stats, and
> calibration (Phase 3); plus opponent tendency extraction, template scouting
> reports, and the Opponents page (Phase 4). The last phase adds the Monte Carlo
> draft simulator.

---

## Setup

Requires **Python 3.11+**.

```bash
cd mlb-dfs

# 1. Create a virtual environment (3.11 recommended)
py -3.11 -m venv .venv            # Windows
# python3.11 -m venv .venv        # macOS/Linux

# 2. Activate it
.venv\Scripts\activate            # Windows (PowerShell/cmd)
# source .venv/bin/activate       # macOS/Linux

# 3. Install (editable, with dev tools)
pip install -e ".[dev]"
# or:  pip install -r requirements.txt
```

### Screenshot OCR (free)

The **New Contest** page reads a draft-board screenshot via free local OCR. The
Python side (Pillow + pytesseract) is already in `requirements.txt`; you also
need the free **Tesseract** engine:

- **Windows:** install from the
  [UB-Mannheim build](https://github.com/UB-Mannheim/tesseract/wiki) and make
  sure `tesseract.exe` is on your PATH (the app also auto-detects the default
  install location, or set the `TESSERACT_CMD` env var).
- **Streamlit Cloud:** installed automatically via `packages.txt`.

OCR is optional — if Tesseract isn't found, the page says so and you use the
**manual grid entry**, which is always available.

### Put it in git / push to GitHub

```bash
cd mlb-dfs
git init
git add .
git commit -m "MLB DFS engine"
# then create a GitHub repo and:
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

## Run the app

```bash
streamlit run app.py
```

Your browser opens to the Home page; use the sidebar to reach **Projections**.

## Deploy to Streamlit Community Cloud (free)

The repo is ready to deploy:

1. Push this folder to a **GitHub repo** (see "Put it in git" below).
2. Go to <https://share.streamlit.io>, sign in with GitHub, and click **Create app**.
3. Pick your repo/branch, set **Main file path** to `app.py`, and deploy.

Streamlit Cloud auto-installs `requirements.txt` (Python deps) and `packages.txt`
(the free **Tesseract** OCR engine), so screenshot OCR works there too.

> ⚠️ **Data persistence:** Streamlit Cloud's filesystem is **ephemeral** — the
> single SQLite file (`data/dfs.db`) is **wiped on reboot or redeploy**. Set up
> free **Turso** persistence (below) so your data survives. Also note free
> Community Cloud apps are public by default; you can restrict viewers in the
> app's settings.

## Data persistence with Turso (free)

To keep your data across reboots/redeploys on the cloud, the app can store a
copy of its SQLite database in a **free [Turso](https://turso.tech) database**
and sync automatically: it **restores** on startup and **auto-saves** in the
background. Everything else keeps using local SQLite, so it's transparent.

**One-time setup:**

1. Sign up at [turso.tech](https://turso.tech) (free; GitHub login).
2. Create a **database** (any name, e.g. `drafters`).
3. Copy its **URL** (looks like `libsql://drafters-yourorg.turso.io`) and create
   a **database token** (Turso dashboard → your DB → *Create Token*, or
   `turso db tokens create <db>` with the CLI).
4. On Streamlit Cloud: your app → **⋮ → Settings → Secrets**, and add:
   ```toml
   TURSO_DATABASE_URL = "libsql://drafters-yourorg.turso.io"
   TURSO_AUTH_TOKEN = "your-token-here"
   ```
   Save — the app restarts and the Home page shows **☁️ Cloud persistence is ON**.

That's it. The Home page also has **Save to cloud now**, **Restore from cloud**,
and **Test connection** buttons.

> **Locally**, your data already persists on disk, so Turso is optional. If you
> *do* want your laptop and the cloud app to share data, set the same two values
> as environment variables (`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`) before
> running `streamlit run app.py`.

## Run the tests

```bash
pytest
```

The suite covers the projection math (known input → known `proj`), the singles
residual logic, dedupe, auto-flags, registry name/alias matching, snake-order
reconstruction, fuzzy name matching, contest scoring (incl. the Ohtani-type and
DNP cases), the OCR cell parser, substitutions, settling + opponent H2H, stats
and calibration, an end-to-end smoke test (sample CSV → projections → roster
total), and headless render tests for every Streamlit page.

## Seed a demo database

```bash
python scripts/seed.py
```

Builds `data/demo.db` from the sample props, assigns demo positions, saves a
slate with two pitchers, and prints the projection table.

---

## Draft Board (temporary live-draft helper)

A scratch board for drafting: open **Draft Board** in the sidebar, load a saved
slate (or upload a tidy CSV), and you get three columns — **IF / OF / P** —
listing Team · Player · Proj, sorted high to low, just like a cheat sheet.

- Click **✕** next to a player to mark them taken; they strike through and (with
  **Hide taken** on) drop out so you instantly see who's left.
- **Find player** filters by name to cross someone off fast.
- **Reset** clears all marks; closing the app clears everything too.

It's **temporary by design** — the "taken" marks live only in the browser
session and are never written to the database. Flex bats (IF+OF) and Ohtani-types
(IF+P) appear in each of their eligible columns. CSV columns (case-insensitive):
`name`, `position` (IF/OF/P), `proj`, and optional `team`.

> **Note on Team:** the props CSV doesn't say which side each batter is on, so
> the Team column comes from the registry and may be blank until a later phase
> captures it. Names + projections still work fully.

## Contests (New Contest + Active Contests)

**New Contest** turns a drafted board into scored side-by-side teams:

1. Pick the **slate** to score against (its projections supply the numbers).
2. Set the number of **drafters** and **rounds**, name the drafters, and tick
   which seat is **you**.
3. **Fill the board** — either run **OCR** on a screenshot (optional) or click
   **Build / reset grid** and type the picks. The grid is pre-laid in **snake
   order** (seat order reverses each round), and every pick keeps its
   `overall_pick_number` (the opponent model in Phase 5 needs the full order).
4. **Resolve players** — each name is fuzzy-matched to the registry; strong
   matches auto-select, weak ones default to "create new". Confirm, and the
   spelling is saved as an alias so it auto-resolves next time.
5. **Score & save** — see the leaderboard (projected leader 👑 and you ⭐
   highlighted) and per-drafter boards, with each team's **floor**,
   **pitching/hitting split**, and **DNP** flags. Saved as `active`.

Scoring rules of note: a hitter dropped into a **P** slot with no pitcher
projection uses his batter projection (the **Ohtani-type** case, flagged 🔁); a
drafted player not in the slate counts as **0** (DNP, flagged ⛔).

**Active Contests** lists saved contests and re-renders their scored boards, and
(Phase 3) lets you:

- **Substitute** a player on any entry (pick a replacement from the slate) →
  the pick's projection and the entry total recompute, and the swap is logged.
- **Settle** a contest → enter each team's actual score and your result; finishes
  are ranked, the contest is marked `completed`, and **opponent H2H records** +
  your stats update. Re-settling is safe (opponent aggregates recompute from
  scratch).

## History & Stats

The **History & Stats** page (Phase 3) summarizes your settled contests:

- **Record, win rate, ROI, profit, best/worst, current streak, average finish.**
- **Records by site and by draft slot**, and a **head-to-head ledger** per
  opponent (your W–L against them + their average score).
- **Projection calibration:** `actual − projected` per contest over time for you
  and your opponents — positive means the model ran **cold** (under-projected),
  negative means it ran **hot**.
- A **filterable history** (by site, draft slot, opponent, status) where you can
  open any contest's scored board.

## Opponents & scouting

The **Opponents** page (Phase 4) profiles each drafter from their pick history.
Because every pick stores its `overall_pick_number`, the engine reconstructs the
pool that was available at each of their picks and computes:

- **Positional profile by round bucket** (R1–2, R3–5, R6–10, R11–16): share of
  P / IF / OF / HT, plus pitchers per draft and the average round of their first
  pitcher.
- **Value adherence** — the average rank of the player they took among all
  available, overall and within position (1 = best available).
- **Player affinity** — `drafted ÷ available`, shrunk toward the league base rate.
- **Team affinity** — their team draft share vs the league's.
- **Stacking coefficient** — P(take a same-team player | they already have one) ÷
  the baseline rate (>1 stacks, <1 diversifies).

Recent contests are weighted more (exponential time decay), and a **template
scouting report** (no LLM) turns the numbers into plain sentences: how they open,
their value discipline, favorite players/teams, stack-vs-diversify, and one
exploitable weakness. Tendencies recompute when you settle a contest, or on
demand via the page's **Recompute** button.

### Draft-board screenshot OCR

Screenshots vary in zoom, round count, and player count (2–4), so the OCR does
**not** assume fixed cell sizes. Instead it **auto-detects each colored pick
box**, clusters the boxes into columns (drafters) and rows (rounds), and reads
each box by corner:

- **name** — top-left
- **pick #** — top-right (used only as a sanity check; the authoritative pick
  number is computed from the box's row/column via the **snake order**, which is
  far more reliable than OCR'ing a tiny digit)
- **position** (`P`/`IF`/`OF`/`HT`) — under the pick number
- **team** — bottom-left

Upload the board, eyeball the green-box detection preview (a brightness slider
handles unusual themes), then run OCR. The player and round counts are filled in
automatically, and everything lands in the editable grid for you to correct
before resolving names. The manual grid is always available as a fallback.

## How projections work (the "ladder method")

Sportsbook props come as several over/under "rungs" per `(player, market)`.
Because `over_prob` at the 0.5 line = P(stat ≥ 1), at 1.5 = P(stat ≥ 2), and so
on, and **E[X] = Σ P(X ≥ k)**, the expected value of a counting stat is just the
**sum of `over_prob` across its rungs**.

- **HR, 2B, 3B, R, RBI, SB:** `E[X] = Σ over_prob`.
- **Singles:**
  - If a `batter_singles` market exists → `E[1B] = over_prob of its lowest rung`.
  - Otherwise (residual) →
    `E[1B] = max(0, P1(hits) − P1(HR) − P1(2B) − P1(3B))`,
    where `P1(market)` is that market's lowest-rung `over_prob` (missing → 0).
- **BB / HBP:** flat baselines `0.08` and `0.011`.

Final projection (default scoring `R=2, 1B=2, 2B=4, 3B=6, HR=8, RBI=2, BB=2,
HBP=2, SB=3`, no strikeout penalty), rounded to 2 decimals:

```
proj = 2·E[R] + 2·E[1B] + 4·E[2B] + 6·E[3B] + 8·E[HR]
     + 2·E[RBI] + 2·0.08 + 2·0.011 + 3·E[SB]
```

The per-stat components (`E[R]`, `E[1B]`, …) are stored alongside `proj_pts` for
auditing.

### Positions

Positions come from a **persistent player registry**, never a hardcoded dict.
There are only three roster groups: **`IF`**, **`OF`**, and **`P`** (pitcher).
A player can be eligible at **more than one** — a flex bat is `IF+OF`, and an
Ohtani-type is `IF+P`. (Granular positions like `SS`/`CF` from any upstream
source are folded into these groups: `OF = {LF, CF, RF}`,
`IF = {C, 1B, 2B, 3B, SS, DH}`.)

When a player isn't in the registry, the Projections page queues them so you
tick the IF/OF/P boxes once; the assignment (and any name aliases) is stored
forever and auto-resolves on future slates.

### Pitchers

Pitcher projections are entered by hand (name + number) and stored per slate.
No formula.

### Auto-flags

- A game where most batters project near zero → flagged `in_progress`.
- A game with many batters projecting 9+ → flagged `inflated`.

Flags surface as warnings; players are never dropped.

---

## Input CSV format (batter props)

Columns:

```
player, normalized_market_key, point, over_prob, game,
commence_time_local, away_team, home_team
```

`normalized_market_key` is one of: `batter_home_runs`, `batter_doubles`,
`batter_triples`, `batter_singles`, `batter_hits`, `batter_runs_scored`,
`batter_rbis`, `batter_stolen_bases`. Each `(player, market)` has one row per
`point` line ("rung"), with `over_prob` = probability the stat goes over that
line. See `sample_data/sample_props.csv`.

---

## Project layout

```
mlb-dfs/
├─ app.py                  # Streamlit entrypoint (Home)
├─ pages/
│  ├─ 1_Projections.py     # Projections page
│  ├─ 2_Draft_Board.py     # temporary live-draft board
│  ├─ 3_New_Contest.py     # ingest + score a drafted contest
│  ├─ 4_Active_Contests.py # view / substitute / settle contests
│  ├─ 5_History_and_Stats.py # record, ROI, calibration, H2H, history
│  └─ 6_Opponents.py       # scouting reports + tendency charts
├─ src/dfs/
│  ├─ config.py            # scoring weights, baselines, position groups
│  ├─ db.py                # SQLite connection + full schema + migrations
│  ├─ registry.py          # persistent player registry + position resolution
│  ├─ projections.py       # the ladder-method projection engine
│  ├─ slate.py             # persist slates / projections to the DB
│  ├─ draftboard.py        # draft-board data loaders (read-only)
│  ├─ draft.py             # snake-order reconstruction
│  ├─ matching.py          # rapidfuzz name matching against the registry
│  ├─ ocr.py               # free local OCR for board screenshots (+ parser)
│  ├─ contest.py           # contest scoring, substitutions, settling
│  ├─ opponents.py         # opponent H2H tracking
│  ├─ tendencies.py        # opponent draft-tendency extraction
│  ├─ scouting.py          # template scouting reports (no LLM)
│  ├─ stats.py             # history + stats aggregations (read-side)
│  └─ boards_ui.py         # shared Streamlit board rendering
├─ scripts/seed.py         # demo database builder
├─ scripts/debug_ocr.py    # diagnose a board screenshot through OCR
├─ sample_data/sample_props.csv
├─ tests/                  # math, registry, draft, matching, contest, OCR, pages
├─ data/                   # SQLite files live here (dfs.db, demo.db)
├─ pyproject.toml
└─ requirements.txt
```

## Pages

| Page | Phase | What it does |
|------|-------|--------------|
| **Projections** | 1 ✅ | Upload props CSV, assign unknown positions, enter pitchers, view/sort/filter/export the projection table, save the slate. |
| **Draft Board** | extra ✅ | Temporary live-draft board (IF / OF / P columns, sorted by projection). Cross players off as they're taken to see who's left. Session-only — nothing is saved. |
| **New Contest** | 2 ✅ | Draft screenshot (OCR) or manual grid entry, confirm fuzzy matches, see scored boards, save as active. |
| **Active Contests** | 2–3 ✅ | View scored boards, substitute players, settle results (updates opponent H2H + your stats). |
| **History & Stats** | 3 ✅ | Filterable history, win rate / ROI, records by site & slot, H2H ledgers, projection calibration. |
| **Opponents** | 4 ✅ | Template scouting reports + tendency charts (positional profile, value adherence, player/team affinity, stacking). |
| Draft assistant | 5 | Monte Carlo simulator, survival probabilities, snipe alerts. |

---

## Roadmap (build phases)

1. ✅ Scaffold + DB schema + projection engine + player registry + Projections page + math tests.
2. ✅ Screenshot ingestion (OCR + manual fallback) + contest scoring + New/Active pages.
3. ✅ Substitutions + settle + history + my stats + calibration.
4. ✅ Opponent tendency extraction + template scouting reports + Opponents page.
5. Pick-probability model + Monte Carlo draft simulator + Draft-assistant page.
