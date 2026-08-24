// The "Session" screen (task C2): the context growth chart over turns with visible
// moments of auto-compaction and branching, the turn feed, the model breakdown.
// The screen's main question is where the session bloated and when to run /clear.

import { useEffect, useMemo, useState } from "react";

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
        <span className="hint">{session.project ?? "-"}</span>
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
      <TurnFeed turns={turns} />
    </section>
  );
}

/** The feed columns in one place: the caption, the cell, what is shown and what is sorted by.
 *  `desc` is where the first click on the column takes it. */
const COLUMNS = [
  {
    key: "time",
    label: "session.col.time",
    cell: "turns-time",
    right: false,
    desc: true,
    show: (turn: SessionTurn) => clockTime(turn.ts),
    by: (turn: SessionTurn) => turn.ts,
  },
  {
    key: "model",
    label: "session.col.model",
    cell: "turns-model",
    right: false,
    desc: false,
    show: (turn: SessionTurn) => modelLabel(turn.model),
    by: (turn: SessionTurn) => modelLabel(turn.model),
  },
  {
    key: "output",
    label: "session.col.output",
    cell: "turns-number",
    right: true,
    desc: true,
    show: (turn: SessionTurn) => compact(turn.output_tokens),
    by: (turn: SessionTurn) => turn.output_tokens,
  },
  {
    key: "context",
    label: "session.col.context",
    cell: "turns-number",
    right: true,
    desc: true,
    show: (turn: SessionTurn) => compact(turn.context_estimate),
    by: (turn: SessionTurn) => turn.context_estimate,
  },
  {
    key: "tools",
    label: "session.col.tools",
    cell: "turns-tools",
    right: false,
    desc: false,
    show: (turn: SessionTurn) => toolsText(turn),
    by: (turn: SessionTurn) => toolsText(turn),
  },
] as const;

type Column = (typeof COLUMNS)[number];

function toolsText(turn: SessionTurn): string {
  return (turn.tools ?? "").split(" ").filter(Boolean).map(toolLabel).join(", ");
}

function compare(a: string | number, b: string | number): number {
  return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
}

/** The turn feed with a sortable header. The chart keeps the order the server gave -
 *  there the turn number is the axis, here it is the reader's choice. */
function TurnFeed({ turns }: { turns: SessionTurn[] }) {
  const { t } = useLang();
  // by default the fresh ones are on top: one comes to the feed for the tail of the session
  const [sort, setSort] = useState({ key: "time", desc: true });

  const rows = useMemo(() => {
    const column = COLUMNS.find((item) => item.key === sort.key) ?? COLUMNS[0];
    const direction = sort.desc ? -1 : 1;
    return [...turns].sort((a, b) => compare(column.by(a), column.by(b)) * direction);
  }, [turns, sort]);

  const pick = (column: Column) =>
    setSort((current) =>
      current.key === column.key
        ? { key: column.key, desc: !current.desc }
        : { key: column.key, desc: column.desc },
    );

  return (
    <div className="turns">
      <div className="turns-head">
        {COLUMNS.map((column) => (
          <button
            key={column.key}
            className={
              "turns-sort" +
              (column.right ? " turns-sort-right" : "") +
              (sort.key === column.key ? " turns-sort-on" : "")
            }
            title={t("session.sortHint")}
            onClick={() => pick(column)}
          >
            {t(column.label)}
            <span className="turns-arrow" aria-hidden="true">
              {sort.key === column.key ? (sort.desc ? "\u2193" : "\u2191") : ""}
            </span>
          </button>
        ))}
      </div>
      {rows.map((turn) => (
        <div
          key={turn.message_id}
          className={turn.is_idle ? "turns-row turns-row-idle" : "turns-row"}
          title={turn.is_idle ? t("session.idleHint") : undefined}
        >
          {COLUMNS.map((column) => (
            <span key={column.key} className={column.cell}>
              {column.show(turn)}
            </span>
          ))}
        </div>
      ))}
    </div>
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
