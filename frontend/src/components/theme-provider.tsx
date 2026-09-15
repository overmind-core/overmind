import * as React from "react";

const THEME_STORAGE_KEY = "overmind-theme";

type Theme = "light" | "dark" | "system";

// Dark is the default for anyone who has not chosen: no stored preference means
// dark, not the OS setting. Only an explicit "system" follows prefers-color-scheme.
// The pre-hydration script in index.html must resolve this the same way.
const DEFAULT_THEME: "light" | "dark" = "dark";

function getSystemTheme(): "light" | "dark" {
  if (typeof window === "undefined") return DEFAULT_THEME;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getStoredTheme(): Theme {
  if (typeof window === "undefined") return DEFAULT_THEME;
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // Storage can be unavailable (private mode, blocked cookies).
  }
  return stored === "light" || stored === "dark" || stored === "system" ? stored : DEFAULT_THEME;
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  const resolved = theme === "system" ? getSystemTheme() : theme;
  if (resolved === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  resolvedTheme: "light" | "dark";
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>(DEFAULT_THEME);
  const [resolvedTheme, setResolvedTheme] = React.useState<"light" | "dark">(DEFAULT_THEME);
  const mounted = React.useRef(false);

  React.useEffect(() => {
    mounted.current = true;
    const stored = getStoredTheme();
    setThemeState(stored);
    const resolved = stored === "system" ? getSystemTheme() : stored;
    setResolvedTheme(resolved);
    applyTheme(stored);
  }, []);

  React.useEffect(() => {
    if (!mounted.current) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      if (theme === "system") {
        const resolved = media.matches ? "dark" : "light";
        setResolvedTheme(resolved);
        applyTheme("system");
      }
    };
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, [theme]);

  const setTheme = React.useCallback((newTheme: Theme) => {
    setThemeState(newTheme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, newTheme);
    } catch {
      // Storage can be unavailable; the choice still applies for this session.
    }
    const resolved = newTheme === "system" ? getSystemTheme() : newTheme;
    setResolvedTheme(resolved);
    applyTheme(newTheme);
  }, []);

  const value = React.useMemo(
    () => ({ resolvedTheme, setTheme, theme }),
    [theme, setTheme, resolvedTheme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
