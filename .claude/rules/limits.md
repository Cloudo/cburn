---
paths:
  - "src/cburn/limits.py"
---

# Subscription limits

- **The subscription limits come from Anthropic, we do not count them.** Claude Code calls
  `GET /api/oauth/usage` with an OAuth token from the macOS keychain (the
  `Claude Code-credentials` entry) and stores the answer in `~/.claude.json` under
  `cachedUsageUtilization`. That cache refreshes only when Claude Code itself
  opens `/usage` and lags by days, so the main path is our own request
  (no more often than once every 5 minutes, the endpoint answers 429 with `Retry-After`),
  and the cache is the fallback. The token is never stored: only percentages go out.
