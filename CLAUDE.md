# CLAUDE.md — Weather Risk Intelligence Agent

## Context
Home assignment for a Forward Deployed Engineer role (Moveo / Wonderful).
Fictional client: a US logistics company with regional distribution hubs. Analysts need to decide
which hubs are most exposed to weather disruption and should get resilience investment.

We build an AI agent that answers questions such as:
- Which hubs in the Midwest are most exposed to winter disruption?
- Compare Miami and Houston in terms of hurricane and flood exposure.
- What percentage of days in Denver last year had snowfall?
- Why is the Dallas hub's weather disruption risk high?

Time budget: ~1 day. **A narrow scope done well beats a broad scope done shallowly.**
The full session with you (Claude Code) is a deliverable, so keep the work in this session and
explain decisions as you go.

## Core principle — read this first
**Code computes, the LLM explains.**
- All numbers, scores and rankings come from deterministic Python code.
- The LLM never invents or calculates a number. It only calls tools and explains their results.
- If a tool does not return a value, the agent says it does not know. No guessing.
- The agent does not accept a false premise. Example: if asked "Why is Dallas's risk high?" and the
  computed score is moderate, it says so, shows the breakdown, and notes hazards outside our scope
  (e.g. tornado, hail, ice storms) that may explain the client's concern.

## Scope (v1)
- **Hubs:** 12 predefined hubs in `data/hubs.json` (name, city, state, region, lat, lon, county FIPS).
  Midwest: Chicago IL, Minneapolis MN, Detroit MI, Kansas City MO, Columbus OH.
  South: Dallas TX, Houston TX, Miami FL, Atlanta GA, Memphis TN.
  West: Denver CO, Phoenix AZ.
  The hub list is an assumption (the client did not provide one) and must be documented.
  Selection criteria: (1) covers every city in the client's example questions; (2) at least 5 Midwest
  hubs so a regional ranking is meaningful; (3) real US logistics centers; (4) a spread of hazard
  profiles, including a low-risk contrast hub (Phoenix) to sanity-check the ranking.
  The design must make it trivial to replace `hubs.json` with the client's real hub list.
  **Hub location = the official NWS climate station at the city's main airport** (e.g. Denver → KDEN).
  All weather data for a hub comes from that one point; the NRI county is the county containing
  the station point (Chicago's O'Hare station is in DuPage County, Atlanta's in Clayton, Kansas City's in Platte).
- **Hazards (scored):** winter, hurricane/wind, flood/heavy rain.
  **Context only (not scored):** NRI tornado, hail, ice storm — so the agent can back up answers
  like "why is Dallas risky?" with data instead of guessing.
- **Period:** "last year" = the previous full **calendar** year (Jan–Dec, not the Jul–Jun snow season
  that NWS NOWData shows by default). Factual questions use that year.
  **Scores use a 10-year average (2016–2025)**, because one year is too noisy for risk scoring.

## Data sources (verified in Stage 1 — see docs/ARCHITECTURE.md section 3)
- **NOAA ACIS station data** (`https://data.rcc-acis.org/StnData`, no key) — daily snowfall, min
  temperature and precipitation measured at the 12 airport stations. This is the service behind
  NWS NOWData. Chosen over Open-Meteo because modelled data misses extreme days (Denver 2025
  measurable-snow days: station 26, confirmed on NOWData; Open-Meteo 38).
- **Open-Meteo Historical Weather API** — **daily max wind gusts only**, requested at the station
  coordinates. No API key. No free source of measured daily peak gusts exists (NCEI `WSFG` is empty).
- **FEMA National Risk Index, December 2025 (v1.20.0)** — long-term county-level layer for hurricane, inland/coastal
  flood, winter weather (+ tornado, hail, ice storm as context). We use the **annualized frequency
  (`*_AFREQ`, events per year)** fields, not `*_RISKS` (which includes dollar exposure and social
  vulnerability and saturates near 100 for big counties). `hazards.fema.gov` blocks our network
  (TLS reset), so the data comes from FEMA's official ArcGIS Online account (`FEMA_NationalRiskIndex`):
  `https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0`.
  A snapshot is saved in `data/raw/`.
- **(Bonus) National Weather Service API** (api.weather.gov) — active alerts per hub location.

Fetched data is cached in the database. The agent never calls external APIs at question time
except for the optional live-alert tool.

## Scoring logic (deterministic)
Per hub, compute metrics, normalize each to 0–100 with **fixed thresholds** (not min-max across hubs,
so scores stay stable over time and alerts are meaningful), then weight them.

| Hazard | Metrics (from data, 10-year average) |
|---|---|
| Winter | days/yr with snowfall ≥ 2.5 cm (1 in); days/yr with min temp ≤ −15 °C; NRI winter weather `WNTW_AFREQ` |
| Hurricane / wind | days/yr with max gusts ≥ 75 km/h (~46 mph, Open-Meteo, low weight); NRI hurricane `HRCN_AFREQ` (main weight) |
| Flood | days/yr with precipitation ≥ 25 mm (~1 in); max 3-day precipitation; NRI inland + coastal flood `AFREQ` (lower weight — county-size bias, e.g. Maricopa/Phoenix; the agent mentions this caveat) |

Next to the overall score, always show the **worst single hazard** — an average hides one extreme
hazard (Miami: very high hurricane, zero winter).

