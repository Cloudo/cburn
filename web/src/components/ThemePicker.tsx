// The theme picker, borrowed in behaviour from the VS Code quick pick: a filter line, a list
// grouped into dark and light, and a live preview - the theme under the cursor is already on
// the screen, so the choice is made by looking rather than by guessing from the name. Escape
// puts back what was there, Enter keeps what is shown.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { useLang } from "../lib/i18n";
import { SYSTEM, useTheme } from "../lib/theme";
import { THEMES, themeById, type Theme } from "../lib/themes";

type Row = { id: string; name: string; theme: Theme; group: string | null };

export function ThemePicker() {
  const { t } = useLang();
  const { choice, system, setTheme, preview } = useTheme();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);

  const rows = useMemo<Row[]>(() => {
    const all: Row[] = [
      { id: SYSTEM, name: t("app.theme.system"), theme: system, group: null },
      ...THEMES.map((theme) => ({ id: theme.id, name: theme.name, theme, group: theme.kind })),
    ];
    const needle = query.trim().toLowerCase();
    return needle ? all.filter((row) => row.name.toLowerCase().includes(needle)) : all;
  }, [query, system, t]);

  // The cursor is the single source of the preview: the keyboard and the mouse both only move
  // it, and closing the list takes the preview off - that is what makes Escape a cancel.
  useEffect(() => {
    preview(open ? (rows[cursor]?.theme ?? null) : null);
  }, [open, cursor, rows, preview]);

  useEffect(() => {
    if (cursor >= rows.length) setCursor(0);
  }, [rows, cursor]);

  useEffect(() => {
    if (!open) return;
    list.current?.querySelector(`[data-index="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, cursor]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const label = choice === SYSTEM ? t("app.theme.system") : (themeById(choice)?.name ?? system.name);

  const show = () => {
    const at = THEMES.findIndex((theme) => theme.id === choice);
    setQuery("");
    setCursor(at < 0 ? 0 : at + 1);
    setOpen(true);
  };

  const close = () => {
    setOpen(false);
    button.current?.focus();
  };

  const commit = (id: string) => {
    setTheme(id);
    close();
  };

  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : rows.length - 1;
      setCursor((at) => (rows.length ? (at + step) % rows.length : 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (rows[cursor]) commit(rows[cursor].id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };

  let group: string | null = null;

  return (
    <div className="theme-picker" ref={box}>
      <button
        ref={button}
        className="theme-button"
        aria-label={t("app.theme")}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={`${t("app.theme")}: ${label}`}
        onClick={() => (open ? close() : show())}
      >
        <PaletteIcon />
      </button>

      {open && (
        <div className="popover theme-menu" role="dialog" aria-label={t("app.theme")}>
          <input
            className="theme-filter"
            autoFocus
            value={query}
            placeholder={t("app.theme.filter")}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKey}
          />
          <div className="theme-list" role="listbox" aria-label={t("app.theme")} ref={list}>
            {rows.map((row, index) => {
              const head = row.group !== group ? row.group : null;
              group = row.group;
              return (
                <div key={row.id}>
                  {head && <div className="theme-group">{t(`app.theme.${head}`)}</div>}
                  <div
                    role="option"
                    data-index={index}
                    aria-selected={row.id === choice}
                    className={index === cursor ? "theme-item theme-item-at" : "theme-item"}
                    onPointerEnter={() => setCursor(index)}
                    onClick={() => commit(row.id)}
                  >
                    <Swatch theme={row.theme} />
                    <span className="theme-name">{row.name}</span>
                    {row.id === choice && <span className="theme-check">✓</span>}
                  </div>
                </div>
              );
            })}
            {!rows.length && <div className="theme-empty">{t("app.theme.none")}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

/** Four accents on the theme's own background - the palette read before the name. */
function Swatch({ theme }: { theme: Theme }) {
  const { amber, steel, mint, indigo, bg, line } = theme.colors;
  return (
    <span className="theme-swatch" style={{ background: bg, borderColor: line }}>
      {[amber, steel, mint, indigo].map((color, index) => (
        <span key={index} className="theme-dot" style={{ background: color }} />
      ))}
    </span>
  );
}

function PaletteIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <path d="M12 3a9 9 0 0 0 0 18h1.5a2 2 0 0 0 1.6-3.2 2 2 0 0 1 1.6-3.2H19a2.5 2.5 0 0 0 2.5-2.5A9.3 9.3 0 0 0 12 3Z" />
      <circle cx="7.5" cy="11.5" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="10.5" cy="7.5" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="15.5" cy="8.5" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  );
}
