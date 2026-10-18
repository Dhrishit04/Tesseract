use serde::{Deserialize, Serialize};
use tauri::Emitter;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct VoiceStatus {
    pub wake_word: serde_json::Value,
    pub stt: serde_json::Value,
    pub tts: serde_json::Value,
    pub pipeline: Option<serde_json::Value>,
    pub enabled: bool,
    /// False when the optional voice packages are not installed in the sidecar.
    #[serde(default)]
    pub deps_available: Option<bool>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct VoiceCommandResponse {
    pub success: bool,
    pub data: Option<serde_json::Value>,
    pub error: Option<String>,
}

#[tauri::command]
pub async fn get_voice_status() -> Result<VoiceStatus, String> {
    let client = reqwest::Client::new();
    let resp = client
        .get("http://127.0.0.1:8765/voice/status")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let status: VoiceStatus = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    Ok(status)
}

#[tauri::command]
pub async fn start_wake_word(app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/wake-word/start")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    // Emit event for UI update
    let _ = app.emit("voice-status-changed", ());

    Ok(result)
}

#[tauri::command]
pub async fn stop_wake_word(app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/wake-word/stop")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let _ = app.emit("voice-status-changed", ());

    Ok(result)
}

#[tauri::command]
pub async fn start_stt_recording(app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/stt/start")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let _ = app.emit("voice-status-changed", ());

    Ok(result)
}

#[tauri::command]
pub async fn stop_stt_recording(app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/stt/stop")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let _ = app.emit("voice-status-changed", ());

    Ok(result)
}

#[tauri::command]
pub async fn speak_text(text: String, app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/tts/speak")
        .json(&serde_json::json!({ "text": text }))
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let _ = app.emit("voice-status-changed", ());

    Ok(result)
}

#[tauri::command]
pub async fn stop_tts(app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/tts/stop")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let _ = app.emit("voice-status-changed", ());

    Ok(result)
}

#[tauri::command]
pub async fn start_voice_pipeline(app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/pipeline/start")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let _ = app.emit("voice-status-changed", ());
    Ok(result)
}

#[tauri::command]
pub async fn stop_voice_pipeline(app: tauri::AppHandle) -> Result<VoiceCommandResponse, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post("http://127.0.0.1:8765/voice/pipeline/stop")
        .send()
        .await
        .map_err(|e| format!("Sidecar unreachable: {}", e))?;

    let result: VoiceCommandResponse = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let _ = app.emit("voice-status-changed", ());
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_proxy_keeps_dependency_flag() {
        let body = r#"{"wake_word":{},"stt":{},"tts":{},"pipeline":null,"enabled":true,"deps_available":false}"#;

        let status: VoiceStatus = serde_json::from_str(body).unwrap();
        let forwarded = serde_json::to_value(&status).unwrap();

        assert_eq!(forwarded["deps_available"], false);
    }
}