Threshold rationale (document in ARCHITECTURE.md):
- **Measurable snowfall** = 0.1 in (0.25 cm), the NWS definition. Used for factual questions like
  "% of days in Denver with snowfall". The scoring metric uses ≥ 1 in, on the assumption that
  disruption starts with real accumulation. The agent must state which threshold it used.
- **Wind ≥ ~46 mph gusts** = lower bound of the NWS Wind Advisory gust range (46–57 mph).
- **Heavy rain ≥ 25 mm/day** ≈ 1 inch, close to the common "very heavy precipitation day" index (≥ 20 mm).
- **Extreme cold ≤ −15 °C** has no single national standard (NWS cold criteria are local). This is an
  explicit assumption.
- In a real deployment, thresholds and weights would be calibrated with the client's historical
  disruption data (which hub closed, when, and why).

- Hazard score = weighted average of its metrics (0–100).
- Overall risk score = weighted average of hazard scores.
- All thresholds and weights live in one config file (`backend/scoring/config.py`) and are documented
  as assumptions the client can tune.
- `explain_score(hub)` must return the breakdown (each metric, its raw value, its normalized value,
  its weight), so the agent can answer "why".

## Architecture
```
React chat UI  →  FastAPI (POST /chat)  →  Agent loop (Claude API + tools)
                                              ↓
                                     Tool functions (Python)
                                              ↓
                                   SQLite (cached data + scores)
                                              ↑
                       Ingestion script (Open-Meteo, FEMA NRI) — run once / nightly
```
- **Agent loop:** plain Anthropic Python SDK with tool use. No agent framework.
  Loop until Claude stops calling tools.
- **Tools:** `list_hubs(region?)`, `rank_hubs(hazard, region?)`, `compare_hubs(hubs[], hazards[])`,
  `get_hub_metric(hub, metric, year?)`, `explain_score(hub, hazard?)`.
- **Structured output:** the agent must finish by calling a `submit_answer` tool whose input schema is
  the response contract:
  `{ answer, hubs_referenced[], scores[], reasoning, assumptions[], confidence: "high"|"medium"|"low", data_sources[] }`.
  Validate with Pydantic in the backend. If validation fails, retry once, then return a clear error.
- **Conversation memory:** message history stored per `session_id` (in memory or SQLite).
- **Storage:** SQLite — single file, no server, enough for 12 hubs; easy to swap for Postgres/Supabase.

## Repository structure (target)
```
backend/
  app.py              # FastAPI app, /chat and /health
  agent/              # agent loop, system prompt, tool definitions, response schema
  tools/              # tool implementations (pure functions over the DB)
  scoring/            # metrics, normalization, weights config
  ingestion/          # fetch + cache data from public APIs
  db.py
frontend/             # React (Vite) chat UI
data/hubs.json
eval/cases.yaml       # evaluation set
eval/run_eval.py
alerts/               # (bonus) score-change check
docs/ARCHITECTURE.md
README.md
```

## Evaluation
- 10–15 cases in `eval/cases.yaml`. Each case: question, expected tool(s), and a checkable expectation
  (top-ranked hub, numeric value within tolerance, required hub names, valid schema, "says I don't know"
  for out-of-scope questions).
- Numeric expectations are computed from the DB by code, not hard-coded guesses.
- `python eval/run_eval.py` prints pass/fail per case and a summary.
- Include at least: each of the 4 example questions, a follow-up question, an out-of-scope hub,
  and a question with no data.

## Bonus: alerts
Nightly job (GitHub Actions cron, or a local script) recomputes scores, compares with the stored
previous scores, and posts to a webhook when a hub's score changes by more than a threshold.

## How we work — rules for Claude Code
1. **Work in stages.** Never start the next stage before I approve the current one.
2. **At the end of every stage:** stop, list every file you created or changed and what it does,
   run the relevant tests or a manual check, and show me the output.
3. **Verify data before building on it.** In Stage 1, call each API and show me a real sample response.
4. **Keep it simple and explainable.** I must be able to explain every part of the code in the interview.
   Prefer clear code over clever code. Explain anything non-obvious.
5. **Python** for the backend (preferred by the client). Use type hints and Pydantic models.
6. **No secrets in the repo.** API keys come from `.env`; commit `.env.example` only.
7. **Document assumptions as we make them** in `docs/ARCHITECTURE.md` under "Assumptions & limitations".
8. **Don't expand scope** without asking me. If something is out of scope, write it under "Future work".
9. If you are unsure about an API, a field name or a library version, check it instead of guessing.

## Stages
1. **Setup & data check** — repo skeleton, `hubs.json`, call each data source for one hub, show samples.
2. **Ingestion & storage** — fetch data for all hubs, store in SQLite, sanity-check the numbers.
3. **Scoring** — metrics, normalization, weights; unit tests; print a score table for all hubs.
4. **Agent** — tools, system prompt, `submit_answer` schema, agent loop, `POST /chat`; test the 4 example questions.
5. **Chat UI** — minimal React chat that calls the API and shows the answer, scores and assumptions.
6. **Evaluation** — cases + runner; fix what fails.
7. **Docs & deploy** — `README.md` (how to run), `docs/ARCHITECTURE.md`, optional deployment.
8. **Bonus** — score-change alert.

## Definition of done
- The agent runs locally with one documented command per part (backend, frontend, eval).
- The 4 example questions get correct, explained, schema-valid answers.
- The eval runs and passes.
- `ARCHITECTURE.md` covers: components and how they talk, repository structure, storage choice,
  scoring logic, assumptions, uncertainty and limitations.