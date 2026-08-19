// The "?" of the interface: a button that opens a cheat sheet right beside what it
// explains. The shell is shared - the statuses of the sessions and the marks of the
// masthead are two lists in one and the same box.

import { useEffect, useRef, useState, type ReactNode } from "react";

import { useLang } from "../lib/i18n";

export function HelpPopover({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="status-help" ref={box}>
      <button
        className="status-help-button"
        aria-label={label}
        aria-expanded={open}
        title={label}
        onClick={() => setOpen((shown) => !shown)}
      >
        ?
      </button>
      {open && (
        <div className="popover status-help-menu" role="dialog" aria-label={label}>
          {children}
        </div>
      )}
    </div>
  );
}

/** The marks of the masthead: the link to the server, the age of the last update and the
 *  turn in flight. The last one is not decoration - it says the readings are behind. */
export function LiveHelp() {
  const { t } = useLang();
  const rows: Array<{ key: string; mark: ReactNode; name: string }> = [
    { key: "live", mark: <span className="dot dot-live" aria-hidden="true" />, name: t("app.live") },
    {
      key: "offline",
      mark: <span className="dot dot-offline" aria-hidden="true" />,
      name: t("app.offline"),
    },
    { key: "connecting", mark: <span className="dot" aria-hidden="true" />, name: t("app.connecting") },
    { key: "ago", mark: <span aria-hidden="true" />, name: t("format.justNow") },
    {
      key: "working",
      mark: <span className="working-pulse" aria-hidden="true" />,
      name: t("app.working"),
    },
  ];

  return (
    <HelpPopover label={t("live.help.open")}>
      <p className="status-help-title">{t("live.help.title")}</p>
      <ul className="status-help-list">
        {rows.map((row) => (
          <li key={row.key}>
            {row.mark}
            <span className="status-help-name">{row.name}</span>
            <span className="status-help-text">{t(`live.help.hint.${row.key}`)}</span>
          </li>
        ))}
      </ul>
      <p className="popover-warning">{t("live.help.note")}</p>
    </HelpPopover>
  );
}
