#!/usr/bin/env python3
"""Offline tests for google-pagespeed-insights."""

from __future__ import annotations

import importlib.util
import io
import json
import os
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
        self.assertEqual([value for key, value in params if key == "category"], ["performance", "seo"])
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

    def test_launcher_help_is_credential_free_and_resolves_outside_repo(self):
        result = subprocess.run([str(LAUNCHER), "--help"], cwd="/tmp", env={"PATH": os.environ.get("PATH", "")}, text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("PageSpeed Insights", result.stdout)


if __name__ == "__main__":
    unittest.main()
