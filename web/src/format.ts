// Числа на приборе читаются на бегу, поэтому крупные округляются до трёх
// значащих цифр, а точные значения остаются в подписях.

const SPACE = " "; // тонкий пробел: 2 439 123 не разъезжается в моноширинном

export function grouped(value: number): string {
  return Math.round(value).toLocaleString("ru-RU").replace(/\s/g, SPACE);
}

/** Крупные числа сокращаются как в США: K, M, B через тонкий пробел. */
export function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${trim(value / 1_000_000_000)}${SPACE}B`;
  if (abs >= 1_000_000) return `${trim(value / 1_000_000)}${SPACE}M`;
  if (abs >= 1_000) return `${trim(value / 1_000)}${SPACE}K`;
  return grouped(value);
}

function trim(value: number): string {
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  const text = value.toFixed(digits).replace(".", ",");
  // Хвостовые нули режутся только в дробной части: иначе «100» станет «1».
  return text.includes(",") ? text.replace(/,?0+$/, "") : text;
}

/** Деньги подписчику не счёт, а вес: точность до цента, разряды как у чисел. */
export function usd(value: number): string {
  const text = value
    .toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    .replace(/\s/g, SPACE);
  return `$${text}`;
}

export function clockTime(at: string | number): string {
  const date = typeof at === "number" ? new Date(at) : new Date(stamp(at));
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Момент из ISO-строки бэкенда в миллисекундах; null — событий не было. */
export function timestamp(iso: string | null): number | null {
  return iso === null ? null : new Date(stamp(iso)).getTime();
}

/** Подсказка у метки в шапке виджета: когда произошло последнее событие
 *  и когда обзор в последний раз пересчитывали. */
export function freshnessLabel(at: number | null, checkedAt: number, now: number): string {
  const checked = `пересчитано ${clockTime(checkedAt)}`;
  if (at === null) return `данных за период нет, ${checked}`;
  return `последние данные ${clockTime(at)}, ${agoLabel(Math.max(now - at, 0) / 1000)}; ${checked}`;
}

export function agoLabel(seconds: number): string {
  if (seconds < 5) return "только что";
  if (seconds < 60) return `${Math.round(seconds)} с назад`;
  return `${Math.round(seconds / 60)} мин назад`;
}

export function modelLabel(model: string | null): string {
  if (!model) return "—";
  return model.replace(/^claude-/, "").replace(/-\d{8}$/, "");
}


/** Сколько сессия уже идёт: «3 ч 12 мин», «7 мин». */
export function duration(fromIso: string | null, toIso: string | null): string {
  if (!fromIso || !toIso) return "—";
  const ms = new Date(stamp(toIso)).getTime() - new Date(stamp(fromIso)).getTime();
  const minutes = Math.max(Math.round(ms / 60000), 0);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
}

/** Насколько давно это было, от «сейчас». */
export function sinceLabel(iso: string | null): string {
  if (!iso) return "—";
  const seconds = (Date.now() - new Date(stamp(iso)).getTime()) / 1000;
  if (seconds < 45) return "только что";
  return agoLabel(seconds);
}

function stamp(iso: string): string {
  return iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`;
}

/** Имя инструмента для показа: `mcp__plugin_playwright_playwright__browser_click`
 *  превращается в `playwright: browser_click`. */
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
