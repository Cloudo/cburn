// Numbers on the instrument are read on the run, so large ones are rounded to three
// significant digits, and the exact values stay in the captions.

import { translate } from "./dict";

const SPACE = " "; // a thin space: 2 439 123 does not sprawl in a monospace font

//: The formatting language. Numbers and dates are drawn outside React, so the language
//: arrives here from the provider rather than through props.
let lang: "ru" | "en" = "ru";
let locale = "ru-RU";

export function setFormatLang(next: "ru" | "en"): void {
  lang = next;
  locale = next === "ru" ? "ru-RU" : "en-US";
}

/** A word from the dictionary - for short units inside a format. */
function word(key: string): string {
  return translate(lang, key);
}

export function grouped(value: number): string {
  // Russian groups are separated by a space (which we thin out), English ones by a comma.
  return Math.round(value).toLocaleString(locale).replace(/\s/g, SPACE);
}

/** Large numbers are shortened through a thin space, and the suffix speaks the language of
 *  the interface: K, M, B in English, "тыс", "млн", "млрд" in Russian. A Latin letter next
 *  to a Cyrillic unit ("1,71 M/мин") reads as two alphabets in one word. */
export function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${trim(value / 1_000_000_000)}${SPACE}${word("format.billion")}`;
  if (abs >= 1_000_000) return `${trim(value / 1_000_000)}${SPACE}${word("format.million")}`;
  if (abs >= 1_000) return `${trim(value / 1_000)}${SPACE}${word("format.thousand")}`;
  return grouped(value);
}

function trim(value: number): string {
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  const text = value.toFixed(digits).replace(".", word("format.decimal"));
  // Trailing zeros are cut only in the fractional part: otherwise "100" would become "1".
  const point = word("format.decimal");
  return text.includes(point) ? text.replace(/[.,]?0+$/, "") : text;
}

/** Money is not a bill to a subscriber but a weight: cent precision, groups as in numbers.
 *
 *  Change smaller than a cent is shown more precisely: "$0.00" reads as zero, while the
 *  talk is about live spend - telemetry service requests, for instance. */
export function usd(value: number): string {
  const digits = value !== 0 && Math.abs(value) < 0.01 ? 4 : 2;
  const text = value
    .toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })
    .replace(/\s/g, SPACE);
  return `$${text}`;
}

export function clockTime(at: string | number): string {
  const date = typeof at === "number" ? new Date(at) : new Date(stamp(at));
  return date.toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23", // 21:05:03 is shorter than 9:05:03 PM and does not jump in width
  });
}

/** A moment from the backend ISO string in milliseconds; null means there were no events. */
export function timestamp(iso: string | null): number | null {
  return iso === null ? null : new Date(stamp(iso)).getTime();
}

/** The tooltip on the mark in a widget header: when the last event happened
 *  and when the overview was last recomputed. */
export function freshnessLabel(at: number | null, checkedAt: number, now: number): string {
  const checked = `${word("format.recomputed")} ${clockTime(checkedAt)}`;
  if (at === null) return `${word("format.noData")}, ${checked}`;
  const ago = agoLabel(Math.max(now - at, 0) / 1000);
  return `${word("format.lastData")} ${clockTime(at)}, ${ago}; ${checked}`;
}

/** How long ago that was: "just now", "42 s ago", "36 min ago", "4 h 10 min ago", "3 d ago". */
export function agoLabel(seconds: number): string {
  if (seconds < 5) return word("format.justNow");
  if (seconds < 60) return `${Math.round(seconds)} ${word("format.secondsAgo")}`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} ${word("format.minutesAgo")}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    // The minutes matter in the first hours and turn into noise past them.
    const rest = minutes % 60;
    return rest && hours < 6
      ? `${hours} ${word("format.hours")} ${rest} ${word("format.minutesAgo")}`
      : `${hours} ${word("format.hoursAgo")}`;
  }
  return `${Math.floor(hours / 24)} ${word("format.daysAgo")}`;
}

export function modelLabel(model: string | null): string {
  if (!model) return "—";
  return model.replace(/^claude-/, "").replace(/-\d{8}$/, "");
}


/** How long the session has been running: "3 h 12 min", "7 min". */
export function duration(fromIso: string | null, toIso: string | null): string {
  if (!fromIso || !toIso) return "—";
  const ms = new Date(stamp(toIso)).getTime() - new Date(stamp(fromIso)).getTime();
  const minutes = Math.max(Math.round(ms / 60000), 0);
  if (minutes < 60) return `${minutes} ${word("format.minutes")}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const h = `${hours} ${word("format.hours")}`;
  return rest ? `${h} ${rest} ${word("format.minutes")}` : h;
}

/** A share in percent with tenths: "1.7", "12.0". The percent sign comes from the translation. */
export function share(value: number): string {
  return (value * 100).toLocaleString(locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

/** How long the work took: "8.4 s", "3 min", "1 h 5 min". */
export function spent(seconds: number): string {
  if (seconds < 60) {
    const value =
      seconds < 10
        ? seconds.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
        : String(Math.round(seconds));
    return `${value} ${word("format.seconds")}`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} ${word("format.minutes")}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const h = `${hours} ${word("format.hours")}`;
  return rest ? `${h} ${rest} ${word("format.minutes")}` : h;
}

/** How long ago that was, counting from "now"; past a week a date is read faster. */
export function sinceLabel(iso: string | null): string {
  if (!iso) return "—";
  const at = new Date(stamp(iso)).getTime();
  const seconds = (Date.now() - at) / 1000;
  if (seconds < 45) return word("format.justNow");
  if (seconds >= 7 * 86400) return dateLabel(at);
  return agoLabel(seconds);
}

/** A day of another week: "14 Aug", and the year only when it is not the current one. */
function dateLabel(at: number): string {
  const date = new Date(at);
  const options: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" };
  if (date.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return date.toLocaleDateString(locale, options);
}

function stamp(iso: string): string {
  return iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`;
}

/** The tool name for display: `mcp__plugin_playwright_playwright__browser_click`
 *  turns into `playwright: browser_click`. */
export function toolLabel(tool: string): string {
  if (!tool.startsWith("mcp__")) return tool;
  const parts = tool.slice("mcp__".length).split("__");
  const name = parts.pop();
  if (!name) return tool;
  const words = (parts.pop() ?? "")
    .split("_")
    .filter((word, index, all) => all.indexOf(word) === index);
  const server = words[words.length - 1];
  return server ? `${server}: ${name}` : name;
}
