#!/usr/bin/env python3
"""Offline tests for google-crux."""

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
SCRIPT = HERE / "google-crux.py"
LAUNCHER = HERE.parent / "google-crux"


def load_module():
    spec = importlib.util.spec_from_file_location("google_crux_module", SCRIPT)
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

    def read(self, size=-1):
        return json.dumps(self.payload).encode()[:size]


def date(year, month, day):
    return {"year": year, "month": month, "day": day}


def current_payload():
    return {"record": {
        "key": {"url": "https://www.example.test/page", "formFactor": "PHONE"},
        "collectionPeriod": {
            "firstDate": date(2026, 7, 28), "lastDate": date(2026, 8, 24),
        },
        "metrics": {
            "largest_contentful_paint": {
                "histogram": [
                    {"start": 0, "end": 2500, "density": 0.7},
                    {"start": 2500, "end": 4000, "density": 0.2},
                    {"start": 4000, "density": 0.1},
                ],
                "percentiles": {"p75": 2750},
            },
            "interaction_to_next_paint": {
                "histogram": [
                    {"start": 0, "end": 200, "density": 0.8},
                    {"start": 200, "end": 500, "density": 0.15},
                    {"start": 500, "density": 0.05},
                ],
                "percentiles": {"p75": 180},
            },
            "cumulative_layout_shift": {
                "histogram": [
                    {"start": "0.00", "end": "0.10", "density": 0.75},
                    {"start": "0.10", "end": "0.25", "density": 0.2},
                    {"start": "0.25", "density": 0.05},
                ],
                "percentiles": {"p75": "0.12"},
            },
        },
    }}


def history_payload():
    return {"record": {
        "key": {"origin": "https://www.example.test", "formFactor": "ALL"},
        "collectionPeriods": [
            {"firstDate": date(2026, 7, 21), "lastDate": date(2026, 8, 17)},
            {"firstDate": date(2026, 7, 28), "lastDate": date(2026, 8, 24)},
        ],
        "metrics": {
            "largest_contentful_paint": {
                "histogramTimeseries": [
                    {"start": 0, "end": 2500, "densities": [0.7, "NaN"]},
                    {"start": 2500, "end": 4000, "densities": [0.2, "NaN"]},
                    {"start": 4000, "densities": [0.1, "NaN"]},
                ],
                "percentilesTimeseries": {"p75s": [2750, None]},
            },
        },
    }}


