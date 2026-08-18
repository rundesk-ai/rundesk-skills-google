#!/usr/bin/env python3
"""Bounded, read-only access to Google PageSpeed Insights."""

from __future__ import annotations

import argparse
import csv
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


SKILL = "GOOGLE_PAGESPEED_INSIGHTS"
API_KEY = f"{SKILL}_API_KEY"
LABEL = f"{SKILL}_LABEL"
API_URL = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"
PROFILE_RE = re.compile(r"[A-Z0-9]+(?:_[A-Z0-9]+)*")
# The CLI keeps Google's lowercase Lighthouse identifiers, which are also the response keys, while
# the query string must carry the uppercase enums from the v5 discovery document.
STRATEGIES = {"mobile": "MOBILE", "desktop": "DESKTOP"}
CATEGORIES = {
    "performance": "PERFORMANCE",
    "accessibility": "ACCESSIBILITY",
    "best-practices": "BEST_PRACTICES",
    "seo": "SEO",
}
METRICS = {
    "first-contentful-paint": "first_contentful_paint",
    "largest-contentful-paint": "largest_contentful_paint",
    "speed-index": "speed_index",
    "total-blocking-time": "total_blocking_time",
    "cumulative-layout-shift": "cumulative_layout_shift",
    "interaction-to-next-paint": "interaction_to_next_paint",
}


class PageSpeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    api_key: str = field(repr=False)
    label: str


