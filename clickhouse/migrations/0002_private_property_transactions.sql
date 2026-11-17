-- Private property transactions (URA REALIS / PMI_Resi_Transaction API).
-- One row per transaction; a project's rows come from several URA batches.
CREATE TABLE IF NOT EXISTS sg_property.private_property_transactions
(
    contract_month  Date,                           -- parsed from URA contractDate (mmyy)
    project         String,
    street          String,
    district        UInt8,
    market_segment  LowCardinality(String),         -- CCR / RCR / OCR
    property_type   LowCardinality(String),
    type_of_sale    LowCardinality(String),         -- 1 New Sale, 2 Sub Sale, 3 Resale
    tenure          LowCardinality(String),
    type_of_area    LowCardinality(String),         -- Strata / Land
    area_sqm        Float32,
    floor_range     LowCardinality(String),
    no_of_units     UInt16,
    price           UInt64,
    x               Nullable(Float64),              -- SVY21 coordinates
    y               Nullable(Float64),
    loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(loaded_at)
PARTITION BY toYear(contract_month)
ORDER BY (district, project, contract_month, floor_range, area_sqm, price, type_of_sale);
