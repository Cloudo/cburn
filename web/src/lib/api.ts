// The only channel to the backend: HTTP and WebSocket on localhost. The frontend has no
// direct filesystem access - otherwise the Tauri wrapper in M5 would have demanded
// a rewrite (see CLAUDE.md, the invariants).
//
// Failures are thrown as dictionary keys rather than as ready phrases: the screen that
// catches them knows the language, and this module does not.

import { useCallback, useEffect, useRef, useState } from "react";

import { detect, translate } from "./dict";

/** A failure message: the key is translated at the moment it is thrown. */
function fail(key: string, status: number): string {
  return translate(detect(), key, { status });
}

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

/** Close a session: terminate its process and remove it from the dashboard. */
export async function closeSession(id: string): Promise<CloseResult> {
  const response = await fetch(`api/sessions/${encodeURIComponent(id)}/close`, { method: "POST" });
  if (!response.ok) throw new Error(fail("error.sessionClose", response.status));
  return response.json();
}

/** Remove a session from the dashboard without touching the process. */
export async function hideSession(id: string): Promise<void> {
  const response = await fetch(`api/sessions/${encodeURIComponent(id)}/hide`, { method: "POST" });
  if (!response.ok) throw new Error(fail("error.sessionHide", response.status));
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
  model: string | null;
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

/** The time of the freshest data per slice: the overview is recomputed every second,
 *  while events appear in it only when something happens. */
export type Stamps = {
  last_turn: string | null;
  today_turn: string | null;
  tool_call: string | null;
  idle_turn: string | null;
};

/** A slice of Claude Code telemetry: what the transcripts do not show (milestone E).
 *  `active: false` means telemetry is off and the widget explains how to switch it on. */
export type Otel = {
  active: boolean;
  last_at: string | null;
  off_transcript: {
    tokens: number;
    input_tokens: number;
    output_tokens: number;
    cache_read: number;
    cache_write: number;
    cost_usd: number;
    share: number;
    request_kinds: Array<{ source: string | null; requests: number; cost_usd: number }>;
  };
  permissions: {
    decisions: number;
    manual: number;
    auto: number;
    rejected: number;
    by_tool: Array<{ tool: string; decisions: number }>;
    /** Going into another permission mode - the same subject from the other side.
     *  It may also be missing: the field appeared later than the rest. */
    mode_switches?: Array<{ mode: string | null; switches: number }>;
  };
  /** Failed API requests: they never reach the transcript at all.
   *  Optional - the frontend may have been built before the server was restarted. */
  api?: {
    errors: number;
    by_status: Array<{ status: string; errors: number }>;
    /** Failures inside the client itself: work breaks off midway. */
    internal?: Array<{ error: string; count: number }>;
  };
  /** The time eaten by hooks: in the transcript only a pause remains of them. */
  hooks?: {
    seconds: number;
    failures: number;
    events: Array<{
      event: string;
      runs: number;
      seconds: number | null;
      slowest: number | null;
      failures: number;
    }>;
  };
  /** What came out of the spend: lines of code and active time without pauses. */
  work?: {
    lines_added: number;
    lines_removed: number;
    active_seconds: number;
    waiting_seconds: number;
    commits: number;
  };
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
  /** May be missing: the built frontend updates apart from the server process. */
  stamps?: Stamps;
  /** May be missing too - the server is older than this frontend. */
  otel?: Otel;
  /** What the advisor itself cost today (task C4). */
  advisor?: {
    ticks: number;
    cost_usd: number;
    last_at: string | null;
    by_kind: Array<{ kind: string; ticks: number; cost_usd: number }>;
  };
  pending_sessions: string[];
  /** Why the dashboard is empty, when it is: the words are built by `dict.ts`. */
  first_run: {
    kind: "ok" | "no_claude" | "no_history" | "not_indexed";
    transcripts: string | null;
  };
};

/** Ask for the subscription limits at once: the overview caches them for five minutes. */
export async function refreshPlan(): Promise<void> {
  const response = await fetch("api/plan/refresh", { method: "POST" });
  if (!response.ok) throw new Error(fail("error.limitsRefresh", response.status));
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
  /** Spend over equal slices of the session's life - the sparkline bars. */
  spark: number[];
};

export type ProjectRow = { slug: string; name: string; root_path: string | null; sessions: number };

export type SessionsPage = { sessions: SessionRow[]; projects: ProjectRow[] };

/** The session list with filters (the "Sessions" screen, task C1).
 *
 *  A separate request rather than the WebSocket: this screen is watched long and rarely,
 *  while the overview flies to every subscriber once a second. */
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
  /** Time inside tools comes from telemetry only, so the list is sometimes empty. */
  tool_times?: Array<{
    tool: string | null;
    calls: number;
    seconds: number | null;
    slowest: number | null;
    failures: number;
  }>;
  chain: { sessions: string[]; turns: number; tokens: number; cost_usd: number };
  turns: SessionTurn[];
  events: SessionEvent[];
};

/** One whole session: totals, turns and milestones (the "Session" screen, task C2). */
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
    /** May be missing: the server is older than this frontend. */
    language?: string;
  };
  /** May be missing: the server is older than this frontend. */
  otel?: { enabled: boolean; keep_days: number };
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

/** Settings exactly as they sit in the file (the "Settings" screen, task C3). */
export async function loadConfig(): Promise<{ config: Config; path: string }> {
  const response = await fetch("api/config");
  if (!response.ok) throw new Error(fail("error.configRead", response.status));
  return response.json();
}

/** Write the settings. Validation errors come as text from the backend: it owns the
 *  file, and repeating the rules on the frontend would mean a second copy of them. */
