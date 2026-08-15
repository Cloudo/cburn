// The "Settings" screen (task C3): a form over ~/.config/cburn/config.toml.
// What a human edits by hand is editable here: zone thresholds, the advisor, telegram, the
// port and model prices. The backend validates the input - it owns the file.

import { useEffect, useState } from "react";

import { loadConfig, saveConfig, type Config, type ModelPrice } from "./api";
import { useLang } from "./i18n";

const PRICE_COLUMNS: Array<keyof ModelPrice> = [
  "input",
  "output",
  "cache_write_5m",
  "cache_write_1h",
  "cache_read",
];

export function Settings() {
  const { t } = useLang();
  const [config, setConfig] = useState<Config | null>(null);
  const [path, setPath] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadConfig()
      .then((data) => {
        setConfig(data.config);
        setPath(data.path);
      })
      .catch(() => setError(t("settings.loadFailed")));
  }, []);

  if (error && !config) return <section className="screen"><p className="hint">{error}</p></section>;
  if (!config)
    return (
      <section className="screen">
        <p className="hint">{t("session.loading")}</p>
      </section>
    );

  const patch = (section: keyof Config, key: string, value: unknown) =>
    setConfig({ ...config, [section]: { ...(config[section] as object), [key]: value } });

  const patchPrice = (model: string, key: keyof ModelPrice, value: number) =>
    setConfig({
      ...config,
      prices: { ...config.prices, [model]: { ...config.prices[model], [key]: value } },
    });

  const save = async () => {
    setSaving(true);
    setNote("");
    setError("");
    try {
      const saved = await saveConfig(config);
      setConfig(saved.config);
      setNote(t("settings.saved"));
    } catch (reason) {
      setError(String(reason instanceof Error ? reason.message : reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="screen">
      <div className="session-head-line">
        <h2>{t("settings.title")}</h2>
        <span className="hint">{path}</span>
      </div>

      <div className="settings-columns">
        <fieldset className="settings-group">
          <legend>{t("settings.zones")}</legend>
          <Num
            label={t("settings.warn")}
            value={config.thresholds.context_warn}
            onChange={(value) => patch("thresholds", "context_warn", value)}
          />
          <Num
            label={t("settings.crit")}
            value={config.thresholds.context_crit}
            onChange={(value) => patch("thresholds", "context_crit", value)}
          />
          <Num
            label={t("settings.idleRun")}
            value={config.thresholds.idle_run}
            onChange={(value) => patch("thresholds", "idle_run", value)}
          />
          <Num
            label={t("settings.burnWarn")}
            value={config.thresholds.burn_rate_warn_per_min}
            onChange={(value) => patch("thresholds", "burn_rate_warn_per_min", value)}
          />
        </fieldset>

        <fieldset className="settings-group">
          <legend>{t("settings.analyzer")}</legend>
          <Check
            label={t("settings.enabled")}
            value={config.analyzer.enabled}
            onChange={(value) => patch("analyzer", "enabled", value)}
          />
          <Num
            label={t("settings.interval")}
            value={config.analyzer.interval_minutes}
            onChange={(value) => patch("analyzer", "interval_minutes", value)}
          />
          <Choice
            label={t("settings.model")}
            value={config.analyzer.model}
            options={["haiku", "sonnet", "opus"]}
            onChange={(value) => patch("analyzer", "model", value)}
          />
          <Choice
            label={t("settings.weeklyModel")}
            value={config.analyzer.weekly_deep_model}
            options={["haiku", "sonnet", "opus"]}
            onChange={(value) => patch("analyzer", "weekly_deep_model", value)}
          />
          <Choice
            label={t("settings.analyzer.language")}
            value={config.analyzer.language ?? "en"}
            options={["en", "ru"]}
            render={(value) => t(`settings.lang.${value}`)}
            onChange={(value) => patch("analyzer", "language", value)}
          />
          <Check
            label={t("settings.snippets")}
            value={config.analyzer.allow_snippets}
            onChange={(value) => patch("analyzer", "allow_snippets", value)}
          />
        </fieldset>

        <fieldset className="settings-group">
          <legend>{t("settings.otel")}</legend>
          <Check
            label={t("settings.enabled")}
            value={config.otel?.enabled ?? true}
            onChange={(value) => patch("otel", "enabled", value)}
          />
          <Num
            label={t("settings.otelKeepDays")}
            value={config.otel?.keep_days ?? 30}
            onChange={(value) => patch("otel", "keep_days", value)}
          />
          <p className="hint">{t("settings.otelHint")}</p>
        </fieldset>

        <fieldset className="settings-group">
          <legend>telegram</legend>
          <Choice
            label={t("settings.channel")}
            value={config.telegram.mode}
            options={["bridge", "bot", "off"]}
            onChange={(value) => patch("telegram", "mode", value)}
          />
          <Text
            label={t("settings.bridge")}
            value={config.telegram.bridge_url}
            onChange={(value) => patch("telegram", "bridge_url", value)}
          />
          <Text
            label={t("settings.botToken")}
            value={config.telegram.bot_token}
            secret
            onChange={(value) => patch("telegram", "bot_token", value)}
          />
          <Text
            label="chat id"
            value={config.telegram.chat_id}
            onChange={(value) => patch("telegram", "chat_id", value)}
          />
          <Text
            label={t("settings.dailyAt")}
            value={config.telegram.daily_summary_at}
            onChange={(value) => patch("telegram", "daily_summary_at", value)}
          />
        </fieldset>

        <fieldset className="settings-group">
          <legend>{t("settings.server")}</legend>
          <Num
            label={t("settings.port")}
            value={config.server.port}
            onChange={(value) => patch("server", "port", value)}
          />
          <p className="hint">{t("settings.portNote")}</p>
        </fieldset>
      </div>

      <h3>{t("settings.prices")}</h3>
      <p className="hint">{t("settings.pricesNote")}</p>
      <div className="prices">
        <div className="prices-head">
          <span>{t("settings.priceModel")}</span>
          {PRICE_COLUMNS.map((column) => (
            <span key={column} className="prices-number">
              {t(`settings.price.${column}`)}
            </span>
          ))}
        </div>
        {Object.entries(config.prices).map(([model, price]) => (
          <div key={model} className="prices-row">
            <span className="prices-model">{model}</span>
            {PRICE_COLUMNS.map((column) => (
              <input
                key={column}
                className="prices-number"
                type="number"
                step="0.01"
                min="0"
                value={price[column] ?? 0}
                onChange={(event) => patchPrice(model, column, Number(event.target.value))}
              />
            ))}
          </div>
        ))}
        {!Object.keys(config.prices).length && (
          <p className="hint">
            {t("settings.noPrices")} <code>cburn prices --init</code>
          </p>
        )}
      </div>

      <div className="settings-actions">
        <button className="settings-save" onClick={save} disabled={saving}>
          {saving ? t("settings.saving") : t("settings.save")}
        </button>
        {note && <span className="settings-note">{note}</span>}
        {error && <span className="settings-error">{error}</span>}
      </div>
    </section>
  );
}

function Num({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function Text({
  label,
  value,
  secret = false,
  onChange,
}: {
  label: string;
  value: string;
  secret?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type={secret ? "password" : "text"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function Check({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="field field-check">
      <input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function Choice({
  label,
  value,
  options,
  onChange,
  render,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  /** How to show an option: model names stay as they are, languages are translated. */
  render?: (option: string) => string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {render ? render(option) : option}
          </option>
        ))}
      </select>
    </label>
  );
}
