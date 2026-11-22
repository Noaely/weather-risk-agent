# Architecture — Weather Risk Intelligence Agent

> Status: Stage 1 (setup & data check). Sections marked _TBD_ are filled in later stages.

## 1. Components and how they talk
_TBD (Stage 4–5)._ Target flow: React chat UI → FastAPI `POST /chat` → agent loop (Anthropic SDK +
tools) → pure Python tool functions → SQLite. A separate ingestion script fills SQLite from public
APIs; the agent never calls external APIs at question time (except the optional live-alert tool).

## 2. Repository structure
_TBD (Stage 7)._ See `CLAUDE.md` for the target layout.

## 3. Data sources (verified in Stage 1, 2026-11-22)

| Source | Access | What we use | Verified |
|---|---|---|---|
| Open-Meteo Historical (archive) API | `archive-api.open-meteo.com/v1/archive`, no key | daily `snowfall_sum` (cm), `precipitation_sum` (mm), `temperature_2m_min` (°C), `wind_gusts_10m_max` (km/h), `timezone=auto` | 365/365 days for 2025, 0 nulls (Denver, Miami) |
| FEMA National Risk Index, **December 2025** release | ArcGIS feature service `services.arcgis.com/XG15cJAlne2vxtgt/.../National_Risk_Index_Counties/FeatureServer/0` (owner `FEMA_NationalRiskIndex`) | county fields per hazard: `*_AFREQ`, `*_ALRB`, `*_ALR_NPCTL`, `*_RISKS` | all 12 hub FIPS match the expected county and state |
| NOAA ACIS (`data.rcc-acis.org/StnData`) — the service behind NWS "NOWData" | POST JSON, no key | official airport climate stations: daily snow (in), min temp (°F), precipitation (in) | 3,653/3,653 days 2016–2025 for all 12 stations; `M` = missing, `T` = trace |
| NWS `api.weather.gov/alerts/active` (bonus) | GET, generic `User-Agent` header | active alerts per hub point | HTTP 200 for Denver and Miami |

Notes
- `hazards.fema.gov` (the NRI download page) resets the TLS connection from our network, while
  `www.fema.gov` works. The same NRI data is published by FEMA on ArcGIS Online, which we use instead:
  `https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0`
  (item page `https://www.arcgis.com/home/item.html?id=39485e8035d446a5bff03259508ae355`, owner
  account `FEMA_NationalRiskIndex` / "FEMA National Risk Index (NRI)"; field `NRI_VER` = "December 2025".
  Older releases are separate services under the same account, e.g. "(March 2023)"). A snapshot of all
  counties is saved to `data/raw/` at ingestion, so the app does not depend on this service at runtime.
- NRI December 2025 renamed riverine flooding to **Inland Flooding (`IFLD_*`)**.
- NRI uses `null` for "hazard not applicable" (e.g. hurricane in Minneapolis/Denver, ice storm in Miami)
  and `0` for "applicable but zero". Both are treated as 0 exposure, not as missing data.
- NCEI daily summaries have no measured peak gust (`WSFG` is empty for all of 2025 at Denver and Miami);
  only 2-minute sustained wind exists. So gusts can only come from Open-Meteo.
- Raw probe responses: `data/raw/probe/`. Probe script: `scripts/probe_sources.py`.

### Modelled vs measured data (2025, same coordinates as the airport station)

| Metric | Denver station | Denver Open-Meteo | Miami station | Miami Open-Meteo |
|---|---|---|---|---|
| Measurable snow days (≥ 0.1 in / 0.25 cm) | 26 | 38 | 0 | 0 |
| Snow days ≥ 1 in / 2.5 cm | 12 | 5 | 0 | 0 |
| Total snowfall (cm) | 75.7 | 51.5 | 0 | 0 |
| Cold days (min ≤ −15 °C) | 13 | 9 | 0 | 0 |
| Heavy rain days (≥ 25 mm) | 4 | 0 | 20 | 5 |
| Total precipitation (mm) | 391 | 475 | 1,471 | 1,457 |

Open-Meteo gets totals roughly right but smooths daily extremes: too many light-snow days, too few
heavy-snow and heavy-rain days. Every threshold metric in our scoring is a count of extreme days.

## 4. Scoring logic
_TBD (Stage 3)._

## 5. Decisions log

