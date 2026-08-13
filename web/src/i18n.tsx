// Язык интерфейса. Библиотеки ради двух языков не нужны: словарь плоский,
// подстановка одна, а выбор языка — такое же дело браузера, как и раскладка,
// поэтому он живёт в localStorage и сервер про него не знает.

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { setFormatLang } from "./format";

export type Lang = "ru" | "en";

const STORAGE_KEY = "cloudo-dash.lang";

/** Пары «русский, английский». Ключ — путь через точку, не сам текст: иначе
 *  правка формулировки на одном языке молча ломает второй. */
const DICT: Record<string, [string, string]> = {
  "app.tagline": ["расход Claude Code на этой машине", "Claude Code usage on this machine"],
  "app.screen.overview": ["обзор", "overview"],
  "app.screen.sessions": ["сессии", "sessions"],
  "app.screen.settings": ["настройки", "settings"],
  "app.working": ["идёт запрос", "request running"],
  "app.live": ["живые данные", "live data"],
  "app.offline": ["нет связи", "offline"],
  "app.noConnection": ["нет связи с cdash serve", "no connection to cdash serve"],
  "app.connecting": ["подключаюсь…", "connecting…"],
  "app.lang": ["язык интерфейса", "interface language"],
  "app.theme": ["тема оформления", "colour theme"],
  "app.theme.light": ["светлая тема", "light theme"],
  "app.theme.dark": ["тёмная тема", "dark theme"],

  "window.10s": ["10 секунд", "10 seconds"],
  "window.1m": ["минута", "minute"],
  "window.5m": ["5 минут", "5 minutes"],
  "window.60m": ["час", "hour"],
  "window.short.10s": ["10 с", "10s"],
  "window.short.1m": ["мин", "min"],
  "window.short.5m": ["5 мин", "5min"],
  "window.short.60m": ["час", "hr"],
  "window.caption.10s": ["за последние 10 секунд", "over the last 10 seconds"],
  "window.caption.1m": ["за последнюю минуту", "over the last minute"],
  "window.caption.5m": ["за последние 5 минут", "over the last 5 minutes"],
  "window.caption.60m": ["за последний час", "over the last hour"],
  "window.picker": ["окно усреднения", "averaging window"],

  "status.permission": ["ждёт разрешения", "awaiting permission"],
  "status.working": ["работает", "working"],
  "status.answered": ["ждёт вас", "waiting for you"],
  "status.idle": ["простаивает", "idle"],
  "status.done": ["закончилась", "finished"],

  "slice.cache_read": ["чтение кэша", "cache read"],
  "slice.cache_write": ["запись кэша", "cache write"],
  "slice.output": ["выход", "output"],
  "slice.input": ["вход", "input"],

  "widget.gauge": ["прибор", "gauge"],
  "widget.gauge.breakdown": ["разбивка", "breakdown"],
  "widget.gauge.note": ["burn rate, разбивка, самописец", "burn rate, breakdown, recorder"],
  "widget.today": ["за сегодня", "today"],
  "widget.today.note": ["суммы с местной полуночи", "totals since local midnight"],
  "widget.live": ["сейчас в работе", "working now"],
  "widget.live.note": ["сессии по статусам", "sessions by status"],
  "widget.plan": ["лимиты подписки", "plan limits"],
  "widget.plan.note": ["проценты плана от Anthropic", "plan percentages from Anthropic"],
  "widget.leaders": ["больше всего за сегодня", "biggest today"],
  "widget.leaders.note": ["топ сессий", "top sessions"],
  "widget.tools": ["на что уходят ходы", "where turns go"],
  "widget.tools.note": ["инструменты и bash", "tools and bash"],
  "widget.models": ["модели за сегодня", "models today"],
  "widget.models.note": ["доля моделей", "model share"],
  "widget.idle": ["холостые ходы", "idle turns"],
  "widget.idle.note": ["ответ короче 10 токенов", "reply under 10 tokens"],
  "widget.feed": ["лента ходов", "turn feed"],
  "widget.feed.note": ["последние ходы", "latest turns"],

  "dash.widgets": ["виджеты", "widgets"],
  "dash.tune": ["настройка дашборда", "dashboard settings"],
  "dash.tuneHint": [
    "Перетаскивать за заголовок, размер — за правый нижний угол. Расположение сохраняется в браузере.",
    "Drag by the header, resize from the bottom-right corner. The layout is kept in your browser.",
  ],
  "dash.reset": ["вернуть как было", "reset layout"],
  "dash.refresh": ["обновить", "refresh"],
  "dash.refreshFailed": [
    "обновить (прошлая попытка не удалась)",
    "refresh (last attempt failed)",
  ],
  "dash.refreshWidget": ["обновить виджет «{title}»", "refresh the “{title}” widget"],
  "dash.hideWidget": ["скрыть виджет «{title}»", "hide the “{title}” widget"],

  "gauge.unit": ["токенов в минуту", "tokens per minute"],
  "meter.label": ["выход модели", "model output"],
  "meter.unit": ["ток/мин", "tok/min"],
  "recorder.label": ["выход по {seconds} с", "output per {seconds}s"],
  "recorder.span": ["последние {minutes} мин", "last {minutes} min"],
  "recorder.output": ["выход", "output"],
  "recorder.total": ["всего", "total"],
  "recorder.turns": ["ходов", "turns"],
  "recorder.ago": ["−{minutes} мин", "−{minutes} min"],
  "recorder.peak": ["пик {value} за корзину", "peak {value} per bucket"],
  "recorder.now": ["сейчас", "now"],

  "today.turns": ["ходов", "turns"],
  "today.output": ["выход", "output"],
  "today.cacheRead": ["чтение кэша", "cache read"],
  "today.cacheWrite": ["запись кэша", "cache write"],
  "today.cost": ["по тарифам API", "at API rates"],
  "today.totals": [
    "всего в базе {turns} ходов, {sessions} сессий, {projects} проектов",
    "{turns} turns, {sessions} sessions, {projects} projects in the database",
  ],

  "leaders.empty": ["сегодня ходов ещё не было", "no turns today yet"],

  "feed.time": ["время", "time"],
  "feed.model": ["модель", "model"],
  "feed.project": ["проект", "project"],
  "feed.output": ["выход", "output"],
  "feed.context": ["контекст", "context"],
  "feed.tools": ["инструменты", "tools"],
  "feed.sidechain": ["сабагент", "subagent"],

  "live.empty": ["ни одной сессии за последний час", "no sessions in the last hour"],
  "live.tabs": ["статус сессий", "session status"],
  "live.emptyStatus": ["в этом состоянии сессий нет", "no sessions in this state"],
  "live.more": ["и ещё {count} — показаны самые свежие", "{count} more — showing the freshest"],

  "card.close": ["закрыть сессию", "close session"],
  "card.closeQuestion": [
    "Завершить процесс Claude Code и убрать сессию с дашборда?",
    "Terminate the Claude Code process and remove the session from the dashboard?",
  ],
  "card.closeWarning": [
    "Процесс получит SIGTERM: хуки SessionEnd при этом могут не отработать.",
    "The process gets SIGTERM, so SessionEnd hooks may not run.",
  ],
  "card.closeConfirm": ["Закрыть сессию", "Close session"],
  "card.hideOnly": ["Только убрать", "Just remove"],
  "card.cancel": ["Отмена", "Cancel"],
  "card.noPrompt": ["без промпта", "no prompt"],
  "card.activity": ["активность {when}", "active {when}"],
  "card.running": ["идёт {duration}", "running {duration}"],
  "card.turns": ["ходов {count}", "turns {count}"],
  "card.context": ["контекст {tokens}", "context {tokens}"],
  "card.terminated": ["процесс {pid} завершён", "process {pid} terminated"],

  "tools.empty": ["сегодня инструменты ещё не вызывались", "no tool calls today yet"],
  "tools.title": ["инструменты", "tools"],
  "tools.bash": ["внутри bash", "inside bash"],
  "models.turns": ["{count} ходов", "{count} turns"],
  "idle.share": ["{percent}% ходов", "{percent}% of turns"],
  "idle.explain": [
    "ответ короче {output} токенов при контексте больше {context}.",
    "a reply under {output} tokens with context over {context}.",
  ],
  "idle.cost": [
    "На них ушло {tokens} токенов чтения кэша.",
    "They spent {tokens} tokens of cache reads.",
  ],

  "limits.empty": [
    "окно ещё не началось — ходов за последние часы нет",
    "the window hasn’t started — no turns in the last hours",
  ],
  "limits.window": ["окно {hours} ч · сброс в {time}", "{hours}h window · resets at {time}"],
  "limits.approx": ["приближение", "estimate"],
  "limits.inWindow": ["в этом окне", "in this window"],
  "limits.week": ["за неделю", "this week"],
  "limits.usage": ["{turns} ходов · {tokens}", "{turns} turns · {tokens}"],
  "limits.note": [
    "Границы окна восстановлены по ходам: Claude Code не пишет в транскрипт ни их, ни сами лимиты. Точные цифры появятся с OTel.",
    "Window bounds are reconstructed from turns: Claude Code writes neither them nor the limits into the transcript. Exact numbers will come with OTel.",
  ],

  "plan.noToken": [
    "лимиты недоступны: нет токена Claude Code в связке ключей",
    "limits unavailable: no Claude Code token in the keychain",
  ],
  "plan.notFetched": ["лимиты пока не получены", "limits not fetched yet"],
  "plan.reset": ["сброс {when}", "resets {when}"],
  "plan.fromCache": ["из кэша Claude Code", "from the Claude Code cache"],
  "plan.fromApi": ["с сервера Anthropic", "from Anthropic"],
  "plan.soon": ["вот-вот", "any moment"],
  "plan.in": ["через {hours} ч {minutes} мин", "in {hours}h {minutes}m"],
  "plan.inMinutes": ["через {minutes} мин", "in {minutes}m"],

  "settings.title": ["настройки", "settings"],
  "settings.loadFailed": ["не удалось прочитать настройки", "could not read the settings"],
  "settings.saved": ["сохранено, цены пересчитаны", "saved, costs recalculated"],
  "settings.save": ["сохранить", "save"],
  "settings.saving": ["сохраняю…", "saving…"],
  "settings.zones": ["зоны контекста и тревоги", "context zones and alerts"],
  "settings.warn": ["жёлтая зона, токенов", "yellow zone, tokens"],
  "settings.crit": ["красная зона, токенов", "red zone, tokens"],
  "settings.idleRun": ["холостых ходов подряд", "idle turns in a row"],
  "settings.burnWarn": ["тревога при токенах в минуту", "alert at tokens per minute"],
  "settings.analyzer": ["советчик", "advisor"],
  "settings.enabled": ["включён", "enabled"],
  "settings.interval": ["такт, минут", "interval, minutes"],
  "settings.model": ["модель", "model"],
  "settings.weeklyModel": ["модель недельного разбора", "weekly review model"],
  "settings.snippets": [
    "разрешить фрагменты команд в дайджесте",
    "allow command snippets in the digest",
  ],
  "settings.channel": ["канал", "channel"],
  "settings.bridge": ["адрес бриджа", "bridge address"],
  "settings.botToken": ["токен бота", "bot token"],
  "settings.dailyAt": ["дневная сводка в", "daily summary at"],
  "settings.server": ["сервер", "server"],
  "settings.port": ["порт", "port"],
  "settings.portNote": [
    "новый порт подхватится при следующем запуске",
    "the new port takes effect on the next start",
  ],
  "settings.prices": [
    "цены моделей, $ за миллион токенов",
    "model prices, $ per million tokens",
  ],
  "settings.pricesNote": [
    "Подписка ими не оплачивается: это общая шкала, чтобы взвесить вход, выход и обе записи кэша между собой.",
    "The subscription is not paid with them: it is a common scale to weigh input, output and both cache writes against each other.",
  ],
  "settings.priceModel": ["модель", "model"],
  "settings.price.input": ["вход", "input"],
  "settings.price.output": ["выход", "output"],
  "settings.price.cache_write_5m": ["кэш 5m", "cache 5m"],
  "settings.price.cache_write_1h": ["кэш 1h", "cache 1h"],
  "settings.price.cache_read": ["чтение", "read"],
  "settings.noPrices": ["цен нет — положите заготовку командой", "no prices — create a stub with"],
  "sessions.allProjects": ["все проекты", "all projects"],
  "sessions.any": ["любой", "any"],
  "sessions.status": ["статус", "status"],
  "sessions.period": ["период", "period"],
  "sessions.period.today": ["сегодня", "today"],
  "sessions.period.24h": ["сутки", "24 hours"],
  "sessions.period.7d": ["неделя", "7 days"],
  "sessions.period.30d": ["месяц", "30 days"],
  "sessions.period.all": ["всё", "all"],
  "sessions.totals": [
    "сессий {sessions}, ходов {turns}, {cost} по тарифам API",
    "{sessions} sessions, {turns} turns, {cost} at API rates",
  ],
  "sessions.col.session": ["сессия", "session"],
  "sessions.col.project": ["проект", "project"],
  "sessions.col.turns": ["ходов", "turns"],
  "sessions.col.tokens": ["токенов", "tokens"],
  "sessions.col.cost": ["по API", "at API"],
  "sessions.col.timeline": ["расход по времени", "usage over time"],
  "sessions.col.activity": ["активность", "activity"],
  "sessions.empty": ["под фильтр ничего не попало", "nothing matches the filter"],
  "session.notFound": ["сессия не найдена", "session not found"],
  "session.loading": ["загружаю…", "loading…"],
  "session.back": ["к сессиям", "to sessions"],
  "session.turns": ["ходов", "turns"],
  "session.output": ["выход", "output"],
  "session.cacheRead": ["чтение кэша", "cache read"],
  "session.cost": ["по тарифам API", "at API rates"],
  "session.context": ["контекст", "context"],
  "session.ran": ["шла", "ran"],
  "session.sidechain": ["сабагенты", "subagents"],
  "session.sidechainValue": ["{turns} ходов, {cost}", "{turns} turns, {cost}"],
  "session.idleTurns": ["холостых ходов", "idle turns"],
  "session.chain": [
    "линия работы: {sessions} сессий, {turns} ходов,",
    "work line: {sessions} sessions, {turns} turns,",
  ],
  "session.continues": [" · продолжает ", " · continues "],
  "session.models": ["модели", "models"],
  "session.tools": ["инструменты", "tools"],
  "session.feed": ["лента ходов", "turn feed"],
  "session.idleHint": [
    "холостой ход: короткий ответ при большом контексте",
    "idle turn: a short reply on a big context",
  ],
  "session.chart": ["контекст по ходам", "context by turn"],
  "session.chartPeak": ["контекст по ходам, максимум {peak}", "context by turn, peak {peak}"],
  "session.compaction": ["автосуммаризация", "auto-compaction"],
  "session.fork": ["ветвление", "fork"],
  "session.above": ["выше {value}", "above {value}"],
  "sessions.ran": ["шла {duration}", "ran {duration}"],
};

