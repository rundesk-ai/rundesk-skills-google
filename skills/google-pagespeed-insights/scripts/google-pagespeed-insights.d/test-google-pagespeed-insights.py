#!/usr/bin/env python3
"""Offline tests for google-pagespeed-insights."""

from __future__ import annotations

import importlib.util
import io
import json
import math
import os
import socket
import subprocess
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "google-pagespeed-insights.py"
LAUNCHER = HERE.parent / "google-pagespeed-insights"


def load_module():
    spec = importlib.util.spec_from_file_location("google_pagespeed_insights_module", SCRIPT)
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


NAN = float("nan")
INFINITY = float("inf")


def lighthouse(categories=None, audits=None, **extra):
    result = {"categories": categories if categories is not None else {}, "audits": audits if audits is not None else {}}
    result.update(extra)
    return {"lighthouseResult": result}


class PageSpeedTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.env = {
            "GOOGLE_PAGESPEED_INSIGHTS_API_KEY__EXAMPLE": "secret-key",
            "GOOGLE_PAGESPEED_INSIGHTS_LABEL__EXAMPLE": "Example PageSpeed",
        }
        self.profile = self.module.Profile("example", "secret-key", "Example PageSpeed")

    def test_profiles_discovers_named_profile_without_network(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.module.urllib.request, "urlopen", side_effect=AssertionError("network")
        ), redirect_stdout(io.StringIO()) as output:
            code = self.module.main(["profiles"])
        self.assertEqual(code, 0)
        self.assertIn("example,Example PageSpeed,ready", output.getvalue())

    def test_named_profile_never_falls_back_to_plain_key(self):
        env = {"GOOGLE_PAGESPEED_INSIGHTS_API_KEY": "plain", "GOOGLE_PAGESPEED_INSIGHTS_PROFILES": "example"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.PageSpeedError) as raised:
                self.module.get_profile("example")
        self.assertIn("API_KEY__EXAMPLE", str(raised.exception))
        self.assertNotIn("plain", str(raised.exception))

    def test_api_key_is_not_in_profile_representation(self):
        self.assertNotIn("secret-key", repr(self.profile))

    def test_request_encodes_parameters_and_does_not_expose_key_on_error(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response({"lighthouseResult": {}})

        self.module.request_json([("url", "https://example.test/a b"), ("key", "secret-key")], opener)
        self.assertIn("url=https%3A%2F%2Fexample.test%2Fa+b", requests[0].full_url)
        error = urllib.error.HTTPError(
            "https://example", 403, "Forbidden", {},
            io.BytesIO(json.dumps({"error": {"message": "API disabled for secret-key"}}).encode()),
        )
        with self.assertRaises(self.module.PageSpeedError) as raised:
            self.module.request_json(
                [("key", "secret-key")],
                opener=lambda *args, **kwargs: (_ for _ in ()).throw(error),
            )
        self.assertIn("API disabled", str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))
        self.assertNotIn("secret-key", str(raised.exception))

    def test_analyze_normalizes_scores_metrics_and_bounded_audits(self):
        args = SimpleNamespace(profile="example", url="https://example.test/", strategy="mobile", category=["performance", "seo"], audit_limit=1, json=True)
        payload = {"lighthouseResult": {
            "requestedUrl": args.url, "finalUrl": "https://www.example.test/", "fetchTime": "2026-08-17T12:00:00Z", "lighthouseVersion": "13.0.0",
            "categories": {
                "performance": {"score": 0.82, "auditRefs": [{"id": "render-blocking-resources", "weight": 5}, {"id": "uses-long-cache-ttl", "weight": 1}]},
                "seo": {"score": 0.95, "auditRefs": []},
            },
            "audits": {
                "largest-contentful-paint": {"score": 0.8, "numericValue": 2400, "displayValue": "2.4 s", "title": "Largest Contentful Paint"},
                "render-blocking-resources": {"score": 0.2, "title": "Eliminate render-blocking resources", "displayValue": "1.2 s"},
                "uses-long-cache-ttl": {"score": 0.4, "title": "Use efficient cache lifetimes"},
            },
        }}
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ) as request, redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            self.module.cmd_analyze(args)
        params = request.call_args.args[0]
        self.assertEqual([value for key, value in params if key == "category"], ["PERFORMANCE", "SEO"])
        self.assertIn(("strategy", "MOBILE"), params)
        self.assertIn(("key", "secret-key"), params)
        rows = json.loads(output.getvalue())
        self.assertEqual([82, 95], [row["score"] for row in rows if row["row_type"] == "summary"])
        self.assertEqual("2.4 s", next(row["value"] for row in rows if row.get("metric") == "largest_contentful_paint"))
        findings = [row for row in rows if row["row_type"] == "audit"]
        self.assertEqual("render-blocking-resources", findings[0]["audit"])
        self.assertIn("truncated", error.getvalue())
        self.assertNotIn("secret-key", output.getvalue() + error.getvalue())

    def test_empty_lighthouse_result_is_refused(self):
        args = SimpleNamespace(profile="example", url="https://example.test/", strategy="mobile", category=None, audit_limit=10, json=True)
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(self.module, "request_json", return_value={}):
            with self.assertRaisesRegex(self.module.PageSpeedError, "no Lighthouse result"):
                self.module.cmd_analyze(args)

    def test_url_validation_rejects_credentials_and_non_http_schemes(self):
        self.assertEqual("https://example.test/", self.module.valid_url("https://example.test/"))
        with self.assertRaises(Exception):
            self.module.valid_url("file:///tmp/page.html")
        with self.assertRaises(Exception):
            self.module.valid_url("https://user:pass@example.test/")

    def test_main_rejects_unbounded_audit_limit_before_network(self):
        with patch.dict(os.environ, self.env, clear=True), redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["analyze", "--profile", "example", "--url", "https://example.test/", "--audit-limit", "51"])
        self.assertEqual(2, code)
        self.assertIn("between 0 and 50", error.getvalue())

    def test_request_uses_the_official_uppercase_discovery_enums(self):
        # https://pagespeedonline.googleapis.com/$discovery/rest?version=v5 defines the query enums
        # as MOBILE/DESKTOP and PERFORMANCE/ACCESSIBILITY/BEST_PRACTICES/SEO.
        self.assertEqual({"mobile": "MOBILE", "desktop": "DESKTOP"}, self.module.STRATEGIES)
        self.assertEqual(
            {"performance": "PERFORMANCE", "accessibility": "ACCESSIBILITY",
             "best-practices": "BEST_PRACTICES", "seo": "SEO"},
            self.module.CATEGORIES,
        )
        for strategy, expected in self.module.STRATEGIES.items():
            with self.subTest(strategy=strategy):
                args = SimpleNamespace(
                    profile="example", url="https://example.test/", strategy=strategy,
                    category=list(self.module.CATEGORIES), audit_limit=10, json=True,
                )
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "request_json", return_value=lighthouse()
                ) as request, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.module.cmd_analyze(args)
                self.assertEqual(
                    [("url", "https://example.test/"), ("strategy", expected),
                     ("category", "PERFORMANCE"), ("category", "ACCESSIBILITY"),
                     ("category", "BEST_PRACTICES"), ("category", "SEO"),
                     ("key", "secret-key")],
                    request.call_args.args[0],
                )

    def test_lowercase_choices_stay_user_facing(self):
        parsed = self.module.parser().parse_args(
            ["analyze", "--url", "https://example.test/", "--strategy", "desktop", "--category", "best-practices"]
        )
        self.assertEqual("desktop", parsed.strategy)
        self.assertEqual(["best-practices"], parsed.category)
        args = SimpleNamespace(
            profile="example", url="https://example.test/", strategy="desktop",
            category=["best-practices"], audit_limit=10, json=True,
        )
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json",
            return_value=lighthouse(categories={"best-practices": {"score": 0.5, "auditRefs": []}}),
        ), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.module.cmd_analyze(args)
        row = json.loads(output.getvalue())[0]
        self.assertEqual("best-practices", row["category"])
        self.assertEqual("desktop", row["strategy"])

    def test_hostile_response_shapes_are_refused(self):
        cases = (
            ({"lighthouseResult": None}, "malformed Lighthouse result"),
            ({"lighthouseResult": []}, "malformed Lighthouse result"),
            ({"lighthouseResult": "x"}, "malformed Lighthouse result"),
            ({}, "no Lighthouse result"),
            (lighthouse(runtimeError=[]), "malformed Lighthouse runtime error"),
            (lighthouse(runtimeError={"code": 7}), "malformed Lighthouse runtime error code"),
            (lighthouse(runtimeError={"message": []}), "malformed Lighthouse runtime error message"),
            ({"lighthouseResult": {"categories": None}}, "malformed Lighthouse categories object"),
            ({"lighthouseResult": {"categories": []}}, "malformed Lighthouse categories object"),
            ({"lighthouseResult": {"audits": None}}, "malformed Lighthouse audits object"),
            ({"lighthouseResult": {"audits": []}}, "malformed Lighthouse audits object"),
            (lighthouse(categories={"performance": None}), "malformed performance category object"),
            (lighthouse(categories={"performance": "x"}), "malformed performance category object"),
            (lighthouse(categories={"performance": {"auditRefs": None}}), "malformed performance audit reference collection"),
            (lighthouse(categories={"performance": {"auditRefs": "x"}}), "malformed performance audit reference collection"),
            (lighthouse(categories={"performance": {"auditRefs": ["x"]}}), "malformed performance audit reference"),
            (lighthouse(categories={"performance": {"auditRefs": [None]}}), "malformed performance audit reference"),
            (lighthouse(categories={"performance": {"auditRefs": [{"id": 7, "weight": 1}]}}), "malformed performance audit reference id"),
            (lighthouse(categories={"performance": {"auditRefs": [{"id": "a", "weight": "heavy"}]}}), "malformed performance audit reference weight"),
            (lighthouse(audits={"a": None}), "malformed a audit object"),
            (lighthouse(audits={"a": "x"}), "malformed a audit object"),
            (lighthouse(audits={"a": {"score": "low"}}), "malformed a audit score"),
            (lighthouse(audits={"a": {"score": 0.1, "title": 7}}), "malformed a audit title"),
            (lighthouse(audits={"largest-contentful-paint": {"numericValue": "fast"}}), "malformed largest-contentful-paint audit numeric value"),
            (lighthouse(categories={"performance": {"score": "great"}}), "malformed performance category score"),
            (lighthouse(requestedUrl=7), "malformed requested URL"),
            (lighthouse(finalUrl=[]), "malformed final URL"),
            (lighthouse(fetchTime=7), "malformed fetch time"),
            (lighthouse(lighthouseVersion={}), "malformed Lighthouse version"),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected, payload=payload):
                args = SimpleNamespace(
                    profile="example", url="https://example.test/", strategy="mobile",
                    category=["performance"], audit_limit=10, json=True,
                )
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "request_json", return_value=payload
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(self.module.PageSpeedError, expected):
                        self.module.cmd_analyze(args)

    def test_lighthouse_runtime_error_exits_two_without_success_output(self):
        payload = lighthouse(runtimeError={
            "code": "ERRORED_DOCUMENT_REQUEST",
            "message": "Lighthouse was unable to reliably load the page.",
        })
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.module, "request_json", return_value=payload
        ), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            code = self.module.main(["analyze", "--profile", "example", "--url", "https://example.test/"])
        self.assertEqual(2, code)
        self.assertEqual("", output.getvalue())
        self.assertIn("ERRORED_DOCUMENT_REQUEST", error.getvalue())
        self.assertIn("unable to reliably load", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_non_finite_values_are_refused_before_rounding_or_emission(self):
        cases = (
            (lighthouse(categories={"performance": {"score": NAN}}), "non-finite performance category score"),
            (lighthouse(categories={"performance": {"score": INFINITY}}), "non-finite performance category score"),
            (lighthouse(categories={"performance": {"score": -INFINITY}}), "non-finite performance category score"),
            (lighthouse(audits={"a": {"score": NAN}}), "non-finite a audit score"),
            (lighthouse(audits={"a": {"score": -INFINITY}}), "non-finite a audit score"),
            (lighthouse(categories={"performance": {"auditRefs": [{"id": "a", "weight": NAN}]}}), "non-finite performance audit reference weight"),
            (lighthouse(categories={"performance": {"auditRefs": [{"id": "a", "weight": INFINITY}]}}), "non-finite performance audit reference weight"),
            (lighthouse(audits={"largest-contentful-paint": {"numericValue": NAN}}), "non-finite largest-contentful-paint audit numeric value"),
            (lighthouse(audits={"largest-contentful-paint": {"numericValue": INFINITY}}), "non-finite largest-contentful-paint audit numeric value"),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                args = SimpleNamespace(
                    profile="example", url="https://example.test/", strategy="mobile",
                    category=["performance"], audit_limit=10, json=True,
                )
                with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
                    self.module, "request_json", return_value=payload
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(self.module.PageSpeedError, expected):
                        self.module.cmd_analyze(args)

    def test_non_finite_json_literals_are_refused_at_parse_time(self):
        for body in (b'{"lighthouseResult": {"categories": {"performance": {"score": NaN}}}}',
                     b'{"lighthouseResult": {"categories": {"performance": {"score": Infinity}}}}',
                     b'{"lighthouseResult": {"categories": {"performance": {"score": -Infinity}}}}'):
            with self.subTest(body=body):
                with self.assertRaisesRegex(self.module.PageSpeedError, "non-finite JSON value"):
                    self.module.request_json([("key", "secret-key")], opener=lambda *a, **k: RawResponse(body))

    def test_json_output_is_standards_safe(self):
        with self.assertRaisesRegex(self.module.PageSpeedError, "non-finite value as JSON"):
            with redirect_stdout(io.StringIO()):
                self.module.write_rows([{"score": NAN}], ["score"], True)
        args = SimpleNamespace(
            profile="example", url="https://example.test/", strategy="mobile",
            category=["performance"], audit_limit=10, json=True,
        )
        payload = lighthouse(
            categories={"performance": {"score": 0.5, "auditRefs": [{"id": "a", "weight": 3}]}},
            audits={"a": {"score": 0.25, "title": "Fix a"},
                    "largest-contentful-paint": {"numericValue": 2400.5, "displayValue": "2.4 s"}},
        )
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.module.cmd_analyze(args)
        emitted = output.getvalue()
        self.assertNotIn("NaN", emitted)
        self.assertNotIn("Infinity", emitted)
        for row in json.loads(emitted):
            for value in row.values():
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value))

    def test_malformed_response_exits_two_without_a_traceback(self):
        for payload in (lighthouse(categories={"performance": {"auditRefs": None}}),
                        lighthouse(categories={"performance": {"score": NAN}}),
                        {"lighthouseResult": None}):
            with self.subTest(payload=payload):
                with patch.dict(os.environ, self.env, clear=True), patch.object(
                    self.module, "request_json", return_value=payload
                ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
                    code = self.module.main(["analyze", "--profile", "example", "--url", "https://example.test/"])
                self.assertEqual(2, code)
                self.assertTrue(error.getvalue().startswith("ERROR: "), error.getvalue())
                self.assertNotIn("Traceback", error.getvalue())

    def test_request_json_resolves_the_opener_at_call_time_without_touching_the_network(self):
        def refuse(*args, **kwargs):
            raise AssertionError("a real network connection was attempted")

        calls = []

        def fake_open_url(request, timeout=60):
            calls.append(request)
            return Response({"lighthouseResult": {"categories": {}, "audits": {}}})

        with patch.object(socket.socket, "connect", refuse), patch.object(
            socket, "create_connection", refuse
        ), patch.object(socket, "getaddrinfo", refuse), patch.object(
            self.module, "open_url", fake_open_url
        ):
            # No opener argument: a def-time default would bypass the patch and hit the guards above.
            result = self.module.request_json([("url", "https://example.test/"), ("key", "secret-key")])
        self.assertEqual({"lighthouseResult": {"categories": {}, "audits": {}}}, result)
        self.assertEqual(1, len(calls))
        self.assertIn("key=secret-key", calls[0].full_url)

    def test_launcher_help_is_credential_free_and_resolves_outside_repo(self):
        result = subprocess.run([str(LAUNCHER), "--help"], cwd="/tmp", env={"PATH": os.environ.get("PATH", "")}, text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("PageSpeed Insights", result.stdout)


if __name__ == "__main__":
    unittest.main()
