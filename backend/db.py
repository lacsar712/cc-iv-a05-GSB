import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    site text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS site text;
UPDATE iv_scans SET site = '一号场区' WHERE site IS NULL;
CREATE TABLE IF NOT EXISTS work_tickets (
    id serial PRIMARY KEY,
    site text NOT NULL,
    work_date date NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    applicant text NOT NULL,
    signed_by text,
    signed_at timestamptz,
    created_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_tickets_site_date
    ON work_tickets (site, work_date);
CREATE TABLE IF NOT EXISTS ticket_traces (
    id serial PRIMARY KEY,
    ticket_id integer NOT NULL REFERENCES work_tickets (id),
    site text NOT NULL,
    action text NOT NULL,
    actor text NOT NULL,
    detail text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS sign_grants (
    id serial PRIMARY KEY,
    username text NOT NULL,
    permission text NOT NULL DEFAULT 'ticket_sign',
    granted_by text NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked boolean NOT NULL DEFAULT false
);
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
