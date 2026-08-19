// The dashboard layout: what sits where, what is hidden. Kept in localStorage -
// the server knows nothing about it, widget placement is strictly local-first.

import type { Layout } from "react-grid-layout";

export const STORAGE_KEY = "cburn.layout.v3";

/** Keys of the former project name: the grid is the same, read as it is. */
const RENAMED_KEY = "cloudo-dash.layout.v3";

/** The key of the former, twice coarser grid - layouts are carried over from it. */
const LEGACY_KEY = "cloudo-dash.layout.v2";

/** How many columns the grid has and how tall one row is (px).
 *
 * The grid step is `ROW_HEIGHT + MARGIN[1]` vertically and a share of the width
 * horizontally, so "twice finer" means twice as many columns and half the row
 * height and gap. */
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

/** The order in the settings list - top down by importance. Names and notes
 *  come from the dictionary under the keys `widget.<id>` and `widget.<id>.note`. */
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

/** The default layout - the same one that existed before dragging. */
//: The heights are picked from the actual content height (h = (px + 8) / 21),
//: so that the first open shows neither emptiness nor a scrollbar.
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

/** `sized` lists widgets whose height the user has set by hand: those keep it,
 *  the rest follow their content. */
export type DashboardState = { layout: Layout[]; hidden: WidgetId[]; sized: WidgetId[] };

export function defaultState(): DashboardState {
  return { layout: DEFAULT_LAYOUT.map((item) => ({ ...item })), hidden: [], sized: [] };
}

/** grid rows for a pixel height: an item spans h*ROW_HEIGHT + (h-1)*MARGIN px */
export function rowsForPixels(px: number): number {
  return Math.max(1, Math.ceil((px + MARGIN[1]) / (ROW_HEIGHT + MARGIN[1])));
}

/** A layout from the old, twice coarser grid: the same places, new units. */
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

/** Read the saved layout; on any garbage - fall back to the default. */
export function loadState(): DashboardState {
  try {
    const current = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(RENAMED_KEY);
    // A configured layout is not lost when the step changes - we carry it from the old key.
    const legacy = current ? null : localStorage.getItem(LEGACY_KEY);
    const raw = current ?? legacy;
    if (!raw) return defaultState();
    const saved = JSON.parse(raw) as Partial<DashboardState>;
    const known = new Set<string>(WIDGETS);
    const stored = (saved.layout ?? []).filter((item) => known.has(item.i));
    const layout = legacy ? upscale(stored) : stored;
    // A widget added by a new version is filled in from the default -
    // otherwise after an update it simply would not appear on the dashboard.
    const placed = new Set(layout.map((item) => item.i));
    const missing = DEFAULT_LAYOUT.filter((item) => !placed.has(item.i));
    return {
      layout: [...layout, ...missing.map((item) => ({ ...item }))],
      hidden: (saved.hidden ?? []).filter((id): id is WidgetId => known.has(id)),
      sized: (saved.sized ?? []).filter((id): id is WidgetId => known.has(id)),
    };
  } catch {
    return defaultState();
  }
}

export function saveState(state: DashboardState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Private mode or a full storage - the placement simply will not be remembered.
  }
}
