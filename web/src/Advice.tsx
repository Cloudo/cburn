// The "Advice" screen (task D6): analysis history with statuses. A dismissed tip
// travels into the next tick's prompt marked "do not repeat" - that is what the
// statuses are for, otherwise the same thing would come round again.

import { useEffect, useMemo, useState } from "react";

import { clockTime, sinceLabel, toolLabel, usd } from "./format";
import { useLang } from "./i18n";
import {
  ActFailed,
  applyAct,
  loadPrompts,
  planAct,
  rollbackPatch,
  runAdvice,
  setAdviceStatus,
  useAdvice,
  type ActPlan,
  type AdviceItem,
  type AdviceRun,
  type AdviceSession,
  type Prompt,
} from "./api";

const SEVERITY_ORDER = ["crit", "warn", "info"] as const;

type Severity = (typeof SEVERITY_ORDER)[number];

//: The bucket for a tip that names no session: it belongs to no project.
const NO_PROJECT = "\u0000none";

/** Projects a tip touches; a tip about the machine as a whole names none. */
function projectsOf(item: AdviceItem): string[] {
  return item.projects.length ? item.projects : [NO_PROJECT];
}

export function Advice() {
  const { t } = useLang();
  const { data, error, reload } = useAdvice();
  const [running, setRunning] = useState(false);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [project, setProject] = useState("");

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
  const items = useMemo(() => flatten(data?.runs ?? []), [data]);

  // Two facets over one list: the tab picks the importance, the buttons narrow it down to a
  // project. Each facet counts through the other one, so the numbers on the buttons say how
  // much will be left after the click rather than how much there is in total.
  const chosenSeverity = items.some((item) => item.severity === severity) ? severity : "all";
  const inTab =
    chosenSeverity === "all"
      ? items
      : items.filter((item) => item.severity === chosenSeverity);

  // The project list stays the same while switching tabs - only the counts change, and an
  // empty button is disabled: a list of projects jumping about is harder to aim at.
  const projects = useMemo(() => {
    const totals = new Map<string, number>();
    for (const item of items) {
      for (const name of projectsOf(item)) totals.set(name, (totals.get(name) ?? 0) + 1);
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([name]) => name);
  }, [items]);
  const inTabByProject = new Map<string, number>();
  for (const item of inTab) {
    for (const name of projectsOf(item)) {
      inTabByProject.set(name, (inTabByProject.get(name) ?? 0) + 1);
    }
  }

  const chosenProject = inTabByProject.get(project) ? project : "";
  const shown = chosenProject
    ? inTab.filter((item) => projectsOf(item).includes(chosenProject))
    : inTab;
  const scoped = chosenProject
    ? items.filter((item) => projectsOf(item).includes(chosenProject))
    : items;
  const groups = chosenSeverity === "all" ? SEVERITY_ORDER : [chosenSeverity];

  // A card is a tip, and on screen that was only implied - a heading in bold looks like
  // any other panel. The number runs through the whole shown list, across the groups: it
  // is a name for a tip ("the third one"), not a place inside its severity.
  const numbers = new Map(
    groups
      .flatMap((key) => shown.filter((item) => item.severity === key))
      .map((item, index) => [item.id, index + 1]),
  );

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
      {runs.length > 0 && !items.length && <p className="hint">{t("advice.noneInRun")}</p>}

      {items.length > 0 && (
        <div className="advice-filters">
          <div className="tabs" role="tablist" aria-label={t("advice.tabs")}>
            <button
              role="tab"
              aria-selected={chosenSeverity === "all"}
              className={chosenSeverity === "all" ? "tab tab-on" : "tab"}
              onClick={() => setSeverity("all")}
            >
              {t("advice.tab.all")}
              <span className="tab-count">{scoped.length}</span>
            </button>
            {SEVERITY_ORDER.map((key) => {
              const count = scoped.filter((item) => item.severity === key).length;
              return (
                <button
                  key={key}
                  role="tab"
                  aria-selected={chosenSeverity === key}
                  disabled={count === 0}
                  className={chosenSeverity === key ? "tab tab-on" : "tab"}
                  onClick={() => setSeverity(key)}
                >
                  {t(`advice.group.${key}`)}
                  <span className={`tab-count tab-count-${key}`}>{count}</span>
                </button>
              );
            })}
          </div>

          {/* A project cut makes sense only when there is more than one of them. */}
          {projects.length > 1 && (
            <div className="filter-tabs" role="tablist" aria-label={t("advice.projects")}>
              <button
                role="tab"
                aria-selected={chosenProject === ""}
                className={chosenProject === "" ? "filter-tab filter-tab-on" : "filter-tab"}
                onClick={() => setProject("")}
              >
                {t("advice.project.all")}
                <span className="filter-tab-count">{inTab.length}</span>
              </button>
              {projects.map((name) => {
                const count = inTabByProject.get(name) ?? 0;
                return (
                  <button
                    key={name}
                    role="tab"
                    aria-selected={chosenProject === name}
                    disabled={count === 0}
                    className={chosenProject === name ? "filter-tab filter-tab-on" : "filter-tab"}
                    onClick={() => setProject(name)}
                  >
                    {name === NO_PROJECT ? t("advice.project.none") : name}
                    <span className="filter-tab-count">{count}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* The order is by importance rather than by analyses: what burns comes first.
          The analysis a tip belongs to is signed on the card itself. */}
      {groups.map((key) => {
        const group = shown.filter((item) => item.severity === key);
        if (!group.length) return null;
        return (
          <div key={key} className="advice-group">
            <h3 className={`advice-group-head advice-group-${key}`}>
              {t(`advice.group.${key}`)} <span className="hint">{group.length}</span>
            </h3>
            {group.map((item) => (
              <Item key={item.id} item={item} number={numbers.get(item.id)} onChange={reload} />
            ))}
          </div>
        );
      })}
    </section>
  );
}

/** Every tip of every analysis in one list: each carries its analysis along.
 *  Dismissed ones sink to the bottom of their group - the decision is already made. */
function flatten(runs: AdviceRun[]): Array<AdviceItem & { run: AdviceRun }> {
  const items = runs.flatMap((run) => run.items.map((item) => ({ ...item, run })));
  const sunk = (item: AdviceItem) => (item.status === "rejected" ? 1 : 0);
  return items.sort(
    (a, b) => sunk(a) - sunk(b) || b.run.ts.localeCompare(a.run.ts) || a.id - b.id,
  );
}

function Item({
  item,
  number,
  onChange,
}: {
  item: AdviceItem & { run: AdviceRun };
  number: number | undefined;
  onChange: () => Promise<void>;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);

  const change = async (status: AdviceItem["status"]) => {
    setBusy(true);
    try {
      await setAdviceStatus(item.id, status);
      await onChange();
    } finally {
      setBusy(false);
    }
  };

  // Selecting the title with the mouse ends in a click on the head as well, and folding
  // the card away in the middle of a copy would be the opposite of what was asked.
  const toggle = () => {
    if (window.getSelection()?.toString()) return;
    setOpen(!open);
  };

  return (
    <article
      className={`advice-item advice-item-${item.severity} advice-item-${item.status}${
        open ? "" : " advice-item-folded"
      }`}
    >
      <div className="advice-item-head" onClick={toggle}>
        <span className={`advice-number advice-number-${item.severity}`}>
          {t("advice.number", { number: number ?? 0 })}
        </span>
        <h4>{item.title}</h4>
        {item.status !== "new" && (
          <span className={`advice-status advice-status-${item.status}`}>
            {t(`advice.status.${item.status}`)}
          </span>
        )}
        <span className="advice-origin">
          {clockTime(item.run.ts)} · {t(`advice.kind.${item.run.kind}`)} · {usd(item.run.cost_usd)}
        </span>
        {/* The whole head folds the card, and the chevron is the same thing for the
            keyboard: a click target of one's own, with a state to announce. */}
        <button
          className="advice-fold"
          aria-expanded={open}
          aria-label={t(open ? "advice.collapse" : "advice.expand")}
          onClick={(event) => {
            event.stopPropagation();
            setOpen(!open);
          }}
        >
          <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
            <path
              d="M4 6l4 4 4-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
      {/* Folded, the card keeps one line: what the tip proposes to do. The title says
          what is wrong, and that alone is not enough to pick a card out of seventeen. */}
      {!open && item.action && <p className="advice-folded-line">{item.action}</p>}
      {open && (
        <>
          {item.sessions.length > 0 && (
            <div className="advice-sessions">
              {item.sessions.map((session) => (
                <Session key={session.id} session={session} />
              ))}
            </div>
          )}
          {item.detail && <p className="advice-detail">{item.detail}</p>}
          {item.action && (
            <p className="advice-action">
              <span className="advice-label">{t("advice.action")}</span> {item.action}
            </p>
          )}
          {/* A tip without support in numbers never reaches the screen - the advisor
              drops it. */}
          <p className="advice-evidence">
            <span className="advice-label">{t("advice.evidence")}</span> {item.evidence}
          </p>
          {item.act && <Act item={item} onChange={onChange} />}
          <div className="advice-buttons">
            {item.status !== "accepted" && (
              <button className="advice-accept" disabled={busy} onClick={() => change("accepted")}>
                {t("advice.accept")}
              </button>
            )}
            {item.status !== "rejected" && (
              <button className="advice-reject" disabled={busy} onClick={() => change("rejected")}>
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
        </>
      )}
    </article>
  );
}

/** A session a tip names, with the log of what was asked of it (task C7).
 *  Two prompts are shown by default - the first says what the session was started for,
 *  the last says what it has come to. The middle is loaded by a click. */
function Session({ session }: { session: AdviceSession }) {
  const { t } = useLang();
  const [whole, setWhole] = useState<Prompt[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const shown = open && whole ? whole : session.prompts;
  const hidden = session.prompt_count - session.prompts.length;

  const expand = async () => {
    if (whole) {
      setOpen(true);
      return;
    }
    setBusy(true);
    try {
      setWhole(await loadPrompts(session.id));
      setOpen(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="advice-session-card">
      <a className="advice-session" href={`#/session/${session.id}`}>
        {session.title ?? session.id.slice(0, 8)}
        <span className="advice-session-project">{session.project ?? "-"}</span>
      </a>
      {shown.length > 0 && (
        <ol className="prompt-log">
          {shown.map((entry, index) => (
            <li key={`${entry.ts}-${index}`} className="prompt-log-row">
              <span className="prompt-log-time">{clockTime(entry.ts)}</span>
              <span className="prompt-log-text">{entry.text}</span>
            </li>
          ))}
        </ol>
      )}
      {/* The log fills up as transcripts are read: for sessions indexed before it
          existed there is nothing to show until a reindex. */}
      {session.prompt_count === 0 && <p className="hint">{t("advice.prompts.empty")}</p>}
      {hidden > 0 && !open && (
        <button className="prompt-log-more" disabled={busy} onClick={expand}>
          {t("advice.prompts.all", { count: session.prompt_count })}
        </button>
      )}
      {open && (
        <button className="prompt-log-more" onClick={() => setOpen(false)}>
          {t("advice.prompts.less")}
        </button>
      )}
    </div>
  );
}

/** Carrying a tip out (task D7): the plan, the diff, the confirmation, the way back.
 *  The backend writes nothing until the confirmation comes back with the hash of the
 *  file the diff was built from - so what is applied is exactly what was seen. */
function Act({ item, onChange }: { item: AdviceItem; onChange: () => Promise<void> }) {
  const { t } = useLang();
  const [plan, setPlan] = useState<ActPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const patch = item.patch;
  const kind = item.act?.type;

  // The reason comes from the backend as a dictionary key: the words are ours.
  const reasonOf = (error: unknown) =>
    error instanceof ActFailed ? t(`advice.act.error.${error.reason}`) : String(error);

  const guard = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setProblem("");
    try {
      await work();
    } catch (error) {
      setProblem(reasonOf(error));
    } finally {
      setBusy(false);
    }
  };

  const preview = () => guard(async () => setPlan(await planAct(item.id)));
  const confirm = () =>
    guard(async () => {
      await applyAct(item.id, plan?.hash ?? "");
      setPlan(null);
      await onChange();
    });
  const undo = () =>
    guard(async () => {
      await rollbackPatch(patch?.id ?? 0);
      await onChange();
    });

  const carried = patch && patch.status !== "rolled_back";

  return (
    <div className="act">
      <div className="act-line">
        {carried ? (
          <>
            <span className={`act-badge act-badge-${patch.status}`}>
              {t(`advice.act.status.${patch.status}`)}
            </span>
            {patch.status !== "failed" && (
              <button className="act-undo" disabled={busy} onClick={undo}>
                {t("advice.act.rollback")}
              </button>
            )}
          </>
        ) : (
          <button className="act-apply" disabled={busy} onClick={preview}>
            {t("advice.apply")}
            <span className="act-kind">{t(`advice.act.kind.${kind}`)}</span>
          </button>
        )}
        {problem && <span className="act-problem">{problem}</span>}
      </div>

      {plan && (
        <div className="act-plan" role="dialog" aria-label={t("advice.act.title")}>
          <div className="act-plan-head">
            <span className="act-kind">{t(`advice.act.kind.${plan.kind}`)}</span>
            <code className="act-target">
              {plan.details.path ?? plan.details.session_id?.slice(0, 8)}
            </code>
            {plan.details.title && <span className="act-session">{plan.details.title}</span>}
            {plan.details.project && <span className="hint">{plan.details.project}</span>}
            {plan.details.status && (
              <span className="hint">{t(`status.${plan.details.status}`)}</span>
            )}
          </div>
          {plan.kind === "close_session" && <What details={plan.details} />}
          {plan.diff && <Diff text={plan.diff} />}
          {plan.notes.map((note) => (
            <p key={note} className="act-note">
              {t(`advice.act.note.${note}`)}
            </p>
          ))}
          <div className="popover-actions">
            <button className="popover-danger" disabled={busy} onClick={confirm}>
              {t("advice.act.confirm")}
            </button>
            <button disabled={busy} onClick={() => setPlan(null)}>
              {t("advice.cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** What a close would interrupt. A closing session has no diff to look at, and the
 *  status word alone ("a step is running") does not say which step: the answer is the
 *  last prompt and the tool the session stands on. */
function What({ details }: { details: ActPlan["details"] }) {
  const { t } = useLang();
  const { prompt, tool, since } = details;
  if (!prompt && !tool) return null;
  return (
    <dl className="act-what">
      {prompt && (
        <>
          <dt>{t("advice.act.prompt")}</dt>
          <dd className="act-prompt">{prompt}</dd>
        </>
      )}
      {tool && (
        <>
          <dt>{t("advice.act.step")}</dt>
          <dd>
            <code>{toolLabel(tool.name)}</code>
            {tool.detail && <span className="act-detail">{tool.detail}</span>}
            <span className="act-since">{sinceLabel(since ?? null)}</span>
          </dd>
        </>
      )}
    </dl>
  );
}

/** The diff of the file as it came from the backend: we colour it, we do not build it.
 *  The header lines carry the path, the rest is the change itself. */
function Diff({ text }: { text: string }) {
  return (
    <pre className="act-diff">
      {text.split("\n").map((line, index) => (
        <span key={index} className={`act-diff-${lineKind(line)}`}>
          {line}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

function lineKind(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "file";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "same";
}
