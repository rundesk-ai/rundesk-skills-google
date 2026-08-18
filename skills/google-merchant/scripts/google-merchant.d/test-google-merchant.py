#!/usr/bin/env python3
"""Offline tests for the Google Merchant Center integration."""

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


SCRIPT = Path(__file__).resolve().parent / "google-merchant.py"
LAUNCHER = SCRIPT.parent.parent / "google-merchant"


def load_module():
    spec = importlib.util.spec_from_file_location("google_merchant_module", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load Google Merchant module")
    module = importlib.util.module_from_spec(spec)
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


def http_error(code, body=None, headers=None):
    stream = io.BytesIO(json.dumps(body).encode("utf-8") if body is not None else b"")
    return urllib.error.HTTPError("https://merchantapi.googleapis.com/x", code, "err", headers or {}, stream)


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_profiles_lists_rundesk_configuration_without_network(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID__EXAMPLE": "client",
            "GOOGLE_MERCHANT_CLIENT_SECRET__EXAMPLE": "secret",
            "GOOGLE_MERCHANT_REFRESH_TOKEN__EXAMPLE": "refresh",
            "GOOGLE_MERCHANT_LABEL__EXAMPLE": "Example Shop",
        }
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "open_url", side_effect=AssertionError("network called")
        ), redirect_stdout(output):
            self.assertEqual(0, self.module.main(["profiles"]))
        self.assertIn("example,Example Shop,true", output.getvalue())

    def test_plain_values_form_the_default_profile(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID": "client",
            "GOOGLE_MERCHANT_CLIENT_SECRET": "secret",
            "GOOGLE_MERCHANT_REFRESH_TOKEN": "refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(["default"], self.module.configured_profile_names())
            self.assertEqual("refresh", self.module.get_profile("default").refresh_token)

    def test_named_profile_never_falls_back_to_plain_credentials(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID": "plain-client",
            "GOOGLE_MERCHANT_CLIENT_SECRET": "plain-secret",
            "GOOGLE_MERCHANT_REFRESH_TOKEN": "plain-refresh",
            "GOOGLE_MERCHANT_CLIENT_ID__EXAMPLE": "client",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.MerchantError) as raised:
                self.module.get_profile("example")
        message = str(raised.exception)
        self.assertIn("GOOGLE_MERCHANT_CLIENT_SECRET__EXAMPLE", message)
        self.assertNotIn("plain-secret", message)

    def test_a_profile_in_both_forms_is_refused_as_ambiguous(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID__EXAMPLE": "suffix",
            "GOOGLE_MERCHANT_EXAMPLE_CLIENT_ID": "infix",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.MerchantError):
                self.module.profile_form("example")

    def test_ambiguous_profile_selection_is_refused(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID__ONE": "a",
            "GOOGLE_MERCHANT_CLIENT_ID__TWO": "b",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(self.module.MerchantError):
                self.module.selected_profile_name(SimpleNamespace(profile=None))

    def test_a_dotenv_readable_beyond_its_owner_is_reported(self):
        with patch.dict(os.environ, {}, clear=True):
            path = Path(self.enterContext_tempdir()) / "env"
            path.write_text("GOOGLE_MERCHANT_CLIENT_ID=abc\n", encoding="utf-8")
            path.chmod(0o644)
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.module.load_dotenv(path)
            self.assertIn("readable beyond its owner", errors.getvalue())
            self.assertNotIn("abc", errors.getvalue())

    def enterContext_tempdir(self):
        import tempfile

        directory = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        return directory


class QueryLanguageTest(unittest.TestCase):
    """MCQL defines no string escape, so a value needing one must be refused."""

    def setUp(self):
        self.module = load_module()

    def test_a_value_containing_a_quote_or_backslash_is_refused(self):
        for value in ("o'brien", 'say "hi"', "back\\slash", "tail'; DROP", '"'):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.mcql_string(value)

    def test_a_value_containing_a_control_character_is_refused(self):
        for value in ("line\nbreak", "tab\tstop", "null\x00byte", "del\x7f"):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.mcql_string(value)

    def test_an_ordinary_value_is_quoted_once(self):
        self.assertEqual("'Acme Tools'", self.module.mcql_string("Acme Tools"))

    def test_an_empty_or_non_string_value_is_refused(self):
        for value in ("", None, 5, ["a"]):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.mcql_string(value)

    def test_injection_through_a_command_option_cannot_reach_the_query(self):
        """A crafted --brand must fail rather than extend the WHERE clause."""
        module = self.module
        args = SimpleNamespace(
            profile="example", account="123", limit=10, json=False,
            status=None, brand="Acme' OR title != '", reporting_context=None,
        )
        with patch.object(module, "get_profile", lambda name: module.Profile("example", "c", "s", "r", "e")), \
                patch.object(module, "refresh_access_token", lambda profile: "token"), \
                patch.object(module, "search_rows", side_effect=AssertionError("query was sent")):
            with self.assertRaises(module.MerchantError):
                module.command_products(args)

    def test_only_snake_case_identifiers_are_accepted_as_names(self):
        for value in ("Date", "offer id", "offer-id", "1st", "drop table", "", "a;b"):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.mcql_name(value)
        self.assertEqual("category_l1", self.module.mcql_name("category_l1"))

    def test_a_query_cannot_be_built_from_an_unchecked_field(self):
        query = self.module.Query("product_view", ("id; DROP",))
        with self.assertRaises(self.module.MerchantError):
            query.text()

    def test_only_real_calendar_dates_are_accepted(self):
        for value in ("2024-02-30", "2024-13-01", "24-01-01", "2024/01/01", "", "LAST_30_DAYS"):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.mcql_date(value, "--start-date")
        self.assertEqual("'2024-02-29'", self.module.mcql_date("2024-02-29", "--start-date"))

    def test_only_googles_documented_relative_ranges_are_accepted(self):
        self.assertEqual("date DURING LAST_30_DAYS", self.module.during("date", "LAST_30_DAYS"))
        for value in ("LAST_90_DAYS", "THIS_YEAR", "yesterday", "LAST_30_DAYS'"):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.during("date", value)

    def test_the_builder_joins_conditions_with_and_only(self):
        query = self.module.Query(
            "product_view", ("id", "title"), ("brand = 'A'", "condition = 'new'"), "title", False, 5
        )
        text = query.text()
        self.assertEqual(
            "SELECT id, title FROM product_view WHERE brand = 'A' AND condition = 'new' "
            "ORDER BY title ASC LIMIT 5",
            text,
        )
        self.assertNotIn(" OR ", text)

    def test_a_country_or_category_must_match_its_documented_form(self):
        self.assertEqual("US", self.module.country_code("us"))
        for value in ("USA", "U", "u1", "US'", ""):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.country_code(value)
        self.assertEqual("166", self.module.category_id("166"))
        for value in ("166 OR 1=1", "-1", "abc", ""):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.category_id(value)

    def test_an_enum_option_is_restricted_to_googles_members(self):
        self.assertEqual("ADS", self.module.enum_choice("ads", self.module.MARKETING_METHODS, "--marketing-method"))
        with self.assertRaises(self.module.MerchantError):
            self.module.enum_choice("BUY_ON_GOOGLE", self.module.MARKETING_METHODS, "--marketing-method")
        with self.assertRaises(self.module.MerchantError):
            self.module.reporting_context("SHOPPING_ADS' OR '1'='1")


class RequestContractTest(unittest.TestCase):
    """The exact query text and HTTP request each command sends."""

    def setUp(self):
        self.module = load_module()

    def capture(self, handler, args, results=None):
        sent = {}

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            sent.update(method=method, url=url, params=params, payload=payload)
            return {"results": results or []}

        module = self.module
        with patch.object(module, "get_profile", lambda name: module.Profile("example", "c", "s", "r", "e")), \
                patch.object(module, "refresh_access_token", lambda profile: "token"), \
                patch.object(module, "api_request", fake_request), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            handler(args)
        return sent

    def base(self, **overrides):
        args = dict(profile="example", account="123", limit=10, json=False)
        args.update(overrides)
        return SimpleNamespace(**args)

    def test_performance_sends_the_documented_search_request(self):
        sent = self.capture(
            self.module.command_performance,
            self.base(breakdown="product", during="LAST_30_DAYS", start_date=None, end_date=None,
                      marketing_method=None, country=None, store_type=None),
        )
        self.assertEqual("POST", sent["method"])
        self.assertEqual(
            "https://merchantapi.googleapis.com/reports/v1/accounts/123/reports:search", sent["url"]
        )
        self.assertEqual(
            {
                "query": "SELECT offer_id, title, clicks, impressions, click_through_rate, "
                         "conversions, conversion_value, conversion_rate "
                         "FROM product_performance_view WHERE date DURING LAST_30_DAYS "
                         "ORDER BY clicks DESC LIMIT 11",
                "pageSize": 11,
            },
            sent["payload"],
        )

    def test_performance_always_carries_a_date_condition(self):
        for window in (
            dict(during="LAST_7_DAYS", start_date=None, end_date=None),
            dict(during="LAST_30_DAYS", start_date="2024-01-01", end_date="2024-01-31"),
        ):
            with self.subTest(**window):
                sent = self.capture(
                    self.module.command_performance,
                    self.base(breakdown="brand", marketing_method=None, country=None,
                              store_type=None, **window),
                )
                self.assertIn("WHERE date ", sent["payload"]["query"])

    def test_a_partial_explicit_window_is_refused(self):
        module = self.module
        args = self.base(breakdown="date", during="LAST_30_DAYS", start_date="2024-01-01",
                         end_date=None, marketing_method=None, country=None, store_type=None)
        with self.assertRaises(module.MerchantError):
            module.date_condition(args)

    def test_a_time_breakdown_reads_in_order_and_others_read_largest_first(self):
        ordered = self.capture(
            self.module.command_performance,
            self.base(breakdown="date", during="LAST_30_DAYS", start_date=None, end_date=None,
                      marketing_method=None, country=None, store_type=None),
        )
        self.assertIn("ORDER BY date ASC", ordered["payload"]["query"])
        ranked = self.capture(
            self.module.command_performance,
            self.base(breakdown="brand", during="LAST_30_DAYS", start_date=None, end_date=None,
                      marketing_method=None, country=None, store_type=None),
        )
        self.assertIn("ORDER BY clicks DESC", ranked["payload"]["query"])

    def test_every_performance_breakdown_selects_a_metric_beside_its_segments(self):
        for breakdown in self.module.PERFORMANCE_BREAKDOWNS:
            with self.subTest(breakdown=breakdown):
                sent = self.capture(
                    self.module.command_performance,
                    self.base(breakdown=breakdown, during="LAST_30_DAYS", start_date=None,
                              end_date=None, marketing_method=None, country=None, store_type=None),
                )
                query = sent["payload"]["query"]
                self.assertIn("clicks", query)
                self.assertNotIn("*", query)

    def test_products_orders_by_click_potential_and_filters_by_context(self):
        sent = self.capture(
            self.module.command_products,
            self.base(status="eligible", brand="Acme", reporting_context="shopping_ads"),
        )
        self.assertEqual(
            "SELECT id, offer_id, title, brand, condition, availability, price, "
            "aggregated_reporting_context_status, click_potential, click_potential_rank, "
            "channel, feed_label, language_code FROM product_view "
            "WHERE aggregated_reporting_context_status = 'ELIGIBLE' AND brand = 'Acme' "
            "AND reporting_context = 'SHOPPING_ADS' ORDER BY click_potential_rank ASC LIMIT 11",
            sent["payload"]["query"],
        )

    def test_best_sellers_sends_googles_required_conditions(self):
        sent = self.capture(
            self.module.command_best_sellers,
            self.base(view="products", granularity="weekly", country="us", category="166", date="2022-10-10"),
        )
        query = sent["payload"]["query"]
        self.assertIn("report_granularity = 'WEEKLY'", query)
        self.assertIn("report_country_code = 'US'", query)
        # Google's own sample sends the category ID unquoted.
        self.assertIn("report_category_id = 166", query)
        self.assertIn("report_date = '2022-10-10'", query)
        for required in ("report_date", "report_granularity", "report_country_code", "report_category_id"):
            self.assertIn(required, query.split(" FROM ")[0])

    def test_best_sellers_omits_the_optional_conditions_when_unset(self):
        sent = self.capture(
            self.module.command_best_sellers,
            self.base(view="brands", granularity="monthly", country="US", category=None, date=None),
        )
        query = sent["payload"]["query"]
        self.assertIn("FROM best_sellers_brand_view", query)
        self.assertNotIn("report_category_id =", query)
        self.assertNotIn("report_date =", query)

    def test_the_top_merchant_view_never_selects_the_date_it_must_filter_on(self):
        sent = self.capture(
            self.module.command_competitive_visibility,
            self.base(view="top-merchant", country="US", category="166", during="LAST_30_DAYS",
                      start_date=None, end_date=None, traffic_source=None),
        )
        query = sent["payload"]["query"]
        selected, _, filtered = query.partition(" FROM ")
        self.assertNotIn("date", selected)
        self.assertIn("date DURING LAST_30_DAYS", filtered)

    def test_the_benchmark_view_selects_the_date_google_requires(self):
        sent = self.capture(
            self.module.command_competitive_visibility,
            self.base(view="benchmark", country="US", category="166", during="LAST_30_DAYS",
                      start_date=None, end_date=None, traffic_source=None),
        )
        selected = sent["payload"]["query"].split(" FROM ")[0]
        self.assertIn("date", selected)

    def test_competitive_visibility_always_bounds_country_and_category(self):
        for view in self.module.VISIBILITY_VIEWS:
            with self.subTest(view=view):
                sent = self.capture(
                    self.module.command_competitive_visibility,
                    self.base(view=view, country="US", category="166", during="LAST_30_DAYS",
                              start_date=None, end_date=None, traffic_source=None),
                )
                query = sent["payload"]["query"]
                self.assertIn("report_country_code = 'US'", query)
                self.assertIn("report_category_id = 166", query)

    def test_price_views_select_the_id_google_requires(self):
        for handler, extra, table in (
            (self.module.command_price_competitiveness, dict(country=None), "price_competitiveness_product_view"),
            (self.module.command_price_insights, {}, "price_insights_product_view"),
        ):
            with self.subTest(table=table):
                sent = self.capture(handler, self.base(**extra))
                query = sent["payload"]["query"]
                self.assertTrue(query.startswith("SELECT id,"))
                self.assertIn(f"FROM {table}", query)

    def test_price_competitiveness_selects_the_required_country_column(self):
        sent = self.capture(self.module.command_price_competitiveness, self.base(country=None))
        self.assertIn("report_country_code", sent["payload"]["query"].split(" FROM ")[0])

    def test_accounts_reads_the_documented_list_endpoint(self):
        sent = self.capture(self.module.command_accounts, self.base(limit=5))
        self.assertEqual("GET", sent["method"])
        self.assertEqual("https://merchantapi.googleapis.com/accounts/v1/accounts", sent["url"])
        self.assertEqual({"pageSize": 5}, sent["params"])

    def test_issue_commands_read_the_issueresolution_endpoint_with_a_bounded_filter(self):
        for handler in (self.module.command_status, self.module.command_issues):
            with self.subTest(handler=handler.__name__):
                sent = self.capture(
                    handler, self.base(reporting_context="shopping_ads", country="us")
                )
                self.assertEqual("GET", sent["method"])
                self.assertEqual(
                    "https://merchantapi.googleapis.com/issueresolution/v1/accounts/123/aggregateProductStatuses",
                    sent["url"],
                )
                self.assertEqual(
                    'reportingContext = "SHOPPING_ADS" AND country = "US"', sent["params"]["filter"]
                )

    def test_an_unfiltered_issue_read_sends_no_filter(self):
        sent = self.capture(self.module.command_status, self.base(reporting_context=None, country=None))
        self.assertNotIn("filter", sent["params"])

    def test_a_bad_query_is_rejected_before_any_credential_is_used(self):
        module = self.module
        args = self.base(status=None, brand=None, reporting_context=None)
        with patch.object(module, "get_profile", lambda name: module.Profile("example", "c", "s", "r", "e")), \
                patch.object(module, "refresh_access_token",
                             side_effect=AssertionError("token refreshed before validation")), \
                patch.object(module, "PRODUCT_FIELDS", ("id", "bad field")):
            with self.assertRaises(module.MerchantError):
                module.command_products(args)

    def test_an_account_id_is_required_to_be_numeric(self):
        self.assertEqual("123", self.module.account_id("accounts/123"))
        self.assertEqual("123", self.module.account_id("123"))
        for value in ("abc", "1/2", "", "12'", None, "accounts/abc"):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.account_id(value)


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_an_unexpected_origin_is_refused_before_any_request(self):
        for url in (
            "https://evil.example/reports/v1/accounts/1/reports:search",
            "https://merchantapi.googleapis.com.evil.example/reports/v1/x",
            "https://merchantapi.googleapis.com/content/v2.1/x",
        ):
            with self.subTest(url=url):
                with patch.object(self.module, "open_url", side_effect=AssertionError("network called")):
                    with self.assertRaises(self.module.MerchantError):
                        self.module.api_request("token", "GET", url)

    def test_a_report_row_missing_the_requested_view_is_refused(self):
        module = self.module
        query = module.Query("product_view", ("id",), limit=2)
        with patch.object(module, "api_request", return_value={"results": [{"wrongView": {}}]}):
            with self.assertRaises(module.MerchantError) as raised:
                module.search_rows("token", "1", query, 2, "productView")
        self.assertIn("without productView", str(raised.exception))

    def test_each_allowed_origin_is_a_pinned_v1_sub_api(self):
        for base in self.module.API_BASES:
            with self.subTest(base=base):
                self.assertTrue(base.startswith("https://merchantapi.googleapis.com/"))
                self.assertTrue(base.endswith("/v1"))
                self.assertNotIn("beta", base)
                self.assertNotIn("alpha", base)

    def test_a_redirect_is_refused_so_credentials_stay_on_the_api_origin(self):
        handler = self.module.RejectRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(None, None, 302, "found", {}, "https://evil.example/")
        )

    def test_search_pages_until_google_omits_the_token(self):
        pages = [
            {"results": [{"productView": {"id": "a"}}], "nextPageToken": "t1"},
            {"results": [{"productView": {"id": "b"}}]},
        ]
        sent = []

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            sent.append(payload)
            return pages[len(sent) - 1]

        query = self.module.Query("product_view", ("id",))
        with patch.object(self.module, "api_request", fake_request):
            rows, truncated = self.module.search_rows("token", "1", query, 10, "productView")
        self.assertEqual([{"id": "a"}, {"id": "b"}], rows)
        self.assertFalse(truncated)
        self.assertNotIn("pageToken", sent[0])
        self.assertEqual("t1", sent[1]["pageToken"])
        # Every page must repeat the same query text.
        self.assertEqual(sent[0]["query"], sent[1]["query"])

    def test_a_short_page_does_not_end_pagination(self):
        pages = [
            {"results": [{"productView": {"id": "a"}}], "nextPageToken": "t1"},
            {"results": [{"productView": {"id": "b"}}, {"productView": {"id": "c"}}]},
        ]
        calls = []

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            calls.append(payload)
            return pages[len(calls) - 1]

        query = self.module.Query("product_view", ("id",))
        with patch.object(self.module, "api_request", fake_request):
            rows, _ = self.module.search_rows("token", "1", query, 10, "productView")
        self.assertEqual(3, len(rows))

    def test_a_page_size_never_exceeds_googles_ceiling(self):
        sent = []

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            sent.append(payload["pageSize"])
            return {"results": []}

        query = self.module.Query("product_view", ("id",))
        with patch.object(self.module, "api_request", fake_request):
            self.module.search_rows("token", "1", query, 5000, "productView")
        self.assertEqual([self.module.MAX_PAGE_SIZE], sent)

    def test_a_repeated_page_token_is_refused_instead_of_looping(self):
        def fake_request(token, method, url, params=None, payload=None, retries=2):
            return {"results": [{"productView": {"id": "a"}}], "nextPageToken": "same"}

        query = self.module.Query("product_view", ("id",))
        with patch.object(self.module, "api_request", fake_request):
            with self.assertRaises(self.module.MerchantError):
                self.module.search_rows("token", "1", query, 100, "productView")

    def test_a_non_string_page_token_is_refused(self):
        def fake_request(token, method, url, params=None, payload=None, retries=2):
            return {"results": [], "nextPageToken": 7}

        query = self.module.Query("product_view", ("id",))
        with patch.object(self.module, "api_request", fake_request):
            with self.assertRaises(self.module.MerchantError):
                self.module.search_rows("token", "1", query, 10, "productView")

    def test_an_empty_page_with_a_token_stops_and_reports_truncation(self):
        def fake_request(token, method, url, params=None, payload=None, retries=2):
            return {"results": [], "nextPageToken": "more"}

        query = self.module.Query("product_view", ("id",))
        with patch.object(self.module, "api_request", fake_request):
            rows, truncated = self.module.search_rows("token", "1", query, 10, "productView")
        self.assertEqual([], rows)
        self.assertTrue(truncated)

    def test_reaching_the_limit_with_more_available_reports_truncation(self):
        def fake_request(token, method, url, params=None, payload=None, retries=2):
            return {"results": [{"productView": {"id": "a"}}], "nextPageToken": "more"}

        query = self.module.Query("product_view", ("id",))
        with patch.object(self.module, "api_request", fake_request):
            rows, truncated = self.module.search_rows("token", "1", query, 1, "productView")
        self.assertEqual(1, len(rows))
        self.assertTrue(truncated)

    def test_list_reads_respect_each_methods_page_ceiling(self):
        sent = []

        def fake_request(token, method, url, params=None, payload=None, retries=2):
            sent.append(params["pageSize"])
            return {"accounts": []}

        with patch.object(self.module, "api_request", fake_request):
            self.module.list_rows("token", "https://x/y", "accounts", "account", 4000, 500)
        self.assertEqual([500], sent)

    def test_limits_are_bounded(self):
        self.assertEqual(10, self.module.bounded_limit(10))
        for value in (0, -1, 100000):
            with self.subTest(value=value):
                with self.assertRaises(self.module.MerchantError):
                    self.module.bounded_limit(value)

    def test_a_retryable_status_is_retried_and_a_client_error_is_not(self):
        attempts = []

        def flaky(request, timeout=30):
            attempts.append(request)
            if len(attempts) == 1:
                raise http_error(503)
            return Response({"ok": True})

        with patch.object(self.module, "open_url", flaky), patch.object(self.module.time, "sleep", lambda s: None):
            self.assertEqual(
                {"ok": True},
                self.module.api_request("token", "GET", f"{self.module.ACCOUNTS_BASE}/accounts"),
            )
        self.assertEqual(2, len(attempts))

    def test_a_permission_failure_reports_googles_message(self):
        body = {
            "error": {
                "code": 401,
                "message": "The caller does not have access to the accounts: [1234567]",
                "status": "UNAUTHENTICATED",
            }
        }
        with patch.object(self.module, "open_url", side_effect=http_error(401, body)):
            with self.assertRaises(self.module.MerchantError) as raised:
                self.module.api_request("token", "GET", f"{self.module.ACCOUNTS_BASE}/accounts")
        self.assertIn("does not have access", str(raised.exception))

    def test_an_invalid_query_failure_reports_googles_message(self):
        body = {"error": {"code": 400, "message": "The query is invalid.", "status": "INVALID_ARGUMENT"}}
        with patch.object(self.module, "open_url", side_effect=http_error(400, body)):
            with self.assertRaises(self.module.MerchantError) as raised:
                self.module.api_request("token", "POST", f"{self.module.REPORTS_BASE}/accounts/1/reports:search")
        self.assertIn("The query is invalid.", str(raised.exception))

    def test_an_error_body_without_a_message_falls_back_to_the_status_code(self):
        with patch.object(self.module, "open_url", side_effect=http_error(403, {"nope": 1})):
            with self.assertRaises(self.module.MerchantError) as raised:
                self.module.api_request("token", "GET", f"{self.module.ACCOUNTS_BASE}/accounts")
        self.assertIn("HTTP 403", str(raised.exception))


class MalformedResponseTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_body_that_is_not_json_is_reported_without_a_traceback(self):
        with patch.object(self.module, "open_url", lambda request, timeout=30: RawResponse(b"<html>")):
            with self.assertRaises(self.module.MerchantError):
                self.module.api_request("token", "GET", f"{self.module.ACCOUNTS_BASE}/accounts")

    def test_a_list_or_scalar_body_is_refused(self):
        for payload in ([], "text", 5):
            with self.subTest(payload=payload):
                with patch.object(self.module, "open_url", lambda request, timeout=30, p=payload: Response(p)):
                    with self.assertRaises(self.module.MerchantError):
                        self.module.api_request("token", "GET", f"{self.module.ACCOUNTS_BASE}/accounts")

    def test_a_malformed_results_collection_is_refused(self):
        for payload in ({"results": "rows"}, {"results": ["a"]}, {"results": [{"productView": 5}]}):
            with self.subTest(payload=payload):
                with patch.object(self.module, "api_request", lambda *a, **k: payload):
                    query = self.module.Query("product_view", ("id",))
                    with self.assertRaises(self.module.MerchantError):
                        self.module.search_rows("token", "1", query, 10, "productView")

    def test_a_nested_value_in_a_scalar_cell_is_refused(self):
        with self.assertRaises(self.module.MerchantError):
            self.module.text_field({"accountId": {"a": 1}}, "accountId")

    def test_an_unrecognized_nested_report_value_is_refused(self):
        with self.assertRaises(self.module.MerchantError):
            self.module.report_cell({"surprise": 1})

    def test_a_malformed_price_amount_is_refused(self):
        with self.assertRaises(self.module.MerchantError):
            self.module.money_amount({"amountMicros": "many", "currencyCode": "USD"})

    def test_a_malformed_body_exits_two_rather_than_raising(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID": "client",
            "GOOGLE_MERCHANT_CLIENT_SECRET": "secret",
            "GOOGLE_MERCHANT_REFRESH_TOKEN": "refresh",
        }
        errors = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "refresh_access_token", lambda profile: "token"
        ), patch.object(self.module, "open_url", lambda request, timeout=30: RawResponse(b"nope")), \
                redirect_stderr(errors), redirect_stdout(io.StringIO()):
            result = self.module.main(["accounts"])
        self.assertEqual(2, result)
        self.assertIn("ERROR:", errors.getvalue())

    def test_a_missing_access_token_is_refused(self):
        module = self.module
        profile = module.Profile("example", "client", "secret", "refresh", "Example")
        for payload in ({}, {"access_token": ""}, {"access_token": 5}):
            with self.subTest(payload=payload):
                with patch.object(module, "open_url", lambda request, timeout=30, p=payload: Response(p)):
                    with self.assertRaises(module.MerchantError):
                        module.refresh_access_token(profile)


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_price_is_rendered_exactly_with_its_own_currency_column(self):
        rows = [{"price": {"amountMicros": "150000000", "currencyCode": "USD"}, "id": "a"}]
        headers, table = self.module.report_table(rows, ("id", "price"), {})
        self.assertEqual(["id", "price", "price_currency"], headers)
        self.assertEqual("150", table[0]["price"])
        self.assertEqual("USD", table[0]["price_currency"])

    def test_a_fractional_price_keeps_its_exact_value(self):
        self.assertEqual("19.99", self.module.money_amount({"amountMicros": "19990000"}))
        self.assertEqual("0.01", self.module.money_amount({"amountMicros": "10000"}))

    def test_googles_zero_padded_currency_row_is_preserved_rather_than_merged(self):
        """Google splits price and non-price metrics into separate rows per currency."""
        rows = [
            {"conversions": "27", "conversionValue": {"amountMicros": "0", "currencyCode": ""}},
            {"conversions": "0", "conversionValue": {"amountMicros": "150000000", "currencyCode": "USD"}},
            {"conversions": "0", "conversionValue": {"amountMicros": "70000000", "currencyCode": "CAD"}},
        ]
        headers, table = self.module.report_table(rows, ("conversions", "conversion_value"), {})
        self.assertIn("conversion_value_currency", headers)
        self.assertEqual(3, len(table))
        self.assertEqual(["", "USD", "CAD"], [row["conversion_value_currency"] for row in table])
        self.assertEqual(["27", "0", "0"], [row["conversions"] for row in table])

    def test_a_price_missing_its_amount_still_reports_its_currency(self):
        """Header detection and cell rendering must recognize a Price identically."""
        rows = [{"price": {"currencyCode": "USD"}}]
        headers, table = self.module.report_table(rows, ("price",), {})
        self.assertIn("price_currency", headers)
        self.assertEqual("", table[0]["price"])
        self.assertEqual("USD", table[0]["price_currency"])

    def test_money_schema_is_stable_for_empty_results(self):
        headers, table = self.module.report_table([], ("id", "price", "conversion_value"), {})
        self.assertEqual(
            ["id", "price", "price_currency", "conversion_value", "conversion_value_currency"],
            headers,
        )
        self.assertEqual([], table)

    def test_a_date_is_rendered_from_either_shape_google_sends(self):
        self.assertEqual("2023-12-01", self.module.report_cell({"year": 2023, "month": 12, "day": 1}))
        self.assertEqual("2022-10-10", self.module.report_cell("2022-10-10"))

    def test_a_repeated_field_is_joined_rather_than_dumped(self):
        self.assertEqual("a|b", self.module.report_cell(["a", "b"]))
        self.assertEqual("true", self.module.report_cell(True))
        self.assertEqual("", self.module.report_cell(None))

    def test_a_snake_case_field_reads_its_camel_case_response_key(self):
        self.assertEqual("clickThroughRate", self.module.camel("click_through_rate"))
        self.assertEqual("categoryL1", self.module.camel("category_l1"))
        self.assertEqual("clicks", self.module.camel("clicks"))
        self.assertEqual("productPerformanceView", self.module.camel("product_performance_view"))

    def test_every_queried_view_maps_to_its_response_key(self):
        tables = ["product_performance_view", "product_view", "price_competitiveness_product_view",
                  "price_insights_product_view", "competitive_visibility_benchmark_view"]
        tables.extend(table for table, _ in self.module.BEST_SELLER_VIEWS.values())
        for table in tables:
            with self.subTest(table=table):
                key = self.module.camel(table)
                self.assertTrue(key[0].islower())
                self.assertEqual(table, "".join(
                    "_" + c.lower() if c.isupper() else c for c in key
                ))

    def test_issues_are_reported_with_the_largest_blast_radius_first(self):
        module = self.module
        statuses = [
            {
                "reportingContext": "SHOPPING_ADS",
                "country": "US",
                "itemLevelIssues": [
                    {"code": "small", "productCount": "2", "severity": "DEMOTED",
                     "resolution": "MERCHANT_ACTION", "documentationUri": "https://x"},
                    {"code": "large", "productCount": "90", "severity": "DISAPPROVED",
                     "resolution": "MERCHANT_ACTION", "documentationUri": "https://y"},
                ],
            }
        ]
        output = io.StringIO()
        with patch.object(module, "get_profile", lambda name: module.Profile("example", "c", "s", "r", "e")), \
                patch.object(module, "refresh_access_token", lambda profile: "token"), \
                patch.object(module, "list_rows", lambda *a, **k: (statuses, False)), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            module.command_issues(SimpleNamespace(
                profile="example", account="123", limit=10, json=False,
                reporting_context=None, country=None,
            ))
        lines = output.getvalue().strip().splitlines()
        self.assertTrue(lines[1].startswith("large,DISAPPROVED,MERCHANT_ACTION,90"))
        self.assertTrue(lines[2].startswith("small,DEMOTED,MERCHANT_ACTION,2"))

    def test_issues_limit_bounds_emitted_rows_not_status_buckets(self):
        module = self.module
        statuses = [{
            "reportingContext": "SHOPPING_ADS",
            "country": "US",
            "itemLevelIssues": [
                {"code": "small", "productCount": "2"},
                {"code": "large", "productCount": "90"},
            ],
        }]
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(module, "get_profile", lambda name: module.Profile("example", "c", "s", "r", "e")), \
                patch.object(module, "refresh_access_token", lambda profile: "token"), \
                patch.object(module, "list_rows", side_effect=lambda *a, **k: (statuses, False)) as listed, \
                redirect_stdout(output), redirect_stderr(errors):
            module.command_issues(SimpleNamespace(
                profile="example", account="123", limit=1, json=False,
                reporting_context=None, country=None,
            ))
        self.assertEqual(1000, listed.call_args.args[4])
        lines = output.getvalue().strip().splitlines()
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[1].startswith("large,"))
        self.assertIn("--limit 1", errors.getvalue())

    def test_issues_refuse_a_non_integer_product_count(self):
        module = self.module
        statuses = [{
            "reportingContext": "SHOPPING_ADS",
            "country": "US",
            "itemLevelIssues": [{"code": "bad", "productCount": "many"}],
        }]
        with patch.object(module, "get_profile", lambda name: module.Profile("example", "c", "s", "r", "e")), \
                patch.object(module, "refresh_access_token", lambda profile: "token"), \
                patch.object(module, "list_rows", return_value=(statuses, False)), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(module.MerchantError) as raised:
                module.command_issues(SimpleNamespace(
                    profile="example", account="123", limit=1, json=False,
                    reporting_context=None, country=None,
                ))
        self.assertIn("productCount", str(raised.exception))

    def test_report_limit_uses_a_sentinel_row_and_warns(self):
        module = self.module
        output, errors = io.StringIO(), io.StringIO()
        captured = {}

        def fake_search(token, account, query, limit, view):
            captured["query"] = query.text()
            captured["limit"] = limit
            return ([{"id": "first"}, {"id": "sentinel"}], False)

        with patch.object(module, "get_profile", lambda name: module.Profile("example", "c", "s", "r", "e")), \
                patch.object(module, "refresh_access_token", lambda profile: "token"), \
                patch.object(module, "search_rows", side_effect=fake_search), \
                redirect_stdout(output), redirect_stderr(errors):
            module.run_report(
                SimpleNamespace(profile="example", account="123", limit=1, json=False),
                "product_view", ("id",), (),
            )
        self.assertEqual(2, captured["limit"])
        self.assertTrue(captured["query"].endswith("LIMIT 2"))
        self.assertEqual(["id,account_id,profile", "first,123,example"], output.getvalue().strip().splitlines())
        self.assertIn("--limit 1", errors.getvalue())

    def test_an_unrecognized_aggregate_status_shape_fails_loudly(self):
        """A shape this package does not recognize must not print empty columns."""
        module = self.module
        # The field names Google's migration-era guide shows instead of the ones the
        # client-library sample and discovery document use.
        foreign = [{
            "reportingContext": "SHOPPING_ADS",
            "countryCode": "US",
            "statistics": {"approvedCount": "1500"},
            "issues": [{"issueType": "missing_image", "numProducts": "15"}],
        }]
        for handler in (module.command_status, module.command_issues):
            with self.subTest(handler=handler.__name__):
                with patch.object(module, "get_profile",
                                  lambda name: module.Profile("example", "c", "s", "r", "e")), \
                        patch.object(module, "refresh_access_token", lambda profile: "token"), \
                        patch.object(module, "list_rows", lambda *a, **k: (foreign, False)), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(module.MerchantError) as raised:
                        handler(SimpleNamespace(
                            profile="example", account="123", limit=10, json=False,
                            reporting_context=None, country=None,
                        ))
                self.assertTrue(
                    "unrecognized" in str(raised.exception) or "without stats" in str(raised.exception)
                )

    def test_a_recognized_aggregate_status_shape_is_accepted(self):
        self.assertEqual(
            {"stats": {}}, self.module.expect_known_shape({"stats": {}}, ("stats", "itemLevelIssues"), "x")
        )
        self.assertEqual(
            {"itemLevelIssues": []},
            self.module.expect_known_shape({"itemLevelIssues": []}, ("stats", "itemLevelIssues"), "x"),
        )

    def test_json_output_is_opt_in(self):
        module = self.module
        rows = [{"a": "1"}]
        plain, structured = io.StringIO(), io.StringIO()
        with redirect_stdout(plain):
            module.emit_rows(("a",), rows, False)
        with redirect_stdout(structured):
            module.emit_rows(("a",), rows, True)
        self.assertEqual("a\n1\n", plain.getvalue())
        self.assertEqual(rows, json.loads(structured.getvalue()))

    def test_truncation_is_reported_on_stderr(self):
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.module.warn_truncated(True, 25)
        self.assertIn("--limit 25", errors.getvalue())


class SecretSafetyTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_profile_never_repeats_its_secrets(self):
        profile = self.module.Profile("example", "client-id", "client-secret", "refresh-token", "Example")
        rendered = repr(profile)
        for secret in ("client-id", "client-secret", "refresh-token"):
            self.assertNotIn(secret, rendered)

    def test_a_failure_never_discloses_credentials(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID": "client-id-value",
            "GOOGLE_MERCHANT_CLIENT_SECRET": "client-secret-value",
            "GOOGLE_MERCHANT_REFRESH_TOKEN": "refresh-token-value",
        }
        errors, output = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "open_url", side_effect=http_error(401, {"error": {"message": "denied"}})
        ), redirect_stderr(errors), redirect_stdout(output):
            result = self.module.main(["performance", "--account", "123"])
        self.assertEqual(2, result)
        combined = errors.getvalue() + output.getvalue()
        for secret in env.values():
            self.assertNotIn(secret, combined)

    def test_the_authorization_header_is_never_printed(self):
        env = {
            "GOOGLE_MERCHANT_CLIENT_ID": "client",
            "GOOGLE_MERCHANT_CLIENT_SECRET": "secret",
            "GOOGLE_MERCHANT_REFRESH_TOKEN": "refresh",
        }
        errors, output = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.module, "refresh_access_token", lambda profile: "super-secret-token"
        ), patch.object(
            self.module, "open_url", side_effect=urllib.error.URLError("offline")
        ), redirect_stderr(errors), redirect_stdout(output):
            self.module.main(["accounts"])
        combined = errors.getvalue() + output.getvalue()
        self.assertNotIn("super-secret-token", combined)
        self.assertNotIn("Bearer", combined)

    def test_the_source_carries_no_credential_or_owner_path(self):
        # Both markers are assembled rather than written out, so this assertion does not
        # itself trip the catalog suite's scan for owner paths and committed keys.
        home_root = str(Path.home().parent) + os.sep
        private_key = "BEGIN " + "RSA PRIVATE KEY"
        text = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (home_root, "refresh_token=1//", private_key):
            self.assertNotIn(forbidden, text)


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_the_launcher_resolves_its_runtime_outside_the_repository(self):
        completed = subprocess.run(
            [str(LAUNCHER), "--help"], cwd="/", env={"PATH": os.environ.get("PATH", "")},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("Google Merchant Center", completed.stdout)

    def test_every_subcommand_helps_without_credentials(self):
        parser = self.module.build_parser()
        commands = [
            action.choices for action in parser._subparsers._group_actions if action.choices
        ][0]
        for command in commands:
            with self.subTest(command=command):
                completed = subprocess.run(
                    [str(LAUNCHER), command, "--help"], cwd="/",
                    env={"PATH": os.environ.get("PATH", "")},
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("usage:", completed.stdout.lower())

    def test_an_unknown_option_value_is_rejected_by_the_parser(self):
        parser = self.module.build_parser()
        for argv in (
            ["performance", "--account", "1", "--breakdown", "cohort"],
            ["best-sellers", "--account", "1", "--country", "US", "--granularity", "daily"],
            ["competitive-visibility", "--account", "1", "--country", "US", "--category", "1", "--view", "rivals"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                    parser.parse_args(argv)

    def test_an_account_is_required_for_every_account_scoped_command(self):
        parser = self.module.build_parser()
        for command in ("status", "issues", "products", "performance", "price-insights"):
            with self.subTest(command=command):
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                    parser.parse_args([command])


if __name__ == "__main__":
    unittest.main()
