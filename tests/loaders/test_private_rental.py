from datetime import date, datetime

import polars as pl
import pytest

from sg_mcproperty.loaders.client import URAClient, URAError
from sg_mcproperty.loaders.private_rental import (
    COLUMNS,
    flatten,
    parse_area_band,
    parse_bedrooms,
    recent_quarters,
    transform,
)

RESULTS = {
    "25q3": [
        {
            "project": "THE TEST",
            "street": "TEST ROAD",
            "x": "27107.4972",
            "y": "32559.5",
            "rental": [
                {
                    "areaSqm": "150-160",
                    "areaSqft": "1700-1800",
                    "leaseDate": "0725",
                    "propertyType": "Non-landed Properties",
                    "district": "10",
                    "noOfBedRoom": "4",
                    "rent": 14484,
                },
                {  # URA says NA
                    "areaSqm": "70-80",
                    "areaSqft": "800-900",
                    "leaseDate": "0825",
                    "propertyType": "Non-landed Properties",
                    "district": "10",
                    "noOfBedRoom": "NA",
                    "rent": 5200,
                },
            ],
        },
        {  # no coordinates, and the bedroom key is missing altogether
            "project": "NO COORDS",
            "street": "Y ROAD",
            "rental": [
                {
                    "areaSqm": ">300",
                    "areaSqft": ">3000",
                    "leaseDate": "0925",
                    "propertyType": "Detached House",
                    "district": "01",
                    "rent": 20000,
                }
            ],
        },
        {"project": "EMPTY", "street": "X", "x": "1", "y": "2", "rental": []},
    ],
    "25q4": [
        {
            "project": "LOW BAND",
            "street": "Z",
            "x": "",
            "y": "",
            "rental": [
                {
                    "areaSqft": "<=500",
                    "leaseDate": "1025",
                    "propertyType": "Terrace House",
                    "district": "15",
                    "noOfBedRoom": "1",
                    "rent": 2500,
                }
            ],
        }
    ],
}


def test_flatten_one_row_per_contract_across_quarters():
    df = flatten(RESULTS)
    assert df.height == 4
    assert df["project"].to_list() == ["THE TEST", "THE TEST", "NO COORDS", "LOW BAND"]
    assert df["noOfBedRoom"].to_list() == ["4", "NA", None, "1"]  # missing key -> null
    assert df["rent"].to_list() == ["14484", "5200", "20000", "2500"]  # all strings until transform


def test_flatten_empty_results():
    assert flatten({}).height == 0
    assert flatten({"25q3": []}).height == 0


def test_transform_types_and_values():
    out = transform(flatten(RESULTS))
    assert out.columns == COLUMNS
    first, second, no_coords, low = (out.row(i, named=True) for i in range(4))
    assert first["district"] == 10 and first["property_type"] == "Non-landed Properties"
    assert first["lease_date"] == datetime(2025, 7, 1)
    assert (first["area_sqft_min"], first["area_sqft_max"]) == (1700, 1800)
    assert first["bedrooms"] == 4 and first["rent"] == 14484.0
    assert (first["x"], first["y"]) == (27107.4972, 32559.5)
    assert second["bedrooms"] is None
    assert no_coords["x"] is None and no_coords["y"] is None and no_coords["bedrooms"] is None
    assert (no_coords["area_sqft_min"], no_coords["area_sqft_max"]) == (3000, None)  # open top band
    assert (low["area_sqft_min"], low["area_sqft_max"]) == (None, 500)  # open bottom band
    assert low["x"] is None  # empty string coordinate
    assert out["rent"].dtype == pl.Float64


def test_transform_empty():
    assert transform(flatten({})).columns == COLUMNS


@pytest.mark.parametrize(
    ("band", "expected"),
    [
        ("1700-1800", (1700, 1800)),
        (" 90 - 100 ", (90, 100)),
        (">3000", (3000, None)),
        ("> 3000", (3000, None)),
        ("<=500", (None, 500)),
        ("<500", (None, 500)),
        ("NA", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_area_band(band, expected):
    assert parse_area_band(band) == expected


def test_parse_bedrooms():
    assert [parse_bedrooms(v) for v in ("3", " 2 ", "NA", "", None)] == [3, 2, None, None, None]


def test_recent_quarters_counts_back_across_years():
    assert recent_quarters(3, date(2026, 11, 18)) == ["26q4", "26q3", "26q2"]
    assert recent_quarters(3, date(2026, 1, 1)) == ["26q1", "25q4", "25q3"]
    assert recent_quarters(2, date(2000, 5, 1)) == ["00q2", "00q1"]
    assert recent_quarters(0, date(2026, 5, 1)) == []


# --- parallel fetching in the client -----------------------------------------------------------


def _client(fetch):
    client = URAClient(access_key="test-key")
    client._token = "test-token"  # skip authentication
    client.rental_contracts = fetch
    return client


def test_rental_contracts_many_returns_each_quarter_in_order():
    client = _client(lambda quarter: [{"project": f"P-{quarter}"}])
    out = client.rental_contracts_many(["26q1", "25q4", "25q3"])
    assert list(out) == ["26q1", "25q4", "25q3"]
    assert out["25q4"] == [{"project": "P-25q4"}]


def test_rental_contracts_many_propagates_a_failed_quarter():
    def fetch(quarter):
        if quarter == "25q4":
            raise URAError("boom")
        return []

    with pytest.raises(URAError, match="boom"):
        _client(fetch).rental_contracts_many(["26q1", "25q4"])


def test_rental_contracts_many_with_no_quarters():
    assert _client(lambda quarter: []).rental_contracts_many([]) == {}
