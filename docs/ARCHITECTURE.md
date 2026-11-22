# Architecture — Weather Risk Intelligence Agent

A chat agent that helps a US logistics company decide which distribution hubs are most exposed to
weather disruption (winter, hurricane, flood) and should get resilience investment. It answers
questions such as *"Which hubs in the Midwest are most exposed to winter disruption?"* or *"Why is the
Dallas hub's risk high?"* with scores, the reasoning behind them, and the assumptions they rest on.

**Core principle: code computes, the LLM explains.** Every number, score and ranking is produced by
deterministic Python over stored data. The model only chooses tools, passes arguments and explains the
results. This is enforced in code, not only asked for in the prompt (see [7.3](#73-guarantees-enforced-in-code)).

Contents: [1 Components](#1-components-and-how-they-talk) · [2 Repository](#2-repository-structure) ·
[3 Data sources](#3-data-sources) · [4 Storage](#4-storage-and-ingestion) · [5 Scoring](#5-scoring) ·
[6 Hubs](#6-hubs) · [7 Agent](#7-agent) · [8 Chat UI](#8-chat-ui) · [9 Evaluation](#9-evaluation) ·
[10 Assumptions & limitations](#10-assumptions-uncertainty-and-limitations) ·
[11 Decisions](#11-decisions-log) · [12 Future work](#12-future-work)

---

## 1. Components and how they talk

```
 Offline (once / nightly)                         Online (per question)
 ────────────────────────                         ─────────────────────
 NOAA ACIS ──┐                                    React chat UI  (frontend/, Vite dev server :5173)
 Open-Meteo ─┼─► ingestion ──► SQLite ◄──┐            │  POST /chat {message, session_id}
 FEMA NRI ───┘   (backend/     data/      │            ▼   (Vite proxies /chat, /health → :8000)
                  ingestion/)  weather.db │       FastAPI  (backend/app.py)
                                          │            │
                                          │            ▼
                                          │       Agent loop  (backend/agent/loop.py) ◄──► Claude API
                                          │            │  tool calls          (Anthropic SDK)
                                          │            ▼
                                          └──── Tool functions  (backend/tools/) ──► Scoring (backend/scoring/)
```

| Component | Responsibility | Talks to |
|---|---|---|
| **Ingestion** (`backend/ingestion/`) | Fetch daily station data, model wind gusts and FEMA NRI county data; store raw rows | Public APIs → SQLite. Run by hand (`python -m backend.ingestion.run`), ~15 s |
| **SQLite** (`data/weather.db`) | Raw daily weather per hub, NRI values, ingestion runs, score snapshots | Read by tools and scoring |
| **Scoring** (`backend/scoring/`) | Metrics → fixed-anchor 0–100 scores → hazard and overall scores with a full breakdown | Pure functions over SQLite |
| **Tools** (`backend/tools/`) | The 5 functions the agent may call (rank, compare, explain, metric, list) | Scoring + SQLite |
| **Agent loop** (`backend/agent/`) | System prompt, tool definitions, the call → tools → call loop, validation and grounding checks | Claude API (Messages API, plain SDK, no framework) + tools |
| **API** (`backend/app.py`) | `POST /chat`, `GET /health`; per-session memory; attaches deterministic hub scores for the UI | Agent loop |
| **Chat UI** (`frontend/`) | Conversation, answer card, scores table, provenance, warnings | `/chat` via the Vite proxy |
| **Evaluation** (`eval/`) | 19 cases with code-only checks, run against the agent in-process | Agent loop + tools (for expected values) |

**Lifecycle of one question**
1. The UI posts `{message, session_id}` to `/chat`. FastAPI looks up the session's message history.
2. The agent loop sends history + system prompt + tool definitions to Claude.
3. Claude replies with tool calls (often several in parallel). The loop runs them against SQLite and
   sends all results back in one message. Usually one round is enough.
4. Claude ends by calling `submit_answer` with the structured response. The loop validates it
   (Pydantic), checks that its numbers came from tool results, and stores the turn.
5. `/chat` adds the hub scores table data (from the scoring code, not the model) and returns
   answer, grounding result, tool calls, timing and token usage. The UI renders them.

The agent never calls an external API at question time; all weather and risk data is read from SQLite.

### Deployment and access control
The live demo (https://weather-risk-agent-vh6p.onrender.com) is **one Render web service** built from
the repo's `Dockerfile`: the FastAPI backend also serves the built UI, so the browser talks to one
origin and the code locally and in production is the same. Configuration is in `render.yaml`;
step-by-step instructions in [DEPLOY.md](DEPLOY.md).

- **Data**: the image contains the committed `data/weather.db` snapshot (2016–2025); nothing is
  ingested at deploy time, so a deploy cannot fail because a public data API is down, and the answers
  match the documented numbers. `ANALYSIS_LAST_YEAR=2025` pins the period to that snapshot.
- **Secrets**: `ANTHROPIC_API_KEY` and `ACCESS_CODE` exist only in Render's environment settings
  (`sync: false` in `render.yaml`); `.dockerignore` keeps `.env` out of the image. Checked after going
  live: no key in the repository's history or in the shipped JavaScript.
- **Access control** (`backend/security.py`), enforced by the backend on **every `/chat` request**,
  not only in the UI:
  1. one shared **access code** (header `X-Access-Code`, constant-time comparison); 10 wrong codes per
     IP per hour block that IP for the hour. With `APP_ENV=production` the app refuses to start without
     a code, so it cannot be deployed open by mistake;
  2. a **$1/day cap** on Claude API spend, counted from each answer's real token usage;
  3. **rate limits**: 40 questions per visitor IP and 60 in total per rolling hour.
  The visitor IP is read from `True-Client-IP` (set by Cloudflare in front of Render — confirmed on the
  live service), never from `X-Forwarded-For`, which a caller can forge there.
- Sessions resume only server-issued ids and are capped at 500. The API docs pages (`/docs`,
  `/openapi.json`) are not published in production.
- **Free tier**: the service sleeps after 15 minutes without traffic, and the first request then waits
  about a minute (decision D18).

## 2. Repository structure

```
backend/
  settings.py           paths and the analysis period (last full year, 10-year window)
  db.py                 SQLite schema and connection
  app.py                FastAPI: POST /chat, POST /access, GET /health; serves the built UI
  security.py           access code, rate limits, daily spend cap
  ingestion/            acis.py (station data), open_meteo.py (gusts), nri.py (FEMA NRI snapshot),
                        common.py (hub sync, run bookkeeping), run.py (CLI)
  scoring/              config.py (all thresholds, anchors, weights), metrics.py, normalize.py, score.py
  tools/                hubs.py (aliases, list_hubs), scores.py (rank/compare/explain), metric.py
  agent/                config.py (.env model settings), prompt.py, tool_defs.py, schema.py
                        (response contract), loop.py (agent loop, grounding, sessions), pricing.py
frontend/               Vite + React + TypeScript chat UI (src/App.tsx, src/components/, src/index.css)
data/
  hubs.json             the 12 hubs (assumed list; replace with the client's)
  weather.db            the SQLite database: committed snapshot the deployment serves (2016-2025)
  raw/                  nri_counties.csv snapshot (+ .meta.json); probe/ = raw samples from the data check
eval/                   cases.yaml, run_eval.py, results/ (saved runs with every answer)
tests/                  85 pytest tests (parsing, data sanity, scoring, tools, agent loop, API, eval
                        checks, security)
scripts/                probe_sources.py (data check), data_report.py, print_scores.py, ask.py (CLI
                        client), smoke_test.py (checks a deployment)
docs/                   ARCHITECTURE.md, DEPLOY.md, screenshots/, sessions/ (Claude Code session exports)
Dockerfile, render.yaml the deployment: one Render service (API + built UI)
CLAUDE.md               project brief and working rules for Claude Code
```

## 3. Data sources

| Source | What we use | Access |
|---|---|---|
| **NOAA ACIS** (`data.rcc-acis.org/StnData`), the service behind NWS "NOWData" | Daily snowfall, minimum temperature, precipitation measured at each hub's airport climate station, 2016–2025 | POST JSON, no key |
| **Open-Meteo Historical API** (`archive-api.open-meteo.com`) | Daily maximum wind gust only, at the station coordinates (context metric, not scored) | GET, no key |
| **FEMA National Risk Index**, December 2025 (v1.20.0) | County annualized event frequency (`*_AFREQ`) for winter weather, hurricane, inland and coastal flood (scored) and tornado, hail, ice storm (context) | FEMA's official ArcGIS service (below); snapshot in `data/raw/` |
| **NWS** `api.weather.gov/alerts` | Verified reachable; reserved for the optional live-alert feature (not built) | GET, generic `User-Agent` |

**Why station data, not model data.** The brief proposed Open-Meteo for everything. A side-by-side
check at the same coordinates showed that modelled data gets yearly totals roughly right but smooths
the daily extremes that every scoring metric counts:

| 2025, at the airport station | Denver station | Denver Open-Meteo | Miami station | Miami Open-Meteo |
|---|---|---|---|---|
| Measurable snow days (≥ 0.1 in) | **26** | 38 | 0 | 0 |
| Snow days ≥ 1 in | **12** | 5 | 0 | 0 |
| Heavy rain days (≥ 25 mm) | **4** | 0 | **20** | 5 |
| Total precipitation (mm) | 391 | 475 | 1,471 | 1,457 |

The Denver figure (26 days) was independently confirmed on NWS NOWData. Open-Meteo stays only for
wind gusts, because no free source of measured daily peak gusts exists (NCEI `WSFG` is empty).

**Source notes**
- `hazards.fema.gov` (the NRI download page) resets the TLS connection from our network. The same data
  is published by FEMA's own ArcGIS account `FEMA_NationalRiskIndex`:
  `https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0`
  (item page `https://www.arcgis.com/home/item.html?id=39485e8035d446a5bff03259508ae355`, Living
  Atlas, `NRI_VER` = "December 2025", v1.20.0). The selected fields for all 3,232 counties are saved to
  `data/raw/nri_counties.csv`, so the app does not depend on the service at runtime.
- NRI's `*_RISKS` scores were rejected: they include dollar exposure and social vulnerability and
  saturate for large counties (inland flood `RISKS` is 95–100 for all 12 hubs). We use event frequency.
- NRI `null` means "hazard not applicable in this county" (e.g. hurricane in Denver); it counts as 0
  events, not as missing data. December 2025 renamed riverine flood to Inland Flooding (`IFLD_*`).
- ACIS codes: `T` (trace) counts as 0; `M` (missing) is stored as NULL. Any other code stops ingestion.
- The data check itself is reproducible: `scripts/probe_sources.py`, raw samples in `data/raw/probe/`.

## 4. Storage and ingestion

**Why SQLite.** One file, no server, nothing to install; ~44k daily rows for 12 hubs × 10 years is
small. The SQL is plain, so moving to Postgres means changing the connection, not the queries. The
agent and tools only read; ingestion and score snapshots are the only writers.

| Table | Content |
|---|---|
| `hubs` | Synced from `data/hubs.json` (removed hubs are deleted with their data) |
| `daily_weather` | One row per hub per local calendar day: `snow_cm`, `tmin_c`, `precip_mm` (ACIS), `gust_kmh` (Open-Meteo), and the ingestion run that wrote each source |
| `nri` | `(hub_id, field) → value` for the hub's county; NULL kept as NULL |
| `ingestion_runs` | Source, time, status, rows written, data-quality details (trace / missing counts, Open-Meteo grid cell) |
| `score_runs`, `hub_scores` | Timestamped score snapshots with a hash of the scoring settings (for a future score-change alert) |

- **Raw daily rows, not aggregates**, so any metric or threshold can be recomputed without re-fetching
  (this is also what makes seasonal scores possible later).
- **Idempotent**: every write is an upsert keyed on `(hub_id, date)` or `(hub_id, field)`. Station and
  gust columns are written by separate steps, so each source can be re-run alone.
- Units are stored metric (inches → cm/mm, °F → °C).

## 5. Scoring

All settings live in one file, `backend/scoring/config.py`, each with its rationale; they are
assumptions the client can tune.

1. **Raw metrics** over the 10-year window 2016–2025 (one year is too noisy for risk).
2. **Fixed-anchor normalization** to 0–100: `lo → 0`, `hi → 100`, linear, clamped. Anchors are set
   from references *outside* our hubs, so scores do not depend on which hubs were picked and stay
   comparable over time: station metrics from well-known extreme US stations (same code, same years);
   NRI metrics by one rule, **100 = the US county 99th percentile**.
3. **Hazard score** = weighted average of its metrics.
4. **Overall score = the worst single hazard**, and hubs are ranked by it: a hub closes when one hazard
   hits it, not on an average. The equal-weight average of the three hazards is shown next to it as a
   secondary **multi-hazard** score (breadth of exposure).
5. **Levels**: low < 33 ≤ moderate < 66 ≤ high.

| Hazard | Metric | Anchors (0 → 100) | Weight | Anchor reference |
|---|---|---|---|---|
| Winter | days/yr with snowfall ≥ 1 in | 0 → 25 | 0.4 | Buffalo 24.6, Duluth 24.2, Syracuse 29.4 |
| | days/yr with min temp ≤ −15 °C | 0 → 45 | 0.3 | Duluth 46, Fargo 51.7 |
| | NRI winter weather frequency | 0 → 41 events/yr | 0.3 | US county p99 |
| Hurricane | NRI hurricane frequency | 0 → 0.306 events/yr | 1.0 | US county p99 |
| Flood | days/yr with precipitation ≥ 25 mm | 0 → 25 | 0.4 | New Orleans 22.5, Mobile 20.9 |
| | average of each year's max 3-day precipitation | 25 → 200 mm | 0.4 | ~1 in (nuisance) → ~8 in (major flood) |
| | NRI inland + coastal flood frequency | 0 → 9.08 events/yr | 0.2 | US county p99 |

- **Context, not scored** (returned with every breakdown so the agent can cite it): NRI tornado, hail
  and ice-storm frequency with US county percentile; model wind-gust days ≥ 75 km/h.
- **Missing data**: a missing day is not a dry day. It is excluded from both the count and the number
  of days, and every metric reports its coverage. Below 90 % coverage a metric is unknown, which makes
  its hazard and the overall score unknown (the unknown hazard could be the worst). Nothing is filled with 0.
- `explain_score` returns the full breakdown: each metric's raw value, unit, 0–100 score, weight,
  anchors, coverage and source. `python scripts/print_scores.py` prints the table for all hubs.

Current results (2016–2025): Miami 99.7 (hurricane), Houston 86.3 (flood), Memphis 70.4 (flood) are
high; Phoenix 22.4 is lowest; Dallas is 51.3 (moderate, flood).

## 6. Hubs

12 hubs in `data/hubs.json`, an **assumed** list (the client did not provide one): Midwest — Chicago,
Minneapolis, Detroit, Kansas City, Columbus; South — Dallas, Houston, Miami, Atlanta, Memphis;
West — Denver, Phoenix. Chosen to cover every city in the client's questions, give ≥ 5 Midwest hubs
for a regional ranking, and include a low-risk contrast (Phoenix).

Each hub is placed at its **main-airport NWS climate station** (e.g. Denver → KDEN), and *all* its
weather data comes from that one point. Its NRI county is the county containing the station point
(Chicago → DuPage, Kansas City → Platte, Atlanta → Clayton; the other 9 match the city's county).
Hubs have aliases ("DFW", "Twin Cities", "Kansas City, MO"). Replacing `hubs.json` with the client's
real sites and re-running ingestion is the only change needed.

## 7. Agent

### 7.1 Tools
All tools are `strict: true` (schema-valid arguments guaranteed) and use enums for hazards, regions and
metrics, so out-of-scope values (e.g. "earthquake") cannot be passed in.

| Tool | Returns |
|---|---|
| `list_hubs(region?)` | Hubs, regions, station and county |
| `rank_hubs(hazard, region?)` | Ranking by overall, multi-hazard or one hazard; equal scores share a rank; `points_behind_previous` per hub |
| `compare_hubs(hubs[], hazards[]?)` | Side-by-side scores with metric breakdowns and caveats |
| `get_hub_metric(hub, metric, year?)` | One factual metric for one year or the 10-year window: qualifying days, observed days, % of observed days, coverage, threshold, source |
| `explain_score(hub, hazard?)` | Full breakdown, rank, not-scored context (tornado / hail / ice storm), caveats |
| `submit_answer(...)` | The response contract (below) |

Tools return units, thresholds, period, coverage and source with every number, and return
`not_found` / `no_data` as normal results, never exceptions. **Caveats are generated from the data**,
not hard-coded per hub: county NRI far above the station data (Phoenix flood), two hazards within 3
points (Detroit), coverage below 99 %, tornado counts growing with county size.

### 7.2 Response contract
`submit_answer` input, validated with Pydantic (`backend/agent/schema.py`):
`{answer, hubs_referenced[], scores[{hub_id, name, value, unit}], reasoning, assumptions[],
confidence: high|medium|low, data_sources[]}`.

### 7.3 Guarantees enforced in code
- **The answer is always structured.** The usual way to guarantee a final tool call is to force it
  with `tool_choice`. With the models we use, that is not available: we tested it on 2026-11-22 —
  Sonnet 5.5 (adaptive thinking and `between_tools`) and Opus 5.5, forcing either `submit_answer` or
  "any tool", all returned HTTP 400 `tool_choice: type "tool" and "any" are not supported for this
  model`. The restriction comes from the model, not the thinking setting. So the prompt asks for
  `submit_answer` and the loop checks it: a reply without it gets one reminder; a second miss is a
  clear error. `strict: true` still guarantees schema-valid arguments, and invalid input gets one
  retry, then an error.
- **Numbers are grounded.** Every value in `scores[]` must appear in a tool result for that hub in the
  session (rounding tolerance only); one retry, then the answer is returned flagged. Every number in
  the answer *text* is also matched against tool results, the system prompt (thresholds, years, hub
  count) and the question; unmatched numbers are returned in `grounding.text_unverified` and shown as
  a warning in the UI (warning only: never blocks).
- **Arithmetic stays in code.** When the text check caught the model subtracting two scores itself
  ("only 2.7 points separate them"), the fix was a tool field (`points_behind_previous`) plus a prompt
  rule to quote it — the root cause, not just the warning.
- **History is append-only** (required for prompt caching and for thinking blocks to stay valid). A
  failed turn is cut off the end of the session so it cannot break the next question.
- Limits: at most 8 model calls per question; errors from tools go back to the model, not up the stack;
  API, network and missing-key problems become a clear `error` in the response, never an HTTP 500.

### 7.4 Prompt, caching and model settings
- The system prompt (`backend/agent/prompt.py`) is built once from `hubs.json` and settings: scope,
  how scores work, and rules — code computes, say "no data" instead of guessing, check the premise of
  the question, state thresholds and periods, quote caveats and gaps, never mention tool or field names.
- When an answer references 2+ hubs, the UI shows the scores table, so the prompt asks for ~3 short
  sentences in that case (the conclusion plus what the table does not show). The same constant
  (`TABLE_MIN_HUBS`) drives the prompt and the UI.
- **Prompt caching**: one breakpoint after the system prompt (tools + system ≈ 3.7k tokens, shared by
  every question) and one automatic breakpoint at the end of the history.
- `.env` settings: `AGENT_MODEL` (default `claude-sonnet-5-5`), `AGENT_EFFORT` (default `medium`),
  `AGENT_THINKING` (`adaptive`, or `between_tools` on Sonnet 5.5 only — validated at startup),
  `AGENT_FALLBACKS` (server-side refusal fallback, on).
- Sessions are kept in memory per `session_id` (only server-issued ids resume; at most 500, least
  recently used dropped first) and lost on restart or redeploy.

## 8. Chat UI

`frontend/` — Vite + React + TypeScript, no UI library, plain CSS. Screenshots with captions:
[`docs/screenshots/README.md`](screenshots/README.md).

- **Answer card**: the answer first; a **hub scores table** (winter, hurricane, flood, overall,
  multi-hazard) with the worst hazard outlined; a confidence badge; a **"How I got this"** line listing
  the data tools called and the time taken; a **warning** when the grounding check reports numbers no
  tool returned; collapsible (closed) figures cited, assumptions and data sources.
- The table is filled by the backend from the scoring code (`hub_scores` in the `/chat` response), never
  by the model. It is open by default for rankings and comparisons (2+ hubs) and behind a show/hide
  toggle for single-hub answers.
- Example-question chips on the empty screen, follow-ups in the same session, a "New conversation"
  button, and a loading card with stage messages, elapsed seconds and bouncing dots (static under
  reduced motion).
- **Colour with text labels**: levels green / amber / red, hazards blue / purple / teal; colour always
  sits next to a word, so it works without colour vision. **Light and dark themes** on CSS variables,
  starting from the system setting and remembering the user's choice; every text colour pair meets
  WCAG AA contrast (≥ 4.5:1) in both themes.

## 9. Evaluation

`python eval/run_eval.py` runs `eval/cases.yaml` against the agent in-process (same code path as
`/chat`). 19 cases / 21 questions:

| Group | Cases |
|---|---|
| Client examples (4) | Midwest winter, Miami vs Houston, Denver snowfall %, Dallas false premise |
| Follow-ups (2) | "And how does Kansas City compare on flood?"; South ranking → "Which of *them*…" |
| Out of scope (7) | Seattle, earthquakes, 2010, safest in the fall, next week's forecast, money lost, prompt injection |
| Judgment (4) | Kansas City / Atlanta flood tie, Detroit near tie, "Phoenix is obviously the riskiest, right?", invest in 3 hubs |
| Aliases (2) | DFW → Dallas, Twin Cities → Minneapolis |

**How it checks — all code, no LLM judge.** Every case: a schema-valid answer, grounded numbers, no
unverified numbers in the text, no tool / field names in the text. Per case: tools called, hubs
referenced, which hub is named first (or the first k), required numbers, required / forbidden wording
(regex), no numbers for out-of-scope questions, confidence. **Expected values are computed** with the
agent's own tool functions (e.g. "the top hub of `rank_hubs(winter, Midwest)`"), and **preconditions**
(e.g. "Kansas City and Atlanta are still tied") make a case skip rather than fail wrongly after a data
refresh. `--dry-run` shows the expected values for free; `--budget` caps the spend of a run.
`tests/test_eval_checks.py` shows the checks fail on deliberately bad answers.

**Results** (2026-11-22, every answer saved in `eval/results/`):

| Setting | Cases | Passed | Median / max per question | Cost |
|---|---|---|---|---|
| **Sonnet 5.5, effort medium (default)** | 19 | **19/19** | 5.7 s / 9.7 s | $0.23 |
| Sonnet 5.5, effort low | 19 | 17/19, 19/19 after a prompt fix | 5.8 s / 14.1 s | $0.22 |
| Opus 5.5, effort medium | 6 hardest | 6/6 | 13.3 s / 20.3 s | $0.27 |

Low effort was not faster (most time is spent writing the answer, not thinking). Opus passed the hard
cases too, at about twice the latency and three times the cost. Sonnet 5.5 at medium effort is the default.

**Cost per question** (Sonnet 5.5, medium effort, list prices): the full eval is $0.23 for 21
questions, so **about $0.01 per answer** on average ($0.011). Answers that need no data lookup
(out of scope) cost 0.4–0.6 cents; a single lookup (Denver %, the Midwest ranking) 0.8–1.0 cent;
comparisons and "why" answers 1.4–2.1 cents. Prompt caching keeps
the ~3.7k tokens of tools and instructions at a tenth of the input price after the first question.
Opus 5.5 costs about 3× more per answer.

## 10. Assumptions, uncertainty and limitations

**Scope**
- The **hub list is assumed** and hubs are placed at **airport weather stations**, not real warehouse
  addresses (section 6).
- Three hazards are scored (winter, hurricane, flood). Tornado, hail and ice storm are context only;
  earthquakes, wildfire, heat and others are out of scope.
- No seasonal or monthly scores, no forecasts, no financial losses: the agent says so instead of guessing.
- **"Last year" = calendar year 2025** (Jan–Dec). NWS NOWData shows Jul–Jun snow seasons by default,
  so season totals there will not match.

**Data**
- One airport station per hub: conditions at a warehouse elsewhere in the metro can differ.
- **Wind gusts are modelled** (Open-Meteo reanalysis), smooth out thunderstorm and hurricane peaks, and
  are therefore not scored. Open-Meteo's free tier is non-commercial; production needs the paid plan.
- **NRI is county-level and long-term.** Large counties aggregate a lot of area: Maricopa (Phoenix) is
  the top US county for inland-flood frequency although Phoenix has the fewest measured heavy-rain days
  of our hubs, so NRI flood has a low weight (0.2) and the agent states this caveat. Tornado counts grow
  with county size (rank correlation +0.34 across US counties); flood counts do not (−0.01), so flood
  frequency is not divided by area.
- Missing station days are excluded, not treated as zero; coverage is ≥ 97 % for every hub, and no
  missing snowfall day had both precipitation and a minimum temperature at or below 1 °C.

**Method**
- **Thresholds and weights are expert assumptions, not calibrated** against the client's disruption
  history: −15 °C for extreme cold has no national standard; 1 in of snow and 25 mm of rain are common
  but not universal disruption thresholds. All live in `backend/scoring/config.py`.
- Anchors at national extremes mean no hub may reach "high" on a hazard even if it is the worst of
  our 12 (Minneapolis winter is "moderate"; Duluth and Buffalo are worse). That is intentional.
- **Overall = worst hazard** ignores breadth: a hub moderately exposed to two hazards can rank below one
  with a single, slightly higher hazard. The multi-hazard score is shown for that reason.
- Combining a county-level, long-term NRI frequency with station-measured day counts in one weighted
  average is a modelling choice: the two measure different things (events recorded in the county vs.
  conditions at one point), which is why NRI never carries more than 30 % of a non-hurricane hazard.

**Agent**
- The grounding check matches numbers in the answer text against *any* hub's tool results, not the
  hub the sentence is about, and does not detect numbers written as words.
- Answers vary slightly between runs; the eval checks structure, numbers and wording, not exact text.
- Sessions live in memory and are lost when the backend restarts.

**Operations (the live demo)** — a demo deployment, not a production service:
- **One shared access code**, no user accounts: anyone with the code has full access, and access cannot
  be revoked for one person without changing the code for everyone.
- **Per-IP and global rate limits** (40 and 60 questions per rolling hour) and a **$1/day cap** on
  Claude API spend. These bound the cost of misuse; they do not identify users.
- **Free-tier cold starts**: after 15 minutes without traffic the service sleeps, and the first page
  load takes about a minute while it wakes up (D18).
- **Sessions are lost on restart or redeploy** (they live in memory), and so are the rate-limit and
  spend counters — a redeploy therefore also resets the day's spend.
- **Fixed 2016–2025 data snapshot**: no nightly ingestion; refreshing the data means re-running
  ingestion, committing the new snapshot and redeploying.
- **Single instance**: limits and sessions are kept in that instance's memory, so the service cannot be
  scaled out as-is (it would need a shared store such as Redis).
- No monitoring or request logging beyond Render's service logs.

## 11. Decisions log

All decisions were made during the build stage by stage (see `docs/sessions/`).

| # | Decision | Why |
|---|---|---|
| D1 | Scores use a **10-year average (2016–2025)**; factual "last year" questions use 2025. | One year is too noisy (a hurricane year or a freeze year swings the score). |
| D2 | NRI **frequency** fields, not `*_RISKS`. | `*_RISKS` includes dollar exposure and social vulnerability and saturates (inland flood 95–100 for all 12 hubs; Dallas winter 98.7 > Minneapolis 97.4). |
| D3 | NRI **tornado, hail, ice storm** stored as context, not scored. | Lets the Dallas answer cite data (hail 96.9th, tornado 96.4th US county percentile). |
| D4 | Project moved out of OneDrive; git repo; one commit per stage. | OneDrive sync locks SQLite and churns on `node_modules`; reviewable history. |
| D5 | Always show the **worst single hazard** with the overall score. | An average hides one extreme hazard (Miami: high hurricane, zero winter). |
| D6 | Model is configurable; default **Claude Sonnet 5.5**. | Demo latency; the model only routes and explains, code computes. Confirmed by D16. |
| D7 | **ACIS station data** for snow, temperature, precipitation; **Open-Meteo for gusts only**, at the same station coordinates; hubs at airport stations. | Modelled data misses extreme days (section 3). One point per hub keeps all metrics comparable. |
| D8 | NRI **annualized frequency (`*_AFREQ`)** for all hazards; low NRI weight for flood. | Easy to explain ("~17 winter events a year"); matches climate expectations for winter and hurricane. |
| D9 | NRI county = county containing the station point. | Same-place rule (D7); neighbouring counties differ little for winter / hurricane / hail / ice. |
| D10 | Missing days excluded from count and denominator; coverage reported; < 90 % → unknown. | A day with no data is not a day with no snow. |
| D11 | **Wind gusts removed from the hurricane score**; kept as context. | Model gusts peak in the Midwest (Columbus 6.2 days/yr vs Miami 1.1). |
| D12 | NRI flood frequency **not divided by county area** (switch `FLOOD_NRI_PER_AREA`, off). Reverses an earlier plan. | Across all US counties flood frequency does not grow with area (−0.01); dividing pushed small urban counties up (Denver to the 88th percentile) and Houston down. |
| D13 | 3-day precipitation = **average of each year's maximum**. | Houston's 10-year maximum (722 mm) is Hurricane Harvey alone. |
| D14 | One anchor rule for NRI: 100 = US county 99th percentile; anchors stay at national extremes. | Consistent, explainable, independent of which hubs were picked; hardcoded so a data refresh cannot move the scale. |
| D15 | **Overall score = worst single hazard**; hubs ranked by it; weighted average kept as "multi-hazard". Replaces the brief's "overall = weighted average". | The client invests per hub and a hub closes when one hazard hits it. The average compressed 10 of 12 hubs to "low" (Memphis: flood 70, average 28). |
| D16 | Keep **Sonnet 5.5 at effort medium**; Opus 5.5 and low effort stay config options. | Eval: Sonnet medium 19/19, median 5.7 s, $0.23 per run; low effort not faster; Opus ~2× slower, ~3× cost, same result on hard cases. |
| D17 | Deploy as **one Render web service** (the backend serves the built UI), not Vercel for the frontend plus a separate backend host. | In-memory sessions and per-instance state rule out serverless functions without adding a shared store; one service means one URL, one deploy, no CORS, and the same code path as local. (Vercel's time limit was checked and was not the blocker: 300 s on Hobby vs 6–20 s answers.) |
| D18 | Stay on Render's **free tier**; no upgrade to the paid Starter instance. The README states the cold start clearly. | Cost vs. a documented delay: the free tier costs nothing, and its only drawback for a demo is a ~1-minute first load after 15 idle minutes, which is announced on the Live demo section; answers after that are unaffected (~5–10 s). The paid instance (~$7/month, third-party figure) would only remove that first-load wait. |
| D19 | Protect the public demo with **one shared access code**, **per-IP (40/h) and global (60/h) rate limits**, **10 wrong codes per IP per hour**, and a **$1/day spend cap**; all enforced by the backend on every request. | No accounts are needed for a handful of reviewers, but an open endpoint would let anyone spend the API credit. The limits bound the worst case to $1/day. 40/h per IP (not 20) because several reviewers may share one office IP. |

## 12. Future work
- **Seasonal / monthly scores** ("which hub is safest in the fall?"): possible without new data,
  because daily rows are stored.
- **Calibrate thresholds and weights** against the client's historical disruption records (which hub
  closed, when, why) — the single biggest improvement to the scores' meaning.
- Real warehouse locations instead of airport stations; nearest long-record station per site.
- **Score-change alerts**: score snapshots are already stored with a settings hash; a nightly job would
  recompute, compare and post to a webhook. Live NWS alerts per hub (API verified, not wired in).
- Measured peak gusts (NOAA ISD hourly data) so wind can be scored.
- Persist sessions and limit counters in a shared store (e.g. Redis) so restarts keep conversations and
  the service can run more than one instance; stream answers in the UI; tie text numbers to the hub
  each sentence is about.
- For real use: per-user accounts (e.g. company single sign-on) instead of one shared code, monitoring,
  and an always-on instance (no cold starts).
- A ranking tool for context hazards (today the agent makes one metric call per hub, in parallel).
