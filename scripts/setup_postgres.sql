-- One-time Postgres setup for Garmin Coach.
--
-- Run this ONCE as the master "postgres" user:
--
--   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -f scripts\setup_postgres.sql
--
-- It creates:
--   1. a dedicated, non-superuser login role  "coach"  (apps should never
--      use the all-powerful "postgres" superuser),
--   2. a database  "garmin_coach"  owned by that role.
--
-- Both steps are written to be safe to re-run (they no-op if already done).

-- 1. Create the app login role if it doesn't exist yet.
DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'coach') THEN
      CREATE ROLE coach WITH LOGIN PASSWORD 'coach_local_dev';
   END IF;
END
$$;

-- 2. Create the database owned by "coach" if it doesn't exist yet.
--    (CREATE DATABASE can't run inside the DO block above, so we use psql's
--     \gexec to run it conditionally.)
SELECT 'CREATE DATABASE garmin_coach OWNER coach'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'garmin_coach')\gexec
