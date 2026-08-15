-- The cburn database schema (see TZ.md §3 "SQLite: the data model").
-- Applied idempotently at start; the version is recorded in user_version.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Transcripts that have been read: the offset for incrementally reading the tail.
-- The offset resets when the inode changes or the size shrinks (truncation/recreation).
CREATE TABLE IF NOT EXISTS files (
    path   TEXT PRIMARY KEY,
    inode  INTEGER,
    size   INTEGER NOT NULL DEFAULT 0,
    offset INTEGER NOT NULL DEFAULT 0,
    mtime  REAL
);

CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,   -- directory name in ~/.claude/projects
    root_path    TEXT,                   -- cwd from the transcript
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,  -- sessionId from the transcript
    project_id        INTEGER REFERENCES projects(id),
    machine_id        TEXT,              -- room for multi-machine aggregation (TZ §11), NULL for now
    started_at        TEXT,
    last_at           TEXT,
    first_prompt      TEXT,              -- trimmed to 200 characters
    last_prompt       TEXT,              -- from the last-prompt record, trimmed the same way
    title             TEXT,              -- from ai-title / custom-title records
    title_source      TEXT,              -- ai | custom (a self-set title wins)
    hidden            INTEGER NOT NULL DEFAULT 0,  -- removed from the dashboard by hand
    parent_session_id TEXT REFERENCES sessions(id),  -- resume fork
    turns             INTEGER NOT NULL DEFAULT 0,
    tokens_in         INTEGER NOT NULL DEFAULT 0,
    tokens_out        INTEGER NOT NULL DEFAULT 0,
    cache_read        INTEGER NOT NULL DEFAULT 0,
    cache_write       INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0,
    last_context      INTEGER NOT NULL DEFAULT 0,  -- context_estimate of the last turn
    -- What the session ends with at the last record read: a prompt or a
    -- tool_result without an answer means a request is running right now.
    last_record_kind  TEXT,
    last_record_at    TEXT,
    last_stop_reason  TEXT,              -- stop_reason of the last turn: end_turn | tool_use | ...
    is_live           INTEGER,  -- 1 alive, 0 no process, NULL not asked (B4)
    -- The start moment of the youngest child of the session process: it shows
    -- whether a tool is running or a permission request hangs.
    busy_since        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, last_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent  ON sessions(parent_session_id);

