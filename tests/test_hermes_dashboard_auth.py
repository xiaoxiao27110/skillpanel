from types import SimpleNamespace
from unittest import TestCase, skipIf
from unittest.mock import patch

try:
    from starlette.requests import Request
    from hermes_cli import main as hermes_main
    from hermes_cli.dashboard_auth import middleware
except ModuleNotFoundError as exc:
    if exc.name != "hermes_cli" and not str(exc.name).startswith("hermes_cli."):
        raise
    Request = None
    hermes_main = None
    middleware = None


@skipIf(middleware is None, "Hermes is only installed in the container test runtime")
class HermesDashboardAuthTests(TestCase):
    def test_password_provider_uses_login_form_instead_of_oauth_redirect(self):
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 9119),
            }
        )
        provider = SimpleNamespace(
            name="basic",
            supports_session=True,
            supports_password=True,
        )

        with patch.object(middleware, "list_session_providers", return_value=[provider]):
            self.assertIsNone(middleware._auto_sso_response(request))

    def test_wheel_uses_bundled_tui_without_source_workspace(self):
        bundled = hermes_main._find_bundled_tui()
        missing_workspace = hermes_main.PROJECT_ROOT / "ui-tui"

        self.assertIsNotNone(bundled)
        self.assertTrue(bundled.is_file())
        self.assertFalse(missing_workspace.exists())

        argv, cwd = hermes_main._make_tui_argv(missing_workspace, False)

        self.assertEqual(argv[0], hermes_main.shutil.which("node"))
        self.assertEqual(argv[1:], ["--expose-gc", str(bundled)])
        self.assertEqual(cwd, bundled.parent)
