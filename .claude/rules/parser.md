---
paths:
  - "src/cburn/collector/**"
  - "tests/fixtures/transcripts/**"
---

# The transcript parser

Invariants:

- **The parser is tolerant.** The transcript format is undocumented and changes between
  versions: ignore unknown fields, stash unknown record types into `raw_events`, and a broken
  line goes to the log without stopping the walk - the offset moves on.
- **Incrementality.** Only the file tail is read from the stored offset; truncation and
  recreation are caught by the inode + size pair. `reindex --full` is therefore run with
  the server stopped: the watcher writes an offset for the file being appended to right
  now, and the walk started next to it reads that file from the end - checked live, the
  prompts of the current session went missing exactly like that.
- **The normalised bash command is the only thing kept from a Bash call**: no arguments,
  no paths, no file names. The subcommand is taken only for commands from the allowlist in
  `parser.py`, otherwise a file name leaks into the database disguised as a subcommand.

## What is already known about the transcript format

Verified against the real history (590 MB, Claude Code versions 2.1.220-228) - these things
diverge from the specification text, and they are what to trust:

- **A record is not a turn**: one assistant answer is spread over several JSONL records
  (one per `thinking`/`text`/`tool_use` block), and each carries the full `usage`. The turn
  key is `message.id`; summing over records inflates the spend roughly fourfold.
- **`usage` in the records of a turn is uneven**: for some records it is still zero - the
  spend is filled in when the answer completes (2,527 turns out of 15,197). The correct value
  is the element-wise maximum over the turn's records: the first record understates the spend
  by a third, the sum inflates it several times over.
- **A file is not a session**: one JSONL holds several `sessionId`s. Turns are grouped by the
  record field, the file name is only a key in `files`.
- **`usage` is wider than the specification**: `cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens`,
  `iterations[]`, `service_tier`, `speed`; next to the record sit `effort` and `requestId`.
  A write into the 1h cache is billed differently from a 5m one - count them apart.
- **There are more than a dozen record types** (`attachment`, `file-history-snapshot`,
  `queue-operation`, `ai-title`, `frame-link`...), and the `summary` type is gone;
  auto-compaction shows as `isCompactSummary: true` on a user record.
- **`user` records come in two kinds**: a real prompt and a `tool_result`. Tell them apart by
  the presence of a `tool_result` block in the content, not by `promptSource` - that one is
  present on less than 5% of records. Prompt content comes both as a string and as an array of
  blocks.
- **Subagents** are marked `isSidechain` and live in the parent session's file.
- **The session title** comes from `ai-title` records (generated) and `custom-title` (set by a
  human, and it wins). They hold only a `sessionId` and the text: neither a time nor a uuid,
  so they do not affect the session state.
- **A slash command in the transcript** is a `<local-command-caveat>` plus
  `<command-name>`/`<command-message>` blocks, with no live text in the record at all.
- **The transcript is appended in bursts every 2-6 s**, and `usage` appears only together with
  a finished turn. The spend inside an unfinished turn is invisible in the JSONL: finer than a
  5-second granularity is impossible without OTel (M4).
- **Turns are duplicated across files**: resume copies past turns into a new file with a new
  `sessionId`, keeping `uuid` and `message.id` (copies of one turn were seen in 20 files).
  Deduplication is mandatory and goes by `message.id`, `uuid` will not do for it.
- **`<synthetic>`** in `message.model` marks service answers ("No response requested", hitting
  the limit) with zero usage and no `requestId`; they do not become turns.
