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



class ReportFamilyTest(unittest.TestCase):
    """Exact request shapes for the traffic, audience, key-event, and commerce reports."""

    def setUp(self):
        self.module = load_module()
        self.profile = self.module.Profile("example", "client", "secret", "refresh", "Example")

    def run_report(self, handler, response=None, **overrides):
        args = SimpleNamespace(
            profile="example",
            property="456",
            start_date="28daysAgo",
            end_date="today",
            limit=25,
            json=True,
            scope="session",
            event=None,
            purchased_only=False,
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        captured = {}
        body = {"rowCount": 0, "rows": []} if response is None else response

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            captured.update({"token": token, "method": method, "url": url, "payload": payload})
            return body

        output, errors = io.StringIO(), io.StringIO()
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "api_request", side_effect=fake_request), redirect_stdout(
            output
        ), redirect_stderr(errors):
            handler(args)
        return captured, output.getvalue(), errors.getvalue()

    def test_traffic_sends_the_documented_session_acquisition_request(self):
        captured, _, _ = self.run_report(self.module.command_traffic, breakdown="channel")
        self.assertEqual("POST", captured["method"])
        self.assertTrue(captured["url"].endswith("properties/456:runReport"))
        self.assertEqual(
            {
                "dateRanges": [{"startDate": "28daysAgo", "endDate": "today"}],
                "dimensions": [{"name": "sessionDefaultChannelGroup"}],
                "metrics": [
                    {"name": "sessions"},
                    {"name": "activeUsers"},
                    {"name": "newUsers"},
                    {"name": "engagedSessions"},
                    {"name": "engagementRate"},
                    {"name": "averageEngagementTimePerSession"},
                    {"name": "keyEvents"},
                    {"name": "totalRevenue"},
                ],
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                "limit": "25",
            },
            captured["payload"],
        )

    def test_traffic_scope_selects_first_user_attribution_names(self):
        captured, _, _ = self.run_report(self.module.command_traffic, breakdown="source-medium", scope="first-user")
        self.assertEqual(
            [{"name": "firstUserSource"}, {"name": "firstUserMedium"}], captured["payload"]["dimensions"]
        )

    def test_traffic_session_scope_keeps_session_attribution_names(self):
        captured, _, _ = self.run_report(self.module.command_traffic, breakdown="source-medium")
        self.assertEqual([{"name": "sessionSource"}, {"name": "sessionMedium"}], captured["payload"]["dimensions"])

    def test_a_date_breakdown_orders_by_day_rather_than_by_size(self):
        captured, _, _ = self.run_report(self.module.command_traffic, breakdown="date")
        self.assertEqual([{"dimension": {"dimensionName": "date"}, "desc": False}], captured["payload"]["orderBys"])

    def test_traffic_refuses_a_scope_the_breakdown_does_not_have(self):
        for breakdown in ("landing-page", "date"):
            with self.subTest(breakdown=breakdown):
                with self.assertRaisesRegex(self.module.AnalyticsError, "has no first-user form"):
                    self.run_report(self.module.command_traffic, breakdown=breakdown, scope="first-user")

    def test_every_offered_breakdown_resolves_to_official_field_names(self):
        cases = (
            (self.module.TRAFFIC_BREAKDOWN_CHOICES, self.module.command_traffic, {}),
            (self.module.AUDIENCE_BREAKDOWN_CHOICES, self.module.command_audience, {}),
            (self.module.KEY_EVENT_BREAKDOWN_CHOICES, self.module.command_key_events, {}),
            (self.module.COMMERCE_BREAKDOWN_CHOICES, self.module.command_commerce, {}),
        )
        for choices, handler, extra in cases:
            for breakdown in choices:
                with self.subTest(handler=handler.__name__, breakdown=breakdown):
                    captured, _, _ = self.run_report(handler, breakdown=breakdown, **extra)
                    payload = captured["payload"]
                    names = [item["name"] for item in payload["dimensions"] + payload["metrics"]]
                    self.assertTrue(names)
                    for name in names:
                        self.assertRegex(name, r"^[A-Za-z][A-Za-z0-9_]*$")
                    self.assertLessEqual(len(payload["dimensions"]), self.module.MAX_DIMENSIONS)
                    self.assertLessEqual(len(payload["metrics"]), self.module.MAX_METRICS)
                    self.assertEqual("25", payload["limit"])

    def test_traffic_table_covers_each_offered_choice_and_nothing_else(self):
        offered = {(breakdown, "session") for breakdown in self.module.TRAFFIC_BREAKDOWN_CHOICES}
        self.assertTrue(offered.issubset(set(self.module.TRAFFIC_DIMENSIONS)))
        for breakdown, scope in self.module.TRAFFIC_DIMENSIONS:
            self.assertIn(breakdown, self.module.TRAFFIC_BREAKDOWN_CHOICES)
            self.assertIn(scope, self.module.TRAFFIC_SCOPE_CHOICES)

    def test_audience_reports_the_metric_set_google_publishes_for_demographics(self):
        captured, _, _ = self.run_report(self.module.command_audience, breakdown="age")
        self.assertEqual(
            [
                {"name": "activeUsers"},
                {"name": "newUsers"},
                {"name": "engagedSessions"},
                {"name": "engagementRate"},
                {"name": "eventCount"},
                {"name": "keyEvents"},
                {"name": "totalRevenue"},
            ],
            captured["payload"]["metrics"],
        )
        # A user-scoped breakdown must not request a session-count metric Google does not
        # pair with it in its own demographic report.
        self.assertNotIn("sessions", self.module.AUDIENCE_METRICS)

    def test_audience_breakdowns_use_the_documented_demographic_dimensions(self):
        expected = {
            "audience": "audienceName",
            "country": "country",
            "region": "region",
            "city": "city",
            "language": "language",
            "device": "deviceCategory",
            "browser": "browser",
            "operating-system": "operatingSystem",
            "platform": "platform",
            "age": "userAgeBracket",
            "gender": "userGender",
        }
        for breakdown, dimension in expected.items():
            with self.subTest(breakdown=breakdown):
                captured, _, _ = self.run_report(self.module.command_audience, breakdown=breakdown)
                self.assertEqual([{"name": dimension}], captured["payload"]["dimensions"])
                self.assertNotIn("dimensionFilter", captured["payload"])
                self.assertNotIn("metricFilter", captured["payload"])

    def test_audience_warns_that_age_and_gender_are_thresholded(self):
        for breakdown in ("age", "gender"):
            with self.subTest(breakdown=breakdown):
                _, _, errors = self.run_report(self.module.command_audience, breakdown=breakdown)
                self.assertIn("aggregation thresholds", errors)
        _, _, errors = self.run_report(self.module.command_audience, breakdown="country")
        self.assertNotIn("aggregation thresholds", errors)

    def test_key_events_always_isolates_key_events(self):
        captured, _, _ = self.run_report(self.module.command_key_events, breakdown="event")
        self.assertEqual(
            {"filter": {"fieldName": "isKeyEvent", "stringFilter": {"matchType": "EXACT", "value": "true"}}},
            captured["payload"]["dimensionFilter"],
        )
        self.assertEqual([{"name": "eventName"}], captured["payload"]["dimensions"])
        self.assertEqual(
            [{"name": "keyEvents"}, {"name": "eventCount"}, {"name": "activeUsers"}, {"name": "totalRevenue"}],
            captured["payload"]["metrics"],
        )

    def test_named_events_are_matched_exactly_inside_the_key_event_filter(self):
        captured, _, _ = self.run_report(
            self.module.command_key_events, breakdown="channel", event="generate_lead, purchase"
        )
        self.assertEqual(
            {
                "andGroup": {
                    "expressions": [
                        {
                            "filter": {
                                "fieldName": "isKeyEvent",
                                "stringFilter": {"matchType": "EXACT", "value": "true"},
                            }
                        },
                        {
                            "filter": {
                                "fieldName": "eventName",
                                "inListFilter": {
                                    "values": ["generate_lead", "purchase"],
                                    "caseSensitive": True,
                                },
                            }
                        },
                    ]
                }
            },
            captured["payload"]["dimensionFilter"],
        )

    def test_invalid_or_unbounded_event_names_are_refused(self):
        for value in ("", ",", " , "):
            with self.subTest(event=value):
                with self.assertRaisesRegex(self.module.AnalyticsError, "at least one GA4 event name"):
                    self.run_report(self.module.command_key_events, breakdown="event", event=value)
        for value in ("1lead", "generate lead", "generate-lead", "a" * 41, "drop table"):
            with self.subTest(event=value):
                with self.assertRaisesRegex(self.module.AnalyticsError, "Invalid GA4 event name"):
                    self.run_report(self.module.command_key_events, breakdown="event", event=value)
        crowd = ",".join(f"event_{index}" for index in range(self.module.MAX_EVENT_FILTER_VALUES + 1))
        with self.assertRaisesRegex(self.module.AnalyticsError, "at most"):
            self.run_report(self.module.command_key_events, breakdown="event", event=crowd)

    def test_commerce_item_breakdowns_use_item_scoped_metrics(self):
        for breakdown, dimension in (
            ("item", "itemName"),
            ("item-id", "itemId"),
            ("brand", "itemBrand"),
            ("category", "itemCategory"),
            ("list", "itemListName"),
        ):
            with self.subTest(breakdown=breakdown):
                captured, _, _ = self.run_report(self.module.command_commerce, breakdown=breakdown)
                self.assertEqual([{"name": dimension}], captured["payload"]["dimensions"])
                self.assertEqual(
                    [
                        {"name": "itemsViewed"},
                        {"name": "itemsAddedToCart"},
                        {"name": "itemsCheckedOut"},
                        {"name": "itemsPurchased"},
                        {"name": "itemRevenue"},
                    ],
                    captured["payload"]["metrics"],
                )
                self.assertEqual(
                    [{"metric": {"metricName": "itemRevenue"}, "desc": True}], captured["payload"]["orderBys"]
                )

    def test_commerce_purchase_breakdowns_use_purchase_scoped_metrics(self):
        for breakdown, dimension in (("date", "date"), ("channel", "sessionDefaultChannelGroup")):
            with self.subTest(breakdown=breakdown):
                captured, _, _ = self.run_report(self.module.command_commerce, breakdown=breakdown)
                self.assertEqual([{"name": dimension}], captured["payload"]["dimensions"])
                self.assertEqual(
                    [{"name": "ecommercePurchases"}, {"name": "purchaseRevenue"}, {"name": "totalRevenue"}],
                    captured["payload"]["metrics"],
                )

    def test_purchased_only_bounds_the_metric_that_the_breakdown_measures(self):
        captured, _, _ = self.run_report(self.module.command_commerce, breakdown="item", purchased_only=True)
        self.assertEqual(
            {
                "filter": {
                    "fieldName": "itemsPurchased",
                    "numericFilter": {"operation": "GREATER_THAN", "value": {"int64Value": "0"}},
                }
            },
            captured["payload"]["metricFilter"],
        )
        captured, _, _ = self.run_report(self.module.command_commerce, breakdown="date", purchased_only=True)
        self.assertEqual("ecommercePurchases", captured["payload"]["metricFilter"]["filter"]["fieldName"])

    def test_commerce_sends_no_filter_unless_it_was_asked_for(self):
        captured, _, _ = self.run_report(self.module.command_commerce, breakdown="item")
        self.assertNotIn("metricFilter", captured["payload"])
        self.assertNotIn("dimensionFilter", captured["payload"])

    def test_new_commands_reject_unbounded_limits_and_bad_properties(self):
        for limit in (0, -1, 10001):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(self.module.AnalyticsError, "--limit must be between"):
                    self.run_report(self.module.command_traffic, breakdown="channel", limit=limit)
        with self.assertRaisesRegex(self.module.AnalyticsError, "numeric propertie?s? ID|numeric property ID"):
            self.run_report(self.module.command_audience, breakdown="country", property="not-an-id")

    def test_only_documented_date_forms_are_accepted(self):
        for value in ("2026-08-17", "today", "yesterday", "28daysAgo", "0daysAgo"):
            with self.subTest(value=value):
                self.assertEqual(value, self.module.bounded_date(value, "--start-date"))
        for value in ("", "2026/08/17", "28 days ago", "lastMonth", "today OR 1=1", "2026-08-17\n"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(self.module.AnalyticsError, "--start-date must be"):
                    self.module.bounded_date(value, "--start-date")
        with self.assertRaisesRegex(self.module.AnalyticsError, "--end-date must be"):
            self.run_report(self.module.command_traffic, breakdown="channel", end_date="whenever")

    def test_package_defined_fields_pass_the_same_validation_as_caller_fields(self):
        self.module.validated_fields(["date"], ["sessions"])
        with self.assertRaisesRegex(self.module.AnalyticsError, "At least one metric"):
            self.module.validated_fields(["date"], [])
        with self.assertRaisesRegex(self.module.AnalyticsError, "at most 9 dimensions"):
            self.module.validated_fields([f"dimension{index}" for index in range(10)], ["sessions"])
        with self.assertRaisesRegex(self.module.AnalyticsError, "Invalid Analytics field name"):
            self.module.validated_fields(["date;drop"], ["sessions"])

    def test_google_caveats_reach_the_operator(self):
        response = {
            "rowCount": 0,
            "rows": [],
            "metadata": {
                "subjectToThresholding": True,
                "dataLossFromOtherRow": True,
                "samplingMetadatas": [{"samplesReadCount": "10"}],
                "emptyReason": "No data available",
                "currencyCode": "USD",
            },
        }
        _, _, errors = self.run_report(self.module.command_commerce, breakdown="item", response=response)
        self.assertIn("aggregation thresholds", errors)
        self.assertIn("(other)", errors)
        self.assertIn("sampled", errors)
        self.assertIn("No data available", errors)
        self.assertIn("Revenue is reported in USD", errors)

    def test_currency_is_only_reported_next_to_revenue(self):
        response = {"rowCount": 0, "rows": [], "metadata": {"currencyCode": "USD"}}
        _, _, errors = self.run_report(self.module.command_key_events, breakdown="event", response=response)
        self.assertIn("totalRevenue", self.module.KEY_EVENT_METRICS)
        self.assertIn("USD", errors)
        _, _, errors = self.run_report(
            self.module.command_audience, breakdown="country", response={"rowCount": 0, "rows": [], "metadata": {}}
        )
        self.assertNotIn("Revenue is reported", errors)

    def test_malformed_report_metadata_is_refused(self):
        for metadata, expected in (
            ([], "malformed report metadata"),
            ({"samplingMetadatas": {"samplesReadCount": "10"}}, "sampling metadata collection"),
            ({"samplingMetadatas": ["10"]}, "malformed sampling metadata"),
        ):
            with self.subTest(metadata=metadata):
                response = {"rowCount": 0, "rows": [], "metadata": metadata}
                with self.assertRaisesRegex(self.module.AnalyticsError, expected):
                    self.run_report(self.module.command_traffic, breakdown="channel", response=response)

    def test_malformed_rows_are_refused_by_the_new_commands(self):
        response = {"rowCount": 1, "rows": [{"dimensionValues": "web", "metricValues": []}]}
        with self.assertRaisesRegex(self.module.AnalyticsError, "report dimension value collection"):
            self.run_report(self.module.command_audience, breakdown="device", response=response)

    def test_rows_are_normalized_and_truncation_is_reported(self):
        response = {
            "rowCount": 99,
            "rows": [
                {
                    "dimensionValues": [{"value": "Organic Search"}],
                    "metricValues": [
                        {"value": "120"},
                        {"value": "88"},
                        {"value": "31"},
                        {"value": "77"},
                        {"value": "0.64"},
                        {"value": "51.2"},
                        {"value": "4"},
                        {"value": "930.5"},
                    ],
                }
            ],
        }
        captured, output, errors = self.run_report(
            self.module.command_traffic, breakdown="channel", response=response
        )
        row = json.loads(output)[0]
        self.assertEqual("Organic Search", row["sessionDefaultChannelGroup"])
        self.assertEqual("120", row["sessions"])
        self.assertEqual("930.5", row["totalRevenue"])
        self.assertEqual("456", row["property_id"])
        self.assertEqual("example", row["profile"])
        self.assertIn("truncated", errors)

    def test_csv_output_leads_with_the_requested_dimensions(self):
        _, output, _ = self.run_report(self.module.command_commerce, breakdown="brand", json=False)
        self.assertEqual(
            "itemBrand,itemsViewed,itemsAddedToCart,itemsCheckedOut,itemsPurchased,itemRevenue,profile,property_id",
            output.splitlines()[0],
        )

    def test_existing_report_and_realtime_requests_are_unchanged(self):
        captured = {}

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            captured.update({"url": url, "payload": payload})
            return {"rowCount": 0, "rows": []}

        report_args = SimpleNamespace(
            profile="example", property="456", start_date="28daysAgo", end_date="today",
            metrics="sessions,activeUsers", dimensions="date", limit=100, json=True,
        )
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "api_request", side_effect=fake_request), redirect_stdout(io.StringIO()):
            self.module.command_report(report_args)
        self.assertEqual(
            {
                "dateRanges": [{"startDate": "28daysAgo", "endDate": "today"}],
                "metrics": [{"name": "sessions"}, {"name": "activeUsers"}],
                "limit": "100",
                "dimensions": [{"name": "date"}],
            },
            captured["payload"],
        )
        # The historical report still accepts any Google date form and adds no ordering or filter.
        report_args.start_date = "2026-01-01"
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "api_request", side_effect=fake_request), redirect_stdout(io.StringIO()):
            self.module.command_report(report_args)
        self.assertNotIn("orderBys", captured["payload"])

        realtime_args = SimpleNamespace(
            profile="example", property="456", metrics="activeUsers", dimensions="", limit=25, json=True
        )
        with patch.object(self.module, "get_profile", return_value=self.profile), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(self.module, "api_request", side_effect=fake_request), redirect_stdout(io.StringIO()):
            self.module.command_realtime(realtime_args)
        self.assertTrue(captured["url"].endswith("properties/456:runRealtimeReport"))
        self.assertEqual({"metrics": [{"name": "activeUsers"}], "limit": "25"}, captured["payload"])

    def test_new_commands_never_disclose_credentials_on_failure(self):
        def forbidden():
            # A fresh body per call because urllib reads an HTTPError's stream only once.
            return urllib.error.HTTPError(
                "https://analyticsdata.googleapis.com/v1beta/properties/456:runReport",
                403, "Forbidden", {}, io.BytesIO(b'{"error":{"message":"User does not have access"}}'),
            )

        env = {
            "GOOGLE_ANALYTICS_CLIENT_ID": "client",
            "GOOGLE_ANALYTICS_CLIENT_SECRET": "top-secret-value",
            "GOOGLE_ANALYTICS_REFRESH_TOKEN": "top-secret-refresh",
        }
        for argv in (
            ["traffic", "--property", "456"],
            ["audience", "--property", "456", "--breakdown", "age"],
            ["key-events", "--property", "456", "--event", "generate_lead"],
            ["commerce", "--property", "456", "--purchased-only"],
        ):
            with self.subTest(argv=argv[0]):
                with patch.dict(os.environ, env, clear=True), patch.object(
                    self.module, "refresh_access_token", return_value="sensitive-access-token"
                ), patch.object(self.module, "open_url", side_effect=forbidden()), redirect_stdout(
                    io.StringIO()
                ), redirect_stderr(io.StringIO()) as errors:
                    code = self.module.main(argv + ["--limit", "5"])
                message = errors.getvalue()
                self.assertEqual(2, code)
                self.assertIn("User does not have access", message)
                for secret in ("top-secret-value", "top-secret-refresh", "sensitive-access-token", "Bearer"):
                    self.assertNotIn(secret, message)
                self.assertNotIn("Traceback", message)

    def test_new_commands_exit_two_on_a_malformed_body(self):
        env = {
            "GOOGLE_ANALYTICS_CLIENT_ID": "client",
            "GOOGLE_ANALYTICS_CLIENT_SECRET": "secret",
            "GOOGLE_ANALYTICS_REFRESH_TOKEN": "refresh",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "refresh_access_token", return_value="token"
        ), patch.object(
            self.module, "open_url", return_value=RawResponse(b"<html>not json</html>")
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            code = self.module.main(["commerce", "--property", "456"])
        self.assertEqual(2, code)
        self.assertIn("not valid JSON", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_new_subcommands_help_without_credentials(self):
        for command in ("traffic", "audience", "key-events", "commerce"):
            with self.subTest(command=command):
                completed = subprocess.run(
                    [str(LAUNCHER), command, "--help"], cwd="/tmp",
                    env={"PATH": os.environ.get("PATH", "")}, text=True, capture_output=True, check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("--breakdown", completed.stdout)

    def test_unknown_breakdown_values_are_rejected_by_the_parser(self):
        parser = self.module.build_parser()
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser.parse_args(["traffic", "--property", "456", "--breakdown", "cohort"])

if __name__ == "__main__":
    unittest.main()
