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
