//! The tray dictionary: the same device the frontend has - a key and a pair of languages.
//!
//! The menu bar is a native surface, it cannot take texts from `web/src/dict.ts`, and
//! Russian is not allowed to spread through the code (a project invariant). So the pairs
//! live in a data file next to it, `dict.json`, and are baked into the binary by
//! `include_str!`: nothing extra to ship with the `.app`, and no build step of its own.
//!
//! Which language to speak is not decided here: the browser chooses it and the server
//! mirrors the choice into `~/.local/share/cburn/ui.json` (see `tray::load_lang`).

use std::collections::HashMap;
use std::sync::OnceLock;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Lang {
    Ru,
    En,
}

impl Lang {
    /// The languages of the interface dictionary; anything else is not our choice to make.
    pub fn parse(value: &str) -> Option<Lang> {
        match value {
            "ru" => Some(Lang::Ru),
            "en" => Some(Lang::En),
            _ => None,
        }
    }

    fn index(self) -> usize {
        match self {
            Lang::Ru => 0,
            Lang::En => 1,
        }
    }
}

fn pairs() -> &'static HashMap<String, [String; 2]> {
    static DICT: OnceLock<HashMap<String, [String; 2]>> = OnceLock::new();
    DICT.get_or_init(|| {
        serde_json::from_str(include_str!("../dict.json")).expect("the tray dictionary is broken")
    })
}

/// A phrase in the chosen language. A missing key gives back the key itself - the same
/// behaviour as in `dict.ts`: a menu without a caption would be worse than a visible key.
pub fn t(lang: Lang, key: &'static str) -> &'static str {
    match pairs().get(key) {
        Some(pair) => pair[lang.index()].as_str(),
        None => key,
    }
}

/// The same with `{name}` substitutions: there are one or two of them per phrase,
/// so a loop of replacements is enough.
pub fn tf(lang: Lang, key: &'static str, vars: &[(&str, String)]) -> String {
    let mut text = t(lang, key).to_string();
    for (name, value) in vars {
        text = text.replace(&format!("{{{name}}}"), value);
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_languages_are_filled_in() {
        for (key, pair) in pairs() {
            assert!(!pair[0].is_empty(), "{key}: the Russian half is empty");
            assert!(!pair[1].is_empty(), "{key}: the English half is empty");
        }
    }

    #[test]
    fn the_language_reaches_the_phrase() {
        assert_eq!(t(Lang::En, "tray.quit"), "quit");
        assert_eq!(t(Lang::Ru, "tray.quit"), "выйти");
        assert_eq!(Lang::parse("ru"), Some(Lang::Ru));
        assert_eq!(Lang::parse("klingon"), None);
    }

    #[test]
    fn substitution_works_in_both_halves() {
        let vars = [
            ("tokens", "6.2".to_string()),
            ("cost", "146.09".to_string()),
        ];
        assert_eq!(
            tf(Lang::En, "tray.burn", &vars),
            "burn rate: 6.2K tok/min · $146.09/h"
        );
        assert!(tf(Lang::Ru, "tray.burn", &vars).contains("6.2K ток/мин"));
    }
}
