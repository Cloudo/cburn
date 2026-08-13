import { useEffect, useState } from "react";

import { Gauge, OutputMeter, Recorder, type Slice } from "./Gauge";
import { Idle, Models, PlanLimits, Tools } from "./Profile";
import { Dashboard, type WidgetContent } from "./Dashboard";
import { Session } from "./Session";
import { Sessions } from "./Sessions";
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
const WINDOW_LABEL: Record<string, string> = {
  "10s": "10 секунд",
  "1m": "минута",
  "5m": "5 минут",
  "60m": "час",
};
//: Подписи для шапки виджета: там переключатель стоит рядом с часами, и
//: «10 секунд» вытеснило бы саму метку времени.
const WINDOW_SHORT: Record<string, string> = {
  "10s": "10 с",
  "1m": "мин",
  "5m": "5 мин",
  "60m": "час",
};

const WINDOW_CAPTION: Record<string, string> = {
  "10s": "за последние 10 секунд",
  "1m": "за последнюю минуту",
  "5m": "за последние 5 минут",
  "60m": "за последний час",
};

//: Статусы в порядке важности: первым открывается таб, где что-то происходит.
const STATUSES: Array<{ key: SessionStatus; label: string }> = [
  { key: "permission", label: "ждёт разрешения" },
  { key: "working", label: "работает" },
  { key: "answered", label: "ждёт вас" },
  { key: "idle", label: "простаивает" },
  { key: "done", label: "закончилась" },
];

const COLORS = {
  cacheRead: "#4d7fa3",
  cacheWrite: "#7b6bc0",
  output: "#e8a33d",
  input: "#5eab86",
};

