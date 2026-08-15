// Раскладка дашборда: что где лежит, что скрыто. Хранится в localStorage —
// сервер про это не знает, расположение виджетов дело сугубо local-first.

import type { Layout } from "react-grid-layout";

export const STORAGE_KEY = "cburn.layout.v3";

/** Ключи прежнего имени проекта: сетка та же, читается как есть. */
const RENAMED_KEY = "cloudo-dash.layout.v3";

/** Ключ прежней, вдвое более крупной сетки — из него переносятся раскладки. */
const LEGACY_KEY = "cloudo-dash.layout.v2";

/** Сколько колонок в сетке и какой высоты одна строка (px).
 *
 * Шаг сетки — это `ROW_HEIGHT + MARGIN[1]` по вертикали и доля ширины по
 * горизонтали, поэтому «мельче вдвое» — это вдвое больше колонок и вдвое
 * меньше и высота строки, и промежуток. */
export const COLUMNS = 24;
export const ROW_HEIGHT = 13;
export const MARGIN: [number, number] = [8, 8];

export type WidgetId =
  | "gauge"
  | "today"
  | "live"
  | "leaders"
  | "tools"
  | "models"
  | "idle"
  | "plan"
  | "otel"
  | "feed";

/** Порядок в списке настроек — сверху вниз по важности. Названия и пояснения
 *  берутся из словаря по ключам `widget.<id>` и `widget.<id>.note`. */
export const WIDGETS: WidgetId[] = [
  "gauge",
  "today",
  "live",
  "plan",
  "leaders",
  "tools",
  "models",
  "idle",
  "otel",
  "feed",
];

/** Раскладка по умолчанию — та же, что была до перетаскивания. */
//: Высоты подобраны по фактической высоте содержимого (h = (px + 8) / 21),
//: чтобы при первом открытии не было ни пустоты, ни скролла.
export const DEFAULT_LAYOUT: Layout[] = [
  { i: "gauge", x: 0, y: 0, w: 14, h: 38, minW: 8, minH: 24 },
  { i: "today", x: 14, y: 0, w: 10, h: 16, minW: 6, minH: 8 },
  { i: "live", x: 14, y: 16, w: 10, h: 18, minW: 6, minH: 10 },
  { i: "plan", x: 14, y: 34, w: 10, h: 16, minW: 6, minH: 10 },
  { i: "tools", x: 0, y: 38, w: 14, h: 18, minW: 6, minH: 10 },
  { i: "leaders", x: 14, y: 50, w: 10, h: 12, minW: 6, minH: 8 },
  { i: "models", x: 0, y: 56, w: 8, h: 6, minW: 6, minH: 6 },
  { i: "idle", x: 8, y: 56, w: 6, h: 10, minW: 4, minH: 6 },
  { i: "feed", x: 0, y: 62, w: 24, h: 42, minW: 8, minH: 10 },
  { i: "otel", x: 0, y: 104, w: 12, h: 18, minW: 6, minH: 10 },
];

export type DashboardState = { layout: Layout[]; hidden: WidgetId[] };

export function defaultState(): DashboardState {
  return { layout: DEFAULT_LAYOUT.map((item) => ({ ...item })), hidden: [] };
}

/** Раскладка со старой, вдвое более крупной сетки: те же места, новые единицы. */
function upscale(layout: Layout[]): Layout[] {
  return layout.map((item) => ({
    ...item,
    x: item.x * 2,
    y: item.y * 2,
    w: item.w * 2,
    h: item.h * 2,
    minW: item.minW === undefined ? undefined : item.minW * 2,
    minH: item.minH === undefined ? undefined : item.minH * 2,
  }));
}

/** Прочитать сохранённую раскладку; на любой мусор — вернуться к умолчанию. */
export function loadState(): DashboardState {
  try {
    const current = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(RENAMED_KEY);
    // Настроенную раскладку не теряем при смене шага — переносим со старого ключа.
    const legacy = current ? null : localStorage.getItem(LEGACY_KEY);
    const raw = current ?? legacy;
    if (!raw) return defaultState();
    const saved = JSON.parse(raw) as Partial<DashboardState>;
    const known = new Set<string>(WIDGETS);
    const stored = (saved.layout ?? []).filter((item) => known.has(item.i));
    const layout = legacy ? upscale(stored) : stored;
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
