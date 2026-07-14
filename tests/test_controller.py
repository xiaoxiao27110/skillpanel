import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException


SKILL = """---
name: canary-alpha
description: test canary
---
Return the canary.
"""

BETA_SKILL = """---
name: canary-beta
description: second test canary
---
Return the beta canary.
"""

REINSTALLED_SKILL = """---
name: canary-alpha
description: reinstalled test canary
---
Return the replacement canary.
"""


class ControllerTests(unittest.TestCase):
    def test_read_pid_treats_permission_error_as_alive(self):
        with patch.object(self.controller.Path, "read_text", return_value="42"), patch.object(
            self.controller.os, "kill", side_effect=PermissionError
        ):
            self.assertEqual(self.controller._read_pid("opencode"), 42)

    def test_read_pid_returns_none_when_pid_file_is_unreadable(self):
        with patch.object(self.controller.Path, "read_text", side_effect=PermissionError):
            self.assertIsNone(self.controller._read_pid("opencode"))

    def test_read_pid_rejects_invalid_numeric_values(self):
        for value in ("0", "-1", str(1 << 100)):
            with self.subTest(value=value), patch.object(
                self.controller.Path, "read_text", return_value=value
            ):
                self.assertIsNone(self.controller._read_pid("opencode"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.enabled = root / "skills"
        self.disabled = root / "skills-disabled"
        self.tui_registry = root / "opencode-tuis"
        skill = self.enabled / "canary-alpha"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(SKILL, encoding="utf-8")
        env = {
            "SKILLPANEL_ENABLED_DIR": str(self.enabled),
            "SKILLPANEL_DISABLED_DIR": str(self.disabled),
            "SKILLPANEL_STATE_FILE": str(root / "state.json"),
            "SKILLPANEL_OPENCODE_TUI_DIR": str(self.tui_registry),
        }
        self.env = patch.dict(os.environ, env)
        self.env.start()
        sys.modules.pop("controller", None)
        self.controller = importlib.import_module("controller")

        def fake_dispose(expected):
            names = sorted(p.name for p in self.enabled.iterdir() if p.is_dir())
            mismatches = [
                name for name, enabled in expected.items() if ((name in names) != enabled)
            ]
            if mismatches:
                raise RuntimeError(f"did not converge: {mismatches}")
            return names

        self.fake_dispose = fake_dispose
        self.dispose_patcher = patch.object(self.controller, "_dispose_and_verify", side_effect=fake_dispose)
        self.dispose = self.dispose_patcher.start()

    def _write_runtime(self, name, payload):
        self.tui_registry.mkdir(parents=True, exist_ok=True)
        path = self.tui_registry / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _root_owned_registry_files(self):
        real_fstat = os.fstat

        def root_fstat(fd):
            metadata = real_fstat(fd)
            return SimpleNamespace(st_uid=0, st_mode=metadata.st_mode)

        return patch.object(self.controller.os, "fstat", side_effect=root_fstat)

    def _skill_command(self, base=None):
        skill_base = base or str(self.enabled / "canary-alpha")
        return {
            "name": "canary-alpha",
            "source": "skill",
            "template": (
                "test skill body\n\n"
                f"Base directory for this skill: {skill_base}\n"
                "Relative paths in this skill are relative to this base directory."
            ),
        }

    def tearDown(self):
        self.dispose_patcher.stop()
        self.env.stop()
        sys.modules.pop("controller", None)
        self.tmp.cleanup()

    def test_toggle_off_and_on_is_atomic_and_revisioned(self):
        initial = self.controller.list_skills()
        self.assertEqual(initial["revision"], 0)
        request = self.controller.ToggleRequest(enabled=False, expected_revision=0)
        disabled = self.controller.toggle_skill("canary-alpha", request)
        self.assertTrue(disabled["changed"])
        self.assertEqual(disabled["revision"], 1)
        self.assertTrue((self.disabled / "canary-alpha" / "SKILL.md").is_file())

        enabled = self.controller.toggle_skill(
            "canary-alpha",
            self.controller.ToggleRequest(enabled=True, expected_revision=1),
        )
        self.assertEqual(enabled["revision"], 2)
        self.assertTrue((self.enabled / "canary-alpha" / "SKILL.md").is_file())

    def test_registry_returns_multiple_live_runtimes_and_ignores_stale_entries(self):
        self._write_runtime(
            "first",
            {
                "pid": 101,
                "start_time": "1001",
                "directory": "/workspace/first",
                "url": "http://127.0.0.1:4101",
            },
        )
        self._write_runtime(
            "second",
            {
                "pid": 102,
                "start_time": 1002,
                "directory": "/workspace/second",
                "url": "http://127.0.0.1:4102",
            },
        )
        self._write_runtime(
            "stale",
            {
                "pid": 103,
                "start_time": "old-start-time",
                "directory": "/workspace/stale",
                "url": "http://127.0.0.1:4103",
            },
        )
        self._write_runtime(
            "dead",
            {
                "pid": 104,
                "start_time": "1004",
                "directory": "/workspace/dead",
                "url": "http://127.0.0.1:4104",
            },
        )

        start_times = {101: "1001", 102: "1002", 103: "new-start-time", 104: None}
        with self._root_owned_registry_files(), patch.object(
            self.controller,
            "_process_start_time",
            side_effect=lambda pid: start_times.get(pid),
        ):
            runtimes = self.controller._registered_tui_runtimes()

        self.assertEqual(
            [(item.pid, item.directory, item.url) for item in runtimes],
            [
                (101, "/workspace/first", "http://127.0.0.1:4101"),
                (102, "/workspace/second", "http://127.0.0.1:4102"),
            ],
        )

    def test_registry_ignores_malformed_non_loopback_and_non_root_files(self):
        self.tui_registry.mkdir(parents=True)
        malformed = self.tui_registry / "malformed.json"
        malformed.write_text("not-json", encoding="utf-8")
        symlink_target = Path(self.tmp.name) / "runtime-target"
        symlink_target.write_text(
            json.dumps(
                {
                    "pid": 200,
                    "start_time": "2000",
                    "directory": "/workspace",
                    "url": "http://127.0.0.1:4100",
                }
            ),
            encoding="utf-8",
        )
        (self.tui_registry / "symlink.json").symlink_to(symlink_target)
        (self.tui_registry / "directory.json").mkdir()
        self._write_runtime(
            "remote",
            {
                "pid": 201,
                "start_time": "2001",
                "directory": "/workspace",
                "url": "http://0.0.0.0:4101",
            },
        )
        self._write_runtime(
            "relative",
            {
                "pid": 202,
                "start_time": "2002",
                "directory": "workspace",
                "url": "http://127.0.0.1:4102",
            },
        )
        non_root = self._write_runtime(
            "non-root",
            {
                "pid": 203,
                "start_time": "2003",
                "directory": "/workspace",
                "url": "http://127.0.0.1:4103",
            },
        )

        with patch.object(
            self.controller.os,
            "fstat",
            return_value=SimpleNamespace(st_uid=1000, st_mode=0o100644),
        ):
            self.assertIsNone(self.controller._read_registry_payload(non_root))

        with self._root_owned_registry_files(), patch.object(
            self.controller, "_process_start_time", return_value="unused"
        ):
            self.assertEqual(self.controller._registered_tui_runtimes(), [])

    def test_dispose_refreshes_managed_server_and_multiple_tui_runtimes(self):
        runtimes = [
            self.controller._OpenCodeRuntime(301, "3001", "/workspace/a", "http://127.0.0.1:4301"),
            self.controller._OpenCodeRuntime(302, "3002", "/workspace/b", "http://127.0.0.1:4302"),
        ]
        global_skill = {
            "name": "canary-alpha",
            "location": str(self.enabled / "canary-alpha" / "SKILL.md"),
        }
        calls = []

        def fake_http(url, method="GET", timeout=10.0):
            calls.append((url, method))
            if "/skill?" in url:
                return [global_skill]
            if "/command?" in url:
                return [self._skill_command()]
            return {}

        self.dispose_patcher.stop()
        try:
            with patch.object(
                self.controller, "_registered_tui_runtimes", return_value=runtimes
            ), patch.object(self.controller, "_http_json", side_effect=fake_http):
                names = self.controller._dispose_and_verify({"canary-alpha": True})
        finally:
            self.dispose = self.dispose_patcher.start()

        self.assertEqual(names, ["canary-alpha"])
        self.assertEqual(
            [(url, method) for url, method in calls if url.endswith("/global/dispose")],
            [
                (f"{self.controller.OPENCODE_URL}/global/dispose", "POST"),
                ("http://127.0.0.1:4301/global/dispose", "POST"),
                ("http://127.0.0.1:4302/global/dispose", "POST"),
            ],
        )
        self.assertEqual(sum("/command?" in url for url, _ in calls), 2)

    def test_tui_catalog_uses_global_location_for_project_duplicate(self):
        project_duplicate = [
            {
                "name": "canary-alpha",
                "location": "/workspace/.opencode/skills/canary-alpha/SKILL.md",
            }
        ]

        self.controller._verify_tui_catalog(project_duplicate, {"canary-alpha": False})
        self.controller._verify_tui_catalog(project_duplicate, {"canary-alpha": True})
        with self.assertRaisesRegex(RuntimeError, "canary-alpha"):
            self.controller._verify_tui_catalog([], {"canary-alpha": True})

        global_entry = [
            {
                "name": "canary-alpha",
                "location": str(self.enabled / "canary-alpha" / "SKILL.md"),
            }
        ]
        self.controller._verify_tui_catalog(global_entry, {"canary-alpha": True})
        with self.assertRaisesRegex(RuntimeError, "canary-alpha"):
            self.controller._verify_tui_catalog(global_entry, {"canary-alpha": False})

    def test_tui_command_rejects_stale_global_and_allows_project_duplicate(self):
        global_command = [self._skill_command()]
        project_command = [
            self._skill_command("/workspace/.opencode/skills/canary-alpha")
        ]

        with self.assertRaisesRegex(RuntimeError, "canary-alpha"):
            self.controller._verify_tui_command_catalog(
                global_command, {"canary-alpha": False}
            )
        self.controller._verify_tui_command_catalog(
            project_command, {"canary-alpha": False}
        )
        self.controller._verify_tui_command_catalog(
            project_command, {"canary-alpha": True}
        )
        with self.assertRaisesRegex(RuntimeError, "canary-alpha"):
            self.controller._verify_tui_command_catalog([], {"canary-alpha": True})
        self.controller._verify_tui_command_catalog(
            global_command, {"canary-alpha": True}
        )

    def test_dispose_accepts_project_shadow_for_enabled_and_disabled_states(self):
        runtime = self.controller._OpenCodeRuntime(
            351, "3501", "/workspace/project", "http://127.0.0.1:4351"
        )
        project_skill = {
            "name": "canary-alpha",
            "location": "/workspace/project/.opencode/skills/canary-alpha/SKILL.md",
        }
        project_command = self._skill_command(
            "/workspace/project/.opencode/skills/canary-alpha"
        )

        self.dispose_patcher.stop()
        try:
            for enabled in (True, False):
                with self.subTest(enabled=enabled):
                    def fake_http(url, method="GET", timeout=10.0):
                        if "/skill?" in url:
                            return [project_skill]
                        if "/command?" in url:
                            return [project_command]
                        return {}

                    with patch.object(
                        self.controller,
                        "_registered_tui_runtimes",
                        return_value=[runtime],
                    ), patch.object(self.controller, "_http_json", side_effect=fake_http):
                        names = self.controller._dispose_and_verify(
                            {"canary-alpha": enabled}
                        )
                    self.assertEqual(names, ["canary-alpha"])
        finally:
            self.dispose = self.dispose_patcher.start()

    def test_managed_catalog_rejects_stale_global_when_disabled(self):
        global_entry = [
            {
                "name": "canary-alpha",
                "location": str(self.enabled / "canary-alpha" / "SKILL.md"),
            }
        ]
        project_entry = [
            {
                "name": "canary-alpha",
                "location": "/workspace/.opencode/skills/canary-alpha/SKILL.md",
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "canary-alpha"):
            self.controller._managed_catalog_names(
                global_entry, {"canary-alpha": False}
            )
        self.assertEqual(
            self.controller._managed_catalog_names(
                project_entry, {"canary-alpha": False}
            ),
            ["canary-alpha"],
        )

    def test_builtin_shadow_is_allowed_when_managed_duplicate_is_enabled_or_disabled(self):
        skill_catalog = [
            {"name": "customize-opencode", "location": "<built-in>"}
        ]
        command_catalog = [
            {
                "name": "customize-opencode",
                "source": "skill",
                "template": "built-in skill instructions without a base marker",
            }
        ]

        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                expected = {"customize-opencode": enabled}
                self.assertEqual(
                    self.controller._managed_catalog_names(skill_catalog, expected),
                    ["customize-opencode"],
                )
                self.controller._verify_tui_catalog(skill_catalog, expected)
                self.controller._verify_tui_command_catalog(command_catalog, expected)

    def test_relative_skill_sources_and_base_markers_are_rejected(self):
        relative_skill = [
            {"name": "canary-alpha", "location": "relative/canary-alpha/SKILL.md"}
        ]
        relative_command = [
            {
                "name": "canary-alpha",
                "source": "skill",
                "template": (
                    "skill body\n\n"
                    "Base directory for this skill: relative/canary-alpha"
                ),
            }
        ]

        for verifier in (
            self.controller._managed_catalog_names,
            self.controller._verify_tui_catalog,
        ):
            with self.subTest(verifier=verifier.__name__), self.assertRaisesRegex(
                RuntimeError, "absolute source"
            ):
                verifier(relative_skill, {"canary-alpha": False})
        with self.assertRaisesRegex(RuntimeError, "absolute base"):
            self.controller._verify_tui_command_catalog(
                relative_command, {"canary-alpha": False}
            )

    def test_live_tui_failure_rolls_back_directory_catalog_and_revision(self):
        self.controller.list_skills()
        runtime = self.controller._OpenCodeRuntime(
            401, "4001", "/workspace", "http://127.0.0.1:4401"
        )
        calls = []
        fail_once = True

        def fake_http(url, method="GET", timeout=10.0):
            nonlocal fail_once
            calls.append((url, method))
            if url == f"{runtime.url}/global/dispose" and fail_once:
                fail_once = False
                raise self.controller.urllib.error.URLError("offline")
            if "/skill?" in url:
                if (self.enabled / "canary-alpha").is_dir():
                    return [
                        {
                            "name": "canary-alpha",
                            "location": str(self.enabled / "canary-alpha" / "SKILL.md"),
                        }
                    ]
                return []
            if "/command?" in url:
                return [self._skill_command()] if (self.enabled / "canary-alpha").is_dir() else []
            return {}

        self.dispose_patcher.stop()
        try:
            with patch.object(
                self.controller, "_registered_tui_runtimes", return_value=[runtime]
            ), patch.object(
                self.controller, "_runtime_is_current", return_value=True
            ), patch.object(self.controller, "_http_json", side_effect=fake_http):
                with self.assertRaises(HTTPException) as raised:
                    self.controller.toggle_skill(
                        "canary-alpha",
                        self.controller.ToggleRequest(enabled=False, expected_revision=0),
                    )
        finally:
            self.dispose = self.dispose_patcher.start()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("rollback was complete", str(raised.exception.detail))
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertFalse((self.disabled / "canary-alpha").exists())
        self.assertEqual(self.controller._load_state()["revision"], 0)
        self.assertEqual(
            sum(url == f"{runtime.url}/global/dispose" for url, _ in calls),
            2,
        )

    def test_idempotent_toggle_refreshes_registered_tui_without_revision_change(self):
        self.controller.list_skills()
        runtime = self.controller._OpenCodeRuntime(
            501, "5001", "/workspace", "http://127.0.0.1:4501"
        )
        global_skill = {
            "name": "canary-alpha",
            "location": str(self.enabled / "canary-alpha" / "SKILL.md"),
        }
        calls = []

        def fake_http(url, method="GET", timeout=10.0):
            calls.append((url, method))
            if "/skill?" in url:
                return [global_skill]
            if "/command?" in url:
                return [self._skill_command()]
            return {}

        self.dispose_patcher.stop()
        try:
            with patch.object(
                self.controller, "_registered_tui_runtimes", return_value=[runtime]
            ), patch.object(self.controller, "_http_json", side_effect=fake_http):
                result = self.controller.toggle_skill(
                    "canary-alpha",
                    self.controller.ToggleRequest(enabled=True, expected_revision=0),
                )
        finally:
            self.dispose = self.dispose_patcher.start()

        self.assertFalse(result["changed"])
        self.assertEqual(result["revision"], 0)
        self.assertEqual(
            [url for url, _ in calls if url.endswith("/global/dispose")],
            [
                f"{self.controller.OPENCODE_URL}/global/dispose",
                f"{runtime.url}/global/dispose",
            ],
        )

    def test_tui_that_exits_during_refresh_is_ignored(self):
        runtime = self.controller._OpenCodeRuntime(
            601, "6001", "/workspace", "http://127.0.0.1:4601"
        )
        global_skill = {
            "name": "canary-alpha",
            "location": str(self.enabled / "canary-alpha" / "SKILL.md"),
        }

        def fake_http(url, method="GET", timeout=10.0):
            if url == f"{runtime.url}/global/dispose":
                raise self.controller.urllib.error.URLError("exited")
            return [global_skill] if "/skill?" in url else {}

        self.dispose_patcher.stop()
        try:
            with patch.object(
                self.controller, "_registered_tui_runtimes", return_value=[runtime]
            ), patch.object(
                self.controller, "_runtime_is_current", return_value=False
            ), patch.object(self.controller, "_http_json", side_effect=fake_http):
                names = self.controller._dispose_and_verify({"canary-alpha": True})
        finally:
            self.dispose = self.dispose_patcher.start()

        self.assertEqual(names, ["canary-alpha"])

    def test_revision_conflict_does_not_move_skill(self):
        self.controller.list_skills()
        with self.assertRaises(HTTPException) as raised:
            self.controller.toggle_skill(
                "canary-alpha",
                self.controller.ToggleRequest(enabled=False, expected_revision=99),
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertTrue((self.enabled / "canary-alpha").is_dir())

    def test_opencode_failure_rolls_back(self):
        self.controller.list_skills()
        self.dispose.side_effect = RuntimeError("offline")
        with self.assertRaises(HTTPException) as raised:
            self.controller.toggle_skill(
                "canary-alpha",
                self.controller.ToggleRequest(enabled=False, expected_revision=0),
            )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertFalse((self.disabled / "canary-alpha").exists())
        self.assertEqual(self.controller._load_state()["revision"], 0)

    def test_invalid_opencode_json_rolls_back(self):
        self.controller.list_skills()

        def response(body):
            result = MagicMock()
            result.__enter__.return_value = result
            result.read.return_value = body
            return result

        responses = [
            response(b"{}"),
            response(b"not-json"),
            response(b"{}"),
            response(b'[{"name":"canary-alpha"}]'),
        ]
        self.dispose_patcher.stop()
        try:
            with patch.object(self.controller.urllib.request, "urlopen", side_effect=responses):
                with self.assertRaises(HTTPException) as raised:
                    self.controller.toggle_skill(
                        "canary-alpha",
                        self.controller.ToggleRequest(enabled=False, expected_revision=0),
                    )
        finally:
            self.dispose = self.dispose_patcher.start()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("Invalid JSON response", str(raised.exception.detail))
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertFalse((self.disabled / "canary-alpha").exists())
        self.assertEqual(self.controller._load_state()["revision"], 0)

    def test_non_array_opencode_catalog_rolls_back(self):
        self.controller.list_skills()

        def response(body):
            result = MagicMock()
            result.__enter__.return_value = result
            result.read.return_value = body
            return result

        responses = [
            response(b"{}"),
            response(b"{}"),
            response(b"{}"),
            response(b'[{"name":"canary-alpha"}]'),
        ]
        self.dispose_patcher.stop()
        try:
            with patch.object(self.controller.urllib.request, "urlopen", side_effect=responses):
                with self.assertRaises(HTTPException) as raised:
                    self.controller.toggle_skill(
                        "canary-alpha",
                        self.controller.ToggleRequest(enabled=False, expected_revision=0),
                    )
        finally:
            self.dispose = self.dispose_patcher.start()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("must be an array", str(raised.exception.detail))
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertFalse((self.disabled / "canary-alpha").exists())
        self.assertEqual(self.controller._load_state()["revision"], 0)

    def test_partial_opencode_catalog_rolls_back(self):
        beta = self.enabled / "canary-beta"
        beta.mkdir()
        (beta / "SKILL.md").write_text(BETA_SKILL, encoding="utf-8")
        self.controller.list_skills()

        def response(body):
            result = MagicMock()
            result.__enter__.return_value = result
            result.read.return_value = body
            return result

        responses = [
            response(b"{}"),
            response(b"[]"),
            response(b"{}"),
            response(b'[{"name":"canary-alpha"},{"name":"canary-beta"}]'),
        ]
        self.dispose_patcher.stop()
        try:
            with patch.object(self.controller.urllib.request, "urlopen", side_effect=responses):
                with self.assertRaises(HTTPException) as raised:
                    self.controller.toggle_skill(
                        "canary-alpha",
                        self.controller.ToggleRequest(enabled=False, expected_revision=0),
                    )
        finally:
            self.dispose = self.dispose_patcher.start()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("canary-beta", str(raised.exception.detail))
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertTrue(beta.is_dir())
        self.assertFalse((self.disabled / "canary-alpha").exists())
        self.assertEqual(
            self.controller._load_state()["skills"],
            {"canary-alpha": True, "canary-beta": True},
        )

    def test_one_hundred_toggle_cycles(self):
        revision = self.controller.list_skills()["revision"]
        enabled = True
        for _ in range(100):
            enabled = not enabled
            result = self.controller.toggle_skill(
                "canary-alpha",
                self.controller.ToggleRequest(enabled=enabled, expected_revision=revision),
            )
            revision = result["revision"]
        self.assertEqual(revision, 100)
        self.assertTrue((self.enabled / "canary-alpha").is_dir())

    def test_state_commit_failure_rolls_back(self):
        self.controller.list_skills()
        with patch.object(self.controller, "_atomic_json_write", side_effect=OSError("disk full")):
            with self.assertRaises(HTTPException) as raised:
                self.controller.toggle_skill(
                    "canary-alpha",
                    self.controller.ToggleRequest(enabled=False, expected_revision=0),
                )
        self.assertEqual(raised.exception.status_code, 507)
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertFalse((self.disabled / "canary-alpha").exists())
        self.assertEqual(self.controller._load_state()["revision"], 0)

    def test_directory_fsync_failure_rolls_back(self):
        self.controller.list_skills()
        real_fsync_dir = self.controller._fsync_dir
        calls = 0

        def fail_once(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated fsync failure")
            real_fsync_dir(path)

        with patch.object(self.controller, "_fsync_dir", side_effect=fail_once):
            with self.assertRaises(HTTPException) as raised:
                self.controller.toggle_skill(
                    "canary-alpha",
                    self.controller.ToggleRequest(enabled=False, expected_revision=0),
                )
        self.assertEqual(raised.exception.status_code, 507)
        self.assertIn("rollback was complete", str(raised.exception.detail))
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertFalse((self.disabled / "canary-alpha").exists())
        self.assertEqual(self.controller._load_state()["revision"], 0)

    def test_external_drift_refresh_failure_does_not_commit_revision(self):
        initial = self.controller.list_skills()
        beta = self.enabled / "canary-beta"
        beta.mkdir()
        (beta / "SKILL.md").write_text(BETA_SKILL, encoding="utf-8")

        self.dispose.side_effect = RuntimeError("offline")
        with self.assertRaises(HTTPException) as raised:
            self.controller.list_skills()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.controller._load_state()["revision"], initial["revision"])
        self.assertEqual(self.controller._load_state()["skills"], {"canary-alpha": True})

        self.dispose.side_effect = self.fake_dispose
        reconciled = self.controller.list_skills()
        self.assertEqual(reconciled["revision"], initial["revision"] + 1)
        self.assertEqual(
            {item["name"] for item in reconciled["skills"]},
            {"canary-alpha", "canary-beta"},
        )

    def test_disabled_wins_when_installer_recreates_enabled_copy(self):
        self.controller.list_skills()
        disabled = self.controller.toggle_skill(
            "canary-alpha",
            self.controller.ToggleRequest(enabled=False, expected_revision=0),
        )
        self.assertEqual(disabled["revision"], 1)

        reinstalled = self.enabled / "canary-alpha"
        reinstalled.mkdir()
        (reinstalled / "SKILL.md").write_text(REINSTALLED_SKILL, encoding="utf-8")

        self.dispose.side_effect = RuntimeError("offline")
        with self.assertRaises(HTTPException) as raised:
            self.controller.list_skills()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.controller._load_state()["revision"], 1)
        self.assertFalse(reinstalled.exists())

        self.dispose.side_effect = self.fake_dispose
        reconciled = self.controller.list_skills()
        self.assertEqual(reconciled["revision"], 2)
        self.assertFalse(reinstalled.exists())
        self.assertEqual(
            (self.disabled / "canary-alpha" / "SKILL.md").read_text(encoding="utf-8"),
            SKILL,
        )
        conflict = reconciled["reconciliations"][0]
        self.assertEqual(
            {key: conflict[key] for key in ("name", "policy", "action")},
            {
                "name": "canary-alpha",
                "policy": "disabled-wins",
                "action": "quarantined-enabled-copy",
            },
        )
        quarantined = Path(conflict["quarantine_location"])
        self.assertEqual((quarantined / "SKILL.md").read_text(encoding="utf-8"), REINSTALLED_SKILL)
        self.assertEqual(self.controller.list_skills()["reconciliations"], reconciled["reconciliations"])

    def test_invalid_and_symlinked_skills_are_ignored(self):
        invalid = self.enabled / "invalid-skill"
        invalid.mkdir()
        (invalid / "SKILL.md").write_text("# no front matter\n", encoding="utf-8")

        target = Path(self.tmp.name) / "linked-target"
        target.mkdir()
        (target / "SKILL.md").write_text(
            SKILL.replace("canary-alpha", "linked-skill"),
            encoding="utf-8",
        )
        (self.enabled / "linked-skill").symlink_to(target, target_is_directory=True)

        catalog = self.controller.list_skills()
        self.assertEqual([item["name"] for item in catalog["skills"]], ["canary-alpha"])

    def test_non_mapping_disabled_copy_does_not_quarantine_valid_enabled_skill(self):
        invalid = self.disabled / "canary-alpha"
        invalid.mkdir(parents=True)
        (invalid / "SKILL.md").write_text(
            "---\n- list-not-a-mapping\n---\n",
            encoding="utf-8",
        )

        catalog = self.controller.list_skills()

        self.assertEqual(
            [(item["name"], item["enabled"]) for item in catalog["skills"]],
            [("canary-alpha", True)],
        )
        self.assertTrue((self.enabled / "canary-alpha").is_dir())
        self.assertTrue(invalid.is_dir())
        self.assertEqual(catalog["reconciliations"], [])


if __name__ == "__main__":
    unittest.main()
