from datetime import datetime

import polars as pl

from sg_mcproperty.loaders.private_residential import (
    COLUMNS,
    extract_lease_year,
    flatten,
    floor_midpoint,
    format_tenure,
    transform,
)

PROJECTS = [
    {
        "project": "THE TEST",
        "street": "TEST ROAD",
        "transaction": [
            {
                "area": "100",
                "floorRange": "06-10",
                "contractDate": "0124",
                "typeOfSale": "1",
                "price": "1500000",
                "district": "09",
                "tenure": "99 yrs lease commencing from 2010",
            },
            {
                "area": "200",
                "floorRange": "-",
                "contractDate": "1223",
                "typeOfSale": "3",
                "price": "3000000",
                "district": "15",
                "tenure": "Freehold",
            },
        ],
    },
    {"project": "EMPTY", "street": "X", "transaction": []},
]


def test_flatten_one_row_per_transaction():
    df = flatten(PROJECTS)
    assert df.height == 2
    assert df["project"].to_list() == ["THE TEST", "THE TEST"]


def test_flatten_empty():
    assert flatten([]).height == 0


def test_transform():
    out = transform(flatten(PROJECTS))
    assert out.columns == COLUMNS
    a, b = out.row(0, named=True), out.row(1, named=True)
    assert a["area_sqft"] == pl.Series([100 * 10.7639])[0]
    assert a["floor"] == 8
    assert a["contract_date"] == datetime(2024, 1, 1)
    assert a["price"] == 1500000.0
    assert a["district"] == 9
    assert a["type_of_sale"] == "New Sale"
    assert b["type_of_sale"] == "Resale"
    assert a["tenure"] == "99 years"
    assert a["lease_year"] == 2010
    assert b["tenure"] == "Freehold"
    assert b["lease_year"] is None
    assert b["floor"] is None
    assert b["contract_date"] == datetime(2023, 12, 1)


def test_transform_empty():
    assert transform(flatten([])).columns == COLUMNS


def test_floor_midpoint():
    assert floor_midpoint("06-10") == 8
    assert floor_midpoint("01-05") == 3
    assert floor_midpoint(" 11 - 15 ") == 13
    assert floor_midpoint("-") is None
    assert floor_midpoint("B1-B5") is None
    assert floor_midpoint(None) is None


def test_extract_lease_year():
    assert extract_lease_year("99 yrs lease commencing from 2024") == 2024
    assert extract_lease_year("999 yrs lease commencing from 1885") == 1885
    assert extract_lease_year("Freehold") is None
    assert extract_lease_year(None) is None


def test_format_tenure():
    assert format_tenure("99 yrs lease commencing from 2024") == "99 years"
    assert format_tenure("999 yrs lease commencing from 1885") == "999 years"
    assert format_tenure(" Freehold ") == "Freehold"
    assert format_tenure(None) is None
