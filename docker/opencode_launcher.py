#!/usr/bin/python3
"""Run direct OpenCode TUI sessions as controller-visible runtimes."""

from __future__ import annotations

import fcntl
import grp
import http.client
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


NATIVE_OPENCODE = "/usr/local/libexec/opencode"
DEFAULT_REGISTRY_DIR = "/run/skillpanel-opencode-tuis"
DEFAULT_STATE_FILE = "/data/skill-state.json"
STARTUP_TIMEOUT_SECONDS = 15.0
HTTP_ATTEMPT_TIMEOUT_SECONDS = 0.5
HEALTH_RETRY_SECONDS = 0.05

# Commands have their own lifecycle and must retain the native CLI behavior.
NON_TUI_COMMANDS = frozenset(
    {
        "acp",
        "agent",
        "attach",
        "auth",
        "completion",
        "console",
        "db",
        "debug",
        "export",
        "github",
        "generate",
        "import",
        "mcp",
        "models",
        "plug",
        "plugin",
        "pr",
        "providers",
        "run",
        "serve",
        "session",
        "stats",
        "uninstall",
        "upgrade",
        "web",
    }
)

VALUE_OPTIONS = frozenset(
    {
        "--agent",
        "--cors",
        "--hostname",
        "--log-level",
        "--mdns-domain",
        "--model",
        "--port",
        "--prompt",
        "--replay-limit",
        "--session",
        "-m",
        "-s",
    }
)

PASSTHROUGH_FLAGS = frozenset({"-h", "--help", "-v", "--version"})
ENDPOINT_OPTIONS = frozenset({"--hostname", "--port", "--mdns", "--mdns-domain"})
FORWARDED_SIGNALS = tuple(
    sig
    for sig in (signal.SIGHUP, signal.SIGQUIT, signal.SIGTERM)
    if sig is not None
)


def _first_positional(arguments: Sequence[str]) -> str | None:
    """Return the root command/project while skipping known option values."""

    skip_value = False
    for argument in arguments:
        if skip_value:
            skip_value = False
            continue
        if argument == "--":
            break
        if argument in VALUE_OPTIONS:
            skip_value = True
            continue
        if argument.startswith("-"):
            continue
        return argument

    if "--" in arguments:
        marker = arguments.index("--")
        if marker + 1 < len(arguments):
            return arguments[marker + 1]
    return None


def is_tui_invocation(arguments: Sequence[str]) -> bool:
    if any(argument in PASSTHROUGH_FLAGS for argument in arguments):
        return False
    if "--" in arguments:
        marker = arguments.index("--")
        positional_before_separator = _first_positional(arguments[:marker])
        if positional_before_separator is None:
            return True
        return positional_before_separator not in NON_TUI_COMMANDS
    positional = _first_positional(arguments)
    return positional not in NON_TUI_COMMANDS


def requests_mini(arguments: Sequence[str]) -> bool:
    for argument in arguments:
        if argument == "--":
            return False
        if argument == "--mini" or argument.startswith("--mini="):
            return True
    return False


def project_directory(arguments: Sequence[str], cwd: str | None = None) -> str:
    positional = _first_positional(arguments)
    base = cwd if cwd is not None else os.getcwd()
    if positional is None:
        return os.path.realpath(base)
    return os.path.realpath(os.path.join(base, os.path.expanduser(positional)))


def _tui_arguments(arguments: Sequence[str], port: int) -> list[str]:
    """Replace user endpoint flags so every registered runtime is loopback-only."""

    result: list[str] = []
    skip_value = False
    after_separator = False
    for argument in arguments:
        if after_separator:
            result.append(argument)
            continue
        if argument == "--":
            after_separator = True
            result.append(argument)
            continue
        if skip_value:
            skip_value = False
            continue
        option = argument.split("=", 1)[0]
        if option in ENDPOINT_OPTIONS:
            if option in VALUE_OPTIONS and "=" not in argument:
                skip_value = True
            continue
        result.append(argument)

    endpoint = ["--hostname=127.0.0.1", f"--port={port}"]
    if "--" in result:
        marker = result.index("--")
        result[marker:marker] = endpoint
    else:
        result.extend(endpoint)
    return result


def choose_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def read_linux_start_time(pid: int) -> int:
    """Read Linux proc stat field 22 for PID reuse protection."""

    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    closing_parenthesis = stat.rfind(")")
    fields = stat[closing_parenthesis + 1 :].split()
    if closing_parenthesis < 0 or len(fields) <= 19:
        raise RuntimeError(f"invalid /proc/{pid}/stat")
    return int(fields[19])


