import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from skillpanel_hot_reload import refresh_after_prompt_restore


class FakeSessionDB:
    def __init__(self):
        self.saved = []

    def update_system_prompt(self, session_id, prompt):
        self.saved.append((session_id, prompt))


class FakeAgent:
    def __init__(self):
        self._cached_system_prompt = "old prompt"
        self._session_db = FakeSessionDB()
        self.session_id = "same-session"
        self.builds = 0

    def _build_system_prompt(self, _system_message):
        self.builds += 1
        return f"fresh prompt {self.builds}"


class HotReloadModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.caches = {}
        self.reload_count = 0

        prompt_builder = types.ModuleType("agent.prompt_builder")
        prompt_builder.clear_skills_system_prompt_cache = lambda **_: self.caches.update(prompt=True)
        skill_commands = types.ModuleType("agent.skill_commands")

        def reload_skills():
            self.reload_count += 1
            return {}

        skill_commands.reload_skills = reload_skills
        self.modules = patch.dict(
            sys.modules,
            {
                "agent.prompt_builder": prompt_builder,
                "agent.skill_commands": skill_commands,
            },
        )
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        self.tmp.cleanup()

    def write_revision(self, revision):
        self.state.write_text(json.dumps({"revision": revision}), encoding="utf-8")

    def test_rebuilds_once_per_revision_and_persists_marker(self):
        self.write_revision(4)
        agent = FakeAgent()
        with patch.dict(os.environ, {"HERMES_SKILL_STATE_FILE": str(self.state)}):
            self.assertTrue(refresh_after_prompt_restore(agent))
            self.assertIn("[SkillPanel-Revision:4]", agent._cached_system_prompt)
            self.assertEqual(agent.builds, 1)
            self.assertEqual(self.reload_count, 1)
            self.assertEqual(len(agent._session_db.saved), 1)

            self.assertFalse(refresh_after_prompt_restore(agent))
            self.assertEqual(agent.builds, 1)

            self.write_revision(5)
            self.assertTrue(refresh_after_prompt_restore(agent))
            self.assertIn("[SkillPanel-Revision:5]", agent._cached_system_prompt)
            self.assertEqual(agent.builds, 2)

    def test_is_noop_without_environment_setting(self):
        agent = FakeAgent()
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(refresh_after_prompt_restore(agent))
        self.assertEqual(agent.builds, 0)


if __name__ == "__main__":
    unittest.main()
