-- Flattened private residential transactions (URA PMI_Resi_Transaction), matching the
-- output of loaders/private_residential.py transform(). One row per transaction.
-- Supersedes the unused private_property_transactions table from 0002.
-- Plain MergeTree on purpose: URA has no transaction id, so identical-looking sales are
-- real separate rows and must not be deduplicated. The loader replaces the table contents
-- on each run instead (see loaders/to_clickhouse.py). ORDER BY is for query speed only.
CREATE TABLE IF NOT EXISTS sg_property.private_residential_transactions
(
    project        String,
    street         String,
    area_sqft      Float64,                       -- converted from URA sqm
    floor          Nullable(Int16),               -- midpoint of floor range, null for landed
    contract_date  DateTime,                      -- first of month, URA gives mmyy only
    type_of_sale   LowCardinality(String),        -- New Sale / Sub Sale / Resale
    price          Float64,                       -- SGD
    district       UInt8,
    tenure         LowCardinality(String),        -- e.g. 99 years, 999 years, Freehold
    lease_year     Nullable(UInt16),              -- lease commencement year, null for freehold
    loaded_at      DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYear(contract_date)
ORDER BY (district, project, contract_date);
