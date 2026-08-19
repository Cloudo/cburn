// The theme catalogue. Like `dict.ts`, this is data rather than code: an id, a name, a kind
// and thirteen colours - the same tokens `styles.css` declares in `:root`. The palettes are
// borrowed from the VS Code themes of the same name, so the dashboard offers the choice a person
// already made in the editor; on the light ones the accents are darkened, because on
// a white background the originals read as faded.
//
// Everything not a colour of the palette - the row tint, the shadows, the radius, the fonts -
// stays in `styles.css` and depends on the kind alone: `applyTheme` puts the kind into
// `data-theme`, and the `:root[data-theme="light"]` block keeps working as before.

export type ThemeKind = "dark" | "light";

/** The palette of one theme. The keys are short so that the table below stays a table. */
export type Palette = {
  bg: string;
  panel: string;
  sunken: string;
  line: string;
  text: string;
  dim: string;
  faint: string;
  amber: string;
  steel: string;
  indigo: string;
  mint: string;
  danger: string;
  dangerStrong: string;
};

export type Theme = { id: string; name: string; kind: ThemeKind; colors: Palette };

/** Which CSS token every palette key stands for. */
const TOKENS: Record<keyof Palette, string> = {
  bg: "--bg",
  panel: "--bg-panel",
  sunken: "--bg-sunken",
  line: "--line",
  text: "--text",
  dim: "--text-dim",
  faint: "--text-faint",
  amber: "--amber",
  steel: "--steel",
  indigo: "--indigo",
  mint: "--mint",
  danger: "--danger",
  dangerStrong: "--danger-strong",
};

