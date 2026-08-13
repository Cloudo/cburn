// Экран «Настройки» (задача C3): форма над ~/.config/cloudo-dash/config.toml.
// Правится то, что человек меняет руками: пороги зон, советчик, telegram, порт
// и цены моделей. Проверяет введённое бэкенд — он же владеет файлом.

import { useEffect, useState } from "react";

import { loadConfig, saveConfig, type Config, type ModelPrice } from "./api";

const PRICE_COLUMNS: Array<{ key: keyof ModelPrice; label: string }> = [
  { key: "input", label: "вход" },
  { key: "output", label: "выход" },
  { key: "cache_write_5m", label: "кэш 5m" },
  { key: "cache_write_1h", label: "кэш 1h" },
  { key: "cache_read", label: "чтение" },
];

export function Settings() {
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
      .catch(() => setError("не удалось прочитать настройки"));
  }, []);

  if (error && !config) return <section className="screen"><p className="hint">{error}</p></section>;
  if (!config) return <section className="screen"><p className="hint">загружаю…</p></section>;

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
      setNote("сохранено, цены пересчитаны");
    } catch (reason) {
      setError(String(reason instanceof Error ? reason.message : reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="screen">
      <div className="session-head-line">
        <h2>настройки</h2>
        <span className="hint">{path}</span>
      </div>

      <div className="settings-columns">
        <fieldset className="settings-group">
          <legend>зоны контекста и тревоги</legend>
          <Num
            label="жёлтая зона, токенов"
            value={config.thresholds.context_warn}
            onChange={(value) => patch("thresholds", "context_warn", value)}
          />
          <Num
            label="красная зона, токенов"
            value={config.thresholds.context_crit}
            onChange={(value) => patch("thresholds", "context_crit", value)}
          />
          <Num
            label="холостых ходов подряд"
            value={config.thresholds.idle_run}
            onChange={(value) => patch("thresholds", "idle_run", value)}
          />
          <Num
            label="тревога при токенах в минуту"
            value={config.thresholds.burn_rate_warn_per_min}
            onChange={(value) => patch("thresholds", "burn_rate_warn_per_min", value)}
          />
        </fieldset>

        <fieldset className="settings-group">
          <legend>советчик</legend>
          <Check
            label="включён"
            value={config.analyzer.enabled}
            onChange={(value) => patch("analyzer", "enabled", value)}
          />
          <Num
            label="такт, минут"
            value={config.analyzer.interval_minutes}
            onChange={(value) => patch("analyzer", "interval_minutes", value)}
          />
          <Choice
            label="модель"
            value={config.analyzer.model}
            options={["haiku", "sonnet", "opus"]}
            onChange={(value) => patch("analyzer", "model", value)}
          />
          <Choice
            label="модель недельного разбора"
            value={config.analyzer.weekly_deep_model}
            options={["haiku", "sonnet", "opus"]}
            onChange={(value) => patch("analyzer", "weekly_deep_model", value)}
          />
          <Check
            label="разрешить фрагменты команд в дайджесте"
            value={config.analyzer.allow_snippets}
            onChange={(value) => patch("analyzer", "allow_snippets", value)}
          />
        </fieldset>

        <fieldset className="settings-group">
          <legend>telegram</legend>
          <Choice
            label="канал"
            value={config.telegram.mode}
            options={["bridge", "bot", "off"]}
            onChange={(value) => patch("telegram", "mode", value)}
          />
          <Text
            label="адрес бриджа"
            value={config.telegram.bridge_url}
            onChange={(value) => patch("telegram", "bridge_url", value)}
          />
          <Text
            label="токен бота"
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
            label="дневная сводка в"
            value={config.telegram.daily_summary_at}
            onChange={(value) => patch("telegram", "daily_summary_at", value)}
          />
        </fieldset>

        <fieldset className="settings-group">
          <legend>сервер</legend>
          <Num
            label="порт"
            value={config.server.port}
            onChange={(value) => patch("server", "port", value)}
          />
          <p className="hint">новый порт подхватится при следующем запуске</p>
        </fieldset>
      </div>

      <h3>цены моделей, $ за миллион токенов</h3>
      <p className="hint">
        Подписка ими не оплачивается: это общая шкала, чтобы взвесить вход, выход и обе записи
        кэша между собой.
      </p>
      <div className="prices">
        <div className="prices-head">
          <span>модель</span>
          {PRICE_COLUMNS.map((column) => (
            <span key={column.key} className="prices-number">
              {column.label}
            </span>
          ))}
        </div>
        {Object.entries(config.prices).map(([model, price]) => (
          <div key={model} className="prices-row">
            <span className="prices-model">{model}</span>
            {PRICE_COLUMNS.map((column) => (
              <input
                key={column.key}
                className="prices-number"
                type="number"
                step="0.01"
                min="0"
                value={price[column.key] ?? 0}
                onChange={(event) => patchPrice(model, column.key, Number(event.target.value))}
              />
            ))}
          </div>
        ))}
        {!Object.keys(config.prices).length && (
          <p className="hint">
            цен нет — положите заготовку командой <code>cdash prices --init</code>
          </p>
        )}
      </div>

      <div className="settings-actions">
        <button className="settings-save" onClick={save} disabled={saving}>
          {saving ? "сохраняю…" : "сохранить"}
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
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}
