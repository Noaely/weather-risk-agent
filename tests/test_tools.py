"""Tool function tests against the real DB (skipped if not built)."""

import pytest

from backend.settings import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run backend.ingestion.run first")


@pytest.fixture(scope="module")
def conn():
    from backend.db import connect
    c = connect()
    yield c
    c.close()


def test_rank_hubs_returns_gap_to_previous_hub(conn):
    from backend.tools.scores import rank_hubs
    ranking = rank_hubs(conn, "winter", "Midwest")["ranking"]
    assert ranking[0]["points_behind_previous"] is None             # the leader has nobody above it
    for above, row in zip(ranking, ranking[1:]):
        assert row["points_behind_previous"] == round(above["score"] - row["score"], 1)
        assert row["points_behind_previous"] >= 0
    by_name = {r["name"]: r for r in ranking}
    # the case the text check caught in Stage 5: Chicago 34.2 -> Detroit 31.5
    assert by_name["Detroit"]["points_behind_previous"] == 2.7


def test_tied_hubs_have_zero_gap_and_share_a_rank(conn):
    from backend.tools.scores import rank_hubs
    r = rank_hubs(conn, "overall")
    tied = [x for x in r["ranking"] if x["rank"] in r["tied_ranks"]]
    assert len(tied) >= 2
    assert all(x["points_behind_previous"] == 0 for x in tied[1:])


def test_hub_aliases_resolve(conn):
    from backend.tools.hubs import resolve_hub
    assert resolve_hub(conn, "Dallas-Fort Worth") == "dallas"
    assert resolve_hub(conn, "Kansas City, MO") == "kansas_city"
    assert resolve_hub(conn, "KC") == "kansas_city"
    assert resolve_hub(conn, "Seattle") is None


def test_year_outside_data_is_no_data_not_an_error(conn):
    from backend.tools.metric import get_hub_metric
    r = get_hub_metric(conn, "Houston", "heavy_rain_days", 2010)
    assert r["status"] == "no_data" and r["available_years"][0] == 2016


def test_denver_2025_measurable_snow(conn):
    from backend.tools.metric import get_hub_metric
    r = get_hub_metric(conn, "Denver", "measurable_snow_days", 2025)
    assert (r["qualifying_days"], r["observed_days"], r["percent_of_observed_days"]) == (26, 364, 7.14)
