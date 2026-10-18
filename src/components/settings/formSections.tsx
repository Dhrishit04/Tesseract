import { openExternal } from "../../lib/sidecar";
import type { AutomationSettings, ProfileSettings, Settings, VoiceSettings } from "../../services/settings";
import { SelectField, SettingsSection, TextAreaField, TextField, Toggle } from "../fields";
import { CLOUD_VOICES, LANGUAGES, MODELS, PIPER_VOICES, STT_MODELS, TTS_ENGINES, WAKE_WORDS, option } from "./options";

export type SettingsErrors = Partial<Record<"apiKey" | "timeout", string>>;

export interface FormSectionProps {
  settings: Settings;
  update: (patch: Partial<Settings>) => void;
  errors: SettingsErrors;
}

export function validateSettings(settings: Settings): SettingsErrors {
  const errors: SettingsErrors = {};
  const key = settings.openrouter_api_key.trim();
  if (key && !key.startsWith("sk-or-")) errors.apiKey = "OpenRouter keys start with sk-or-. Check that you copied the whole key.";
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

export function ModelsSection({ settings, update, errors }: Readonly<FormSectionProps>) {
  return (
    <SettingsSection
      id="models"
      title="Models & accounts"
      description="Tesseract sends your requests to the model you choose through OpenRouter, using your own API key."
    >
      <TextField
        label="OpenRouter API key"
        secret
        value={settings.openrouter_api_key}
        placeholder="sk-or-v1-..."
        error={errors.apiKey}
        onChange={(openrouter_api_key) => update({ openrouter_api_key })}
        hint={
          <>
            Create one at{" "}
            <a href="https://openrouter.ai/keys" onClick={(event) => { event.preventDefault(); void openExternal("https://openrouter.ai/keys"); }}>
              openrouter.ai/keys
            </a>
            . It is saved in plain text in your settings file.
          </>
        }
      />
      <SelectField
        label="Default model"
        value={settings.default_model}
        options={MODELS}
        hint="Used for new chats. Each chat remembers the model you pick in it."
        onChange={(default_model) => update({ default_model })}
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
        </>
      )}
    </SettingsSection>
  );
}
