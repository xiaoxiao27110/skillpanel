"""Hermes turn hook for revision-aware skill prompt refreshes.

The module is intentionally separate from Hermes. The image patches one call into
Hermes' existing per-turn prompt setup; when HERMES_SKILL_STATE_FILE is unset,
the call is a no-op.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MARKER = "SkillPanel-Revision"
_MARKER_RE = re.compile(r"^\[SkillPanel-Revision:([0-9]+)]$", re.MULTILINE)


def _read_revision(path: str) -> int | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        revision = payload.get("revision")
        if isinstance(revision, int) and revision >= 0:
            return revision
    except (OSError, ValueError, TypeError):
        logger.debug("SkillPanel revision is not readable yet", exc_info=True)
    return None


def _prompt_revision(prompt: str | None) -> int | None:
    if not prompt:
        return None
    match = _MARKER_RE.search(prompt)
    return int(match.group(1)) if match else None


def _invalidate_skill_caches() -> None:
    from agent.prompt_builder import clear_skills_system_prompt_cache
    from agent.skill_commands import reload_skills

    clear_skills_system_prompt_cache(clear_snapshot=False)
    reload_skills()


def refresh_after_prompt_restore(agent: Any, system_message: str | None = None) -> bool:
    """Refresh Hermes' cached prompt when the external skill revision changes.

    This runs after Hermes restores or builds its normal session prompt. A marker
    embedded in the persisted prompt lets a newly-created gateway agent compare
    its restored prompt with the current external revision without rebuilding on
    every turn.
    """

    state_file = os.getenv("HERMES_SKILL_STATE_FILE", "").strip()
    if not state_file:
        return False

    revision = _read_revision(state_file)
    if revision is None:
        return False

    if _prompt_revision(getattr(agent, "_cached_system_prompt", None)) == revision:
        return False

    _invalidate_skill_caches()
    prompt = agent._build_system_prompt(system_message)
    agent._cached_system_prompt = f"{prompt.rstrip()}\n\n[{_MARKER}:{revision}]"

    session_db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", None)
    if session_db is not None and session_id:
        try:
            session_db.update_system_prompt(session_id, agent._cached_system_prompt)
        except Exception:
            logger.warning(
                "Failed to persist refreshed SkillPanel system prompt for session %s",
                session_id,
                exc_info=True,
            )

    logger.info("Refreshed Hermes skill state to revision %s", revision)
    return True


__all__ = ["refresh_after_prompt_restore"]
