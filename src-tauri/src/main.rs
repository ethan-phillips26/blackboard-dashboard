// The dashboard is a local web app, so this shell is deliberately thin: it
// starts the Python server, waits for it to answer, and points a window at it.
// Everything the user sees is served over loopback by that server.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// A first run downloads Chromium for the sign-in browser, so the budget is
/// generous. It only ever costs this much on the run that has to do the work.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(120);
const PORT_TIMEOUT: Duration = Duration::from_secs(45);
const POLL_INTERVAL: Duration = Duration::from_millis(150);

/// Short on purpose. An update is never worth making someone wait to open the
/// app, and a machine that is offline or behind a captive portal must not stall
/// on the way to a dashboard that is served entirely from disk.
const UPDATE_TIMEOUT: Duration = Duration::from_secs(6);

/// How long the sign-in window may stay open. Generous because the clock is
/// mostly spent on a phone: a push to approve, or a code to copy across.
const LOGIN_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const COOKIE_POLL: Duration = Duration::from_secs(1);

/// The running server, held so it can be killed when the shell exits. An
/// orphaned server would keep serving a logged-in session with no window
/// attached to it, which is both a surprise and a leak.
struct Sidecar(Mutex<Option<Child>>);

fn binary_name() -> &'static str {
    if cfg!(windows) {
        "blackboard-server.exe"
    } else {
        "blackboard-server"
    }
}

/// Where the frozen server lives: inside the bundle once installed, and in the
/// build output when running under `tauri dev`.
fn sidecar_path(app: &AppHandle) -> Result<PathBuf, String> {
    let relative = PathBuf::from("sidecar")
        .join("blackboard-server")
        .join(binary_name());

    let bundled = app
        .path()
        .resource_dir()
        .map_err(|e| format!("could not resolve the resource directory: {e}"))?
        .join(&relative);
    if bundled.exists() {
        return Ok(bundled);
    }

    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(&relative);
    if dev.exists() {
        return Ok(dev);
    }

    Err(format!(
        "the server is missing. Looked in {} and {}. Run `npm run sidecar` to build it.",
        bundled.display(),
        dev.display()
    ))
}

/// Start the server and learn which port it took.
///
/// The port is read off stdout rather than fixed here, because a fixed port
/// collides with whatever else the machine is running — and with a second copy
/// of this app.
fn spawn_sidecar(app: &AppHandle, exe: &PathBuf) -> Result<(Child, u16), String> {
    let mut cmd = Command::new(exe);
    cmd.arg("--port")
        .arg("0")
        // Nothing is ever written down this pipe. It exists so that the server
        // sees EOF the moment this process goes away, however it goes away —
        // killing the child on a clean exit only covers the clean exits.
        .arg("--shell")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // The server is a console program so that its stdout can be read at
        // all; this is what stops Windows opening a console window for it.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("could not start the server: {e}"))?;

    // Both pipes are drained on threads. uvicorn logs every request, and a pipe
    // nobody reads fills up and blocks the server rather than losing a line.
    if let Some(err) = child.stderr.take() {
        thread::spawn(move || {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                eprintln!("[server] {line}");
            }
        });
    }

    // NB: child.stdin is deliberately left in place. Taking it here would drop
    // it at the end of this function, closing the pipe and stopping the server
    // it was meant to outlive.
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "the server produced no stdout".to_string())?;
    let (tx, rx) = mpsc::channel();
    let handle = app.clone();
    // The reader outlives the port handshake: the server keeps using this pipe
    // to ask for things only the shell can do, sign-in being the one that
    // matters. Keeping it one-way means the dashboard page never needs a handle
    // on the window system to get a window opened for it.
    let port_seen: Arc<Mutex<Option<u16>>> = Arc::new(Mutex::new(None));
    let slot = port_seen.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let line = line.trim().to_string();

            if let Some(value) = line.strip_prefix("BLACKBOARD_PORT=") {
                if let Ok(port) = value.parse::<u16>() {
                    *slot.lock().unwrap() = Some(port);
                    let _ = tx.send(port);
                    continue;
                }
            }

            if let Some(origin) = line.strip_prefix("BLACKBOARD_LOGIN=") {
                match *slot.lock().unwrap() {
                    // Its own thread: the sign-in sits open for as long as the
                    // person takes, and this reader has a pipe to keep draining.
                    Some(port) => {
                        let handle = handle.clone();
                        let origin = origin.to_string();
                        thread::spawn(move || open_login(&handle, &origin, port));
                    }
                    None => eprintln!("[login] asked for before the port was known"),
                }
                continue;
            }

            eprintln!("[server] {line}");
        }
    });

    match rx.recv_timeout(PORT_TIMEOUT) {
        Ok(port) => Ok((child, port)),
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            Err("the server started but never reported a port".to_string())
        }
    }
}

