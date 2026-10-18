//! Locating, starting and stopping the Python sidecar.

use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

pub const SIDECAR_PORT: u16 = 8765;

/// Name Tauri gives the `externalBin` sidecar next to the installed app.
#[cfg(windows)]
pub const PACKAGED_BINARY: &str = "tesseract-sidecar.exe";
#[cfg(not(windows))]
pub const PACKAGED_BINARY: &str = "tesseract-sidecar";

/// Python interpreter inside `sidecar/.venv`, relative to the sidecar folder.
#[cfg(windows)]
pub const VENV_PYTHON: &str = ".venv/Scripts/python.exe";
#[cfg(not(windows))]
pub const VENV_PYTHON: &str = ".venv/bin/python";

#[cfg(windows)]
const SYSTEM_PYTHON: &str = "python";
#[cfg(not(windows))]
const SYSTEM_PYTHON: &str = "python3";

/// How to launch the sidecar: a program plus its arguments and working directory.
#[derive(Debug, PartialEq, Eq)]
pub struct SidecarCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub working_dir: PathBuf,
}

/// Walk up from `start` to the checkout that contains `sidecar/main.py`.
pub fn find_project_root(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|dir| dir.join("sidecar").join("main.py").is_file())
        .map(Path::to_path_buf)
}

fn checkout_command(start: &Path) -> Option<SidecarCommand> {
    let sidecar_dir = find_project_root(start)?.join("sidecar");
    let venv_python = sidecar_dir.join(VENV_PYTHON);
    let program = if venv_python.is_file() { venv_python } else { PathBuf::from(SYSTEM_PYTHON) };
    Some(SidecarCommand { program, args: vec!["main.py".to_string()], working_dir: sidecar_dir })
}

/// Pick the sidecar to run: a bundled binary, or the source checkout's virtualenv.
///
/// Dev builds pass `prefer_checkout` so edits to the Python code take effect even
/// though `tauri dev` copies an older bundled binary next to the debug executable.
pub fn sidecar_command(packaged_dirs: &[PathBuf], start: &Path, prefer_checkout: bool) -> Option<SidecarCommand> {
    if prefer_checkout {
        if let Some(command) = checkout_command(start) {
            return Some(command);
        }
    }
    if let Some(binary) = packaged_dirs
        .iter()
        .map(|dir| dir.join(PACKAGED_BINARY))
        .find(|path| path.is_file())
    {
        let working_dir = binary.parent().map(Path::to_path_buf).unwrap_or_default();
        return Some(SidecarCommand { program: binary, args: vec![], working_dir });
    }

    checkout_command(start)
}

/// True when something already answers on the sidecar port (e.g. a dev server started by hand).
pub fn sidecar_port_in_use() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], SIDECAR_PORT));
    TcpStream::connect_timeout(&address, Duration::from_millis(300)).is_ok()
}

/// Owns the sidecar child process so it can be stopped when the app exits.
#[derive(Default)]
pub struct SidecarProcess {
    child: Mutex<Option<Child>>,
}

impl SidecarProcess {
    pub fn start(&self, command: &SidecarCommand) -> std::io::Result<()> {
        let mut process = Command::new(&command.program);
        process.args(&command.args).current_dir(&command.working_dir);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            process.creation_flags(CREATE_NO_WINDOW);
        }
        let child = process.spawn()?;
        *self.child.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
        Ok(())
    }

    #[cfg(test)]
    pub fn pid(&self) -> Option<u32> {
        self.child.lock().unwrap_or_else(|e| e.into_inner()).as_ref().map(Child::id)
    }

    /// Stop the sidecar and anything it started. A PyInstaller one-file build runs the
    /// real server as a child of its bootloader, so killing only our child would orphan it.
    pub fn stop(&self) {
        if let Some(mut child) = self.child.lock().unwrap_or_else(|e| e.into_inner()).take() {
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                let _ = Command::new("taskkill")
                    .args(["/PID", &child.id().to_string(), "/T", "/F"])
                    .creation_flags(CREATE_NO_WINDOW)
                    .status();
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tesseract-sidecar-{}-{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn make_repo(root: &Path) {
        fs::create_dir_all(root.join("sidecar")).unwrap();
        fs::write(root.join("sidecar").join("main.py"), "").unwrap();
        fs::create_dir_all(root.join("src-tauri")).unwrap();
    }

    #[test]
    fn finds_repo_root_from_src_tauri() {
        let root = scratch("root");
        make_repo(&root);

        assert_eq!(find_project_root(&root.join("src-tauri")), Some(root.clone()));
        assert_eq!(find_project_root(&root), Some(root));
    }

    #[test]
    fn packaged_binary_wins_over_dev_checkout() {
        let root = scratch("packaged");
        make_repo(&root);
        let bundle = root.join("bundle");
        fs::create_dir_all(&bundle).unwrap();
        let exe = bundle.join(PACKAGED_BINARY);
        fs::write(&exe, "").unwrap();

        let command = sidecar_command(std::slice::from_ref(&bundle), &root, false).unwrap();

        assert_eq!(command.program, exe);
        assert!(command.args.is_empty());
    }

    #[test]
    fn dev_checkout_uses_sidecar_virtualenv_python() {
        let root = scratch("venv");
        make_repo(&root);
        let python = root.join("sidecar").join(VENV_PYTHON);
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "").unwrap();

        let command = sidecar_command(&[], &root.join("src-tauri"), false).unwrap();

        assert_eq!(command.program, python);
        assert_eq!(command.args, vec!["main.py".to_string()]);
        assert_eq!(command.working_dir, root.join("sidecar"));
    }

    #[test]
    fn dev_builds_prefer_the_checkout_over_a_copied_binary() {
        let root = scratch("prefer");
        make_repo(&root);
        let python = root.join("sidecar").join(VENV_PYTHON);
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "").unwrap();
        let target = root.join("src-tauri").join("target").join("debug");
        fs::create_dir_all(&target).unwrap();
        fs::write(target.join(PACKAGED_BINARY), "").unwrap();

        let command = sidecar_command(std::slice::from_ref(&target), &root, true).unwrap();

        assert_eq!(command.program, python);
    }

    #[cfg(windows)]
    #[test]
    fn stop_ends_grandchild_processes() {
        // cmd.exe starts ping.exe as its own child, like the PyInstaller bootloader starts Python.
        let process = SidecarProcess::default();
        let command = SidecarCommand {
            program: PathBuf::from("cmd"),
            args: vec!["/C".into(), "ping -n 60 127.0.0.1 > NUL".into()],
            working_dir: std::env::temp_dir(),
        };
        process.start(&command).unwrap();
        std::thread::sleep(Duration::from_millis(800));
        let parent = process.pid().unwrap();

        process.stop();
        std::thread::sleep(Duration::from_millis(500));

        let query = format!("(Get-CimInstance Win32_Process -Filter 'ParentProcessId={}').ProcessId", parent);
        let survivors = Command::new("powershell")
            .args(["-NoProfile", "-Command", &query])
            .output()
            .map(|out| String::from_utf8_lossy(&out.stdout).lines().filter(|l| l.trim().parse::<u32>().is_ok()).count())
            .unwrap_or(0);
        assert_eq!(survivors, 0);
    }

    #[test]
    fn nothing_to_launch_outside_a_checkout() {
        let empty = scratch("empty");

        assert_eq!(sidecar_command(&[], &empty, false), None);
    }
}
