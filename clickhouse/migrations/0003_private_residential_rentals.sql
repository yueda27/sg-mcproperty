-- Private residential rental contracts (URA PMI_Resi_Rental), matching the output of
-- loaders/private_rental.py transform(). One row per rental contract reported by URA.
-- Plain MergeTree on purpose, like the sales table: URA has no contract id and area is only
-- a band, so identical looking contracts are real separate rows and must not be deduplicated.
-- The loader replaces the table contents on each run. ORDER BY is for query speed only.
CREATE TABLE IF NOT EXISTS sg_property.private_residential_rentals
(
    project        String,
    street         String,
    district       UInt8,
    property_type  LowCardinality(String),        -- as URA reports it, e.g. Non-landed Properties
    lease_date     DateTime,                      -- first of month, URA gives mmyy only
    area_sqft_min  Nullable(UInt32),              -- URA gives a band: 1700-1800, >3000 or <=500
    area_sqft_max  Nullable(UInt32),              -- null for the open ended top band (>3000)
    bedrooms       Nullable(UInt8),               -- null when URA says NA or omits it
    rent           Float64,                       -- monthly rent, SGD
    x              Nullable(Float64),             -- project location in SVY21 metres
    y              Nullable(Float64),
    loaded_at      DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYear(lease_date)
ORDER BY (district, project, lease_date);
