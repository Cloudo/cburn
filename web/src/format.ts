// Числа на приборе читаются на бегу, поэтому крупные округляются до трёх
// значащих цифр, а точные значения остаются в подписях.

const SPACE = " "; // тонкий пробел: 2 439 123 не разъезжается в моноширинном

//: Язык форматирования. Числа и даты рисуются вне React, поэтому язык
//: приезжает сюда из провайдера, а не через пропсы.
let lang: "ru" | "en" = "ru";
let locale = "ru-RU";

export function setFormatLang(next: "ru" | "en"): void {
  lang = next;
  locale = next === "ru" ? "ru-RU" : "en-US";
}

/** Слово по текущему языку — для коротких единиц внутри формата. */
function word(ru: string, en: string): string {
  return lang === "ru" ? ru : en;
}

export function grouped(value: number): string {
  // Русские разряды разделены пробелом (его и утончаем), английские — запятой.
  return Math.round(value).toLocaleString(locale).replace(/\s/g, SPACE);
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
  const text = value.toFixed(digits).replace(".", word(",", "."));
  // Хвостовые нули режутся только в дробной части: иначе «100» станет «1».
  const point = word(",", ".");
  return text.includes(point) ? text.replace(/[.,]?0+$/, "") : text;
}

/** Деньги подписчику не счёт, а вес: точность до цента, разряды как у чисел.
 *
 *  Мелочь дешевле цента показывается точнее: «$0,00» читается как ноль, а речь
 *  о живом расходе — например, о служебных запросах телеметрии. */
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
    hourCycle: "h23", // 21:05:03 короче, чем 9:05:03 PM, и не прыгает по ширине
  });
}

/** Момент из ISO-строки бэкенда в миллисекундах; null — событий не было. */
export function timestamp(iso: string | null): number | null {
  return iso === null ? null : new Date(stamp(iso)).getTime();
}

/** Подсказка у метки в шапке виджета: когда произошло последнее событие
 *  и когда обзор в последний раз пересчитывали. */
export function freshnessLabel(at: number | null, checkedAt: number, now: number): string {
  const checked = `${word("пересчитано", "recomputed")} ${clockTime(checkedAt)}`;
  if (at === null) return `${word("данных за период нет", "no data for the period")}, ${checked}`;
  const ago = agoLabel(Math.max(now - at, 0) / 1000);
  return `${word("последние данные", "last data")} ${clockTime(at)}, ${ago}; ${checked}`;
}

export function agoLabel(seconds: number): string {
  if (seconds < 5) return word("только что", "just now");
  if (seconds < 60) return `${Math.round(seconds)} ${word("с назад", "s ago")}`;
  return `${Math.round(seconds / 60)} ${word("мин назад", "min ago")}`;
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
  if (minutes < 60) return `${minutes} ${word("мин", "m")}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const h = `${hours} ${word("ч", "h")}`;
  return rest ? `${h} ${rest} ${word("мин", "m")}` : h;
}

/** Насколько давно это было, от «сейчас». */
export function sinceLabel(iso: string | null): string {
  if (!iso) return "—";
  const seconds = (Date.now() - new Date(stamp(iso)).getTime()) / 1000;
  if (seconds < 45) return word("только что", "just now");
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
