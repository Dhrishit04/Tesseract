import { useEffect, useState } from "react";
import { errorMessage, isTauri } from "../lib/sidecar";
import { SettingsSection, Toggle } from "./fields";

/** Launch-at-sign-in switch. Applies immediately; only exists inside the desktop app. */
export function StartupSection() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isTauri()) return;
    import("@tauri-apps/plugin-autostart")
      .then(({ isEnabled }) => isEnabled())
      .then(setEnabled)
      .catch((failure) => setError(errorMessage(failure)));
  }, []);

  if (!isTauri()) return null;

  const change = async (next: boolean) => {
    setError("");
    try {
      const autostart = await import("@tauri-apps/plugin-autostart");
      await (next ? autostart.enable() : autostart.disable());
      setEnabled(await autostart.isEnabled());
    } catch (failure) {
      setError(errorMessage(failure));
    }
  };

  return (
    <SettingsSection id="startup" title="Startup" description="Tesseract can start in the system tray when you sign in, ready for Ctrl+Alt+P.">
      {enabled === null && !error ? (
        <span className="skeleton skeleton-line skeleton-short" aria-label="Checking startup setting" />
      ) : (
        <Toggle
          label="Start Tesseract when I sign in"
          checked={Boolean(enabled)}
          onChange={(next) => void change(next)}
          hint="Applies right away. No need to save."
        />
      )}
      {error && <p className="field-error" role="alert">{error}</p>}
    </SettingsSection>
  );
}
