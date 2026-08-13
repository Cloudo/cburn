// Экран «Советы» (задача D6): история разборов со статусами. Отклонённый совет
// уезжает в промпт следующего такта пометкой «не повторять» — ради этого
// статусы и нужны, иначе одно и то же приходило бы по кругу.

import { useEffect, useState } from "react";

import { clockTime, usd } from "./format";
import { useLang } from "./i18n";
import { runAdvice, setAdviceStatus, useAdvice, type AdviceItem, type AdviceRun } from "./api";

const SEVERITY_ORDER = ["crit", "warn", "info"] as const;

export function Advice() {
  const { t } = useLang();
  const { data, error, reload } = useAdvice();
  const [running, setRunning] = useState(false);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    const timer = setInterval(reload, 15000);
    return () => clearInterval(timer);
  }, [reload]);

  const runNow = async () => {
    setAsking(false);
    setRunning(true);
    setNote("");
    try {
      const result = await runAdvice("24h");
      setNote(t("advice.done", { cost: usd(result.cost_usd), count: result.advice.length }));
      await reload();
    } catch (reason) {
      setNote(String(reason instanceof Error ? reason.message : reason));
    } finally {
      setRunning(false);
    }
  };

  const runs = data?.runs ?? [];
  const spent = runs.reduce((sum, run) => sum + run.cost_usd, 0);

  return (
    <section className="screen">
      <div className="session-head-line">
        <h2>{t("advice.title")}</h2>
        {runs.length > 0 && (
          <span className="hint">{t("advice.spent", { cost: usd(spent), runs: runs.length })}</span>
        )}
      </div>

      <div className="advice-actions">
        <button className="settings-save" onClick={() => setAsking(true)} disabled={running}>
          {running ? t("advice.running") : t("advice.run")}
        </button>
        {note && <span className="settings-note">{note}</span>}
        {asking && (
          <div className="popover" role="dialog" aria-label={t("advice.run")}>
            <p>{t("advice.confirm")}</p>
            <div className="popover-actions">
              <button className="popover-danger" onClick={runNow}>
                {t("advice.confirmYes")}
              </button>
              <button onClick={() => setAsking(false)}>{t("advice.cancel")}</button>
            </div>
          </div>
        )}
      </div>

      {error && <p className="hint">{t("app.noConnection")}</p>}
      {!runs.length && !error && <p className="hint">{t("advice.empty")}</p>}

      {/* Порядок — по важности, а не по разборам: сперва то, что горит.
          Разбор, которому совет принадлежит, подписан на самой карточке. */}
      {SEVERITY_ORDER.map((severity) => {
        const group = flatten(runs).filter((item) => item.severity === severity);
        if (!group.length) return null;
        return (
          <div key={severity} className="advice-group">
            <h3 className={`advice-group-head advice-group-${severity}`}>
              {t(`advice.group.${severity}`)} <span className="hint">{group.length}</span>
            </h3>
            {group.map((item) => (
              <Item key={item.id} item={item} onChange={reload} />
            ))}
          </div>
        );
      })}
    </section>
  );
}

/** Все советы всех разборов одним списком: у каждого при себе его разбор.
 *  Отклонённые опускаются вниз своей группы — решение уже принято. */
function flatten(runs: AdviceRun[]): Array<AdviceItem & { run: AdviceRun }> {
  const items = runs.flatMap((run) => run.items.map((item) => ({ ...item, run })));
  const sunk = (item: AdviceItem) => (item.status === "rejected" ? 1 : 0);
  return items.sort(
    (a, b) => sunk(a) - sunk(b) || b.run.ts.localeCompare(a.run.ts) || a.id - b.id,
  );
}

function Item({
  item,
  onChange,
}: {
  item: AdviceItem & { run: AdviceRun };
  onChange: () => Promise<void>;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState(false);

  const change = async (status: AdviceItem["status"]) => {
    setBusy(true);
    try {
      await setAdviceStatus(item.id, status);
      await onChange();
    } finally {
      setBusy(false);
    }
  };

  return (
    <article
      className={`advice-item advice-item-${item.severity} advice-item-${item.status}`}
    >
      <div className="advice-item-head">
        <h4>{item.title}</h4>
        {item.status !== "new" && (
          <span className={`advice-status advice-status-${item.status}`}>
            {t(`advice.status.${item.status}`)}
          </span>
        )}
        <span className="advice-origin">
          {clockTime(item.run.ts)} · {t(`advice.kind.${item.run.kind}`)} · {usd(item.run.cost_usd)}
        </span>
      </div>
      {item.sessions.length > 0 && (
        <p className="advice-sessions">
          {item.sessions.map((session) => (
            <a key={session.id} className="advice-session" href={`#/session/${session.id}`}>
              {session.title ?? session.id.slice(0, 8)}
              <span className="advice-session-project">{session.project ?? "—"}</span>
            </a>
          ))}
        </p>
      )}
      {item.detail && <p className="advice-detail">{item.detail}</p>}
      {item.action && (
        <p className="advice-action">
          <span className="advice-label">{t("advice.action")}</span> {item.action}
        </p>
      )}
      {/* Совет без опоры на цифры до экрана не доезжает — его отбрасывает советчик. */}
      <p className="advice-evidence">
        <span className="advice-label">{t("advice.evidence")}</span> {item.evidence}
      </p>
      <div className="advice-buttons">
        {item.status !== "accepted" && (
          <button disabled={busy} onClick={() => change("accepted")}>
            {t("advice.accept")}
          </button>
        )}
        {item.status !== "rejected" && (
          <button disabled={busy} onClick={() => change("rejected")}>
            {t("advice.reject")}
          </button>
        )}
        {item.status !== "new" && (
          <button disabled={busy} onClick={() => change("new")}>
            {t("advice.back")}
          </button>
        )}
        {item.status === "rejected" && <span className="hint">{t("advice.rejectedNote")}</span>}
      </div>
    </article>
  );
}