export async function saveConfig(config: Config): Promise<{ config: Config }> {
  const response = await fetch("api/config", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ config }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail ?? fail("error.request", response.status));
  return payload;
}

export type AdviceItem = {
  id: number;
  key: string;
  title: string;
  severity: "info" | "warn" | "crit";
  detail: string | null;
  action: string | null;
  evidence: string;
  status: "new" | "accepted" | "rejected";
  /** Sessions a tip refers to: expanded from the short ids. */
  sessions: AdviceSession[];
  /** Projects a tip is about: from the mentioned sessions and from the text itself. */
  projects: string[];
  /** The typed action from the closed list, if the tip has one that can be carried out. */
  act: AdviceAct | null;
  /** The patch this tip has already produced: a card shows whether it was carried out. */
  patch: AppliedPatch | null;
};

/** What the human typed, with the moment it was typed at (task C7). */
export type Prompt = { ts: string; text: string };

export type AdviceSession = {
  id: string;
  title: string | null;
  project: string | null;
  /** The ends of the prompt log: the first and the last one. The middle is asked for
   *  by a click - twenty tips must not drag every prompt of every session along. */
  prompts: Prompt[];
  prompt_count: number;
};

/** The whole prompt log of a session, in order. */
export async function loadPrompts(sessionId: string): Promise<Prompt[]> {
  const response = await fetch(`api/sessions/${encodeURIComponent(sessionId)}/prompts`);
  if (!response.ok) throw new Error(fail("error.request", response.status));
  return (await response.json()).prompts as Prompt[];
}

/** What the advisor proposes to do, in a form the machine understands (task D7). */
export type AdviceAct = {
  type: "close_session" | "allow_permission" | "disable_hook" | "disable_plugin";
  session_id?: string;
  rule?: string;
  event?: string;
  matcher?: string;
  plugin?: string;
  scope?: string;
  project?: string;
};

/** What the action would change. Built before the confirmation; nothing is written yet. */
export type ActPlan = {
  kind: AdviceAct["type"];
  /** The file to be written, or the session to be closed. */
  target: string;
  details: {
    path?: string;
    rule?: string;
    event?: string;
    matcher?: string | null;
    plugin?: string;
    session_id?: string;
    project?: string | null;
    status?: string;
    live?: boolean;
    /** What is being interrupted: the session name, its last prompt and the step it stands on. */
    title?: string | null;
    prompt?: string | null;
    since?: string | null;
    tool?: { name: string; detail: string | null } | null;
  };
  /** A unified diff of the file; empty where nothing is written. */
  diff: string;
  /** The state the plan was built from: the confirmation goes back with it. */
  hash: string;
  /** Dictionary keys for the warnings under the diff - the words are ours. */
  notes: string[];
};

export type AppliedPatch = {
  id: number;
  item_id: number | null;
  kind: string;
  status: "pending" | "applied" | "rolled_back" | "failed";
  ts: string;
  note: string | null;
};

export type AdviceRun = {
  id: number;
  ts: string;
  kind: string;
  period_start: string | null;
  period_end: string | null;
  model: string | null;
  cost_usd: number;
  max_severity: string | null;
  items: AdviceItem[];
};

/** Analysis history with statuses (the "Advice" screen, task D6). */
export function useAdvice(): {
  data: { runs: AdviceRun[] } | null;
  error: boolean;
  reload: () => Promise<void>;
} {
  const [data, setData] = useState<{ runs: AdviceRun[] } | null>(null);
  const [error, setError] = useState(false);

  const reload = useCallback(async () => {
    try {
      const response = await fetch("api/advice");
      if (!response.ok) throw new Error(String(response.status));
      setData(await response.json());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, reload };
}

export async function setAdviceStatus(id: number, status: AdviceItem["status"]): Promise<void> {
  const response = await fetch(`api/advice/items/${id}?status=${status}`, { method: "POST" });
  if (!response.ok) throw new Error(fail("error.adviceStatus", response.status));
}

/** A refusal to carry an action out. The backend sends a reason key, not a sentence:
 *  the words live in the dictionary, as everywhere else. */
export class ActFailed extends Error {
  constructor(readonly reason: string) {
    super(reason);
  }
}

async function act<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ActFailed(payload?.detail ?? String(response.status));
  return payload as T;
}

/** What the tip's action would change: the diff of the file and its hash (task D7). */
export function planAct(itemId: number): Promise<ActPlan> {
  return act<ActPlan>(`api/advice/items/${itemId}/plan`);
}

/** Carry it out. The hash is the one the diff was built from: a foreign change in
 *  between comes back as a conflict instead of overwriting it. */
export function applyAct(itemId: number, hash: string): Promise<AppliedPatch> {
  return act<AppliedPatch>(`api/advice/items/${itemId}/apply`, { hash });
}

export function rollbackPatch(patchId: number): Promise<AppliedPatch> {
  return act<AppliedPatch>(`api/patches/${patchId}/rollback`);
}

/** Analyse the period now. It costs money - called from the button only. */
export async function runAdvice(
  period: string,
): Promise<{ cost_usd: number; advice: AdviceItem[] }> {
  const response = await fetch(`api/advice/run?period=${period}`, { method: "POST" });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail ?? fail("error.request", response.status));
  return payload;
}

/** An overview that refreshes itself: the first frame and the pushes come over WebSocket. */
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

  // The refresh button in the widget: the ticker sends the overview once a second anyway,
  // but with a broken socket this is the only way to see fresh numbers.
  const refresh = useCallback(async () => {
    const response = await fetch("api/overview");
    if (!response.ok) throw new Error(fail("error.overviewRefresh", response.status));
    setData(await response.json());
    setUpdatedAt(Date.now());
  }, []);

  return { data, connection, updatedAt, refresh };
}
