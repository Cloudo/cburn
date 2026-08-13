import { useEffect, useState } from "react";

import { Gauge, OutputMeter, Recorder, type Slice } from "./Gauge";
import { Idle, Models, PlanLimits, Tools } from "./Profile";
import { Dashboard, type WidgetContent } from "./Dashboard";
import {
  agoLabel,
  clockTime,
  compact,
  duration,
  grouped,
  modelLabel,
  sinceLabel,
  toolLabel,
} from "./format";
import {
  closeSession,
  hideSession,
  useOverview,
  type LiveSession,
  type Overview,
  type SessionStatus,
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
];

const COLORS = {
  cacheRead: "#4d7fa3",
  cacheWrite: "#7b6bc0",
  output: "#e8a33d",
  input: "#5eab86",
};

//: Окно разбивки: «как прибор» держит её в такт со стрелкой, остальные
//: значения отвязывают её — например, чтобы смотреть долю кэша за сутки.
const SLICE_WINDOWS = ["sync", "10s", "1m", "5m", "60m", "today"] as const;
const SLICE_LABEL: Record<string, string> = {
  sync: "как прибор",
  "10s": "10 с",
  "1m": "мин",
  "5m": "5 мин",
  "60m": "час",
  today: "сегодня",
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

export default function App() {
  const { data, connection, updatedAt } = useOverview();
  const [, tick] = useState(0);

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
          <span className="brand-note">расход Claude Code на этой машине</span>
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

      {data ? (
        <Dashboard widgets={buildWidgets(data)} />
      ) : (
        <p className="empty-note">
          {connection === "offline" ? "нет связи с cdash serve" : "подключаюсь…"}
        </p>
      )}
    </main>
  );
}

/** Содержимое виджетов. Раскладку и видимость держит Dashboard. */
function buildWidgets(data: Overview): WidgetContent[] {
  return [
    { id: "gauge", title: "прибор", body: <GaugeWidget data={data} /> },
    { id: "today", title: "за сегодня", body: <TodayWidget data={data} /> },
    {
      id: "live",
      title: "сейчас в работе",
      body: <SessionBoard sessions={data.live_sessions} limit={data.live_limit} now={data.now} />,
    },
    { id: "plan", title: "лимиты подписки", body: <PlanLimits plan={data.plan} /> },
    { id: "leaders", title: "больше всего за сегодня", body: <LeadersWidget data={data} /> },
    { id: "tools", title: "на что уходят ходы", body: <Tools profile={data.tools} /> },
    { id: "models", title: "модели за сегодня", body: <Models models={data.models} /> },
    { id: "idle", title: "холостые ходы", body: <Idle idle={data.idle} /> },
    { id: "feed", title: "лента ходов", body: <FeedWidget data={data} /> },
  ];
}

function GaugeWidget({ data }: { data: Overview }) {
  const [window, setWindow] = useState<string>("1m");
  const [sliceWindow, setSliceWindow] = useState<string>("sync");

  const burn = data.burn[window];
  const sliceKey = sliceWindow === "sync" ? window : sliceWindow;
  const sliceSource = sliceKey === "today" ? data.today : data.burn[sliceKey].usage;
  const slices = slicesOf(sliceSource);
  const peak = Math.max(...WINDOWS.map((key) => data.burn[key].output_per_min), 500);
  const total = slices.reduce((sum, item) => sum + item.value, 0);

  return (
    <>
      <div className="windows" role="tablist" aria-label="окно усреднения">
        {WINDOWS.map((key) => (
          <button
            key={key}
            role="tab"
            aria-selected={key === window}
            className={key === window ? "window window-on" : "window"}
            onClick={() => setWindow(key)}
          >
            {WINDOW_LABEL[key]}
          </button>
        ))}
      </div>

      <Gauge value={burn.tokens_per_min} slices={slices} caption={WINDOW_CAPTION[window]} />

      <div className="legend-head">
        <span className="legend-title">разбивка</span>
        <div className="slice-windows" role="tablist" aria-label="окно разбивки">
          {SLICE_WINDOWS.map((key) => (
            <button
              key={key}
              role="tab"
              aria-selected={key === sliceWindow}
              className={key === sliceWindow ? "slice-window slice-window-on" : "slice-window"}
              onClick={() => setSliceWindow(key)}
            >
              {SLICE_LABEL[key]}
            </button>
          ))}
        </div>
      </div>

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
              style={{ width: `${(session.tokens / data.top_sessions[0].tokens) * 100}%` }}
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
        <span className="session-project">{session.project ?? "—"}</span>
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
