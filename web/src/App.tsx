import { useEffect, useState } from "react";

import { Gauge, OutputMeter, Recorder, type Slice } from "./Gauge";
import { Idle, Models, PlanLimits, Telemetry, Tools } from "./Profile";
import { Dashboard, type WidgetContent } from "./Dashboard";
import { Advice } from "./Advice";
import { Session } from "./Session";
import { Sessions } from "./Sessions";
import { Settings } from "./Settings";
import { useLang } from "./i18n";
import { ThemePicker } from "./ThemePicker";
import { useZoom } from "./zoom";
import {
  agoLabel,
  clockTime,
  compact,
  duration,
  grouped,
  modelLabel,
  share as percent,
  sinceLabel,
  timestamp,
  toolLabel,
  usd,
} from "./format";
import {
  closeSession,
  hideSession,
  refreshPlan,
  useOverview,
  type LiveSession,
  type Overview,
  type SessionStatus,
  type Stamps,
  type Turn,
  type Usage,
} from "./api";

const WINDOWS = ["10s", "1m", "5m", "60m"] as const;

//: Statuses in order of importance: the tab that opens first is the one where something happens.
const STATUSES: SessionStatus[] = ["permission", "working", "answered", "idle", "done"];

// The slices take the accents from the theme rather than from a copy of their values: the
// palette is switched by the picker, and a duplicated hex would stay at the old theme.
const COLORS = {
  cacheRead: "var(--steel)",
  cacheWrite: "var(--indigo)",
  output: "var(--amber)",
  input: "var(--mint)",
};

function slicesOf(source: Usage, t: (key: string) => string): Slice[] {
  return [
    {
      key: "cache_read",
      label: t("slice.cache_read"),
      value: source.cache_read,
      color: COLORS.cacheRead,
    },
    {
      key: "cache_write",
      label: t("slice.cache_write"),
      value: source.cache_write,
      color: COLORS.cacheWrite,
    },
    { key: "output", label: t("slice.output"), value: source.output_tokens, color: COLORS.output },
    { key: "input", label: t("slice.input"), value: source.input_tokens, color: COLORS.input },
  ];
}

/** The screen is chosen by the hash: #/sessions. A router for two pages is overkill, but
 *  the address must survive a reload and live in bookmarks. */