/// A hand-rolled GET rather than an HTTP crate: the only request this shell
/// ever makes is to its own child, on loopback, for one status line.
fn healthy(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let request = format!(
        "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 32];
    let Ok(n) = stream.read(&mut buf) else {
        return false;
    };
    String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1.1 200")
}

/// Hand a cookie to the server, which is the only side that can say whether it
/// means anything. Returns true once the server reports a real session.
fn post_cookie(port: u16, origin: &str, cookie: &str) -> bool {
    let body = serde_json::json!({ "cookie": cookie, "host": origin }).to_string();
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    // A cookie that works triggers a full sync before the response comes back.
    let _ = stream.set_read_timeout(Some(Duration::from_secs(120)));
    let request = format!(
        "POST /api/auth/desktop/cookie HTTP/1.1\r\n\
         Host: 127.0.0.1:{port}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\r\n{body}",
        body.len()
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }
    response.contains("\"logged_in\":true")
}

/// Open the institution's own sign-in page and wait for it to hand us a session.
///
/// This is why the desktop app needs no automation of the login at all: there is
/// no form to recognise and no second factor to anticipate, because the person
/// signs in exactly as they would in a browser. Whatever the institution does —
/// a push, a code, a passkey, a redirect through three providers — happens in a
/// real browser window and we only read the result.
fn open_login(app: &AppHandle, origin: &str, port: u16) {
    let Ok(url) = origin.parse::<Url>() else {
        return eprintln!("[login] not a usable address: {origin}");
    };

    // A second window would leave the first one polling against a dead handle.
    if let Some(existing) = app.get_webview_window("login") {
        let _ = existing.close();
    }

    // Deliberately granted no capability: this window loads a university's
    // identity provider, which must never reach a Tauri API.
    let window = match WebviewWindowBuilder::new(app, "login", WebviewUrl::External(url.clone()))
        .title("Sign in to Blackboard")
        .inner_size(960.0, 780.0)
        .center()
        .build()
    {
        Ok(window) => window,
        Err(e) => return eprintln!("[login] could not open the window: {e}"),
    };

    let deadline = Instant::now() + LOGIN_TIMEOUT;
    let mut last = String::new();
    while Instant::now() < deadline {
        thread::sleep(COOKIE_POLL);

        // Closing the window is how someone cancels; nothing else to clean up.
        if app.get_webview_window("login").is_none() {
            return;
        }

        let Ok(cookies) = window.cookies_for_url(url.clone()) else {
            continue;
        };
        let Some(value) = cookies
            .iter()
            .find(|c| c.name() == "BbRouter")
            .map(|c| c.value().to_string())
        else {
            continue;
        };

        // Blackboard reissues this as you move through the login, and hands one
        // to anonymous visitors too, so only a changed value is worth asking
        // about — and only the server can tell whether it is a real session.
        if value == last {
            continue;
        }
        last = value.clone();

        if post_cookie(port, origin, &value) {
            let _ = window.close();
            return;
        }
    }

    eprintln!("[login] gave up after {LOGIN_TIMEOUT:?}");
    let _ = window.close();
}

fn status(app: &AppHandle, message: &str) {
    if let Some(splash) = app.get_webview_window("splash") {
        let payload = serde_json::to_string(message).unwrap_or_else(|_| "\"\"".into());
        let _ = splash.eval(&format!("window.setStatus({payload})"));
    }
}

fn show_error(app: &AppHandle, message: &str) {
    eprintln!("startup failed: {message}");
    if let Some(splash) = app.get_webview_window("splash") {
        let payload = serde_json::to_string(message).unwrap_or_else(|_| "\"\"".into());
        let _ = splash.eval(&format!("window.showError({payload})"));
        let _ = splash.set_resizable(true);
    }
}

/// Offer a newer release, and install it if the user accepts.
///
/// Every failure here is soft. The dashboard's data is already on disk, so a
/// missing endpoint, a dead network or a malformed feed must all end with the
/// app opening normally — an updater that can stop you reading your own
/// deadlines is worse than one that silently misses a version.
///
/// Runs before the server starts, so an install never has to tear down a
/// running child, and returns only if the app is to carry on launching.
fn offer_update(app: &AppHandle) {
    status(app, "Checking for updates\u{2026}");

    let updater = match app.updater_builder().timeout(UPDATE_TIMEOUT).build() {
        Ok(updater) => updater,
        Err(e) => return eprintln!("[updater] not configured: {e}"),
    };

    let found = match tauri::async_runtime::block_on(updater.check()) {
        Ok(Some(update)) => update,
        Ok(None) => return,
        Err(e) => return eprintln!("[updater] check failed: {e}"),
    };

    let accepted = app
        .dialog()
        .message(format!(
            "Whiteboard {} is available. You have {}.",
            found.version, found.current_version
        ))
        .title("Update available")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install and restart".to_string(),
            "Not now".to_string(),
        ))
        .blocking_show();
    if !accepted {
        return;
    }

    // The bundle is ~100MB, so silence here would read as a hang.
    let mut taken: usize = 0;
    let progress = |chunk: usize, total: Option<u64>| {
        taken += chunk;
        match total {
            Some(total) if total > 0 => {
                let pct = (taken as f64 / total as f64 * 100.0).round() as u64;
                status(app, &format!("Downloading update\u{2026} {pct}%"));
            }
            _ => status(app, "Downloading update\u{2026}"),
        }
    };
    let finished = || status(app, "Installing\u{2026}");

    if let Err(e) = tauri::async_runtime::block_on(found.download_and_install(progress, finished))
    {
        eprintln!("[updater] install failed: {e}");
        app.dialog()
            .message(format!(
                "The update could not be installed: {e}\n\nWhiteboard will open as it is."
            ))
            .title("Update failed")
            .kind(MessageDialogKind::Warning)
            .blocking_show();
        return;
    }

    // Never returns: the process is replaced by the freshly installed one.
    app.restart();
}

