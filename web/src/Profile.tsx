// «На что уходят ходы»: метрики ТЗ §4 — профиль инструментов, доля моделей,
// холостые ходы и оценка окна лимитов подписки.

import { agoLabel, compact, grouped, modelLabel, toolLabel } from "./format";
import type { IdleTurns, Limits, ModelShare, Plan, ToolProfile } from "./api";

/** Список с полосками доли — одна форма для инструментов и bash-команд. */
function Ranked({ rows, total }: { rows: Array<{ name: string; calls: number }>; total: number }) {
  const peak = Math.max(...rows.map((row) => row.calls), 1);
  return (
    <ol className="ranked">
      {rows.map((row) => (
        <li key={row.name}>
          <span className="ranked-bar" style={{ width: `${(row.calls / peak) * 100}%` }} />
          <span className="ranked-name">{row.name}</span>
          <span className="ranked-calls">{grouped(row.calls)}</span>
          <span className="ranked-share">{total ? Math.round((row.calls / total) * 100) : 0}%</span>
        </li>
      ))}
    </ol>
  );
}

export function Tools({ profile }: { profile: ToolProfile }) {
  const bashTotal = profile.bash_commands.reduce((sum, row) => sum + row.calls, 0);
  if (profile.tools_total === 0) {
    return <p className="hint">сегодня инструменты ещё не вызывались</p>;
  }
  return (
    <div className="profile">
      <div>
        <h3>инструменты</h3>
        <Ranked
          rows={profile.tools.map((row) => ({ name: toolLabel(row.tool), calls: row.calls }))}
          total={profile.tools_total}
        />
      </div>
      {profile.bash_commands.length > 0 && (
        <div>
          <h3>внутри bash</h3>
          <Ranked
            rows={profile.bash_commands.map((row) => ({ name: row.command, calls: row.calls }))}
            total={bashTotal}
          />
        </div>
      )}
    </div>
  );
}

export function Models({ models }: { models: ModelShare[] }) {
  const total = models.reduce((sum, row) => sum + row.tokens, 0);
  if (!total) return <p className="hint">сегодня ходов ещё не было</p>;
  return (
    <ol className="ranked">
      {models.map((row) => (
        <li key={row.model}>
          <span className="ranked-bar" style={{ width: `${(row.tokens / total) * 100}%` }} />
          <span className="ranked-name">{modelLabel(row.model)}</span>
          <span className="ranked-calls">{row.turns} ходов</span>
          <span className="ranked-share">{Math.round((row.tokens / total) * 100)}%</span>
        </li>
      ))}
    </ol>
  );
}

export function Idle({ idle }: { idle: IdleTurns }) {
  return (
    <div className="idle">
      <div className="idle-value">
        {idle.turns}
        <span className="idle-share">{Math.round(idle.share * 100)}% ходов</span>
      </div>
      <p className="hint">
        ответ короче {idle.max_output} токенов при контексте больше {compact(idle.min_context)}.
        {idle.turns > 0 && <> На них ушло {compact(idle.cache_read)} токенов чтения кэша.</>}
      </p>
    </div>
  );
}

export function LimitWindow({ limits, now }: { limits: Limits; now: string }) {
  if (!limits.started_at || !limits.resets_at || !limits.usage) {
    return <p className="hint">окно ещё не началось — ходов за последние часы нет</p>;
  }
  const started = new Date(limits.started_at).getTime();
  const resets = new Date(limits.resets_at).getTime();
  const passed = Math.min(Math.max((new Date(now).getTime() - started) / (resets - started), 0), 1);
  const clock = (iso: string) =>
    new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

  return (
    <div className="limits">
      <div className="limits-head">
        <span>
          окно {limits.window_hours} ч · сброс в {clock(limits.resets_at)}
        </span>
        <span className="limits-note">приближение</span>
      </div>
      <div className="limits-track">
        <span className="limits-fill" style={{ width: `${passed * 100}%` }} />
      </div>
      <dl className="limits-facts">
        <div>
          <dt>в этом окне</dt>
          <dd>
            {limits.usage.turns} ходов · {compact(limits.usage.tokens)}
          </dd>
        </div>
        <div>
          <dt>за неделю</dt>
          <dd>
            {limits.week.turns} ходов · {compact(limits.week.tokens)}
          </dd>
        </div>
      </dl>
      <p className="hint">
        Границы окна восстановлены по ходам: Claude Code не пишет в транскрипт ни их, ни сами
        лимиты. Точные цифры появятся с OTel.
      </p>
    </div>
  );
}

const PLAN_LABELS: Record<string, string> = { max: "Max", pro: "Pro", team: "Team" };

/** Лимиты подписки — те же проценты, что показывает `/usage` в Claude Code. */
export function PlanLimits({ plan }: { plan: Plan }) {
  if (plan.limits.length === 0) {
    return (
      <p className="hint">
        {plan.source === "none"
          ? "лимиты недоступны: нет токена Claude Code в связке ключей"
          : "лимиты пока не получены"}
      </p>
    );
  }

  const stale = plan.source === "cache";
  const age = plan.fetched_at ? (Date.now() - plan.fetched_at * 1000) / 1000 : null;

  return (
    <div className="plan">
      {plan.plan && (
        <p className="plan-name">
          {PLAN_LABELS[plan.plan] ?? plan.plan}
          {plan.tier?.includes("5x") && " (5x)"}
        </p>
      )}

      <ul className="plan-limits">
        {plan.limits.map((limit) => (
          <li key={limit.kind + limit.label} className={limit.is_active ? "plan-active" : ""}>
            <div className="plan-head">
              <span className="plan-label">{limit.label}</span>
              <span className={`plan-percent plan-${limit.severity ?? "normal"}`}>
                {Math.round(limit.percent)}%
              </span>
            </div>
            <div className="plan-track">
              <span
                className={`plan-fill plan-fill-${limit.severity ?? "normal"}`}
                style={{ width: `${Math.min(limit.percent, 100)}%` }}
              />
            </div>
            {limit.resets_at && (
              <p className="plan-reset">сброс {resetLabel(limit.resets_at)}</p>
            )}
          </li>
        ))}
      </ul>

      <p className="hint">
        {stale ? "из кэша Claude Code" : "с сервера Anthropic"}
        {age !== null && `, ${agoLabel(age)}`}
      </p>
    </div>
  );
}

/** «через 2 ч 10 мин» — как в самом Claude Code, а не голая дата. */
function resetLabel(iso: string): string {
  const left = (new Date(iso).getTime() - Date.now()) / 1000;
  if (left <= 0) return "вот-вот";
  const hours = Math.floor(left / 3600);
  const minutes = Math.round((left % 3600) / 60);
  const when = new Date(iso).toLocaleString("ru-RU", {
    weekday: hours >= 24 ? "short" : undefined,
    hour: "2-digit",
    minute: "2-digit",
  });
  const inText = hours > 0 ? `через ${hours} ч ${minutes} мин` : `через ${minutes} мин`;
  return hours >= 24 ? when : `${when} · ${inText}`;
}
