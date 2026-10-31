import { useState } from "react";
import type { AutomationSettings, ProfileSettings, Settings, VoiceSettings } from "../../services/settings";
import { SelectField, SettingsSection, TextAreaField, TextField, Toggle } from "../fields";
import { ModelPicker } from "../ModelPicker";
import { AccountsSection } from "./AccountsSection";
import { CLOUD_VOICES, LANGUAGES, PIPER_VOICES, STT_MODELS, TTS_ENGINES, WAKE_WORDS, option } from "./options";

export type SettingsErrors = Partial<Record<"timeout", string>>;

export interface FormSectionProps {
  settings: Settings;
  update: (patch: Partial<Settings>) => void;
  errors: SettingsErrors;
}

export function validateSettings(settings: Settings): SettingsErrors {
  const errors: SettingsErrors = {};
  const timeout = settings.automation.browser_timeout;
  if (!Number.isFinite(timeout) || timeout < 1000 || timeout > 300000) errors.timeout = "Enter a value between 1 and 300 seconds.";
  return errors;
}

export function ProfileSection({ settings, update }: Readonly<FormSectionProps>) {
  const setProfile = (patch: Partial<ProfileSettings>) => update({ profile: { ...settings.profile, ...patch } });
  return (
    <SettingsSection id="profile" title="Profile" description="How Tesseract knows you. It uses this when it greets you and talks to you.">
      <TextField label="Your name" value={settings.profile.name} autoComplete="given-name" onChange={(name) => setProfile({ name })} />
      <TextField
        label="What should Tesseract call you?"
        value={settings.profile.address_as}
        placeholder="boss"
        hint="Any name or nickname, e.g. boss"
        onChange={(address_as) => setProfile({ address_as })}
      />
      <TextAreaField
        label="About you"
        value={settings.profile.about}
        hint="Optional. Anything you'd like Tesseract to keep in mind, like your job, your studies or how you like answers."
        onChange={(about) => setProfile({ about })}
      />
    </SettingsSection>
  );
}

export function ModelsSection({ settings, update }: Readonly<FormSectionProps>) {
  // Bumped when an account changes so the model list below is fetched again.
  const [accountsVersion, setAccountsVersion] = useState(0);
  return (
    <SettingsSection
      id="models"
      title="Models & accounts"
      description="Sign in with ChatGPT or add an API key. Tesseract only lists models from accounts you connect."
    >
      <AccountsSection onChange={() => setAccountsVersion((version) => version + 1)} />
      <ModelPicker
        key={accountsVersion}
        label="Default model"
        currentModel={settings.default_model}
        onModelChange={(default_model) => update({ default_model })}
        hint="Used for new chats. Each chat remembers the model you pick in it."
        connectHint="Connect an account above to choose a default model."
      />
    </SettingsSection>
  );
}

export function VoiceSection({ settings, update }: Readonly<FormSectionProps>) {
  const voice = settings.voice;
  const setVoice = (patch: Partial<VoiceSettings>) => update({ voice: { ...voice, ...patch } });
  return (
    <SettingsSection
      id="voice"
      title="Voice"
      description="Talk to Tesseract and hear answers aloud. Requires the optional voice packages and, for the wake word, a free Picovoice access key."
    >
      <Toggle label="Use voice" checked={voice.voice_enabled} onChange={(voice_enabled) => setVoice({ voice_enabled })} />
      {voice.voice_enabled && (
        <>
          <Toggle label="Listen for a wake word" checked={voice.wake_word_enabled} onChange={(wake_word_enabled) => setVoice({ wake_word_enabled })} />
          {voice.wake_word_enabled && (
            <>
              <SelectField label="Wake word" value={voice.wake_word} options={WAKE_WORDS} onChange={(wake_word) => setVoice({ wake_word })} />
              <TextField
                label="Picovoice access key"
                secret
                value={voice.porcupine_access_key}
                onChange={(porcupine_access_key) => setVoice({ porcupine_access_key })}
                hint="Free from console.picovoice.ai."
              />
            </>
          )}
          <SelectField
            label="Speech recognition model"
            value={voice.stt_model}
            options={STT_MODELS}
            hint="Larger models are more accurate but slower and use more memory."
            onChange={(stt_model) => setVoice({ stt_model })}
          />
          <SelectField label="Spoken language" value={voice.stt_language} options={LANGUAGES} onChange={(stt_language) => setVoice({ stt_language })} />
          <SelectField label="Speech engine" value={voice.tts_engine} options={TTS_ENGINES} onChange={(tts_engine) => setVoice({ tts_engine })} />
          {voice.tts_engine === "piper" ? (
            <SelectField label="Voice" value={voice.tts_voice} options={PIPER_VOICES} onChange={(tts_voice) => setVoice({ tts_voice })} />
          ) : (
            <SelectField label="Voice" value={voice.openrouter_tts_voice} options={CLOUD_VOICES} onChange={(openrouter_tts_voice) => setVoice({ openrouter_tts_voice })} />
          )}
          <div className="field">
            <label className="field-label" htmlFor="tts-speed">Speaking speed</label>
            <div className="range-row">
              <input
                id="tts-speed"
                type="range"
                min={0.5}
                max={2}
                step={0.1}
                value={voice.tts_speed}
                onChange={(event) => setVoice({ tts_speed: Number.parseFloat(event.target.value) })}
              />
              <output htmlFor="tts-speed">{voice.tts_speed.toFixed(1)}×</output>
            </div>
          </div>
        </>
      )}
    </SettingsSection>
  );
}

export function AutomationSection({ settings, update, errors }: Readonly<FormSectionProps>) {
  const automation = settings.automation;
  const setAutomation = (patch: Partial<AutomationSettings>) => update({ automation: { ...automation, ...patch } });
  return (
    <SettingsSection
      id="automation"
      title="Automation"
      description="Let Tesseract operate a browser, desktop apps and Office files on your behalf. Off by default. When on, the model can click and type on this computer."
    >
      <Toggle label="Allow automation" checked={automation.automation_enabled} onChange={(automation_enabled) => setAutomation({ automation_enabled })} />
      {automation.automation_enabled && (
        <>
          <TextField
            label="Browser timeout, in seconds"
            type="number"
            inputMode="numeric"
            value={String(automation.browser_timeout / 1000)}
            error={errors.timeout}
            onChange={(value) => setAutomation({ browser_timeout: Math.round(Number(value) * 1000) })}
          />
          <SelectField
            label="Browser window"
            value={automation.headless_default ? "headless" : "headed"}
            options={[option("headless", "Hidden, faster"), option("headed", "Visible, so you can watch")]}
            onChange={(value) => setAutomation({ headless_default: value === "headless" })}
          />
          <Toggle
            label="Read the screen with OCR when an app hides its controls"
            checked={automation.ocr_enabled}
            onChange={(ocr_enabled) => setAutomation({ ocr_enabled })}
            hint="Needs the Tesseract OCR engine installed."
          />
          <Toggle
            label="Use installed Microsoft Office for complex documents"
            checked={automation.office_use_com}
            onChange={(office_use_com) => setAutomation({ office_use_com })}
            hint="Windows only. Otherwise documents are edited directly without opening Office."
          />
          <Toggle
            label="Let the model see screenshots"
            checked={automation.share_screenshots}
            onChange={(share_screenshots) => setAutomation({ share_screenshots })}
            hint="Helps in apps that hide their controls. Tesseract asks before each screenshot unless you always allow it, and anything visible in it goes to your model provider. The model must support images."
          />
        </>
      )}
    </SettingsSection>
  );
}