def env_candidates() -> list[Path]:
    paths: list[Path] = []
    for key in (f"{SKILL}_ENV_FILE", "RUNDESK_INTEGRATIONS_ENV"):
        if os.environ.get(key):
            paths.append(Path(os.environ[key]).expanduser())
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    paths.extend([
        xdg / "rundesk" / "integrations" / "google-pagespeed-insights" / "env",
        xdg / "google-pagespeed-insights" / "env",
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
            raise PageSpeedError(f"Environment file does not exist: {path}")
        return
    try:
        mode = path.stat().st_mode & 0o777
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PageSpeedError(f"Cannot read environment file {path}: {exc.strerror or exc}") from exc
    if mode & 0o077:
        print(f"WARNING: dotenv file {path} is accessible by group or others; use chmod 600.", file=sys.stderr)
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


def normalize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()


def is_default(name: str) -> bool:
    return normalize(name) in ("", "DEFAULT")


def profile_value(name: str, field: str) -> str:
    suffix = normalize(name)
    if not is_default(name):
        return os.environ.get(f"{field}__{suffix}", "")
    return os.environ.get(field, "")


def discovered_profiles() -> list[str]:
    explicit = [item.strip() for item in os.environ.get(f"{SKILL}_PROFILES", "").split(",") if item.strip()]
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
        raise PageSpeedError("Profile names must contain at least one letter or digit.")
    api_key = profile_value(name, API_KEY)
    if not api_key:
        missing = API_KEY if is_default(name) else f"{API_KEY}__{normalize(name)}"
        raise PageSpeedError(f"Missing required configuration: {missing}. Run rundesk skills configure for this skill.")
    return Profile(name, api_key, profile_value(name, LABEL) or name)


def selected_profile(args: argparse.Namespace) -> Profile:
    names = discovered_profiles()
    if args.profile:
        return get_profile(args.profile)
    if not names:
        raise PageSpeedError("No configured PageSpeed Insights profiles. Run rundesk skills configure for this skill.")
    if len(names) != 1:
        raise PageSpeedError("Multiple profiles are configured; select one with --profile: " + ", ".join(names))
    return get_profile(names[0])


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the API key on the expected Google request boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_url(request: urllib.request.Request, timeout: int = 60):
    return urllib.request.build_opener(RejectRedirectHandler()).open(request, timeout=timeout)


def expect_object(value: Any, noun: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PageSpeedError(f"Google returned a malformed {noun}.")
    return value


def expect_list(container: dict[str, Any], key: str, noun: str) -> list[Any]:
    value = container.get(key, [])
    if not isinstance(value, list):
        raise PageSpeedError(f"Google returned a malformed {noun} collection.")
    return value


def expect_objects(container: dict[str, Any], key: str, noun: str) -> list[dict[str, Any]]:
    return [expect_object(item, noun) for item in expect_list(container, key, noun)]


def expect_text(container: dict[str, Any], key: str, noun: str) -> str:
    value = container.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PageSpeedError(f"Google returned a malformed {noun}.")
    return value


def optional_number(container: dict[str, Any], key: str, noun: str) -> int | float | None:
    """Absent stays absent; anything kept must survive rounding, sorting, and RFC 8259 emission."""
    value = container.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PageSpeedError(f"Google returned a malformed {noun}.")
    if not math.isfinite(value):
        raise PageSpeedError(f"Google returned a non-finite {noun}.")
    return value


def refuse_non_finite(token: str) -> float:
    raise PageSpeedError(f"Google returned the non-finite JSON value {token}.")


def request_json(params: list[tuple[str, str]], opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    # Resolving the opener per call keeps open_url patchable; a default bound it at import time.
    opener = opener or open_url
    request = urllib.request.Request(API_URL + "?" + urllib.parse.urlencode(params), method="GET")
    api_key = next((value for name, value in params if name == "key"), "")
    try:
        with opener(request, timeout=60) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        raise PageSpeedError(f"Google API request failed with HTTP {exc.code}" + (f": {message}" if message else ".")) from exc
    except urllib.error.URLError as exc:
        raise PageSpeedError(f"Google API request failed: {exc.reason}") from exc
    if not payload:
        return {}
    try:
        # json.loads accepts the NaN and Infinity literals by default; PageSpeed output cannot.
        result = json.loads(payload, parse_constant=refuse_non_finite)
    except ValueError as exc:
        raise PageSpeedError("Google API returned invalid JSON.") from exc
    return expect_object(result, "API response")


def write_rows(rows: list[dict[str, Any]], columns: list[str], as_json: bool) -> None:
    if as_json:
        try:
            # allow_nan=False refuses to emit NaN or Infinity, which are not valid JSON.
            print(json.dumps(rows, indent=2, sort_keys=True, allow_nan=False))
        except ValueError as exc:
            raise PageSpeedError("Refused to emit a non-finite value as JSON.") from exc
        return
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    sys.stdout.write(output.getvalue())


def cmd_profiles(args: argparse.Namespace) -> None:
    rows = [{
        "profile": name,
        "label": profile_value(name, LABEL) or name,
        "status": "ready" if profile_value(name, API_KEY) else "missing 1",
    } for name in discovered_profiles()]
    write_rows(rows, ["profile", "label", "status"], args.json)


def valid_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError("URL must be an absolute HTTP or HTTPS URL without credentials.")
    return value


def cmd_analyze(args: argparse.Namespace) -> None:
    profile = selected_profile(args)
    categories = args.category or ["performance"]
    params = [("url", args.url), ("strategy", STRATEGIES[args.strategy])]
    params.extend(("category", CATEGORIES[category]) for category in categories)
    params.append(("key", profile.api_key))
    response = request_json(params)
    lighthouse = expect_object(response.get("lighthouseResult", {}), "Lighthouse result")
    if not lighthouse:
        raise PageSpeedError("Google returned no Lighthouse result for the requested URL.")
    returned_categories = expect_object(lighthouse.get("categories", {}), "Lighthouse categories object")
    audits = expect_object(lighthouse.get("audits", {}), "Lighthouse audits object")
    common = {
        "requested_url": expect_text(lighthouse, "requestedUrl", "requested URL") or args.url,
        "final_url": expect_text(lighthouse, "finalUrl", "final URL"),
        "strategy": args.strategy,
        "fetch_time": expect_text(lighthouse, "fetchTime", "fetch time"),
        "lighthouse_version": expect_text(lighthouse, "lighthouseVersion", "Lighthouse version"),
        "profile": profile.name,
    }
    summaries = []
    for category in categories:
        result = expect_object(returned_categories.get(category, {}), f"{category} category object")
        score = optional_number(result, "score", f"{category} category score")
        summaries.append({**common, "row_type": "summary", "category": category, "score": "" if score is None else round(score * 100)})
    metrics = []
    for audit_id, metric_name in METRICS.items():
        result = expect_object(audits.get(audit_id, {}), f"{audit_id} audit object")
        display = expect_text(result, "displayValue", f"{audit_id} audit display value")
        numeric = optional_number(result, "numericValue", f"{audit_id} audit numeric value")
        if display or numeric is not None:
            metrics.append({**common, "row_type": "metric", "metric": metric_name, "value": display or numeric, "numeric_value": "" if numeric is None else numeric})
    # Every returned category contributes weights, not only the requested ones, so an audit keeps
    # the highest weight any category assigns it.
    weighted: dict[str, int | float] = {}
    for name, returned in returned_categories.items():
        category_object = expect_object(returned, f"{name} category object")
        for ref in expect_objects(category_object, "auditRefs", f"{name} audit reference"):
            weight = optional_number(ref, "weight", f"{name} audit reference weight")
            if weight is None:
                continue
            ref_id = expect_text(ref, "id", f"{name} audit reference id")
            weighted[ref_id] = max(weighted.get(ref_id, 0), weight)
    findings = []
    for audit_id, result in audits.items():
        result = expect_object(result, f"{audit_id} audit object")
        score = optional_number(result, "score", f"{audit_id} audit score")
        if score is None or score >= 1:
            continue
        findings.append({**common, "row_type": "audit", "audit": audit_id, "title": expect_text(result, "title", f"{audit_id} audit title"), "score": round(score * 100), "display_value": expect_text(result, "displayValue", f"{audit_id} audit display value"), "weight": weighted.get(audit_id, 0)})
    findings.sort(key=lambda item: (-item["weight"], item["score"], item["audit"]))
    if len(findings) > args.audit_limit:
        print(f"WARNING: audit output truncated to {args.audit_limit} findings.", file=sys.stderr)
    rows = summaries + metrics + findings[:args.audit_limit]
    columns = ["row_type", "category", "metric", "audit", "title", "score", "value", "numeric_value", "display_value", "weight", "requested_url", "final_url", "strategy", "fetch_time", "lighthouse_version", "profile"]
    write_rows(rows, columns, args.json)


def parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--env-file")
    parent.add_argument("--json", action="store_true")
    profile = argparse.ArgumentParser(add_help=False)
    profile.add_argument("--profile")
    result = argparse.ArgumentParser(prog="google-pagespeed-insights", description="Read bounded Google PageSpeed Insights evidence.")
    subs = result.add_subparsers(dest="command", required=True)
    command = subs.add_parser("profiles", parents=[parent], help="List locally configured profiles without contacting Google.")
    command.set_defaults(func=cmd_profiles)
    command = subs.add_parser("analyze", parents=[parent, profile], help="Run a Lighthouse assessment for one public webpage.")
    command.add_argument("--url", required=True, type=valid_url)
    command.add_argument("--strategy", choices=list(STRATEGIES), default="mobile")
    command.add_argument("--category", action="append", choices=list(CATEGORIES))
    command.add_argument("--audit-limit", type=int, default=10)
    command.set_defaults(func=cmd_analyze)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        load_dotenv(resolve_env_file(args.env_file), required=bool(args.env_file))
        if hasattr(args, "audit_limit") and not 0 <= args.audit_limit <= 50:
            raise PageSpeedError("--audit-limit must be between 0 and 50.")
        args.func(args)
        return 0
    except PageSpeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
