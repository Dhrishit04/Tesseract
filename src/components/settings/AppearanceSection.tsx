import { motion } from "motion/react";
import { SPRING_PRESS, useReducedMotionPreference } from "../../lib/motion";
import type { ThemePreference } from "../../services/settings";
import { SettingsSection } from "../fields";

const THEMES: { value: ThemePreference; label: string; hint: string }[] = [
  { value: "light", label: "Light", hint: "Warm dusk light" },
  { value: "dark", label: "Dark", hint: "Night sky" },
  { value: "system", label: "System", hint: "Follow Windows" },
];

interface AppearanceSectionProps {
  preference: ThemePreference;
  onChange: (preference: ThemePreference) => void;
}

/** Theme applies the moment you pick it and is saved straight away; no Save needed. */
export function AppearanceSection({ preference, onChange }: Readonly<AppearanceSectionProps>) {
  const reduced = useReducedMotionPreference();
  return (
    <SettingsSection id="appearance" title="Appearance" description="Choose how Tesseract looks. System follows your Windows light or dark setting as it changes.">
      <fieldset className="segmented">
        <legend className="field-label">Theme</legend>
        <div className="segmented-options">
          {THEMES.map((theme) => {
            const checked = preference === theme.value;
            return (
              <label key={theme.value} className={`segmented-option${checked ? " is-checked" : ""}`}>
                <input type="radio" name="theme" value={theme.value} checked={checked} onChange={() => onChange(theme.value)} />
                {checked && <motion.span className="segmented-thumb" layoutId={reduced ? undefined : "theme-thumb"} transition={SPRING_PRESS} />}
                <span className={`theme-swatch theme-swatch-${theme.value}`} aria-hidden="true" />
                <span className="segmented-label">{theme.label}</span>
                <span className="segmented-hint">{theme.hint}</span>
              </label>
            );
          })}
        </div>
      </fieldset>
    </SettingsSection>
  );
}
