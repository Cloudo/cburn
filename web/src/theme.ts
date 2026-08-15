// The colour theme. Like the language and the layout, this is the browser's business:
// the choice lives in localStorage and the server knows nothing about it. Until a choice
// is made, the theme follows the system one and switches together with it.

import { useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "cburn.theme";
const RENAMED_KEY = "cloudo-dash.theme"; // the key of the former project name

function system(): Theme {
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function stored(): Theme | null {
  const saved = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(RENAMED_KEY);
  return saved === "dark" || saved === "light" ? saved : null;
}

export function useTheme(): { theme: Theme; setTheme: (next: Theme) => void } {
  const [theme, setTheme] = useState<Theme>(() => stored() ?? system());

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Until the theme is chosen by hand, it rides the system one: the macOS mode
  // changed - the dashboard changed too, without a reload.
  useEffect(() => {
    if (stored()) return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setTheme(system());
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  const choose = (next: Theme) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // private mode - the choice simply will not be remembered
    }
    setTheme(next);
  };

  return { theme, setTheme: choose };
}
