---
paths:
  - "src/cburn/notifier/**"
---

# Notifications (milestone D)

- **An alert does no counting of its own.** The thresholds and the numbers come from the same
  overview the screen shows: otherwise the phone and the dashboard would carry different
  numbers, and neither would be trusted.
- **The memory lives in the database, not in the process.** `notifications` stores what went
  out and when: the cooldown survives a restart, and a failed send is marked
  `ok = 0` - otherwise silence after a failure would look like "we already warned".
- **A pause does not switch the instrument off.** Two hours of silence hold everything except
  `crit`: when the spend is burning right now, staying quiet is not an option. It is set by a
  tray item and by the `/api/notify/pause` endpoint.
- **The bridge token is not duplicated.** It is read from `~/.config/cc-tg-bridge/config.json`,
  that is, from where the bridge itself reads it.
- **The notification tick lives inside the advisor loop** - it already ticks once a minute,
  and a second loop for two checks is unnecessary.
