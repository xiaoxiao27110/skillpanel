#!/usr/bin/env python3
"""Exercise direct ``opencode`` TUI hot reload through its private runtime."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import signal
import stat
import struct
import subprocess
import sys
import termios
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opencode_session import (
    HttpClient,
    TestFailure,
    as_list,
    as_mapping,
    assert_minimax_available,
    assert_pids,
    controller_catalog,
    create_session,
    get_session,
    pid_snapshot,
    public_endpoint,
    require,
    run_round,
    safe_error,
    set_skill_enabled,
    skill_enabled,
)


@dataclass(frozen=True)
class RuntimeRegistration:
    path: Path
    pid: int
    start_time: str
    directory: str
    url: str


class PtyProcess:
    """Keep a real terminal attached while discarding TUI redraw output."""

    def __init__(self, command: list[str], directory: str):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        environment = dict(os.environ)
        environment.setdefault("TERM", "xterm-256color")
        environment.setdefault("COLORTERM", "truecolor")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=directory,
                env=environment,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
        finally:
            os.close(slave)
        self.master = master
        self._drain = threading.Thread(target=self._discard_output, daemon=True)
        self._drain.start()

    def _discard_output(self) -> None:
        while True:
            try:
                if not os.read(self.master, 8192):
                    return
            except OSError:
                return

    def stop(self) -> None:
        if self.process.poll() is None:
            # This is a programmatic teardown, not a terminal Ctrl+C gesture.
            # SIGTERM exercises the launcher's forwarding/cleanup path without
            # waiting for OpenCode's interactive double-Ctrl+C confirmation.
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        try:
            os.close(self.master)
        except OSError:
            pass
        self._drain.join(timeout=1)


@dataclass(frozen=True)
class ActiveRuntime:
    role: str
    launcher: PtyProcess
    registration: RuntimeRegistration
    client: HttpClient


def process_start_time(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    closing_parenthesis = raw.rfind(")")
    fields = raw[closing_parenthesis + 1 :].split() if closing_parenthesis >= 0 else []
    return fields[19] if len(fields) > 19 else None


def registry_snapshot(directory: Path) -> dict[Path, tuple[int, int]]:
    result: dict[Path, tuple[int, int]] = {}
    try:
        candidates = directory.glob("*.json")
        for path in candidates:
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError:
                continue
            result[path] = (metadata.st_ino, metadata.st_mtime_ns)
    except OSError:
        pass
    return result


def read_registration(path: Path) -> RuntimeRegistration:
    metadata = path.stat(follow_symlinks=False)
    require(stat.S_ISREG(metadata.st_mode), f"registry entry {path} is not a regular file")
    require(metadata.st_uid == 0, f"registry entry {path} is not owned by root")
    require(
        stat.S_IMODE(metadata.st_mode) & 0o007 == 0,
        f"registry entry {path} is accessible to other users",
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TestFailure(f"registry entry {path} is not valid JSON") from exc
    item = as_mapping(payload, "OpenCode TUI registry entry")
    pid = item.get("pid")
    start_time = item.get("start_time")
    directory = item.get("directory")
    url = item.get("url")
    require(type(pid) is int and pid > 0, "registry entry has an invalid pid")
    require(
        type(start_time) in (str, int) and not isinstance(start_time, bool),
        "registry entry has an invalid start_time",
    )
    require(isinstance(directory, str) and os.path.isabs(directory), "invalid TUI directory")
    require(isinstance(url, str), "registry entry has an invalid URL")
    parsed = urllib.parse.urlsplit(url)
    require(
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.port is not None
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment,
        "registered TUI runtime is not a private loopback HTTP endpoint",
    )
    return RuntimeRegistration(path, pid, str(start_time), directory, url.rstrip("/"))


def wait_for_registration(
    directory: Path,
    before: dict[Path, tuple[int, int]],
    launcher: PtyProcess,
    expected_directory: str,
    timeout: float,
) -> RuntimeRegistration:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        return_code = launcher.process.poll()
        if return_code is not None:
            raise TestFailure(f"direct opencode exited before registration (status {return_code})")
        current = registry_snapshot(directory)
        changed = [path for path, identity in current.items() if before.get(path) != identity]
        for path in changed:
            try:
                runtime = read_registration(path)
                require(
                    runtime.directory == os.path.realpath(expected_directory),
                    "registered TUI directory does not match its working directory",
                )
                require(
                    process_start_time(runtime.pid) == runtime.start_time,
                    "registered TUI pid/start_time is not live",
                )
                health = as_mapping(
                    HttpClient(runtime.url, 2).request("/global/health"),
                    "direct TUI GET /global/health",
                )
                require(health.get("healthy") is True, "direct TUI runtime is not healthy")
                return runtime
            except Exception as exc:  # A registry write may precede the HTTP listener.
                last_error = exc
        time.sleep(0.1)
    if last_error is not None:
        raise TestFailure(f"direct opencode runtime did not become ready: {last_error}")
    raise TestFailure("direct opencode did not create a TUI runtime registration")


def assert_runtime_identity(runtime: RuntimeRegistration) -> None:
    require(runtime.path.exists(), "direct TUI registry entry disappeared")
    current = read_registration(runtime.path)
    require(current == runtime, "direct TUI runtime registration changed")
    require(
        process_start_time(runtime.pid) == runtime.start_time,
        "direct TUI native process changed or exited",
    )


def skill_entries(opencode: HttpClient, directory: str, skill: str) -> list[dict[str, Any]]:
    payload = as_list(
        opencode.request("/skill", query={"directory": directory}),
        "direct TUI GET /skill",
    )
    return [
        as_mapping(item, "direct TUI skill")
        for item in payload
        if isinstance(item, dict) and item.get("name") == skill
    ]


def skill_command_names(opencode: HttpClient, directory: str) -> set[str]:
    payload = as_list(
        opencode.request("/command", query={"directory": directory}),
        "direct TUI GET /command",
    )
    return {
        item["name"]
        for raw in payload
        if isinstance(raw, dict)
        for item in [raw]
        if item.get("source") == "skill" and isinstance(item.get("name"), str)
    }


def assert_runtime_stage(
    active: ActiveRuntime,
    *,
    directory: str,
    session_id: str | None,
    skill: str,
    enabled: bool,
    enabled_root: Path,
) -> dict[str, Any]:
    runtime = active.registration
    opencode = active.client
    assert_runtime_identity(runtime)

    # These are intentionally single, immediate reads. A successful PUT is the
    # synchronization barrier; this test must not hide delayed convergence by polling.
    entries = skill_entries(opencode, directory, skill)
    commands = skill_command_names(opencode, directory)
    require(bool(entries) is enabled, "direct TUI /skill did not converge before PUT returned")
    require(
        (skill in commands) is enabled,
        "direct TUI /command did not converge before PUT returned",
    )
    if enabled:
        expected_location = os.path.realpath(enabled_root / skill / "SKILL.md")
        locations = {
            os.path.realpath(str(item.get("location")))
            for item in entries
            if isinstance(item.get("location"), str)
        }
        require(
            expected_location in locations,
            "direct TUI loaded a same-named skill from the wrong source",
        )
    if session_id is not None:
        get_session(opencode, directory, session_id)
    result: dict[str, Any] = {
        "role": active.role,
        "pid": runtime.pid,
        "start_time": runtime.start_time,
        "pid_and_start_time_unchanged": True,
        "session_checked": session_id is not None,
        "skill_catalog_matches": True,
        "command_catalog_matches": True,
    }
    if session_id is not None:
        result.update({"session_id": session_id, "session_id_unchanged": True})
    return result


def assert_stage(
    controller: HttpClient,
    runtimes: list[ActiveRuntime],
    *,
    directory: str,
    main_session_id: str | None,
    skill: str,
    enabled: bool,
    enabled_root: Path,
    expected_pids: dict[str, int],
) -> dict[str, Any]:
    require(runtimes, "no direct TUI runtimes are available for stage validation")
    runtime_results: dict[str, dict[str, Any]] = {}
    for active in runtimes:
        result = assert_runtime_stage(
            active,
            directory=directory,
            session_id=main_session_id if active.role == "main" else None,
            skill=skill,
            enabled=enabled,
            enabled_root=enabled_root,
        )
        runtime_results[active.role] = result

    catalog = controller_catalog(controller)
    assert_pids(catalog, expected_pids, "direct TUI stage")
    require(skill_enabled(catalog, skill) is enabled, "controller has the wrong skill state")
    return {
        "revision": catalog["revision"],
        "enabled": enabled,
        "runtime_count": len(runtimes),
        "runtimes": runtime_results,
        "supervised_pids_unchanged": True,
    }


def wait_for_registry_removal(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    require(not path.exists(), "direct TUI registry entry remained after process exit")


def execute(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    controller = HttpClient(args.controller_url, args.timeout)
    registry_dir = Path(args.registry_dir)
    summary: dict[str, Any] = {
        "ok": False,
        "controller": public_endpoint(args.controller_url),
        "directory": os.path.realpath(args.directory),
        "skill": args.skill,
        "llm": args.llm,
        "stages": [],
    }
    original_enabled: bool | None = None
    baseline_pids: dict[str, int] | None = None
    launchers: list[tuple[str, PtyProcess]] = []
    runtimes: list[ActiveRuntime] = []
    session_id: str | None = None
    primary_error: dict[str, Any] | None = None
    cleanup_errors: list[dict[str, Any]] = []

    try:
        initial = controller_catalog(controller)
        original_enabled = skill_enabled(initial, args.skill)
        baseline_pids = pid_snapshot(initial, "direct TUI initial catalog")
        summary["original_enabled"] = original_enabled
        summary["supervised_pids"] = baseline_pids

        for role in ("main", "peer"):
            before = registry_snapshot(registry_dir)
            launcher = PtyProcess([args.opencode_command], args.directory)
            launchers.append((role, launcher))
            registration = wait_for_registration(
                registry_dir, before, launcher, args.directory, args.startup_timeout
            )
            runtimes.append(
                ActiveRuntime(
                    role,
                    launcher,
                    registration,
                    HttpClient(registration.url, args.timeout),
                )
            )

        require(
            len({active.registration.pid for active in runtimes}) == 2,
            "the two direct TUI runtimes share a native pid",
        )
        require(
            len({active.registration.url for active in runtimes}) == 2,
            "the two direct TUI runtimes share a loopback endpoint",
        )
        summary["tuis"] = {
            active.role: {
                "pid": active.registration.pid,
                "start_time": active.registration.start_time,
                "url": public_endpoint(active.registration.url),
            }
            for active in runtimes
        }
        main_runtime = runtimes[0]
        if args.llm:
            assert_minimax_available(
                main_runtime.client, args.directory, args.provider, args.model
            )
        session_id = create_session(
            main_runtime.client, args.directory, args.provider, args.model, args.agent
        )
        summary["session_id"] = session_id

        for name, enabled in (("enabled", True), ("disabled", False), ("reenabled", True)):
            set_skill_enabled(controller, args.skill, enabled, baseline_pids)
            state = assert_stage(
                controller,
                runtimes,
                directory=args.directory,
                main_session_id=session_id,
                skill=args.skill,
                enabled=enabled,
                enabled_root=Path(args.enabled_root),
                expected_pids=baseline_pids,
            )
            model_result: dict[str, Any] = {}
            if args.llm:
                model_result = run_round(
                    main_runtime.client,
                    directory=args.directory,
                    session_id=session_id,
                    provider=args.provider,
                    model=args.model,
                    agent=args.agent,
                    skill=args.skill,
                    canary=args.canary,
                    enabled=enabled,
                )
            summary["stages"].append({"name": name, **state, **model_result})
    except Exception as exc:  # JSON output replaces a potentially noisy TUI traceback.
        primary_error = safe_error(exc)
    finally:
        if original_enabled is not None:
            try:
                restored = set_skill_enabled(
                    controller, args.skill, original_enabled, baseline_pids
                )
                if runtimes and baseline_pids is not None:
                    restored_state = assert_stage(
                        controller,
                        runtimes,
                        directory=args.directory,
                        main_session_id=session_id,
                        skill=args.skill,
                        enabled=original_enabled,
                        enabled_root=Path(args.enabled_root),
                        expected_pids=baseline_pids,
                    )
                else:
                    final = controller_catalog(controller)
                    require(
                        skill_enabled(final, args.skill) is original_enabled,
                        "cleanup did not restore the original controller state",
                    )
                    if baseline_pids is not None:
                        assert_pids(final, baseline_pids, "direct TUI cleanup")
                    restored_state = {}
                summary["restore"] = {
                    "ok": True,
                    "original_enabled": original_enabled,
                    "final_enabled": original_enabled,
                    "put_revision": restored["revision"],
                    **restored_state,
                }
            except Exception as exc:
                cleanup_errors.append(safe_error(exc))
                summary["restore"] = {"ok": False, "error": safe_error(exc)}
        else:
            summary["restore"] = {
                "ok": False,
                "attempted": False,
                "reason": "initial controller state was unavailable",
            }

        if launchers:
            cleanup_results: dict[str, dict[str, Any]] = {}
            runtime_by_launcher = {id(active.launcher): active for active in runtimes}
            for role, launcher in reversed(launchers):
                active = runtime_by_launcher.get(id(launcher))
                try:
                    launcher.stop()
                    if active is not None:
                        registration = active.registration
                        wait_for_registry_removal(registration.path, 5)
                        require(
                            process_start_time(registration.pid) != registration.start_time,
                            f"registered {role} native TUI process survived launcher exit",
                        )
                    cleanup_results[role] = {
                        "ok": True,
                        "registry_removed": active is not None,
                    }
                except Exception as exc:
                    cleanup_errors.append(safe_error(exc))
                    cleanup_results[role] = {"ok": False, "error": safe_error(exc)}
            summary["tui_cleanup"] = {
                "ok": all(result["ok"] for result in cleanup_results.values()),
                "runtimes": cleanup_results,
            }

    if primary_error is not None:
        summary["error"] = primary_error
    if cleanup_errors:
        summary["cleanup_errors"] = cleanup_errors
        if primary_error is None:
            summary["error"] = cleanup_errors[0]
    summary["ok"] = primary_error is None and not cleanup_errors
    summary["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-url", default="http://127.0.0.1:8787")
    parser.add_argument("--registry-dir", default="/run/skillpanel-opencode-tuis")
    parser.add_argument("--directory", default="/workspace")
    parser.add_argument(
        "--enabled-root", default=os.path.expanduser("~/.agents/skills")
    )
    parser.add_argument("--opencode-command", default="opencode")
    parser.add_argument("--startup-timeout", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--provider", default="minimax-cn-coding-plan")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--agent", default="build")
    parser.add_argument("--skill", default="canary-beta")
    parser.add_argument("--canary", default="SKILLPANEL_BETA_91C4")
    args = parser.parse_args()
    if args.startup_timeout <= 0 or args.timeout <= 0:
        parser.error("timeouts must be positive")
    return args


def main() -> int:
    summary = execute(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
