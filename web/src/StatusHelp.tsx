// A statuses cheat sheet: the "?" next to the tabs opens the whole list at once, and the
// same words go into the tooltip of every dot and badge. The status is a guess about
// someone else's process, so what exactly it means has to be sayable on the spot.

import { useEffect, useRef, useState } from "react";

import { useLang } from "./i18n";
import type { SessionStatus } from "./api";

//: Statuses in order of importance: the tab that opens first is the one where something happens.
export const STATUSES: SessionStatus[] = ["permission", "working", "answered", "idle", "done"];

/** The tooltip for a dot, a badge or a tab: the name and the explanation in one line. */
export function statusTitle(t: (key: string) => string, status: string): string {
  return `${t(`status.${status}`)} - ${t(`status.hint.${status}`)}`;
}

export function StatusHelp() {
  const { t } = useLang();
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
        aria-label={t("status.help.open")}
        aria-expanded={open}
        title={t("status.help.open")}
        onClick={() => setOpen((shown) => !shown)}
      >
        ?
      </button>
      {open && (
        <div className="popover status-help-menu" role="dialog" aria-label={t("status.help.open")}>
          <p className="status-help-title">{t("status.help.title")}</p>
          <ul className="status-help-list">
            {STATUSES.map((key) => (
              <li key={key}>
                <span className={`sessions-dot sessions-dot-${key}`} aria-hidden="true" />
                <span className="status-help-name">{t(`status.${key}`)}</span>
                <span className="status-help-text">{t(`status.hint.${key}`)}</span>
              </li>
            ))}
          </ul>
          <p className="popover-warning">{t("status.help.note")}</p>
        </div>
      )}
    </div>
  );
}