| # | Date | Decision | Why |
|---|---|---|---|
| D1 | 2026-11-22 | Scores use a **10-year average (2016–2025)**; factual questions ("last year") use 2025. | One year is too noisy (a hurricane year or a freeze year swings the score). |
| D2 | 2026-11-22 | Use NRI **frequency / loss-rate** fields, not `*_RISKS`. Exact field chosen after Stage 1 values were reviewed. | `*_RISKS` includes dollar exposure and social vulnerability; Stage 1 confirms it saturates (inland flood `RISKS` is 95–100 for all 12 hubs; Dallas winter 98.7 > Minneapolis 97.4). |
| D3 | 2026-11-22 | Store NRI **tornado, hail, ice storm** as context only (not scored). | Lets the agent back the Dallas answer with data. Stage 1: Dallas hail loss-rate percentile 98.3, tornado 97.9. |
| D4 | 2026-11-22 | Project moved out of OneDrive to `C:\DEV\moveo_project`; git repo; `CLAUDE.md` renamed. | OneDrive sync locks SQLite and churns on `node_modules`. |
| D5 | 2026-11-22 | Show the **worst single hazard** next to the overall average. | An average hides one extreme hazard (Miami: high hurricane, zero winter). |
| D6 | 2026-11-22 | Default agent model `claude-sonnet-5-5` at medium effort, configurable via `.env`; compare with Opus in the Stage 6 eval. | Demo latency; the model only routes and explains, code computes. |
| D7 | 2026-11-22 | **ACIS station data** for snowfall, min temperature, precipitation; **Open-Meteo for wind gusts only**, requested at the same station coordinates. Hubs are located at their main-airport NWS station. | Modelled data misses extreme days (table above). Denver 2025 measurable-snow days = 26 on ACIS, independently confirmed by the user on NWS NOWData (23 Jan–Jun + 3 Jul–Dec). One point per hub keeps all metrics comparable. |
| D8 | 2026-11-22 | NRI **`*_AFREQ`** (annualized frequency, events/year) for all hazards. Flood NRI gets a lower weight; the agent mentions the Phoenix/Maricopa caveat. | Easy to explain ("~17 winter events a year in Hennepin County"); matches climate expectations for winter and hurricane. |
| D9 | 2026-11-22 | NRI county = the county containing the station point (point query on the NRI layer). Chicago → DuPage, Kansas City → Platte, Atlanta → Clayton; the other 9 match the city's county. | Same-place rule (D7). Winter/hurricane/hail/ice frequencies barely differ between neighbouring counties (Cook 14.6 vs DuPage 14.3 winter events/yr). |

## 6. Assumptions & limitations
- **Hub list is assumed** (client gave none): 12 hubs in `data/hubs.json`, chosen to cover every city
  in the example questions, ≥ 5 Midwest hubs, real logistics centres, and a spread of hazard profiles.
- **Hub location = the main-airport NWS climate station** (coordinates from ACIS metadata, see
  `data/hubs.json`). Real warehouse addresses would replace this; the nearest long-record station
  would then be chosen per warehouse.
- **"Last year" = calendar year** (Jan–Dec). NWS NOWData shows snow seasons (Jul–Jun) by default, so
  season totals there will not match our calendar-year figures; monthly sums do.
- ACIS codes: `T` (trace) counts as 0 for every threshold; `M` (missing) is stored as NULL and
  excluded from both the count and the number of observed days.
- Open-Meteo returns the nearest model grid cell, not the exact point; we store the returned coordinates.
- **NRI `AFREQ` for localized hazards scales with county size.** Inland flood and tornado
  frequencies count events anywhere in the county: Cook (1,651 sq mi) has 5.9 flood events/yr vs
  DuPage (340 sq mi) 2.8, while winter is the same (14.6 vs 14.3). Maricopa (~9,200 sq mi) therefore
  tops the flood list. Addressed in Stage 3.
- Open-Meteo free tier is for non-commercial use; a production deployment needs the commercial plan.
- Open-Meteo is reanalysis/model data, not measurements (see section 3); wind gusts are model gusts and
  understate thunderstorm and hurricane gusts.
- NRI is a county-level, long-term estimate; large counties (Maricopa, Harris, Cook) aggregate a lot
  of area.
- Extreme cold ≤ −15 °C is an explicit assumption (no national standard).

## 7. Future work
- NOAA GHCN-Daily / ISD for measured peak gusts (hourly ISD has gusts; daily summaries do not).
- Calibrate thresholds and weights against the client's historical disruption records.
