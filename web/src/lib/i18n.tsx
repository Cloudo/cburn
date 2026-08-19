// The interface language. A library for two languages is overkill: the dictionary is flat,
// there is one substitution, and the language choice is the browser's business just like
// the layout, so it lives in localStorage. The server is told about it for one reason only:
// the menu-bar tray cannot read localStorage and must speak the same language.
//
// The pairs themselves live in `dict.ts` - `format.ts` reads them from there too.

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { detect, STORAGE_KEY, translate, type Lang } from "./dict";
import { setFormatLang } from "./format";

export { translate, type Lang } from "./dict";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const LangContext = createContext<{ lang: Lang; setLang: (next: Lang) => void }>({
  lang: "ru",
  setLang: () => {},
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(detect);

  // Numbers and dates are formatted outside React, so the language reaches them before
  // the children start rendering.
  setFormatLang(lang);

  useEffect(() => {
    document.documentElement.lang = lang;
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // private mode - the language simply will not be remembered
    }
    // The menu-bar tray is the second surface of the same instrument, and `localStorage`
    // is closed to it: the choice is mirrored through the server, the only channel to the
    // native part. A failure changes nothing here - the tray simply keeps the old language.
    void fetch(`api/ui/lang?lang=${lang}`, { method: "POST" }).catch(() => {});
  }, [lang]);

  return <LangContext.Provider value={{ lang, setLang }}>{children}</LangContext.Provider>;
}

export function useLang(): { lang: Lang; setLang: (next: Lang) => void; t: Translate } {
  const { lang, setLang } = useContext(LangContext);
  return { lang, setLang, t: (key, vars) => translate(lang, key, vars) };
}

