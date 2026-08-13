// Единственный канал к бэкенду: HTTP и WebSocket на localhost. Прямых обращений
// к файловой системе у фронта нет — иначе обёртка Tauri на M5 потребовала бы
// переделки (см. CLAUDE.md, инварианты).

import { useCallback, useEffect, useRef, useState } from "react";

export type Usage = {
  turns: number;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_write_5m: number;
  cache_write_1h: number;
  cache_write: number;
  cost_usd: number;
  tokens: number;
};

export type BurnRate = {
  tokens_per_min: number;
  output_per_min: number;
  cost_per_hour: number;
  turns: number;
  sessions: number;
  window_seconds: number;
  usage: Usage;
};

export type LiveSession = {
  id: string;
  project: string | null;
  root_path: string | null;
  last_at: string | null;
  started_at: string | null;
  turns: number;
  tokens_out: number;
  last_context: number;
  first_prompt: string | null;
  last_prompt: string | null;
  title: string | null;
  title_source: string | null;
  status: SessionStatus;
  output_recent: number;
};

export type SessionStatus = "working" | "permission" | "answered" | "idle" | "done";

export type CloseResult = {
  session_id: string;
  stopped: boolean;
  pid: number | null;
  note: string | null;
};

/** Закрыть сессию: завершить её процесс и убрать с дашборда. */
export async function closeSession(id: string): Promise<CloseResult> {
  const response = await fetch(`api/sessions/${encodeURIComponent(id)}/close`, { method: "POST" });
  if (!response.ok) throw new Error(`не удалось закрыть сессию: ${response.status}`);
  return response.json();
}

/** Убрать сессию с дашборда, не трогая процесс. */
export async function hideSession(id: string): Promise<void> {
  const response = await fetch(`api/sessions/${encodeURIComponent(id)}/hide`, { method: "POST" });
  if (!response.ok) throw new Error(`не удалось убрать сессию: ${response.status}`);
}

export type Turn = {
  message_id: string;
  session_id: string;
  ts: string;
  model: string | null;
  output_tokens: number;
  input_tokens: number;
  cache_read: number;
  cache_write: number;
  context_estimate: number;
  is_sidechain: number;
  project: string | null;
  tools: string | null;
};

export type Bucket = { at: string; turns: number; tokens: number; output_tokens: number };

export type ModelShare = { model: string; turns: number; output_tokens: number; tokens: number };

export type ToolProfile = {
  tools: Array<{ tool: string; calls: number }>;
  tools_total: number;
  bash_commands: Array<{ command: string; calls: number }>;
};

export type IdleTurns = {
  turns: number;
  cache_read: number;
  output_tokens: number;
  share: number;
  max_output: number;
  min_context: number;
};

export type Limits = {
  approximate: boolean;
  window_hours: number;
  started_at: string | null;
  resets_at: string | null;
  usage: Usage | null;
  week: Usage;
};

export type PlanLimit = {
  kind: string;
  label: string;
  percent: number;
  resets_at: string | null;
  severity: string | null;
  is_active: boolean;
};

export type Plan = {
  source: "api" | "cache" | "none";
  fetched_at: number | null;
  plan: string | null;
  tier: string | null;
  limits: PlanLimit[];
  error: string | null;
};

/** Время самых свежих данных по срезам: обзор пересчитывается каждую секунду,
 *  а вот события в нём появляются, только когда что-то происходит. */
export type Stamps = {
  last_turn: string | null;
  today_turn: string | null;
  tool_call: string | null;
  idle_turn: string | null;
};

export type Overview = {
  now: string;
  burn: Record<string, BurnRate>;
  today: Usage;
  live_sessions: LiveSession[];
  top_sessions: Array<{
    id: string;
    project: string | null;
    turns: number;
    tokens: number;
    output_tokens: number;
    last_context: number;
    first_prompt: string | null;
    title: string | null;
  }>;
  totals: { sessions: number; turns: number; projects: number; last_turn_at: string | null };
  recent_turns: Turn[];
  live_limit: number;
  models: ModelShare[];
  tools: ToolProfile;
  idle: IdleTurns;
  limits: Limits;
  plan: Plan;
  series: Bucket[];
  series_bucket_seconds: number;
  /** Может не прийти: собранный фронт обновляется отдельно от процесса сервера. */
  stamps?: Stamps;
  pending_sessions: string[];
};

/** Спросить лимиты подписки немедленно: сам обзор их кэширует на пять минут. */
export async function refreshPlan(): Promise<void> {
  const response = await fetch("api/plan/refresh", { method: "POST" });
  if (!response.ok) throw new Error(`не удалось обновить лимиты: ${response.status}`);
}

export type Connection = "connecting" | "live" | "offline";

const RECONNECT_DELAY = 2000;

export type OverviewFeed = {
  data: Overview | null;
  connection: Connection;
  updatedAt: number;
  refresh: () => Promise<void>;
};

export type SessionRow = {
  id: string;
  project: string | null;
  root_path: string | null;
  title: string | null;
  first_prompt: string | null;
  last_prompt: string | null;
  started_at: string | null;
  last_at: string | null;
  turns: number;
  tokens: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  cost_usd: number;
  last_context: number;
  parent_session_id: string | null;
  children: number;
  sidechain_turns: number;
  status: SessionStatus;
  /** Расход по равным долям жизни сессии — столбики спарклайна. */
  spark: number[];
};

export type ProjectRow = { slug: string; name: string; root_path: string | null; sessions: number };

