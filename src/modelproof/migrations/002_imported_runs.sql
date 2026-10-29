-- 002_imported_runs: evidence bundles imported from other machines must be
-- distinguishable from locally-executed runs and must record their origin.
ALTER TABLE runs ADD COLUMN imported_from TEXT;
