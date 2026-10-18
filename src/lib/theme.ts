import { useCallback, useEffect, useState } from "react";
import { ThemePreference, getSettings } from "../services/settings";

export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "tesseract.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

export function resolveTheme(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
  if (preference === "system") return systemDark ? "dark" : "light";
  return preference;
}

export function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.dataset.theme = resolved;
}

function isPreference(value: unknown): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

function storedPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    return isPreference(value) ? value : "system";
  } catch {
    return "system"; // storage can be unavailable; fall back to following the system
  }
}

function storePreference(preference: ThemePreference): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Not fatal: the saved settings file still holds the choice.
  }
}

/** Theme preference with live system tracking. The caller saves it with the rest of the settings. */
export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>(storedPreference);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia(DARK_QUERY).matches);

  useEffect(() => {
    getSettings()
      .then((settings) => {
        if (isPreference(settings.theme)) {
          setPreferenceState(settings.theme);
          storePreference(settings.theme);
        }
      })
      .catch(() => undefined); // keep the locally stored choice
  }, []);

  useEffect(() => {
    const media = window.matchMedia(DARK_QUERY);
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const resolved = resolveTheme(preference, systemDark);
  useEffect(() => applyTheme(resolved), [resolved]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    storePreference(next);
  }, []);

  return { preference, resolved, setPreference };
}
