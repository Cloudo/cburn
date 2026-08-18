---
paths:
  - "tests/**"
---

# Tests

- **A test that looks at a "today" slice is not nailed to a date.** A payload with a
  hardcoded `2026-08-14` fell inside the daily window yesterday and no longer does today -
  three telemetry tests failed exactly at the turn of the day. Parsing is checked with a
  fixed time (the format, deduplication, window bounds), while everything that counts "today"
  and "over the last day" is built from `datetime.now`.
