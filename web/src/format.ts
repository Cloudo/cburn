// Числа на приборе читаются на бегу, поэтому крупные округляются до трёх
// значащих цифр, а точные значения остаются в подписях.

const SPACE = " "; // тонкий пробел: 2 439 123 не разъезжается в моноширинном

export function grouped(value: number): string {
  return Math.round(value).toLocaleString("ru-RU").replace(/\s/g, SPACE);
}

export function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${trim(value / 1_000_000)}${SPACE}млн`;
  if (abs >= 1_000) return `${trim(value / 1_000)}${SPACE}тыс`;
  return grouped(value);
}

function trim(value: number): string {
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  const text = value.toFixed(digits).replace(".", ",");
  // Хвостовые нули режутся только в дробной части: иначе «100» станет «1».
  return text.includes(",") ? text.replace(/,?0+$/, "") : text;
}

export function clockTime(iso: string): string {
  const date = new Date(stamp(iso));
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
