// Settings live in ~/.tesseract/settings.json, shared by the desktop app and the sidecar.

import { invoke } from "@tauri-apps/api/core";
import { isTauri, postJson, sidecarJson } from "../lib/sidecar";

export interface VoiceSettings {
  voice_enabled: boolean;
  wake_word: string;
  wake_word_enabled: boolean;
  porcupine_access_key: string;
  stt_model: string;
  stt_language: string;
  tts_engine: string;
  tts_voice: string;
  tts_speed: number;
  openrouter_tts_voice: string;
}

export interface AutomationSettings {
  automation_enabled: boolean;
  browser_timeout: number;
  headless_default: boolean;
  ocr_enabled: boolean;
  office_use_com: boolean;
  share_screenshots: boolean;
}

export interface GoogleAuthSettings {
  client_id: string;
  connected: boolean;
  email: string;
}

export type ThemePreference = "light" | "dark" | "system";

export interface ProfileSettings {
  name: string;
  address_as: string;
  about: string;
}

export interface Settings {
  default_model: string;
  voice: VoiceSettings;
  automation: AutomationSettings;
  theme: ThemePreference;
  language: string;
  file_access: string;
  google: GoogleAuthSettings;
  profile: ProfileSettings;
}

export const DEFAULT_MODEL = "anthropic/claude-3.5-sonnet";

export const DEFAULT_SETTINGS: Settings = {
  default_model: DEFAULT_MODEL,
  voice: {
    voice_enabled: false,
    wake_word: "jarvis",
    wake_word_enabled: false,
    porcupine_access_key: "",
    stt_model: "tiny",
    stt_language: "en",
    tts_engine: "piper",
    tts_voice: "en_US-lessac-medium",
    tts_speed: 1,
    openrouter_tts_voice: "alloy",
  },
  automation: {
    automation_enabled: false,
    browser_timeout: 30000,
    headless_default: true,
    ocr_enabled: false,
    office_use_com: false,
    share_screenshots: false,
  },
  theme: "system",
  language: "en",
  file_access: "profile",
  google: { client_id: "", connected: false, email: "" },
  profile: { name: "", address_as: "", about: "" },
};

/** Fill gaps in a partially written settings file with defaults. Secrets now live in the vault, so stale ones are dropped. */
function withDefaults(loaded: Partial<Settings>): Settings {
  const { openrouter_api_key: _key, ...rest } = loaded as Partial<Settings> & { openrouter_api_key?: string };
  const { client_secret: _secret, ...google } = (loaded.google ?? {}) as Partial<Settings["google"]> & { client_secret?: string };
  return {
    ...DEFAULT_SETTINGS,
    ...rest,
    voice: { ...DEFAULT_SETTINGS.voice, ...loaded.voice },
    automation: { ...DEFAULT_SETTINGS.automation, ...loaded.automation },
    google: { ...DEFAULT_SETTINGS.google, ...google },
    profile: { ...DEFAULT_SETTINGS.profile, ...loaded.profile },
  };
}

export async function getSettings(): Promise<Settings> {
  if (isTauri()) return withDefaults(await invoke<Settings>("get_settings"));
  return withDefaults(await sidecarJson<Partial<Settings>>("/settings"));
}

/**
 * Save settings and make the sidecar pick them up. `applied` is false when the file was written
 * but the assistant service could not reload it right now (it reads the file when it next starts).
 */
export async function saveSettings(settings: Settings): Promise<{ applied: boolean }> {
  if (isTauri()) {
    await invoke("save_settings", { settings });
    try {
      await postJson("/settings/reload");
      return { applied: true };
    } catch {
      return { applied: false };
    }
  }
  await sidecarJson("/settings", { method: "PUT", body: JSON.stringify(settings) });
  return { applied: true };
}

/** Saves only the theme, on top of whatever is on disk (the theme applies without pressing Save). */
export async function saveThemePreference(theme: ThemePreference): Promise<void> {
  const current = await getSettings();
  await saveSettings({ ...current, theme });
}

export async function resetSettings(): Promise<Settings> {
  if (isTauri()) {
    const defaults = withDefaults(await invoke<Settings>("reset_settings"));
    await postJson("/settings/reload");
    return defaults;
  }
  await sidecarJson("/settings", { method: "PUT", body: JSON.stringify(DEFAULT_SETTINGS) });
  return DEFAULT_SETTINGS;
}

export async function getConfigPath(): Promise<string> {
  if (isTauri()) return invoke<string>("get_config_path");
  return "~/.tesseract/settings.json";
}
