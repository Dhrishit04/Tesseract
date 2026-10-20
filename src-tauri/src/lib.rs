// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::time::Duration;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, RunEvent,
};

mod commands;
mod sidecar;

use sidecar::SidecarProcess;

const TRAY_ID: &str = "main";
const STATUS_POLL: Duration = Duration::from_secs(3);

/// Start the Python sidecar unless one is already listening on its port.
fn launch_sidecar(app: &AppHandle) {
    if sidecar::sidecar_port_in_use() {
        return;
    }
    let mut packaged_dirs = Vec::new();
    if let Ok(dir) = app.path().resource_dir() {
        packaged_dirs.push(dir);
    }
    if let Some(dir) = std::env::current_exe().ok().and_then(|exe| exe.parent().map(|p| p.to_path_buf())) {
        packaged_dirs.push(dir);
    }
    let start = std::env::current_dir().unwrap_or_default();
    match sidecar::sidecar_command(&packaged_dirs, &start, cfg!(debug_assertions)) {
        Some(command) => {
            if let Err(error) = app.state::<SidecarProcess>().start(&command) {
                eprintln!("Failed to start sidecar {:?}: {}", command.program, error);
            }
        }
        None => eprintln!("No sidecar found: not a packaged build and no sidecar/main.py above {:?}", start),
    }
}

/// Keep the tray tooltip in step with whether the assistant backend is reachable.
fn watch_sidecar_status(app: AppHandle) {
    std::thread::spawn(move || {
        let mut last: Option<bool> = None;
        loop {
            let online = sidecar::sidecar_port_in_use();
            if last != Some(online) {
                let tooltip = if online { "Tesseract: ready" } else { "Tesseract: assistant offline" };
                if let Some(tray) = app.tray_by_id(TRAY_ID) {
                    let _ = tray.set_tooltip(Some(tooltip));
                }
                let _ = app.emit("sidecar-status", online);
                last = Some(online);
            }
            std::thread::sleep(STATUS_POLL);
        }
    });
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[cfg(desktop)]
fn register_toggle_shortcut(app: &tauri::App) -> tauri::Result<()> {
    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

    let toggle = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyP);
    app.handle().plugin(
        tauri_plugin_global_shortcut::Builder::new()
            .with_handler(move |app, shortcut, event| {
                if shortcut == &toggle && event.state() == ShortcutState::Pressed {
                    commands::toggle_main_window(app);
                }
            })
            .build(),
    )?;
    if let Err(error) = app.global_shortcut().register(toggle) {
        // Another app may own the combination; the tray menu still works.
        eprintln!("Could not register Ctrl+Alt+P: {}", error);
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .manage(SidecarProcess::default())
        .setup(|app| {
            launch_sidecar(app.handle());

            #[cfg(desktop)]
            register_toggle_shortcut(app)?;

            let show = MenuItem::with_id(app, "show", "Show/Hide", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &settings, &quit])?;

            let mut tray = TrayIconBuilder::with_id(TRAY_ID)
                .tooltip("Tesseract: starting")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => commands::toggle_main_window(app),
                    "settings" => {
                        show_main_window(app);
                        let _ = app.emit("open-settings", ());
                    }
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            watch_sidecar_status(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::settings::get_settings,
            commands::settings::save_settings,
            commands::settings::reset_settings,
            commands::settings::get_config_path,
            commands::voice::get_voice_status,
            commands::voice::start_stt_recording,
            commands::voice::stop_stt_recording,
            commands::voice::stop_tts,
            commands::voice::start_voice_pipeline,
            commands::voice::stop_voice_pipeline,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Minimize to tray instead of closing
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<SidecarProcess>().stop();
        }
    });
}
