//! Client for the `iofast rpc` sidecar. Protocol: docs/RPC.md.
//!
//! One child at a time, spawned lazily. Line-delimited JSON-RPC 2.0 over its
//! stdio pipes: requests out through a mutex-guarded stdin, replies and event
//! notifications in through one reader thread.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::oneshot;

pub type Reply = Result<Value, String>;

/// How long a closed stdin gets to land `os._exit(0)` before we kill.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(12);

struct Proc {
    child: Child,
    /// `Option` so shutdown can drop stdin (EOF) while still holding the child.
    stdin: Option<ChildStdin>,
}

struct Inner {
    proc: Mutex<Option<Proc>>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Reply>>>,
    spawn_lock: Mutex<()>,
    next_id: AtomicU64,
    stdout_closed: AtomicBool,
}

impl Inner {
    fn start<E>(inner: &Arc<Self>, mut cmd: Command, emit: E) -> Result<(), String>
    where
        E: Fn(&str, Value) + Send + 'static,
    {
        let mut child = cmd
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("could not spawn sidecar: {e}"))?;
        let stdout = child.stdout.take().expect("piped");
        let stderr = child.stderr.take().expect("piped");
        let stdin = child.stdin.take().expect("piped");

        inner.stdout_closed.store(false, Ordering::SeqCst);
        *inner.proc.lock().unwrap() = Some(Proc {
            child,
            stdin: Some(stdin),
        });

        let reader = Arc::clone(inner);
        std::thread::spawn(move || reader.read_loop(stdout, emit));
        // docs/RPC.md: stderr is human noise, never protocol — but never dropped.
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                log::debug!("sidecar stderr: {line}");
            }
        });
        Ok(())
    }

    fn read_loop<E: Fn(&str, Value)>(self: Arc<Self>, stdout: ChildStdout, emit: E) {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let msg: Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(e) => {
                    log::warn!("non-JSON line on protocol stdout ({e}): {line}");
                    continue;
                }
            };
            if msg.get("method").and_then(Value::as_str) == Some("event") {
                // Forwarded verbatim; `params` IS the engine event dict, rid included.
                emit("engine-event", msg.get("params").cloned().unwrap_or(Value::Null));
                continue;
            }
            match msg.get("id").and_then(Value::as_u64) {
                Some(id) if msg.get("result").is_some() || msg.get("error").is_some() => {
                    self.resolve(id, reply_of(&msg));
                }
                _ => log::warn!("unroutable sidecar line: {line}"),
            }
        }
        // EOF: no further reply can ever arrive on this child.
        self.stdout_closed.store(true, Ordering::SeqCst);
        self.fail_all("sidecar exited");
    }

    fn resolve(&self, id: u64, reply: Reply) {
        if let Some(tx) = self.pending.lock().unwrap().remove(&id) {
            let _ = tx.send(reply);
        }
    }

    fn fail_all(&self, why: &str) {
        for (_, tx) in self.pending.lock().unwrap().drain() {
            let _ = tx.send(Err(why.to_string()));
        }
    }

    fn alive(&self) -> bool {
        if self.stdout_closed.load(Ordering::SeqCst) {
            return false;
        }
        match self.proc.lock().unwrap().as_mut() {
            Some(p) => matches!(p.child.try_wait(), Ok(None)),
            None => false,
        }
    }

    fn send(&self, method: &str, params: Value) -> Result<(u64, oneshot::Receiver<Reply>), String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(id, tx);
        let line = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params});
        match self.write_line(&serde_json::to_string(&line).map_err(|e| e.to_string())?) {
            Ok(()) => Ok((id, rx)),
            Err(e) => {
                self.pending.lock().unwrap().remove(&id);
                Err(e)
            }
        }
    }

    fn write_line(&self, line: &str) -> Result<(), String> {
        let mut slot = self.proc.lock().unwrap();
        let stdin = slot
            .as_mut()
            .and_then(|p| p.stdin.as_mut())
            .ok_or("sidecar is not running")?;
        let fail = |e: std::io::Error| format!("write to sidecar failed: {e}");
        stdin.write_all(format!("{line}\n").as_bytes()).map_err(fail)?;
        stdin.flush().map_err(fail)
    }

    fn shutdown(&self) {
        let Some(mut p) = self.proc.lock().unwrap().take() else {
            return;
        };
        // docs/RPC.md "Process lifecycle": EOF on stdin IS the shutdown signal.
        // The server silences its emit sink, sets the cancel flag, joins running
        // threads (bounded), then os._exit(0). No signals, no PID files.
        drop(p.stdin.take());
        let deadline = Instant::now() + SHUTDOWN_GRACE;
        while Instant::now() < deadline {
            match p.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                Err(_) => break,
            }
        }
        let _ = p.child.kill();
        let _ = p.child.wait();
    }
}