function slicesOf(source: Usage): Slice[] {
  return [
    { key: "cache_read", label: "чтение кэша", value: source.cache_read, color: COLORS.cacheRead },
    {
      key: "cache_write",
      label: "запись кэша",
      value: source.cache_write,
      color: COLORS.cacheWrite,
    },
    { key: "output", label: "выход", value: source.output_tokens, color: COLORS.output },
    { key: "input", label: "вход", value: source.input_tokens, color: COLORS.input },
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

const SCREENS: Array<{ key: string; label: string }> = [
  { key: "", label: "обзор" },
  { key: "sessions", label: "сессии" },
];

export default function App() {
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
            {SCREENS.map((item) => (
              <a
                key={item.key}
                href={`#/${item.key}`}
                className={item.key === screen ? "screen-link screen-link-on" : "screen-link"}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </div>
        <div className="status">
          {data && data.pending_sessions.length > 0 && (
            <span className="working">
              <span className="working-pulse" />
              идёт запрос
            </span>
          )}
          <span className={`dot dot-${connection}`} />
          <span>{connection === "live" ? "живые данные" : "нет связи"}</span>
          <span className="status-ago">{updatedAt ? agoLabel(ago) : ""}</span>
        </div>
      </header>

      {screen.startsWith("session/") ? (
        <Session id={screen.slice("session/".length)} />
      ) : screen === "sessions" ? (
        <Sessions />
      ) : data ? (
        <Dashboard widgets={buildWidgets(data, refresh, burnWindow, setBurnWindow)} />
      ) : (
        <p className="empty-note">
          {connection === "offline" ? "нет связи с cdash serve" : "подключаюсь…"}
        </p>
      )}
    </main>
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
      title: "прибор",
      body: <GaugeWidget data={data} window={burnWindow} />,
      tools: <WindowPicker value={burnWindow} onChange={setBurnWindow} />,
      at: lastTurn,
      checkedAt,
      refresh,
    },
    {
      id: "today",
      title: "за сегодня",
      body: <TodayWidget data={data} />,
      at: todayTurn,
      checkedAt,
      refresh,
    },
    {
      id: "live",
      title: "сейчас в работе",
      body: <SessionBoard sessions={data.live_sessions} limit={data.live_limit} now={data.now} />,
      at: liveAt,
      checkedAt,
      refresh,
    },
    {
      id: "plan",
      title: "лимиты подписки",
      body: <PlanLimits plan={data.plan} />,
      at: planAt,
      checkedAt,
      staleAfter: PLAN_STALE_SECONDS,
      refresh: refreshPlanLimits,
    },
    {
      id: "leaders",
      title: "больше всего за сегодня",
      body: <LeadersWidget data={data} />,
      at: todayTurn,
      checkedAt,
      refresh,
    },
    {
      id: "tools",
      title: "на что уходят ходы",
      body: <Tools profile={data.tools} />,
      at: timestamp(stamps.tool_call),
      checkedAt,
      refresh,
    },
    {
      id: "models",
      title: "модели за сегодня",
      body: <Models models={data.models} />,
      at: todayTurn,
      checkedAt,
      refresh,
    },
    {
      id: "idle",
      title: "холостые ходы",
      body: <Idle idle={data.idle} />,
      at: timestamp(stamps.idle_turn),
      checkedAt,
      refresh,
    },
    {
      id: "feed",
      title: "лента ходов",
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
  return (
    <div className="window-picker" role="tablist" aria-label="окно усреднения">
      {WINDOWS.map((key) => (
        <button
          key={key}
          role="tab"
          aria-selected={key === value}
          title={WINDOW_LABEL[key]}
          className={key === value ? "window window-on" : "window"}
          onClick={() => onChange(key)}
        >
          {WINDOW_SHORT[key]}
        </button>
      ))}
    </div>
  );
}

function GaugeWidget({ data, window }: { data: Overview; window: string }) {
  const burn = data.burn[window];
  // Разбивка всегда за то же окно, что и стрелка: два разных периода рядом
  // читались как одно целое и путали.
  const slices = slicesOf(burn.usage);
  const peak = Math.max(...WINDOWS.map((key) => data.burn[key].output_per_min), 500);
  const total = slices.reduce((sum, item) => sum + item.value, 0);

  return (
    <>
      {/* Разбивка — сбоку от прибора: по бокам полукруга остаётся пустое поле,
          а список из четырёх строк как раз в него укладывается. */}
      <div className="gauge-row">
        <Gauge value={burn.tokens_per_min} slices={slices} caption={WINDOW_CAPTION[window]} />

        <div className="breakdown">
          <span className="legend-title">разбивка</span>
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
  return (
    <>
      <dl className="today">
        <div>
          <dt>ходов</dt>
          <dd>{grouped(data.today.turns)}</dd>
        </div>
        <div>
          <dt>выход</dt>
          <dd>{grouped(data.today.output_tokens)}</dd>
        </div>
        <div>
          <dt>чтение кэша</dt>
          <dd>{grouped(data.today.cache_read)}</dd>
        </div>
        <div>
          <dt>запись кэша</dt>
          <dd>{grouped(data.today.cache_write)}</dd>
        </div>
        {/* Тариф подписочный: это не счёт, а «сколько стоило бы по API» (ТЗ §4). */}
        <div className="today-wide">
          <dt>по тарифам API</dt>
          <dd>{usd(data.today.cost_usd)}</dd>
        </div>
      </dl>
      <p className="hint">
        всего в базе {grouped(data.totals.turns)} ходов, {data.totals.sessions} сессий,{" "}
        {data.totals.projects} проектов
      </p>
    </>
  );
}

function LeadersWidget({ data }: { data: Overview }) {
  if (data.top_sessions.length === 0) {
    return <p className="hint">сегодня ходов ещё не было</p>;
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
  return (
    <>
      {/* Классы те же, что у строк: на узких экранах колонки прячутся по ним,
          иначе подписи разъехались бы относительно значений. */}
      <div className="turn turn-head" aria-hidden="true">
        <span>время</span>
        <span className="turn-model">модель</span>
        <span className="turn-project">проект</span>
        <span className="turn-output">выход</span>
        <span className="turn-context">контекст</span>
        <span className="turn-tools">инструменты</span>
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
  const [chosen, setChosen] = useState<SessionStatus | null>(null);
  const counts = STATUSES.map((status) => ({
    ...status,
    items: sessions.filter((session) => session.status === status.key),
  }));

  // Пока вкладку не выбрали руками, открыта первая, где что-то есть: смотреть
  // на пустой список «ждёт разрешения» смысла нет.
  const active = chosen ?? counts.find((status) => status.items.length > 0)?.key ?? "working";
  const shown = counts.find((status) => status.key === active)?.items ?? [];

  if (sessions.length === 0) {
    return <p className="hint">ни одной сессии за последний час</p>;
  }

  return (
    <>
      <div className="tabs" role="tablist" aria-label="статус сессий">
        {counts.map((status) => (
          <button
            key={status.key}
            role="tab"
            aria-selected={status.key === active}
            disabled={status.items.length === 0}
            className={status.key === active ? "tab tab-on" : "tab"}
            onClick={() => setChosen(status.key)}
          >
            {status.label}
            <span className={`tab-count tab-count-${status.key}`}>{status.items.length}</span>
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <p className="hint">в этом состоянии сессий нет</p>
      ) : (
        <ul className="sessions">
          {shown.slice(0, limit).map((session) => (
            <SessionCard key={session.id} session={session} now={now} />
          ))}
        </ul>
      )}
      {shown.length > limit && (
        <p className="hint">и ещё {shown.length - limit} — показаны самые свежие</p>
      )}
    </>
  );
}

const STATUS_NOTE: Record<SessionStatus, string> = {
  permission: "ждёт разрешения",
  working: "работает",
  answered: "ждёт вас",
  idle: "простаивает",
  done: "закончилась",
};

function SessionCard({ session, now }: { session: LiveSession; now: string }) {
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const close = async () => {
    setAsking(false);
    try {
      const result = await closeSession(session.id);
      setNote(result.stopped ? `процесс ${result.pid} завершён` : result.note);
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
          {STATUS_NOTE[session.status]}
        </span>
        <div className="session-close">
          <button
            className="session-close-button"
            aria-label="закрыть сессию"
            aria-expanded={asking}
            onClick={() => setAsking((open) => !open)}
          >
            ×
          </button>
          {asking && (
            <div className="popover" role="dialog" aria-label="закрыть сессию">
              <p>Завершить процесс Claude Code и убрать сессию с дашборда?</p>
              <p className="popover-warning">
                Процесс получит SIGTERM: хуки SessionEnd при этом могут не отработать.
              </p>
              <div className="popover-actions">
                <button className="popover-danger" onClick={close}>
                  Закрыть сессию
                </button>
                <button onClick={hide}>Только убрать</button>
                <button onClick={() => setAsking(false)}>Отмена</button>
              </div>
            </div>
          )}
        </div>
      </div>
      <p className="session-prompt">
        {session.last_prompt ?? session.first_prompt ?? "без промпта"}
      </p>
      <div className="session-meta">
        <code>{session.id.slice(0, 8)}</code>
        {/* Полный путь — в подсказке: на карточке важно короткое имя. */}
        <span className="session-project" title={session.root_path ?? undefined}>
          {session.project ?? "—"}
        </span>
      </div>
      <div className="session-meta">
        <span>активность {sinceLabel(session.last_at)}</span>
        <span>идёт {duration(session.started_at, now)}</span>
        <span>ходов {session.turns}</span>
        <span>контекст {compact(session.last_context)}</span>
      </div>
      {note && <p className="session-note">{note}</p>}
    </li>
  );
}

function TurnRow({ turn }: { turn: Turn }) {
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
      {turn.is_sidechain === 1 && <span className="badge">сабагент</span>}
    </li>
  );
}
