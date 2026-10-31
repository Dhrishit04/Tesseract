import { useCallback, useEffect, useState } from "react";
import type { SidecarState } from "../../hooks/useSidecarStatus";
import type { LegalKind, SettingsSection as SectionId } from "../../lib/route";
import { errorMessage, isTauri } from "../../lib/sidecar";
import {
  Settings,
  ThemePreference,
  getConfigPath,
  getSettings,
  resetSettings,
  saveSettings,
  saveThemePreference,
} from "../../services/settings";
import { GoogleAccountSection } from "../GoogleAccountSection";
import { LegalPage } from "../LegalPage";
import { StartupSection } from "../StartupSection";
import { AboutSection } from "./AboutSection";
import { AppearanceSection } from "./AppearanceSection";
import { PermissionsSection } from "./PermissionsSection";
import { AutomationSection, ModelsSection, ProfileSection, VoiceSection, validateSettings } from "./formSections";

const SECTIONS: { id: SectionId; label: string; desktopOnly?: boolean }[] = [
  { id: "profile", label: "Profile" },
  { id: "appearance", label: "Appearance" },
  { id: "models", label: "Models & accounts" },
  { id: "permissions", label: "Permissions" },
  { id: "google", label: "Google account" },
  { id: "voice", label: "Voice" },
  { id: "automation", label: "Automation" },
  { id: "startup", label: "Startup", desktopOnly: true },
  { id: "about", label: "About" },
];

/** Sections whose fields are saved with the Save button (the rest apply immediately). */
const FORM_SECTIONS = new Set<SectionId>(["profile", "models", "permissions", "google", "voice", "automation"]);

type SaveState = { kind: "idle" | "saving" | "saved" | "error"; text?: string };

interface SettingsShellProps {
  section: SectionId;
  legal: LegalKind | null;
  sidecar: SidecarState;
  theme: ThemePreference;
  onThemeChange: (preference: ThemePreference) => void;
}

export function SettingsShell({ section, legal, sidecar, theme, onThemeChange }: Readonly<SettingsShellProps>) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saved, setSaved] = useState<Settings | null>(null);
  const [loadError, setLoadError] = useState("");
  const [configPath, setConfigPath] = useState("");
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [showErrors, setShowErrors] = useState(false);

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const loaded = await getSettings();
      setSettings(loaded);
      setSaved(loaded);
      setConfigPath(await getConfigPath());
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const changeTheme = (preference: ThemePreference) => {
    onThemeChange(preference);
    setSettings((current) => current && { ...current, theme: preference });
    setSaved((current) => current && { ...current, theme: preference });
    saveThemePreference(preference).catch((error) =>
      setSaveState({ kind: "error", text: `Theme applied but not saved. ${errorMessage(error)}` }),
    );
  };

  const nav = (
    <nav className="settings-nav" aria-label="Settings sections">
      <ul>
        {SECTIONS.filter((item) => !item.desktopOnly || isTauri()).map((item) => (
          <li key={item.id}>
            <a href={`#/settings/${item.id}`} aria-current={item.id === section && !legal ? "page" : undefined}>{item.label}</a>
          </li>
        ))}
      </ul>
    </nav>
  );

  if (legal) {
    return (
      <div className="settings-layout">
        {nav}
        <div className="settings-content">
          <a className="text-button back-link" href="#/settings/about">Back to About</a>
          <LegalPage kind={legal} />
        </div>
      </div>
    );
  }

  let content;
  if (loadError) {
    content = (
      <div className="inline-error" role="alert">
        <p>Settings could not be loaded. {loadError}</p>
        <button type="button" className="text-button" onClick={() => void load()}>Try again</button>
      </div>
    );
  } else if (!settings) {
    content = (
      <div aria-busy="true" aria-label="Loading settings">
        {[60, 90, 75].map((width) => <span key={width} className="skeleton skeleton-line" style={{ width: `${width}%` }} />)}
      </div>
    );
  } else {
    const errors = validateSettings(settings);
    const shownErrors = showErrors ? errors : {};
    const dirty = JSON.stringify(settings) !== JSON.stringify(saved);
    const update = (patch: Partial<Settings>) => setSettings({ ...settings, ...patch });
    const formProps = { settings, update, errors: shownErrors };

    const save = async () => {
      if (Object.keys(errors).length) {
        setShowErrors(true);
        setSaveState({ kind: "error", text: "Fix the highlighted fields, then save again." });
        return;
      }
      setSaveState({ kind: "saving" });
      try {
        const { applied } = await saveSettings(settings);
        setSaved(settings);
        setSaveState({
          kind: "saved",
          text: applied ? "Settings saved" : "Settings saved. They take effect once the assistant is running again.",
        });
      } catch (error) {
        setSaveState({ kind: "error", text: `Settings were not saved. ${errorMessage(error)}` });
      }
    };

    const reset = async () => {
      try {
        const defaults = await resetSettings();
        setSettings(defaults);
        setSaved(defaults);
        onThemeChange(defaults.theme);
        setSaveState({ kind: "saved", text: "Settings reset to defaults" });
      } catch (error) {
        setSaveState({ kind: "error", text: `Settings were not reset. ${errorMessage(error)}` });
      }
    };

    let statusText = saveState.text ?? "";
    if (saveState.kind === "saving") statusText = "Saving";
    else if (dirty && saveState.kind !== "error") statusText = "You have unsaved changes";

    content = (
      <>
        {section === "profile" && <ProfileSection {...formProps} />}
        {section === "appearance" && <AppearanceSection preference={theme} onChange={changeTheme} />}
        {section === "models" && <ModelsSection {...formProps} />}
        {section === "permissions" && <PermissionsSection {...formProps} />}
        {section === "google" && <GoogleAccountSection google={settings.google} onChange={(google) => update({ google })} />}
        {section === "voice" && <VoiceSection {...formProps} />}
        {section === "automation" && <AutomationSection {...formProps} />}
        {section === "startup" && <StartupSection />}
        {section === "about" && <AboutSection sidecar={sidecar} configPath={configPath} onReset={reset} />}
        {(FORM_SECTIONS.has(section) || dirty) && (
          <div className="save-bar">
            <p className={`save-status save-${saveState.kind}`} role="status" aria-live="polite">{statusText}</p>
            <button type="button" className="primary-button" disabled={!dirty || saveState.kind === "saving"} onClick={() => void save()}>
              Save settings
            </button>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="settings-layout">
      {nav}
      <div className="settings-content">
        <h1 className="page-title">Settings</h1>
        {content}
      </div>
    </div>
  );
}
