"""Collector: watchdog over ~/.claude/projects + incremental JSONL parser (SPEC §3).

Split of responsibilities:
  parser.py  - parse one transcript line into turns/tool_calls/raw_events records;
  indexer.py - read the file tail from the stored offset, initial history indexing;
  watcher.py - subscribe to FS events and queue files for reading.

Invariants: ~/.claude is read-only; a broken line does not stop the walk
(the line goes to the log, the offset moves on); unknown record types go to raw_events.
"""