def initialize_runtime(
    child: subprocess.Popen[bytes],
    url: str,
    *,
    timeout: float = STARTUP_TIMEOUT_SECONDS,
) -> None:
    """Wait for the TUI HTTP runtime, then clear its startup skill cache once."""

    deadline = time.monotonic() + timeout
    last_health_error = "runtime did not report healthy"
    while True:
        return_code = child.poll()
        if return_code is not None:
            raise RuntimeError(
                f"OpenCode TUI exited with status {return_code} before becoming healthy"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"OpenCode TUI did not become healthy within {timeout:g}s: "
                f"{last_health_error}"
            )

        health = urllib.request.Request(
            f"{url}/global/health",
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                health,
                timeout=min(HTTP_ATTEMPT_TIMEOUT_SECONDS, remaining),
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("healthy") is True:
                break
            last_health_error = "runtime returned an unhealthy response"
        except (
            OSError,
            ValueError,
            urllib.error.URLError,
            http.client.HTTPException,
        ) as exc:
            last_health_error = str(exc) or type(exc).__name__

        if child.poll() is not None:
            continue
        time.sleep(min(HEALTH_RETRY_SECONDS, max(0.0, deadline - time.monotonic())))

    if child.poll() is not None:
        raise RuntimeError("OpenCode TUI exited after health check")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError(f"OpenCode TUI initialization exceeded {timeout:g}s")

    dispose = urllib.request.Request(f"{url}/global/dispose", method="POST")
    try:
        with urllib.request.urlopen(
            dispose,
            # Health is a cheap retry probe, but disposing a real workspace can
            # legitimately take longer when many plugins/instances are loaded.
            # Give the synchronization barrier the rest of the startup budget.
            timeout=remaining,
        ) as response:
            response.read()
    except (
        OSError,
        urllib.error.URLError,
        http.client.HTTPException,
    ) as exc:
        raise RuntimeError(f"OpenCode TUI initial dispose failed: {exc}") from exc
    if child.poll() is not None:
        raise RuntimeError("OpenCode TUI exited during initial dispose")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def state_lock_path() -> Path:
    state_file = Path(os.environ.get("SKILLPANEL_STATE_FILE", DEFAULT_STATE_FILE))
    return state_file.with_suffix(".lock")


@contextmanager
def locked_state(path: Path) -> Iterator[None]:
    # entrypoint pre-creates this as skillpanel:skillpanel 0660. Never let the
    # root launcher recreate a deleted lock with controller-incompatible owner.
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_registry_record(
    registry_dir: Path,
    record: dict[str, object],
    *,
    owner_uid: int,
    owner_gid: int,
) -> Path:
    pid = int(record["pid"])
    target = registry_dir / f"{pid}.json"
    temporary = registry_dir / f".{pid}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(temporary, flags, 0o640)
    try:
        os.fchown(descriptor, owner_uid, owner_gid)
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            descriptor = -1
            json.dump(record, stream, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(registry_dir)
        return target
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def remove_registry_record(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _terminate_after_setup_failure(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def run_tui(
    arguments: Sequence[str],
    *,
    registry_dir: Path | None = None,
    owner_uid: int = 0,
    owner_gid: int | None = None,
    startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
    lock_file: Path | None = None,
) -> int:
    directory = registry_dir or Path(
        os.environ.get("SKILLPANEL_OPENCODE_TUI_DIR", DEFAULT_REGISTRY_DIR)
    )
    gid = owner_gid if owner_gid is not None else grp.getgrnam("skillpanel").gr_gid
    state_lock = lock_file or state_lock_path()
    child: subprocess.Popen[bytes] | None = None
    registry: Path | None = None
    old_handlers: dict[signal.Signals, object] = {}

    try:
        with locked_state(state_lock):
            port = choose_loopback_port()
            command = [NATIVE_OPENCODE, *_tui_arguments(arguments, port)]
            child = subprocess.Popen(command)
            try:
                # The native child shares the foreground process group and receives
                # the terminal's SIGINT directly. Ignore it here to avoid a duplicate.
                old_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, signal.SIG_IGN)

                def forward(received: int, _frame: object) -> None:
                    if child is not None and child.poll() is None:
                        try:
                            child.send_signal(received)
                        except ProcessLookupError:
                            pass

                for forwarded_signal in FORWARDED_SIGNALS:
                    old_handlers[forwarded_signal] = signal.signal(
                        forwarded_signal, forward
                    )

                runtime_url = f"http://127.0.0.1:{port}"
                initialize_runtime(child, runtime_url, timeout=startup_timeout)
                start_time = read_linux_start_time(child.pid)
                registry = write_registry_record(
                    directory,
                    {
                        "directory": project_directory(arguments),
                        "pid": child.pid,
                        "start_time": start_time,
                        "url": runtime_url,
                    },
                    owner_uid=owner_uid,
                    owner_gid=gid,
                )
            except BaseException:
                _terminate_after_setup_failure(child)
                raise

        return child.wait()
    except BaseException:
        if child is not None:
            _terminate_after_setup_failure(child)
        raise
    finally:
        for forwarded_signal, old_handler in old_handlers.items():
            signal.signal(forwarded_signal, old_handler)
        if registry is not None:
            remove_registry_record(registry)


def main(arguments: Sequence[str] | None = None) -> int:
    cli_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not is_tui_invocation(cli_arguments):
        os.execv(NATIVE_OPENCODE, [NATIVE_OPENCODE, *cli_arguments])
        raise AssertionError("unreachable")
    if requests_mini(cli_arguments):
        print(
            "opencode launcher: --mini is unsupported because SkillPanel hot reload "
            "requires the standard OpenCode TUI runtime",
            file=sys.stderr,
        )
        return 2

    try:
        return_code = run_tui(cli_arguments)
    except (OSError, RuntimeError, KeyError) as exc:
        print(f"opencode launcher: {exc}", file=sys.stderr)
        return 1
    if return_code < 0:
        received = -return_code
        signal.signal(received, signal.SIG_DFL)
        os.kill(os.getpid(), received)
        return 128 + received
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
