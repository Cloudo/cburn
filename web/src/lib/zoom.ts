// The interface scale. Like the language, the theme and the layout, this is the browser's
// business: the choice lives in localStorage and the server knows nothing about it.
// The webview of M5 has no zoom hotkeys of its own (and the dashboard is drawn in pixels,
// not in `rem`), so the scale is set through the CSS `zoom` of the root element - it
// re-lays the page out instead of stretching a picture, and the widget grid keeps
// measuring itself in its own pixels.

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "cburn.zoom";

//: The rungs of the ladder rather than a free number: a step is always visible, and the
//: value cannot drift into an unreadable 0.37 from a hurried keypress.
export const STEPS = [0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2] as const;

const NORMAL = STEPS.indexOf(1);

function stored(): number {
  const saved = Number(localStorage.getItem(STORAGE_KEY));
  return STEPS.includes(saved as (typeof STEPS)[number]) ? saved : 1;
}

export function useZoom(): {
  zoom: number;
  zoomIn: () => void;
  zoomOut: () => void;
  reset: () => void;
} {
  const [zoom, setZoom] = useState<number>(() => {
    try {
      return stored();
    } catch {
      return 1; // private mode - the scale is simply not remembered
    }
  });

  useEffect(() => {
    document.documentElement.style.setProperty("zoom", String(zoom));
    try {
      localStorage.setItem(STORAGE_KEY, String(zoom));
    } catch {
      // private mode - the choice simply will not be remembered
    }
  }, [zoom]);

  const shift = useCallback((delta: number) => {
    setZoom((current) => {
      const index = STEPS.indexOf(current as (typeof STEPS)[number]);
      const next = Math.min(STEPS.length - 1, Math.max(0, (index < 0 ? NORMAL : index) + delta));
      return STEPS[next];
    });
  }, []);

  const zoomIn = useCallback(() => shift(1), [shift]);
  const zoomOut = useCallback(() => shift(-1), [shift]);
  const reset = useCallback(() => setZoom(1), []);

  // Ctrl/Cmd with a plus, a minus or a zero - the habit of every application. The numpad
  // sends the same characters under other codes, hence the pair of checks.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key;
      const code = event.code;
      if (key === "+" || key === "=" || code === "NumpadAdd") shift(1);
      else if (key === "-" || key === "_" || code === "NumpadSubtract") shift(-1);
      else if (key === "0" || code === "Numpad0") setZoom(1);
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shift]);

  return { zoom, zoomIn, zoomOut, reset };
}
