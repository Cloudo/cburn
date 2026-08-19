// "Where the turns go": the TZ §4 metrics - the tool profile, the model share,
// idle turns and an estimate of the subscription limit window.

import { agoLabel, compact, grouped, modelLabel, share, spent, toolLabel, usd } from "../lib/format";
import type { IdleTurns, Limits, ModelShare, Otel, Plan, ToolProfile } from "../lib/api";
import { translate, useLang, type Lang } from "../lib/i18n";

/** A list with share bars - one shape for tools and for bash commands. */
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
  const { t } = useLang();
  const bashTotal = profile.bash_commands.reduce((sum, row) => sum + row.calls, 0);
  if (profile.tools_total === 0) {
    return <p className="hint">{t("tools.empty")}</p>;
  }
  return (
    <div className="profile">
      <div>
        <h3>{t("tools.title")}</h3>
        <Ranked
          rows={profile.tools.map((row) => ({ name: toolLabel(row.tool), calls: row.calls }))}
          total={profile.tools_total}
        />
      </div>
      {profile.bash_commands.length > 0 && (
        <div>
          <h3>{t("tools.bash")}</h3>
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
  const { t } = useLang();
  const total = models.reduce((sum, row) => sum + row.tokens, 0);
  if (!total) return <p className="hint">{t("leaders.empty")}</p>;
  return (
    <ol className="ranked">
      {models.map((row) => (
        <li key={row.model}>
          <span className="ranked-bar" style={{ width: `${(row.tokens / total) * 100}%` }} />
          <span className="ranked-name">{modelLabel(row.model)}</span>
          <span className="ranked-calls">{t("models.turns", { count: row.turns })}</span>
          <span className="ranked-share">{Math.round((row.tokens / total) * 100)}%</span>
        </li>
      ))}
    </ol>
  );
}

export function Idle({ idle }: { idle: IdleTurns }) {
  const { t } = useLang();
  return (
    <div className="idle">
      <div className="idle-value">
        {idle.turns}
        <span className="idle-share">
          {t("idle.share", { percent: Math.round(idle.share * 100) })}
        </span>
      </div>
      <p className="hint">
        {t("idle.explain", { output: idle.max_output, context: compact(idle.min_context) })}
        {idle.turns > 0 && <> {t("idle.cost", { tokens: compact(idle.cache_read) })}</>}
      </p>
    </div>
  );
}

/** From how many permission mode switches it is worth talking about.
 *  One or two is ordinary work, not a sign that the rules are in the way. */
const MODE_SWITCHES_WORTH_MENTIONING = 3;

/** From what duration hooks are worth showing: fast ones fit into single
 *  milliseconds, and their noise says nothing. */
const HOOK_SECONDS_WORTH_MENTIONING = 5;

/** Claude Code telemetry: what the transcripts do not hold (milestone E).
 *
 *  Service requests the model makes itself (inventing a session title, for
 *  instance) never reach the history files, so the other dashboard numbers
 *  are understated by that much. Permission confirmations are there too:
 *  each manual one stops the work until the human answers. */
export function Telemetry({ otel }: { otel?: Otel }) {
  const { t } = useLang();
  if (!otel?.active) {
    return (
      <div className="telemetry-off">
        <p className="hint">{t("otel.off")}</p>
        <code>cburn otel --env</code>
      </div>
    );
  }
  const { off_transcript: extra, permissions } = otel;
  const switches = (permissions.mode_switches ?? []).reduce((sum, row) => sum + row.switches, 0);
  // The fields appeared later than the rest: the frontend could outrun the running server.
  const work = otel.work;
  const errors = otel.api?.errors ?? 0;
  const hooks = otel.hooks;
  const internal = otel.api?.internal ?? [];
  return (
    <div className="telemetry">
      <div className="telemetry-pair">
        <div className="telemetry-cell">
          <span className="telemetry-value">{usd(extra.cost_usd)}</span>
          <span className="telemetry-label">{t("otel.hidden")}</span>
          <span className="telemetry-note">
            {t("otel.hidden.note", {
              tokens: compact(extra.tokens),
              percent: share(extra.share),
            })}
          </span>
        </div>
        <div className="telemetry-cell">
          <span className="telemetry-value">{grouped(permissions.manual)}</span>
          <span className="telemetry-label">{t("otel.manual")}</span>
          <span className="telemetry-note">
            {t("otel.manual.note", { auto: grouped(permissions.auto) })}
          </span>
        </div>
      </div>
      {/* Observations in short lines: the detailed explanation goes into
          the tooltip, otherwise the widget does not fit on the dashboard. */}
      <ul className="telemetry-facts">
        {work && work.active_seconds > 0 && (
          <li title={t("otel.work.why")}>
            {t("otel.work.time", { time: spent(work.active_seconds) })}
            {work.lines_added + work.lines_removed > 0 &&
              ` · ${t("otel.work.lines", {
                added: grouped(work.lines_added),
                removed: grouped(work.lines_removed),
              })}`}
          </li>
        )}
        {hooks && hooks.seconds >= HOOK_SECONDS_WORTH_MENTIONING && (
          <li title={t("otel.hooks.why")}>
            {t("otel.hooks", {
              time: spent(hooks.seconds),
              events: hooks.events
                .filter((row) => (row.seconds ?? 0) >= 1)
                .slice(0, 2)
                .map((row) => `${row.event} ${spent(row.seconds ?? 0)}`)
                .join(", "),
            })}
          </li>
        )}
        {switches >= MODE_SWITCHES_WORTH_MENTIONING && (
          <li title={t("otel.modes.why")}>
            {t("otel.modes", {
              modes: (permissions.mode_switches ?? [])
                .map((row) => `${row.mode ?? "—"} ×${row.switches}`)
                .join(", "),
            })}
          </li>
        )}
        {errors > 0 && (
          <li title={t("otel.errors.why")}>
            {t("otel.errors", {
              count: errors,
              statuses: (otel.api?.by_status ?? []).map((row) => row.status).join(", "),
            })}
          </li>
        )}
        {internal.length > 0 && (
          <li title={t("otel.internal.why")}>
            {t("otel.internal", {
              count: internal.reduce((sum, row) => sum + row.count, 0),
              errors: internal.map((row) => `${row.error} ×${row.count}`).join(", "),
            })}
          </li>
        )}
      </ul>
      {permissions.by_tool.length > 0 && (
        <ol className="ranked">
          {permissions.by_tool.slice(0, 5).map((row) => (
            <li key={row.tool}>
              <span
                className="ranked-bar"
                style={{ width: `${(row.decisions / permissions.manual) * 100}%` }}
              />
              <span className="ranked-name">{toolLabel(row.tool)}</span>
              <span className="ranked-calls">{grouped(row.decisions)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function LimitWindow({ limits, now }: { limits: Limits; now: string }) {
  const { lang, t } = useLang();
  if (!limits.started_at || !limits.resets_at || !limits.usage) {
    return <p className="hint">{t("limits.empty")}</p>;
  }
  const started = new Date(limits.started_at).getTime();
  const resets = new Date(limits.resets_at).getTime();
  const passed = Math.min(Math.max((new Date(now).getTime() - started) / (resets - started), 0), 1);
  const clock = (iso: string) =>
    new Date(iso).toLocaleTimeString(localeOf(lang), {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });

  return (
    <div className="limits">
      <div className="limits-head">
        <span>
          {t("limits.window", { hours: limits.window_hours, time: clock(limits.resets_at) })}
        </span>
        <span className="limits-note">{t("limits.approx")}</span>
      </div>
      <div className="limits-track">
        <span className="limits-fill" style={{ width: `${passed * 100}%` }} />
      </div>
      <dl className="limits-facts">
        <div>
          <dt>{t("limits.inWindow")}</dt>
          <dd>
            {t("limits.usage", {
              turns: limits.usage.turns,
              tokens: compact(limits.usage.tokens),
            })}
          </dd>
        </div>
        <div>
          <dt>{t("limits.week")}</dt>
          <dd>
            {t("limits.usage", { turns: limits.week.turns, tokens: compact(limits.week.tokens) })}
          </dd>
        </div>
      </dl>
      <p className="hint">{t("limits.note")}</p>
    </div>
  );
}

const PLAN_LABELS: Record<string, string> = { max: "Max", pro: "Pro", team: "Team" };

/** The window caption: the server sends the kind, the words come from the dictionary. */
function limitLabel(
  t: (key: string, vars?: Record<string, string | number>) => string,
  kind: string,
  model: string | null,
): string {
  const window = t(`limit.${kind}`);
  return model ? t("limit.scoped", { window, model }) : window;
}

/** Subscription limits - the same percentages `/usage` shows in Claude Code. */
export function PlanLimits({ plan }: { plan: Plan }) {
  const { lang, t } = useLang();
  if (plan.limits.length === 0) {
    return (
      <p className="hint">{plan.source === "none" ? t("plan.noToken") : t("plan.notFetched")}</p>
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
          <li key={limit.kind + (limit.model ?? "")} className={limit.is_active ? "plan-active" : ""}>
            <div className="plan-head">
              <span className="plan-label">{limitLabel(t, limit.kind, limit.model)}</span>
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
              <p className="plan-reset">
                {t("plan.reset", { when: resetLabel(limit.resets_at, lang) })}
              </p>
            )}
          </li>
        ))}
      </ul>

      <p className="hint">
        {stale ? t("plan.fromCache") : t("plan.fromApi")}
        {age !== null && `, ${agoLabel(age)}`}
      </p>
    </div>
  );
}

function localeOf(lang: Lang): string {
  return lang === "ru" ? "ru-RU" : "en-US";
}

/** "in 2 h 10 min" - as in Claude Code itself, rather than a bare date. */
function resetLabel(iso: string, lang: Lang): string {
  const left = (new Date(iso).getTime() - Date.now()) / 1000;
  if (left <= 0) return translate(lang, "plan.soon");
  const hours = Math.floor(left / 3600);
  const minutes = Math.round((left % 3600) / 60);
  const when = new Date(iso).toLocaleString(localeOf(lang), {
    weekday: hours >= 24 ? "short" : undefined,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const inText =
    hours > 0
      ? translate(lang, "plan.in", { hours, minutes })
      : translate(lang, "plan.inMinutes", { minutes });
  return hours >= 24 ? when : `${when} · ${inText}`;
}
