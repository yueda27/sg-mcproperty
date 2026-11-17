from datetime import date

import polars as pl

from sg_mcproperty.loaders.hdb_resale import COLUMNS, transform


def test_transform_maps_columns_and_types():
    raw = pl.DataFrame(
        {
            "month": ["2024-03"],
            "town": [" ANG MO KIO "],
            "flat_type": ["4 ROOM"],
            "block": ["101"],
            "street_name": ["ANG MO KIO AVE 1"],
            "storey_range": ["04 TO 06"],
            "floor_area_sqm": ["92.0"],
            "flat_model": ["New Generation"],
            "lease_commence_date": ["1980"],
            "resale_price": ["500000.00"],
        }
    )
    out = transform(raw)
    assert out.columns == COLUMNS
    row = out.row(0, named=True)
    assert row["month"] == date(2024, 3, 1)
    assert row["town"] == "ANG MO KIO"
    assert row["remaining_lease"] is None
    assert row["resale_price"] == 500000
