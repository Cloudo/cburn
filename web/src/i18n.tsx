// The interface language. A library for two languages is overkill: the dictionary is flat,
// there is one substitution, and the language choice is the browser's business just like
// the layout, so it lives in localStorage and the server knows nothing about it.
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
  }, [lang]);

  return <LangContext.Provider value={{ lang, setLang }}>{children}</LangContext.Provider>;
}

export function useLang(): { lang: Lang; setLang: (next: Lang) => void; t: Translate } {
  const { lang, setLang } = useContext(LangContext);
  return { lang, setLang, t: (key, vars) => translate(lang, key, vars) };
}

