-- Схема БД cloudo-dash (см. TZ.md §3 «SQLite: модель данных»).
-- Применяется идемпотентно при старте; версия фиксируется в user_version.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Прочитанные транскрипты: offset для инкрементального дочитывания хвоста.
-- Сброс offset при смене inode или уменьшении size (усечение/пересоздание файла).
CREATE TABLE IF NOT EXISTS files (
    path   TEXT PRIMARY KEY,
    inode  INTEGER,
    size   INTEGER NOT NULL DEFAULT 0,
    offset INTEGER NOT NULL DEFAULT 0,
    mtime  REAL
);

CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,   -- имя каталога в ~/.claude/projects
    root_path    TEXT,                   -- cwd из транскрипта
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,  -- sessionId из транскрипта
    project_id        INTEGER REFERENCES projects(id),
    machine_id        TEXT,              -- задел на агрегацию с нескольких машин (TZ §11), пока NULL
    started_at        TEXT,
    last_at           TEXT,
    first_prompt      TEXT,              -- обрезан до 200 символов
    parent_session_id TEXT REFERENCES sessions(id),  -- resume-форк
    turns             INTEGER NOT NULL DEFAULT 0,
    tokens_in         INTEGER NOT NULL DEFAULT 0,
    tokens_out        INTEGER NOT NULL DEFAULT 0,
    cache_read        INTEGER NOT NULL DEFAULT 0,
    cache_write       INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0,
    is_live           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, last_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent  ON sessions(parent_session_id);

CREATE TABLE IF NOT EXISTS turns (
    id               INTEGER PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES sessions(id),
    uuid             TEXT UNIQUE,        -- uuid записи транскрипта, защита от повторного импорта
    ts               TEXT NOT NULL,
    model            TEXT,
    role             TEXT,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_read       INTEGER NOT NULL DEFAULT 0,
    cache_write      INTEGER NOT NULL DEFAULT 0,
    context_estimate INTEGER NOT NULL DEFAULT 0,  -- input + cache_read + cache_write
    cost_usd         REAL NOT NULL DEFAULT 0,
    is_idle          INTEGER NOT NULL DEFAULT 0   -- холостой ход, эвристика TZ §6
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_turns_ts      ON turns(ts);

CREATE TABLE IF NOT EXISTS tool_calls (
    id      INTEGER PRIMARY KEY,
    turn_id INTEGER NOT NULL REFERENCES turns(id),
    tool    TEXT NOT NULL,  -- Bash, Edit, Read, Task, mcp__*...
    detail  TEXT            -- для Bash: нормализованная команда (первое слово + подкоманда)
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_turn ON tool_calls(turn_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);

CREATE TABLE IF NOT EXISTS advice (
    id           INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    machine_id   TEXT,
    period_start TEXT,
    period_end   TEXT,
    digest_json  TEXT,
    response_md  TEXT,
    model        TEXT,
    cost_usd     REAL NOT NULL DEFAULT 0,
    max_severity TEXT,
    status       TEXT NOT NULL DEFAULT 'new'  -- new | accepted | rejected (TZ §5, экран «Советы»)
);

CREATE TABLE IF NOT EXISTS model_prices (
    model                TEXT PRIMARY KEY,
    in_per_mtok          REAL NOT NULL DEFAULT 0,
    out_per_mtok         REAL NOT NULL DEFAULT 0,
    cache_write_per_mtok REAL NOT NULL DEFAULT 0,
    cache_read_per_mtok  REAL NOT NULL DEFAULT 0
);

-- Незнакомые типы записей транскрипта: парсер не падает, кладёт сырьё сюда (TZ §2).
CREATE TABLE IF NOT EXISTS raw_events (
    id      INTEGER PRIMARY KEY,
    path    TEXT,
    line_no INTEGER,
    ts      TEXT,
    type    TEXT,
    payload TEXT
);
