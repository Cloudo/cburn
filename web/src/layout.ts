// Раскладка дашборда: что где лежит, что скрыто. Хранится в localStorage —
// сервер про это не знает, расположение виджетов дело сугубо local-first.

import type { Layout } from "react-grid-layout";

export const STORAGE_KEY = "cloudo-dash.layout.v2";

/** Сколько колонок в сетке и какой высоты одна строка (px). */
export const COLUMNS = 12;
export const ROW_HEIGHT = 26;
export const MARGIN: [number, number] = [16, 16];

export type WidgetId =
  | "gauge"
  | "today"
  | "live"
  | "leaders"
  | "tools"
  | "models"
  | "idle"
  | "plan"
  | "feed";

export type WidgetMeta = { id: WidgetId; title: string; note: string };

/** Порядок в списке настроек — сверху вниз по важности. */
export const WIDGETS: WidgetMeta[] = [
  { id: "gauge", title: "прибор", note: "burn rate, разбивка, самописец" },
  { id: "today", title: "за сегодня", note: "суммы с местной полуночи" },
  { id: "live", title: "сейчас в работе", note: "сессии по статусам" },
  { id: "plan", title: "лимиты подписки", note: "проценты плана от Anthropic" },
  { id: "leaders", title: "больше всего за сегодня", note: "топ сессий" },
  { id: "tools", title: "на что уходят ходы", note: "инструменты и bash" },
  { id: "models", title: "модели за сегодня", note: "доля моделей" },
  { id: "idle", title: "холостые ходы", note: "ответ короче 10 токенов" },
  { id: "feed", title: "лента ходов", note: "последние ходы" },
];

/** Раскладка по умолчанию — та же, что была до перетаскивания. */
//: Высоты подобраны по фактической высоте содержимого (h = (px + 16) / 42),
//: чтобы при первом открытии не было ни пустоты, ни скролла.
export const DEFAULT_LAYOUT: Layout[] = [
  { i: "gauge", x: 0, y: 0, w: 7, h: 19, minW: 4, minH: 12 },
  { i: "today", x: 7, y: 0, w: 5, h: 8, minW: 3, minH: 4 },
  { i: "live", x: 7, y: 8, w: 5, h: 9, minW: 3, minH: 5 },
  { i: "plan", x: 7, y: 17, w: 5, h: 8, minW: 3, minH: 5 },
  { i: "tools", x: 0, y: 19, w: 7, h: 9, minW: 3, minH: 5 },
  { i: "leaders", x: 7, y: 25, w: 5, h: 6, minW: 3, minH: 4 },
  { i: "models", x: 0, y: 28, w: 4, h: 3, minW: 3, minH: 3 },
  { i: "idle", x: 4, y: 28, w: 3, h: 5, minW: 2, minH: 3 },
  { i: "feed", x: 0, y: 31, w: 12, h: 21, minW: 4, minH: 5 },
];

export type DashboardState = { layout: Layout[]; hidden: WidgetId[] };

export function defaultState(): DashboardState {
  return { layout: DEFAULT_LAYOUT.map((item) => ({ ...item })), hidden: [] };
}

/** Прочитать сохранённую раскладку; на любой мусор — вернуться к умолчанию. */
export function loadState(): DashboardState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultState();
    const saved = JSON.parse(raw) as Partial<DashboardState>;
    const known = new Set<string>(WIDGETS.map((widget) => widget.id));
    const layout = (saved.layout ?? []).filter((item) => known.has(item.i));
    // Виджет, добавленный новой версией, подставляется из умолчания —
    // иначе после обновления он просто не появился бы на дашборде.
    const placed = new Set(layout.map((item) => item.i));
    const missing = DEFAULT_LAYOUT.filter((item) => !placed.has(item.i));
    return {
      layout: [...layout, ...missing.map((item) => ({ ...item }))],
      hidden: (saved.hidden ?? []).filter((id): id is WidgetId => known.has(id)),
    };
  } catch {
    return defaultState();
  }
}

export function saveState(state: DashboardState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Приватный режим или переполненное хранилище — расположение просто не запомнится.
  }
}
