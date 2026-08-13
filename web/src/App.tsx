import { useEffect, useState } from "react";
import { Gauge, OutputMeter, Recorder, type Slice } from "./Gauge";
import { agoLabel, clockTime, compact, grouped, modelLabel } from "./format";
import { useOverview, type Overview, type Turn } from "./api";

const WINDOWS = ["1m", "5m", "60m"] as const;
const WINDOW_LABEL: Record<string, string> = { "1m": "минута", "5m": "5 минут", "60m": "час" };

const COLORS = {
  cacheRead: "#4d7fa3",
  cacheWrite: "#7b6bc0",
  output: "#e8a33d",
  input: "#5eab86",
};

function slicesOf(data: Overview): Slice[] {
  // Разбивка берётся из расхода за сегодня: в минутном окне ходов бывает
  // два-три, и доли скакали бы при каждом пуше.
  const source = data.today;
  return [
    { key: "cache_read", label: "чтение кэша", value: source.cache_read, color: COLORS.cacheRead },
    { key: "cache_write", label: "запись кэша", value: source.cache_write, color: COLORS.cacheWrite },
    { key: "output", label: "выход", value: source.output_tokens, color: COLORS.output },
    { key: "input", label: "вход", value: source.input_tokens, color: COLORS.input },
  ];
}

export default function App() {
  const { data, connection, updatedAt } = useOverview();
  const [window, setWindow] = useState<string>("1m");
  const [, tick] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  if (!data) {
    return (
      <main className="empty">
        <p>{connection === "offline" ? "нет связи с cdash serve" : "подключаюсь…"}</p>
      </main>
    );
  }

  const burn = data.burn[window];
  const slices = slicesOf(data);
  const peak = Math.max(...WINDOWS.map((key) => data.burn[key].output_per_min), 500);
  const ago = updatedAt ? (Date.now() - updatedAt) / 1000 : 0;

  return (
    <main>
      <header className="masthead">
        <div className="brand">
          <span className="brand-name">cloudo-dash</span>
          <span className="brand-note">расход Claude Code на этой машине</span>
        </div>
        <div className="status">
          {data.pending_sessions.length > 0 && (
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

      <section className="dash">
        <div className="panel panel-gauge">
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

          <Gauge
            value={burn.tokens_per_min}
            slices={slices}
            caption={`за последн${window === "1m" ? "юю минуту" : window === "5m" ? "ие 5 минут" : "ий час"}`}
          />

          <ul className="legend">
            {slices.map((slice) => {
              const total = slices.reduce((sum, item) => sum + item.value, 0);
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
        </div>

        <div className="side">
          <div className="panel">
            <h2>за сегодня</h2>
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
          </div>

          <div className="panel">
            <h2>
              сейчас в работе
              {data.live_sessions.length > 0 && (
                <span className="count">{data.live_sessions.length}</span>
              )}
            </h2>
            {data.live_sessions.length === 0 ? (
              <p className="hint">ни одной сессии за последние две минуты</p>
            ) : (
              <ul className="sessions">
                {data.live_sessions.map((session) => (
                  <li
                    key={session.id}
                    className={data.pending_sessions.includes(session.id) ? "session-working" : ""}
                  >
                    <div className="session-head">
                      <code>{session.id.slice(0, 8)}</code>
                      <span className="session-project">{session.project ?? "—"}</span>
                      {data.pending_sessions.includes(session.id) && (
                        <span className="session-badge">ждёт ответа</span>
                      )}
                    </div>
                    <p className="session-prompt">{session.first_prompt ?? "без промпта"}</p>
                    <div className="session-meta">
                      <span>ходов {session.turns}</span>
                      <span>контекст {compact(session.last_context)}</span>
                      <span>выход {compact(session.tokens_out)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="panel">
            <h2>больше всего за сегодня</h2>
            {data.top_sessions.length === 0 ? (
              <p className="hint">сегодня ходов ещё не было</p>
            ) : (
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
            )}
          </div>
        </div>
      </section>

      <section className="panel feed">
        <h2>лента ходов</h2>
        <ol>
          {data.recent_turns.map((turn) => (
            <TurnRow key={turn.message_id} turn={turn} />
          ))}
        </ol>
      </section>
    </main>
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
        {tools.length === 0 ? "" : tools.slice(0, 4).join(" · ")}
        {tools.length > 4 && ` +${tools.length - 4}`}
      </span>
      {turn.is_sidechain === 1 && <span className="badge">сабагент</span>}
    </li>
  );
}
