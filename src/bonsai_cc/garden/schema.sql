-- bonsai-cc garden, schema v3.
--
-- One row per session. Indices cover the queries the browser makes:
-- by date (default sort), by project, by detected language.
--
-- ``final_ascii`` and ``final_state_json`` are snapshots taken at
-- save time. ``event_log_path`` points to the JSONL journal that
-- can replay the session bit-for-bit (the determinism contract from
-- DESIGN.md §2.3).
--
-- ``status`` (v2): how the row got here.
--   ``complete``  -- clean SessionEnd hook OR daemon shutdown OR
--                   idle-timeout finalisation. Tree is "done."
--   ``partial``   -- periodic snapshot taken while the session is
--                   still alive. Replay-equivalent geometry but
--                   may be superseded by a later save.
--   ``recovered`` -- found by the orphan-journal scan at daemon
--                   start. The original daemon process died
--                   (kill -9 / power loss) before saving; we
--                   reconstructed from the journal.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS sessions (
    id                 TEXT    PRIMARY KEY,
    seed_hex           TEXT    NOT NULL,
    started_at         INTEGER NOT NULL,           -- unix epoch ms (wall clock)
    ended_at           INTEGER,                    -- unix epoch ms, NULL if still live
    project_path       TEXT    NOT NULL,
    detected_language  TEXT,
    theme              TEXT    NOT NULL,
    tool_call_count    INTEGER NOT NULL DEFAULT 0,
    error_count        INTEGER NOT NULL DEFAULT 0,
    file_branch_count  INTEGER NOT NULL DEFAULT 0,
    final_ascii        TEXT,
    final_state_json   TEXT,                       -- TreeState serialised
    event_log_path     TEXT    NOT NULL,
    tags               TEXT,                       -- comma-separated; LIKE-queryable
    status             TEXT    NOT NULL DEFAULT 'complete',
    -- v3: cached SVG thumbnail, generated at save time so the web
    -- garden grid doesn't re-render every card on every page load.
    -- NULL until the next save touches the row (or a one-shot
    -- backfill runs).
    thumbnail_svg      TEXT,
    schema_version     INTEGER NOT NULL DEFAULT 3
);

CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_project    ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_sessions_lang       ON sessions(detected_language);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('version', '3');
