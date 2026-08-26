#!/usr/bin/env python3
"""Bounded, read-only access to current and historical Chrome UX Report data."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


SKILL = "GOOGLE_CRUX"
API_KEY = f"{SKILL}_API_KEY"
LABEL = f"{SKILL}_LABEL"
API_ROOT = "https://chromeuxreport.googleapis.com/v1/records:"
CURRENT_ENDPOINT = "queryRecord"
HISTORY_ENDPOINT = "queryHistoryRecord"
PROFILE_RE = re.compile(r"[A-Z0-9]+(?:_[A-Z0-9]+)*")
FORM_FACTORS = {"all": "", "phone": "PHONE", "tablet": "TABLET", "desktop": "DESKTOP"}
METRICS = {
    "lcp": ("largest_contentful_paint", "milliseconds"),
    "inp": ("interaction_to_next_paint", "milliseconds"),
    "cls": ("cumulative_layout_shift", "unitless"),
}
DEFAULT_METRICS = ("lcp", "inp", "cls")
MAX_PERIODS = 40
DEFAULT_PERIODS = 25
MAX_BINS = 10
MAX_BODY = 2 * 1024 * 1024
MAX_ERROR_BODY = 65536
DISTRIBUTION_TOLERANCE = 0.02
COLUMNS = [
    "row_type", "metric", "unit", "p75", "bucket_start", "bucket_end", "density",
    "eligible", "first_date", "last_date", "scope", "requested", "identifier",
    "form_factor", "profile",
]


class CruxError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    api_key: str = field(repr=False)
    label: str = ""


def normalize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()


def is_default(name: str) -> bool:
    return normalize(name) in ("", "DEFAULT")


def env_candidates() -> list[Path]:
    paths = []
    for key in (f"{SKILL}_ENV_FILE", "RUNDESK_INTEGRATIONS_ENV"):
        if os.environ.get(key):
            paths.append(Path(os.environ[key]).expanduser())
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    paths.extend([
        xdg / "rundesk" / "integrations" / "google-crux" / "env",
        xdg / "google-crux" / "env",
    ])
    return paths


def resolve_env_file(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    for path in env_candidates():
        if path.is_file():
            return path
    return env_candidates()[-1]


def load_dotenv(path: Path, *, required: bool = False) -> None:
    if not path.exists():
        if required:
            raise CruxError(f"Environment file does not exist: {path}")
        return
    try:
        mode = path.stat().st_mode & 0o777
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CruxError(f"Cannot read environment file {path}: {exc.strerror or exc}") from exc
    if mode & 0o077:
        print(f"WARNING: dotenv file {path} is accessible by group or others; use chmod 600.",
              file=sys.stderr)
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and key not in os.environ:
            os.environ[key] = value


def profile_value(name: str, field_name: str) -> str:
    suffix = normalize(name)
    if not is_default(name):
        return os.environ.get(f"{field_name}__{suffix}", "")
    return os.environ.get(field_name, "")


def discovered_profiles() -> list[str]:
    explicit = [one.strip() for one in os.environ.get(f"{SKILL}_PROFILES", "").split(",")
                if one.strip()]
    default = os.environ.get(f"{SKILL}_DEFAULT_PROFILE", "")
    if default and default not in explicit:
        explicit.insert(0, default)
    if explicit:
        return explicit
    names = {
        key[len(API_KEY) + 2:].lower().replace("_", "-")
        for key in os.environ
        if key.startswith(f"{API_KEY}__") and PROFILE_RE.fullmatch(key[len(API_KEY) + 2:])
    }
    if os.environ.get(API_KEY):
        names.add("default")
    return sorted(names)


def get_profile(name: str) -> Profile:
    if name and not normalize(name):
        raise CruxError("Profile names must contain at least one letter or digit.")
    key = profile_value(name, API_KEY)
    if not key:
        missing = API_KEY if is_default(name) else f"{API_KEY}__{normalize(name)}"
        raise CruxError(
            f"Missing required configuration: {missing}. Run rundesk skills configure for this skill."
        )
    return Profile(name, key, profile_value(name, LABEL) or name)


def selected_profile(args: argparse.Namespace) -> Profile:
    names = discovered_profiles()
    if args.profile:
        return get_profile(args.profile)
    if not names:
        raise CruxError("No configured CrUX profiles. Run rundesk skills configure for this skill.")
    if len(names) != 1:
        raise CruxError("Multiple profiles are configured; select one with --profile: " +
                        ", ".join(names))
    return get_profile(names[0])


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_url(request: urllib.request.Request, timeout: int = 60):
    return urllib.request.build_opener(RejectRedirectHandler()).open(request, timeout=timeout)


def expect_object(value: Any, noun: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CruxError(f"Google returned a malformed {noun}.")
    return value


def expect_list(container: dict[str, Any], key: str, noun: str) -> list[Any]:
    value = container.get(key, [])
    if not isinstance(value, list):
        raise CruxError(f"Google returned a malformed {noun} collection.")
    return value


def response_bytes(response: Any, maximum: int) -> bytes:
    raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise CruxError("Google API returned an oversized response.")
    return raw


def safe_error(exc: urllib.error.HTTPError, api_key: str) -> str:
    try:
        raw = exc.read(MAX_ERROR_BODY)
    except OSError:
        return f"HTTP {exc.code}"
    finally:
        exc.close()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return f"HTTP {exc.code}"
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    said = str(message).strip() if message and str(message).strip() else f"HTTP {exc.code}"
    return said.replace(api_key, "[REDACTED]") if api_key else said


def refuse_non_finite(token: str) -> float:
    raise CruxError(f"Google returned the non-finite JSON value {token}.")


def request_json(
    endpoint: str, api_key: str, body: dict[str, Any], opener: Callable[..., Any] | None = None
) -> dict[str, Any]:
    opener = opener or open_url
    url = API_ROOT + endpoint + "?" + urllib.parse.urlencode({"key": api_key})
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with opener(request, timeout=60) as response:
            raw = response_bytes(response, MAX_BODY)
    except urllib.error.HTTPError as exc:
        raise CruxError(f"Google API request failed: {safe_error(exc, api_key)}.") from exc
    except urllib.error.URLError as exc:
        raise CruxError(f"Google API request failed: {exc.reason}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"), parse_constant=refuse_non_finite)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CruxError("Google API returned invalid JSON.") from exc
    return expect_object(decoded, "API response")


def write_rows(rows: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        try:
            print(json.dumps(rows, indent=2, sort_keys=True, allow_nan=False))
        except ValueError as exc:
            raise CruxError("Refused to emit a non-finite value as JSON.") from exc
        return
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    sys.stdout.write(output.getvalue())


def cmd_profiles(args: argparse.Namespace) -> int:
    rows = [{
        "profile": name,
        "label": profile_value(name, LABEL) or name,
        "status": "ready" if profile_value(name, API_KEY) else "missing 1",
    } for name in discovered_profiles()]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["profile", "label", "status"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        sys.stdout.write(output.getvalue())
    return 0


def web_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.username or parsed.password):
        raise argparse.ArgumentTypeError(
            "URL must be an absolute HTTP or HTTPS URL without credentials."
        )
    return value


def web_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError(
            "Origin must contain only an HTTP or HTTPS scheme and host, without credentials."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def date_text(value: Any, noun: str) -> str:
    date = expect_object(value, noun)
    parts = [date.get(name) for name in ("year", "month", "day")]
    if any(isinstance(one, bool) or not isinstance(one, int) for one in parts):
        raise CruxError(f"Google returned a malformed {noun}.")
    try:
        return dt.date(*parts).isoformat()
    except ValueError as exc:
        raise CruxError(f"Google returned an invalid {noun}.") from exc


def finite_number(value: Any, noun: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CruxError(f"Google returned a malformed {noun}.")
    if not math.isfinite(value):
        raise CruxError(f"Google returned a non-finite {noun}.")
    return value


def metric_number(value: Any, metric: str, noun: str) -> int | float | str:
    if metric == "cumulative_layout_shift":
        if not isinstance(value, str) or not value.strip():
            raise CruxError(f"Google returned a malformed {noun}.")
        try:
            number = float(value)
        except ValueError as exc:
            raise CruxError(f"Google returned a malformed {noun}.") from exc
        if not math.isfinite(number):
            raise CruxError(f"Google returned a non-finite {noun}.")
        return value
    return finite_number(value, noun)


def comparable(value: int | float | str) -> float:
    return float(value)


def histogram(metric: str, value: Any, *, history: bool, periods: int = 1) -> list[dict[str, Any]]:
    noun = f"{metric} {'history ' if history else ''}histogram"
    bins = value
    if not isinstance(bins, list):
        raise CruxError(f"Google returned a malformed {noun} collection.")
    if not bins:
        raise CruxError(f"Google returned an empty {noun} collection.")
    if len(bins) > MAX_BINS:
        raise CruxError(f"Google returned more than {MAX_BINS} {noun} buckets.")
    parsed = []
    previous_end: float | None = None
    for index, raw in enumerate(bins):
        bucket = expect_object(raw, f"{noun} bucket")
        if "start" not in bucket:
            raise CruxError(f"Google returned a {noun} bucket without a start.")
        start = metric_number(bucket["start"], metric, f"{noun} bucket start")
        end = metric_number(bucket["end"], metric, f"{noun} bucket end") if "end" in bucket else ""
        if end == "" and index != len(bins) - 1:
            raise CruxError(f"Google returned an open-ended {noun} bucket before the last one.")
        if end != "" and comparable(end) < comparable(start):
            raise CruxError(f"Google returned a {noun} bucket ending before it starts.")
        if previous_end is not None and comparable(start) < previous_end:
            raise CruxError(f"Google returned overlapping or unordered {noun} buckets.")
        previous_end = None if end == "" else comparable(end)
        if history:
            densities = bucket.get("densities")
            if not isinstance(densities, list) or len(densities) != periods:
                raise CruxError(
                    f"Google returned {noun} densities that do not match its collection periods."
                )
            normalized = []
            for density in densities:
                if density == "NaN":
                    normalized.append(None)
                else:
                    number = finite_number(density, f"{noun} density")
                    if not 0 <= number <= 1:
                        raise CruxError(f"Google returned a {noun} density outside 0 to 1.")
                    normalized.append(number)
        else:
            number = finite_number(bucket.get("density"), f"{noun} density")
            if not 0 <= number <= 1:
                raise CruxError(f"Google returned a {noun} density outside 0 to 1.")
            normalized = [number]
        parsed.append({"start": start, "end": end, "densities": normalized})
    for period in range(periods):
        values = [one["densities"][period] for one in parsed]
        if not values:
            continue
        missing = [one is None for one in values]
        if any(missing) and not all(missing):
            raise CruxError(f"Google returned mixed eligible and ineligible {noun} densities.")
        if not any(missing) and abs(sum(values) - 1) > DISTRIBUTION_TOLERANCE:
            raise CruxError(f"Google returned {noun} densities that do not total 1.")
    return parsed


def target(args: argparse.Namespace) -> tuple[str, str]:
    return ("url", args.url) if args.url else ("origin", args.origin)


def requested_metrics(args: argparse.Namespace) -> list[tuple[str, str]]:
    selected = args.metric or list(DEFAULT_METRICS)
    # argparse permits a repeatable option to name the same value more than once. Preserve the
    # caller's first-seen order but never spend response space or quota on duplicate metrics.
    unique = list(dict.fromkeys(selected))
    return [(METRICS[name][0], METRICS[name][1]) for name in unique]


def request_body(args: argparse.Namespace, metrics: list[tuple[str, str]]) -> dict[str, Any]:
    scope, requested = target(args)
    body: dict[str, Any] = {scope: requested, "metrics": [name for name, _ in metrics]}
    form_factor = FORM_FACTORS[args.form_factor]
    if form_factor:
        body["formFactor"] = form_factor
    if args.command == "history":
        body["collectionPeriodCount"] = args.periods
    return body


def record_context(
    response: dict[str, Any], args: argparse.Namespace, profile: Profile
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = expect_object(response.get("record"), "CrUX record")
    if not record:
        raise CruxError("Google returned no CrUX record for the requested identifier.")
    key = expect_object(record.get("key"), "CrUX record key")
    scope, requested = target(args)
    identifier = key.get(scope)
    if not isinstance(identifier, str) or not identifier:
        raise CruxError(f"Google returned no {scope} in the CrUX record key.")
    returned_form = key.get("formFactor", "ALL")
    if returned_form not in {"ALL", "PHONE", "TABLET", "DESKTOP"}:
        raise CruxError("Google returned a malformed CrUX form factor.")
    requested_form = FORM_FACTORS[args.form_factor] or "ALL"
    if returned_form != requested_form:
        raise CruxError(
            f"Google returned unexpected CrUX form factor {returned_form}; "
            f"requested {requested_form}."
        )
    context = {
        "scope": scope,
        "requested": requested,
        "identifier": identifier,
        "form_factor": returned_form.lower(),
        "profile": profile.name,
    }
    return record, context


def metric_object(metrics: dict[str, Any], name: str) -> dict[str, Any] | None:
    if name not in metrics:
        return None
    return expect_object(metrics[name], f"{name} metric")


def report_missing(missing: list[str]) -> int:
    if not missing:
        return 0
    print("ERROR: Google returned no usable data for requested metrics: " +
          ", ".join(missing) + ".", file=sys.stderr)
    return 2


def cmd_current(args: argparse.Namespace) -> int:
    profile = selected_profile(args)
    wanted = requested_metrics(args)
    response = request_json(CURRENT_ENDPOINT, profile.api_key, request_body(args, wanted))
    record, context = record_context(response, args, profile)
    period = expect_object(record.get("collectionPeriod"), "CrUX collection period")
    dates = {
        "first_date": date_text(period.get("firstDate"), "CrUX collection first date"),
        "last_date": date_text(period.get("lastDate"), "CrUX collection last date"),
    }
    if dates["first_date"] > dates["last_date"]:
        raise CruxError("Google returned a CrUX collection period ending before it starts.")
    metrics = expect_object(record.get("metrics"), "CrUX metrics")
    rows, missing = [], []
    for name, unit in wanted:
        metric = metric_object(metrics, name)
        if metric is None:
            missing.append(name)
            continue
        percentiles = expect_object(metric.get("percentiles"), f"{name} percentiles")
        if "p75" not in percentiles:
            missing.append(name)
            continue
        p75 = metric_number(percentiles["p75"], name, f"{name} p75")
        bins = histogram(name, metric.get("histogram"), history=False)
        rows.append({**context, **dates, "row_type": "percentile", "metric": name,
                     "unit": unit, "p75": p75, "eligible": "true"})
        if args.distributions:
            rows.extend({
                **context, **dates, "row_type": "distribution", "metric": name, "unit": unit,
                "bucket_start": one["start"], "bucket_end": one["end"],
                "density": one["densities"][0], "eligible": "true",
            } for one in bins)
    if rows:
        write_rows(rows, args.json)
    return report_missing(missing)


def cmd_history(args: argparse.Namespace) -> int:
    profile = selected_profile(args)
    wanted = requested_metrics(args)
    response = request_json(HISTORY_ENDPOINT, profile.api_key, request_body(args, wanted))
    record, context = record_context(response, args, profile)
    raw_periods = expect_list(record, "collectionPeriods", "CrUX history collection period")
    if not raw_periods or len(raw_periods) > args.periods:
        raise CruxError("Google returned an invalid number of CrUX history collection periods.")
    periods = [{
        "first_date": date_text(one.get("firstDate"), "CrUX history collection first date"),
        "last_date": date_text(one.get("lastDate"), "CrUX history collection last date"),
    } for one in (expect_object(raw, "CrUX history collection period") for raw in raw_periods)]
    if any(one["first_date"] > one["last_date"] for one in periods):
        raise CruxError("Google returned a history collection period ending before it starts.")
    if any(periods[index]["last_date"] >= periods[index + 1]["last_date"]
           for index in range(len(periods) - 1)):
        raise CruxError("Google returned unordered CrUX history collection periods.")
    metrics = expect_object(record.get("metrics"), "CrUX history metrics")
    rows, missing = [], []
    for name, unit in wanted:
        metric = metric_object(metrics, name)
        if metric is None:
            missing.append(name)
            continue
        percentiles = expect_object(metric.get("percentilesTimeseries"),
                                    f"{name} history percentiles")
        p75s = percentiles.get("p75s")
        if not isinstance(p75s, list) or len(p75s) != len(periods):
            raise CruxError(
                f"Google returned {name} p75 history that does not match its collection periods."
            )
        bins = histogram(name, metric.get("histogramTimeseries"), history=True,
                         periods=len(periods))
        for index, dates in enumerate(periods):
            raw_p75 = p75s[index]
            p75 = "" if raw_p75 is None else metric_number(raw_p75, name, f"{name} history p75")
            densities = [one["densities"][index] for one in bins]
            density_missing = bool(densities) and all(one is None for one in densities)
            if (raw_p75 is None) != density_missing:
                raise CruxError(
                    f"Google returned inconsistent eligibility for {name} history period {index + 1}."
                )
            eligible = "false" if raw_p75 is None else "true"
            rows.append({**context, **dates, "row_type": "percentile", "metric": name,
                         "unit": unit, "p75": p75, "eligible": eligible})
            if args.distributions:
                rows.extend({
                    **context, **dates, "row_type": "distribution", "metric": name,
                    "unit": unit, "bucket_start": one["start"], "bucket_end": one["end"],
                    "density": "" if one["densities"][index] is None
                    else one["densities"][index], "eligible": eligible,
                } for one in bins)
    if rows:
        write_rows(rows, args.json)
    return report_missing(missing)


def parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--env-file")
    parent.add_argument("--json", action="store_true")
    profile = argparse.ArgumentParser(add_help=False)
    profile.add_argument("--profile")
    query = argparse.ArgumentParser(add_help=False)
    target_group = query.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--url", type=web_url)
    target_group.add_argument("--origin", type=web_origin)
    query.add_argument("--form-factor", choices=list(FORM_FACTORS), default="all")
    query.add_argument("--metric", action="append", choices=list(METRICS))
    query.add_argument("--distributions", action="store_true")
    result = argparse.ArgumentParser(
        prog="google-crux", description="Read bounded current and historical Chrome UX Report evidence."
    )
    subs = result.add_subparsers(dest="command", required=True)
    command = subs.add_parser("profiles", parents=[parent],
                              help="List locally configured profiles without contacting Google.")
    command.set_defaults(func=cmd_profiles)
    command = subs.add_parser("current", parents=[parent, profile, query],
                              help="Read the latest CrUX 28-day rolling window.")
    command.set_defaults(func=cmd_current)
    command = subs.add_parser("history", parents=[parent, profile, query],
                              help="Read weekly CrUX 28-day rolling windows.")
    command.add_argument("--periods", type=int, default=DEFAULT_PERIODS)
    command.set_defaults(func=cmd_history)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        load_dotenv(resolve_env_file(args.env_file), required=bool(args.env_file))
        if hasattr(args, "periods") and not 1 <= args.periods <= MAX_PERIODS:
            raise CruxError(f"--periods must be between 1 and {MAX_PERIODS}.")
        return args.func(args)
    except CruxError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
