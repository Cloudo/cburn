// The "Session" screen (task C2): the context growth chart over turns with visible
// moments of auto-compaction and branching, the turn feed, the model breakdown.
// The screen's main question is where the session bloated and when to run /clear.

import { useEffect, useMemo } from "react";

import { clockTime, compact, duration, grouped, modelLabel, spent, toolLabel, usd } from "../lib/format";
import { useLang } from "../lib/i18n";
import { useSession, type SessionEvent, type SessionTurn } from "../lib/api";

/** Context zones from SPEC §4: up to 80k is calm, up to 150k is time to look around. */
const WARN = 80_000;
const CRIT = 150_000;

export function Session({ id }: { id: string }) {
  const { t } = useLang();
  const { data, error, reload } = useSession(id);

  useEffect(() => {
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [reload]);

  if (error)
    return (
      <section className="screen">
        <p className="hint">{t("session.notFound")}</p>
      </section>
    );
  if (!data)
    return (
      <section className="screen">
        <p className="hint">{t("session.loading")}</p>
      </section>
    );

  const { session, turns, events, models, tools, chain } = data;
  const idle = turns.filter((turn) => turn.is_idle).length;
  // Only telemetry knows the time inside a tool - without it the column stays empty.
  const seconds = new Map(
    (data.tool_times ?? [])
      .filter((row) => row.tool && row.seconds)
      .map((row) => [row.tool as string, row.seconds as number]),
  );

  return (
    <section className="screen">
      <div className="session-head-line">
        <a className="back" href="#/sessions">
          ← {t("session.back")}
        </a>
        <h2>{session.title ?? session.session_id.slice(0, 8)}</h2>
        <span className="hint">{session.project ?? "—"}</span>
      </div>

      <p className="session-prompt">{session.first_prompt ?? t("card.noPrompt")}</p>

      <dl className="session-facts">
        <Fact label={t("session.turns")} value={grouped(session.turns)} />
        <Fact label={t("session.output")} value={compact(session.output_tokens)} />
        <Fact label={t("session.cacheRead")} value={compact(session.cache_read)} />
        <Fact label={t("session.cost")} value={usd(session.cost_usd)} />
        <Fact label={t("session.context")} value={compact(session.last_context)} />
        <Fact label={t("session.ran")} value={duration(session.started_at, session.last_at)} />
        {session.sidechain_turns > 0 && (
          <Fact
            label={t("session.sidechain")}
            value={t("session.sidechainValue", {
              turns: session.sidechain_turns,
              cost: usd(session.sidechain_cost_usd),
            })}
          />
        )}
        {idle > 0 && <Fact label={t("session.idleTurns")} value={String(idle)} />}
      </dl>

      <ContextChart turns={turns} events={events} />

      {chain.sessions.length > 1 && (
        <p className="hint">
          {t("session.chain", {
            sessions: chain.sessions.length,
            turns: grouped(chain.turns),
          })}{" "}
          {usd(chain.cost_usd)}
          {session.parent_session_id && (
            <>
              {t("session.continues")}
              <a href={`#/session/${session.parent_session_id}`}>
                {session.parent_session_id.slice(0, 8)}
              </a>
            </>
          )}
        </p>
      )}

      <div className="session-columns">
        <div>
          <h3>{t("session.models")}</h3>
          <ul className="bars">
            {models.map((model) => (
              <li key={model.model}>
                <span className="bars-label">{modelLabel(model.model)}</span>
                <span className="bars-value">{t("models.turns", { count: model.turns })}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>{t("session.tools")}</h3>
          <ul className="bars">
            {tools.map((tool) => (
              <li key={tool.tool}>
                <span className="bars-label">{toolLabel(tool.tool)}</span>
                <span className="bars-value">
                  {tool.calls}
                  {seconds.get(tool.tool) !== undefined && (
                    <span className="bars-extra">({spent(seconds.get(tool.tool)!)})</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <h3>{t("session.feed")}</h3>
      <div className="turns">
        {turns.map((turn) => (
          <div
            key={turn.message_id}
            className={turn.is_idle ? "turns-row turns-row-idle" : "turns-row"}
            title={turn.is_idle ? t("session.idleHint") : undefined}
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

/** The context chart over turns. The X axis is the turn number, not the time:
 *  pauses between turns run into hours, and by time the chart degenerates into a shelf. */
function ContextChart({ turns, events }: { turns: SessionTurn[]; events: SessionEvent[] }) {
  const { t } = useLang();
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

  // A milestone is placed on the nearest turn in time: it has its own time, the axis has turns.
  const marks = events
    .map((event) => {
      const index = turns.findIndex((turn) => turn.ts >= event.ts);
      return { ...event, index: index < 0 ? turns.length - 1 : index };
    })
    .filter((mark) => mark.index > 0);

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img"
        aria-label={t("session.chart")}>
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
        <span>{t("session.chartPeak", { peak: compact(peak) })}</span>
        <span className="chart-key chart-key-compact">{t("session.compaction")}</span>
        <span className="chart-key chart-key-fork">{t("session.fork")}</span>
        <span className="chart-key chart-key-crit">
          {t("session.above", { value: compact(CRIT) })}
        </span>
      </figcaption>
    </figure>
  );
}
