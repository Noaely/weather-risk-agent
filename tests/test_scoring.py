"""Scoring tests: normalization, weights, missing-data rules, metrics on synthetic rows, and
sanity checks of the real scores (skipped if the DB is not built)."""

from datetime import date, timedelta

import pytest

from backend.scoring import config as cfg
from backend.scoring.metrics import DailyRow, avg_annual_max_3day_precip, day_count_metric
from backend.scoring.normalize import normalize, weighted_average
from backend.settings import DB_PATH


def year_rows(year: int, **overrides) -> list[DailyRow]:
    """A dry, mild year: no snow, no rain, 10 C, light wind. overrides: {date_str: dict(col=value)}."""
    d, rows = date(year, 1, 1), []
    while d.year == year:
        vals = dict(snow_cm=0.0, tmin_c=10.0, precip_mm=0.0, gust_kmh=20.0)
        vals.update(overrides.get(d.isoformat(), {}))
        rows.append(DailyRow(d.isoformat(), **vals))
        d += timedelta(days=1)
    return rows


# --------------------------------------------------------------------------- normalize
def test_normalize_clamps_and_is_linear():
    assert normalize(-5, 0, 10) == 0
    assert normalize(0, 0, 10) == 0
    assert normalize(2.5, 0, 10) == 25
    assert normalize(10, 0, 10) == 100
    assert normalize(50, 0, 10) == 100


def test_normalize_unknown_stays_unknown():
    assert normalize(None, 0, 10) is None


def test_normalize_rejects_bad_anchors():
    with pytest.raises(ValueError):
        normalize(1, 10, 10)


def test_weighted_average_unknown_propagates():
    assert weighted_average([(50, 0.5), (100, 0.5)]) == 75
    assert weighted_average([(50, 0.5), (None, 0.5)]) is None


# --------------------------------------------------------------------------- config
def test_metric_weights_sum_to_one_per_hazard():
    for hazard, specs in cfg.HAZARDS.items():
        assert sum(s.weight for s in specs) == pytest.approx(1.0), hazard


def test_hazard_weights_sum_to_one_and_cover_all_hazards():
    assert set(cfg.HAZARD_WEIGHTS) == set(cfg.HAZARDS)
    assert sum(cfg.HAZARD_WEIGHTS.values()) == pytest.approx(1.0)


def test_gusts_are_not_in_the_hurricane_score():
    assert all(s.key != "strong_gust_days" for specs in cfg.HAZARDS.values() for s in specs)


def test_level_bands():
    assert cfg.level_for(None) is None
    assert cfg.level_for(0) == "low"
    assert cfg.level_for(32.9) == "low"
    assert cfg.level_for(33) == "moderate"
    assert cfg.level_for(65.9) == "moderate"
    assert cfg.level_for(66) == "high"
    assert cfg.level_for(100) == "high"


# --------------------------------------------------------------------------- metrics on synthetic rows
def test_day_count_per_year_and_percent():
    rows = year_rows(2025, **{"2025-01-10": {"snow_cm": 3.0}, "2025-01-11": {"snow_cm": 0.3},
                              "2025-02-01": {"snow_cm": 0.2}})  # 0.2 cm is below measurable
    assert day_count_metric(rows, "heavy_snow_days", 2025, 2025).value == 1
    m = day_count_metric(rows, "measurable_snow_days", 2025, 2025, as_percent=True)
    assert m.value == round(2 / 365 * 100, 2)
    assert m.coverage == 1.0


def test_threshold_boundaries_are_inclusive():
    rows = year_rows(2025, **{"2025-01-01": {"tmin_c": -15.0}, "2025-01-02": {"precip_mm": 25.0},
                              "2025-01-03": {"snow_cm": 0.254}})
    assert day_count_metric(rows, "extreme_cold_days", 2025, 2025).value == 1
    assert day_count_metric(rows, "heavy_rain_days", 2025, 2025).value == 1
    assert day_count_metric(rows, "measurable_snow_days", 2025, 2025).value == 1


def test_missing_days_are_excluded_not_counted_as_zero():
    # 10 snow days in 365; then 36 non-snow days go missing -> rate over observed days goes up.
    snow = {f"2025-01-{d:02d}": {"snow_cm": 3.0} for d in range(1, 11)}
    missing = {(date(2025, 6, 1) + timedelta(days=i)).isoformat(): {"snow_cm": None} for i in range(36)}
    m = day_count_metric(year_rows(2025, **snow, **missing), "heavy_snow_days", 2025, 2025)
    assert m.observed_days == 329 and m.expected_days == 365
    assert m.coverage == round(329 / 365, 4)
    assert m.value == round(10 / 329 * 365, 2)  # 11.09, not 10