function useScreen(): string {
  const [screen, setScreen] = useState(() => window.location.hash.replace(/^#\/?/, ""));
  useEffect(() => {
    const onHash = () => setScreen(window.location.hash.replace(/^#\/?/, ""));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return screen;
}

const SCREENS = ["", "sessions", "advice", "settings"];

export default function App() {
  const { lang, setLang, t } = useLang();
  const { zoom, zoomIn, zoomOut, reset: resetZoom } = useZoom();
  const { data, connection, updatedAt, refresh } = useOverview();
  const [, tick] = useState(0);
  const [burnWindow, setBurnWindow] = useState<string>("1m");
  const screen = useScreen();

  useEffect(() => {
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const ago = updatedAt ? (Date.now() - updatedAt) / 1000 : 0;

  return (
    <main>
      <header className="masthead">
        <div className="brand">
          <span className="brand-name">cburn</span>
          <nav className="screens">
            {SCREENS.map((key) => (
              <a
                key={key}
                href={`#/${key}`}
                className={key === screen ? "screen-link screen-link-on" : "screen-link"}
              >
                {t(`app.screen.${key || "overview"}`)}
              </a>
            ))}
          </nav>
        </div>
        <div className="status">
          {data && data.pending_sessions.length > 0 && (
            <span className="working">
              <span className="working-pulse" />
              {t("app.working")}
            </span>
          )}
          <span className={`dot dot-${connection}`} />
          <span>{connection === "live" ? t("app.live") : t("app.offline")}</span>
          <span className="status-ago">{updatedAt ? agoLabel(ago) : ""}</span>
          <ThemePicker />
          <div className="lang-picker" role="group" aria-label={t("app.lang")}>
            {(["ru", "en"] as const).map((key) => (
              <button
                key={key}
                aria-pressed={key === lang}
                className={key === lang ? "lang lang-on" : "lang"}
                onClick={() => setLang(key)}
              >
                {key.toUpperCase()}
              </button>
            ))}
          </div>
          <div className="zoom-picker" role="group" aria-label={t("app.zoom")}>
            <button className="zoom" title={t("app.zoom.out")} onClick={zoomOut}>
              &minus;
            </button>
            <button
              className={zoom === 1 ? "zoom zoom-level" : "zoom zoom-level zoom-on"}
              title={t("app.zoom.reset")}
              onClick={resetZoom}
            >
              {Math.round(zoom * 100)}%
            </button>
            <button className="zoom" title={t("app.zoom.in")} onClick={zoomIn}>
              +
            </button>
          </div>
        </div>
      </header>

      {screen === "advice" ? (
        <Advice />
      ) : screen === "settings" ? (
        <Settings />
      ) : screen.startsWith("session/") ? (
        <Session id={screen.slice("session/".length)} />
      ) : screen === "sessions" ? (
        <Sessions />
      ) : data ? (
        <Dashboard widgets={buildWidgets(data, refresh, burnWindow, setBurnWindow, t)} />
      ) : (
        <p className="empty-note">
          {connection === "offline" ? t("app.noConnection") : t("app.connecting")}
        </p>
      )}
    </main>
  );
}

//: An overview from a server that knows nothing about timestamps: in the pair "frontend
//: from web/dist + a long-running process" that happens, and the whole dashboard must
//: not fall over because of it.
const NO_STAMPS: Stamps = {
  last_turn: null,
  today_turn: null,
  tool_call: null,
  idle_turn: null,
};

//: How long the subscription limits may stay quiet before the mark turns alarming.
//: The usual tick is five minutes, so a gap twice that means not quiet work
//: but failed requests to Anthropic.
const PLAN_STALE_SECONDS = 600;

/** The widget contents. The layout and visibility are held by Dashboard.
 *
 * Every widget has its own time of last data: the overview is recomputed every
 * second, but events appear in it only when something happens. The daily widgets
 * stand on the last turn since midnight, the feed on the last turn of all,
 * the subscription limits on the answer from Anthropic. */
function buildWidgets(
  data: Overview,
  refresh: () => Promise<void>,
  burnWindow: string,
  setBurnWindow: (key: string) => void,
  t: (key: string, vars?: Record<string, string | number>) => string,
): WidgetContent[] {
  const checkedAt = timestamp(data.now) ?? Date.now();
  const stamps = data.stamps ?? NO_STAMPS;
  const lastTurn = timestamp(stamps.last_turn);
  const todayTurn = timestamp(stamps.today_turn);
  const planAt = data.plan.fetched_at === null ? null : data.plan.fetched_at * 1000;
  const liveAt = timestamp(data.live_sessions[0]?.last_at ?? null);
  const refreshPlanLimits = async () => {
    await refreshPlan();
    await refresh();
  };
  return [
    {
      id: "gauge",
      title: t("widget.gauge"),
      body: <GaugeWidget data={data} window={burnWindow} />,
      tools: <WindowPicker value={burnWindow} onChange={setBurnWindow} />,
      at: lastTurn,
      checkedAt,
      refresh,
    },
    {
      id: "today",
      title: t("widget.today"),
      body: <TodayWidget data={data} />,
      at: todayTurn,
      checkedAt,
      refresh,
    },
    {
      id: "live",
      title: t("widget.live"),
      body: <SessionBoard sessions={data.live_sessions} limit={data.live_limit} now={data.now} />,
      at: liveAt,
      checkedAt,
      refresh,
    },
    {
      id: "plan",
      title: t("widget.plan"),
      body: <PlanLimits plan={data.plan} />,
      at: planAt,
      checkedAt,
      staleAfter: PLAN_STALE_SECONDS,
      refresh: refreshPlanLimits,
    },
    {
      id: "leaders",
      title: t("widget.leaders"),
      body: <LeadersWidget data={data} />,
      at: todayTurn,
      checkedAt,
      refresh,
    },
    {
      id: "tools",
      title: t("widget.tools"),
      body: <Tools profile={data.tools} />,
      at: timestamp(stamps.tool_call),
      checkedAt,
      refresh,
    },
    {
      id: "models",
      title: t("widget.models"),
      body: <Models models={data.models} />,
      at: todayTurn,
      checkedAt,
      refresh,
    },
    {
      id: "idle",
      title: t("widget.idle"),
      body: <Idle idle={data.idle} />,
      at: timestamp(stamps.idle_turn),
      checkedAt,
      refresh,
    },
    {
      id: "otel",
      title: t("widget.otel"),
      body: <Telemetry otel={data.otel} />,
      at: timestamp(data.otel?.last_at ?? null),
      checkedAt,
      refresh,
    },
    {
      id: "feed",
      title: t("widget.feed"),
      body: <FeedWidget data={data} />,
      at: timestamp(data.recent_turns[0]?.ts ?? null),
      checkedAt,
      refresh,
    },
  ];
}

/** The averaging window sits in the widget header, in the same tone as the clock: it is
 *  changed rarely, and in the instrument body it used to take a whole line. */
function WindowPicker({ value, onChange }: { value: string; onChange: (key: string) => void }) {
  const { t } = useLang();
  return (
    <div className="window-picker" role="tablist" aria-label={t("window.picker")}>
      {WINDOWS.map((key) => (
        <button
          key={key}
          role="tab"
          aria-selected={key === value}
          title={t(`window.${key}`)}
          className={key === value ? "window window-on" : "window"}
          onClick={() => onChange(key)}
        >
          {t(`window.short.${key}`)}
        </button>
      ))}
    </div>
  );
}

function GaugeWidget({ data, window }: { data: Overview; window: string }) {
  const { t } = useLang();
  const burn = data.burn[window];
  // The breakdown always covers the same window as the needle: two different periods
  // side by side read as one whole and confused.
  const slices = slicesOf(burn.usage, t);
  const peak = Math.max(...WINDOWS.map((key) => data.burn[key].output_per_min), 500);
  const total = slices.reduce((sum, item) => sum + item.value, 0);

  return (
    <>
      {/* The breakdown sits beside the instrument: empty space is left along the sides
          of the semicircle, and a list of four rows fits into it exactly. */}
      <div className="gauge-row">
        <Gauge value={burn.tokens_per_min} slices={slices} caption={t(`window.caption.${window}`)} />

        <div className="breakdown">
          <span className="legend-title">{t("widget.gauge.breakdown")}</span>
          <ul className="legend">
            {slices.map((slice) => {
              const share = total > 0 ? (slice.value / total) * 100 : 0;
              return (
                <li key={slice.key}>
                  <span className="legend-swatch" style={{ background: slice.color }} />
                  <span className="legend-label">{slice.label}</span>
                  <span className="legend-share">{share < 1 ? "<1" : Math.round(share)}%</span>
                  <span className="legend-value">{compact(slice.value)}</span>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      <OutputMeter value={burn.output_per_min} peak={peak} />
      <Recorder series={data.series} bucketSeconds={data.series_bucket_seconds} />
    </>
  );
}

function TodayWidget({ data }: { data: Overview }) {
  const { t } = useLang();
  const advisor = data.advisor;
  return (
    <>
      <dl className="today">
        <div>
          <dt>{t("today.turns")}</dt>
          <dd>{grouped(data.today.turns)}</dd>
        </div>
        <div>
          <dt>{t("today.output")}</dt>
          <dd>{grouped(data.today.output_tokens)}</dd>
        </div>
        <div>
          <dt>{t("today.cacheRead")}</dt>
          <dd>{grouped(data.today.cache_read)}</dd>
        </div>
        <div>
          <dt>{t("today.cacheWrite")}</dt>
          <dd>{grouped(data.today.cache_write)}</dd>
        </div>
        {/* The rate is the subscription one: this is not a bill but "what it would cost over the API" (TZ §4). */}
        <div className="today-wide">
          <dt>{t("today.cost")}</dt>
          <dd>{usd(data.today.cost_usd)}</dd>
        </div>
      </dl>
      {/* Our own spend next to everyone else's: an instrument that costs more than it
          saves is a bad instrument (task C4). */}
      {advisor !== undefined && advisor.ticks > 0 && (
        <p className="hint">
          {t("today.advisor", {
            cost: usd(advisor.cost_usd),
            ticks: advisor.ticks,
            share: percent(advisor.cost_usd / (data.today.cost_usd || advisor.cost_usd)),
          })}
        </p>
      )}
      <p className="hint">
        {t("today.totals", {
          turns: grouped(data.totals.turns),
          sessions: data.totals.sessions,
          projects: data.totals.projects,
        })}
      </p>
    </>
  );
}

function LeadersWidget({ data }: { data: Overview }) {
  const { t } = useLang();
  if (data.top_sessions.length === 0) {
    return <p className="hint">{t("leaders.empty")}</p>;
  }
  return (
    <ol className="leaders">
      {data.top_sessions.map((session) => (
        <li key={session.id}>
          <div className="leaders-bar">
            <span
              className="leaders-fill"
              style={{
                width: `${(session.tokens / data.top_sessions[0].tokens) * 100}%`,
              }}
            />
          </div>
          <code>{session.id.slice(0, 8)}</code>
          <span className="leaders-project">{session.project ?? "—"}</span>
          <span className="leaders-tokens">{compact(session.tokens)}</span>
        </li>
      ))}
    </ol>
  );
}

function FeedWidget({ data }: { data: Overview }) {
  const { t } = useLang();
  return (
    <>
      {/* The classes are the same as on the rows: on narrow screens columns hide by them,
          otherwise the captions would drift away from the values. */}
      <div className="turn turn-head" aria-hidden="true">
        <span>{t("feed.time")}</span>
        <span className="turn-model">{t("feed.model")}</span>
        <span className="turn-project">{t("feed.project")}</span>
        <span className="turn-output">{t("feed.output")}</span>
        <span className="turn-context">{t("feed.context")}</span>
        <span className="turn-tools">{t("feed.tools")}</span>
      </div>
      <ol>
        {data.recent_turns.map((turn) => (
          <TurnRow key={turn.message_id} turn={turn} />
        ))}
      </ol>
    </>
  );
}

function SessionBoard({
  sessions,
  limit,
  now,
}: {
  sessions: LiveSession[];
  limit: number;
  now: string;
}) {
  const { t } = useLang();
  const [chosen, setChosen] = useState<SessionStatus | null>(null);
  const counts = STATUSES.map((key) => ({
    key,
    items: sessions.filter((session) => session.status === key),
  }));

  // Until a tab is chosen by hand, the first non-empty one is open: staring at an
  // empty "waiting for permission" list is pointless.
  const active = chosen ?? counts.find((status) => status.items.length > 0)?.key ?? "working";
  const shown = counts.find((status) => status.key === active)?.items ?? [];

  if (sessions.length === 0) {
    return <p className="hint">{t("live.empty")}</p>;
  }

  return (
    <>
      <div className="tabs" role="tablist" aria-label={t("live.tabs")}>
        {counts.map((status) => (
          <button
            key={status.key}
            role="tab"
            aria-selected={status.key === active}
            disabled={status.items.length === 0}
            className={status.key === active ? "tab tab-on" : "tab"}
            onClick={() => setChosen(status.key)}
          >
            {t(`status.${status.key}`)}
            <span className={`tab-count tab-count-${status.key}`}>{status.items.length}</span>
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <p className="hint">{t("live.emptyStatus")}</p>
      ) : (
        <ul className="sessions">
          {shown.slice(0, limit).map((session) => (
            <SessionCard key={session.id} session={session} now={now} />
          ))}
        </ul>
      )}
      {shown.length > limit && (
        <p className="hint">{t("live.more", { count: shown.length - limit })}</p>
      )}
    </>
  );
}

function SessionCard({ session, now }: { session: LiveSession; now: string }) {
  const { t } = useLang();
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const close = async () => {
    setAsking(false);
    try {
      const result = await closeSession(session.id);
      setNote(result.stopped ? t("card.terminated", { pid: String(result.pid) }) : result.note);
    } catch (error) {
      setNote(String(error));
    }
  };

  const hide = async () => {
    setAsking(false);
    try {
      await hideSession(session.id);
    } catch (error) {
      setNote(String(error));
    }
  };

  return (
    <li className={`session-${session.status}`}>
      <div className="session-head">
        <span className="session-name">{session.title ?? session.id.slice(0, 8)}</span>
        <span className={`session-badge session-badge-${session.status}`}>
          {t(`status.${session.status}`)}
        </span>
        <div className="session-close">
          <button
            className="session-close-button"
            aria-label={t("card.close")}
            aria-expanded={asking}
            onClick={() => setAsking((open) => !open)}
          >
            ×
          </button>
          {asking && (
            <div className="popover" role="dialog" aria-label={t("card.close")}>
              <p>{t("card.closeQuestion")}</p>
              <p className="popover-warning">
                {t("card.closeWarning")}
              </p>
              <div className="popover-actions">
                <button className="popover-danger" onClick={close}>
                  {t("card.closeConfirm")}
                </button>
                <button onClick={hide}>{t("card.hideOnly")}</button>
                <button onClick={() => setAsking(false)}>{t("card.cancel")}</button>
              </div>
            </div>
          )}
        </div>
      </div>
      <p className="session-prompt">
        {session.last_prompt ?? session.first_prompt ?? t("card.noPrompt")}
      </p>
      <div className="session-meta">
        <code>{session.id.slice(0, 8)}</code>
        {/* The full path is in the tooltip: on the card the short name is what matters. */}
        <span className="session-project" title={session.root_path ?? undefined}>
          {session.project ?? "—"}
        </span>
      </div>
      <div className="session-meta">
        <span>{t("card.activity", { when: sinceLabel(session.last_at) })}</span>
        <span>{t("card.running", { duration: duration(session.started_at, now) })}</span>
        <span>{t("card.turns", { count: session.turns })}</span>
        <span>{t("card.context", { tokens: compact(session.last_context) })}</span>
      </div>
      {note && <p className="session-note">{note}</p>}
    </li>
  );
}

function TurnRow({ turn }: { turn: Turn }) {
  const { t } = useLang();
  const tools = (turn.tools ?? "").split(" ").filter(Boolean);
  return (
    <li className={turn.is_sidechain ? "turn turn-sidechain" : "turn"}>
      <time>{clockTime(turn.ts)}</time>
      <span className="turn-model">{modelLabel(turn.model)}</span>
      <span className="turn-project">{turn.project ?? "—"}</span>
      <span className="turn-output">{grouped(turn.output_tokens)}</span>
      <span className="turn-context">{compact(turn.context_estimate)}</span>
      <span className="turn-tools">
        {tools.length === 0 ? "" : tools.slice(0, 4).map(toolLabel).join(" · ")}
        {tools.length > 4 && ` +${tools.length - 4}`}
      </span>
      {turn.is_sidechain === 1 && <span className="badge">{t("feed.sidechain")}</span>}
    </li>
  );
}
