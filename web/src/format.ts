// Числа на приборе читаются на бегу, поэтому крупные округляются до трёх
// значащих цифр, а точные значения остаются в подписях.

import { useEffect, useRef, useState } from "react";

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
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
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

/** Значение, догоняющее цель за то же время, что и стрелка. */
export function useSmoothNumber(target: number, duration = 900): number {
  const [shown, setShown] = useState(target);
  const from = useRef(target);
  const started = useRef(0);
  const frame = useRef(0);

  useEffect(() => {
    from.current = shown;
    started.current = performance.now();
    cancelAnimationFrame(frame.current);

    const step = (now: number) => {
      const t = Math.min((now - started.current) / duration, 1);
      // Та же кривая, что у стрелки: цифра и стрелка приходят одновременно.
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(from.current + (target - from.current) * eased);
      if (t < 1) frame.current = requestAnimationFrame(step);
    };

    frame.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame.current);
    // shown намеренно не в зависимостях: иначе анимация перезапускалась бы на
    // каждом кадре и не доезжала до цели.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration]);

  return shown;
}