export type SessionsPage = { sessions: SessionRow[]; projects: ProjectRow[] };

/** Список сессий с фильтрами (экран «Сессии», задача C1).
 *
 *  Отдельным запросом, а не через WebSocket: экран смотрят подолгу и редко, а
 *  обзор летит каждую секунду всем подписчикам. */
export function useSessions(filters: { project: string; status: string; period: string }): {
  data: SessionsPage | null;
  error: boolean;
  reload: () => Promise<void>;
} {
  const [data, setData] = useState<SessionsPage | null>(null);
  const [error, setError] = useState(false);
  const { project, status, period } = filters;

  const reload = useCallback(async () => {
    const query = new URLSearchParams({ period });
    if (project) query.set("project", project);
    if (status) query.set("status", status);
    try {
      const response = await fetch(`api/sessions?${query}`);
      if (!response.ok) throw new Error(String(response.status));
      setData(await response.json());
      setError(false);
    } catch {
      setError(true);
    }
  }, [project, status, period]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, reload };
}

export type SessionTurn = {
  message_id: string;
  ts: string;
  model: string | null;
  output_tokens: number;
  input_tokens: number;
  cache_read: number;
  cache_write: number;
  context_estimate: number;
  cost_usd: number;
  is_sidechain: number;
  is_idle: number;
  tools: string | null;
};

export type SessionEvent = { ts: string; kind: "compact" | "fork"; session_id?: string };

export type SessionDetails = {
  session: {
    session_id: string;
    project: string | null;
    root_path: string | null;
    title: string | null;
    first_prompt: string | null;
    started_at: string | null;
    last_at: string | null;
    turns: number;
    sidechain_turns: number;
    sidechain_cost_usd: number;
    output_tokens: number;
    cache_read: number;
    cache_write: number;
    cost_usd: number;
    last_context: number;
    parent_session_id: string | null;
  };
  models: Array<{ model: string; turns: number; output_tokens: number }>;
  tools: Array<{ tool: string; calls: number }>;
  chain: { sessions: string[]; turns: number; tokens: number; cost_usd: number };
  turns: SessionTurn[];
  events: SessionEvent[];
};

/** Одна сессия целиком: суммы, ходы и вехи (экран «Сессия», задача C2). */
export function useSession(id: string): {
  data: SessionDetails | null;
  error: boolean;
  reload: () => Promise<void>;
} {
  const [data, setData] = useState<SessionDetails | null>(null);
  const [error, setError] = useState(false);

  const reload = useCallback(async () => {
    try {
      const response = await fetch(`api/sessions/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error(String(response.status));
      setData(await response.json());
      setError(false);
    } catch {
      setError(true);
    }
  }, [id]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, reload };
}

export type ModelPrice = {
  input: number;
  output: number;
  cache_write_5m: number;
  cache_write_1h: number;
  cache_read: number;
};

export type Config = {
  watch: { include: string[]; exclude: string[] };
  thresholds: {
    context_warn: number;
    context_crit: number;
    idle_run: number;
    burn_rate_warn_per_min: number;
  };
  analyzer: {
    enabled: boolean;
    interval_minutes: number;
    model: string;
    weekly_deep_model: string;
    allow_snippets: boolean;
  };
  telegram: {
    mode: string;
    bridge_url: string;
    bot_token: string;
    chat_id: string;
    daily_summary_at: string;
  };
  server: { port: number };
  prices: Record<string, ModelPrice>;
};

/** Настройки как они лежат в файле (экран «Настройки», задача C3). */
export async function loadConfig(): Promise<{ config: Config; path: string }> {
  const response = await fetch("api/config");
  if (!response.ok) throw new Error(`не удалось прочитать настройки: ${response.status}`);
  return response.json();
}

/** Записать настройки. Ошибки проверки приходят текстом от бэкенда: он владеет
 *  файлом, и повторять правила на фронте значит завести вторую их версию. */
export async function saveConfig(config: Config): Promise<{ config: Config }> {
  const response = await fetch("api/config", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ config }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail ?? `ошибка ${response.status}`);
  return payload;
}

/** Обзор, который сам себя обновляет: первый кадр и пуши приходят по WebSocket. */
export function useOverview(): OverviewFeed {
  const [data, setData] = useState<Overview | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [updatedAt, setUpdatedAt] = useState(0);
  const timer = useRef<number>(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let stopped = false;

    const open = () => {
      if (stopped) return;
      const url = new URL("ws", window.location.href);
      url.protocol = url.protocol.replace("http", "ws");
      socket = new WebSocket(url);

      socket.onopen = () => setConnection("live");
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.type === "overview") {
          setData(message.data);
          setUpdatedAt(Date.now());
        }
      };
      socket.onclose = () => {
        setConnection("offline");
        if (!stopped) timer.current = window.setTimeout(open, RECONNECT_DELAY);
      };
      socket.onerror = () => socket?.close();
    };

    open();
    return () => {
      stopped = true;
      window.clearTimeout(timer.current);
      socket?.close();
    };
  }, []);

  // Кнопка обновления в виджете: тикер и так шлёт обзор раз в секунду, но при
  // разорванном сокете это единственный способ увидеть свежие числа.
  const refresh = useCallback(async () => {
    const response = await fetch("api/overview");
    if (!response.ok) throw new Error(`не удалось обновить обзор: ${response.status}`);
    setData(await response.json());
    setUpdatedAt(Date.now());
  }, []);

  return { data, connection, updatedAt, refresh };
}
