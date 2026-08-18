// The "Sessions" screen (task C1): the whole history with filters by project, status and
// period. A resume chain is collapsed into one row: continuations expand on click,
// otherwise one work line would take half the screen.

import { useEffect, useMemo, useState } from "react";

import { compact, duration, grouped, sinceLabel, usd } from "./format";
import { useSessions, type SessionRow } from "./api";
import { useLang } from "./i18n";
import { STATUSES, StatusHelp, statusTitle } from "./StatusHelp";

//: Period keys; the captions come from the dictionary, the statuses from `StatusHelp`.
const PERIODS = ["today", "24h", "7d", "30d", "all"];

/** A list row: either a chain root with its continuations, or a loner. */
type Line = { root: SessionRow; children: SessionRow[] };

/** Assemble resume chains: a continuation goes under its parent.
 *  The parent may be missing from the results (filtered out) - then the row stands alone. */
function toLines(rows: SessionRow[]): Line[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const lines = new Map<string, Line>();
  const order: string[] = [];
  for (const row of rows) {
    const parent = row.parent_session_id;
    const rootId = parent && byId.has(parent) ? parent : row.id;
    if (!lines.has(rootId)) {
      lines.set(rootId, { root: byId.get(rootId) ?? row, children: [] });
      order.push(rootId);
    }
    if (rootId !== row.id) lines.get(rootId)!.children.push(row);
  }
  return order.map((id) => lines.get(id)!);
}

export function Sessions() {
  const { t } = useLang();
  const [project, setProject] = useState("");
  const [status, setStatus] = useState("");
  const [period, setPeriod] = useState("7d");
  const { data, error, reload } = useSessions({ project, status, period });

  // The list re-reads itself: the screen is watched for long, and sessions are alive.
  useEffect(() => {
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [reload]);

  const lines = useMemo(() => toLines(data?.sessions ?? []), [data]);
  const totals = useMemo(() => {
    const rows = data?.sessions ?? [];
    return {
      sessions: rows.length,
      turns: rows.reduce((sum, row) => sum + row.turns, 0),
      cost: rows.reduce((sum, row) => sum + row.cost_usd, 0),
    };
  }, [data]);

  return (
    <section className="screen">
      <div className="filters">
        <select value={project} onChange={(event) => setProject(event.target.value)}>
          <option value="">{t("sessions.allProjects")}</option>
          {(data?.projects ?? []).map((item) => (
            <option key={item.slug} value={item.slug}>
              {item.name} ({item.sessions})
            </option>
          ))}
        </select>

        <div className="tabs-line">
          <div className="filter-tabs" role="tablist" aria-label={t("sessions.status")}>
            <button
              role="tab"
              aria-selected={status === ""}
              className={status === "" ? "filter-tab filter-tab-on" : "filter-tab"}
              onClick={() => setStatus("")}
            >
              {t("sessions.any")}
            </button>
            {STATUSES.map((key) => (
              <button
                key={key}
                role="tab"
                aria-selected={status === key}
                className={status === key ? "filter-tab filter-tab-on" : "filter-tab"}
                title={statusTitle(t, key)}
                onClick={() => setStatus(key)}
              >
                {t(`status.${key}`)}
              </button>
            ))}
          </div>
          <StatusHelp />
        </div>

        <div className="filter-tabs" role="tablist" aria-label={t("sessions.period")}>
          {PERIODS.map((key) => (
            <button
              key={key}
              role="tab"
              aria-selected={period === key}
              className={period === key ? "filter-tab filter-tab-on" : "filter-tab"}
              onClick={() => setPeriod(key)}
            >
              {t(`sessions.period.${key}`)}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="hint">{t("app.noConnection")}</p>}

      <p className="hint">
        {t("sessions.totals", {
          sessions: totals.sessions,
          turns: grouped(totals.turns),
          cost: usd(totals.cost),
        })}
      </p>

      <div className="sessions-table">
        <div className="sessions-head">
          <span>{t("sessions.col.session")}</span>
          <span>{t("sessions.col.project")}</span>
          <span className="sessions-number">{t("sessions.col.turns")}</span>
          <span className="sessions-number">{t("sessions.col.tokens")}</span>
          <span className="sessions-number">{t("sessions.col.cost")}</span>
          <span>{t("sessions.col.timeline")}</span>
          <span>{t("sessions.col.activity")}</span>
        </div>
        {lines.map((line) => (
          <Row key={line.root.id} line={line} />
        ))}
        {!lines.length && !error && <p className="hint">{t("sessions.empty")}</p>}
      </div>
    </section>
  );
}

function Row({ line }: { line: Line }) {
  const [open, setOpen] = useState(false);
  const { root, children } = line;

  return (
    <>
      <SessionLine
        row={root}
        chain={children.length}
        open={open}
        onToggle={() => setOpen((value) => !value)}
      />
      {open && children.map((child) => <SessionLine key={child.id} row={child} nested />)}
    </>
  );
}

function SessionLine({
  row,
  chain = 0,
  open = false,
  onToggle,
  nested = false,
}: {
  row: SessionRow;
  chain?: number;
  open?: boolean;
  onToggle?: () => void;
  nested?: boolean;
}) {
  const { t } = useLang();
  return (
    <div className={nested ? "sessions-row sessions-row-nested" : "sessions-row"}>
      <span className="sessions-name">
        {chain > 0 && (
          <button className="chain-toggle" onClick={onToggle} aria-expanded={open}>
            {open ? "−" : "+"}
            {chain}
          </button>
        )}
        <span
          className={`sessions-dot sessions-dot-${row.status}`}
          title={statusTitle(t, row.status)}
        />
        <a
          className="sessions-title"
          href={`#/session/${row.id}`}
          title={row.first_prompt ?? undefined}
        >
          {row.title ?? row.id.slice(0, 8)}
        </a>
      </span>
      <span className="sessions-project" title={row.root_path ?? undefined}>
        {row.project ?? "—"}
      </span>
      <span className="sessions-number">{grouped(row.turns)}</span>
      <span className="sessions-number">{compact(row.tokens)}</span>
      <span className="sessions-number">{usd(row.cost_usd)}</span>
      <Spark values={row.spark} />
      <span
        className="sessions-when"
        title={t("sessions.ran", { duration: duration(row.started_at, row.last_at) })}
      >
        {sinceLabel(row.last_at)}
      </span>
    </div>
  );
}

/** The spend sparkline: one bar per equal slice of the session's life. */
function Spark({ values }: { values: number[] }) {
  const peak = Math.max(...values, 1);
  return (
    <span className="spark" aria-hidden="true">
      {values.map((value, index) => (
        <span
          key={index}
          className="spark-bar"
          style={{ height: `${Math.max((value / peak) * 100, value > 0 ? 8 : 2)}%` }}
        />
      ))}
    </span>
  );
}
