// «На что уходят ходы»: метрики ТЗ §4 — профиль инструментов, доля моделей,
// холостые ходы и оценка окна лимитов подписки.

import { compact, grouped, modelLabel, toolLabel } from "./format";
import type { IdleTurns, Limits, ModelShare, ToolProfile } from "./api";

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
