---
paths:
  - "src/cburn/processes.py"
  - "src/cburn/metrics.py"
---

# Session liveness

- **The session-to-process link is `claude agents --json`.** It prints the active sessions
  (interactive ones included) with `pid`, `sessionId`, `cwd` and a name. The process itself
  does not reveal the `sessionId`: it is neither in the arguments nor in the open descriptors -
  the transcript is appended to and closed right away.
- **"Waiting for permission" is recognised by processes, not by the transcript.** A long tool
  and a hanging "allow?" question look identical in the JSONL: a tool request without an
  answer. What tells them apart is a child of the session process started after the request
  (`busy_since` in `sessions`); MCP servers and background commands start earlier and do not
  count. Tools without a process of their own (MCP calls, `WebFetch`) still show up as waiting
  for a permission after 25 s.
  Where telemetry is on, the guess is unnecessary: the decision arrives as a `tool_decision`
  event (milestone E), and there is no reason to wait a quarter of a minute. The process rule
  stays as the fallback - for sessions without telemetry and for the case when it was switched
  off mid-work (`OTEL_STALE_SECONDS` in `metrics.py`).
