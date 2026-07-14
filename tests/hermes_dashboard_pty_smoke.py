#!/usr/bin/env python3
"""Spawn and reap the Hermes Dashboard bundled TUI through ``/api/pty``."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from hermes_cli import main as hermes_main
from integration import _hermes_ticket, require


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    pgid: int
    start_time: str
    argv: tuple[str, ...]


def _read_process(pid: int) -> ProcessInfo | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        raw_argv = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, UnicodeError):
        return None

    closing_parenthesis = stat_text.rfind(")")
    fields = stat_text[closing_parenthesis + 1 :].split() if closing_parenthesis >= 0 else []
    if len(fields) <= 19:
        return None
    argv = tuple(
        part.decode("utf-8", errors="surrogateescape")
        for part in raw_argv.split(b"\0")
        if part
    )
    return ProcessInfo(
        pid=pid,
        ppid=int(fields[1]),
        pgid=int(fields[2]),
        start_time=fields[19],
        argv=argv,
    )


def _processes() -> list[ProcessInfo]:
    result: list[ProcessInfo] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        process = _read_process(int(entry.name))
        if process is not None:
            result.append(process)
    return result


def _matching_children(parent_pid: int, bundled_tui: Path) -> list[ProcessInfo]:
    expected_argv = (
        "/usr/local/bin/node",
        "--expose-gc",
        str(bundled_tui),
    )
    return [
        process
        for process in _processes()
        if process.ppid == parent_pid and process.argv == expected_argv
    ]


def _same_process(process: ProcessInfo) -> bool:
    current = _read_process(process.pid)
    return current is not None and current.start_time == process.start_time


def _group_members(pgid: int) -> list[int]:
    return [process.pid for process in _processes() if process.pgid == pgid]


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _wait_for_new_child(
    parent_pid: int,
    bundled_tui: Path,
    before: set[tuple[int, str]],
    timeout: float,
) -> ProcessInfo:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [
            process
            for process in _matching_children(parent_pid, bundled_tui)
            if (process.pid, process.start_time) not in before
        ]
        if len(candidates) == 1:
            return candidates[0]
        require(
            len(candidates) < 2,
            "multiple new bundled TUI children appeared during the isolated smoke",
        )
        time.sleep(0.05)
    raise AssertionError("Hermes /api/pty did not spawn the bundled TUI before timeout")


def _terminate_process_group(
    process: ProcessInfo, baseline: set[tuple[int, str]]
) -> None:
    require(process.pgid == process.pid, "Hermes PTY child is not its process-group leader")
    require(process.pgid > 1, "refusing to signal an unsafe process group")
    require(process.pgid != os.getpgrp(), "refusing to signal the smoke process group")

    for received, grace in (
        (signal.SIGHUP, 0.5),
        (signal.SIGTERM, 0.75),
        (signal.SIGKILL, 1.0),
    ):
        members = [item for item in _processes() if item.pgid == process.pgid]
        if not members:
            return
        leader = _read_process(process.pid)
        if leader is not None:
            require(
                leader.start_time == process.start_time
                and leader.argv == process.argv
                and leader.pgid == process.pgid,
                "refusing to signal a reused Hermes PTY process group",
            )
        require(
            all((item.pid, item.start_time) not in baseline for item in members),
            "refusing to signal a process group containing a baseline process",
        )
        require(
            all(int(item.start_time) >= int(process.start_time) for item in members),
            "refusing to signal a process group containing an older process",
        )
        try:
            os.killpg(process.pgid, received)
        except ProcessLookupError:
            return
        _wait_until(lambda: not _group_members(process.pgid), grace)


def execute(args: argparse.Namespace) -> dict[str, object]:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise AssertionError("Hermes /api/pty smoke requires websockets sync support") from exc

    bundled_tui = hermes_main._find_bundled_tui()
    require(bundled_tui is not None and bundled_tui.is_file(), "bundled Hermes TUI is missing")
    bundled_tui = bundled_tui.resolve()
    source_workspace = hermes_main.PROJECT_ROOT / "ui-tui"
    require(not source_workspace.exists(), "Hermes source ui-tui workspace unexpectedly exists")

    parent_pid = int(Path(args.hermes_pid_file).read_text(encoding="utf-8").strip())
    parent = _read_process(parent_pid)
    require(parent is not None, "Hermes dashboard PID is not live")
    baseline = {(process.pid, process.start_time) for process in _processes()}
    before = {
        (process.pid, process.start_time)
        for process in _matching_children(parent_pid, bundled_tui)
    }

    ticket = _hermes_ticket(args.base_url.rstrip("/"), args.username, args.password)
    parsed = urllib.parse.urlsplit(args.base_url)
    require(parsed.scheme in {"http", "https"} and parsed.netloc, "invalid Hermes base URL")
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    prefix = parsed.path.rstrip("/")
    ws_url = (
        f"{ws_scheme}://{parsed.netloc}{prefix}/api/pty?"
        + urllib.parse.urlencode({"ticket": ticket})
    )
    origin = f"{parsed.scheme}://{parsed.netloc}"

    websocket = None
    child: ProcessInfo | None = None
    group_snapshot: list[ProcessInfo] = []
    graceful_reap = True
    close_error: str | None = None
    try:
        try:
            websocket = connect(
                ws_url,
                origin=origin,
                proxy=None,
                open_timeout=min(args.timeout, 30),
                ping_interval=None,
                close_timeout=2,
                max_size=8 * 1024 * 1024,
            )
        except Exception as exc:
            safe_message = str(exc).replace(ticket, "<redacted-ticket>")
            raise AssertionError(f"Hermes /api/pty connection failed: {safe_message}") from exc
        child = _wait_for_new_child(parent_pid, bundled_tui, before, args.timeout)
        require(child.argv[0] == "/usr/local/bin/node", "Hermes PTY did not use bundled Node")
        require(child.argv[1] == "--expose-gc", "Hermes PTY missed --expose-gc")
        require(Path(child.argv[2]).resolve() == bundled_tui, "Hermes PTY used the wrong TUI")
        require("ui-tui" not in child.argv[2], "Hermes PTY used the source workspace")
        require(child.pgid == child.pid, "Hermes PTY child is not a process-group leader")
        frame = websocket.recv(timeout=min(args.timeout, 10))
        require(isinstance(frame, bytes) and frame, "Hermes /api/pty sent no binary TUI frame")
        require(_same_process(child), "Hermes bundled TUI exited after its first PTY frame")
        group_snapshot = [
            process for process in _processes() if process.pgid == child.pgid
        ]
        require(
            any(
                process.pid == child.pid and process.start_time == child.start_time
                for process in group_snapshot
            ),
            "Hermes bundled TUI was missing from its captured process group",
        )
    finally:
        if child is None and websocket is not None:
            candidates = [
                process
                for process in _matching_children(parent_pid, bundled_tui)
                if (process.pid, process.start_time) not in before
            ]
            if len(candidates) == 1:
                child = candidates[0]
        if websocket is not None:
            try:
                websocket.close()
            except Exception as exc:
                close_error = str(exc).replace(ticket, "<redacted-ticket>")
        if child is not None:
            graceful_reap = _wait_until(lambda: not _group_members(child.pgid), 5)
            if not graceful_reap:
                _terminate_process_group(child, baseline)
            require(
                _wait_until(lambda: not _same_process(child), 3),
                f"Hermes bundled TUI PID {child.pid} survived smoke cleanup",
            )
            require(
                _wait_until(lambda: not _group_members(child.pgid), 3),
                f"Hermes bundled TUI process group {child.pgid} survived smoke cleanup",
            )

    require(child is not None, "Hermes /api/pty connection did not create an identifiable child")
    require(graceful_reap, "Hermes /api/pty did not reap its process group after WebSocket close")
    require(close_error is None, f"Hermes /api/pty WebSocket close failed: {close_error}")
    require(_same_process(parent), "Hermes dashboard process changed during /api/pty smoke")
    return {
        "ok": True,
        "dashboard_pid": parent_pid,
        "pid": child.pid,
        "start_time": child.start_time,
        "pgid": child.pgid,
        "argv": list(child.argv),
        "processes": [
            {"pid": process.pid, "start_time": process.start_time}
            for process in group_snapshot
        ],
        "source_workspace_absent": True,
        "graceful_reap": True,
        "pid_removed": True,
        "process_group_empty": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:9119")
    parser.add_argument("--username", default=os.getenv("HERMES_DASHBOARD_BASIC_AUTH_USERNAME", "skillpanel"))
    parser.add_argument("--password", default=os.getenv("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "skillpanel-dev"))
    parser.add_argument("--hermes-pid-file", default="/run/hermes.pid")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main() -> int:
    try:
        summary = execute(parse_args())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
