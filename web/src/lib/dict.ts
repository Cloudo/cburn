// The interface dictionary: the only place in the repository where Russian text lives.
// The rest of the code, the CLI output and the documentation are English; the two languages
// of the interface live here as pairs and are picked by the reader's choice.
//
// It sits apart from `i18n.tsx` so that `format.ts` can reach the same pairs without an
// import cycle: `i18n.tsx` needs `setFormatLang` from `format.ts`.

export type Lang = "ru" | "en";

export const STORAGE_KEY = "cburn.lang";
export const RENAMED_KEY = "cloudo-dash.lang"; // the key of the former project name

const DICT: Record<string, [string, string]> = {
  "app.tagline": ["расход Claude Code на этой машине", "Claude Code usage on this machine"],
  "app.screen.overview": ["обзор", "overview"],
  "app.screen.sessions": ["сессии", "sessions"],
  "app.screen.advice": ["советы", "advice"],
  "app.screen.settings": ["настройки", "settings"],
  "app.working": ["идёт запрос", "request running"],
  "app.live": ["живые данные", "live data"],
  "app.offline": ["нет связи", "offline"],
  "app.noConnection": ["нет связи с cburn serve", "no connection to cburn serve"],
  "app.connecting": ["подключаюсь…", "connecting…"],
  "live.help.open": ["что показывают эти значки", "what these marks mean"],
  "live.help.title": ["связь и свежесть цифр", "the link and the freshness of the numbers"],
  "live.help.hint.live": [
    "сокет открыт, сервер сам шлёт обновления",
    "the socket is open, the server pushes updates itself",
  ],
  "live.help.hint.offline": [
    "поток прервался: на экране последнее, что успело прийти",
    "the stream broke: on screen is the last that got through",
  ],
  "live.help.hint.connecting": ["сокет ещё открывается", "the socket is still opening"],
  "live.help.hint.ago": [
    "когда пришло последнее обновление",
    "when the last update arrived",
  ],
  "live.help.hint.working": [
    "в одной из сессий модель работает прямо сейчас",
    "in one of the sessions the model is working right now",
  ],
  "live.help.note": [
    "Токены незаконченного хода попадают в транскрипт только вместе с ответом, поэтому при идущем запросе показания прибора занижены.",
    "The tokens of an unfinished turn reach the transcript only together with the answer, so while a request is running the readings stand below the truth.",
  ],
  "app.lang": ["язык интерфейса", "interface language"],
  "app.theme": ["тема оформления", "colour theme"],
  "app.theme.system": ["как в системе", "follow the system"],
  "app.theme.dark": ["тёмные", "dark"],
  "app.theme.light": ["светлые", "light"],
  "app.theme.filter": ["поиск темы", "search themes"],
  "app.theme.none": ["ничего не найдено", "nothing found"],
  "app.zoom": ["масштаб интерфейса", "interface scale"],
  "app.zoom.in": ["крупнее", "larger"],
  "app.zoom.out": ["мельче", "smaller"],
  "app.zoom.reset": ["обычный масштаб", "normal scale"],

  "window.live": [
    "живая стрелка: ход толкает вверх, тишина плавно роняет",
    "live needle: a turn pushes it up, silence lets it fall",
  ],
  "window.5s": ["5 секунд", "5 seconds"],
  "window.10s": ["10 секунд", "10 seconds"],
  "window.1m": ["минута", "minute"],
  "window.short.live": ["живая", "live"],
  "window.short.5s": ["5 с", "5s"],
  "window.short.10s": ["10 с", "10s"],
  "window.short.1m": ["мин", "min"],
  "window.caption.live": ["прямо сейчас", "right now"],
  "window.caption.5s": ["за последние 5 секунд", "over the last 5 seconds"],
  "window.caption.10s": ["за последние 10 секунд", "over the last 10 seconds"],
  "window.caption.1m": ["за последнюю минуту", "over the last minute"],
  "window.picker": ["режим стрелки", "needle mode"],

  "status.permission": ["ждёт разрешения", "awaiting permission"],
  "status.working": ["работает", "working"],
  "status.answered": ["ждёт вас", "waiting for you"],
  "status.idle": ["простаивает", "idle"],
  "status.done": ["закончилась", "finished"],

  // Расшифровка статусов: подсказка "?" рядом с вкладками и тултип каждой точки.
  "status.hint.permission": [
    "запрошен инструмент, ответа нет: на экране висит вопрос о разрешении",
    "a tool was requested and no answer came: an allow-this question hangs on the screen",
  ],
  "status.hint.working": [
    "ход не закончен: модель думает или гоняет инструменты",
    "the turn is unfinished: the model is thinking or driving tools",
  ],
  "status.hint.answered": [
    "модель ответила, слово за вами",
    "the model has answered, the move is yours",
  ],
  "status.hint.idle": [
    "тишина дольше нескольких минут, но процесс сессии жив",
    "silence for more than a few minutes, but the session process is alive",
  ],
  "status.hint.done": [
    "процесса нет: в эту сессию больше ничего не запишется",
    "no process left: nothing more will be written into this session",
  ],
  "status.help.open": ["что значат статусы", "what the statuses mean"],
  "status.help.title": ["кого сессия ждёт прямо сейчас", "whom the session is waiting for"],
  "status.help.note": [
    "Долгий инструмент и висящий вопрос выглядят в транскрипте одинаково: их разводят по процессам сессии, а где включена телеметрия - по событию о решении.",
    "A long tool and a hanging question look the same in the transcript: they are told apart by the session processes, and where telemetry is on, by the decision event.",
  ],

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
  "widget.otel": ["мимо транскриптов", "off the transcript"],
  "widget.otel.note": ["данные телеметрии Claude Code", "Claude Code telemetry"],

  "otel.off": [
    "Телеметрия Claude Code выключена. С ней видно расход, которого нет в файлах истории, и сколько раз работа вставала ради подтверждения разрешения. Переменные окружения печатает команда:",
    "Claude Code telemetry is off. With it you see spending absent from the history files, and how often work stopped for a permission prompt. This command prints the environment variables:",
  ],
  "otel.hidden": ["расхода мимо истории", "spending off the record"],
  "otel.hidden.note": [
    "{tokens} токенов служебных запросов — {percent}% от всего расхода за сегодня",
    "{tokens} tokens of service requests — {percent}% of everything spent today",
  ],
  "otel.manual": ["подтверждений руками", "manual confirmations"],
  "otel.manual.note": [
    "и ещё {auto} разрешено автоматически",
    "plus {auto} allowed automatically",
  ],
  "settings.otel": ["телеметрия", "telemetry"],
  "settings.otelKeepDays": ["хранить дней", "keep days"],
  "settings.otelHint": [
    "Приём данных от Claude Code. Сама телеметрия включается его переменными окружения — их печатает команда cburn otel --env.",
    "Receiving data from Claude Code. Telemetry itself is switched on by its environment variables — run cburn otel --env to print them.",
  ],
  "otel.work.time": ["работа {time} без пауз", "worked {time}, idle excluded"],
  "otel.work.why": [
    "Активное время считает сам Claude Code: паузы, когда никто ничего не делал, в него не входят.",
    "Claude Code counts active time itself: idle stretches are excluded.",
  ],
  "otel.work.lines": ["кода +{added} / −{removed}", "code +{added} / −{removed}"],
  "otel.modes": ["режим разрешений менялся: {modes}", "permission mode switched: {modes}"],
  "otel.modes.why": [
    "Частые переходы в другой режим значат, что правила разрешений мешают работе.",
    "Frequent mode switching means the permission rules get in the way.",
  ],
  "otel.hooks": ["хуки отняли {time}: {events}", "hooks took {time}: {events}"],
  "otel.hooks.why": [
    "Хук выполняется между ходами, и в файлах истории на его месте остаётся только пауза — ожидание хука там не отличить от раздумий модели.",
    "A hook runs between turns and the history files show only a pause — waiting on a hook is indistinguishable from the model thinking.",
  ],
  "otel.internal": ["сбоев клиента: {count} ({errors})", "client failures: {count} ({errors})"],
  "otel.internal.why": [
    "Claude Code споткнулся сам: работа обрывается на середине, и потраченные на неё токены не вернуть.",
    "Claude Code failed on its own: work stops midway and the tokens spent on it are gone.",
  ],
  "otel.errors": [
    "запросов сорвалось: {count} ({statuses})",
    "failed requests: {count} ({statuses})",
  ],
  "otel.errors.why": [
    "В файлах истории неудавшихся запросов нет — там виден только тот ответ, который в итоге пришёл, поэтому повторы после 429 и 529 незаметны.",
    "The history files omit failed requests — only the reply that eventually arrived is there, so retries after 429 and 529 stay invisible.",
  ],

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

  "gauge.rate": ["ток/мин", "tok/min"],
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

  "today.advisor": [
    "советчик обошёлся в {cost} за {ticks} разбор(а/ов) — {share}% дневного расхода",
    "the advisor cost {cost} over {ticks} run(s) — {share}% of today's spending",
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
  "advice.title": ["советы", "advice"],
  "advice.empty": [
    "разборов ещё не было — советчик тикает раз в час, когда включён",
    "no runs yet — the analyzer ticks hourly while enabled",
  ],
  "advice.noneInRun": [
    "в этом разборе советов не нашлось",
    "this run produced no advice",
  ],
  "advice.run": ["разобрать сейчас", "analyse now"],
  "advice.running": ["разбираю…", "analysing…"],
  "advice.confirm": [
    "Такт советчика стоит около $0.08 и разберёт последние сутки. Запустить?",
    "An analyzer tick costs about $0.08 and covers the last day. Run it?",
  ],
  "advice.confirmYes": ["запустить", "run"],
  "advice.cancel": ["отмена", "cancel"],
  "advice.done": ["готово: советов {count}, такт стоил {cost}", "done: {count} advice, tick cost {cost}"],
  "advice.spent": ["разборов {runs}, потрачено {cost}", "{runs} runs, {cost} spent"],
  "advice.kind.manual": ["вручную", "manual"],
  "advice.kind.hourly": ["часовой", "hourly"],
  "advice.kind.weekly": ["недельный", "weekly"],
  "advice.status.accepted": ["принят", "accepted"],
  "advice.status.rejected": ["отклонён", "rejected"],
  "advice.tabs": ["важность", "severity"],
  "advice.tab.all": ["все", "all"],
  "advice.projects": ["проект", "project"],
  "advice.project.all": ["все проекты", "all projects"],
  "advice.project.none": ["без проекта", "no project"],
  "advice.group.crit": ["горит", "burning"],
  "advice.group.warn": ["утечки", "leaks"],
  "advice.group.info": ["к сведению", "worth knowing"],
  "advice.severity.info": ["к сведению", "info"],
  "advice.severity.warn": ["утечка", "warning"],
  "advice.severity.crit": ["горит", "critical"],
  "advice.action": ["что сделать:", "what to do:"],
  "advice.evidence": ["на основании:", "based on:"],
  "advice.number": ["совет {number}", "tip {number}"],
  "advice.collapse": ["свернуть совет", "collapse the tip"],
  "advice.expand": ["развернуть совет", "expand the tip"],
  "advice.prompts.all": ["показать все промты ({count})", "show all prompts ({count})"],
  "advice.prompts.less": ["свернуть промты", "collapse the prompts"],
  "advice.prompts.empty": [
    "лог промтов пуст: сессия проиндексирована до того, как он появился - нужен cburn reindex",
    "no prompt log: the session was indexed before it existed - run cburn reindex",
  ],
  "advice.apply": ["применить", "apply"],
  "advice.act.title": ["что изменится", "what changes"],
  "advice.act.prompt": ["последний запрос", "the last prompt"],
  "advice.act.step": ["на чём стоит", "the step it stands on"],
  "advice.act.confirm": ["применить", "apply"],
  "advice.act.rollback": ["откатить", "roll back"],
  "advice.act.session": ["сессия", "session"],
  "advice.act.kind.close_session": ["закрыть сессию", "close the session"],
  "advice.act.kind.allow_permission": ["добавить разрешение", "add a permission rule"],
  "advice.act.kind.disable_hook": ["убрать хук", "remove the hook"],
  "advice.act.kind.disable_plugin": ["выключить плагин", "switch the plugin off"],
  "advice.act.status.pending": ["закроем в паузе", "closing in a pause"],
  "advice.act.status.applied": ["применено", "applied"],
  "advice.act.status.rolled_back": ["откачено", "rolled back"],
  "advice.act.status.failed": ["не удалось", "failed"],
  "advice.act.note.sigterm": [
    "Claude Code выходит сразу по сигналу, поэтому хуки SessionEnd не отработают",
    "Claude Code exits on the signal at once, so the SessionEnd hooks will not run",
  ],
  "advice.act.note.waits_for_idle": [
    "сейчас идёт шаг - сигнал уйдёт в первую же паузу между шагами",
    "a step is running - the signal goes out in the first pause between steps",
  ],
  "advice.act.note.not_live": [
    "процесса уже нет: карточка просто уедет с дашборда",
    "the process is gone: the card is simply removed from the dashboard",
  ],
  "advice.act.note.restart_needed": [
    "работающие сессии живут со старыми настройками, новые подхватят изменение при запуске",
    "running sessions keep the old settings; new ones pick the change up at start",
  ],
  "advice.act.error.stale": [
    "файл изменился, пока вы смотрели дифф - откройте заново",
    "the file changed while you were looking - open the diff again",
  ],
  "advice.act.error.changed_since": [
    "после нас файл правили: откат затёр бы чужое изменение",
    "the file was edited after us: a rollback would throw that change away",
  ],
  "advice.act.error.no_change": ["менять нечего, всё уже так", "nothing to change, it is already so"],
  "advice.act.error.not_found": ["не нашлось того, что нужно изменить", "the thing to change was not found"],
  "advice.act.error.no_project": ["проект не найден", "the project was not found"],
  "advice.act.error.unknown_act": ["такое действие не выполняется", "this action is not carried out"],
  "advice.act.error.unreadable": ["файл настроек не разобрать", "the settings file cannot be parsed"],
  "advice.act.error.write_failed": ["записать не удалось", "the write failed"],
  "advice.act.error.no_rollback": ["это уже не откатить", "this cannot be rolled back"],
  "advice.act.error.already_rolled_back": ["уже откачено", "already rolled back"],
  "advice.act.error.disabled": [
    "применение выключено в настройках (actions.enabled)",
    "carrying tips out is switched off in the settings (actions.enabled)",
  ],
  "advice.accept": ["принять", "accept"],
  "advice.reject": ["отклонить", "reject"],
  "advice.back": ["вернуть", "undo"],
  "advice.rejectedNote": [
    "не придёт снова: уедет в следующий такт пометкой «не повторять»",
    "won't come back: the next tick is told not to repeat it",
  ],

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

  // --- Форматирование чисел и времени (format.ts) ---
  "format.decimal": [",", "."],
  "format.thousand": ["тыс", "K"],
  "format.million": ["млн", "M"],
  "format.billion": ["млрд", "B"],
  "format.recomputed": ["пересчитано", "recomputed"],
  "format.noData": ["данных за период нет", "no data for the period"],
  "format.lastData": ["последние данные", "last data"],
  "format.justNow": ["только что", "just now"],
  "format.secondsAgo": ["с назад", "s ago"],
  "format.minutesAgo": ["мин назад", "min ago"],
  "format.hoursAgo": ["ч назад", "h ago"],
  "format.daysAgo": ["дн назад", "d ago"],
  "format.minutes": ["мин", "m"],
  "format.hours": ["ч", "h"],
  "format.seconds": ["с", "s"],

  // --- Окна лимитов подписки: сервер отдаёт kind, подпись живёт здесь ---
  "limit.session": ["текущая сессия", "current session"],
  "limit.weekly_all": ["неделя, все модели", "week, all models"],
  "limit.weekly_scoped": ["неделя, модель", "week, model"],
  "limit.scoped": ["{window}: {model}", "{window}: {model}"],

  // --- Ошибки запросов к API: бросаются ключом, переводятся при показе ---
  "error.sessionClose": ["не удалось закрыть сессию: {status}", "could not close the session: {status}"],
  "error.sessionHide": ["не удалось убрать сессию: {status}", "could not hide the session: {status}"],
  "error.limitsRefresh": ["не удалось обновить лимиты: {status}", "could not refresh the limits: {status}"],
  "error.configRead": ["не удалось прочитать настройки: {status}", "could not read the settings: {status}"],
  "error.adviceStatus": ["не удалось сохранить статус: {status}", "could not save the status: {status}"],
  "error.overviewRefresh": ["не удалось обновить обзор: {status}", "could not refresh the overview: {status}"],
  "error.request": ["ошибка {status}", "error {status}"],

  // --- Граница ошибок: рисуется выше провайдера языка ---
  "boundary.title": ["дашборд не отрисовался: {message}", "the dashboard failed to render: {message}"],
  "boundary.hint": [
    "если сервер запущен давно, а фронт пересобран — перезапустите cburn serve",
    "if the server has been running for a while and the frontend was rebuilt, restart cburn serve",
  ],

  // --- Язык ответов советчика (analyzer.language) ---
  "settings.analyzer.language": ["язык советов", "advice language"],
  "settings.analyzer.language.note": [
    "промпт всегда английский, ответ приходит на выбранном языке",
    "the prompt is always English, the answer comes in the chosen language",
  ],
  "settings.lang.ru": ["русский", "Russian"],
  "settings.lang.en": ["английский", "English"],
};

/** The default language is English, like everything else outside the dictionary;
 *  only an explicit choice in the masthead switches to Russian. */
export function detect(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(RENAMED_KEY);
  if (saved === "ru" || saved === "en") return saved;
  return "en";
}

/** Translation outside React - where there are no hooks (constant tables, formatting). */
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
