"""Unit tests for ACIS value parsing and unit conversion (no network, no DB)."""

import pytest

from backend.ingestion.acis import f_to_c, inches_to_cm, inches_to_mm, parse_acis_value


def test_trace_is_zero_and_missing_is_none():
    assert parse_acis_value("T") == 0.0
    assert parse_acis_value("M") is None


def test_numbers():
    assert parse_acis_value("0.0") == 0.0
    assert parse_acis_value("1.25") == 1.25
    assert parse_acis_value("-7") == -7.0


def test_unknown_code_raises():
    # e.g. an accumulated value '1.20A' or a 'S' flag must not be silently misread
    for raw in ["1.20A", "S", "", "abc"]:
        with pytest.raises(ValueError):
            parse_acis_value(raw)


def test_conversions_match_threshold_definitions():
    assert inches_to_cm(0.1) == 0.254      # NWS measurable snowfall
    assert inches_to_cm(1.0) == 2.54       # scoring snow threshold (>= 2.5 cm)
    assert f_to_c(5) == -15.0              # cold threshold -15 C == 5 F
    assert inches_to_mm(0.99) >= 25 > inches_to_mm(0.98)  # heavy rain 25 mm sits between 0.98 and 0.99 in
    assert inches_to_cm(None) is None and f_to_c(None) is None