fn start(app: &AppHandle) -> Result<u16, String> {
    let exe = sidecar_path(app)?;

    offer_update(app);

    status(app, "Starting the server\u{2026}");
    let (child, port) = spawn_sidecar(app, &exe)?;
    app.state::<Sidecar>().0.lock().unwrap().replace(child);

    status(app, "Waiting for the dashboard\u{2026}");
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if healthy(port) {
            return Ok(port);
        }
        thread::sleep(POLL_INTERVAL);
    }
    Err("the server started but never answered".to_string())
}

/// Open the real window, then drop the splash. In that order: closing the last
/// window first would be read as the app quitting.
fn show_dashboard(app: &AppHandle, port: u16) {
    let url = match format!("http://127.0.0.1:{port}").parse() {
        Ok(url) => url,
        Err(e) => return show_error(app, &format!("bad server address: {e}")),
    };

    match WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .title("Whiteboard")
        .inner_size(1240.0, 840.0)
        .min_inner_size(900.0, 600.0)
        .center()
        .build()
    {
        Ok(_) => {
            if let Some(splash) = app.get_webview_window("splash") {
                let _ = splash.close();
            }
        }
        Err(e) => show_error(app, &format!("could not open the dashboard window: {e}")),
    }
}

fn main() {
    let app = tauri::Builder::default()
        // Both are driven from Rust only. Capabilities gate the JavaScript API,
        // and the dashboard — served over loopback and holding real grades — is
        // deliberately granted neither.
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            // Off the main thread: this blocks for as long as the server takes
            // to come up, and the splash has to keep painting meanwhile.
            let handle = app.handle().clone();
            thread::spawn(move || match start(&handle) {
                Ok(port) => show_dashboard(&handle, port),
                Err(e) => show_error(&handle, &e),
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the application");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Sidecar>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
