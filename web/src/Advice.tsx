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

      {runs.map((run) => (
        <Run key={run.id} run={run} onChange={reload} />
      ))}
    </section>
  );
}

function Run({ run, onChange }: { run: AdviceRun; onChange: () => Promise<void> }) {
  const { t } = useLang();
  const items = [...run.items].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
  );

  return (
    <div className="advice-run">
      <div className="advice-run-head">
        <span className="advice-when">{clockTime(run.ts)}</span>
        <span className="advice-kind">{t(`advice.kind.${run.kind}`)}</span>
        <span className="hint">{run.model}</span>
        <span className="advice-cost">{usd(run.cost_usd)}</span>
      </div>
      {!items.length && <p className="hint">{t("advice.noneInRun")}</p>}
      {items.map((item) => (
        <Item key={item.id} item={item} onChange={onChange} />
      ))}
    </div>
  );
}

function Item({ item, onChange }: { item: AdviceItem; onChange: () => Promise<void> }) {
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
    <article className={`advice-item advice-item-${item.status}`}>
      <div className="advice-item-head">
        <span className={`advice-badge advice-badge-${item.severity}`}>
          {t(`advice.severity.${item.severity}`)}
        </span>
        <h3>{item.title}</h3>
      </div>
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