impl Drop for Inner {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn reply_of(msg: &Value) -> Reply {
    match msg.get("error") {
        Some(e) => Err(format!(
            "{} (code {})",
            e.get("message").and_then(Value::as_str).unwrap_or("sidecar error"),
            e.get("code").and_then(Value::as_i64).unwrap_or(0),
        )),
        None => Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
    }
}

/// Tauri managed state. Cheap to clone; all clones share one child.
#[derive(Clone)]
pub struct SidecarManager(Arc<Inner>);

impl SidecarManager {
    pub fn new() -> Self {
        Self(Arc::new(Inner {
            proc: Mutex::default(),
            pending: Mutex::default(),
            spawn_lock: Mutex::default(),
            next_id: AtomicU64::new(1),
            stdout_closed: AtomicBool::new(false),
        }))
    }

    /// Spawn on first use, or respawn after the child died.
    fn ensure(&self, app: &AppHandle) -> Result<(), String> {
        let _guard = self.0.spawn_lock.lock().unwrap();
        if self.0.alive() {
            return Ok(());
        }
        self.0.shutdown(); // reap the corpse before replacing it
        let bin = resolve_binary(app)?;
        log::info!("spawning sidecar: {} rpc", bin.display());
        let mut cmd = Command::new(bin);
        cmd.arg("rpc");
        // No secrets here by design: OPENROUTER_API_KEY / IOFAST_RESCUE_MODEL
        // ride in the `scrape` request body, read from the keychain per run
        // (store::scrape_env), so they never sit in a long-lived child's
        // environment. IOFAST_CACHE_DIR: the sidecar defaults it itself.
        let app = app.clone();
        Inner::start(&self.0, cmd, move |name, payload| {
            let _ = app.emit(name, payload);
        })
    }

    /// Send and return immediately with the request id, for calls whose reply
    /// arrives minutes later (`scrape`).
    pub fn start_request(
        &self,
        app: &AppHandle,
        method: &str,
        params: Value,
    ) -> Result<(u64, oneshot::Receiver<Reply>), String> {
        self.ensure(app)?;
        self.0.send(method, params)
    }

    pub async fn request(&self, app: &AppHandle, method: &str, params: Value) -> Reply {
        let (_, rx) = self.start_request(app, method, params)?;
        rx.await.map_err(|_| "sidecar exited before replying".to_string())?
    }

    pub fn shutdown(&self) {
        self.0.shutdown();
    }
}

fn resolve_binary(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(explicit) = std::env::var_os("IOFAST_BIN") {
        return Ok(PathBuf::from(explicit));
    }
    if let Ok(dir) = app.path().resource_dir() {
        let bundled = dir.join("iofast").join("iofast");
        if bundled.is_file() {
            return Ok(bundled);
        }
    }
    // Dev fallback. Anchored to the crate dir, not cwd: `pnpm tauri dev` runs the
    // binary from src-tauri, `cargo run` from wherever you invoked it.
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../engine/dist/iofast/iofast");
    if dev.is_file() {
        return Ok(dev);
    }
    Err(format!(
        "no iofast sidecar found — set IOFAST_BIN, or build one at {}",
        dev.display()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    impl Inner {
        fn write_raw(&self, line: &str) {
            self.write_line(line).unwrap();
        }
    }

    /// `cat` is a loopback sidecar: every line we write comes back through the
    /// real reader thread, so this drives the actual framing and pending map.
    #[test]
    fn correlates_replies_to_request_ids() {
        let mgr = SidecarManager::new();
        let (events, seen) = std::sync::mpsc::channel();
        Inner::start(&mgr.0, Command::new("cat"), move |name, payload| {
            let _ = events.send((name.to_string(), payload));
        })
        .expect("cat spawns");

        let (id1, rx1) = mgr.0.send("map", json!({ "url": "a" })).unwrap();
        let (id2, rx2) = mgr.0.send("scrape", json!({ "url": "b" })).unwrap();
        assert_eq!((id1, id2), (1, 2), "ids come from the counter, starting at 1");

        // Replies land out of order, interleaved with an event notification.
        mgr.0.write_raw(r#"{"jsonrpc":"2.0","id":2,"result":{"written":7}}"#);
        mgr.0.write_raw(r#"{"jsonrpc":"2.0","method":"event","params":{"type":"page","rid":2}}"#);
        mgr.0
            .write_raw(r#"{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"bad url"}}"#);

        let block_on = tauri::async_runtime::block_on;
        assert_eq!(block_on(rx2).unwrap().unwrap()["written"], json!(7));
        let err = block_on(rx1).unwrap().unwrap_err();
        assert_eq!(err, "bad url (code -32602)");

        let (name, payload) = seen.recv_timeout(Duration::from_secs(5)).unwrap();
        assert_eq!(name, "engine-event");
        assert_eq!(payload["rid"], json!(2), "events carry the originating rid");

        // Closing stdin ends `cat`; anything still pending must be rejected,
        // not left hanging forever.
        let (_, orphan) = mgr.0.send("map", json!({ "url": "c" })).unwrap();
        mgr.shutdown();
        assert!(block_on(orphan).unwrap().is_err());
        assert!(!mgr.0.alive());
    }
}