export const THEMES: Theme[] = [
  {
    id: "cburn-dark",
    name: "cburn Dark",
    kind: "dark",
    colors: {
      bg: "#0f1216",
      panel: "#171b21",
      sunken: "#12161b",
      line: "#242a33",
      text: "#e6e9ee",
      dim: "#8a929d",
      faint: "#5d6570",
      amber: "#e8a33d",
      steel: "#4d7fa3",
      indigo: "#7b6bc0",
      mint: "#5eab86",
      danger: "#d98080",
      dangerStrong: "#c05c5c",
    },
  },
  {
    id: "dark-modern",
    name: "Dark Modern",
    kind: "dark",
    colors: {
      bg: "#1f1f1f",
      panel: "#262626",
      sunken: "#181818",
      line: "#313131",
      text: "#cccccc",
      dim: "#9d9d9d",
      faint: "#7a7a7a",
      amber: "#d18616",
      steel: "#3794ff",
      indigo: "#b180d7",
      mint: "#89d185",
      danger: "#f14c4c",
      dangerStrong: "#cd3131",
    },
  },
  {
    id: "one-dark-pro",
    name: "One Dark Pro",
    kind: "dark",
    colors: {
      bg: "#282c34",
      panel: "#2f333d",
      sunken: "#21252b",
      line: "#3e4451",
      text: "#abb2bf",
      dim: "#828997",
      faint: "#5c6370",
      amber: "#d19a66",
      steel: "#61afef",
      indigo: "#c678dd",
      mint: "#98c379",
      danger: "#e06c75",
      dangerStrong: "#be5046",
    },
  },
  {
    id: "dracula",
    name: "Dracula",
    kind: "dark",
    colors: {
      bg: "#282a36",
      panel: "#343746",
      sunken: "#21222c",
      line: "#44475a",
      text: "#f8f8f2",
      dim: "#bcc2cd",
      faint: "#6272a4",
      amber: "#ffb86c",
      steel: "#8be9fd",
      indigo: "#bd93f9",
      mint: "#50fa7b",
      danger: "#ff5555",
      dangerStrong: "#ff79c6",
    },
  },
  {
    id: "monokai",
    name: "Monokai",
    kind: "dark",
    colors: {
      bg: "#272822",
      panel: "#32332c",
      sunken: "#1e1f1c",
      line: "#414339",
      text: "#f8f8f2",
      dim: "#cfcfc2",
      faint: "#75715e",
      amber: "#fd971f",
      steel: "#66d9ef",
      indigo: "#ae81ff",
      mint: "#a6e22e",
      danger: "#f92672",
      dangerStrong: "#c9145a",
    },
  },
  {
    id: "nord",
    name: "Nord",
    kind: "dark",
    colors: {
      bg: "#2e3440",
      panel: "#3b4252",
      sunken: "#292e39",
      line: "#434c5e",
      text: "#eceff4",
      dim: "#aeb8ca",
      faint: "#7b88a1",
      amber: "#ebcb8b",
      steel: "#88c0d0",
      indigo: "#b48ead",
      mint: "#a3be8c",
      danger: "#bf616a",
      dangerStrong: "#a54e57",
    },
  },
  {
    id: "tokyo-night",
    name: "Tokyo Night",
    kind: "dark",
    colors: {
      bg: "#1a1b26",
      panel: "#24283b",
      sunken: "#16161e",
      line: "#292e42",
      text: "#c0caf5",
      dim: "#9aa5ce",
      faint: "#565f89",
      amber: "#e0af68",
      steel: "#7aa2f7",
      indigo: "#bb9af7",
      mint: "#9ece6a",
      danger: "#f7768e",
      dangerStrong: "#db4b4b",
    },
  },
  {
    id: "github-dark",
    name: "GitHub Dark",
    kind: "dark",
    colors: {
      bg: "#0d1117",
      panel: "#161b22",
      sunken: "#010409",
      line: "#30363d",
      text: "#c9d1d9",
      dim: "#8b949e",
      faint: "#6e7681",
      amber: "#d29922",
      steel: "#58a6ff",
      indigo: "#bc8cff",
      mint: "#3fb950",
      danger: "#f85149",
      dangerStrong: "#da3633",
    },
  },
  {
    id: "gruvbox-dark",
    name: "Gruvbox Dark",
    kind: "dark",
    colors: {
      bg: "#282828",
      panel: "#32302f",
      sunken: "#1d2021",
      line: "#3c3836",
      text: "#ebdbb2",
      dim: "#bdae93",
      faint: "#928374",
      amber: "#fabd2f",
      steel: "#83a598",
      indigo: "#d3869b",
      mint: "#b8bb26",
      danger: "#fb4934",
      dangerStrong: "#cc241d",
    },
  },
  {
    id: "catppuccin-mocha",
    name: "Catppuccin Mocha",
    kind: "dark",
    colors: {
      bg: "#1e1e2e",
      panel: "#282839",
      sunken: "#181825",
      line: "#45475a",
      text: "#cdd6f4",
      dim: "#a6adc8",
      faint: "#7f849c",
      amber: "#fab387",
      steel: "#89b4fa",
      indigo: "#cba6f7",
      mint: "#a6e3a1",
      danger: "#f38ba8",
      dangerStrong: "#eba0ac",
    },
  },
  {
    id: "solarized-dark",
    name: "Solarized Dark",
    kind: "dark",
    colors: {
      bg: "#002b36",
      panel: "#073642",
      sunken: "#00212b",
      line: "#14505f",
      text: "#93a1a1",
      dim: "#839496",
      faint: "#657b83",
      amber: "#b58900",
      steel: "#268bd2",
      indigo: "#6c71c4",
      mint: "#859900",
      danger: "#dc322f",
      dangerStrong: "#cb4b16",
    },
  },
  {
    id: "night-owl",
    name: "Night Owl",
    kind: "dark",
    colors: {
      bg: "#011627",
      panel: "#0b2942",
      sunken: "#01111d",
      line: "#1d3b53",
      text: "#d6deeb",
      dim: "#a2bbcc",
      faint: "#637777",
      amber: "#ecc48d",
      steel: "#82aaff",
      indigo: "#c792ea",
      mint: "#22da6e",
      danger: "#ef5350",
      dangerStrong: "#d3423e",
    },
  },
  {
    id: "cburn-light",
    name: "cburn Light",
    kind: "light",
    colors: {
      bg: "#f2f4f7",
      panel: "#ffffff",
      sunken: "#eaeef3",
      line: "#d8dee6",
      text: "#171c23",
      dim: "#55606d",
      faint: "#828d9b",
      amber: "#b87613",
      steel: "#3a6b8f",
      indigo: "#5f4fa8",
      mint: "#35825c",
      danger: "#b34a4a",
      dangerStrong: "#a03b3b",
    },
  },
  {
    id: "light-modern",
    name: "Light Modern",
    kind: "light",
    colors: {
      bg: "#f8f8f8",
      panel: "#ffffff",
      sunken: "#ececec",
      line: "#e0e0e0",
      text: "#3b3b3b",
      dim: "#616161",
      faint: "#8b8b8b",
      amber: "#b3720a",
      steel: "#0451a5",
      indigo: "#652d90",
      mint: "#388a34",
      danger: "#cd3131",
      dangerStrong: "#a1260d",
    },
  },
  {
    id: "github-light",
    name: "GitHub Light",
    kind: "light",
    colors: {
      bg: "#f6f8fa",
      panel: "#ffffff",
      sunken: "#eaeef2",
      line: "#d0d7de",
      text: "#1f2328",
      dim: "#59636e",
      faint: "#818b98",
      amber: "#9a6700",
      steel: "#0969da",
      indigo: "#8250df",
      mint: "#1a7f37",
      danger: "#cf222e",
      dangerStrong: "#a40e26",
    },
  },
  {
    id: "solarized-light",
    name: "Solarized Light",
    kind: "light",
    colors: {
      bg: "#eee8d5",
      panel: "#fdf6e3",
      sunken: "#e4ddc8",
      line: "#ddd6c1",
      text: "#586e75",
      dim: "#657b83",
      faint: "#93a1a1",
      amber: "#b58900",
      steel: "#268bd2",
      indigo: "#6c71c4",
      mint: "#859900",
      danger: "#dc322f",
      dangerStrong: "#cb4b16",
    },
  },
  {
    id: "catppuccin-latte",
    name: "Catppuccin Latte",
    kind: "light",
    colors: {
      bg: "#eff1f5",
      panel: "#ffffff",
      sunken: "#e6e9ef",
      line: "#ccd0da",
      text: "#4c4f69",
      dim: "#6c6f85",
      faint: "#8c8fa1",
      amber: "#c07c0c",
      steel: "#1e66f5",
      indigo: "#8839ef",
      mint: "#40a02b",
      danger: "#d20f39",
      dangerStrong: "#e64553",
    },
  },
  {
    id: "gruvbox-light",
    name: "Gruvbox Light",
    kind: "light",
    colors: {
      bg: "#fbf1c7",
      panel: "#f9f5d7",
      sunken: "#ebdbb2",
      line: "#d5c4a1",
      text: "#3c3836",
      dim: "#504945",
      faint: "#7c6f64",
      amber: "#b57614",
      steel: "#076678",
      indigo: "#8f3f71",
      mint: "#79740e",
      danger: "#cc241d",
      dangerStrong: "#9d0006",
    },
  },
];

/** The pair the system mode falls back to when nothing has been picked by hand yet. */
export const DEFAULT_THEME: Record<ThemeKind, string> = {
  dark: "cburn-dark",
  light: "cburn-light",
};

export function themeById(id: string | null): Theme | undefined {
  return THEMES.find((theme) => theme.id === id);
}

/** Puts the palette onto `<html>`: the kind as an attribute, the colours as inline tokens. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.dataset.theme = theme.kind;
  root.dataset.themeId = theme.id;
  for (const [key, token] of Object.entries(TOKENS)) {
    root.style.setProperty(token, theme.colors[key as keyof Palette]);
  }
}