def query_args(command="current", **overrides):
    defaults = dict(
        command=command, profile="example", url="https://www.example.test/page",
        origin=None, form_factor="phone", metric=None, distributions=False,
        json=True, periods=25,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class CruxTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.profile = self.module.Profile("example", "secret-key", "Example CrUX")

    def execute(self, function, args, payload):
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ) as request, redirect_stdout(io.StringIO()) as output, redirect_stderr(
            io.StringIO()
        ) as error:
            code = function(args)
        return code, json.loads(output.getvalue()) if output.getvalue() else [], error.getvalue(), request

    def test_profiles_discovers_named_profile_without_network_or_secret_output(self):
        env = {
            "GOOGLE_CRUX_API_KEY__EXAMPLE": "secret-key",
            "GOOGLE_CRUX_LABEL__EXAMPLE": "Example CrUX",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "open_url", side_effect=AssertionError("network")
        ), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, self.module.main(["profiles"]))
        self.assertIn("example,Example CrUX,ready", output.getvalue())
        self.assertNotIn("secret-key", output.getvalue())

    def test_named_profile_never_falls_back_to_default_key(self):
        env = {"GOOGLE_CRUX_API_KEY": "plain", "GOOGLE_CRUX_PROFILES": "example"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(self.module.CruxError, "API_KEY__EXAMPLE"):
                self.module.get_profile("example")
        self.assertNotIn("plain", repr(self.profile))

    def test_request_posts_bounded_json_and_redacts_key_from_google_error(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response({"record": {}})

        body = {"url": "https://example.test/a b", "metrics": ["largest_contentful_paint"]}
        self.module.request_json(self.module.CURRENT_ENDPOINT, "secret-key", body, opener)
        self.assertEqual("POST", requests[0].method)
        self.assertEqual(body, json.loads(requests[0].data))
        self.assertIn("records:queryRecord?key=secret-key", requests[0].full_url)

        error = urllib.error.HTTPError(
            "https://example", 403, "Forbidden", {},
            io.BytesIO(json.dumps({"error": {
                "message": "API disabled for secret-key",
            }}).encode()),
        )
        with self.assertRaises(self.module.CruxError) as raised:
            self.module.request_json(
                self.module.CURRENT_ENDPOINT, "secret-key", body,
                opener=lambda *args, **kwargs: (_ for _ in ()).throw(error),
            )
        self.assertIn("[REDACTED]", str(raised.exception))
        self.assertNotIn("secret-key", str(raised.exception))

    def test_current_requests_core_metrics_and_emits_p75_without_distributions(self):
        args = query_args()
        code, rows, error, request = self.execute(self.module.cmd_current, args, current_payload())
        self.assertEqual(0, code)
        self.assertEqual("", error)
        request.assert_called_once()
        endpoint, _, body = request.call_args.args
        self.assertEqual("queryRecord", endpoint)
        self.assertEqual({
            "url": args.url,
            "metrics": [
                "largest_contentful_paint", "interaction_to_next_paint",
                "cumulative_layout_shift",
            ],
            "formFactor": "PHONE",
        }, body)
        self.assertEqual(["percentile"] * 3, [row["row_type"] for row in rows])
        self.assertEqual([2750, 180, "0.12"], [row["p75"] for row in rows])
        self.assertEqual("https://www.example.test/page", rows[0]["requested"])
        self.assertEqual("phone", rows[0]["form_factor"])
        self.assertEqual("2026-07-28", rows[0]["first_date"])

    def test_current_distributions_preserve_google_bucket_bounds(self):
        args = query_args(metric=["cls"], distributions=True)
        code, rows, _, _ = self.execute(self.module.cmd_current, args, current_payload())
        self.assertEqual(0, code)
        self.assertEqual(4, len(rows))
        self.assertEqual(["0.00", "0.10", "0.25"], [
            row["bucket_start"] for row in rows[1:]
        ])
        self.assertEqual("", rows[-1]["bucket_end"])

    def test_repeated_metric_is_requested_and_reported_once(self):
        args = query_args(metric=["lcp", "lcp"])
        code, rows, _, request = self.execute(self.module.cmd_current, args, current_payload())
        self.assertEqual(0, code)
        self.assertEqual(["largest_contentful_paint"], request.call_args.args[2]["metrics"])
        self.assertEqual(1, len(rows))

    def test_history_joins_periods_by_index_and_marks_missing_eligibility(self):
        args = query_args(
            command="history", url=None, origin="https://www.example.test",
            form_factor="all", metric=["lcp"], distributions=True, periods=2,
        )
        code, rows, error, request = self.execute(
            self.module.cmd_history, args, history_payload()
        )
        self.assertEqual(0, code)
        self.assertEqual("", error)
        self.assertEqual("queryHistoryRecord", request.call_args.args[0])
        self.assertEqual(2, request.call_args.args[2]["collectionPeriodCount"])
        percentile = [row for row in rows if row["row_type"] == "percentile"]
        self.assertEqual([2750, ""], [row["p75"] for row in percentile])
        self.assertEqual(["true", "false"], [row["eligible"] for row in percentile])
        missing = [row for row in rows if row["eligible"] == "false"]
        self.assertTrue(missing)
        self.assertTrue(all(row.get("density", "") == "" for row in missing))

    def test_history_refuses_misaligned_or_mixed_eligibility_arrays(self):
        args = query_args(
            command="history", url=None, origin="https://www.example.test",
            form_factor="all", metric=["lcp"], periods=2,
        )
        payload = history_payload()
        payload["record"]["metrics"]["largest_contentful_paint"][
            "percentilesTimeseries"
        ]["p75s"] = [2750]
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ):
            with self.assertRaisesRegex(self.module.CruxError, "does not match"):
                self.module.cmd_history(args)

        payload = history_payload()
        payload["record"]["metrics"]["largest_contentful_paint"][
            "histogramTimeseries"
        ][0]["densities"][1] = 0.2
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ):
            with self.assertRaisesRegex(self.module.CruxError, "mixed eligible"):
                self.module.cmd_history(args)

    def test_malformed_distributions_and_wrong_form_factor_are_refused(self):
        args = query_args(metric=["lcp"])
        payload = current_payload()
        payload["record"]["metrics"]["largest_contentful_paint"]["histogram"][0][
            "density"
        ] = 0.5
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ):
            with self.assertRaisesRegex(self.module.CruxError, "do not total 1"):
                self.module.cmd_current(args)

        payload = current_payload()
        payload["record"]["metrics"]["largest_contentful_paint"]["histogram"] = []
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ):
            with self.assertRaisesRegex(self.module.CruxError, "empty"):
                self.module.cmd_current(args)

        payload = current_payload()
        payload["record"]["key"]["formFactor"] = "DESKTOP"
        with patch.object(self.module, "selected_profile", return_value=self.profile), patch.object(
            self.module, "request_json", return_value=payload
        ):
            with self.assertRaisesRegex(self.module.CruxError, "unexpected CrUX form factor"):
                self.module.cmd_current(args)

    def test_missing_metric_returns_partial_evidence_and_nonzero_status(self):
        args = query_args(metric=["lcp", "inp"])
        payload = current_payload()
        del payload["record"]["metrics"]["interaction_to_next_paint"]
        code, rows, error, _ = self.execute(self.module.cmd_current, args, payload)
        self.assertEqual(2, code)
        self.assertEqual(["largest_contentful_paint"], [row["metric"] for row in rows])
        self.assertIn("interaction_to_next_paint", error)

    def test_cli_validates_target_period_bound_and_works_outside_repository(self):
        self.assertEqual("https://example.test", self.module.web_origin("https://example.test/"))
        with self.assertRaises(self.module.argparse.ArgumentTypeError):
            self.module.web_origin("https://example.test/path")
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(2, self.module.main([
                "history", "--origin", "https://example.test", "--periods", "41",
            ]))
        self.assertIn("between 1 and 40", error.getvalue())

        completed = subprocess.run(
            [str(LAUNCHER), "--help"], cwd="/tmp", capture_output=True, text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())


if __name__ == "__main__":
    unittest.main()
