CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS "Automation";

CREATE TABLE IF NOT EXISTS "Automation"."gstFetch" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gstin VARCHAR(15) NOT NULL UNIQUE,
    business_info JSONB,
    filing_tables JSONB,
    legal_name TEXT,
    status TEXT,
    fetched_at TIMESTAMPTZ,
    fetch_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    state_name TEXT,
    district_name TEXT
);

CREATE TABLE IF NOT EXISTS "Automation"."gstEvidence" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gstin TEXT,
    company TEXT,
    state_name TEXT,
    district_name TEXT,
    month TEXT,
    financial_year TEXT,
    form JSONB,
    s3_path JSONB
);

-- Test record corresponding to the UUID/GSTIN stored in local Infisical.
INSERT INTO "Automation"."gstFetch" (id, gstin)
VALUES ('0342ea1d-78f8-43c2-b112-f13e88774733', '29AAACR5056C2ZM')
ON CONFLICT (id) DO NOTHING;
