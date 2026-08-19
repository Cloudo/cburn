// The interface language. A library for two languages is overkill: the dictionary is flat,
// there is one substitution, and the language choice is the browser's business just like
// the layout, so it lives in localStorage. The choice is made on the "Settings" screen and
// may be "system" - then the language follows the browser's own. The server is told the
// resolved language for one reason only: the menu-bar tray cannot read localStorage and
// must speak the same language.
//
// The pairs themselves live in `dict.ts` - `format.ts` reads them from there too.

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import {
  storedChoice,
  systemLang,
  STORAGE_KEY,
  translate,
  type Lang,
  type LangChoice,
} from "./dict";
import { setFormatLang } from "./format";

export { translate, type Lang, type LangChoice } from "./dict";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const LangContext = createContext<{
  lang: Lang;
  choice: LangChoice;
  setChoice: (next: LangChoice) => void;
}>({
  lang: "en",
  choice: "system",
  setChoice: () => {},
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<LangChoice>(storedChoice);
  const lang = choice === "system" ? systemLang() : choice;

  // Numbers and dates are formatted outside React, so the language reaches them before
  // the children start rendering.
  setFormatLang(lang);

  useEffect(() => {
    document.documentElement.lang = lang;
    try {
      localStorage.setItem(STORAGE_KEY, choice);
    } catch {
      // private mode - the choice simply will not be remembered
    }
    // The menu-bar tray is the second surface of the same instrument, and `localStorage`
    // is closed to it: the choice is mirrored through the server, the only channel to the
    // native part. A failure changes nothing here - the tray simply keeps the old language.
    void fetch(`api/ui/lang?lang=${lang}`, { method: "POST" }).catch(() => {});
  }, [choice, lang]);

  return (
    <LangContext.Provider value={{ lang, choice, setChoice }}>{children}</LangContext.Provider>
  );
}

export function useLang(): {
  lang: Lang;
  choice: LangChoice;
  setChoice: (next: LangChoice) => void;
  t: Translate;
} {
  const { lang, choice, setChoice } = useContext(LangContext);
  return { lang, choice, setChoice, t: (key, vars) => translate(lang, key, vars) };
}