-- A turn is one model answer. In the transcript it is spread over several records
-- (thinking, text, every tool_use), and each carries the full identical usage,
-- so the key is message_id, not the record uuid. On resume past turns are copied
-- into a new file with a new sessionId, keeping message_id: UNIQUE swallows that too.
CREATE TABLE IF NOT EXISTS turns (
    id               INTEGER PRIMARY KEY,
    message_id       TEXT NOT NULL UNIQUE,
    session_id       TEXT NOT NULL REFERENCES sessions(id),
    request_id       TEXT,
    uuid             TEXT,               -- uuid of the turn's first record, for parentUuid chains
    parent_uuid      TEXT,
    ts               TEXT NOT NULL,
    model            TEXT,
    role             TEXT,
    is_sidechain     INTEGER NOT NULL DEFAULT 0,  -- a subagent turn (TZ §4)
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_read       INTEGER NOT NULL DEFAULT 0,
    cache_write_5m   INTEGER NOT NULL DEFAULT 0,  -- billed differently from 1h
    cache_write_1h   INTEGER NOT NULL DEFAULT 0,
    context_estimate INTEGER NOT NULL DEFAULT 0,  -- input + cache_read + cache_write
    cost_usd         REAL NOT NULL DEFAULT 0,
    is_idle          INTEGER NOT NULL DEFAULT 0   -- an idle turn, the TZ §6 heuristic
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_turns_ts      ON turns(ts);

-- tool_use_id is unique across history and makes re-importing the tail idempotent
-- even when the turn is already stored and its blocks are read on the next pass.
CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY,
    turn_id     INTEGER NOT NULL REFERENCES turns(id),
    tool_use_id TEXT UNIQUE,
    tool        TEXT NOT NULL,  -- Bash, Edit, Read, Task, mcp__*...
    detail      TEXT            -- for Bash: the normalised command (first word + subcommand)
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_turn ON tool_calls(turn_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);

CREATE TABLE IF NOT EXISTS advice (
    id           INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'hourly',  -- hourly | weekly (task D3)
    machine_id   TEXT,
    period_start TEXT,
    period_end   TEXT,
    digest_json  TEXT,
    response_md  TEXT,
    model        TEXT,
    cost_usd     REAL NOT NULL DEFAULT 0,
    max_severity TEXT,
    status       TEXT NOT NULL DEFAULT 'new'  -- new | accepted | rejected (TZ §5, the "Advice" screen)
);

CREATE TABLE IF NOT EXISTS model_prices (
    model                TEXT PRIMARY KEY,
    in_per_mtok          REAL NOT NULL DEFAULT 0,
    out_per_mtok         REAL NOT NULL DEFAULT 0,
    cache_write_per_mtok REAL NOT NULL DEFAULT 0,  -- the 5-minute cache
    cache_write_1h_per_mtok REAL NOT NULL DEFAULT 0,
    cache_read_per_mtok  REAL NOT NULL DEFAULT 0
);

-- Individual tips of one analysis: each has its own status so that a dismissed one
-- does not come again (TZ §5, task D6). `key` is a stable tip fingerprint:
-- it recognises the tip on the next tick even if the wording changed.
CREATE TABLE IF NOT EXISTS advice_items (
    id        INTEGER PRIMARY KEY,
    advice_id INTEGER NOT NULL REFERENCES advice(id),
    key       TEXT NOT NULL,
    title     TEXT NOT NULL,
    severity  TEXT NOT NULL DEFAULT 'info',  -- info | warn | crit
    detail    TEXT,
    action    TEXT,
    evidence  TEXT NOT NULL,   -- without it a tip is not stored (TZ §6)
    status    TEXT NOT NULL DEFAULT 'new',   -- new | accepted | rejected
    UNIQUE (advice_id, key)
);
CREATE INDEX IF NOT EXISTS idx_advice_items ON advice_items(status, key);

-- Notable moments inside a session: auto-compaction (after it the context
-- collapses), other milestones later on. They are needed so the context chart
-- shows why it dropped (TZ §5, task C2).
CREATE TABLE IF NOT EXISTS session_events (
    id         INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,  -- compact
    UNIQUE (session_id, ts, kind)
);
CREATE INDEX IF NOT EXISTS idx_session_events ON session_events(session_id, ts);

-- Unknown transcript record types: the parser does not fail, it stores the raw data here (TZ §2).
CREATE TABLE IF NOT EXISTS raw_events (
    id      INTEGER PRIMARY KEY,
    path    TEXT,
    line_no INTEGER,  -- line number inside the chunk of the file that was read
    ts      TEXT,
    type    TEXT,
    version TEXT,     -- the Claude Code version: the format changes between them
    payload TEXT
);

-- The full payload is kept only for the first samples of each (type, version) pair,
-- beyond that a counter grows: unknown records run into tens of thousands in history
-- (attachment alone is 33 thousand per version), and without a limit the table
-- outgrows the useful data (TZ §2, task B6).
CREATE TABLE IF NOT EXISTS raw_event_counts (
    type     TEXT NOT NULL,
    version  TEXT NOT NULL DEFAULT '',
    seen     INTEGER NOT NULL DEFAULT 0,
    first_at TEXT,
    last_at  TEXT,
    PRIMARY KEY (type, version)
);

-- The second data channel: official Claude Code telemetry over OTLP (TZ §2, milestone E).
-- It is stored next to the parser data rather than on top of it: the channel is in beta,
-- and on a mismatch the transcripts are to be trusted. `key` is the point fingerprint: the
-- exporter repeats an unacknowledged payload, and without it a retry would double the numbers.
CREATE TABLE IF NOT EXISTS otel_metrics (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,   -- claude_code.token.usage, claude_code.cost.usage, ...
    ts         TEXT NOT NULL,   -- the end of the point window
    start_ts   TEXT,            -- the start of the window: delta points get their own per export
    session_id TEXT,
    model      TEXT,
    kind       TEXT,            -- the type attribute: input | output | cacheRead | cacheCreation | added | ...
    value      REAL NOT NULL,
    attrs      TEXT NOT NULL DEFAULT '{}'  -- the remaining point and resource attributes
);
CREATE INDEX IF NOT EXISTS idx_otel_metrics_ts      ON otel_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_otel_metrics_session ON otel_metrics(session_id, name);
CREATE INDEX IF NOT EXISTS idx_otel_metrics_name    ON otel_metrics(name, ts);

-- Telemetry events (OTLP logs): api_request with the exact price and duration,
-- tool_decision with permission decisions - neither of those is in the transcript.
CREATE TABLE IF NOT EXISTS otel_events (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,   -- api_request, tool_decision, tool_result, api_error, ...
    ts         TEXT NOT NULL,
    session_id TEXT,
    attrs      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_otel_events_ts      ON otel_events(ts);
CREATE INDEX IF NOT EXISTS idx_otel_events_session ON otel_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_otel_events_name    ON otel_events(name, ts);

-- Accounting of the reception itself: it shows whether telemetry arrives at all and how
-- many chunks of a payload the receiver failed to understand (`cburn otel`, check E3).
CREATE TABLE IF NOT EXISTS otel_ingest (
    signal  TEXT PRIMARY KEY,   -- metrics | logs | traces
    last_at TEXT,
    batches INTEGER NOT NULL DEFAULT 0,
    stored  INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0
);

-- What has already been sent to telegram (task D5). Not history for history's sake but
-- memory: the cooldown is counted from it, so one session does not wake you every
-- minute, and it shows that today's daily digest has already gone out.
CREATE TABLE IF NOT EXISTS notifications (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    kind     TEXT NOT NULL,   -- digest | daily | alert
    key      TEXT,            -- the session for an alert, the date for a digest
    severity TEXT NOT NULL DEFAULT 'info',  -- info | warn | crit
    channel  TEXT,            -- bridge | bot
    text     TEXT,
    ok       INTEGER NOT NULL DEFAULT 1     -- 0 if sending failed
);
CREATE INDEX IF NOT EXISTS idx_notifications_kind ON notifications(kind, ts);
CREATE INDEX IF NOT EXISTS idx_notifications_key  ON notifications(key, ts);

-- Small notification state: until when the global pause is on.
-- A table of its own rather than a config field: the pause is set by a button and lives
-- for hours, while the config is edited by hand and re-read on every request.
CREATE TABLE IF NOT EXISTS notifier_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
