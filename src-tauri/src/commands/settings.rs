use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

/// Keys written by the sidecar or newer app versions that this struct does not model.
/// Kept so a save from the UI never drops them.
type Extra = serde_json::Map<String, serde_json::Value>;

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(default)]
pub struct VoiceSettings {
    pub voice_enabled: bool,
    pub wake_word: String,
    pub wake_word_enabled: bool,
    pub porcupine_access_key: String,
    pub stt_model: String,
    pub stt_language: String,
    pub tts_engine: String,
    pub tts_voice: String,
    pub tts_speed: f32,
    pub openrouter_tts_voice: String,
    #[serde(flatten)]
    pub extra: Extra,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(default)]
pub struct AutomationSettings {
    pub automation_enabled: bool,
    pub browser_timeout: u32,
    pub headless_default: bool,
    pub ocr_enabled: bool,
    pub office_use_com: bool,
    #[serde(flatten)]
    pub extra: Extra,
}

impl Default for AutomationSettings {
    fn default() -> Self {
        Self {
            automation_enabled: false,
            browser_timeout: 30000,
            headless_default: true,
            ocr_enabled: false,
            office_use_com: false,
            extra: Extra::new(),
        }
    }
}

impl Default for VoiceSettings {
    fn default() -> Self {
        Self {
            voice_enabled: false,
            wake_word: "jarvis".to_string(),
            wake_word_enabled: false,
            porcupine_access_key: "".to_string(),
            stt_model: "tiny".to_string(),
            stt_language: "en".to_string(),
            tts_engine: "piper".to_string(),
            tts_voice: "en_US-lessac-medium".to_string(),
            tts_speed: 1.0,
            openrouter_tts_voice: "alloy".to_string(),
            extra: Extra::new(),
        }
    }
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
#[serde(default)]
pub struct GoogleAuthSettings {
    pub client_id: String,
    pub client_secret: String,
    pub connected: bool,
    pub email: String,
    #[serde(flatten)]
    pub extra: Extra,
}

/// Who the owner is and how Tesseract should address them.
#[derive(Debug, Serialize, Deserialize, Clone, Default)]
#[serde(default)]
pub struct ProfileSettings {
    pub name: String,
    pub address_as: String,
    pub about: String,
    #[serde(flatten)]
    pub extra: Extra,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(default)]
pub struct Settings {
    pub openrouter_api_key: String,
    pub default_model: String,
    pub voice: VoiceSettings,
    pub automation: AutomationSettings,
    pub theme: String,
    pub language: String,
    pub google: GoogleAuthSettings,
    pub profile: ProfileSettings,
    #[serde(flatten)]
    pub extra: Extra,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            openrouter_api_key: String::new(),
            default_model: "anthropic/claude-3.5-sonnet".to_string(),
            voice: VoiceSettings::default(),
            automation: AutomationSettings::default(),
            theme: "system".to_string(),
            language: "en".to_string(),
            google: GoogleAuthSettings::default(),
            profile: ProfileSettings::default(),
            extra: Extra::new(),
        }
    }
}

impl Settings {
    fn settings_path() -> PathBuf {
        let mut path = dirs::home_dir().unwrap_or_else(|| PathBuf::from("."));
        path.push(".tesseract");
        path.push("settings.json");
        path
    }

    pub fn load() -> Result<Self, String> {
        Self::load_from(&Self::settings_path())
    }

    pub fn load_from(path: &Path) -> Result<Self, String> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let content = fs::read_to_string(path).map_err(|e| format!("Failed to read settings: {}", e))?;
        serde_json::from_str(&content).map_err(|e| format!("Failed to parse settings: {}", e))
    }

    pub fn save(&self) -> Result<(), String> {
        self.save_to(&Self::settings_path())
    }

    pub fn save_to(&self, path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("Failed to create settings directory: {}", e))?;
        }
        let content = serde_json::to_string_pretty(self).map_err(|e| format!("Failed to serialize settings: {}", e))?;
        // Write then rename so an interrupted save never leaves a truncated file.
        let temporary = path.with_extension("json.tmp");
        fs::write(&temporary, content).map_err(|e| format!("Failed to write settings: {}", e))?;
        fs::rename(&temporary, path).map_err(|e| format!("Failed to replace settings: {}", e))
    }
}

#[tauri::command]
pub async fn get_settings() -> Result<Settings, String> {
    Settings::load()
}

#[tauri::command]
pub async fn save_settings(settings: Settings) -> Result<(), String> {
    settings.save()
}

#[tauri::command]
pub async fn reset_settings() -> Result<Settings, String> {
    let defaults = Settings::default();
    defaults.save()?;
    Ok(defaults)
}

#[tauri::command]
pub async fn get_config_path() -> Result<String, String> {
    let path = Settings::settings_path();
    Ok(path.to_string_lossy().to_string())
}
#[cfg(test)]
mod tests {
    use super::*;

    fn temp_settings_path(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tesseract-settings-{}-{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        dir.join("settings.json")
    }

    #[test]
    fn defaults_to_system_theme() {
        assert_eq!(Settings::default().theme, "system");
    }

    #[test]
    fn profile_round_trips() {
        let path = temp_settings_path("profile");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, r#"{"profile": {"name": "Dhrishit", "address_as": "boss"}}"#).unwrap();

        let settings = Settings::load_from(&path).unwrap();

        assert_eq!(settings.profile.name, "Dhrishit");
        assert_eq!(settings.profile.address_as, "boss");
        assert_eq!(settings.profile.about, "");
    }

    #[test]
    fn loads_partial_file_written_by_sidecar() {
        let path = temp_settings_path("partial");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, r#"{"openrouter_api_key": "sk-test"}"#).unwrap();

        let settings = Settings::load_from(&path).expect("partial settings should load");

        assert_eq!(settings.openrouter_api_key, "sk-test");
        assert_eq!(settings.default_model, "anthropic/claude-3.5-sonnet");
        assert_eq!(settings.automation.browser_timeout, 30000);
        assert_eq!(settings.voice.wake_word, "jarvis");
    }

    #[test]
    fn save_keeps_fields_the_ui_does_not_know() {
        let path = temp_settings_path("unknown");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, r#"{"automation": {"office_root": "C:/Docs"}, "future_flag": true}"#).unwrap();

        let settings = Settings::load_from(&path).unwrap();
        settings.save_to(&path).unwrap();

        let saved: serde_json::Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(saved["automation"]["office_root"], "C:/Docs");
        assert_eq!(saved["future_flag"], true);
    }
}
