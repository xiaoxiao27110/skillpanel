"""Minimal external control plane for atomic shared-skill toggles."""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ENABLED_DIR = Path(os.getenv("SKILLPANEL_ENABLED_DIR", "/root/.config/opencode/skills"))
DISABLED_DIR = Path(os.getenv("SKILLPANEL_DISABLED_DIR", "/root/.config/opencode/skills-disabled"))
STATE_FILE = Path(os.getenv("SKILLPANEL_STATE_FILE", "/data/skill-state.json"))
SCENES_FILE = Path(os.getenv("SKILLPANEL_SCENES_FILE", "/data/scenes.json"))
LOCK_FILE = STATE_FILE.with_suffix(".lock")
OPENCODE_URL = os.getenv("SKILLPANEL_OPENCODE_URL", "http://127.0.0.1:4096").rstrip("/")
OPENCODE_DIRECTORY = os.getenv("SKILLPANEL_OPENCODE_DIRECTORY", "/workspace")
OPENCODE_TUI_DIR = Path(
    os.getenv("SKILLPANEL_OPENCODE_TUI_DIR", "/run/skillpanel-opencode-tuis")
)
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_SCENE_NAME = "默认"
SCENE_NAME_MAX_LENGTH = 64
LOOPBACK_RUNTIME_RE = re.compile(r"^http://127\.0\.0\.1:([0-9]{1,5})$")
SKILL_BASE_RE = re.compile(r"^Base directory for this skill: (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class _OpenCodeRuntime:
    pid: int
    start_time: str
    directory: str
    url: str


class ToggleRequest(BaseModel):
    enabled: bool
    expected_revision: int


class SceneCreateRequest(BaseModel):
    name: str


class SceneActivateRequest(BaseModel):
    expected_revision: int


class SceneRenameRequest(BaseModel):
    new_name: str
    expected_revision: int


class SceneDeleteRequest(BaseModel):
    expected_revision: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_pid(name: str) -> int | None:
    try:
        pid = int(Path(f"/run/{name}.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return pid
    except PermissionError:
        # The controller intentionally runs unprivileged while the agents run
        # as root. EPERM still proves that the process exists.
        return pid
    except (ProcessLookupError, OSError, OverflowError):
        return None


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


@contextmanager
def _locked() -> Iterator[None]:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _skill_metadata(directory: Path) -> dict[str, str]:
    skill_md = directory / "SKILL.md"
    try:
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError("missing YAML frontmatter")
        _, frontmatter, _ = text.split("---", 2)
        data = yaml.safe_load(frontmatter) or {}
        if not isinstance(data, dict):
            raise ValueError("YAML frontmatter must be a mapping")
        name = str(data.get("name") or directory.name)
        description = str(data.get("description") or "")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Invalid skill at {skill_md}: {exc}") from exc
    if not SKILL_NAME_RE.fullmatch(name):
        raise RuntimeError(f"Invalid skill name {name!r} at {skill_md}")
    if name != directory.name:
        raise RuntimeError(f"Skill name {name!r} does not match directory {directory.name!r}")
    return {"name": name, "description": description}


def _quarantine_enabled_conflicts() -> list[dict[str, str]]:
    """Keep disabled skills disabled when an installer recreates them as enabled."""

    ENABLED_DIR.mkdir(parents=True, exist_ok=True)
    DISABLED_DIR.mkdir(parents=True, exist_ok=True)
    for disabled_path in sorted(DISABLED_DIR.iterdir()):
        if (
            disabled_path.name.startswith(".")
            or disabled_path.is_symlink()
            or not disabled_path.is_dir()
        ):
            continue
        try:
            _skill_metadata(disabled_path)
        except RuntimeError:
            continue
        enabled_path = ENABLED_DIR / disabled_path.name
        if enabled_path.is_symlink() or not enabled_path.is_dir():
            continue

        quarantine_root = DISABLED_DIR / ".skillpanel-conflicts" / disabled_path.name
        quarantine_root.mkdir(parents=True, exist_ok=True)
        quarantine_path = quarantine_root / f"{time.time_ns()}-{os.getpid()}"
        os.rename(enabled_path, quarantine_path)
        _fsync_dir(ENABLED_DIR)
        _fsync_dir(quarantine_root)
        _fsync_dir(DISABLED_DIR)
    reconciliations: list[dict[str, str]] = []
    conflict_root = DISABLED_DIR / ".skillpanel-conflicts"
    if not conflict_root.is_dir():
        return reconciliations
    for skill_root in sorted(conflict_root.iterdir()):
        if skill_root.is_symlink() or not skill_root.is_dir():
            continue
        for quarantine_path in sorted(skill_root.iterdir()):
            if quarantine_path.is_symlink() or not quarantine_path.is_dir():
                continue
            reconciliations.append(
                {
                    "name": skill_root.name,
                    "policy": "disabled-wins",
                    "action": "quarantined-enabled-copy",
                    "disabled_location": str(DISABLED_DIR / skill_root.name),
                    "quarantine_location": str(quarantine_path),
                }
            )
    return reconciliations


def _scan() -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    reconciliations = _quarantine_enabled_conflicts()
    result: dict[str, dict[str, Any]] = {}
    for root, enabled in ((ENABLED_DIR, True), (DISABLED_DIR, False)):
        root.mkdir(parents=True, exist_ok=True)
        for directory in sorted(root.iterdir()):
            if directory.name.startswith(".") or directory.is_symlink() or not directory.is_dir():
                continue
            try:
                metadata = _skill_metadata(directory)
            except RuntimeError:
                continue
            name = metadata["name"]
            if name in result:
                raise RuntimeError(f"Skill {name!r} exists in both enabled and disabled roots")
            result[name] = {
                **metadata,
                "enabled": enabled,
                "location": str(directory),
            }
    return result, reconciliations


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data.get("revision"), int) and isinstance(data.get("skills"), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"revision": -1, "skills": {}}


def _state_from_scan(
    revision: int,
    skills: dict[str, dict[str, Any]],
    reconciliations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "revision": revision,
        "updated_at": _utc_now(),
        "skills": {name: item["enabled"] for name, item in sorted(skills.items())},
        "reconciliations": reconciliations or [],
    }


def _reconcile_locked() -> tuple[dict[str, Any], dict[str, dict[str, Any]], bool]:
    skills, reconciliations = _scan()
    state = _load_state()
    observed = {name: item["enabled"] for name, item in sorted(skills.items())}
    changed = (
        state.get("skills") != observed
        or state.get("revision", -1) < 0
        or state.get("reconciliations", []) != reconciliations
    )
    if changed:
        try:
            _dispose_and_verify(
                {name: item["enabled"] for name, item in skills.items()}
            )
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            raise RuntimeError(f"OpenCode reconcile failed: {exc}") from exc

        revision = max(int(state.get("revision", -1)) + 1, 0)
        next_state = _state_from_scan(revision, skills, reconciliations)
        try:
            _atomic_json_write(STATE_FILE, next_state)
        except OSError as exc:
            raise RuntimeError(f"Skill state commit failed: {exc}") from exc
        state = next_state
    return state, skills, changed


def _http_json(url: str, method: str = "GET", timeout: float = 10.0) -> Any:
    request = urllib.request.Request(url, method=method, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        try:
            return json.loads(response.read().decode("utf-8"))
        except (http.client.HTTPException, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON response from {url}: {exc}") from exc


def _process_start_time(pid: int) -> str | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    closing_paren = value.rfind(")")
    fields = value[closing_paren + 1 :].split() if closing_paren >= 0 else []
    return fields[19] if len(fields) > 19 else None


def _runtime_is_current(runtime: _OpenCodeRuntime) -> bool:
    current = _process_start_time(runtime.pid)
    if current is not None:
        return current == runtime.start_time
    try:
        os.kill(runtime.pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except (PermissionError, OSError):
        # If /proc becomes unreadable, fail closed unless the PID is known gone.
        return True
    return True


def _read_registry_payload(path: Path) -> dict[str, Any] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        metadata = os.fstat(fd)
        if metadata.st_uid != 0 or not stat.S_ISREG(metadata.st_mode):
            return None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            total += len(chunk)
            if total > 65536:
                return None
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    finally:
        os.close(fd)
    return payload if isinstance(payload, dict) else None


def _registered_tui_runtimes() -> list[_OpenCodeRuntime]:
    try:
        registry_paths = sorted(OPENCODE_TUI_DIR.glob("*.json"))
    except OSError:
        return []

    runtimes: list[_OpenCodeRuntime] = []
    seen: set[tuple[int, str, str, str]] = set()
    for path in registry_paths:
        payload = _read_registry_payload(path)
        if payload is None:
            continue
        pid = payload.get("pid")
        start_time = payload.get("start_time")
        directory = payload.get("directory")
        url = payload.get("url")
        if (
            type(pid) is not int
            or pid <= 0
            or type(start_time) not in (str, int)
            or isinstance(start_time, bool)
            or not isinstance(directory, str)
            or not Path(directory).is_absolute()
            or not isinstance(url, str)
        ):
            continue
        match = LOOPBACK_RUNTIME_RE.fullmatch(url)
        if match is None or not 1 <= int(match.group(1)) <= 65535:
            continue
        runtime = _OpenCodeRuntime(pid, str(start_time), directory, url)
        if _process_start_time(pid) != runtime.start_time:
            continue
        identity = (runtime.pid, runtime.start_time, runtime.directory, runtime.url)
        if identity not in seen:
            seen.add(identity)
            runtimes.append(runtime)
    return runtimes


def _refresh_catalog(url: str, directory: str) -> Any:
    _http_json(f"{url}/global/dispose", method="POST")
    query = urllib.parse.urlencode({"directory": directory})
    return _http_json(f"{url}/skill?{query}")


def _refresh_tui_catalogs(runtime: _OpenCodeRuntime) -> tuple[Any, Any]:
    _http_json(f"{runtime.url}/global/dispose", method="POST")
    query = urllib.parse.urlencode({"directory": runtime.directory})
    skills = _http_json(f"{runtime.url}/skill?{query}")
    commands = _http_json(f"{runtime.url}/command?{query}")
    return skills, commands


def _managed_catalog_names(catalog: Any, expected: dict[str, bool]) -> list[str]:
    if not isinstance(catalog, list):
        raise RuntimeError("OpenCode skill catalog response must be an array")
    if any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        for item in catalog
    ):
        raise RuntimeError("OpenCode skill catalog contains an invalid entry")
    names = sorted(item["name"] for item in catalog)
    if len(names) != len(set(names)):
        raise RuntimeError("OpenCode skill catalog contains duplicate names")
    entries: list[tuple[str, str | None]] = []
    for item in catalog:
        if item["name"] not in expected:
            continue
        location = _catalog_location(item)
        if location == "<built-in>":
            entries.append((item["name"], None))
            continue
        normalized = _normalized_location(location) if location is not None else None
        if normalized is None:
            raise RuntimeError(
                f"OpenCode skill catalog has no absolute source for {item['name']!r}"
            )
        entries.append((item["name"], normalized))

    mismatches: list[str] = []
    for name, enabled in expected.items():
        global_location = os.path.realpath(ENABLED_DIR / name / "SKILL.md")
        global_loaded = (name, global_location) in entries
        name_loaded = any(entry_name == name for entry_name, _ in entries)
        if (enabled and not name_loaded) or (not enabled and global_loaded):
            mismatches.append(name)
    if mismatches:
        raise RuntimeError(
            "OpenCode catalog did not converge for global managed skills: "
            f"{sorted(mismatches)}; catalog={names}"
        )
    return names


def _catalog_location(item: dict[str, Any]) -> str | None:
    location = item.get("location")
    if not isinstance(location, str):
        location = item.get("source")
    return location if isinstance(location, str) else None


def _normalized_location(location: str) -> str | None:
    if not Path(location).is_absolute():
        return None
    return os.path.realpath(location)


def _verify_tui_catalog(catalog: Any, expected: dict[str, bool]) -> None:
    if not isinstance(catalog, list):
        raise RuntimeError("OpenCode TUI skill catalog response must be an array")
    entries: list[tuple[str, str | None]] = []
    for item in catalog:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RuntimeError("OpenCode TUI skill catalog contains an invalid entry")
        if item["name"] not in expected:
            continue
        location = _catalog_location(item)
        if location == "<built-in>":
            entries.append((item["name"], None))
            continue
        normalized = _normalized_location(location) if location is not None else None
        if normalized is None:
            raise RuntimeError(
                f"OpenCode TUI skill catalog has no absolute source for {item['name']!r}"
            )
        entries.append((item["name"], normalized))

    mismatches: list[str] = []
    for name, enabled in expected.items():
        global_location = os.path.realpath(ENABLED_DIR / name / "SKILL.md")
        global_loaded = (name, global_location) in entries
        name_loaded = any(entry_name == name for entry_name, _ in entries)
        if (enabled and not name_loaded) or (not enabled and global_loaded):
            mismatches.append(name)
    if mismatches:
        raise RuntimeError(
            "OpenCode TUI catalog did not converge for global managed skills: "
            f"{sorted(mismatches)}"
        )


def _verify_tui_command_catalog(catalog: Any, expected: dict[str, bool]) -> None:
    if not isinstance(catalog, list):
        raise RuntimeError("OpenCode TUI command catalog response must be an array")
    entries: list[tuple[str, str | None]] = []
    for item in catalog:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RuntimeError("OpenCode TUI command catalog contains an invalid entry")
        if item.get("source") != "skill" or item["name"] not in expected:
            continue
        template = item.get("template")
        if not isinstance(template, str):
            raise RuntimeError(
                f"OpenCode TUI skill command has an invalid template for {item['name']!r}"
            )
        matches = SKILL_BASE_RE.findall(template)
        if not matches:
            entries.append((item["name"], None))
            continue
        normalized = _normalized_location(matches[-1].strip())
        if normalized is None:
            raise RuntimeError(
                f"OpenCode TUI skill command has no absolute base for {item['name']!r}"
            )
        entries.append((item["name"], normalized))

    mismatches: list[str] = []
    for name, enabled in expected.items():
        global_base = os.path.realpath(ENABLED_DIR / name)
        global_loaded = (name, global_base) in entries
        name_loaded = any(entry_name == name for entry_name, _ in entries)
        if (enabled and not name_loaded) or (not enabled and global_loaded):
            mismatches.append(name)
    if mismatches:
        raise RuntimeError(
            "OpenCode TUI command catalog did not converge for global managed skills: "
            f"{sorted(mismatches)}"
        )


def _dispose_and_verify(expected: dict[str, bool]) -> list[str]:
    catalog = _refresh_catalog(OPENCODE_URL, OPENCODE_DIRECTORY)
    names = _managed_catalog_names(catalog, expected)
    for runtime in _registered_tui_runtimes():
        try:
            skills, commands = _refresh_tui_catalogs(runtime)
            _verify_tui_catalog(skills, expected)
            _verify_tui_command_catalog(commands, expected)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            if not _runtime_is_current(runtime):
                continue
            raise RuntimeError(
                f"OpenCode TUI runtime {runtime.pid} at {runtime.url} failed to refresh: {exc}"
            ) from exc
    return names


def _rollback_directory(source: Path, target: Path) -> list[str]:
    errors: list[str] = []
    try:
        os.rename(target, source)
        _fsync_dir(source.parent)
        _fsync_dir(target.parent)
    except OSError as exc:
        errors.append(f"directory rollback failed: {exc}")
    return errors


def _move(name: str, enabled: bool) -> tuple[Path, Path]:
    source = (DISABLED_DIR if enabled else ENABLED_DIR) / name
    target = (ENABLED_DIR if enabled else DISABLED_DIR) / name
    if not source.is_dir():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)
    os.rename(source, target)
    try:
        _fsync_dir(source.parent)
        _fsync_dir(target.parent)
    except OSError as exc:
        rollback_errors = _rollback_directory(source, target)
        if rollback_errors:
            raise RuntimeError(
                f"Directory move failed; rollback was {'; '.join(rollback_errors)}: {exc}"
            ) from exc
        raise
    return source, target


def _rollback_toggle(
    source: Path,
    target: Path,
    previous_enabled: bool,
    previous_state: dict[str, Any],
) -> list[str]:
    errors = _rollback_directory(source, target)

    try:
        expected = dict(previous_state["skills"])
        expected[source.name] = previous_enabled
        _dispose_and_verify(expected)
    except Exception as exc:
        errors.append(f"OpenCode rollback failed: {exc}")

    try:
        _atomic_json_write(STATE_FILE, previous_state)
    except OSError as exc:
        errors.append(f"state rollback failed: {exc}")
    return errors


def _default_scenes(skills: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "revision": 0,
        "updated_at": _utc_now(),
        "active": DEFAULT_SCENE_NAME,
        "scenes": {
            DEFAULT_SCENE_NAME: {
                "disabled": sorted(
                    name for name, item in skills.items() if not item["enabled"]
                )
            }
        },
    }


def _valid_scenes_payload(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    revision = data.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        return False
    active = data.get("active")
    scenes = data.get("scenes")
    if not isinstance(active, str) or not isinstance(scenes, dict) or active not in scenes:
        return False
    for scene_name, entry in scenes.items():
        if not isinstance(scene_name, str) or not isinstance(entry, dict):
            return False
        disabled = entry.get("disabled")
        if not isinstance(disabled, list) or any(
            not isinstance(item, str) for item in disabled
        ):
            return False
    return True


def _load_scenes_locked(skills: dict[str, dict[str, Any]]) -> dict[str, Any]:
    try:
        data = json.loads(SCENES_FILE.read_text(encoding="utf-8"))
        if _valid_scenes_payload(data):
            return data
    except (OSError, ValueError, TypeError):
        pass
    data = _default_scenes(skills)
    _atomic_json_write(SCENES_FILE, data)
    return data


def _load_scenes_or_507(skills: dict[str, dict[str, Any]]) -> dict[str, Any]:
    try:
        return _load_scenes_locked(skills)
    except OSError as exc:
        raise HTTPException(
            status_code=507, detail=f"Scene state commit failed: {exc}"
        ) from exc


def _commit_scenes_locked(scenes_state: dict[str, Any]) -> dict[str, Any]:
    next_scenes = {
        **scenes_state,
        "revision": scenes_state["revision"] + 1,
        "updated_at": _utc_now(),
    }
    _atomic_json_write(SCENES_FILE, next_scenes)
    return next_scenes


def _scenes_view(scenes_state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "disabled": list(entry["disabled"]),
            "active": name == scenes_state["active"],
        }
        for name, entry in sorted(scenes_state["scenes"].items())
    ]


def _scene_name_or_400(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > SCENE_NAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid scene name")
    return normalized


def _writeback_scene_locked(
    skills: dict[str, dict[str, Any]], name: str, enabled: bool
) -> dict[str, Any]:
    scenes_state = _load_scenes_locked(skills)
    active = scenes_state["active"]
    disabled = list(scenes_state["scenes"][active]["disabled"])
    if enabled and name in disabled:
        disabled.remove(name)
    elif not enabled and name not in disabled:
        disabled.append(name)
    else:
        return scenes_state
    return _commit_scenes_locked(
        {
            **scenes_state,
            "scenes": {
                **scenes_state["scenes"],
                active: {"disabled": sorted(disabled)},
            },
        }
    )


def _rollback_apply(
    moved: list[tuple[Path, Path]],
    previous_state: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for source, target in reversed(moved):
        errors.extend(_rollback_directory(source, target))

    try:
        _dispose_and_verify(dict(previous_state["skills"]))
    except Exception as exc:
        errors.append(f"OpenCode rollback failed: {exc}")

    try:
        _atomic_json_write(STATE_FILE, previous_state)
    except OSError as exc:
        errors.append(f"state rollback failed: {exc}")
    return errors


def _apply_scene_locked(
    state: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    disabled_names: list[str],
    pending_scenes: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], bool, dict[str, Any]]:
    disabled_set = set(disabled_names)
    targets = {name: name not in disabled_set for name in sorted(skills)}
    moved: list[tuple[Path, Path]] = []
    failing = ""
    try:
        for name, target_enabled in targets.items():
            if skills[name]["enabled"] == target_enabled:
                continue
            failing = name
            moved.append(_move(name, target_enabled))
    except (OSError, RuntimeError) as exc:
        errors: list[str] = []
        for source, target in reversed(moved):
            errors.extend(_rollback_directory(source, target))
        rollback = "complete" if not errors else "; ".join(errors)
        raise HTTPException(
            status_code=503,
            detail=f"Scene apply failed at skill {failing!r}; rollback was {rollback}: {exc}",
        ) from exc

    previous_state = state
    try:
        _dispose_and_verify(targets)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        if moved:
            rollback_errors = _rollback_apply(moved, previous_state)
            rollback = "complete" if not rollback_errors else "; ".join(rollback_errors)
            raise HTTPException(
                status_code=503,
                detail=f"OpenCode refresh failed; rollback was {rollback}: {exc}",
            ) from exc
        raise HTTPException(
            status_code=503, detail=f"OpenCode refresh failed: {exc}"
        ) from exc

    if moved:
        try:
            skills, reconciliations = _scan()
            next_state = _state_from_scan(state["revision"] + 1, skills, reconciliations)
            _atomic_json_write(STATE_FILE, next_state)
        except (OSError, RuntimeError) as exc:
            rollback_errors = _rollback_apply(moved, previous_state)
            rollback = "complete" if not rollback_errors else "; ".join(rollback_errors)
            raise HTTPException(
                status_code=507,
                detail=f"State commit failed; rollback was {rollback}: {exc}",
            ) from exc
        state = next_state

    try:
        scenes_state = _commit_scenes_locked(pending_scenes)
    except OSError as exc:
        if moved:
            rollback_errors = _rollback_apply(moved, previous_state)
            rollback = "complete" if not rollback_errors else "; ".join(rollback_errors)
            raise HTTPException(
                status_code=507,
                detail=f"Scene commit failed; rollback was {rollback}: {exc}",
            ) from exc
        raise HTTPException(
            status_code=507, detail=f"Scene commit failed: {exc}"
        ) from exc
    return state, skills, bool(moved), scenes_state


def _pids() -> dict[str, int | None]:
    return {name: _read_pid(name) for name in ("opencode", "hermes", "controller")}


def _startup_reconcile() -> None:
    try:
        with _locked():
            _, skills, _ = _reconcile_locked()
            _load_scenes_locked(skills)
    except (OSError, RuntimeError, urllib.error.URLError):
        # Supervisor may still be starting OpenCode. No revision was committed,
        # so the next API request retries the same reconcile.
        pass


def _reconcile_for_request() -> tuple[dict[str, Any], dict[str, dict[str, Any]], bool]:
    try:
        with _locked():
            return _reconcile_locked()
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        raise HTTPException(status_code=503, detail=f"Skill reconcile failed: {exc}") from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    _startup_reconcile()
    yield


app = FastAPI(title="SkillPanel hot-reload PoC", version="1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    state, skills, _ = _reconcile_for_request()
    pids = _pids()
    return {
        "ok": all(pids.values()),
        "revision": state["revision"],
        "skill_count": len(skills),
        "reconciliations": state.get("reconciliations", []),
        "pids": pids,
    }


@app.get("/skills")
def list_skills() -> dict[str, Any]:
    state, skills, _ = _reconcile_for_request()
    return {
        "revision": state["revision"],
        "skills": list(skills.values()),
        "reconciliations": state.get("reconciliations", []),
        "pids": _pids(),
    }


@app.put("/skills/{name}")
def toggle_skill(name: str, body: ToggleRequest) -> dict[str, Any]:
    if not SKILL_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid skill name")

    started = time.monotonic()
    with _locked():
        try:
            state, skills, _ = _reconcile_locked()
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            raise HTTPException(status_code=503, detail=f"Skill reconcile failed: {exc}") from exc
        if body.expected_revision != state["revision"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "Revision conflict", "current_revision": state["revision"]},
            )
        if name not in skills:
            raise HTTPException(status_code=404, detail=f"Unknown skill {name!r}")

        current = skills[name]["enabled"]
        if current == body.enabled:
            try:
                opencode_names = _dispose_and_verify(
                    {skill_name: item["enabled"] for skill_name, item in skills.items()}
                )
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                raise HTTPException(status_code=503, detail=f"OpenCode refresh failed: {exc}") from exc
            try:
                scenes_state = _writeback_scene_locked(skills, name, body.enabled)
            except OSError as exc:
                raise HTTPException(
                    status_code=507, detail=f"Scene state commit failed: {exc}"
                ) from exc
            return {
                "ok": True,
                "changed": False,
                "name": name,
                "enabled": body.enabled,
                "revision": state["revision"],
                "reconciliations": state.get("reconciliations", []),
                "opencode_skills": opencode_names,
                "active_scene": scenes_state["active"],
                "scenes_revision": scenes_state["revision"],
                "pids": _pids(),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
            }

        previous_state = state
        try:
            source, target = _move(name, body.enabled)
        except OSError as exc:
            raise HTTPException(
                status_code=507,
                detail=f"Directory move failed; rollback was complete: {exc}",
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        try:
            expected = {skill_name: item["enabled"] for skill_name, item in skills.items()}
            expected[name] = body.enabled
            opencode_names = _dispose_and_verify(expected)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            rollback_errors = _rollback_toggle(source, target, current, previous_state)
            rollback = "complete" if not rollback_errors else "; ".join(rollback_errors)
            raise HTTPException(
                status_code=503,
                detail=f"OpenCode refresh failed; rollback was {rollback}: {exc}",
            ) from exc

        try:
            skills, reconciliations = _scan()
            next_state = _state_from_scan(state["revision"] + 1, skills, reconciliations)
            _atomic_json_write(STATE_FILE, next_state)
            scenes_state = _writeback_scene_locked(skills, name, body.enabled)
        except (OSError, RuntimeError) as exc:
            rollback_errors = _rollback_toggle(source, target, current, previous_state)
            rollback = "complete" if not rollback_errors else "; ".join(rollback_errors)
            raise HTTPException(
                status_code=507,
                detail=f"State commit failed; rollback was {rollback}: {exc}",
            ) from exc
        state = next_state

        return {
            "ok": True,
            "changed": True,
            "name": name,
            "enabled": body.enabled,
            "revision": state["revision"],
            "reconciliations": state.get("reconciliations", []),
            "hermes_refresh": "next-turn",
            "opencode_skills": opencode_names,
            "active_scene": scenes_state["active"],
            "scenes_revision": scenes_state["revision"],
            "pids": _pids(),
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
        }


def _reconcile_for_scene_request() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        state, skills, _ = _reconcile_locked()
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        raise HTTPException(status_code=503, detail=f"Skill reconcile failed: {exc}") from exc
    return state, skills


def _scenes_revision_or_409(scenes_state: dict[str, Any], expected_revision: int) -> None:
    if expected_revision != scenes_state["revision"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Revision conflict",
                "current_revision": scenes_state["revision"],
            },
        )


@app.get("/scenes")
def list_scenes() -> dict[str, Any]:
    with _locked():
        _, skills = _reconcile_for_scene_request()
        scenes_state = _load_scenes_or_507(skills)
    return {
        "revision": scenes_state["revision"],
        "active": scenes_state["active"],
        "scenes": _scenes_view(scenes_state),
        "pids": _pids(),
    }


@app.post("/scenes")
def create_scene(body: SceneCreateRequest) -> dict[str, Any]:
    name = _scene_name_or_400(body.name)
    started = time.monotonic()
    with _locked():
        state, skills = _reconcile_for_scene_request()
        scenes_state = _load_scenes_or_507(skills)
        if name in scenes_state["scenes"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Scene {name!r} already exists",
                    "current_revision": scenes_state["revision"],
                },
            )
        pending = {
            **scenes_state,
            "active": name,
            "scenes": {**scenes_state["scenes"], name: {"disabled": []}},
        }
        state, _, _, scenes_state = _apply_scene_locked(state, skills, [], pending)
    return {
        "ok": True,
        "revision": scenes_state["revision"],
        "active": scenes_state["active"],
        "skill_revision": state["revision"],
        "scenes": _scenes_view(scenes_state),
        "pids": _pids(),
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
    }


@app.put("/scenes/{name}/activate")
def activate_scene(name: str, body: SceneActivateRequest) -> dict[str, Any]:
    name = name.strip()
    started = time.monotonic()
    with _locked():
        state, skills = _reconcile_for_scene_request()
        scenes_state = _load_scenes_or_507(skills)
        _scenes_revision_or_409(scenes_state, body.expected_revision)
        if name not in scenes_state["scenes"]:
            raise HTTPException(status_code=404, detail=f"Unknown scene {name!r}")
        pending = {**scenes_state, "active": name}
        state, _, changed, scenes_state = _apply_scene_locked(
            state, skills, scenes_state["scenes"][name]["disabled"], pending
        )
    return {
        "ok": True,
        "revision": scenes_state["revision"],
        "active": scenes_state["active"],
        "skill_revision": state["revision"],
        "changed": changed,
        "scenes": _scenes_view(scenes_state),
        "pids": _pids(),
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
    }


@app.put("/scenes/{name}")
def rename_scene(name: str, body: SceneRenameRequest) -> dict[str, Any]:
    name = name.strip()
    new_name = _scene_name_or_400(body.new_name)
    started = time.monotonic()
    with _locked():
        state, skills = _reconcile_for_scene_request()
        scenes_state = _load_scenes_or_507(skills)
        _scenes_revision_or_409(scenes_state, body.expected_revision)
        if name not in scenes_state["scenes"]:
            raise HTTPException(status_code=404, detail=f"Unknown scene {name!r}")
        if new_name != name and new_name in scenes_state["scenes"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Scene {new_name!r} already exists",
                    "current_revision": scenes_state["revision"],
                },
            )
        remaining = {
            scene_name: entry
            for scene_name, entry in scenes_state["scenes"].items()
            if scene_name != name
        }
        remaining[new_name] = scenes_state["scenes"][name]
        pending = {**scenes_state, "scenes": remaining}
        if scenes_state["active"] == name:
            pending["active"] = new_name
        try:
            scenes_state = _commit_scenes_locked(pending)
        except OSError as exc:
            raise HTTPException(
                status_code=507, detail=f"Scene state commit failed: {exc}"
            ) from exc
    return {
        "ok": True,
        "revision": scenes_state["revision"],
        "active": scenes_state["active"],
        "skill_revision": state["revision"],
        "scenes": _scenes_view(scenes_state),
        "pids": _pids(),
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
    }


@app.delete("/scenes/{name}")
def delete_scene(name: str, body: SceneDeleteRequest) -> dict[str, Any]:
    name = name.strip()
    with _locked():
        state, skills = _reconcile_for_scene_request()
        scenes_state = _load_scenes_or_507(skills)
        _scenes_revision_or_409(scenes_state, body.expected_revision)
        if name not in scenes_state["scenes"]:
            raise HTTPException(status_code=404, detail=f"Unknown scene {name!r}")
        remaining = {
            scene_name: entry
            for scene_name, entry in scenes_state["scenes"].items()
            if scene_name != name
        }
        if scenes_state["active"] != name:
            try:
                scenes_state = _commit_scenes_locked(
                    {**scenes_state, "scenes": remaining}
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=507, detail=f"Scene state commit failed: {exc}"
                ) from exc
        else:
            if remaining:
                successor = sorted(remaining)[0]
                pending = {
                    **scenes_state,
                    "active": successor,
                    "scenes": remaining,
                }
                disabled_names = remaining[successor]["disabled"]
            else:
                pending = {
                    **scenes_state,
                    "active": DEFAULT_SCENE_NAME,
                    "scenes": {DEFAULT_SCENE_NAME: {"disabled": []}},
                }
                disabled_names = []
            _, _, _, scenes_state = _apply_scene_locked(
                state, skills, disabled_names, pending
            )
    return {
        "ok": True,
        "revision": scenes_state["revision"],
        "active": scenes_state["active"],
        "scenes": _scenes_view(scenes_state),
        "pids": _pids(),
    }
