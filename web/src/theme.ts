// The colour theme. Like the language and the layout, this is the browser's business:
// the choice lives in localStorage and the server knows nothing about it. Until a theme is
// picked by hand, it follows the system one and switches together with it.
//
// The choice is a theme id or the word "system", and next to it two more keys remember the
// last dark and the last light theme: that is what "follow the system" then switches between,
// the way VS Code does it with a preferred dark and a preferred light theme. Picking Dracula
// and Catppuccin Latte once is enough for the system mode to swing between exactly those two.

import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";

import {
  DEFAULT_THEME,
  applyTheme,
  themeById,
  type Theme,
  type ThemeKind,
} from "./themes";

export const SYSTEM = "system";

const STORAGE_KEY = "cburn.theme";
const RENAMED_KEY = "cloudo-dash.theme"; // the key of the former project name
const PREFERRED_KEY: Record<ThemeKind, string> = {
  dark: "cburn.theme.dark",
  light: "cburn.theme.light",
};

function systemKind(): ThemeKind {
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function remember(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // private mode - the choice simply will not be remembered
  }
}

/** The old key held "dark" / "light" - the same pair the system mode now switches between. */
function storedChoice(): string {
  const saved = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(RENAMED_KEY);
  if (saved === "dark" || saved === "light") return DEFAULT_THEME[saved];
  return saved && themeById(saved) ? saved : SYSTEM;
}

function storedPreferred(): Record<ThemeKind, string> {
  const pick = (kind: ThemeKind) => {
    const saved = localStorage.getItem(PREFERRED_KEY[kind]);
    return saved && themeById(saved)?.kind === kind ? saved : DEFAULT_THEME[kind];
  };
  return { dark: pick("dark"), light: pick("light") };
}

export type ThemeState = {
  /** A theme id or `SYSTEM` - what the picker shows as chosen. */
  choice: string;
  /** The theme currently on the screen: the preview one while the cursor walks the list. */
  theme: Theme;
  /** The theme the system mode resolves to right now - the preview for the "system" row. */
  system: Theme;
  setTheme: (choice: string) => void;
  /** Shows a theme without choosing it; `null` returns to the chosen one. */
  preview: (theme: Theme | null) => void;
};

export function useTheme(): ThemeState {
  const [choice, setChoice] = useState<string>(storedChoice);
  const [preferred, setPreferred] = useState<Record<ThemeKind, string>>(storedPreferred);
  const [kind, setKind] = useState<ThemeKind>(systemKind);
  const [preview, setPreview] = useState<Theme | null>(null);

  const system = useMemo(
    () => themeById(preferred[kind]) ?? themeById(DEFAULT_THEME[kind])!,
    [preferred, kind],
  );
  const chosen = useMemo(
    () => (choice === SYSTEM ? system : (themeById(choice) ?? system)),
    [choice, system],
  );
  const shown = preview ?? chosen;

  // A layout effect rather than a plain one: the palette lands on `<html>` before the paint,
  // so a reload does not flash the previous theme.
  useLayoutEffect(() => applyTheme(shown), [shown]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setKind(systemKind());
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((next: string) => {
    remember(STORAGE_KEY, next);
    const theme = themeById(next);
    if (theme) {
      remember(PREFERRED_KEY[theme.kind], theme.id);
      setPreferred((previous) => ({ ...previous, [theme.kind]: theme.id }));
    }
    setPreview(null);
    setChoice(next);
  }, []);

  return { choice, theme: shown, system, setTheme, preview: setPreview };
}
