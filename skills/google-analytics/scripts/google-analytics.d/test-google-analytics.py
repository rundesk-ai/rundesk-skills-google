#!/usr/bin/env python3
"""Offline tests for the Google Analytics integration."""

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


SCRIPT = Path(__file__).resolve().parent / "google-analytics.py"
LAUNCHER = SCRIPT.parent.parent / "google-analytics"


def load_module():
    spec = importlib.util.spec_from_file_location("google_analytics_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load Google Analytics module")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload


class RawResponse:
    """A response body Google never should have sent, kept exactly as received."""

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload


class GoogleAnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.profile = self.module.Profile("example", "client", "secret", "refresh", "Example")

    def test_profiles_discovers_rundesk_accounts_without_network(self):
        env = {
            "GOOGLE_ANALYTICS_CLIENT_ID__EXAMPLE": "client",
            "GOOGLE_ANALYTICS_CLIENT_SECRET__EXAMPLE": "secret",
            "GOOGLE_ANALYTICS_REFRESH_TOKEN__EXAMPLE": "refresh",
            "GOOGLE_ANALYTICS_LABEL__EXAMPLE": "Example Analytics",
        }
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "open_url", side_effect=AssertionError("network called")
        ), redirect_stdout(output):
            result = self.module.main(["profiles"])
        self.assertEqual(result, 0)
        self.assertIn("example,Example Analytics,true", output.getvalue())

    def test_plain_values_form_default_profile(self):
        env = {
            "GOOGLE_ANALYTICS_CLIENT_ID": "client",
            "GOOGLE_ANALYTICS_CLIENT_SECRET": "secret",
            "GOOGLE_ANALYTICS_REFRESH_TOKEN": "refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.module.configured_profile_names(), ["default"])
            profile = self.module.get_profile("default")
        self.assertEqual(profile.refresh_token, "refresh")

    def test_legacy_profile_values_are_supported(self):
        env = {
            "GOOGLE_ANALYTICS_EXAMPLE_CLIENT_ID": "client",
            "GOOGLE_ANALYTICS_EXAMPLE_CLIENT_SECRET": "secret",
            "GOOGLE_ANALYTICS_EXAMPLE_REFRESH_TOKEN": "refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.module.configured_profile_names(), ["example"])
            self.assertEqual(self.module.get_profile("example").client_secret, "secret")

    def test_named_profile_never_uses_plain_credentials(self):
        env = {
            "GOOGLE_ANALYTICS_CLIENT_ID": "plain-client",
            "GOOGLE_ANALYTICS_CLIENT_SECRET": "plain-secret",
            "GOOGLE_ANALYTICS_REFRESH_TOKEN": "plain-refresh",
            "GOOGLE_ANALYTICS_CLIENT_ID__EXAMPLE": "client",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.AnalyticsError) as raised:
                self.module.get_profile("example")
        self.assertIn("GOOGLE_ANALYTICS_CLIENT_SECRET__EXAMPLE", str(raised.exception))
        self.assertNotIn("plain-secret", str(raised.exception))

    def test_default_profile_setting_does_not_reassign_plain_credentials(self):
        env = {
            "GOOGLE_ANALYTICS_DEFAULT_PROFILE": "example",
            "GOOGLE_ANALYTICS_CLIENT_ID": "plain-client",
            "GOOGLE_ANALYTICS_CLIENT_SECRET": "plain-secret",
            "GOOGLE_ANALYTICS_REFRESH_TOKEN": "plain-refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.AnalyticsError):
                self.module.get_profile("example")

    def test_profile_credentials_never_mix_rundesk_and_legacy_forms(self):
        env = {
            "GOOGLE_ANALYTICS_CLIENT_ID__EXAMPLE": "suffix-client",
            "GOOGLE_ANALYTICS_EXAMPLE_CLIENT_SECRET": "legacy-secret",
            "GOOGLE_ANALYTICS_EXAMPLE_REFRESH_TOKEN": "legacy-refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(self.module.AnalyticsError, "both Rundesk suffix and legacy"):
                self.module.get_profile("example")

    def test_missing_legacy_credential_uses_the_legacy_variable_name(self):
        env = {
            "GOOGLE_ANALYTICS_EXAMPLE_CLIENT_ID": "legacy-client",
            "GOOGLE_ANALYTICS_EXAMPLE_CLIENT_SECRET": "legacy-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.AnalyticsError) as raised:
                self.module.get_profile("example")
        self.assertIn("GOOGLE_ANALYTICS_EXAMPLE_REFRESH_TOKEN", str(raised.exception))
        self.assertNotIn("REFRESH_TOKEN__EXAMPLE", str(raised.exception))

    def test_dotenv_does_not_replace_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            path.write_text("GOOGLE_ANALYTICS_CLIENT_ID=file\n", encoding="utf-8")
            path.chmod(0o600)
            with patch.dict(os.environ, {"GOOGLE_ANALYTICS_CLIENT_ID": "process"}, clear=True):
                self.module.load_dotenv(path)
                self.assertEqual(os.environ["GOOGLE_ANALYTICS_CLIENT_ID"], "process")

    def test_dotenv_warns_about_broad_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            path.write_text("GOOGLE_ANALYTICS_CLIENT_ID=file\n", encoding="utf-8")
            path.chmod(0o604)
            errors = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), redirect_stderr(errors):
                self.module.load_dotenv(path)
        self.assertIn("chmod 600", errors.getvalue())

    def test_explicit_missing_env_file_is_refused(self):
        missing = "/tmp/rundesk-google-analytics-does-not-exist"
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["profiles", "--env-file", missing])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", error.getvalue())

    def test_refresh_access_token_posts_oauth_form(self):
        captured = {}

        def fake_open(request, timeout=30):
            captured["url"] = request.full_url
            captured["body"] = request.data.decode("ascii")
            return Response({"access_token": "access"})

        with patch.object(self.module, "open_url", side_effect=fake_open):
            token = self.module.refresh_access_token(self.profile)
        self.assertEqual(token, "access")
        self.assertEqual(captured["url"], self.module.TOKEN_URL)
        self.assertIn("refresh_token=refresh", captured["body"])
        self.assertNotIn("secret", repr(self.profile))

    def test_api_request_refuses_unexpected_origin(self):
        with self.assertRaises(self.module.AnalyticsError):
            self.module.api_request("token", "GET", "https://example.test/data")

    def test_redirects_are_refused(self):
        handler = self.module.RejectRedirectHandler()
        request = self.module.urllib.request.Request(
            self.module.ADMIN_BASE + "/accountSummaries",
            headers={"Authorization": "Bearer secret"},
        )
        self.assertIsNone(
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://example.test/intercept",
            )
        )

    def test_api_error_does_not_disclose_authorization(self):
        request_error = urllib.error.HTTPError(
            "https://analyticsdata.googleapis.com/v1beta/properties/1:runReport",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"error":{"message":"Permission denied"}}'),
        )
        with patch.object(self.module, "open_url", side_effect=request_error):
            with self.assertRaises(self.module.AnalyticsError) as raised:
                self.module.api_request(
                    "sensitive-access-token",
                    "POST",
                    self.module.DATA_BASE + "/properties/1:runReport",
                    payload={},
                    retries=0,
                )
        self.assertIn("Permission denied", str(raised.exception))
        self.assertNotIn("sensitive-access-token", str(raised.exception))

    def test_account_summaries_pages_only_to_limit(self):
        calls = []
        responses = [
            ({"accountSummaries": [{"account": "accounts/1"}], "nextPageToken": "next"}),
            ({"accountSummaries": [{"account": "accounts/2"}], "nextPageToken": "more"}),
        ]

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            calls.append(params)
            return responses.pop(0)

        with patch.object(self.module, "api_request", side_effect=fake_request):
            rows, truncated = self.module.account_summaries("token", 2)
        self.assertEqual([row["account"] for row in rows], ["accounts/1", "accounts/2"])
        self.assertTrue(truncated)
        self.assertEqual(calls[1]["pageToken"], "next")

    def test_account_summaries_stops_on_an_empty_page_with_a_token(self):
        response = {"accountSummaries": [], "nextPageToken": "next"}
        with patch.object(self.module, "api_request", return_value=response) as request:
            rows, truncated = self.module.account_summaries("token", 5)
        self.assertEqual([], rows)
        self.assertTrue(truncated)
        request.assert_called_once()

    def test_accounts_emits_normalized_rows(self):
        args = SimpleNamespace(profile="example", limit=25, json=True)
        summaries = [
            {
                "account": "accounts/123",
                "displayName": "Example account",
                "propertySummaries": [{"property": "properties/456"}],
            }
        ]
        output = io.StringIO()
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "account_summaries", return_value=(summaries, False)), redirect_stdout(output):
            self.module.command_accounts(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload[0]["account_id"], "123")
        self.assertEqual(payload[0]["property_count"], 1)

    def test_properties_filters_one_account_and_bounds_rows(self):
        args = SimpleNamespace(profile="example", account="123", limit=1, json=True)
        summaries = [
            {
                "account": "accounts/123",
                "propertySummaries": [
                    {"property": "properties/10", "displayName": "One", "propertyType": "PROPERTY_TYPE_ORDINARY", "parent": "accounts/123"},
                    {"property": "properties/11", "displayName": "Two", "propertyType": "PROPERTY_TYPE_ORDINARY", "parent": "accounts/123"},
                ],
            },
            {"account": "accounts/999", "propertySummaries": [{"property": "properties/99"}]},
        ]
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "account_summaries", return_value=(summaries, False)), redirect_stdout(output), redirect_stderr(errors):
            self.module.command_properties(args)
        self.assertEqual(json.loads(output.getvalue())[0]["property_id"], "10")
        self.assertIn("truncated", errors.getvalue())

    def test_report_builds_bounded_data_api_request(self):
        args = SimpleNamespace(
            profile="example", property="456", start_date="28daysAgo", end_date="today",
            metrics="sessions,activeUsers", dimensions="date", limit=25, json=True,
        )
        captured = {}

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            captured.update({"method": method, "url": url, "payload": payload})
            return {
                "rowCount": 1,
                "rows": [{"dimensionValues": [{"value": "20260817"}], "metricValues": [{"value": "12"}, {"value": "9"}]}],
            }

        output = io.StringIO()
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "api_request", side_effect=fake_request), redirect_stdout(output):
            self.module.command_report(args)
        self.assertTrue(captured["url"].endswith("properties/456:runReport"))
        self.assertEqual(captured["payload"]["limit"], "25")
        self.assertEqual(json.loads(output.getvalue())[0]["sessions"], "12")

    def test_report_refuses_an_invalid_row_count(self):
        args = SimpleNamespace(
            profile="example", property="123", start_date="28daysAgo", end_date="today",
            metrics="sessions", dimensions="date", limit=2, json=False,
        )
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(
            self.module, "api_request", return_value={"rows": [], "rowCount": None}
        ), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(self.module.AnalyticsError, "invalid row count"):
                self.module.command_report(args)

    def test_realtime_uses_realtime_endpoint(self):
        args = SimpleNamespace(profile="example", property="456", metrics="activeUsers", dimensions="", limit=10, json=True)
        captured = {}

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            captured["url"] = url
            return {"rowCount": 0, "rows": []}

        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "api_request", side_effect=fake_request), redirect_stdout(io.StringIO()):
            self.module.command_realtime(args)
        self.assertTrue(captured["url"].endswith("properties/456:runRealtimeReport"))

    def test_malformed_json_is_reported_without_a_traceback(self):
        for body in (b"<html>not json</html>", b"\xff\xfe not utf-8"):
            with self.subTest(body=body):
                with patch.object(self.module, "open_url", return_value=RawResponse(body)):
                    with self.assertRaisesRegex(self.module.AnalyticsError, "not valid JSON"):
                        self.module.api_request("token", "GET", self.module.ADMIN_BASE + "/accountSummaries")

    def test_non_object_api_and_token_responses_are_refused(self):
        for body in (b"[]", b'"text"', b"7", b"null"):
            with self.subTest(body=body):
                with patch.object(self.module, "open_url", return_value=RawResponse(body)):
                    with self.assertRaisesRegex(self.module.AnalyticsError, "malformed API response"):
                        self.module.api_request("token", "GET", self.module.ADMIN_BASE + "/accountSummaries")
                with patch.object(self.module, "open_url", return_value=RawResponse(body)):
                    with self.assertRaisesRegex(self.module.AnalyticsError, "malformed OAuth token response"):
                        self.module.refresh_access_token(self.profile)

    def test_non_string_access_token_is_refused(self):
        with patch.object(self.module, "open_url", return_value=Response({"access_token": {"value": "x"}})):
            with self.assertRaisesRegex(self.module.AnalyticsError, "no access token"):
                self.module.refresh_access_token(self.profile)

    def test_account_summaries_refuse_wrong_collection_and_object_shapes(self):
        cases = (
            ({"accountSummaries": {"account": "accounts/1"}}, "account summary collection"),
            ({"accountSummaries": ["accounts/1"]}, "malformed account summary"),
            ({"accountSummaries": [], "nextPageToken": {"token": "next"}}, "malformed page token"),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                with patch.object(self.module, "api_request", return_value=response):
                    with self.assertRaisesRegex(self.module.AnalyticsError, expected):
                        self.module.account_summaries("token", 5)

    def test_report_refuses_wrong_row_and_value_shapes(self):
        cases = (
            ({"rows": {"dimensionValues": []}}, "report row collection"),
            ({"rows": ["20260817"]}, "malformed report row"),
            ({"rows": [{"dimensionValues": "20260817"}]}, "report dimension value collection"),
            ({"rows": [{"dimensionValues": ["20260817"]}]}, "malformed report dimension value"),
            ({"rows": [{"dimensionValues": [], "metricValues": ["12"]}]}, "malformed report metric value"),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                with self.assertRaisesRegex(self.module.AnalyticsError, expected):
                    self.module.normalized_report(response, ["date"], ["sessions"], self.profile, "456")

    def test_non_string_resource_identifiers_are_refused(self):
        with self.assertRaises(self.module.AnalyticsError):
            self.module.resource_id({"account": 1}, "accounts")

    def test_malformed_response_exits_two_instead_of_raising(self):
        env = {
            "GOOGLE_ANALYTICS_CLIENT_ID": "client",
            "GOOGLE_ANALYTICS_CLIENT_SECRET": "secret",
            "GOOGLE_ANALYTICS_REFRESH_TOKEN": "refresh",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(
            self.module, "open_url", return_value=RawResponse(b"<html>not json</html>")
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["accounts"])
        self.assertEqual(2, code)
        self.assertIn("not valid JSON", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_invalid_property_and_excessive_limits_are_refused(self):
        with self.assertRaises(self.module.AnalyticsError):
            self.module.resource_id("not-an-id", "properties")
        with self.assertRaises(self.module.AnalyticsError):
            self.module.bounded_limit(10001)

    def test_launcher_help_resolves_outside_repository(self):
        completed = subprocess.run(
            [str(LAUNCHER), "--help"], cwd="/tmp", env={"PATH": os.environ.get("PATH", "")},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Google Analytics", completed.stdout)


if __name__ == "__main__":
    unittest.main()
