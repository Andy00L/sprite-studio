#!/usr/bin/env python3
"""Sprite Studio Bridge — REST sidecar for the sprite-studio Hermes plugin.

Why this exists:
  Hermes 0.12.0's OpenAI-compatible API server (/v1/chat/completions) does
  not dispatch plugin slash commands — it routes user messages through the
  LLM as conversation. Plugin slash commands only fire via the gateway's
  chat router (Telegram/Discord/Slack/CLI).

  This sidecar imports the sprite-studio plugin directly and exposes its
  registered handlers as POST /slash, so the web app can invoke them as
  deterministic REST calls without an LLM in the loop.

Endpoints:
  GET  /health                — liveness + plugin-loaded check
  POST /slash {command, args} — dispatch a registered slash command

Auth:
  Bearer token from the API_SERVER_KEY env var (same key as the Hermes API
  server, so a single secret is shared between surfaces).

Run:
  set -a; source ~/.hermes/.env; set +a
  /home/drew/.hermes/hermes-agent/venv/bin/python3 \\
      /home/drew/sprite-studio/bridge/server.py

Env overrides:
  SPRITE_BRIDGE_HOST   default 127.0.0.1
  SPRITE_BRIDGE_PORT   default 8643
  SPRITE_PLUGIN_PATH   default ~/.hermes/plugins/sprite-studio
  API_SERVER_KEY       required (will also be loaded from ~/.hermes/.env)
"""
from __future__ import annotations

import asyncio
import atexit
import fcntl
import importlib.util
import json
import logging
import os
import signal
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("sprite_bridge")

# Bridge process lifecycle (P19a-18): a PID file plus a flock-protected
# pre-flight kill make `npm run dev:bridge` self-healing in the face of
# stale orphans (detached `python server.py`, supervisor SIGKILL, etc.).
_RUN_DIR = (Path("~/.hermes/plugins/sprite-studio/run").expanduser())
_PID_FILE = _RUN_DIR / "bridge.pid"
_LOCK_FILE = _RUN_DIR / "bridge.lock"

# Defaults; main() resolves the actual bridge port from env at startup.
_PORT_BRIDGE = 8643
_PORT_ASSET = 9120

# The lock fd is kept open until just after the PID file is written. Closing
# it releases the kernel-held flock so the next `npm run dev` can take over
# without manual intervention.
_lock_fd: int | None = None


def _load_dotenv_for_api_key() -> None:
    """Backfill API_SERVER_KEY from ~/.hermes/.env if not already in env."""
    if os.environ.get("API_SERVER_KEY"):
        return
    dotenv = Path("~/.hermes/.env").expanduser()
    if not dotenv.exists():
        return
    try:
        for raw_line in dotenv.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key == "API_SERVER_KEY" and value.strip():
                os.environ["API_SERVER_KEY"] = value.strip()
                break
    except OSError:
        pass


def _read_pid_file() -> tuple[int, str, int] | None:
    """Return (pid, comm_basename, port) from the PID file, or None if it is
    absent or malformed. Format: `<pid> <comm> <port> <iso_timestamp>`."""
    if not _PID_FILE.exists():
        return None
    try:
        parts = _PID_FILE.read_text().strip().split()
        if len(parts) < 3:
            return None
        return int(parts[0]), parts[1], int(parts[2])
    except (OSError, ValueError):
        return None


def _proc_comm(pid: int) -> str | None:
    """Return /proc/<pid>/comm (kernel-truncated to 15 chars), or None if the
    pid is gone."""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except (OSError, FileNotFoundError):
        return None


def _is_python_process(pid: int) -> bool:
    """True iff /proc/<pid>/comm starts with "python". The PID-reuse safety
    net: even if the OS reassigned this PID to an unrelated process, we will
    not kill it unless its comm matches a Python interpreter."""
    comm = _proc_comm(pid)
    return comm is not None and comm.startswith("python")