/** Язык по умолчанию — язык браузера; всё, кроме русского, считаем английским. */
function detect(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "ru" || saved === "en") return saved;
  return navigator.language.startsWith("ru") ? "ru" : "en";
}

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const LangContext = createContext<{ lang: Lang; setLang: (next: Lang) => void }>({
  lang: "ru",
  setLang: () => {},
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(detect);

  // Числа и даты форматируются вне React, поэтому язык им передаётся до того,
  // как дети начнут рисоваться.
  setFormatLang(lang);

  useEffect(() => {
    document.documentElement.lang = lang;
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // приватный режим — язык просто не запомнится
    }
  }, [lang]);

  return <LangContext.Provider value={{ lang, setLang }}>{children}</LangContext.Provider>;
}

export function useLang(): { lang: Lang; setLang: (next: Lang) => void; t: Translate } {
  const { lang, setLang } = useContext(LangContext);
  return { lang, setLang, t: (key, vars) => translate(lang, key, vars) };
}

/** Перевод вне React — там, где нет хуков (словари констант, форматирование). */
export function translate(
  lang: Lang,
  key: string,
  vars?: Record<string, string | number>,
): string {
  const pair = DICT[key];
  const text = pair ? pair[lang === "ru" ? 0 : 1] : key;
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name) =>
    name in vars ? String(vars[name]) : whole,
  );
}
