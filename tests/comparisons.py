#!/usr/bin/env python3
"""Negative-control experiments for SkillPanel hot reload.

Run as root inside the PoC container. The controller is SIGSTOP'ed while a
skill directory is moved directly, then the directory is restored and the
controller is SIGCONT'ed in a ``finally`` block.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

from integration import CANARY_SKILL, HermesRpcClient, request, require


BASELINE_TOKEN = "SKILLPANEL_NATIVE_RELOAD_BASELINE_OK"
AFTER_RELOAD_TOKEN = "SKILLPANEL_NATIVE_RELOAD_AFTER_OK"
PID_KEYS = ("controller", "opencode", "hermes")


def _skill_enabled(catalog, skill):
    row = next(
        (item for item in catalog.get("skills", []) if item.get("name") == skill), None
    )
    require(row is not None, f"Comparison fixture {skill!r} is missing")
    enabled = row.get("enabled")
    require(type(enabled) is bool, f"Comparison fixture {skill!r} has invalid state")
    return enabled


def _pid_snapshot(payload, phase):
    pids = payload.get("pids") or {}
    result = {}
    for name in PID_KEYS:
        pid = pids.get(name)
        require(isinstance(pid, int) and pid > 0, f"{phase}: no live {name} PID")
        result[name] = pid
    return result


def _assert_pids(payload, expected, phase):
    actual = _pid_snapshot(payload, phase)
    require(actual == expected, f"{phase}: expected PIDs {expected}, got {actual}")


def _controller_set(controller_url, skill, enabled, expected_pids, phase):
    """Use a freshly fetched revision for the PUT, retrying only conflicts."""

    for attempt in range(3):
        catalog = request(f"{controller_url}/skills")
        _assert_pids(catalog, expected_pids, f"{phase} catalog")
        _skill_enabled(catalog, skill)
        try:
            result = request(
                f"{controller_url}/skills/{urllib.parse.quote(skill, safe='')}",
                "PUT",
                {"enabled": enabled, "expected_revision": catalog["revision"]},
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 409 and attempt < 2:
                continue
            raise AssertionError(
                f"{phase}: controller PUT failed with HTTP {exc.code}"
            ) from exc
        require(result.get("ok") is True, f"{phase}: controller PUT was not successful")
        require(result.get("enabled") is enabled, f"{phase}: controller returned wrong state")
        _assert_pids(result, expected_pids, f"{phase} PUT")
        require(
            (skill in result.get("opencode_skills", [])) == enabled,
            f"{phase}: OpenCode catalog does not match enabled={enabled}",
        )
        return result
    raise AssertionError(f"{phase}: revision conflicted three consecutive times")


def _restore_original(controller_url, skill, original_enabled, original_pids):
    restored = _controller_set(
        controller_url,
        skill,
        original_enabled,
        original_pids,
        "cleanup restore",
    )
    final = request(f"{controller_url}/skills")
    _assert_pids(final, original_pids, "cleanup final catalog")
    final_enabled = _skill_enabled(final, skill)
    require(
        final_enabled is original_enabled,
        f"cleanup final state is {final_enabled}, expected {original_enabled}",
    )
    require(
        final.get("revision") == restored.get("revision"),
        "cleanup final revision differs from cleanup PUT revision",
    )
    return {
        "ok": True,
        "original_enabled": original_enabled,
        "final_enabled": final_enabled,
        "put_changed": restored.get("changed"),
        "put_revision": restored["revision"],
        "final_revision": final["revision"],
        "controller_state_matches": True,
        "opencode_catalog_matches": True,
        "pids_unchanged": True,
    }


def _read_pid(name):
    path = Path(f"/run/{name}.pid")
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError) as exc:
        raise AssertionError(f"Could not read a live {name} PID from {path}: {exc}") from exc
    return pid


def _process_state(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("State:"):
                return line.split()[1]
    except OSError:
        return ""
    return ""


def _wait_process_state(pid, stopped, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _process_state(pid)
        if stopped and state in {"T", "t"}:
            return
        if not stopped and state in {"R", "S", "D", "I"}:
            return
        time.sleep(0.02)
    expected = "stopped" if stopped else "running"
    raise AssertionError(
        f"Controller PID {pid} did not become {expected}; /proc state={_process_state(pid)!r}"
    )


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _controller_paused_with_skill_disabled(enabled_root, disabled_root, skill):
    """Pause controller, atomically move the skill away, then always undo both."""

    source = enabled_root / skill
    target = disabled_root / skill
    require(source.is_dir(), f"Raw-move source is missing: {source}")
    require(not target.exists(), f"Raw-move target already exists: {target}")
    require(source.stat().st_dev == disabled_root.stat().st_dev, "Skill roots are not one filesystem")

    controller_pid = _read_pid("controller")
    stopped = False
    moved = False
    try:
        os.kill(controller_pid, signal.SIGSTOP)
        stopped = True
        _wait_process_state(controller_pid, stopped=True)

        os.rename(source, target)
        moved = True
        _fsync_dir(source.parent)
        _fsync_dir(target.parent)
        yield controller_pid
    finally:
        restore_error = None
        continue_error = None
        if moved:
            try:
                if target.is_dir() and not source.exists():
                    os.rename(target, source)
                    _fsync_dir(source.parent)
                    _fsync_dir(target.parent)
                elif not source.is_dir():
                    raise RuntimeError(
                        f"cannot restore {skill}: source={source.exists()} target={target.exists()}"
                    )
            except Exception as exc:  # cleanup must still continue the controller
                restore_error = exc
        if stopped:
            try:
                os.kill(controller_pid, signal.SIGCONT)
                _wait_process_state(controller_pid, stopped=False)
            except Exception as exc:
                continue_error = exc
        if restore_error or continue_error:
            raise AssertionError(
                "Raw-move cleanup failed: "
                f"directory_restore={restore_error!r}, controller_SIGCONT={continue_error!r}"
            )


def _wait_controller(base_url, timeout=8.0):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            return request(f"{base_url}/skills")
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError(f"Controller did not recover after SIGCONT: {last_error}")


def _ensure_enabled(controller_url, skill, expected_pids):
    # Even when already enabled, the idempotent PUT runs OpenCode dispose+verify,
    # establishing the cached precondition used by comparison A.
    result = _controller_set(
        controller_url,
        skill,
        True,
        expected_pids,
        "comparison precondition",
    )
    return result


def _opencode_skill_names(base_url, directory):
    query = urllib.parse.urlencode({"directory": directory})
    payload = request(f"{base_url.rstrip('/')}/skill?{query}")
    require(isinstance(payload, list), f"Unexpected OpenCode /skill response: {payload!r}")
    return sorted(
        item["name"]
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )


def _state_revision(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"Could not read revision state {path}: {exc}") from exc
    revision = payload.get("revision")
    require(isinstance(revision, int), f"State file has no integer revision: {payload!r}")
    return revision


def _prompt_has_skill(prompt, skill):
    return bool(
        re.search(rf"(?<![A-Za-z0-9_-]){re.escape(skill)}(?![A-Za-z0-9_-])", prompt)
    )


def _listed_skills(info):
    result = set()
    skills = info.get("skills")
    if not isinstance(skills, dict):
        return result
    for entries in skills.values():
        if isinstance(entries, list):
            result.update(str(item) for item in entries)
    return result


def _dispatch_skill(client, live_id, skill):
    return client.rpc_raw(
        "command.dispatch",
        {"session_id": live_id, "name": skill, "arg": "comparison probe"},
    )


def _assert_same_session(client, live_id, stored_id, phase):
    active = client.rpc("session.activate", {"session_id": live_id})
    require(active.get("session_id") == live_id, f"{phase}: live session changed: {active!r}")
    require(
        active.get("session_key") == stored_id,
        f"{phase}: stored session changed from {stored_id!r}: {active!r}",
    )


def compare_opencode_raw_move(args, enabled_root, disabled_root, original_pids):
    normalized = _ensure_enabled(args.url, args.skill, original_pids)
    revision = normalized["revision"]
    opencode_pid = _read_pid("opencode")
    controller_pid = _read_pid("controller")
    before = _opencode_skill_names(args.opencode_url, args.opencode_directory)
    require(args.skill in before, f"OpenCode precondition missing {args.skill}: {before!r}")

    with _controller_paused_with_skill_disabled(
        enabled_root, disabled_root, args.skill
    ) as paused_pid:
        require(paused_pid == controller_pid, "Controller PID changed before raw move")
        require(not (enabled_root / args.skill).exists(), "Raw move did not hide enabled skill")
        require((disabled_root / args.skill).is_dir(), "Raw move did not reach disabled root")
        stale = _opencode_skill_names(args.opencode_url, args.opencode_directory)
        require(
            args.skill in stale,
            "Directory-only move unexpectedly refreshed OpenCode; expected its cached "
            f"catalog to retain {args.skill!r}, got {stale!r}",
        )
        require(_read_pid("opencode") == opencode_pid, "OpenCode PID changed during comparison A")

    recovered = _wait_controller(args.url)
    _assert_pids(recovered, original_pids, "OpenCode comparison recovery")
    require(recovered.get("revision") == revision, "Controller revision changed during raw move")
    row = next(
        (item for item in recovered["skills"] if item.get("name") == args.skill), None
    )
    require(row and row.get("enabled") is True, f"Skill directory was not restored: {row!r}")
    require(_read_pid("controller") == controller_pid, "Controller PID changed after SIGCONT")
    return {
        "result": "raw-move-does-not-refresh-opencode",
        "revision_unchanged": True,
        "cached_skill_still_visible": True,
        "opencode_pid_unchanged": True,
        "controller_pid_unchanged": True,
        "all_pids_unchanged": True,
    }


def compare_hermes_native_reload(
    args, enabled_root, disabled_root, state_file, original_pids
):
    normalized = _ensure_enabled(args.url, args.skill, original_pids)
    revision = normalized["revision"]
    require(_state_revision(state_file) == revision, "Controller/state revision mismatch")
    hermes_pid = _read_pid("hermes")
    cleanup_reload_error = None

    with HermesRpcClient(
        args.hermes_url,
        args.hermes_user,
        args.hermes_password,
        args.hermes_timeout,
    ) as client:
        created = client.rpc(
            "session.create",
            {"cols": 120, "source": "tool", "close_on_disconnect": True},
        )
        live_id = created.get("session_id")
        stored_id = created.get("stored_session_id")
        require(live_id and stored_id, f"Hermes session.create failed: {created!r}")

        # Seed the process-local slash mapping before the raw removal so
        # skills.reload can report this concrete skill in its removed diff.
        seeded = _dispatch_skill(client, live_id, args.skill)
        require(
            (seeded.get("result") or {}).get("type") == "skill",
            f"Hermes skill command precondition failed: {seeded!r}",
        )

        complete, baseline_info = client.run_turn(
            live_id,
            f"Do not call tools. Reply with exactly {BASELINE_TOKEN}.",
        )
        require(
            BASELINE_TOKEN in str(complete.get("text") or ""),
            f"Hermes baseline turn was unexpected: {complete!r}",
        )
        baseline_prompt = baseline_info.get("system_prompt") or ""
        marker = f"[SkillPanel-Revision:{revision}]"
        require(marker in baseline_prompt, f"Baseline prompt is missing {marker}")
        require(
            _prompt_has_skill(baseline_prompt, args.skill),
            f"Baseline system prompt did not list {args.skill!r}",
        )
        require(args.skill in _listed_skills(baseline_info), "Baseline skills list is missing skill")
        _assert_same_session(client, live_id, stored_id, "baseline")

        comparison_error_active = False
        try:
            with _controller_paused_with_skill_disabled(
                enabled_root, disabled_root, args.skill
            ):
                require(
                    _state_revision(state_file) == revision,
                    "Raw move unexpectedly changed the external revision",
                )

                resolved = client.rpc("command.resolve", {"name": "reload-skills"})
                require(
                    resolved.get("canonical") == "reload-skills",
                    f"Hermes did not resolve the native reload command: {resolved!r}",
                )
                # In 0.18.2 the in-process TUI endpoint for this native command
                # is skills.reload. slash.exec delegates to a separate worker.
                reloaded = client.rpc("skills.reload", {"session_id": live_id})
                reload_result = reloaded.get("result") or {}
                removed = {
                    item.get("name")
                    for item in reload_result.get("removed", [])
                    if isinstance(item, dict)
                }
                require(
                    args.skill in removed,
                    f"Native skills.reload did not report {args.skill!r} removed: {reloaded!r}",
                )

                rejected = _dispatch_skill(client, live_id, args.skill)
                require(
                    (rejected.get("error") or {}).get("code") == 4018,
                    f"Reloaded slash mapping still resolved removed skill: {rejected!r}",
                )

                complete, after_info = client.run_turn(
                    live_id,
                    f"Do not call tools. Reply with exactly {AFTER_RELOAD_TOKEN}.",
                )
                require(
                    AFTER_RELOAD_TOKEN in str(complete.get("text") or ""),
                    f"Hermes post-reload turn was unexpected: {complete!r}",
                )
                after_prompt = after_info.get("system_prompt") or ""
                require(marker in after_prompt, "Native reload changed/lost the revision marker")
                require(
                    after_prompt == baseline_prompt,
                    "Native reload unexpectedly replaced the cached system prompt",
                )
                require(
                    _prompt_has_skill(after_prompt, args.skill),
                    "Native skills.reload unexpectedly rebuilt the cached system prompt",
                )
                require(
                    args.skill not in _listed_skills(after_info),
                    "Post-turn live skills list still contains the raw-moved skill",
                )
                require(
                    _state_revision(state_file) == revision,
                    "External revision changed during native reload comparison",
                )
                require(_read_pid("hermes") == hermes_pid, "Hermes PID changed")
                _assert_same_session(client, live_id, stored_id, "after native reload")
        except BaseException:
            comparison_error_active = True
            raise
        finally:
            # The context above has restored the directory. Restore Hermes'
            # slash map too, while preserving any primary comparison failure.
            try:
                client.rpc("skills.reload", {"session_id": live_id})
                restored = _dispatch_skill(client, live_id, args.skill)
                require(
                    (restored.get("result") or {}).get("type") == "skill",
                    f"Hermes slash-map cleanup did not restore skill: {restored!r}",
                )
            except Exception as exc:
                cleanup_reload_error = str(exc)
                if not comparison_error_active:
                    raise
                print(
                    f"COMPARISON INTERNAL CLEANUP FAILURE: {cleanup_reload_error}",
                    file=sys.stderr,
                )

        closed = client.rpc("session.close", {"session_id": live_id})
        require(closed.get("closed") is True, f"Hermes session.close failed: {closed!r}")

    recovered = _wait_controller(args.url)
    _assert_pids(recovered, original_pids, "Hermes comparison recovery")
    require(recovered.get("revision") == revision, "Revision changed after comparison cleanup")
    require(_read_pid("hermes") == hermes_pid, "Hermes PID changed after cleanup")
    return {
        "result": "native-reload-refreshes-slash-map-not-cached-prompt",
        "native_command": "reload-skills",
        "native_rpc": "skills.reload",
        "revision_unchanged": True,
        "slash_mapping_removed_skill": True,
        "cached_system_prompt_retained_skill": True,
        "live_skills_list_removed_skill": True,
        "hermes_pid_unchanged": True,
        "all_pids_unchanged": True,
        "live_session_id": live_id,
        "stored_session_id": stored_id,
        "cleanup_reload_error": cleanup_reload_error,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--opencode-url", default="http://127.0.0.1:4096")
    parser.add_argument("--opencode-directory", default="/workspace")
    parser.add_argument("--skill", default=CANARY_SKILL)
    parser.add_argument(
        "--enabled-dir",
        default=os.getenv("SKILLPANEL_ENABLED_DIR", "/root/.config/opencode/skills"),
    )
    parser.add_argument(
        "--disabled-dir",
        default=os.getenv(
            "SKILLPANEL_DISABLED_DIR", "/root/.config/opencode/skills-disabled"
        ),
    )
    parser.add_argument(
        "--state-file",
        default=os.getenv("SKILLPANEL_STATE_FILE", "/data/skill-state.json"),
    )
    parser.add_argument("--hermes-url", default="http://127.0.0.1:9119")
    parser.add_argument(
        "--hermes-user",
        default=os.getenv("HERMES_DASHBOARD_BASIC_AUTH_USERNAME", "skillpanel"),
    )
    parser.add_argument(
        "--hermes-password",
        default=os.getenv("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "skillpanel-dev"),
    )
    parser.add_argument("--hermes-timeout", type=float, default=180.0)
    parser.add_argument(
        "--llm-native-reload",
        action="store_true",
        help="Run the real two-turn Hermes native-reload negative control",
    )
    args = parser.parse_args()

    require(os.geteuid() == 0, "comparisons.py must run as root inside the container")
    require(args.hermes_timeout > 0, "--hermes-timeout must be positive")
    require(
        re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.skill),
        f"Unsafe --skill value: {args.skill!r}",
    )
    enabled_root = Path(args.enabled_dir)
    disabled_root = Path(args.disabled_dir)
    state_file = Path(args.state_file)
    require(enabled_root.is_dir(), f"Enabled skill root is missing: {enabled_root}")
    require(disabled_root.is_dir(), f"Disabled skill root is missing: {disabled_root}")

    initial = request(f"{args.url}/skills")
    original_enabled = _skill_enabled(initial, args.skill)
    original_pids = _pid_snapshot(initial, "initial catalog")
    output = {
        "original_enabled": original_enabled,
        "initial_revision": initial["revision"],
        "initial_pids": original_pids,
        "opencode_directory_only": {"result": "not-started"},
        "hermes_native_reload": {"result": "skipped"},
    }
    primary_error = None
    cleanup_error = None
    try:
        output["opencode_directory_only"] = compare_opencode_raw_move(
            args, enabled_root, disabled_root, original_pids
        )
        if args.llm_native_reload:
            output["hermes_native_reload"] = compare_hermes_native_reload(
                args, enabled_root, disabled_root, state_file, original_pids
            )
    except Exception as exc:
        primary_error = exc
    finally:
        try:
            output["restore"] = _restore_original(
                args.url, args.skill, original_enabled, original_pids
            )
            output["final_revision"] = output["restore"]["final_revision"]
        except Exception as exc:
            cleanup_error = exc
            output["restore"] = {
                "ok": False,
                "original_enabled": original_enabled,
                "error": str(exc),
            }

    if primary_error is not None:
        output["test_failure"] = str(primary_error)
        print(f"COMPARISON FAILURE: {primary_error}", file=sys.stderr)
    if cleanup_error is not None:
        output["cleanup_failure"] = str(cleanup_error)
        print(f"COMPARISON CLEANUP FAILURE: {cleanup_error}", file=sys.stderr)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 1 if primary_error is not None or cleanup_error is not None else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"COMPARISON FAILURE: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
