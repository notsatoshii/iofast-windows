"""JSON-RPC 2.0 stdio server — the desktop sidecar front end. See docs/RPC.md.

Line-delimited JSON both directions, serialized exactly like the CLI's NDJSON
(json.dumps(obj, default=str)). Engine events forwarded verbatim as "event"
notifications with one additive field: rid.
"""

import json
import os
import sys
import threading

from . import __build__, __version__
from .browsers import default_storage_env
from .estimate import run_estimate
from .manifest import SCHEMA
from .push import run_push
from .runner import run_map, run_scrape, validate
from .sources import GrabOptions, detect as detect_source

PROTOCOL = 1
JOIN_TIMEOUT = 10  # seconds granted to run threads on EOF before hard exit
DEPTH_MAX = 10
MAX_PAGES_MAX = 2000

# Request-scoped secrets a scrape may carry (PLAN key-handoff rule): applied to
# the environment for that run only. Allowlisted so a client cannot use the
# sidecar to set arbitrary process environment.
ALLOWED_ENV = {"OPENROUTER_API_KEY", "IOFAST_RESCUE_MODEL", "IOFAST_PROXY"}

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
        self.grab_rid = None
        self.grab_cancel_event = None
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
        # Dotted methods (social.signin, vault.file, …) map to handlers with
        # underscores replacing dots; plain methods keep their existing names.
        handler_name = f"handle_{msg['method'].replace('.', '_')}"
        handler = getattr(self, handler_name, None)
        if handler is None:
            self.fail(rid, METHOD_NOT_FOUND, f"unknown method: {msg['method']}")
            return
        handler(rid, params)

    # ---- methods ----

    def handle_initialize(self, rid, params):
        self.respond(
            rid,
            {
                "protocol": PROTOCOL,
                "engine": __version__,
                "build": __build__,
                "manifest_schema": SCHEMA,
            },
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
        resume = params.get("resume", False)
        if not isinstance(resume, bool):
            return self.fail(rid, INVALID_PARAMS, "resume must be a boolean")
        if resume and update:
            return self.fail(rid, INVALID_PARAMS, "resume and update cannot both be set")
        interactive = params.get("interactive", False)
        if not isinstance(interactive, bool):
            return self.fail(rid, INVALID_PARAMS, "interactive must be a boolean")
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
        if resume and not os.path.exists(
            os.path.join(os.path.abspath(out), ".crawl", "checkpoint.pkl")
        ):
            return self.fail(rid, INVALID_PARAMS, f"no checkpoint to resume in {out}")
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
                args=(rid, url, os.path.abspath(out), scope, depth, max_pages, update, resume, interactive, env, self.cancel_event),
                daemon=True,
            )
            self.threads["scrape"] = thread
            thread.start()

    def _scrape_thread(self, rid, url, out, scope, depth, max_pages, update, resume, interactive, env, cancel_event):
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
                resume=resume,
                interactive=interactive,
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

    def handle_browser_status(self, rid, params):
        from . import browsers

        self.respond(rid, browsers.status())

    def handle_verify_components(self, rid, params):
        from . import browsers

        # Read-only digest check (~1s over the driver files) — repair goes
        # through install_browsers, which is idempotent per package.
        self.respond(rid, browsers.verify())

    def handle_install_browsers(self, rid, params):
        with self.state:
            thread = self.threads.get("browsers")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "browser install already running")
            thread = threading.Thread(
                target=self._install_browsers_thread, args=(rid,), daemon=True
            )
            self.threads["browsers"] = thread
            thread.start()

    def _install_browsers_thread(self, rid):
        from . import browsers

        try:
            result = browsers.install(emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_detect(self, rid, params):
        """Synchronous URL -> source routing. No network, no thread."""
        url = params.get("url")
        if not isinstance(url, str) or not url:
            return self.fail(rid, INVALID_PARAMS, "url must be a non-empty string")
        try:
            source = detect_source(url)
        except RuntimeError as error:
            return self.fail(rid, INTERNAL_ERROR, str(error))
        modes = ["page", "site"] if source.name == "web" else ["page"]
        needs_signin = source.name in ("instagram", "x")
        label = {
            "web": "Web page / site",
            "youtube": "YouTube video / playlist",
            "github": "GitHub repository",
            "instagram": "Instagram (sign-in required)",
            "x": "X / Twitter (sign-in required)",
        }.get(source.name, source.name)
        self.respond(rid, {
            "source": source.name,
            "label": label,
            "modes": modes,
            "needs_signin": needs_signin,
        })

    def handle_grab(self, rid, params):
        """Multi-source grab: routes via detect(), runs in a worker thread."""
        url = params.get("url")
        out = params.get("out")
        mode = params.get("mode")
        limit = params.get("limit")
        lang = params.get("lang")
        file_after = params.get("file", False)
        vault_path = params.get("vault")
        if not isinstance(url, str) or not url:
            return self.fail(rid, INVALID_PARAMS, "url must be a non-empty string")
        if not isinstance(out, str) or not out:
            return self.fail(rid, INVALID_PARAMS, "out is required and must be a string")
        if not (mode is None or mode in ("page", "site")):
            return self.fail(rid, INVALID_PARAMS, "mode must be 'page' or 'site'")
        if not (limit is None or (_is_int(limit) and limit > 0)):
            return self.fail(rid, INVALID_PARAMS, "limit must be a positive integer or null")
        if not (lang is None or isinstance(lang, str)):
            return self.fail(rid, INVALID_PARAMS, "lang must be a string or null")
        if not isinstance(file_after, bool):
            return self.fail(rid, INVALID_PARAMS, "file must be a boolean")
        with self.state:
            thread = self.threads.get("grab")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "grab already running")
            self.grab_rid = rid
            self.grab_cancel_event = threading.Event()
            thread = threading.Thread(
                target=self._grab_thread,
                args=(rid, url, os.path.abspath(out), mode, limit, lang, self.grab_cancel_event, file_after, vault_path),
                daemon=True,
            )
            self.threads["grab"] = thread
            thread.start()

    def _grab_thread(self, rid, url, out, mode, limit, lang, cancel_event, file_after=False, vault_path=None):
        import asyncio
        from pathlib import Path

        try:
            source = detect_source(url)
            opts = GrabOptions(
                mode=mode or "page",
                limit=limit,
                lang=lang,
            )

            def emit_with_rid(event):
                self.notify(rid, event)

            result = asyncio.run(source.grab(url, Path(out), opts, emit_with_rid))
            written = result.ok + result.thin
            exit_code = 0 if result.ok > 0 and result.failed == 0 else 1
            cancelled = cancel_event.is_set()
            manifest_path = Path(out) / "manifest.json"
            manifest = {}
            if manifest_path.exists():
                try:
                    with open(manifest_path, encoding="utf-8") as fh:
                        manifest = json.load(fh)
                except Exception:
                    pass

            response = {
                "written": written,
                "out_dir": out,
                "exit_code": exit_code,
                "cancelled": cancelled,
                "manifest": manifest,
            }

            # Opt-in filing: only when grab succeeded and file_after is True
            if file_after and not cancelled and exit_code == 0:
                try:
                    from .vault.vault_rpc import vault_file
                    file_result = vault_file(
                        {"run_dir": out, "vault": vault_path},
                        emit=lambda event: self.notify(rid, event),
                    )
                    response["vault_filed"] = True
                    response["vault_path"] = file_result.get("vault_path")
                except Exception as file_err:
                    response["vault_filed"] = False
                    response["vault_error"] = f"{type(file_err).__name__}: {file_err}"

            self.respond(rid, response)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")
        finally:
            with self.state:
                self.grab_rid = None
                self.grab_cancel_event = None

    # ---- social methods ----

    def handle_social_signin(self, rid, params):
        """Opens a visible browser for sign-in; runs in a worker thread."""
        site = params.get("site")
        if site not in ("instagram", "x"):
            return self.fail(rid, INVALID_PARAMS, "site must be 'instagram' or 'x'")
        with self.state:
            thread = self.threads.get("social_signin")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "social.signin already running")
            thread = threading.Thread(
                target=self._social_signin_thread, args=(rid, params), daemon=True
            )
            self.threads["social_signin"] = thread
            thread.start()

    def _social_signin_thread(self, rid, params):
        try:
            from .sources.social_rpc import signin
            result = signin(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_social_status(self, rid, params):
        """Synchronous signed-in state check."""
        try:
            from .sources.social_rpc import status
            result = status(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    # ---- watch methods ----

    def handle_watch_add(self, rid, params):
        """Add a URL to the watch list. Synchronous."""
        try:
            from .watch.watch_rpc import watch_add
            result = watch_add(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_watch_list(self, rid, params):
        """List all watch entries. Synchronous."""
        try:
            from .watch.watch_rpc import watch_list
            result = watch_list(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_watch_remove(self, rid, params):
        """Remove a watch entry by id. Synchronous."""
        try:
            from .watch.watch_rpc import watch_remove
            result = watch_remove(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_watch_run(self, rid, params):
        """Run due watch checks. Runs in a worker thread."""
        with self.state:
            thread = self.threads.get("watch_run")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "watch.run already running")
            thread = threading.Thread(
                target=self._watch_run_thread, args=(rid, params), daemon=True
            )
            self.threads["watch_run"] = thread
            thread.start()

    def _watch_run_thread(self, rid, params):
        try:
            from .watch.watch_rpc import watch_run
            result = watch_run(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    # ---- import methods ----

    def handle_import_chatgpt(self, rid, params):
        """Import ChatGPT export archive. Runs in a worker thread."""
        with self.state:
            thread = self.threads.get("import_chatgpt")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "import.chatgpt already running")
            thread = threading.Thread(
                target=self._import_chatgpt_thread, args=(rid, params), daemon=True
            )
            self.threads["import_chatgpt"] = thread
            thread.start()

    def _import_chatgpt_thread(self, rid, params):
        try:
            from .import_chat.import_rpc import import_chatgpt
            result = import_chatgpt(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_import_claude(self, rid, params):
        """Import Claude export archive. Runs in a worker thread."""
        with self.state:
            thread = self.threads.get("import_claude")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "import.claude already running")
            thread = threading.Thread(
                target=self._import_claude_thread, args=(rid, params), daemon=True
            )
            self.threads["import_claude"] = thread
            thread.start()

    def _import_claude_thread(self, rid, params):
        try:
            from .import_chat.import_rpc import import_claude
            result = import_claude(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    # ---- vault methods ----

    def handle_vault_status(self, rid, params):
        """Synchronous vault availability check."""
        try:
            from .vault.vault_rpc import vault_status
            result = vault_status(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_vault_file(self, rid, params):
        """File a capture into the vault; runs in a worker thread."""
        run_dir = params.get("run_dir")
        if not isinstance(run_dir, str) or not run_dir:
            return self.fail(rid, INVALID_PARAMS, "run_dir is required and must be a string")
        with self.state:
            thread = self.threads.get("vault_file")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "vault.file already running")
            thread = threading.Thread(
                target=self._vault_file_thread, args=(rid, params), daemon=True
            )
            self.threads["vault_file"] = thread
            thread.start()

    def _vault_file_thread(self, rid, params):
        try:
            from .vault.vault_rpc import vault_file
            result = vault_file(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_vault_lint(self, rid, params):
        """Synchronous vault lint."""
        try:
            from .vault.vault_rpc import vault_lint
            result = vault_lint(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_vault_ask(self, rid, params):
        """Ask a question against the vault; runs in a worker thread."""
        with self.state:
            thread = self.threads.get("vault_ask")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "vault.ask already running")
            thread = threading.Thread(
                target=self._vault_ask_thread, args=(rid, params), daemon=True
            )
            self.threads["vault_ask"] = thread
            thread.start()

    def _vault_ask_thread(self, rid, params):
        try:
            from .vault.extras_rpc import vault_ask
            result = vault_ask(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_vault_check(self, rid, params):
        """Weekly vault health check; runs in a worker thread."""
        with self.state:
            thread = self.threads.get("vault_check")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "vault.check already running")
            thread = threading.Thread(
                target=self._vault_check_thread, args=(rid, params), daemon=True
            )
            self.threads["vault_check"] = thread
            thread.start()

    def _vault_check_thread(self, rid, params):
        try:
            from .vault.extras_rpc import vault_check
            result = vault_check(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_digest_make(self, rid, params):
        """Generate weekly digest; runs in a worker thread."""
        with self.state:
            thread = self.threads.get("digest_make")
            if thread is not None and thread.is_alive():
                return self.fail(rid, BUSY, "digest.make already running")
            thread = threading.Thread(
                target=self._digest_make_thread, args=(rid, params), daemon=True
            )
            self.threads["digest_make"] = thread
            thread.start()

    def _digest_make_thread(self, rid, params):
        try:
            from .vault.extras_rpc import digest_make
            result = digest_make(params, emit=lambda event: self.notify(rid, event))
            self.respond(rid, result)
        except Exception as error:
            self.fail(rid, INTERNAL_ERROR, f"{type(error).__name__}: {error}")

    def handle_cancel(self, rid, params):
        want = params.get("rid")
        if not (want is None or _is_int(want)):
            return self.fail(rid, INVALID_PARAMS, "rid must be an integer or null")
        with self.state:
            # Cancel targets whichever run matches the rid (scrape or grab).
            scrape_running = (
                self.threads.get("scrape") is not None
                and self.threads["scrape"].is_alive()
                and self.scrape_rid is not None
            )
            grab_running = (
                self.threads.get("grab") is not None
                and self.threads["grab"].is_alive()
                and getattr(self, "grab_rid", None) is not None
            )
            if scrape_running and (want is None or want == self.scrape_rid):
                target = self.scrape_rid
                first = not self.cancelling
                self.cancelling = True
                self.cancel_event.set()
                spider = self.spider
                if first and spider is not None:
                    spider.request_cancel()
                self.respond(rid, {"cancelling": True, "rid": target})
                return
            if grab_running and (want is None or want == self.grab_rid):
                target = self.grab_rid
                self.grab_cancel_event.set()
                self.respond(rid, {"cancelling": True, "rid": target})
                return
            return self.fail(rid, NO_SCRAPE, "no matching scrape or grab running")

    # ---- lifecycle ----

    def shutdown(self):
        """EOF: the parent is gone. Silence the sink, cancel, bounded join, exit."""
        with self.write_lock:
            self.closed = True
        with self.state:
            if self.cancel_event is not None:
                self.cancel_event.set()
            if self.grab_cancel_event is not None:
                self.grab_cancel_event.set()
            spider = self.spider
        if spider is not None:
            spider.request_cancel()
        for thread in list(self.threads.values()):
            thread.join(timeout=JOIN_TIMEOUT)
        self._exit(0)


def serve_stdio():
    """Entry point for `iofast rpc`: harden stdout, then serve until EOF."""
    default_storage_env()
    # Dup the real stdout for protocol lines, then point fd 1 at stderr so a
    # dependency that print()s can never corrupt the protocol stream.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    RpcServer(stdout=protocol).serve()
    return 0  # unreachable: serve() ends in os._exit(0)
