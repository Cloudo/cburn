// Экран «Сессия» (задача C2): график роста контекста по ходам с видимыми
// моментами автосуммаризации и ветвления, лента ходов, разбивка по моделям.
// Главный вопрос экрана — где сессия раздулась и когда пора делать /clear.

import { useEffect, useMemo } from "react";

import { clockTime, compact, duration, modelLabel, toolLabel, usd } from "./format";
import { useSession, type SessionEvent, type SessionTurn } from "./api";

/** Зоны контекста по ТЗ §4: до 80k спокойно, до 150k пора оглядеться. */
const WARN = 80_000;
const CRIT = 150_000;

export function Session({ id }: { id: string }) {
  const { data, error, reload } = useSession(id);

  useEffect(() => {
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [reload]);

  if (error) return <section className="screen"><p className="hint">сессия не найдена</p></section>;
  if (!data) return <section className="screen"><p className="hint">загружаю…</p></section>;

  const { session, turns, events, models, tools, chain } = data;
  const idle = turns.filter((turn) => turn.is_idle).length;

  return (
    <section className="screen">
      <div className="session-head-line">
        <a className="back" href="#/sessions">
          ← к сессиям
        </a>
        <h2>{session.title ?? session.session_id.slice(0, 8)}</h2>
        <span className="hint">{session.project ?? "—"}</span>
      </div>

      <p className="session-prompt">{session.first_prompt ?? "без промпта"}</p>

      <dl className="session-facts">
        <Fact label="ходов" value={session.turns.toLocaleString("ru-RU")} />
        <Fact label="выход" value={compact(session.output_tokens)} />
        <Fact label="чтение кэша" value={compact(session.cache_read)} />
        <Fact label="по тарифам API" value={usd(session.cost_usd)} />
        <Fact label="контекст" value={compact(session.last_context)} />
        <Fact label="шла" value={duration(session.started_at, session.last_at)} />
        {session.sidechain_turns > 0 && (
          <Fact
            label="сабагенты"
            value={`${session.sidechain_turns} ходов, ${usd(session.sidechain_cost_usd)}`}
          />
        )}
        {idle > 0 && <Fact label="холостых ходов" value={String(idle)} />}
      </dl>

      <ContextChart turns={turns} events={events} />

      {chain.sessions.length > 1 && (
        <p className="hint">
          линия работы: {chain.sessions.length} сессий, {chain.turns.toLocaleString("ru-RU")} ходов,{" "}
          {usd(chain.cost_usd)}
          {session.parent_session_id && (
            <>
              {" · продолжает "}
              <a href={`#/session/${session.parent_session_id}`}>
                {session.parent_session_id.slice(0, 8)}
              </a>
            </>
          )}
        </p>
      )}

      <div className="session-columns">
        <div>
          <h3>модели</h3>
          <ul className="bars">
            {models.map((model) => (
              <li key={model.model}>
                <span className="bars-label">{modelLabel(model.model)}</span>
                <span className="bars-value">{model.turns} ходов</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>инструменты</h3>
          <ul className="bars">
            {tools.map((tool) => (
              <li key={tool.tool}>
                <span className="bars-label">{toolLabel(tool.tool)}</span>
                <span className="bars-value">{tool.calls}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <h3>лента ходов</h3>
      <div className="turns">
        {turns.map((turn) => (
          <div
            key={turn.message_id}
            className={turn.is_idle ? "turns-row turns-row-idle" : "turns-row"}
            title={turn.is_idle ? "холостой ход: короткий ответ при большом контексте" : undefined}
          >
            <span className="turns-time">{clockTime(turn.ts)}</span>
            <span className="turns-model">{modelLabel(turn.model)}</span>
            <span className="turns-number">{compact(turn.output_tokens)}</span>
            <span className="turns-number">{compact(turn.context_estimate)}</span>
            <span className="turns-tools">
              {(turn.tools ?? "").split(" ").filter(Boolean).map(toolLabel).join(", ")}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/** График контекста по ходам. Ось X — порядковый номер хода, а не время:
 *  паузы между ходами бывают часами, и по времени график вырождается в полку. */
function ContextChart({ turns, events }: { turns: SessionTurn[]; events: SessionEvent[] }) {
  const width = 1000;
  const height = 180;
  const peak = useMemo(
    () => Math.max(...turns.map((turn) => turn.context_estimate), CRIT),
    [turns],
  );

  if (turns.length < 2) return null;

  const x = (index: number) => (index / (turns.length - 1)) * width;
  const y = (value: number) => height - (value / peak) * height;
  const line = turns.map((turn, index) => `${x(index)},${y(turn.context_estimate)}`).join(" ");

  // Веха ставится на ближайший по времени ход: у неё своё время, а ось — ходы.
  const marks = events
    .map((event) => {
      const index = turns.findIndex((turn) => turn.ts >= event.ts);
      return { ...event, index: index < 0 ? turns.length - 1 : index };
    })
    .filter((mark) => mark.index > 0);

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img"
        aria-label="контекст по ходам">
        <rect x="0" y={y(CRIT)} width={width} height={y(WARN) - y(CRIT)} className="chart-warn" />
        <rect x="0" y="0" width={width} height={y(CRIT)} className="chart-crit" />
        <polyline points={line} className="chart-line" />
        {marks.map((mark) => (
          <line
            key={`${mark.kind}-${mark.ts}`}
            x1={x(mark.index)}
            x2={x(mark.index)}
            y1="0"
            y2={height}
            className={mark.kind === "compact" ? "chart-mark-compact" : "chart-mark-fork"}
          />
        ))}
      </svg>
      <figcaption className="chart-legend">
        <span>контекст по ходам, максимум {compact(peak)}</span>
        <span className="chart-key chart-key-compact">автосуммаризация</span>
        <span className="chart-key chart-key-fork">ветвление</span>
        <span className="chart-key chart-key-crit">выше {compact(CRIT)}</span>
      </figcaption>
    </figure>
  );
}
