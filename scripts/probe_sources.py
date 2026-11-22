"""Stage 1 data check: call each data source, save raw samples, print what we got.

Run:  .venv/Scripts/python scripts/probe_sources.py

What it checks
1. Open-Meteo Historical (archive) API: variable names, units, nulls, 2025 metrics for Denver and Miami.
2. Ground truth: NOAA ACIS station data (the service behind NWS "NOWData") for the official airport
   stations, compared with Open-Meteo at the same coordinates. This tells us how far the modelled
   data is from real measurements.
3. FEMA National Risk Index (ArcGIS feature service): every hub FIPS matches a county, and the
   candidate fields (RISKS / AFREQ / ALRB / ALR_NPCTL) for our hazards.
4. NWS api.weather.gov: active alerts for Denver and Miami.

This is a throwaway check script, not part of the app. Raw responses go to data/raw/probe/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR = ROOT / "data" / "raw" / "probe"
PROBE_DIR.mkdir(parents=True, exist_ok=True)
HUBS = json.loads((ROOT / "data" / "hubs.json").read_text(encoding="utf-8"))["hubs"]
HUB = {h["id"]: h for h in HUBS}

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
ACIS_URL = "https://data.rcc-acis.org/StnData"
NRI_URL = (
    "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
    "National_Risk_Index_Counties/FeatureServer/0/query"
)
NWS_URL = "https://api.weather.gov/alerts/active"
NWS_UA = os.getenv("NWS_USER_AGENT", "hub-weather-risk-agent/0.1")

DAILY_VARS = ["snowfall_sum", "precipitation_sum", "temperature_2m_min", "wind_gusts_10m_max"]
YEAR = 2025

# Official NWS climate stations (airports) used for the ground-truth comparison.
# Coordinates are taken from the ACIS metadata response, so Open-Meteo is sampled at the station.
STATIONS = {"denver": "KDEN", "miami": "KMIA"}

client = httpx.Client(timeout=60)


def save(name: str, data: object) -> None:
    (PROBE_DIR / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def header(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


# ---------------------------------------------------------------- Open-Meteo
def open_meteo(lat: float, lon: float, start: str, end: str) -> dict:
    r = client.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": start,
            "end_date": end,
            "daily": ",".join(DAILY_VARS),
            "timezone": "auto",  # local calendar days, so "a day" matches the station's day
        },
    )
    r.raise_for_status()
    return r.json()


def om_metrics(d: dict) -> dict:
    daily = d["daily"]
    n = len(daily["time"])
    snow = daily["snowfall_sum"]
    pcp = daily["precipitation_sum"]
    tmin = daily["temperature_2m_min"]
    gust = daily["wind_gusts_10m_max"]
    nulls = {v: sum(x is None for x in daily[v]) for v in DAILY_VARS}
    z = lambda xs: [x for x in xs if x is not None]  # noqa: E731
    return {
        "days": n,
        "nulls": nulls,
        "snow_days_ge_0.25cm (NWS measurable)": sum(x >= 0.25 for x in z(snow)),
        "snow_days_ge_2.5cm (scoring)": sum(x >= 2.5 for x in z(snow)),
        "total_snow_cm": round(sum(z(snow)), 1),
        "cold_days_le_-15C": sum(x <= -15 for x in z(tmin)),
        "heavy_rain_days_ge_25mm": sum(x >= 25 for x in z(pcp)),
        "total_precip_mm": round(sum(z(pcp)), 1),
        "gust_days_ge_75kmh": sum(x >= 75 for x in z(gust)),
        "max_gust_kmh": max(z(gust)),
    }


def probe_open_meteo() -> None:
    header(f"1. Open-Meteo archive API - {YEAR}, hub coordinates from data/hubs.json")
    for hid in ["denver", "miami"]:
        h = HUB[hid]
        d = open_meteo(h["lat"], h["lon"], f"{YEAR}-01-01", f"{YEAR}-12-31")
        save(f"open_meteo_{hid}_{YEAR}.json", d)
        print(f"\n{h['name']}: requested ({h['lat']}, {h['lon']}) -> grid cell "
              f"({d['latitude']:.3f}, {d['longitude']:.3f}), elevation {d['elevation']} m, tz {d['timezone']}")
        print("units:", d["daily_units"])
        print("first 3 days:", {k: v[:3] for k, v in d["daily"].items()})
        for k, v in om_metrics(d).items():
            print(f"  {k:40s} {v}")


# ---------------------------------------------------------------- ACIS ground truth
def acis_val(s: str) -> float | None:
    """ACIS codes: 'T' = trace (counts as 0 for thresholds), 'M' = missing."""
    if s == "M":
        return None
    if s == "T":
        return 0.0
    return float(s.rstrip("A"))  # 'A' suffix = accumulated value; keep the number


def probe_ground_truth() -> None:
    header(f"2. Ground truth: NOAA ACIS station data vs Open-Meteo at the station, {YEAR}")
    for hid, sid in STATIONS.items():
        r = client.post(ACIS_URL, json={
            "sid": sid, "sdate": f"{YEAR}-01-01", "edate": f"{YEAR}-12-31",
            "elems": "snow,mint,pcpn", "meta": "name,ll",
        })
        r.raise_for_status()
        a = r.json()
        save(f"acis_{sid}_{YEAR}.json", a)
        lon, lat = a["meta"]["ll"]
        rows = a["data"]
        snow_in = [acis_val(x[1]) for x in rows]
        tmin_f = [acis_val(x[2]) for x in rows]
        pcp_in = [acis_val(x[3]) for x in rows]
        z = lambda xs: [x for x in xs if x is not None]  # noqa: E731
        station = {
            "days": len(rows),
            "missing (snow/tmin/pcpn)": (snow_in.count(None), tmin_f.count(None), pcp_in.count(None)),
            "snow_days_ge_0.1in": sum(x >= 0.1 for x in z(snow_in)),
            "snow_days_ge_1in": sum(x >= 1.0 for x in z(snow_in)),
            "total_snow_cm": round(sum(z(snow_in)) * 2.54, 1),
            "cold_days_le_5F(-15C)": sum(x <= 5 for x in z(tmin_f)),
            "heavy_rain_days_ge_0.98in(25mm)": sum(x >= 0.984 for x in z(pcp_in)),
            "total_precip_mm": round(sum(z(pcp_in)) * 25.4, 1),
        }
        om = om_metrics(open_meteo(lat, lon, f"{YEAR}-01-01", f"{YEAR}-12-31"))
        print(f"\n{a['meta']['name']} ({sid}) at ({lat:.3f}, {lon:.3f})")
        pairs = [
            ("measurable snow days (>=0.1 in / 0.25 cm)", "snow_days_ge_0.1in", "snow_days_ge_0.25cm (NWS measurable)"),
            ("snow days >= 1 in / 2.5 cm", "snow_days_ge_1in", "snow_days_ge_2.5cm (scoring)"),
            ("total snowfall (cm)", "total_snow_cm", "total_snow_cm"),
            ("cold days (min <= -15 C)", "cold_days_le_5F(-15C)", "cold_days_le_-15C"),
            ("heavy rain days (>= 25 mm)", "heavy_rain_days_ge_0.98in(25mm)", "heavy_rain_days_ge_25mm"),
            ("total precipitation (mm)", "total_precip_mm", "total_precip_mm"),
        ]
        print(f"  {'metric':45s} {'station':>10s} {'open-meteo':>11s}")
        for label, sk, ok in pairs:
            print(f"  {label:45s} {station[sk]!s:>10s} {om[ok]!s:>11s}")
        print(f"  station days={station['days']}, missing (snow/tmin/pcpn)={station['missing (snow/tmin/pcpn)']}")


# ---------------------------------------------------------------- FEMA NRI
NRI_HAZARDS = {
    "WNTW": "Winter weather", "HRCN": "Hurricane", "IFLD": "Inland flooding",
    "CFLD": "Coastal flooding", "ISTM": "Ice storm", "TRND": "Tornado", "HAIL": "Hail",
}
NRI_SUFFIXES = ["RISKS", "AFREQ", "ALRB", "ALR_NPCTL"]


def probe_nri() -> None:
    header("3. FEMA National Risk Index (ArcGIS feature service), 12 hub counties")
    fips = [h["county_fips"] for h in HUBS]
    fields = ["STCOFIPS", "STATEABBRV", "COUNTY", "POPULATION", "NRI_VER", "RISK_SCORE"]
    fields += [f"{p}_{s}" for p in NRI_HAZARDS for s in NRI_SUFFIXES]
    r = client.get(NRI_URL, params={
        "where": "STCOFIPS IN (" + ",".join(f"'{f}'" for f in fips) + ")",
        "outFields": ",".join(fields),
        "returnGeometry": "false",
        "f": "json",
    })
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(d["error"])
    save("nri_hub_counties.json", d)
    rows = {f["attributes"]["STCOFIPS"]: f["attributes"] for f in d["features"]}

    print("\nFIPS check (hubs.json county_name vs FEMA COUNTY):")
    for h in HUBS:
        a = rows.get(h["county_fips"])
        if a is None:
            print(f"  MISSING  {h['name']:12s} {h['county_fips']}")
            continue
        ok = a["COUNTY"] == h["county_name"] and a["STATEABBRV"] == h["state"]
        print(f"  {'OK  ' if ok else 'DIFF'}     {h['name']:12s} {h['county_fips']} -> "
              f"{a['COUNTY']}, {a['STATEABBRV']}  pop {a['POPULATION']:,}  NRI_VER={a['NRI_VER']}")

    def fmt(v: object) -> str:
        if v is None:
            return "null"
        if isinstance(v, float):
            return f"{v:.3g}" if abs(v) < 1 else f"{v:.1f}"
        return str(v)

    for p, label in NRI_HAZARDS.items():
        print(f"\n{label} ({p}):")
        print(f"  {'hub':12s}" + "".join(f"{s:>12s}" for s in NRI_SUFFIXES))
        for h in HUBS:
            a = rows.get(h["county_fips"], {})
            print(f"  {h['name']:12s}" + "".join(f"{fmt(a.get(f'{p}_{s}')):>12s}" for s in NRI_SUFFIXES))


# ---------------------------------------------------------------- NWS alerts
def probe_nws() -> None:
    header("4. NWS api.weather.gov - active alerts")
    for hid in ["denver", "miami"]:
        h = HUB[hid]
        r = client.get(NWS_URL, params={"point": f"{h['lat']},{h['lon']}"},
                       headers={"User-Agent": NWS_UA, "Accept": "application/geo+json"})
        r.raise_for_status()
        d = r.json()
        save(f"nws_alerts_{hid}.json", d)
        feats = d.get("features", [])
        print(f"\n{h['name']}: HTTP {r.status_code}, {len(feats)} active alert(s)")
        for f in feats[:3]:
            p = f["properties"]
            print(f"  - {p['event']} | {p['severity']} | {p['headline']}")


if __name__ == "__main__":
    probe_open_meteo()
    probe_ground_truth()
    probe_nri()
    probe_nws()
    print(f"\nRaw responses saved in {PROBE_DIR.relative_to(ROOT)}")
