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
import datetime as dt
import unittest
import urllib.error
import zoneinfo
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


class RawResponse(Response):
    """A response body Google never should have sent, kept exactly as received."""

    def read(self):
        return self.payload


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
            with self.assertRaisesRegex(self.module.SearchConsoleError, "both Rundesk suffix and legacy"):
                self.module.get_profile("example")

    def test_missing_legacy_credential_uses_the_legacy_variable_name(self):
        env = {
            "GOOGLE_SEARCH_CONSOLE_EXAMPLE_CLIENT_ID": "legacy-client",
            "GOOGLE_SEARCH_CONSOLE_EXAMPLE_CLIENT_SECRET": "legacy-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.SearchConsoleError) as raised:
                self.module.get_profile("example")
        self.assertIn("GOOGLE_SEARCH_CONSOLE_EXAMPLE_REFRESH_TOKEN", str(raised.exception))
        self.assertNotIn("REFRESH_TOKEN__EXAMPLE", str(raised.exception))

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
            dimension=["query", "page"], search_type="web", filter=[], limit=10, json=True,
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
            dimension=["query"], search_type=None, filter=[], limit=1, json=False,
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

    def test_default_range_uses_complete_pacific_days_not_utc_days(self):
        # Google buckets Search Console rows by Pacific day. Late UTC evening is still the prior
        # Pacific day, so a UTC clock would report one day too many as complete.
        cases = (
            ("2026-08-17T06:59:59+00:00", "2026-07-19", "2026-08-15"),
            ("2026-08-17T07:00:00+00:00", "2026-07-20", "2026-08-16"),
            ("2026-01-05T07:59:59+00:00", "2025-12-07", "2026-01-03"),
            ("2026-01-05T08:00:00+00:00", "2025-12-08", "2026-01-04"),
        )
        args = SimpleNamespace(days=28, start_date=None, end_date=None)
        for frozen, start, end in cases:
            with self.subTest(now=frozen):
                with patch.object(
                    self.module, "utc_now", return_value=dt.datetime.fromisoformat(frozen)
                ):
                    self.assertEqual((start, end), self.module.date_range(args))

    def test_pacific_fallback_tracks_the_iana_zone_across_dst_transitions(self):
        try:
            iana = zoneinfo.ZoneInfo(self.module.PACIFIC_ZONE)
        except zoneinfo.ZoneInfoNotFoundError:  # pragma: no cover - depends on the host database
            self.skipTest("no IANA time zone database is installed")
        fallback = self.module.PacificFallback()
        moment = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        limit = dt.datetime(2028, 1, 1, tzinfo=dt.timezone.utc)
        while moment < limit:
            expected = moment.astimezone(iana)
            actual = moment.astimezone(fallback)
            self.assertEqual(expected.replace(tzinfo=None), actual.replace(tzinfo=None), moment)
            self.assertEqual(expected.date(), actual.date(), moment)
            moment += dt.timedelta(hours=1)

    def test_malformed_and_non_object_payloads_are_refused(self):
        cases = (
            (b"<html>not json</html>", "invalid JSON"),
            (b"[]", "malformed API response"),
            (b'"text"', "malformed API response"),
            (b"7", "malformed API response"),
            (b"null", "malformed API response"),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                with self.assertRaisesRegex(self.module.SearchConsoleError, expected):
                    self.module.request_json(
                        "https://www.googleapis.com/example",
                        opener=lambda *args, **kwargs: RawResponse(body),
                    )

    def test_token_refresh_refuses_non_object_and_non_string_tokens(self):
        with self.assertRaisesRegex(self.module.SearchConsoleError, "malformed OAuth token response"):
            self.module.access_token(self.profile, opener=lambda *a, **k: RawResponse(b"[]"))
        with self.assertRaisesRegex(self.module.SearchConsoleError, "no access token"):
            self.module.access_token(
                self.profile, opener=lambda *a, **k: Response({"access_token": {"value": "x"}})
            )

    def test_commands_refuse_wrong_collection_and_object_shapes(self):
        sites = SimpleNamespace(profile="example", limit=5, json=False)
        performance = SimpleNamespace(
            profile="example", site="sc-domain:example.test", days=28, start_date=None,
            end_date=None, dimension=["query"], search_type=None, filter=[], limit=5, json=False,
        )
        sitemaps = SimpleNamespace(profile="example", site="sc-domain:example.test", limit=5, json=False)
        cases = (
            (self.module.cmd_sites, sites, {"siteEntry": {"siteUrl": "sc-domain:example.test"}}, "site entry collection"),
            (self.module.cmd_sites, sites, {"siteEntry": ["sc-domain:example.test"]}, "malformed site entry"),
            (self.module.cmd_performance, performance, {"rows": {"keys": []}}, "performance row collection"),
            (self.module.cmd_performance, performance, {"rows": ["example"]}, "malformed performance row"),
            (self.module.cmd_performance, performance, {"rows": [{"keys": "example"}]}, "performance row key collection"),
            (self.module.cmd_sitemaps, sitemaps, {"sitemap": {"path": "x"}}, "sitemap entry collection"),
            (self.module.cmd_sitemaps, sitemaps, {"sitemap": [7]}, "malformed sitemap entry"),
        )
        for command, args, payload, expected in cases:
            with self.subTest(command=command.__name__, payload=payload):
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "api", return_value=payload
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(self.module.SearchConsoleError, expected):
                        command(args)

    def test_malformed_response_exits_two_instead_of_raising(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.module, "access_token", return_value="token"
        ), patch.object(
            self.module, "open_url", side_effect=lambda *a, **k: RawResponse(b"<html>not json</html>")
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["sites", "--profile", "example"])
        self.assertEqual(2, code)
        self.assertIn("invalid JSON", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_main_rejects_unbounded_limit_before_network(self):
        with patch.dict(os.environ, self.env, clear=True), redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["sites", "--profile", "example", "--limit", "1001"])
        self.assertEqual(code, 2)
        self.assertIn("between 1 and 1000", error.getvalue())

    def performance_args(self, **overrides):
        values = dict(
            profile="example", site="https://www.example.test/", days=28, start_date="2026-07-01",
            end_date="2026-07-31", dimension=["query"], search_type=None, filter=[], limit=25, json=True,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def performance_body(self, args, payload=None):
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", return_value=payload if payload is not None else {"rows": []}
        ) as call, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.module.cmd_performance(args)
        return call.call_args.kwargs["body"]

    def submit_args(self, **overrides):
        values = dict(
            profile="example", site="https://www.example.test/",
            sitemap="https://www.example.test/sitemap.xml", confirm=False, json=False,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_unfiltered_performance_body_is_exactly_what_it_was_before_filters(self):
        body = self.performance_body(self.performance_args())
        self.assertEqual(
            {"startDate": "2026-07-01", "endDate": "2026-07-31", "rowLimit": 25, "startRow": 0,
             "dimensions": ["query"]},
            body,
        )
        self.assertNotIn("dimensionFilterGroups", body)

    def test_filters_build_one_official_and_group_in_argument_order(self):
        args = self.performance_args(filter=[
            "query:contains:running shoes",
            "page:equals:https://www.example.test/a:b?x=1&y=2",
            "country:equals:USA",
            "device:equals:mobile",
            "searchAppearance:equals:AMP_BLUE_LINK",
        ])
        self.assertEqual(
            [{"groupType": "and", "filters": [
                {"dimension": "query", "operator": "contains", "expression": "running shoes"},
                {"dimension": "page", "operator": "equals",
                 "expression": "https://www.example.test/a:b?x=1&y=2"},
                {"dimension": "country", "operator": "equals", "expression": "usa"},
                {"dimension": "device", "operator": "equals", "expression": "MOBILE"},
                {"dimension": "searchAppearance", "operator": "equals", "expression": "AMP_BLUE_LINK"},
            ]}],
            self.performance_body(args)["dimensionFilterGroups"],
        )

    def test_substring_and_regex_expressions_are_sent_exactly_as_typed(self):
        args = self.performance_args(filter=[
            "country:contains:US", "device:includingRegex:^MOB", "searchAppearance:contains:amp",
            "query:excludingRegex:(?-i)Brand",
        ])
        filters = self.performance_body(args)["dimensionFilterGroups"][0]["filters"]
        self.assertEqual(
            ["US", "^MOB", "amp", "(?-i)Brand"], [item["expression"] for item in filters]
        )

    def test_filtered_performance_request_reaches_google_as_declared_json(self):
        sent = []

        def opener(request, timeout):
            sent.append(request)
            return Response({"rows": []})

        args = self.performance_args(filter=["page:includingRegex:^/blog/.*$", "query:contains:caf\u00e9 & 100%"])
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "access_token", return_value="access"
        ), patch.object(self.module, "open_url", side_effect=opener), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(io.StringIO()):
            self.module.cmd_performance(args)
        request = sent[0]
        self.assertEqual("POST", request.get_method())
        self.assertEqual(
            "https://www.googleapis.com/webmasters/v3/sites/"
            "https%3A%2F%2Fwww.example.test%2F/searchAnalytics/query",
            request.full_url,
        )
        self.assertEqual("application/json", request.get_header("Content-type"))
        # Filters travel in the JSON body, so nothing in them may arrive percent-encoded.
        self.assertEqual(
            [{"groupType": "and", "filters": [
                {"dimension": "page", "operator": "includingRegex", "expression": "^/blog/.*$"},
                {"dimension": "query", "operator": "contains", "expression": "caf\u00e9 & 100%"},
            ]}],
            json.loads(request.data.decode("utf-8"))["dimensionFilterGroups"],
        )

    def test_malformed_filters_are_refused_before_configuration_or_network(self):
        cases = (
            ("", "must be DIMENSION:OPERATOR:EXPRESSION"),
            ("query", "must be DIMENSION:OPERATOR:EXPRESSION"),
            ("query:contains", "must be DIMENSION:OPERATOR:EXPRESSION"),
            ("date:equals:2026-08-01", "dimension 'date' must be one of"),
            ("hour:equals:5", "dimension 'hour' must be one of"),
            ("QUERY:equals:shoes", "dimension 'QUERY' must be one of"),
            ("query:Equals:shoes", "operator 'Equals' must be one of"),
            ("query:regex:shoes", "operator 'regex' must be one of"),
            ("query:contains:", "needs a non-empty expression"),
            ("country:equals:US", "alpha-3"),
            ("country:equals:united", "alpha-3"),
            ("country:notEquals:12", "alpha-3"),
            ("device:equals:phone", "must be one of: DESKTOP, MOBILE, TABLET"),
            ("device:notEquals:", "needs a non-empty expression"),
            ("query:contains:" + "x" * 4097, "exceeds 4096 characters"),
        )
        for value, expected in cases:
            with self.subTest(filter=value[:40]):
                with patch.object(
                    self.module, "selected_profile", side_effect=AssertionError("configuration")
                ), patch.object(self.module, "api", side_effect=AssertionError("network")):
                    with self.assertRaisesRegex(self.module.SearchConsoleError, expected):
                        self.module.cmd_performance(self.performance_args(filter=[value]))

    def test_main_rejects_a_malformed_filter_without_contacting_google(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.module.urllib.request, "urlopen", side_effect=AssertionError("network")
        ), redirect_stderr(io.StringIO()) as error:
            code = self.module.main([
                "performance", "--profile", "example", "--site", "sc-domain:example.test",
                "--filter", "query:like:shoes",
            ])
        self.assertEqual(2, code)
        self.assertIn("operator 'like'", error.getvalue())

    def test_submit_sitemap_previews_without_reaching_google_and_refuses(self):
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", side_effect=AssertionError("network")
        ), patch.object(
            self.module, "access_token", side_effect=AssertionError("network")
        ), patch.object(
            self.module, "open_url", side_effect=AssertionError("network")
        ), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(self.module.SearchConsoleError, "without --confirm"):
                self.module.cmd_submit_sitemap(self.submit_args())
        printed = output.getvalue()
        self.assertIn("preview", printed)
        self.assertIn("PUT", printed)
        self.assertIn(
            "https://www.googleapis.com/webmasters/v3/sites/https%3A%2F%2Fwww.example.test%2F"
            "/sitemaps/https%3A%2F%2Fwww.example.test%2Fsitemap.xml",
            printed,
        )
        self.assertIn("https://www.googleapis.com/auth/webmasters", printed)

    def test_submit_sitemap_preview_exits_two_in_both_output_modes(self):
        for extra in ([], ["--json"]):
            with self.subTest(json=bool(extra)):
                with patch.dict(os.environ, self.env, clear=True), patch.object(
                    self.module.urllib.request, "urlopen", side_effect=AssertionError("network")
                ), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
                    code = self.module.main([
                        "submit-sitemap", "--profile", "example", "--site", "https://www.example.test/",
                        "--sitemap", "https://www.example.test/sitemap.xml", *extra,
                    ])
                self.assertEqual(2, code)
                self.assertIn("Refusing to submit", error.getvalue())
                if extra:
                    self.assertEqual("preview", json.loads(output.getvalue())[0]["state"])
                else:
                    self.assertIn("preview", output.getvalue())

    def test_submit_sitemap_puts_the_official_path_then_verifies_by_reading_it_back(self):
        entry = {
            "path": "https://www.example.test/sitemap.xml", "type": "sitemap", "isPending": True,
            "lastSubmitted": "2026-08-17T00:00:00.000Z", "warnings": 0, "errors": 0,
        }
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", side_effect=[{}, entry]
        ) as call, redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.module.cmd_submit_sitemap(self.submit_args(confirm=True, json=True))
        expected = ("/sites/https%3A%2F%2Fwww.example.test%2F"
                    "/sitemaps/https%3A%2F%2Fwww.example.test%2Fsitemap.xml")
        submit, verify = call.call_args_list
        self.assertEqual((self.profile, expected), submit.args)
        self.assertEqual({"method": "PUT"}, submit.kwargs)
        self.assertEqual((self.profile, expected), verify.args)
        self.assertEqual({}, verify.kwargs)
        row = json.loads(output.getvalue())[0]
        self.assertEqual("submitted", row["state"])
        self.assertEqual("https://www.example.test/sitemap.xml", row["path"])
        self.assertTrue(row["pending"])

    def test_submit_sitemap_refuses_to_report_success_it_cannot_verify(self):
        for payload in ({}, {"path": ""}, {"path": 7}, {"errors": 0}):
            with self.subTest(payload=payload):
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "api", side_effect=[{}, payload]
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(
                        self.module.SearchConsoleError, "did not return the sitemap"
                    ):
                        self.module.cmd_submit_sitemap(self.submit_args(confirm=True))

    def test_submit_sitemap_refuses_a_malformed_verification_response(self):
        for payload in ([], "ok", 7, None):
            with self.subTest(payload=payload):
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "api", side_effect=[{}, payload]
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(
                        self.module.SearchConsoleError, "malformed sitemap entry"
                    ):
                        self.module.cmd_submit_sitemap(self.submit_args(confirm=True))

    def test_submit_sitemap_reports_a_path_google_rewrote(self):
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "api", side_effect=[{}, {"path": "https://www.example.test/sitemap_index.xml"}]
        ), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            self.module.cmd_submit_sitemap(self.submit_args(confirm=True))
        self.assertIn("recorded the sitemap as https://www.example.test/sitemap_index.xml", error.getvalue())
        self.assertIn("sitemap_index.xml", output.getvalue())

    def test_submit_sitemap_refuses_anything_that_is_not_an_absolute_web_url(self):
        cases = ("", "sitemap.xml", "/sitemap.xml", "//example.test/sitemap.xml",
                 "ftp://example.test/sitemap.xml", "file:///etc/passwd", "javascript:alert(1)",
                 "https:///sitemap.xml", "https://user@example.test/sitemap.xml",
                 "https://user:password@example.test/sitemap.xml")
        for value in cases:
            with self.subTest(sitemap=value):
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "api", side_effect=AssertionError("network")
                ), redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(
                        self.module.SearchConsoleError, "absolute http or https URL without credentials"
                    ):
                        self.module.cmd_submit_sitemap(self.submit_args(sitemap=value, confirm=True))

    def test_submit_sitemap_warns_only_when_a_url_prefix_property_cannot_contain_it(self):
        cases = (
            ("https://www.example.test/", "https://other.test/sitemap.xml", True),
            ("https://www.example.test/", "https://www.example.test/a/sitemap.xml", False),
            ("sc-domain:example.test", "https://blog.example.test/sitemap.xml", False),
        )
        for site, sitemap, warns in cases:
            with self.subTest(site=site, sitemap=sitemap):
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "api", side_effect=[{}, {"path": sitemap}]
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
                    self.module.cmd_submit_sitemap(
                        self.submit_args(site=site, sitemap=sitemap, confirm=True)
                    )
                self.assertEqual(warns, "outside the property" in error.getvalue())

    def test_writing_and_filtering_paths_never_print_credentials_or_tokens(self):
        env = {
            "GOOGLE_SEARCH_CONSOLE_CLIENT_ID__EXAMPLE": "leak-client-id",
            "GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET__EXAMPLE": "leak-client-secret",
            "GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN__EXAMPLE": "leak-refresh-token",
        }
        secrets = ("leak-client-id", "leak-client-secret", "leak-refresh-token",
                   "leak-access-token", "Bearer", "Authorization")

        def opener(request, timeout):
            if request.full_url == self.module.TOKEN_URL:
                return Response({"access_token": "leak-access-token"})
            if request.get_method() == "PUT":
                return RawResponse(b"")
            if "/sitemaps/" in request.full_url:
                return Response({"path": "https://www.example.test/sitemap.xml"})
            return Response({"rows": [{"keys": ["shoes"], "clicks": 1}]})

        base = ["--profile", "example", "--site", "https://www.example.test/"]
        sitemap = ["--sitemap", "https://www.example.test/sitemap.xml"]
        commands = (
            ["submit-sitemap", *base, *sitemap],
            ["submit-sitemap", *base, *sitemap, "--confirm"],
            ["submit-sitemap", *base, *sitemap, "--confirm", "--json"],
            ["performance", *base, "--dimension", "query", "--filter", "query:contains:shoes"],
        )
        for argv in commands:
            with self.subTest(command=" ".join(argv[:1] + argv[5:])):
                with patch.dict(os.environ, env, clear=True), patch.object(
                    self.module, "open_url", side_effect=opener
                ), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
                    self.module.main(argv)
                printed = output.getvalue() + error.getvalue()
                for secret in secrets:
                    self.assertNotIn(secret, printed)

    def test_launcher_help_is_credential_free_and_resolves_outside_repo(self):
        result = subprocess.run(
            [str(LAUNCHER), "--help"], cwd="/tmp", env={"PATH": os.environ.get("PATH", "")},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Google Search Console", result.stdout)


if __name__ == "__main__":
    unittest.main()
