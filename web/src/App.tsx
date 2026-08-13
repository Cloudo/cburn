import { useEffect, useState } from "react";

import { Gauge, OutputMeter, Recorder, type Slice } from "./Gauge";
import { Idle, Models, PlanLimits, Tools } from "./Profile";
import { Dashboard, type WidgetContent } from "./Dashboard";
import { Session } from "./Session";
import { Sessions } from "./Sessions";
import { Settings } from "./Settings";
import { useLang } from "./i18n";
import { useTheme } from "./theme";
import {
  agoLabel,
  clockTime,
  compact,
  duration,
  grouped,
  modelLabel,
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

//: Статусы в порядке важности: первым открывается таб, где что-то происходит.
const STATUSES: SessionStatus[] = ["permission", "working", "answered", "idle", "done"];

const COLORS = {
  cacheRead: "#4d7fa3",
  cacheWrite: "#7b6bc0",
  output: "#e8a33d",
  input: "#5eab86",
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

/** Экран выбирается хэшем: #/sessions. Роутер ради двух страниц не нужен, а
 *  адрес должен переживать перезагрузку и жить в закладках. */
function useScreen(): string {
  const [screen, setScreen] = useState(() => window.location.hash.replace(/^#\/?/, ""));
  useEffect(() => {
    const onHash = () => setScreen(window.location.hash.replace(/^#\/?/, ""));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return screen;
}

const SCREENS = ["", "sessions", "settings"];

export default function App() {
  const { lang, setLang, t } = useLang();
  const { theme, setTheme } = useTheme();
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
          <span className="brand-name">cloudo-dash</span>
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
          <button
            className="theme-toggle"
            aria-label={t("app.theme")}
            title={t(theme === "dark" ? "app.theme.light" : "app.theme.dark")}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
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
        </div>
      </header>

      {screen === "settings" ? (
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

/** Солнце и луна: кнопка показывает, куда переключит, а не что сейчас. */
function SunIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
        <path d="M8 1v1.8M8 13.2V15M1 8h1.8M13.2 8H15M3 3l1.3 1.3M11.7 11.7 13 13M13 3l-1.3 1.3M4.3 11.7 3 13" />
      </g>
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
      <path
        d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

//: Обзор от сервера, который не знает про штампы времени: в паре «фронт из
//: web/dist + давно запущенный процесс» такое бывает, и падать из-за этого
//: всему дашборду нельзя.
const NO_STAMPS: Stamps = {
  last_turn: null,
  today_turn: null,
  tool_call: null,
  idle_turn: null,
};

//: Сколько лимиты подписки могут молчать, прежде чем метка станет тревожной.
//: Обычный такт — пять минут, так что вдвое больший разрыв означает не тишину
//: в работе, а неудачные запросы к Anthropic.
const PLAN_STALE_SECONDS = 600;

/** Содержимое виджетов. Раскладку и видимость держит Dashboard.
 *
 * У каждого виджета своё время последних данных: обзор пересчитывается каждую
 * секунду, но события в нём появляются, только когда что-то происходит. Дневные
 * виджеты стоят на последнем ходе с полуночи, лента — на последнем ходе вообще,
 * лимиты подписки — на ответе Anthropic. */
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
      id: "feed",
      title: t("widget.feed"),
      body: <FeedWidget data={data} />,
      at: timestamp(data.recent_turns[0]?.ts ?? null),
      checkedAt,
      refresh,
    },
  ];
}

/** Окно усреднения — в шапке виджета, в одной гамме с часами: меняют его
 *  редко, а места в теле прибора он занимал целую строку. */
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
  // Разбивка всегда за то же окно, что и стрелка: два разных периода рядом
  // читались как одно целое и путали.
  const slices = slicesOf(burn.usage, t);
  const peak = Math.max(...WINDOWS.map((key) => data.burn[key].output_per_min), 500);
  const total = slices.reduce((sum, item) => sum + item.value, 0);

  return (
    <>
      {/* Разбивка — сбоку от прибора: по бокам полукруга остаётся пустое поле,
          а список из четырёх строк как раз в него укладывается. */}
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
        {/* Тариф подписочный: это не счёт, а «сколько стоило бы по API» (ТЗ §4). */}
        <div className="today-wide">
          <dt>{t("today.cost")}</dt>
          <dd>{usd(data.today.cost_usd)}</dd>
        </div>
      </dl>
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
      {/* Классы те же, что у строк: на узких экранах колонки прячутся по ним,
          иначе подписи разъехались бы относительно значений. */}
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

  // Пока вкладку не выбрали руками, открыта первая, где что-то есть: смотреть
  // на пустой список «ждёт разрешения» смысла нет.
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
        {/* Полный путь — в подсказке: на карточке важно короткое имя. */}
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
