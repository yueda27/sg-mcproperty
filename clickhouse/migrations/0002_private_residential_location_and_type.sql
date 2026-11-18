-- Adds property type and project location to private_residential_transactions.
-- x and y are SVY21 metres, so plain Euclidean distance gives metres between projects.
-- Existing rows get an empty property_type and null coordinates until the table is reloaded.
ALTER TABLE sg_property.private_residential_transactions
    ADD COLUMN IF NOT EXISTS property_type LowCardinality(String) DEFAULT '';

ALTER TABLE sg_property.private_residential_transactions
    ADD COLUMN IF NOT EXISTS x Nullable(Float64);

ALTER TABLE sg_property.private_residential_transactions
    ADD COLUMN IF NOT EXISTS y Nullable(Float64);
