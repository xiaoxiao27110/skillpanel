import contextlib
import fcntl
import importlib.util
import io
import json
import os
import signal
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


LAUNCHER_PATH = Path(__file__).parents[1] / "docker" / "opencode_launcher.py"
SPEC = importlib.util.spec_from_file_location("opencode_launcher", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(launcher)


class FakeChild:
    def __init__(self, pid, wait_callback=None, returncode=0):
        self.pid = pid
        self.wait_callback = wait_callback
        self.returncode = None
        self.final_returncode = returncode
        self.signals = []
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.wait_callback is not None:
            self.wait_callback()
        self.returncode = self.final_returncode
        return self.returncode

    def send_signal(self, received):
        self.signals.append(received)

    def terminate(self):
        self.terminated = True
        self.returncode = -signal.SIGTERM

    def kill(self):
        self.returncode = -signal.SIGKILL


class FakeResponse:
    def __init__(self, payload=b""):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self):
        return self.payload


class OpenCodeLauncherTests(unittest.TestCase):
    def test_registry_default_is_separate_from_auth_runtime(self):
        self.assertEqual(
            launcher.DEFAULT_REGISTRY_DIR,
            "/run/skillpanel-opencode-tuis",
        )

    def test_state_lock_path_matches_controller_suffix_rule(self):
        with patch.dict(
            os.environ,
            {"SKILLPANEL_STATE_FILE": "/data/custom.state.json"},
        ):
            self.assertEqual(
                launcher.state_lock_path(),
                Path("/data/custom.state.lock"),
            )

    def test_routes_only_default_root_command_to_tui(self):
        self.assertTrue(launcher.is_tui_invocation([]))
        self.assertTrue(launcher.is_tui_invocation(["/workspace/project"]))
        self.assertTrue(launcher.is_tui_invocation(["--model", "provider/model", "--mini"]))
        self.assertTrue(launcher.is_tui_invocation(["--", "run"]))
        self.assertFalse(launcher.is_tui_invocation(["generate"]))
        self.assertFalse(launcher.is_tui_invocation(["console"]))

        for command in launcher.NON_TUI_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(launcher.is_tui_invocation([command]))
                self.assertFalse(
                    launcher.is_tui_invocation(["--log-level", "DEBUG", command])
                )
        for flag in launcher.PASSTHROUGH_FLAGS:
            with self.subTest(flag=flag):
                self.assertFalse(launcher.is_tui_invocation([flag]))

    def test_mini_tui_fails_closed_with_usage_error(self):
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            patch.object(launcher, "run_tui") as run_tui,
        ):
            result = launcher.main(["/workspace", "--mini"])

        self.assertEqual(result, 2)
        self.assertIn("--mini is unsupported", stderr.getvalue())
        run_tui.assert_not_called()

    def test_mini_after_separator_is_a_project_argument(self):
        self.assertFalse(launcher.requests_mini(["--", "--mini"]))

    def test_forces_random_loopback_endpoint(self):
        arguments = launcher._tui_arguments(
            [
                "/workspace",
                "--hostname",
                "0.0.0.0",
                "--port=9999",
                "--mdns",
                "--mdns-domain",
                "unsafe.local",
                "--mini",
            ],
            43210,
        )
        self.assertEqual(
            arguments,
            [
                "/workspace",
                "--mini",
                "--hostname=127.0.0.1",
                "--port=43210",
            ],
        )

    def test_project_directory_uses_tui_project_positional(self):
        self.assertEqual(
            launcher.project_directory(["relative/project"], "/workspace"),
            "/workspace/relative/project",
        )
        self.assertEqual(
            launcher.project_directory(["--model", "p/m"], "/workspace"),
            "/workspace",
        )

    def test_loopback_options_precede_positional_separator(self):
        self.assertEqual(
            launcher._tui_arguments(["--", "run"], 43210),
            ["--hostname=127.0.0.1", "--port=43210", "--", "run"],
        )

    def test_registry_record_is_atomic_and_group_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry_dir = Path(temporary)
            record = {
                "directory": "/workspace",
                "pid": os.getpid(),
                "start_time": 123,
                "url": "http://127.0.0.1:4567",
            }
            path = launcher.write_registry_record(
                registry_dir,
                record,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            )
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), record)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(list(registry_dir.glob(".*.tmp")), [])

    def test_runtime_is_healthy_and_disposed_once_before_publication(self):
        child = FakeChild(43209)
        requests = []

        def urlopen(request, timeout):
            requests.append((request.get_method(), request.full_url, timeout))
            if request.get_method() == "GET":
                return FakeResponse(b'{"healthy":true}')
            return FakeResponse()

        with patch.object(launcher.urllib.request, "urlopen", side_effect=urlopen):
            launcher.initialize_runtime(
                child,
                "http://127.0.0.1:32122",
                timeout=1,
            )

        self.assertEqual(
            [(method, url) for method, url, _timeout in requests],
            [
                ("GET", "http://127.0.0.1:32122/global/health"),
                ("POST", "http://127.0.0.1:32122/global/dispose"),
            ],
        )
        self.assertLessEqual(requests[0][2], launcher.HTTP_ATTEMPT_TIMEOUT_SECONDS)
        self.assertGreater(requests[1][2], launcher.HTTP_ATTEMPT_TIMEOUT_SECONDS)

    def test_runtime_initialization_rejects_timeout_and_early_exit(self):
        with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
            launcher.initialize_runtime(FakeChild(43208), "http://127.0.0.1:1", timeout=0)

        exited = FakeChild(43207)
        exited.returncode = 9
        with self.assertRaisesRegex(RuntimeError, "exited with status 9"):
            launcher.initialize_runtime(exited, "http://127.0.0.1:1", timeout=1)

    def test_state_lock_covers_start_initialization_and_registry_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry_dir = Path(temporary)
            lock_file = registry_dir / "state.lock"
            lock_file.touch()
            child_pid = 43210
            captured = {}

            def lock_is_held(expected):
                with lock_file.open("a+", encoding="utf-8") as handle:
                    if expected:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(
                                handle.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                    else:
                        fcntl.flock(
                            handle.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            def inspect_record():
                lock_is_held(False)
                path = registry_dir / f"{child_pid}.json"
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(record["pid"], child_pid)
                self.assertEqual(record["start_time"], 87654)
                self.assertEqual(record["url"], "http://127.0.0.1:32123")
                self.assertEqual(record["directory"], os.path.realpath("/workspace"))

            child = FakeChild(child_pid, inspect_record, returncode=7)

            def popen(command):
                lock_is_held(True)
                captured["command"] = command
                return child

            def choose_port():
                lock_is_held(True)
                captured["port_chosen_under_lock"] = True
                return 32123

            def initialize(_child, url, *, timeout):
                lock_is_held(True)
                captured["initialized"] = (url, timeout)
                self.assertEqual(list(registry_dir.glob("*.json")), [])

            real_write = launcher.write_registry_record

            def publish(*args, **kwargs):
                lock_is_held(True)
                return real_write(*args, **kwargs)

            with (
                patch.object(launcher, "choose_loopback_port", side_effect=choose_port),
                patch.object(launcher, "initialize_runtime", side_effect=initialize),
                patch.object(launcher, "read_linux_start_time", return_value=87654),
                patch.object(launcher, "write_registry_record", side_effect=publish),
                patch.object(launcher.subprocess, "Popen", side_effect=popen),
            ):
                result = launcher.run_tui(
                    ["/workspace"],
                    registry_dir=registry_dir,
                    owner_uid=os.getuid(),
                    owner_gid=os.getgid(),
                    lock_file=lock_file,
                )

            self.assertEqual(result, 7)
            self.assertTrue(captured["port_chosen_under_lock"])
            self.assertEqual(
                captured["initialized"],
                ("http://127.0.0.1:32123", launcher.STARTUP_TIMEOUT_SECONDS),
            )
            self.assertEqual(
                captured["command"],
                [
                    launcher.NATIVE_OPENCODE,
                    "/workspace",
                    "--hostname=127.0.0.1",
                    "--port=32123",
                ],
            )
            self.assertFalse((registry_dir / f"{child_pid}.json").exists())

    def test_tui_forwards_termination_signal_to_native_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry_dir = Path(temporary)
            lock_file = registry_dir / "state.lock"
            lock_file.touch()
            child = FakeChild(
                43211,
                wait_callback=lambda: os.kill(os.getpid(), signal.SIGTERM),
            )
            with (
                patch.object(launcher, "choose_loopback_port", return_value=32124),
                patch.object(launcher, "initialize_runtime"),
                patch.object(launcher, "read_linux_start_time", return_value=87655),
                patch.object(launcher.subprocess, "Popen", return_value=child),
            ):
                result = launcher.run_tui(
                    [],
                    registry_dir=registry_dir,
                    owner_uid=os.getuid(),
                    owner_gid=os.getgid(),
                    lock_file=lock_file,
                )

            self.assertEqual(result, 0)
            self.assertEqual(child.signals, [signal.SIGTERM])
            self.assertFalse((registry_dir / "43211.json").exists())

    def test_terminal_sigint_is_not_forwarded_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry_dir = Path(temporary)
            lock_file = registry_dir / "state.lock"
            lock_file.touch()
            child = FakeChild(
                43212,
                wait_callback=lambda: os.kill(os.getpid(), signal.SIGINT),
                returncode=-signal.SIGINT,
            )
            with (
                patch.object(launcher, "choose_loopback_port", return_value=32125),
                patch.object(launcher, "initialize_runtime"),
                patch.object(launcher, "read_linux_start_time", return_value=87656),
                patch.object(launcher.subprocess, "Popen", return_value=child),
            ):
                result = launcher.run_tui(
                    [],
                    registry_dir=registry_dir,
                    owner_uid=os.getuid(),
                    owner_gid=os.getgid(),
                    lock_file=lock_file,
                )

            self.assertEqual(result, -signal.SIGINT)
            self.assertNotIn(signal.SIGINT, launcher.FORWARDED_SIGNALS)
            self.assertEqual(child.signals, [])
            self.assertFalse((registry_dir / "43212.json").exists())

    def test_initialization_failure_terminates_child_without_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry_dir = Path(temporary)
            lock_file = registry_dir / "state.lock"
            lock_file.touch()
            child = FakeChild(43213)
            with (
                patch.object(launcher, "choose_loopback_port", return_value=32126),
                patch.object(
                    launcher,
                    "initialize_runtime",
                    side_effect=RuntimeError("startup failed"),
                ),
                patch.object(launcher.subprocess, "Popen", return_value=child),
            ):
                with self.assertRaisesRegex(RuntimeError, "startup failed"):
                    launcher.run_tui(
                        [],
                        registry_dir=registry_dir,
                        owner_uid=os.getuid(),
                        owner_gid=os.getgid(),
                        lock_file=lock_file,
                    )

            self.assertTrue(child.terminated)
            self.assertEqual(
                [path for path in registry_dir.iterdir() if path != lock_file],
                [],
            )

    def test_missing_precreated_lock_fails_before_native_child_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry_dir = Path(temporary)
            with (
                patch.object(launcher, "choose_loopback_port", return_value=32127),
                patch.object(launcher.subprocess, "Popen") as popen,
            ):
                with self.assertRaises(FileNotFoundError):
                    launcher.run_tui(
                        [],
                        registry_dir=registry_dir,
                        owner_uid=os.getuid(),
                        owner_gid=os.getgid(),
                        lock_file=registry_dir / "missing.lock",
                    )

            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
