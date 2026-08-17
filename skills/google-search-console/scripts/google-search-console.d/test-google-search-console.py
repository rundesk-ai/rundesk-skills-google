#!/usr/bin/env python3
"""Offline tests for google-search-console."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "google-search-console.py"
LAUNCHER = HERE.parent / "google-search-console"


def load_module():
    spec = importlib.util.spec_from_file_location("google_search_console_module", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class SearchConsoleTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.env = {
            "GOOGLE_SEARCH_CONSOLE_CLIENT_ID__EXAMPLE": "client",
            "GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET__EXAMPLE": "secret",
            "GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN__EXAMPLE": "refresh",
            "GOOGLE_SEARCH_CONSOLE_LABEL__EXAMPLE": "Example Search Console",
        }
        self.profile = self.module.Profile("example", "client", "secret", "refresh", "Example")

    def test_profiles_discovers_rundesk_account_without_network(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.module.urllib.request, "urlopen", side_effect=AssertionError("network")
        ), redirect_stdout(io.StringIO()) as output:
            code = self.module.main(["profiles"])
        self.assertEqual(code, 0)
        self.assertIn("example,Example Search Console,ready", output.getvalue())

    def test_named_profile_never_falls_back_to_plain_credentials(self):
        env = {
            "GOOGLE_SEARCH_CONSOLE_CLIENT_ID": "plain-client",
            "GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET": "plain-secret",
            "GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN": "plain-refresh",
            "GOOGLE_SEARCH_CONSOLE_CLIENT_ID__EXAMPLE": "named-client",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.SearchConsoleError) as raised:
                self.module.get_profile("example")
        self.assertIn("CLIENT_SECRET__EXAMPLE", str(raised.exception))
        self.assertNotIn("plain-secret", str(raised.exception))

    def test_default_profile_setting_does_not_reassign_plain_credentials(self):
        env = {
            "GOOGLE_SEARCH_CONSOLE_DEFAULT_PROFILE": "example",
            "GOOGLE_SEARCH_CONSOLE_CLIENT_ID": "plain-client",
            "GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET": "plain-secret",
            "GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN": "plain-refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.SearchConsoleError):
                self.module.get_profile("example")
            with self.assertRaisesRegex(self.module.SearchConsoleError, "letter or digit"):
                self.module.get_profile("---")

    def test_profile_credentials_never_mix_rundesk_and_legacy_forms(self):
        env = {
            "GOOGLE_SEARCH_CONSOLE_CLIENT_ID__EXAMPLE": "suffix-client",
            "GOOGLE_SEARCH_CONSOLE_EXAMPLE_CLIENT_SECRET": "legacy-secret",
            "GOOGLE_SEARCH_CONSOLE_EXAMPLE_REFRESH_TOKEN": "legacy-refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.SearchConsoleError) as raised:
                self.module.get_profile("example")
        self.assertIn("CLIENT_SECRET__EXAMPLE", str(raised.exception))
        self.assertNotIn("legacy-secret", str(raised.exception))

    def test_plain_credentials_create_default_profile(self):
        env = {
            "GOOGLE_SEARCH_CONSOLE_CLIENT_ID": "client",
            "GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET": "secret",
            "GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN": "refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.module.discovered_profiles(), ["default"])
            self.assertEqual(self.module.get_profile("default").client_id, "client")

    def test_dotenv_preserves_unmatched_quotes_and_rejects_invalid_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            path.write_text(
                "GOOGLE_SEARCH_CONSOLE_CLIENT_ID=value'\n"
                "export GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET=ignored\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            with patch.dict(os.environ, {}, clear=True):
                self.module.load_dotenv(path)
                self.assertEqual(os.environ["GOOGLE_SEARCH_CONSOLE_CLIENT_ID"], "value'")
                self.assertNotIn("export GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET", os.environ)

    def test_explicit_missing_env_file_is_refused(self):
        missing = "/tmp/rundesk-google-search-console-does-not-exist"
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["profiles", "--env-file", missing])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", error.getvalue())

    def test_token_refresh_posts_credentials_without_printing_them(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response({"access_token": "access"})

        self.assertEqual(self.module.access_token(self.profile, opener=opener), "access")
        request = requests[0]
        body = request.data.decode()
        self.assertIn("client_id=client", body)
        self.assertIn("refresh_token=refresh", body)
        self.assertNotIn("secret", repr(self.profile))

    def test_request_error_exposes_google_message_but_not_authorization(self):
        error = urllib.error.HTTPError(
            "https://www.googleapis.com/example", 403, "Forbidden", {},
            io.BytesIO(json.dumps({"error": {"message": "Permission denied"}}).encode()),
        )
        with self.assertRaises(self.module.SearchConsoleError) as raised:
            self.module.request_json(
                "https://www.googleapis.com/example",
                headers={"Authorization": "Bearer hidden"},
                opener=lambda *args, **kwargs: (_ for _ in ()).throw(error),
            )
        self.assertIn("Permission denied", str(raised.exception))
        self.assertNotIn("hidden", str(raised.exception))

    def test_redirects_are_refused(self):
        handler = self.module.RejectRedirectHandler()
        request = self.module.urllib.request.Request(
            self.module.WEBMASTERS_API + "/sites",
            headers={"Authorization": "Bearer secret"},
        )
        self.assertIsNone(
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://example.test/intercept"
            )
        )

    def test_sites_uses_exact_api_and_bounds_output(self):
        args = SimpleNamespace(profile="example", limit=1, json=False)
        payload = {"siteEntry": [
            {"siteUrl": "sc-domain:example.test", "permissionLevel": "siteOwner"},
            {"siteUrl": "https://www.example.test/", "permissionLevel": "siteRestrictedUser"},
        ]}
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", return_value=payload
        ) as call, redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            self.module.cmd_sites(args)
        call.assert_called_once_with(self.profile, "/sites")
        self.assertIn("sc-domain:example.test", output.getvalue())
        self.assertNotIn("www.example.test", output.getvalue())
        self.assertIn("truncated", error.getvalue())

    def test_performance_encodes_property_and_posts_requested_dimensions(self):
        args = SimpleNamespace(
            profile="example", site="https://www.example.test/", days=28,
            start_date="2026-07-01", end_date="2026-07-31",
            dimension=["query", "page"], search_type="web", limit=10, json=True,
        )
        payload = {"rows": [{"keys": ["example", "https://www.example.test/page"], "clicks": 3, "impressions": 20, "ctr": 0.15, "position": 4.2}]}
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", return_value=payload
        ) as call, redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.module.cmd_performance(args)
        path = call.call_args.args[1]
        body = call.call_args.kwargs["body"]
        self.assertIn("https%3A%2F%2Fwww.example.test%2F", path)
        self.assertEqual(body["dimensions"], ["query", "page"])
        self.assertEqual(body["rowLimit"], 10)
        self.assertEqual(json.loads(output.getvalue())[0]["clicks"], 3)

    def test_performance_warns_when_row_limit_is_reached(self):
        args = SimpleNamespace(
            profile="example", site="sc-domain:example.test", days=28,
            start_date="2026-07-01", end_date="2026-07-31",
            dimension=["query"], search_type=None, limit=1, json=False,
        )
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", return_value={"rows": [{"keys": ["example"]}]}
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            self.module.cmd_performance(args)
        self.assertIn("may be truncated", error.getvalue())

    def test_inspect_url_uses_inspection_api_and_normalizes_result(self):
        args = SimpleNamespace(profile="example", site="sc-domain:example.test", url="https://example.test/page", json=True)
        payload = {"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed", "lastCrawlTime": "2026-08-01T12:00:00Z"}}}
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", return_value=payload
        ) as call, redirect_stdout(io.StringIO()) as output:
            self.module.cmd_inspect(args)
        self.assertEqual(call.call_args.kwargs["base"], self.module.INSPECTION_API)
        self.assertEqual(call.call_args.kwargs["body"]["inspectionUrl"], args.url)
        self.assertEqual(json.loads(output.getvalue())[0]["verdict"], "PASS")

    def test_inspect_url_refuses_an_empty_success_response(self):
        args = SimpleNamespace(
            profile="example",
            site="sc-domain:example.test",
            url="https://example.test/page",
            json=True,
        )
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", return_value={}
        ):
            with self.assertRaisesRegex(self.module.SearchConsoleError, "no URL inspection result"):
                self.module.cmd_inspect(args)

    def test_sitemaps_lists_compact_fields(self):
        args = SimpleNamespace(profile="example", site="sc-domain:example.test", limit=2, json=False)
        payload = {"sitemap": [{"path": "https://example.test/sitemap.xml", "type": "sitemap", "isPending": False, "errors": "0", "warnings": "1"}]}
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", return_value=payload
        ), redirect_stdout(io.StringIO()) as output:
            self.module.cmd_sitemaps(args)
        self.assertIn("sitemap.xml", output.getvalue())
        self.assertIn("warnings", output.getvalue())

    def test_date_range_rejects_partial_or_reversed_dates(self):
        base = {"days": 28, "start_date": "2026-08-02", "end_date": None}
        with self.assertRaises(self.module.SearchConsoleError):
            self.module.date_range(SimpleNamespace(**base))
        with self.assertRaises(self.module.SearchConsoleError):
            self.module.date_range(SimpleNamespace(days=28, start_date="2026-08-02", end_date="2026-08-01"))

    def test_main_rejects_unbounded_limit_before_network(self):
        with patch.dict(os.environ, self.env, clear=True), redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["sites", "--profile", "example", "--limit", "1001"])
        self.assertEqual(code, 2)
        self.assertIn("between 1 and 1000", error.getvalue())

    def test_launcher_help_is_credential_free_and_resolves_outside_repo(self):
        result = subprocess.run(
            [str(LAUNCHER), "--help"], cwd="/tmp", env={"PATH": os.environ.get("PATH", "")},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Google Search Console", result.stdout)


if __name__ == "__main__":
    unittest.main()
