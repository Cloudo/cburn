// Экран «Сессии» (задача C1): вся история с фильтрами по проекту, статусу и
// периоду. Цепочка resume схлопнута в одну строку: продолжения раскрываются по
// клику, иначе одна линия работы занимала бы половину экрана.

import { useEffect, useMemo, useState } from "react";

import { compact, duration, sinceLabel, usd } from "./format";
import { useSessions, type SessionRow } from "./api";

const STATUS_LABEL: Record<string, string> = {
  permission: "ждёт разрешения",
  working: "работает",
  answered: "ждёт вас",
  idle: "простаивает",
  done: "закончилась",
};

const PERIODS: Array<{ key: string; label: string }> = [
  { key: "today", label: "сегодня" },
  { key: "24h", label: "сутки" },
  { key: "7d", label: "неделя" },
  { key: "30d", label: "месяц" },
  { key: "all", label: "всё" },
];

/** Строка списка: либо корень цепочки со своими продолжениями, либо одиночка. */
type Line = { root: SessionRow; children: SessionRow[] };

/** Собрать цепочки resume: продолжение уходит под своего родителя.
 *  Родителя может не быть в выдаче (отфильтрован) — тогда строка сама по себе. */
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
  const [project, setProject] = useState("");
  const [status, setStatus] = useState("");
  const [period, setPeriod] = useState("7d");
  const { data, error, reload } = useSessions({ project, status, period });

  // Список перечитывается сам: экран смотрят подолгу, а сессии живые.
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
          <option value="">все проекты</option>
          {(data?.projects ?? []).map((item) => (
            <option key={item.slug} value={item.slug}>
              {item.name} ({item.sessions})
            </option>
          ))}
        </select>

        <div className="filter-tabs" role="tablist" aria-label="статус">
          <button
            role="tab"
            aria-selected={status === ""}
            className={status === "" ? "filter-tab filter-tab-on" : "filter-tab"}
            onClick={() => setStatus("")}
          >
            любой
          </button>
          {Object.entries(STATUS_LABEL).map(([key, label]) => (
            <button
              key={key}
              role="tab"
              aria-selected={status === key}
              className={status === key ? "filter-tab filter-tab-on" : "filter-tab"}
              onClick={() => setStatus(key)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="filter-tabs" role="tablist" aria-label="период">
          {PERIODS.map((item) => (
            <button
              key={item.key}
              role="tab"
              aria-selected={period === item.key}
              className={period === item.key ? "filter-tab filter-tab-on" : "filter-tab"}
              onClick={() => setPeriod(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="hint">нет связи с cdash serve</p>}

      <p className="hint">
        сессий {totals.sessions}, ходов {totals.turns.toLocaleString("ru-RU")},{" "}
        {usd(totals.cost)} по тарифам API
      </p>

      <div className="sessions-table">
        <div className="sessions-head">
          <span>сессия</span>
          <span>проект</span>
          <span className="sessions-number">ходов</span>
          <span className="sessions-number">токенов</span>
          <span className="sessions-number">по API</span>
          <span>расход по времени</span>
          <span>активность</span>
        </div>
        {lines.map((line) => (
          <Row key={line.root.id} line={line} />
        ))}
        {!lines.length && !error && <p className="hint">под фильтр ничего не попало</p>}
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
  return (
    <div className={nested ? "sessions-row sessions-row-nested" : "sessions-row"}>
      <span className="sessions-name">
        {chain > 0 && (
          <button className="chain-toggle" onClick={onToggle} aria-expanded={open}>
            {open ? "−" : "+"}
            {chain}
          </button>
        )}
        <span className={`sessions-dot sessions-dot-${row.status}`} title={STATUS_LABEL[row.status]} />
        <span className="sessions-title" title={row.first_prompt ?? undefined}>
          {row.title ?? row.id.slice(0, 8)}
        </span>
      </span>
      <span className="sessions-project" title={row.root_path ?? undefined}>
        {row.project ?? "—"}
      </span>
      <span className="sessions-number">{row.turns.toLocaleString("ru-RU")}</span>
      <span className="sessions-number">{compact(row.tokens)}</span>
      <span className="sessions-number">{usd(row.cost_usd)}</span>
      <Spark values={row.spark} />
      <span className="sessions-when" title={`шла ${duration(row.started_at, row.last_at)}`}>
        {sinceLabel(row.last_at)}
      </span>
    </div>
  );
}

/** Спарклайн расхода: столбик на равную долю жизни сессии. */
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
