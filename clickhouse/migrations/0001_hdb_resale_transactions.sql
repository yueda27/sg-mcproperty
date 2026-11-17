-- HDB resale flat transactions (data.gov.sg, "Resale flat prices").
-- ReplacingMergeTree: re-loading the same file is safe; duplicates collapse on merge.
-- Query with FINAL (or argMax) when exact counts matter.
CREATE TABLE IF NOT EXISTS sg_property.hdb_resale_transactions
(
    month               Date,                       -- first day of the transaction month
    town                LowCardinality(String),
    flat_type           LowCardinality(String),
    block               String,
    street_name         String,
    storey_range        LowCardinality(String),
    floor_area_sqm      Float32,
    flat_model          LowCardinality(String),
    lease_commence_year UInt16,
    remaining_lease     Nullable(String),           -- absent in older source files
    resale_price        UInt32,
    loaded_at           DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(loaded_at)
PARTITION BY toYear(month)
ORDER BY (town, flat_type, month, block, street_name, storey_range, floor_area_sqm, resale_price);
