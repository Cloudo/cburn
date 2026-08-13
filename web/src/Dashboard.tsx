// Сетка дашборда: виджеты перетаскиваются за заголовок, тянутся за угол,
// прячутся крестиком. Всё это живёт в localStorage.

import { useCallback, useEffect, useMemo, useState } from "react";
import GridLayout, { type Layout } from "react-grid-layout";

import {
  COLUMNS,
  MARGIN,
  ROW_HEIGHT,
  WIDGETS,
  defaultState,
  loadState,
  saveState,
  type WidgetId,
} from "./layout";

export type WidgetContent = { id: WidgetId; title: string; body: React.ReactNode };

/** Ширина сетки в пикселях: react-grid-layout не умеет считать её сам. */
function useWidth(): [number, (node: HTMLDivElement | null) => void] {
  const [width, setWidth] = useState(1180);
  const [node, setNode] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);

  return [width, setNode];
}

export function Dashboard({ widgets }: { widgets: WidgetContent[] }) {
  const [state, setState] = useState(loadState);
  const [tuning, setTuning] = useState(false);
  const [width, ref] = useWidth();

  useEffect(() => saveState(state), [state]);

  const hidden = useMemo(() => new Set(state.hidden), [state.hidden]);
  const visible = widgets.filter((widget) => !hidden.has(widget.id));
  const layout = state.layout.filter((item) => !hidden.has(item.i as WidgetId));

  const onLayoutChange = useCallback((next: Layout[]) => {
    setState((current) => {
      // Скрытые виджеты в next не приходят — сохраняем их прежние места,
      // чтобы при возврате виджет встал туда же, откуда его убрали.
      const moved = new Map(next.map((item) => [item.i, item]));
      const merged = current.layout.map((item) => moved.get(item.i) ?? item);
      const known = new Set(merged.map((item) => item.i));
      const added = next.filter((item) => !known.has(item.i));
      return { ...current, layout: [...merged, ...added] };
    });
  }, []);

  const toggle = (id: WidgetId) =>
    setState((current) => ({
      ...current,
      hidden: current.hidden.includes(id)
        ? current.hidden.filter((other) => other !== id)
        : [...current.hidden, id],
    }));

  return (
    <>
      <div className="dashboard-bar">
        <button className="tune" onClick={() => setTuning((open) => !open)} aria-expanded={tuning}>
          виджеты
          {state.hidden.length > 0 && <span className="tune-count">−{state.hidden.length}</span>}
        </button>
        {tuning && (
          <div className="tune-panel" role="dialog" aria-label="настройка дашборда">
            <p className="tune-hint">
              Перетаскивать за заголовок, размер — за правый нижний угол. Расположение
              сохраняется в браузере.
            </p>
            <ul>
              {WIDGETS.map((widget) => (
                <li key={widget.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={!hidden.has(widget.id)}
                      onChange={() => toggle(widget.id)}
                    />
                    <span className="tune-title">{widget.title}</span>
                    <span className="tune-note">{widget.note}</span>
                  </label>
                </li>
              ))}
            </ul>
            <button className="tune-reset" onClick={() => setState(defaultState())}>
              вернуть как было
            </button>
          </div>
        )}
      </div>

      <div className="grid-host" ref={ref}>
        <GridLayout
          className="grid"
          layout={layout}
          cols={COLUMNS}
          rowHeight={ROW_HEIGHT}
          margin={MARGIN}
          width={width}
          draggableHandle=".widget-grip"
          onLayoutChange={onLayoutChange}
          resizeHandles={["se"]}
          compactType="vertical"
        >
          {visible.map((widget) => (
            <section key={widget.id} className="panel widget">
              <header className="widget-grip">
                <h2>{widget.title}</h2>
                <button
                  className="widget-hide"
                  aria-label={`скрыть виджет «${widget.title}»`}
                  onClick={() => toggle(widget.id)}
                >
                  ×
                </button>
              </header>
              <div className="widget-body">{widget.body}</div>
            </section>
          ))}
        </GridLayout>
      </div>
    </>
  );
}
