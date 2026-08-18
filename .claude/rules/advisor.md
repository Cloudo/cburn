---
paths:
  - "src/cburn/analyzer/**"
---

# `claude -p` (the advisor)

Checked against the installed Claude Code 2.1.231 - the contract changes, so check it again
at the next advisor edit:

- **`--max-turns` is gone.** Extra turns are cut off by an empty `--tools ""`
  (without tools the model has nothing to continue with) and by `--max-budget-usd`.
- **`--json-schema` works in `-p` too**: the parsed answer arrives in a separate
  `structured_output` field, and there is no need to dig JSON out of the text by hand. The
  `result` field is there as well - it holds the same structure as a string.
- **The `--output-format json` envelope** carries `total_cost_usd`, `usage`,
  `modelUsage` (the full model name), `num_turns`, `stop_reason`, `is_error`.
- **The `haiku` alias is alive** and expands into `claude-haiku-4-5-20251001`.
- **The schema may hold an optional nested object**, and the model fills it. Checked on a
  live call with the `act` of task D7: haiku returned `close_session`, `allow_permission`
  (with `scope` and `project`), `disable_plugin` and `disable_hook` for the four sections
  of the digest, and a tip without a fitting action simply came back without `act`.
- **A tick costs about $0.08** on haiku with a digest of ~1.5k tokens: almost all of that is
  writing the system prompt into the cache (11k tokens). `--strict-mcp-config` and
  `--exclude-dynamic-system-prompt-sections` trim it but do not zero it out.
  The "<= $0.02 per tick" threshold from TZ §10 is unreachable on today's CLI.
