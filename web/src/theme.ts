// Тема оформления. Как язык и раскладка, это дело браузера: выбор живёт
// в localStorage, а сервер про него не знает. Пока выбора не сделали, тема
// следует за системной и переключается вместе с ней.

import { useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "cloudo-dash.theme";

function system(): Theme {
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function stored(): Theme | null {
  const saved = localStorage.getItem(STORAGE_KEY);
  return saved === "dark" || saved === "light" ? saved : null;
}

export function useTheme(): { theme: Theme; setTheme: (next: Theme) => void } {
  const [theme, setTheme] = useState<Theme>(() => stored() ?? system());

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Пока тему не выбрали руками, она едет за системной: сменился режим
  // в macOS — сменился и дашборд, без перезагрузки.
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
      // приватный режим — выбор просто не запомнится
    }
    setTheme(next);
  };

  return { theme, setTheme: choose };
}
