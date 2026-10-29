-- 001_init: core schema. Runs are append-only records; cases keep complete
-- failure evidence. Private acceptance tests are NEVER stored here.
CREATE TABLE IF NOT EXISTS models (
    name            TEXT PRIMARY KEY,
    backend         TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    server_model_id TEXT,
    weights_dir     TEXT,
    weights_manifest_json TEXT,
    generation_json TEXT NOT NULL,
    declared_notes  TEXT,
    registered_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suites (
    suite_id      TEXT NOT NULL,
    version       TEXT NOT NULL,
    path          TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    registered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (suite_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id                    TEXT PRIMARY KEY,
    status                TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','INCOMPLETE','FAILED','CANCELLED')),
    verdict               TEXT CHECK (verdict IN ('PASS','FAIL','INCOMPLETE','INVALID','INSUFFICIENT_EVIDENCE')),
    verdict_explanation   TEXT,
    model_name            TEXT NOT NULL,
    suite_id              TEXT NOT NULL,
    suite_version         TEXT NOT NULL,
    config_json           TEXT NOT NULL,
    config_hash           TEXT NOT NULL,
    prompt_template_hash  TEXT,
    is_fixture            INTEGER NOT NULL DEFAULT 0,
    resumed_from          TEXT,
    cancel_requested      INTEGER NOT NULL DEFAULT 0,
    error                 TEXT,
    worker_pid            INTEGER,
    heartbeat_at          TEXT,
    started_at            TEXT,
    ended_at              TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS cases (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT NOT NULL REFERENCES runs(id),
    task_id               TEXT NOT NULL,
    category              TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('PASSED','FAILED','TIMEOUT','INVALID_OUTPUT','MODEL_ERROR','SANDBOX_ERROR')),
    is_infrastructure_failure INTEGER NOT NULL DEFAULT 0,
    duration_ms           INTEGER,
    checks_passed         INTEGER NOT NULL DEFAULT 0,
    checks_total          INTEGER NOT NULL DEFAULT 0,
    failed_checks_json    TEXT NOT NULL DEFAULT '[]',
    error_text            TEXT,
    raw_output_ref        TEXT,
    raw_output_sha256     TEXT,
    extracted_code_sha256 TEXT,
    scored_json           TEXT NOT NULL DEFAULT '{}',
    is_resumed            INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_cases_run ON cases(run_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    rel_path    TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    size        INTEGER NOT NULL,
    media_type  TEXT NOT NULL DEFAULT 'application/octet-stream',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (run_id, rel_path)
);

CREATE TABLE IF NOT EXISTS gates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(id),
    gate         TEXT NOT NULL,
    outcome      TEXT NOT NULL CHECK (outcome IN ('PASS','FAIL','WARN','BLOCKED','NOT_RUN')),
    detail       TEXT NOT NULL,
    evidence_ref TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (run_id, gate)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT,
    at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    level   TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