def test_low_coverage_makes_metric_unknown():
    missing = {(date(2025, 1, 1) + timedelta(days=i)).isoformat(): {"snow_cm": None} for i in range(40)}
    m = day_count_metric(year_rows(2025, **missing), "heavy_snow_days", 2025, 2025)
    assert m.coverage < cfg.MIN_COVERAGE
    assert m.value is None


def test_rows_absent_from_db_count_as_missing():
    rows = year_rows(2025)[:300]  # only 300 of 365 days exist at all
    assert day_count_metric(rows, "heavy_snow_days", 2025, 2025).value is None


def test_avg_annual_max_3day_uses_yearly_maxima():
    y1 = year_rows(2016, **{"2016-08-01": {"precip_mm": 300}, "2016-08-02": {"precip_mm": 300},
                            "2016-08-03": {"precip_mm": 100}})  # one Harvey-like event: 700 mm
    y2 = year_rows(2017, **{"2017-05-01": {"precip_mm": 50}, "2017-05-02": {"precip_mm": 50}})  # 100 mm
    m = avg_annual_max_3day_precip(y1 + y2, 2016, 2017)
    assert m.value == 400.0  # (700 + 100) / 2, not the 700 maximum
    assert "700 mm in 2016" in m.note


def test_3day_window_needs_three_observed_days():
    rows = year_rows(2025, **{"2025-03-01": {"precip_mm": 80}, "2025-03-02": {"precip_mm": None},
                              "2025-03-03": {"precip_mm": 80}})
    # Every window containing the missing day is skipped; the best full window holds one 80 mm day.
    assert avg_annual_max_3day_precip(rows, 2025, 2025).value == 80.0


# --------------------------------------------------------------------------- real scores
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="run backend.ingestion.run first")


@pytest.fixture(scope="module")
def scores():
    from backend.db import connect
    from backend.scoring.score import score_all
    conn = connect()
    yield {s.hub_id: s for s in score_all(conn)}
    conn.close()


@needs_db
def test_all_scores_known_and_in_range(scores):
    for s in scores.values():
        assert s.overall is not None and 0 <= s.overall <= 100
        for h in s.hazards.values():
            assert h.score is not None and 0 <= h.score <= 100 and not h.unknown_metrics


@needs_db
def test_rankings_match_common_sense(scores):
    by = lambda hz: sorted(scores, key=lambda k: -scores[k].hazards[hz].score)  # noqa: E731
    assert by("hurricane")[:2] == ["miami", "houston"]
    midwest_winter = [k for k in by("winter") if scores[k].region == "Midwest"]
    assert midwest_winter[0] == "minneapolis"
    assert scores["phoenix"].overall == min(s.overall for s in scores.values())
    assert scores["columbus"].hazards["hurricane"].score < 10  # gusts no longer push it up


@needs_db
def test_overall_is_the_worst_hazard(scores):
    for s in scores.values():
        assert s.overall == max(h.score for h in s.hazards.values())
        assert s.overall == s.hazards[s.worst_hazard].score
        assert s.overall >= s.multi_hazard  # the average can never exceed the worst
    assert scores["miami"].worst_hazard == "hurricane"


@needs_db
def test_single_hazard_hub_is_not_hidden_by_the_average(scores):
    # Memphis: flood is high; the multi-hazard average alone would have called it low.
    memphis = scores["memphis"]
    assert memphis.worst_hazard == "flood" and memphis.overall_level == "high"
    assert memphis.multi_hazard_level == "low"


def test_unknown_hazard_makes_overall_unknown(monkeypatch):
    from backend.scoring import score as score_mod
    from backend.scoring.metrics import MetricValue
    if not DB_PATH.exists():
        pytest.skip("needs DB")
    from backend.db import connect
    real = score_mod.raw_metric

    def fake(conn, hub_id, key, rows, start, end):
        if key == "nri_hurricane_freq":
            return MetricValue(key=key, value=None, unit="events/yr", period="x", source="test")
        return real(conn, hub_id, key, rows, start, end)

    monkeypatch.setattr(score_mod, "raw_metric", fake)
    s = score_mod.score_hub(connect(), "minneapolis")
    assert s.hazards["hurricane"].score is None and s.hazards["winter"].score is not None
    assert s.overall is None and s.worst_hazard is None and s.multi_hazard is None


@needs_db
def test_score_all_is_ranked_by_overall(scores):
    from backend.db import connect
    from backend.scoring.score import score_all
    ranked = [s.overall for s in score_all(connect())]
    assert ranked == sorted(ranked, reverse=True)


@needs_db
def test_breakdown_explains_the_score(scores):
    flood = scores["dallas"].hazards["flood"]
    assert [m.key for m in flood.metrics] == [s.key for s in cfg.HAZARDS["flood"]]
    for m in flood.metrics:
        assert m.raw is not None and m.normalized is not None and m.unit and m.source
    context = {m.key for m in scores["dallas"].context}
    assert {"nri_tornado_freq", "nri_hail_freq", "nri_ice_storm_freq", "strong_gust_days"} <= context
