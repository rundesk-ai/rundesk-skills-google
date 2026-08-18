#!/usr/bin/env python3
"""Bounded, read-only access to Google PageSpeed Insights."""

from __future__ import annotations

import argparse
import csv
import io
import json
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


def request_json(params: list[tuple[str, str]], opener: Callable[..., Any] = open_url) -> dict[str, Any]:
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
    try:
        result = json.loads(payload) if payload else {}
    except ValueError as exc:
        raise PageSpeedError("Google API returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise PageSpeedError("Google API returned an unexpected response.")
    return result


def write_rows(rows: list[dict[str, Any]], columns: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
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
    params = [("url", args.url), ("strategy", args.strategy)]
    params.extend(("category", category) for category in categories)
    params.append(("key", profile.api_key))
    response = request_json(params)
    lighthouse = response.get("lighthouseResult")
    if not isinstance(lighthouse, dict) or not lighthouse:
        raise PageSpeedError("Google returned no Lighthouse result for the requested URL.")
    returned_categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})
    if not isinstance(returned_categories, dict) or not isinstance(audits, dict):
        raise PageSpeedError("Google returned an incomplete Lighthouse result.")
    common = {
        "requested_url": lighthouse.get("requestedUrl", args.url),
        "final_url": lighthouse.get("finalUrl", ""),
        "strategy": args.strategy,
        "fetch_time": lighthouse.get("fetchTime", ""),
        "lighthouse_version": lighthouse.get("lighthouseVersion", ""),
        "profile": profile.name,
    }
    summaries = []
    for category in categories:
        result = returned_categories.get(category, {})
        if isinstance(result, dict):
            score = result.get("score")
            summaries.append({**common, "row_type": "summary", "category": category, "score": round(score * 100) if isinstance(score, (int, float)) else ""})
    metrics = []
    for audit_id, metric_name in METRICS.items():
        result = audits.get(audit_id, {})
        if isinstance(result, dict) and (result.get("displayValue") or result.get("numericValue") is not None):
            metrics.append({**common, "row_type": "metric", "metric": metric_name, "value": result.get("displayValue", result.get("numericValue", "")), "numeric_value": result.get("numericValue", "")})
    weighted: dict[str, float] = {}
    for category in returned_categories.values():
        if not isinstance(category, dict):
            continue
        for ref in category.get("auditRefs", []):
            if isinstance(ref, dict) and isinstance(ref.get("weight"), (int, float)):
                weighted[ref.get("id", "")] = max(weighted.get(ref.get("id", ""), 0), ref["weight"])
    findings = []
    for audit_id, result in audits.items():
        if not isinstance(result, dict) or not isinstance(result.get("score"), (int, float)) or result["score"] >= 1:
            continue
        findings.append({**common, "row_type": "audit", "audit": audit_id, "title": result.get("title", ""), "score": round(result["score"] * 100), "display_value": result.get("displayValue", ""), "weight": weighted.get(audit_id, 0)})
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
    command.add_argument("--strategy", choices=["mobile", "desktop"], default="mobile")
    command.add_argument("--category", action="append", choices=["performance", "accessibility", "best-practices", "seo"])
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
