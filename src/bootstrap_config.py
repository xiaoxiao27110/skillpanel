"""Merge PoC-owned settings without replacing persisted user configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

import yaml


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _read_mapping(path: Path, loader: Callable[[str], Any]) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = loader(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Refusing to replace non-mapping config at {path}")
    return data


def main() -> None:
    model = os.getenv("SKILLPANEL_MODEL", "MiniMax-M2.7")
    opencode_provider = os.getenv("SKILLPANEL_OPENCODE_PROVIDER", "minimax-cn-coding-plan")
    hermes_provider = os.getenv("SKILLPANEL_HERMES_PROVIDER", "minimax-cn")
    enabled_dir = os.environ["SKILLPANEL_ENABLED_DIR"]

    home = Path.home()
    opencode_path = home / ".config" / "opencode" / "opencode.json"
    opencode = _read_mapping(opencode_path, json.loads)
    opencode.setdefault("$schema", "https://opencode.ai/config.json")
    opencode["model"] = f"{opencode_provider}/{model}"
    opencode["small_model"] = f"{opencode_provider}/{model}"
    _atomic_write(opencode_path, json.dumps(opencode, ensure_ascii=False, indent=2) + "\n")

    hermes_home = Path(os.getenv("HERMES_HOME", str(home / ".hermes")))
    hermes_path = hermes_home / "config.yaml"
    hermes = _read_mapping(hermes_path, lambda text: yaml.safe_load(text) or {})
    model_config = hermes.setdefault("model", {})
    skills_config = hermes.setdefault("skills", {})
    if not isinstance(model_config, dict) or not isinstance(skills_config, dict):
        raise RuntimeError("Hermes model and skills config sections must be mappings")
    model_config.update({"provider": hermes_provider, "default": model})
    external_dirs = list(skills_config.get("external_dirs") or [])
    if enabled_dir not in external_dirs:
        external_dirs.append(enabled_dir)
    skills_config["external_dirs"] = external_dirs
    skills_config.setdefault("disabled", [])
    _atomic_write(hermes_path, yaml.safe_dump(hermes, sort_keys=False))
    (hermes_home / ".no-bundled-skills").touch()


if __name__ == "__main__":
    main()