def _kill_with_escalation(pid: int, label: str, timeout_s: float = 5.0) -> None:
    """SIGTERM, wait up to `timeout_s`, then escalate to SIGKILL. No-op if
    the process is already gone."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            logger.info("%s pid=%d exited after SIGTERM", label, pid)
            return
        time.sleep(0.1)
    logger.warning(
        "%s pid=%d did not exit in %.1fs; escalating to SIGKILL",
        label, pid, timeout_s,
    )
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _find_pid_holding_port(port: int) -> int | None:
    """Scan /proc/net/tcp for a LISTEN row on 127.0.0.1:<port>, then walk
    /proc/*/fd to map the socket inode to the owning PID. IPv4 only, since
    the bridge binds 127.0.0.1 explicitly."""
    port_hex = f"{port:04X}"
    inode = 0
    try:
        with open("/proc/net/tcp", "r") as fh:
            next(fh, None)  # skip header
            for line in fh:
                cols = line.split()
                if len(cols) < 10:
                    continue
                if cols[3] != "0A":  # not LISTEN
                    continue
                if not cols[1].endswith(":" + port_hex):
                    continue
                try:
                    inode = int(cols[9])
                except ValueError:
                    continue
                if inode != 0:
                    break
    except OSError:
        return None
    if inode == 0:
        return None

    target = f"socket:[{inode}]"
    try:
        proc_entries = os.listdir("/proc")
    except OSError:
        return None
    for entry in proc_entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(f"{fd_dir}/{fd}") == target:
                    return pid
            except OSError:
                continue
    return None


def _acquire_start_lock() -> None:
    """Take an exclusive non-blocking lock on bridge.lock. The lock is held
    only across pre-flight + PID-file write (microseconds) so that a healthy
    running bridge does not block a future `npm run dev` from taking over.
    Concurrent starts that race on the lock have one winner; the loser
    aborts with exit code 2."""
    global _lock_fd
    _RUN_DIR.mkdir(parents=True, exist_ok=True)
    _lock_fd = os.open(str(_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.stderr.write(
            "[bridge] another bridge instance is starting on the same lock; "
            "aborting. If this is wrong, remove "
            f"{_LOCK_FILE} and retry.\n"
        )
        os.close(_lock_fd)
        _lock_fd = None
        sys.exit(2)


def _release_start_lock() -> None:
    """Close the lock fd. The kernel releases the flock; the file itself is
    left in place for the next start to reuse."""
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        os.close(_lock_fd)
    except OSError:
        pass
    _lock_fd = None


def _preflight_cleanup() -> None:
    """Remove or kill any prior bridge before binding.

    Two passes:
      1. PID-file pass: if the file points at a live python process, SIGTERM
         (then SIGKILL) it. Then delete the file.
      2. Port-holder pass: independently scan /proc/net/tcp for any process
         holding 8643 or 9120. Catches bridges that started without writing a
         PID file (direct `python server.py`, standalone asset_server.py).
         Only kills if the holder's comm matches a Python interpreter to
         avoid harming unrelated processes that happen to use the same port.
    """
    record = _read_pid_file()
    if record is not None:
        pid, _comm, _port = record
        if _is_python_process(pid):
            logger.warning(
                "preflight: prior bridge pid=%d still alive; terminating",
                pid,
            )
            _kill_with_escalation(pid, label="prior bridge")
        else:
            logger.info(
                "preflight: stale pid file (pid=%d not a python process)",
                pid,
            )
        try:
            _PID_FILE.unlink()
        except FileNotFoundError:
            pass

    for port, label in ((_PORT_BRIDGE, "bridge"), (_PORT_ASSET, "asset")):
        holder = _find_pid_holding_port(port)
        if holder is None or holder == os.getpid():
            continue
        comm = _proc_comm(holder) or "?"
        if not comm.startswith("python"):
            logger.warning(
                "preflight: port %d held by non-python pid=%d (comm=%s); "
                "skipping kill",
                port, holder, comm,
            )
            continue
        logger.warning(
            "preflight: port %d held by python pid=%d without pid file; "
            "terminating",
            port, holder,
        )
        _kill_with_escalation(holder, label=f"{label} port-holder")


def _write_pid_file() -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _PID_FILE.write_text(
        f"{os.getpid()} server.py {_PORT_BRIDGE} {timestamp}\n"
    )


def _cleanup_pid_file() -> None:
    """Remove the PID file. Idempotent; safe to call from atexit, signal
    handlers, and try/finally simultaneously."""
    try:
        _PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("could not remove pid file %s: %s", _PID_FILE, exc)


def _install_signal_handlers() -> None:
    """SIGINT and SIGTERM are usually owned by aiohttp.web.run_app's own
    asyncio shutdown path; the explicit handlers here are defense-in-depth
    for the brief window before the event loop starts. SIGHUP is not handled
    by aiohttp, so the handler below is the sole owner. atexit guarantees
    cleanup on every normal termination path."""
    def _handler(signum: int, _frame) -> None:
        logger.info("received signal %d, shutting down", signum)
        _cleanup_pid_file()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGHUP, _handler)
    atexit.register(_cleanup_pid_file)


def load_plugin(plugin_path: Path) -> types.ModuleType:
    """Import the sprite-studio plugin as ``sprite_studio``.

    Mirrors the plugin loader at
    ~/.hermes/hermes-agent/hermes_cli/plugins.py:1015 so relative imports
    inside the plugin (``from . import db``) resolve correctly.
    """
    init_file = plugin_path / "__init__.py"
    if not init_file.exists():
        raise SystemExit(f"plugin not found at {plugin_path}")

    venv_site = Path(
        "~/.hermes/hermes-agent/venv/lib/python3.11/site-packages"
    ).expanduser()
    if venv_site.exists() and str(venv_site) not in sys.path:
        sys.path.insert(0, str(venv_site))

    module_name = "sprite_studio"
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(init_file),
        submodule_search_locations=[str(plugin_path)],
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot create module spec for {init_file}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(plugin_path)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _check_auth(request: web.Request, api_key: str) -> web.Response | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return web.json_response(
            {"error": "missing or malformed Authorization header"}, status=401
        )
    if header[len("Bearer ") :] != api_key:
        return web.json_response({"error": "invalid api key"}, status=401)
    return None


def _import_asset_server() -> types.ModuleType:
    """Import the plugin's asset_server module via the same submodule search
    locations the bridge uses for the plugin itself. Must be called after
    load_plugin() has registered ``sprite_studio`` in sys.modules so that
    relative imports inside asset_server resolve correctly.
    """
    import importlib
    return importlib.import_module("sprite_studio.workers.asset_server")


async def _start_asset_server(app: web.Application) -> None:
    """Spawn the asset server (with auth-gated upload) in the same event
    loop on 9120.

    Folded into the bridge so a single `bridge/run.sh` launches both the
    REST sidecar (8643) and the asset server (9120). Failure to bind is
    logged but doesn't take the bridge down — a standalone asset_server.py
    may already be holding the port from a prior run.

    The bridge's api_key is forwarded so the upload endpoint can validate
    Bearer tokens with the same secret callers already use for /slash.
    """
    asset_server = _import_asset_server()
    plugin_path = Path(
        os.environ.get("SPRITE_PLUGIN_PATH", "~/.hermes/plugins/sprite-studio"),
    ).expanduser()
    projects_root = plugin_path / "projects"
    api_key = app.get("api_key") or os.environ.get("API_SERVER_KEY", "")
    asset_app = asset_server.make_app(
        api_key=api_key,
        projects_root=projects_root,
    )
    runner = web.AppRunner(asset_app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=9120)
    try:
        await site.start()
    except OSError as exc:
        logger.warning(
            "asset server failed to bind on 9120 (%s) — assuming a "
            "standalone instance is already running",
            exc,
        )
        await runner.cleanup()
        return
    logger.info("asset server started on http://127.0.0.1:9120")
    app["_asset_runner"] = runner


async def _stop_asset_server(app: web.Application) -> None:
    runner = app.get("_asset_runner")
    if runner is not None:
        await runner.cleanup()


def make_app(plugin: types.ModuleType, api_key: str) -> web.Application:
    slash_commands = plugin.commands.SLASH_COMMANDS

    async def health(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "plugin_loaded": True,
                "command_count": len(slash_commands),
            }
        )

    async def slash(request: web.Request) -> web.Response:
        if (err := _check_auth(request, api_key)) is not None:
            return err
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        command = (body.get("command") or "").strip().lstrip("/")
        args = body.get("args", "") or ""
        if not command:
            return web.json_response({"error": "missing 'command'"}, status=400)

        # Optional kwargs from the client. Web chat injects project_id here so
        # mutating commands target the project the user is viewing instead of
        # the backend's "latest project" fallback (P19a-27 Bug 1). Reserved
        # keys (surface, platform) are stripped so callers can't impersonate
        # a different gateway.
        raw_kwargs = body.get("kwargs") or {}
        if not isinstance(raw_kwargs, dict):
            return web.json_response(
                {"error": "kwargs must be an object"}, status=400,
            )
        client_kwargs = {
            k: v for k, v in raw_kwargs.items()
            if k not in ("surface", "platform")
        }

        meta = slash_commands.get(command)
        if meta is None:
            return web.json_response(
                {"error": f"unknown command: /{command}"}, status=404
            )

        handler = meta["handler"]
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(args, surface="api", **client_kwargs)
            else:
                result = await asyncio.to_thread(
                    handler, args, surface="api", **client_kwargs,
                )
        except Exception as exc:
            logger.exception("handler /%s raised", command)
            return web.json_response(
                {
                    "ok": False,
                    "data": None,
                    "raw": "",
                    "parseError": f"handler raised: {exc}",
                },
                status=500,
            )

        raw = str(result) if result is not None else ""
        try:
            data = json.loads(raw) if raw else None
            return web.json_response(
                {"ok": True, "data": data, "raw": raw, "parseError": None}
            )
        except json.JSONDecodeError as exc:
            return web.json_response(
                {"ok": False, "data": None, "raw": raw, "parseError": str(exc)}
            )

    async def delete_project_route(request: web.Request) -> web.Response:
        """DELETE /projects/{project_id}.

        REST sibling to the /sprite_delete_project slash command, used by the
        web lobby's per-card trash button. Same auth as /slash (Bearer). ULID
        is validated here in addition to the cascade layer (defense in depth)
        so a malformed id never reaches the orchestrator.
        """
        if (err := _check_auth(request, api_key)) is not None:
            return err

        pid = request.match_info["project_id"]
        # Inline ULID guard. Duplicates db._is_valid_ulid on purpose so the
        # bridge can reject malformed ids without importing the plugin in
        # this hot path. Crockford base32, exactly 26 chars.
        import re as _re
        if not _re.match(r"^[0-9A-HJKMNP-TV-Z]{26}$", pid):
            return web.json_response(
                {"error": "invalid_project_id", "id": pid}, status=400,
            )

        try:
            orchestrator = plugin.commands._get_orchestrator()
            result = await orchestrator.delete_project(pid)
            return web.json_response(result, status=200)
        except plugin.db.ProjectNotFoundError:
            return web.json_response(
                {"error": "not_found", "id": pid}, status=404,
            )
        except plugin.orchestrator.ProjectBusyError as e:
            return web.json_response(
                {"error": "busy", "reason": e.reason, "id": pid},
                status=409,
            )
        except ValueError as e:
            return web.json_response(
                {"error": "invalid_project_id", "id": pid, "detail": str(e)},
                status=400,
            )
        except Exception as e:
            logger.exception("delete_project failed pid=%s", pid)
            return web.json_response(
                {"error": "internal", "detail": str(e)}, status=500,
            )

    app = web.Application()
    # Stash the bridge's Bearer key on the app dict so the asset server
    # startup hook (which runs in the same process) can forward it to the
    # upload endpoint without re-reading env.
    app["api_key"] = api_key
    app.router.add_get("/health", health)
    app.router.add_post("/slash", slash)
    app.router.add_delete("/projects/{project_id}", delete_project_route)
    app.on_startup.append(_start_asset_server)
    app.on_cleanup.append(_stop_asset_server)
    return app


def main() -> None:
    global _PORT_BRIDGE

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _load_dotenv_for_api_key()
    api_key = os.environ.get("API_SERVER_KEY", "")
    if not api_key:
        raise SystemExit(
            "API_SERVER_KEY env var required. "
            "Set it in shell or in ~/.hermes/.env."
        )

    host = os.environ.get("SPRITE_BRIDGE_HOST", "127.0.0.1")
    _PORT_BRIDGE = int(os.environ.get("SPRITE_BRIDGE_PORT", "8643"))

    _install_signal_handlers()
    _acquire_start_lock()
    _preflight_cleanup()

    plugin_path = Path(
        os.environ.get(
            "SPRITE_PLUGIN_PATH", "~/.hermes/plugins/sprite-studio"
        )
    ).expanduser()
    plugin = load_plugin(plugin_path)
    logger.info(
        "loaded plugin from %s, %d commands",
        plugin_path,
        len(plugin.commands.SLASH_COMMANDS),
    )

    _write_pid_file()
    _release_start_lock()
    logger.info("starting sprite-bridge on http://%s:%d", host, _PORT_BRIDGE)
    try:
        web.run_app(
            app=make_app(plugin, api_key),
            host=host,
            port=_PORT_BRIDGE,
            print=lambda _msg: None,
        )
    finally:
        _cleanup_pid_file()


if __name__ == "__main__":
    main()
