// The dashboard grid: widgets are dragged by their header, resized by the corner,
// hidden with the cross. All of that lives in localStorage.

import { useCallback, useEffect, useMemo, useState } from "react";
import GridLayout, { type Layout } from "react-grid-layout";

import { clockTime, freshnessLabel } from "./format";
import { useLang } from "./i18n";
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

export type WidgetContent = {
  id: WidgetId;
  title: string;
  body: React.ReactNode;
  /** The time of the last event in the widget data (ms); null means there were no events. */
  at: number | null;
  /** When this data was last recomputed (ms). */
  checkedAt: number;
  /** After how many seconds of silence the mark counts as stale. Set only
   *  where the pause is explained not by quiet work but by a failed refresh. */
  staleAfter?: number;
  /** Refresh exactly this data, without waiting for the tick. */
  refresh: () => Promise<void>;
  /** The widget's own toggles - in the header, to the left of the timestamp. */
  tools?: React.ReactNode;
};

/** The grid width in pixels: react-grid-layout cannot compute it itself. */
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
  const { t } = useLang();
  const [state, setState] = useState(loadState);
  const [tuning, setTuning] = useState(false);
  const [width, ref] = useWidth();

  useEffect(() => saveState(state), [state]);

  const hidden = useMemo(() => new Set(state.hidden), [state.hidden]);
  const visible = widgets.filter((widget) => !hidden.has(widget.id));
  const layout = state.layout.filter((item) => !hidden.has(item.i as WidgetId));

  const onLayoutChange = useCallback((next: Layout[]) => {
    setState((current) => {
      // Hidden widgets do not arrive in next - we keep their previous places,
      // so that on return a widget lands exactly where it was taken from.
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
          {t("dash.widgets")}
          {state.hidden.length > 0 && <span className="tune-count">−{state.hidden.length}</span>}
        </button>
        {tuning && (
          <div className="tune-panel" role="dialog" aria-label={t("dash.tune")}>
            <p className="tune-hint">{t("dash.tuneHint")}</p>
            <ul>
              {WIDGETS.map((id) => (
                <li key={id}>
                  <label>
                    <input type="checkbox" checked={!hidden.has(id)} onChange={() => toggle(id)} />
                    <span className="tune-title">{t(`widget.${id}`)}</span>
                    <span className="tune-note">{t(`widget.${id}.note`)}</span>
                  </label>
                </li>
              ))}
            </ul>
            <button className="tune-reset" onClick={() => setState(defaultState())}>
              {t("dash.reset")}
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
          draggableCancel=".widget-tools"
          onLayoutChange={onLayoutChange}
          resizeHandles={["se"]}
          compactType="vertical"
        >
          {visible.map((widget) => (
            <section key={widget.id} className="panel widget">
              <WidgetHead widget={widget} onHide={() => toggle(widget.id)} />
              <div className="widget-body">{widget.body}</div>
            </section>
          ))}
        </GridLayout>
      </div>
    </>
  );
}

/** The widget header: the name, what time the data is for and refresh on hover. */
function WidgetHead({ widget, onHide }: { widget: WidgetContent; onHide: () => void }) {
  const { t } = useLang();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const now = Date.now();
  const stale =
    widget.staleAfter !== undefined &&
    widget.at !== null &&
    now - widget.at > widget.staleAfter * 1000;

  const refresh = async () => {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      await widget.refresh();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <header className="widget-grip">
      <h2>{widget.title}</h2>
      <div className="widget-tools">
        {widget.tools}
        {widget.at === null ? (
          <span
            className="widget-at widget-at-none"
            title={freshnessLabel(null, widget.checkedAt, now)}
          >
            —
          </span>
        ) : (
          <time
            className={stale ? "widget-at widget-at-stale" : "widget-at"}
            dateTime={new Date(widget.at).toISOString()}
            title={freshnessLabel(widget.at, widget.checkedAt, now)}
          >
            {clockTime(widget.at)}
          </time>
        )}
        <button
          className={busy ? "widget-refresh widget-refresh-busy" : "widget-refresh"}
          aria-label={t("dash.refreshWidget", { title: widget.title })}
          title={failed ? t("dash.refreshFailed") : t("dash.refresh")}
          onClick={refresh}
        >
          <RefreshIcon />
        </button>
        <button
          className="widget-hide"
          aria-label={t("dash.hideWidget", { title: widget.title })}
          onClick={onHide}
        >
          ×
        </button>
      </div>
    </header>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">
      <path
        d="M13.2 8a5.2 5.2 0 1 1-1.6-3.75"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path
        d="M13.2 1.9v3.2H10"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
