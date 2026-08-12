"""JSON-RPC 2.0 stdio server — the desktop sidecar front end. See docs/RPC.md.

Line-delimited JSON both directions, serialized exactly like the CLI's NDJSON
(json.dumps(obj, default=str)). Engine events forwarded verbatim as "event"
notifications with one additive field: rid.
"""

import json
import os
import sys
import threading

from . import __version__
from .estimate import run_estimate
from .manifest import SCHEMA
from .push import run_push
from .runner import run_map, run_scrape, validate

PROTOCOL = 1
JOIN_TIMEOUT = 10  # seconds granted to run threads on EOF before hard exit
DEPTH_MAX = 10
MAX_PAGES_MAX = 2000

# Request-scoped secrets a scrape may carry (PLAN key-handoff rule): applied to
# the environment for that run only. Allowlisted so a client cannot use the
# sidecar to set arbitrary process environment.
ALLOWED_ENV = {"OPENROUTER_API_KEY", "IOFAST_RESCUE_MODEL"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
BUSY = -32000
NO_SCRAPE = -32001


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class RpcServer:
    """One scrape + one map at a time; cancel is cooperative (docs/RPC.md)."""

    def __init__(self, stdin=None, stdout=None, exit_fn=os._exit):
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self._exit = exit_fn
        self.write_lock = threading.Lock()  # serializes protocol lines
        self.state = threading.Lock()  # guards the run bookkeeping below
        self.threads = {}  # "scrape" / "map" -> Thread
        self.scrape_rid = None
        self.cancel_event = None
        self.spider = None
        self.cancelling = False
        self.closed = False  # EOF seen: the sink is a no-op from here on

    # ---- wire ----

    def _write(self, obj):
        line = json.dumps(obj, default=str)
        with self.write_lock:
            if self.closed:
                return
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def notify(self, rid, event):
        self._write({"jsonrpc": "2.0", "method": "event", "params": {**event, "rid": rid}})

    def respond(self, rid, result):
        self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    def fail(self, rid, code, message):
        self._write({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    # ---- dispatch ----

    def serve(self):
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.fail(None, PARSE_ERROR, "parse error")
                continue
            self.dispatch(msg)
        self.shutdown()

    def dispatch(self, msg):
        if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
            rid = msg.get("id") if isinstance(msg, dict) else None
            self.fail(rid, INVALID_REQUEST, "invalid request")
            return
        rid = msg.get("id")
        if rid is None:
            return  # client notification: none defined, dropped
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            self.fail(rid, INVALID_PARAMS, "params must be an object")
            return
        handler = getattr(self, f"handle_{msg['method']}", None)
        if handler is None:
            self.fail(rid, METHOD_NOT_FOUND, f"unknown method: {msg['method']}")
            return
        handler(rid, params)

    # ---- methods ----

    def handle_initialize(self, rid, params):
        self.respond(
            rid, {"protocol": PROTOCOL, "engine": __version__, "manifest_schema": SCHEMA}
        )

    def handle_estimate(self, rid, params):
        url = params.get("url")
        if not isinstance(url, str):
            return self.fail(rid, INVALID_PARAMS, "url must be a string")
        error = validate(url)
        if error:
            return self.fail(rid, INVALID_PARAMS, error)
        with self.state:
            thread = self.threads.get("estimate")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "estimate already running")
            thread = threading.Thread(target=self._estimate_thread, args=(rid, url), daemon=True)
            self.threads["estimate"] = thread
            thread.start()

    def _estimate_thread(self, rid, url):
        try:
            self.respond(rid, run_estimate(url))
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_map(self, rid, params):
        url, scope = params.get("url"), params.get("scope")
        if not isinstance(url, str):
            return self.fail(rid, INVALID_PARAMS, "url must be a string")
        if not (scope is None or isinstance(scope, str)):
            return self.fail(rid, INVALID_PARAMS, "scope must be a string or null")
        error = validate(url, scope)
        if error:
            return self.fail(rid, INVALID_PARAMS, error)
        with self.state:
            thread = self.threads.get("map")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "map already running")
            thread = threading.Thread(
                target=self._map_thread, args=(rid, url, scope), daemon=True
            )
            self.threads["map"] = thread
            thread.start()

    def _map_thread(self, rid, url, scope):
        try:
            result = run_map(url, scope=scope, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_scrape(self, rid, params):
        url, out, scope = params.get("url"), params.get("out"), params.get("scope")
        depth, max_pages = params.get("depth", 3), params.get("max_pages", 200)
        update = params.get("update", False)
        if not isinstance(update, bool):
            return self.fail(rid, INVALID_PARAMS, "update must be a boolean")
        env = params.get("env") or {}
        if not (
            isinstance(env, dict)
            and set(env) <= ALLOWED_ENV
            and all(isinstance(v, str) for v in env.values())
        ):
            return self.fail(
                rid, INVALID_PARAMS, f"env keys must be within {sorted(ALLOWED_ENV)}"
            )
        if not isinstance(url, str):
            return self.fail(rid, INVALID_PARAMS, "url must be a string")
        if not isinstance(out, str) or not out:
            return self.fail(rid, INVALID_PARAMS, "out is required and must be a string")
        if not (scope is None or isinstance(scope, str)):
            return self.fail(rid, INVALID_PARAMS, "scope must be a string or null")
        if not (_is_int(depth) and _is_int(max_pages)):
            return self.fail(rid, INVALID_PARAMS, "depth and max_pages must be integers")
        error = validate(url, scope)
        if error:
            return self.fail(rid, INVALID_PARAMS, error)
        depth = max(0, min(DEPTH_MAX, depth))
        max_pages = max(1, min(MAX_PAGES_MAX, max_pages))
        with self.state:
            thread = self.threads.get("scrape")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "scrape already running")
            self.scrape_rid = rid
            self.cancel_event = threading.Event()
            self.spider = None
            self.cancelling = False
            thread = threading.Thread(
                target=self._scrape_thread,
                args=(rid, url, os.path.abspath(out), scope, depth, max_pages, update, env, self.cancel_event),
                daemon=True,
            )
            self.threads["scrape"] = thread
            thread.start()

    def _scrape_thread(self, rid, url, out, scope, depth, max_pages, update, env, cancel_event):
        try:
            result = run_scrape(
                url,
                out=out,
                scope=scope,
                depth=depth,
                max_pages=max_pages,
                emit=lambda event: self.notify(rid, event),
                cancel=cancel_event,
                on_spider=self._adopt_spider,
                update=update,
                env=env,
            )
            result["out_dir"] = os.path.abspath(result["out_dir"]) if result["out_dir"] else out
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")
        finally:
            with self.state:
                self.scrape_rid = None
                self.spider = None

    def _adopt_spider(self, spider):
        with self.state:
            self.spider = spider
            pending_cancel = self.cancel_event is not None and self.cancel_event.is_set()
        if pending_cancel:
            spider.request_cancel()

    def handle_push(self, rid, params):
        run_dir, prompt, model = params.get("dir"), params.get("prompt"), params.get("model")
        if not isinstance(run_dir, str) or not run_dir:
            return self.fail(rid, INVALID_PARAMS, "dir is required and must be a string")
        if not isinstance(prompt, str) or not prompt.strip():
            return self.fail(rid, INVALID_PARAMS, "prompt is required and must be a non-empty string")
        if not (model is None or isinstance(model, str)):
            return self.fail(rid, INVALID_PARAMS, "model must be a string or null")
        env = params.get("env") or {}
        if not (
            isinstance(env, dict)
            and set(env) <= ALLOWED_ENV
            and all(isinstance(v, str) for v in env.values())
        ):
            return self.fail(
                rid, INVALID_PARAMS, f"env keys must be within {sorted(ALLOWED_ENV)}"
            )
        with self.state:
            thread = self.threads.get("push")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "push already running")
            thread = threading.Thread(
                target=self._push_thread, args=(rid, run_dir, prompt, model, env), daemon=True
            )
            self.threads["push"] = thread
            thread.start()

    def _push_thread(self, rid, run_dir, prompt, model, env):
        try:
            result = run_push(
                run_dir, prompt, model=model, env=env,
                emit=lambda event: self.notify(rid, event),
            )
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_cancel(self, rid, params):
        want = params.get("rid")
        if not (want is None or _is_int(want)):
            return self.fail(rid, INVALID_PARAMS, "rid must be an integer or null")
        with self.state:
            thread = self.threads.get("scrape")
            running = thread is not None and thread.is_alive() and self.scrape_rid is not None
            if not running or (want is not None and want != self.scrape_rid):
                return self.fail(rid, NO_SCRAPE, "no matching scrape running")
            target = self.scrape_rid
            first = not self.cancelling
            self.cancelling = True
            self.cancel_event.set()
            spider = self.spider
        # Engine pause only on the FIRST cancel: a second request_pause() would
        # escalate to a force-stop mid-write. request_cancel is itself guarded,
        # so the parse-time backstop cannot double-fire either.
        if first and spider is not None:
            spider.request_cancel()
        self.respond(rid, {"cancelling": True, "rid": target})

    # ---- lifecycle ----

    def shutdown(self):
        """EOF: the parent is gone. Silence the sink, cancel, bounded join, exit."""
        with self.write_lock:
            self.closed = True
        with self.state:
            if self.cancel_event is not None:
                self.cancel_event.set()
            spider = self.spider
        if spider is not None:
            spider.request_cancel()
        for thread in list(self.threads.values()):
            thread.join(timeout=JOIN_TIMEOUT)
        self._exit(0)


def _default_storage_env():
    """Browsers and caches live under app support — the frozen bundle dir is
    read-only in a signed .app. Explicit spawn-time env still wins (setdefault)."""
    if sys.platform == "darwin":
        support = os.path.expanduser("~/Library/Application Support/iofast")
    else:
        support = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "iofast"
        )
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(support, "browsers"))
    os.environ.setdefault("IOFAST_CACHE_DIR", os.path.join(support, "cache"))


def serve_stdio():
    """Entry point for `iofast rpc`: harden stdout, then serve until EOF."""
    _default_storage_env()
    # Dup the real stdout for protocol lines, then point fd 1 at stderr so a
    # dependency that print()s can never corrupt the protocol stream.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    RpcServer(stdout=protocol).serve()
    return 0  # unreachable: serve() ends in os._exit(0)
