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
        "x": "27107.4972",
        "y": "32559.5",
        "transaction": [
            {
                "area": "100",
                "floorRange": "06-10",
                "contractDate": "0124",
                "typeOfSale": "1",
                "price": "1500000",
                "district": "09",
                "tenure": "99 yrs lease commencing from 2010",
                "propertyType": "Condominium",
            },
            {
                "area": "200",
                "floorRange": "-",
                "contractDate": "1223",
                "typeOfSale": "3",
                "price": "3000000",
                "district": "15",
                "tenure": "Freehold",
                "propertyType": "Terrace House",
            },
        ],
    },
    {
        "project": "NO COORDS",
        "street": "Y",
        "x": "",
        "y": None,
        "transaction": [
            {
                "area": "50",
                "floorRange": "01-05",
                "contractDate": "0124",
                "typeOfSale": "2",
                "price": "1",
                "district": "01",
                "tenure": "Freehold",
                "propertyType": "Apartment",
            }
        ],
    },
    {"project": "EMPTY", "street": "X", "transaction": []},
]


def test_flatten_one_row_per_transaction():
    df = flatten(PROJECTS)
    assert df.height == 3
    assert df["project"].to_list() == ["THE TEST", "THE TEST", "NO COORDS"]


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
    assert (a["property_type"], b["property_type"]) == ("Condominium", "Terrace House")
    assert (a["x"], a["y"]) == (27107.4972, 32559.5)
    c = out.row(2, named=True)
    assert c["x"] is None and c["y"] is None  # empty or missing coordinates become null


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
